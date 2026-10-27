"""MBA-11: minimal patient context with consent and an UNKNOWN fallback.

The allocation policy needs only one fact about a patient's history: whether
this request continues an existing therapy. This module asks the external
source for exactly the configured attributes, only with valid consent, and
turns anything missing, unclear, or unavailable into UNKNOWN. It never guesses
a value from other medical information (REQ-EP-12..15, BR-13).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

from .access import BOOKING_CREATE_OWN, PRESCRIPTION_REVIEW, Principal, authorize_patient_record
from .contracts import (
    CONTEXT_AVAILABLE, CONTEXT_UNKNOWN, ContextRequest, ExternalServiceError,
    PatientContextSource, call_external,
)
from .domain import (
    ActorRole, AuditEvent, ConsentStatus, ContinuityStatus, PriorityContext,
    new_record_id, utc_now,
)

CONTINUITY_ATTRIBUTE = "continuity_of_therapy"
ALLOCATION_PURPOSE = "medicine-allocation"


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    """Who allowed which lookup, when, and until when (REQ-EP-14)."""

    patient_id: str
    purpose: str
    status: ConsentStatus
    reference: str
    recorded_by: str
    recorded_at: datetime
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not all((self.patient_id.strip(), self.purpose.strip(),
                    self.reference.strip(), self.recorded_by.strip())):
            raise ValueError("consent patient, purpose, reference, and recorder are required")
        _aware(self.recorded_at, "recorded_at")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
            if self.expires_at <= self.recorded_at:
                raise ValueError("consent must expire after it was recorded")

    def allows(self, at: datetime) -> bool:
        return (self.status is ConsentStatus.GRANTED
                and (self.expires_at is None or at < self.expires_at))


@dataclass(frozen=True, slots=True)
class PatientContextPolicy:
    """Which attributes allocation may see, and whether consent is required."""

    attributes: frozenset[str] = frozenset({CONTINUITY_ATTRIBUTE})
    purpose: str = ALLOCATION_PURPOSE
    consent_required: bool = True
    timeout_seconds: float = 2.0
    attempts: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", frozenset(self.attributes))
        if CONTINUITY_ATTRIBUTE not in self.attributes:
            raise ValueError("the continuity attribute must be configured")
        if any(not name.strip() for name in self.attributes) or not self.purpose.strip():
            raise ValueError("attribute names and purpose cannot be blank")
        if self.timeout_seconds <= 0 or self.attempts < 1:
            raise ValueError("timeout and attempts must be positive")


@dataclass(frozen=True, slots=True)
class PatientContextResult:
    """What allocation receives: only configured attributes, plus provenance."""

    patient_id: str
    continuity: ContinuityStatus
    status: str
    retrieved_at: datetime
    values: Mapping[str, Any] = field(default_factory=dict)
    reason: str | None = None
    consent_reference: str | None = None
    source_timestamp: datetime | None = None


class PatientContextService:
    def __init__(self, source: PatientContextSource, *,
                 policy: PatientContextPolicy | None = None,
                 audit_events: list[AuditEvent] | None = None) -> None:
        self.source = source
        self.policy = policy if policy is not None else PatientContextPolicy()
        self.consents: dict[tuple[str, str], ConsentRecord] = {}
        self.consent_history: list[ConsentRecord] = []
        self.audit_events = audit_events if audit_events is not None else []
        self._lock = RLock()

    def record_consent(self, principal: Principal, patient_id: str, status: ConsentStatus, *,
                       reference: str, at: datetime | None = None,
                       expires_at: datetime | None = None,
                       purpose: str | None = None) -> ConsentRecord:
        """Record a patient's consent decision.

        A patient or linked caregiver may record their own; a pharmacist may
        record one on the patient's behalf. Earlier records are kept.
        """
        when = at if at is not None else utc_now()
        _aware(when, "at")
        authorize_patient_record(principal, patient_id, own_permission=BOOKING_CREATE_OWN,
                                 any_permission=PRESCRIPTION_REVIEW, at=when)
        record = ConsentRecord(patient_id, purpose or self.policy.purpose, status, reference,
                               principal.actor_id, when, expires_at)
        with self._lock:
            self.consents[(patient_id, record.purpose)] = record
            self.consent_history.append(record)
            self.audit_events.append(AuditEvent(
                new_record_id(), principal.actor_id, principal.actor_role, "CONSENT_RECORDED",
                "Patient", patient_id, timestamp=when,
                new_state={"status": status.value, "purpose": record.purpose},
                reason=f"consent_reference={reference}",
            ))
        return record

    def lookup(self, patient_id: str, *, at: datetime | None = None) -> PatientContextResult:
        """Return continuity context, or UNKNOWN with a reason. Never raises for
        source problems, so a booking can still be ranked."""
        when = at if at is not None else utc_now()
        _aware(when, "at")
        with self._lock:
            consent = self.consents.get((patient_id, self.policy.purpose))
        reference = consent.reference if consent is not None else None
        if self.policy.consent_required and (consent is None or not consent.allows(when)):
            return self._unknown(patient_id, when, "CONSENT_NOT_GRANTED", reference)
        request = ContextRequest(patient_id, self.policy.attributes, self.policy.purpose,
                                 reference)
        try:
            response = call_external("patient-context", lambda: self.source.fetch(request),
                                     timeout=self.policy.timeout_seconds,
                                     attempts=self.policy.attempts)
        except ExternalServiceError as exc:
            return self._unknown(patient_id, when, f"SOURCE_{exc.code}", reference)
        if response.patient_id != patient_id:
            return self._unknown(patient_id, when, "SOURCE_PATIENT_MISMATCH", reference)
        if response.status != CONTEXT_AVAILABLE:
            return self._unknown(patient_id, when, "SOURCE_HAS_NO_CONTEXT", reference)

        # Keep only what policy allows, even if the source sent more.
        values = {k: v for k, v in response.values.items() if k in self.policy.attributes}
        discarded = len(response.values) - len(values)
        raw = values.get(CONTINUITY_ATTRIBUTE)
        # Only an explicit boolean counts. Strings, numbers, or a missing
        # value are not interpreted.
        if raw is True:
            continuity = ContinuityStatus.CONTINUITY
        elif raw is False:
            continuity = ContinuityStatus.NO_CONTINUITY
        else:
            continuity = ContinuityStatus.UNKNOWN
        status = CONTEXT_AVAILABLE if continuity is not ContinuityStatus.UNKNOWN else CONTEXT_UNKNOWN
        reason = None if status == CONTEXT_AVAILABLE else "CONTINUITY_NOT_PROVIDED"
        result = PatientContextResult(
            patient_id, continuity, status, when, MappingProxyType(values), reason,
            reference, response.source_timestamp,
        )
        self._audit(result, discarded)
        return result

    def _unknown(self, patient_id: str, when: datetime, reason: str,
                 reference: str | None) -> PatientContextResult:
        result = PatientContextResult(patient_id, ContinuityStatus.UNKNOWN, CONTEXT_UNKNOWN,
                                      when, MappingProxyType({}), reason, reference)
        self._audit(result, 0)
        return result

    def _audit(self, result: PatientContextResult, discarded: int) -> None:
        # Records that a lookup happened and its outcome, never the values.
        reasons = [f"status={result.status}"]
        if result.reason:
            reasons.append(f"reason={result.reason}")
        if result.consent_reference:
            reasons.append(f"consent_reference={result.consent_reference}")
        if discarded:
            reasons.append(f"discarded_unrequested_fields={discarded}")
        with self._lock:
            self.audit_events.append(AuditEvent(
                new_record_id(), "patient-context-service", ActorRole.SYSTEM,
                "PATIENT_CONTEXT_LOOKUP", "Patient", result.patient_id,
                timestamp=result.retrieved_at, reason=";".join(reasons),
            ))


def apply_to_context(context: PriorityContext, result: PatientContextResult) -> PriorityContext:
    """Return a priority context carrying the looked-up continuity value."""
    if context.evaluated_at > result.retrieved_at:
        raise ValueError("context result is older than the priority context")
    codes = tuple(code for code in context.reason_codes if not code.startswith("CONTEXT_"))
    return replace(context, continuity_status=result.continuity,
                   reason_codes=codes + (f"CONTEXT_{result.status}",),
                   evaluated_at=result.retrieved_at)


def refresh_continuity(service, contexts: PatientContextService, booking_id: str, *,
                       at: datetime | None = None) -> PatientContextResult:
    """Look up continuity for a booking's patient and store it in the waitlist
    contexts, so the next priority evaluation uses it (REQ-PB-18)."""
    when = at if at is not None else utc_now()
    booking = service.bookings[booking_id]
    result = contexts.lookup(booking.patient_id, at=when)
    current = service.waitlist.contexts.get(booking_id)
    if current is not None:
        service.waitlist.contexts[booking_id] = apply_to_context(current, result)
    return result


__all__ = [
    "ALLOCATION_PURPOSE", "CONTINUITY_ATTRIBUTE", "ConsentRecord", "PatientContextPolicy",
    "PatientContextResult", "PatientContextService", "apply_to_context",
    "refresh_continuity",
]
