"""MBA-8: contracts for external services and deterministic test doubles.

Every external system the module depends on (prescriptions, patient context,
inventory, authentication, notifications) sits behind a typed interface
defined here. Production adapters implement the same interface; tests and demos
use the in-memory doubles, so behaviour is repeatable without real services.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from time import sleep
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, TypeVar

from .access import AccessDenied, Principal
from .domain import (
    DeliveryStatus, Inventory, MedicationRequest, MedicationRequestStatus,
    NotificationChannel, Patient, Prescription, PrescriptionRecord,
    PrescriptionSource, PrescriptionStatus, new_record_id, utc_now,
)
from .waitlist import InMemoryInventoryGateway, InventoryGateway, RestockEvent

T = TypeVar("T")


# ------------------------------------------------------------------ errors

class ExternalServiceError(Exception):
    """A controlled failure from an external service.

    ``code`` is a short, safe value for logs and audit; it never carries the
    remote error text, which may contain patient data or secrets.
    """

    def __init__(self, service: str, code: str = "UNAVAILABLE") -> None:
        super().__init__(f"{service}: {code}")
        self.service = service
        self.code = code


class ServiceTimeout(ExternalServiceError):
    def __init__(self, service: str) -> None:
        super().__init__(service, "TIMEOUT")


class InvalidPayload(ExternalServiceError):
    def __init__(self, service: str, reasons: tuple[str, ...]) -> None:
        super().__init__(service, "INVALID_PAYLOAD")
        self.reasons = reasons


def call_external(service: str, operation: Callable[[], T], *,
                  timeout: float, attempts: int = 1) -> T:
    """Run ``operation`` with a timeout and optional retries (NFR-P-05).

    Only transient failures (timeouts and unavailability) are retried. An
    invalid payload is returned at once because retrying cannot fix it.
    Anything unexpected becomes ``ExternalServiceError`` so callers see a
    controlled error state, never a crash (NFR-QA-05).
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    last: ExternalServiceError = ExternalServiceError(service)
    for _ in range(attempts):
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            return pool.submit(operation).result(timeout=timeout)
        except FutureTimeout:
            last = ServiceTimeout(service)
        except InvalidPayload:
            raise
        except ExternalServiceError as exc:
            last = exc
        except AccessDenied:
            raise
        except Exception:
            last = ExternalServiceError(service)
        finally:
            # Do not wait for a hung call; its result is discarded.
            pool.shutdown(wait=False)
    raise last


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


# ----------------------------------------------------------- prescriptions

@dataclass(frozen=True, slots=True)
class ExternalMedicationLine:
    source_line_id: str
    quantity: int
    medicine_code: str | None = None
    medicine_name: str | None = None
    dosage_text: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalPrescription:
    """A structured prescription as received from an external source (SRS 3.2.1)."""

    source_system: str
    source_identifier: str
    patient_reference: str
    status: str
    lines: tuple[ExternalMedicationLine, ...]
    received_at: datetime
    authored_on: datetime | None = None
    prescriber_reference: str | None = None


# FHIR MedicationRequest.status values mapped to the internal states.
EXTERNAL_STATUS_MAP: Mapping[str, PrescriptionStatus] = MappingProxyType({
    "active": PrescriptionStatus.VALID,
    "completed": PrescriptionStatus.EXPIRED,
    "cancelled": PrescriptionStatus.INVALID,
    "stopped": PrescriptionStatus.INVALID,
    "entered-in-error": PrescriptionStatus.INVALID,
    "on-hold": PrescriptionStatus.MANUAL_REVIEW_REQUIRED,
    "draft": PrescriptionStatus.MANUAL_REVIEW_REQUIRED,
    "unknown": PrescriptionStatus.MANUAL_REVIEW_REQUIRED,
})

_PRESCRIPTION_FIELDS = frozenset({
    "source_system", "source_identifier", "patient_reference", "status",
    "authored_on", "prescriber_reference", "lines",
})
_LINE_FIELDS = frozenset({
    "source_line_id", "medicine_code", "medicine_name", "quantity", "dosage_text",
})


def _text(payload: Mapping[str, Any], key: str, reasons: list[str], *,
          required: bool, prefix: str = "") -> str | None:
    value = payload.get(key)
    if value is None:
        if required:
            reasons.append(f"{prefix}{key.upper()}_REQUIRED")
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        reasons.append(f"{prefix}{key.upper()}_INVALID")
        return None
    return value.strip()


def parse_prescription_payload(payload: Mapping[str, Any], *,
                               received_at: datetime) -> ExternalPrescription:
    """Validate a structured prescription against the schema (REQ-EP-10/11).

    Unknown fields are rejected so unexpected data cannot slip into the model.
    """
    _aware(received_at, "received_at")
    if not isinstance(payload, Mapping):
        raise InvalidPayload("prescription", ("PAYLOAD_NOT_AN_OBJECT",))
    reasons: list[str] = []
    if set(payload) - _PRESCRIPTION_FIELDS:
        reasons.append("UNEXPECTED_FIELD")
    system = _text(payload, "source_system", reasons, required=True)
    identifier = _text(payload, "source_identifier", reasons, required=True)
    patient = _text(payload, "patient_reference", reasons, required=True)
    status = _text(payload, "status", reasons, required=True)
    if status is not None and status not in EXTERNAL_STATUS_MAP:
        reasons.append("STATUS_NOT_SUPPORTED")
    prescriber = _text(payload, "prescriber_reference", reasons, required=False)
    authored: datetime | None = None
    raw_authored = payload.get("authored_on")
    if raw_authored is not None:
        try:
            authored = datetime.fromisoformat(raw_authored)
            _aware(authored, "authored_on")
        except (TypeError, ValueError):
            reasons.append("AUTHORED_ON_INVALID")
            authored = None
        else:
            if authored > received_at:
                reasons.append("AUTHORED_ON_IN_FUTURE")
    raw_lines = payload.get("lines")
    lines: list[ExternalMedicationLine] = []
    if not isinstance(raw_lines, list) or not raw_lines:
        reasons.append("LINES_REQUIRED")
    else:
        seen: set[str] = set()
        for raw in raw_lines:
            if not isinstance(raw, Mapping):
                reasons.append("LINE_NOT_AN_OBJECT")
                continue
            if set(raw) - _LINE_FIELDS:
                reasons.append("LINE_UNEXPECTED_FIELD")
            line_id = _text(raw, "source_line_id", reasons, required=True, prefix="LINE_")
            code = _text(raw, "medicine_code", reasons, required=False, prefix="LINE_")
            name = _text(raw, "medicine_name", reasons, required=False, prefix="LINE_")
            dosage = _text(raw, "dosage_text", reasons, required=False, prefix="LINE_")
            quantity = raw.get("quantity")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                reasons.append("LINE_QUANTITY_INVALID")
            if code is None and name is None:
                reasons.append("LINE_MEDICINE_REQUIRED")
            if line_id is not None:
                if line_id in seen:
                    reasons.append("LINE_ID_DUPLICATED")
                seen.add(line_id)
            if line_id and isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0:
                lines.append(ExternalMedicationLine(line_id, quantity, code, name, dosage))
    if reasons:
        raise InvalidPayload("prescription", tuple(dict.fromkeys(reasons)))
    return ExternalPrescription(system, identifier, patient, status, tuple(lines),
                                received_at, authored, prescriber)


def to_internal(prescription: ExternalPrescription, patient: Patient, *,
                prescription_id: str | None = None) -> PrescriptionRecord:
    """Map an external prescription into the linked internal records.

    The source identifier is preserved (REQ-EP-20). Medication requests start
    ELIGIBLE only when the prescription maps to VALID; otherwise they stay
    PENDING until a pharmacist reviews them.
    """
    status = EXTERNAL_STATUS_MAP[prescription.status]
    internal_id = prescription_id if prescription_id is not None else new_record_id()
    request_status = (MedicationRequestStatus.ELIGIBLE if status is PrescriptionStatus.VALID
                      else MedicationRequestStatus.PENDING)
    record = Prescription(
        internal_id, patient.patient_id, PrescriptionSource.STRUCTURED_IMPORT, status,
        source_identifier=f"{prescription.source_system}:{prescription.source_identifier}",
        authored_on=prescription.authored_on,
        prescriber_reference=prescription.prescriber_reference,
        created_at=prescription.received_at,
    )
    requests = tuple(
        MedicationRequest(
            f"{internal_id}:{line.source_line_id}", internal_id, line.quantity,
            medicine_code=line.medicine_code, medicine_name=line.medicine_name,
            dosage_text=line.dosage_text, request_status=request_status,
            created_at=prescription.received_at,
        )
        for line in prescription.lines
    )
    return PrescriptionRecord(patient, record, requests)


class PrescriptionSourceAdapter(Protocol):
    def fetch(self, source_identifier: str) -> Mapping[str, Any]: ...


def import_prescription(source: PrescriptionSourceAdapter, source_identifier: str,
                        patient: Patient, *, received_at: datetime,
                        timeout: float = 2.0, attempts: int = 2) -> PrescriptionRecord:
    """Fetch, validate, and map one prescription through its adapter."""
    payload = call_external("prescription", lambda: source.fetch(source_identifier),
                            timeout=timeout, attempts=attempts)
    parsed = parse_prescription_payload(payload, received_at=received_at)
    if parsed.patient_reference != (patient.external_patient_id or patient.patient_id):
        raise InvalidPayload("prescription", ("PATIENT_REFERENCE_MISMATCH",))
    return to_internal(parsed, patient)


# --------------------------------------------------------- patient context

CONTEXT_AVAILABLE = "AVAILABLE"
CONTEXT_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """Ask only for named attributes, for a stated purpose (SRS 3.2.3)."""

    patient_id: str
    attributes: frozenset[str]
    purpose: str
    consent_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", frozenset(self.attributes))
        if not self.patient_id.strip() or not self.purpose.strip() or not self.attributes:
            raise ValueError("patient, purpose, and at least one attribute are required")


@dataclass(frozen=True, slots=True)
class ContextResponse:
    patient_id: str
    status: str
    values: Mapping[str, Any] = field(default_factory=dict)
    source_timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in {CONTEXT_AVAILABLE, CONTEXT_UNKNOWN}:
            raise ValueError("context status must be AVAILABLE or UNKNOWN")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        if self.source_timestamp is not None:
            _aware(self.source_timestamp, "source_timestamp")


class PatientContextSource(Protocol):
    def fetch(self, request: ContextRequest) -> ContextResponse: ...


# ---------------------------------------------------------- authentication

class AuthProvider(Protocol):
    """Turns a verified session token into a ``Principal`` (SRS 3.2.5)."""

    def authenticate(self, token: str, *, at: datetime) -> Principal: ...


# ------------------------------------------------------------ notification

@dataclass(frozen=True, slots=True)
class NotificationRequest:
    """One message for one recipient (SRS 3.2.4). Parameters must be non-sensitive."""

    notification_id: str
    booking_id: str
    recipient_reference: str
    channel: NotificationChannel
    template_id: str
    body: str
    parameters: Mapping[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not all((self.notification_id.strip(), self.booking_id.strip(),
                    self.recipient_reference.strip(), self.template_id.strip(),
                    self.body.strip())):
            raise ValueError("notification identifiers, recipient, template, and body are required")
        params = dict(self.parameters)
        if any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 100
               for k, v in params.items()):
            raise ValueError("notification parameters must be short strings")
        object.__setattr__(self, "parameters", MappingProxyType(params))
        _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    notification_id: str
    status: DeliveryStatus
    attempted_at: datetime
    provider_reference: str | None = None
    error_code: str | None = None


class NotificationSender(Protocol):
    def send(self, request: NotificationRequest) -> DeliveryReceipt: ...


# --------------------------------------------------------- test doubles

class _FaultPlan:
    """Fails the first ``fail_times`` calls, optionally after a delay."""

    def __init__(self, service: str, fail_times: int, delay: float) -> None:
        self.service = service
        self.remaining = fail_times
        self.delay = delay
        self.calls = 0
        self._lock = Lock()

    def before_call(self) -> None:
        with self._lock:
            self.calls += 1
            fail = self.remaining > 0
            if fail:
                self.remaining -= 1
        if self.delay:
            sleep(self.delay)
        if fail:
            raise ExternalServiceError(self.service)


class MockPrescriptionSource:
    """Returns stored payloads; can fail or be slow on purpose."""

    def __init__(self, payloads: Mapping[str, Mapping[str, Any]], *,
                 fail_times: int = 0, delay: float = 0.0) -> None:
        self.payloads = dict(payloads)
        self.faults = _FaultPlan("prescription", fail_times, delay)

    @property
    def calls(self) -> int:
        return self.faults.calls

    def fetch(self, source_identifier: str) -> Mapping[str, Any]:
        self.faults.before_call()
        if source_identifier not in self.payloads:
            raise ExternalServiceError("prescription", "NOT_FOUND")
        return self.payloads[source_identifier]


class MockPatientContextSource:
    """Holds a full record per patient but returns only requested attributes.

    ``leak_unrequested`` makes it behave like a careless upstream that sends
    extra fields, so the caller's filtering can be tested.
    """

    def __init__(self, records: Mapping[str, Mapping[str, Any]], *,
                 source_timestamp: datetime | None = None, fail_times: int = 0,
                 delay: float = 0.0, leak_unrequested: bool = False) -> None:
        self.records = {pid: dict(values) for pid, values in records.items()}
        self.source_timestamp = source_timestamp
        self.leak_unrequested = leak_unrequested
        self.faults = _FaultPlan("patient-context", fail_times, delay)
        self.requests: list[ContextRequest] = []

    def fetch(self, request: ContextRequest) -> ContextResponse:
        self.faults.before_call()
        self.requests.append(request)
        record = self.records.get(request.patient_id)
        if record is None:
            return ContextResponse(request.patient_id, CONTEXT_UNKNOWN)
        values = (dict(record) if self.leak_unrequested
                  else {k: v for k, v in record.items() if k in request.attributes})
        return ContextResponse(request.patient_id, CONTEXT_AVAILABLE, values,
                               self.source_timestamp)


class MockAuthProvider:
    """Maps opaque tokens to principals; unknown or expired tokens are denied."""

    def __init__(self, sessions: Mapping[str, Principal]) -> None:
        self.sessions = dict(sessions)

    def authenticate(self, token: str, *, at: datetime) -> Principal:
        principal = self.sessions.get(token)
        if principal is None or not principal.is_active(at):
            raise AccessDenied()
        return principal


class MockNotificationSender:
    """Records deliveries; can fail or time out for the first N sends.

    Sending the same notification ID twice delivers it only once.
    """

    def __init__(self, *, fail_times: int = 0, delay: float = 0.0) -> None:
        self.faults = _FaultPlan("notification", fail_times, delay)
        self.delivered: dict[str, NotificationRequest] = {}
        self._receipts: dict[str, DeliveryReceipt] = {}
        self._lock = Lock()

    @property
    def calls(self) -> int:
        return self.faults.calls

    def send(self, request: NotificationRequest) -> DeliveryReceipt:
        with self._lock:
            if request.notification_id in self._receipts:
                return self._receipts[request.notification_id]
        self.faults.before_call()
        with self._lock:
            receipt = DeliveryReceipt(request.notification_id, DeliveryStatus.SENT,
                                      utc_now(), provider_reference=f"mock-{len(self.delivered) + 1}")
            self.delivered[request.notification_id] = request
            self._receipts[request.notification_id] = receipt
            return receipt


class FaultyInventoryGateway:
    """Wraps an inventory gateway and fails the first N reservations."""

    def __init__(self, inner: InventoryGateway, *, fail_reserve_times: int = 0) -> None:
        self.inner = inner
        self.remaining = fail_reserve_times

    def apply_restock(self, event: RestockEvent) -> bool:
        return self.inner.apply_restock(event)

    def read(self, medicine_code: str, location_id: str) -> Inventory:
        return self.inner.read(medicine_code, location_id)

    def reservation_for(self, reservation_key: str) -> tuple[str, str, int] | None:
        return self.inner.reservation_for(reservation_key)

    def reserve(self, inventory_id: str, booking_id: str, quantity: int,
                reservation_key: str, *, at: datetime) -> bool:
        if self.remaining > 0:
            self.remaining -= 1
            raise ExternalServiceError("inventory")
        return self.inner.reserve(inventory_id, booking_id, quantity, reservation_key, at=at)

    def release(self, reservation_key: str, *, at: datetime) -> bool:
        return self.inner.release(reservation_key, at=at)


__all__ = [
    "AuthProvider", "CONTEXT_AVAILABLE", "CONTEXT_UNKNOWN", "ContextRequest",
    "ContextResponse", "DeliveryReceipt", "EXTERNAL_STATUS_MAP",
    "ExternalMedicationLine", "ExternalPrescription", "ExternalServiceError",
    "FaultyInventoryGateway", "InMemoryInventoryGateway", "InvalidPayload",
    "InventoryGateway", "MockAuthProvider", "MockNotificationSender",
    "MockPatientContextSource", "MockPrescriptionSource", "NotificationRequest",
    "NotificationSender", "PatientContextSource", "PrescriptionSourceAdapter",
    "ServiceTimeout", "call_external", "import_prescription",
    "parse_prescription_payload", "to_internal",
]
