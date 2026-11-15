"""Typed, dependency-free domain models aligned with SRS Appendix B."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4


def utc_now() -> datetime:
    """Return an aware UTC timestamp for persisted domain events."""
    return datetime.now(timezone.utc)


def new_record_id() -> str:
    """Generate a stable identifier for a new domain record."""
    return str(uuid4())


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class ActorRole(str, Enum):
    PATIENT = "PATIENT"
    CAREGIVER = "CAREGIVER"
    PHARMACIST = "PHARMACIST"
    PHARMACY_MANAGER = "PHARMACY_MANAGER"
    SYSTEM = "SYSTEM"
    ADMINISTRATOR = "ADMINISTRATOR"


class ConsentStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    GRANTED = "GRANTED"
    REVOKED = "REVOKED"


class PrescriptionSource(str, Enum):
    UPLOAD = "UPLOAD"
    STRUCTURED_IMPORT = "STRUCTURED_IMPORT"
    MANUAL = "MANUAL"


class PrescriptionStatus(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATION_PENDING = "VALIDATION_PENDING"
    VALID = "VALID"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


@dataclass(frozen=True, slots=True)
class PrescriptionStateChange:
    previous_status: PrescriptionStatus
    new_status: PrescriptionStatus
    changed_at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_aware(self.changed_at, "changed_at")
        if self.previous_status == self.new_status:
            raise ValueError("a state change must change the prescription status")


class MedicationRequestStatus(str, Enum):
    PENDING = "PENDING"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FULFILLED = "FULFILLED"


class BookingStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    ELIGIBILITY_PENDING = "ELIGIBILITY_PENDING"
    ELIGIBLE = "ELIGIBLE"
    RESERVED = "RESERVED"
    WAITLISTED = "WAITLISTED"
    CONFIRMED = "CONFIRMED"
    READY = "READY"
    FULFILLED = "FULFILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ContinuityStatus(str, Enum):
    CONTINUITY = "CONTINUITY"
    NO_CONTINUITY = "NO_CONTINUITY"
    UNKNOWN = "UNKNOWN"


class InventoryState(str, Enum):
    IN_STOCK = "IN_STOCK"
    QUARANTINED = "QUARANTINED"
    EXPIRED = "EXPIRED"
    RECALLED = "RECALLED"
    BLOCKED = "BLOCKED"


class WaitlistState(str, Enum):
    ACTIVE = "ACTIVE"
    OFFERED = "OFFERED"
    PROMOTED = "PROMOTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FULFILLED = "FULFILLED"


class NotificationChannel(str, Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    PUSH = "PUSH"
    IN_APP = "IN_APP"


class DeliveryStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class Patient:
    patient_id: str
    display_name: str
    external_patient_id: str | None = None
    contact_reference: str | None = None
    consent_status: ConsentStatus = ConsentStatus.UNKNOWN
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.patient_id.strip() or not self.display_name.strip():
            raise ValueError("patient_id and display_name are required")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class Prescription:
    prescription_id: str
    patient_id: str
    source: PrescriptionSource
    status: PrescriptionStatus
    source_identifier: str | None = None
    authored_on: datetime | None = None
    prescriber_reference: str | None = None
    validation_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    state_changes: tuple[PrescriptionStateChange, ...] = ()

    def __post_init__(self) -> None:
        if not self.prescription_id.strip() or not self.patient_id.strip():
            raise ValueError("prescription_id and patient_id are required")
        object.__setattr__(self, "state_changes", tuple(self.state_changes))
        _require_aware(self.created_at, "created_at")
        if self.authored_on is not None:
            _require_aware(self.authored_on, "authored_on")
        last_at = self.created_at
        for index, change in enumerate(self.state_changes):
            if change.changed_at < last_at:
                raise ValueError("prescription state changes must be chronological")
            if index and change.previous_status != self.state_changes[index - 1].new_status:
                raise ValueError("prescription state changes must form a continuous history")
            last_at = change.changed_at
        if self.state_changes and self.status != self.state_changes[-1].new_status:
            raise ValueError("prescription status must match its latest state change")


def transition_prescription(
    prescription: Prescription,
    new_status: PrescriptionStatus,
    *,
    at: datetime | None = None,
    reason: str | None = None,
) -> Prescription:
    """Record a validation-state change without losing the original record."""
    changed_at = at if at is not None else utc_now()
    change = PrescriptionStateChange(prescription.status, new_status, changed_at, reason)
    if changed_at < (prescription.state_changes[-1].changed_at if prescription.state_changes else prescription.created_at):
        raise ValueError("transition time cannot precede the last state change")
    return replace(
        prescription,
        status=new_status,
        validation_reason=reason,
        state_changes=prescription.state_changes + (change,),
    )


@dataclass(frozen=True, slots=True)
class MedicationRequest:
    medication_request_id: str
    prescription_id: str
    requested_quantity: int
    medicine_code: str | None = None
    medicine_name: str | None = None
    dosage_text: str | None = None
    request_status: MedicationRequestStatus = MedicationRequestStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.medication_request_id.strip() or not self.prescription_id.strip():
            raise ValueError("medication_request_id and prescription_id are required")
        if not (self.medicine_code and self.medicine_code.strip()) and not (
            self.medicine_name and self.medicine_name.strip()
        ):
            raise ValueError("medicine_code or medicine_name is required")
        if self.requested_quantity <= 0:
            raise ValueError("requested_quantity must be greater than zero")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class Booking:
    booking_id: str
    patient_id: str
    prescription_id: str
    medication_request_id: str
    requested_quantity: int
    booking_state: BookingStatus = BookingStatus.SUBMITTED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not all((self.booking_id.strip(), self.patient_id.strip(), self.prescription_id.strip(), self.medication_request_id.strip())):
            raise ValueError("booking and relationship identifiers are required")
        if self.requested_quantity <= 0:
            raise ValueError("requested_quantity must be greater than zero")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")


@dataclass(frozen=True, slots=True)
class PrescriptionRecord:
    """A linked intake record with one or more medication requests and bookings."""

    patient: Patient
    prescription: Prescription
    medication_requests: tuple[MedicationRequest, ...]
    bookings: tuple[Booking, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "medication_requests", tuple(self.medication_requests))
        object.__setattr__(self, "bookings", tuple(self.bookings))
        if self.prescription.patient_id != self.patient.patient_id:
            raise ValueError("prescription patient does not match the intake patient")
        if not self.medication_requests:
            raise ValueError("a prescription requires at least one medication request")
        request_ids: set[str] = set()
        for request in self.medication_requests:
            if request.prescription_id != self.prescription.prescription_id:
                raise ValueError("medication request does not belong to this prescription")
            if request.medication_request_id in request_ids:
                raise ValueError("duplicate medication request identifier")
            request_ids.add(request.medication_request_id)
        booking_ids: set[str] = set()
        for booking in self.bookings:
            if booking.booking_id in booking_ids:
                raise ValueError("duplicate booking identifier")
            booking_ids.add(booking.booking_id)
            if booking.patient_id != self.patient.patient_id or booking.prescription_id != self.prescription.prescription_id:
                raise ValueError("booking does not belong to this patient and prescription")
            if booking.medication_request_id not in request_ids:
                raise ValueError("booking does not reference a medication request in this prescription")


_BOOKING_TRANSITIONS: Mapping[BookingStatus, frozenset[BookingStatus]] = {
    BookingStatus.SUBMITTED: frozenset({BookingStatus.ELIGIBILITY_PENDING}),
    BookingStatus.ELIGIBILITY_PENDING: frozenset({BookingStatus.ELIGIBLE, BookingStatus.REJECTED}),
    BookingStatus.ELIGIBLE: frozenset({BookingStatus.RESERVED, BookingStatus.WAITLISTED}),
    BookingStatus.WAITLISTED: frozenset({BookingStatus.RESERVED, BookingStatus.CANCELLED, BookingStatus.EXPIRED}),
    BookingStatus.RESERVED: frozenset({BookingStatus.CONFIRMED, BookingStatus.CANCELLED, BookingStatus.EXPIRED}),
    BookingStatus.CONFIRMED: frozenset({BookingStatus.READY}),
    BookingStatus.READY: frozenset({BookingStatus.FULFILLED, BookingStatus.CANCELLED}),
    BookingStatus.FULFILLED: frozenset(),
    BookingStatus.REJECTED: frozenset(),
    BookingStatus.CANCELLED: frozenset(),
    BookingStatus.EXPIRED: frozenset(),
}


def transition_booking(booking: Booking, new_state: BookingStatus, *, at: datetime | None = None) -> Booking:
    """Return a new booking with a validated SRS lifecycle transition."""
    if new_state not in _BOOKING_TRANSITIONS[booking.booking_state]:
        raise ValueError(f"invalid booking transition: {booking.booking_state} -> {new_state}")
    changed_at = at or utc_now()
    _require_aware(changed_at, "at")
    if changed_at < booking.updated_at:
        raise ValueError("transition time cannot precede the last update")
    return replace(booking, booking_state=new_state, updated_at=changed_at)


@dataclass(frozen=True, slots=True)
class PriorityContext:
    booking_id: str
    verified_urgency: str
    urgency_source: str
    continuity_status: ContinuityStatus
    waiting_since: datetime
    actor_role: ActorRole
    policy_version: str
    reason_codes: tuple[str, ...] = ()
    evaluated_at: datetime = field(default_factory=utc_now)
    urgency_actor_id: str | None = None
    urgency_verified_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all((self.booking_id.strip(), self.verified_urgency.strip(), self.urgency_source.strip(), self.policy_version.strip())):
            raise ValueError("booking_id, urgency, urgency_source, and policy_version are required")
        _require_aware(self.waiting_since, "waiting_since")
        _require_aware(self.evaluated_at, "evaluated_at")
        if self.urgency_actor_id is not None and not self.urgency_actor_id.strip():
            raise ValueError("urgency_actor_id cannot be blank")
        if self.urgency_verified_at is not None:
            _require_aware(self.urgency_verified_at, "urgency_verified_at")


@dataclass(frozen=True, slots=True)
class Inventory:
    inventory_id: str
    medicine_code: str
    location_id: str
    on_hand_quantity: int
    reserved_quantity: int
    inventory_state: InventoryState
    batch_id: str | None = None
    expiry_date: date | None = None
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not all((self.inventory_id.strip(), self.medicine_code.strip(), self.location_id.strip())):
            raise ValueError("inventory_id, medicine_code, and location_id are required")
        if self.on_hand_quantity < 0 or self.reserved_quantity < 0:
            raise ValueError("inventory quantities cannot be negative")
        if self.reserved_quantity > self.on_hand_quantity:
            raise ValueError("reserved_quantity cannot exceed on_hand_quantity")
        _require_aware(self.updated_at, "updated_at")

    @property
    def allocatable_quantity(self) -> int:
        if self.inventory_state is not InventoryState.IN_STOCK:
            return 0
        return self.on_hand_quantity - self.reserved_quantity


@dataclass(frozen=True, slots=True)
class WaitlistEntry:
    waitlist_id: str
    booking_id: str
    medicine_code: str
    location_id: str
    state: WaitlistState = WaitlistState.ACTIVE
    entered_at: datetime = field(default_factory=utc_now)
    offered_at: datetime | None = None
    reservation_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all((self.waitlist_id.strip(), self.booking_id.strip(), self.medicine_code.strip(), self.location_id.strip())):
            raise ValueError("waitlist identifiers are required")
        _require_aware(self.entered_at, "entered_at")
        if self.offered_at is not None:
            _require_aware(self.offered_at, "offered_at")
        if self.reservation_expires_at is not None:
            _require_aware(self.reservation_expires_at, "reservation_expires_at")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    audit_id: str
    actor_id: str
    actor_role: ActorRole
    event_type: str
    object_type: str
    object_id: str
    timestamp: datetime = field(default_factory=utc_now)
    previous_state: Mapping[str, Any] | None = None
    new_state: Mapping[str, Any] | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not all((self.audit_id.strip(), self.actor_id.strip(), self.event_type.strip(), self.object_type.strip(), self.object_id.strip())):
            raise ValueError("audit identifiers and event metadata are required")
        _require_aware(self.timestamp, "timestamp")


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    notification_id: str
    booking_id: str
    recipient_reference: str
    channel: NotificationChannel
    template_id: str
    delivery_status: DeliveryStatus = DeliveryStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    delivered_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all((self.notification_id.strip(), self.booking_id.strip(), self.recipient_reference.strip(), self.template_id.strip())):
            raise ValueError("notification identifiers and routing metadata are required")
        _require_aware(self.created_at, "created_at")
        if self.delivered_at is not None:
            _require_aware(self.delivered_at, "delivered_at")
            if self.delivered_at < self.created_at:
                raise ValueError("delivered_at cannot precede created_at")
