"""MBA-12: create medicine bookings and track their eligibility.

``BookingService`` shares state, lock, and audit trail with ``WaitlistService``
so a booking can later move to the waitlist without two copies of its state
disagreeing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .access import (
    BOOKING_CREATE_ANY, BOOKING_CREATE_OWN, PRESCRIPTION_REVIEW, Principal,
    authorize_patient_record,
)
from .domain import (
    ActorRole, AuditEvent, Booking, BookingStatus, ContinuityStatus,
    MedicationRequest, MedicationRequestStatus, Prescription, PrescriptionStatus,
    PriorityContext, new_record_id, transition_booking, utc_now,
)
from .waitlist import WaitlistService


class DuplicateAction(str, Enum):
    REJECT = "REJECT"
    FLAG = "FLAG"


class DuplicateMatch(str, Enum):
    # Same patient asking for the same medicine again, from any prescription.
    SAME_MEDICINE = "SAME_MEDICINE"
    # Same patient booking the same medication request line again.
    SAME_REQUEST = "SAME_REQUEST"


# States in which a booking still holds or is waiting for stock.
ACTIVE_BOOKING_STATES = frozenset({
    BookingStatus.SUBMITTED, BookingStatus.ELIGIBILITY_PENDING,
    BookingStatus.ELIGIBLE, BookingStatus.WAITLISTED, BookingStatus.RESERVED,
    BookingStatus.CONFIRMED, BookingStatus.READY,
})
_REVIEW_PENDING = frozenset({
    PrescriptionStatus.RECEIVED, PrescriptionStatus.VALIDATION_PENDING,
    PrescriptionStatus.MANUAL_REVIEW_REQUIRED,
})
_CLOSED_REQUEST = frozenset({
    MedicationRequestStatus.REJECTED, MedicationRequestStatus.CANCELLED,
    MedicationRequestStatus.FULFILLED,
})


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class BookingPolicy:
    """Configurable booking rules (REQ-PB-25, REQ-PB-26)."""

    duplicate_action: DuplicateAction = DuplicateAction.REJECT
    duplicate_match: DuplicateMatch = DuplicateMatch.SAME_MEDICINE
    # When set, an eligible booking gets this starting urgency from a trusted
    # system source until a pharmacist confirms a different one. Patients
    # never supply urgency.
    default_urgency: str | None = None
    default_urgency_source: str = "booking-service"
    policy_version: str = "v1"

    def __post_init__(self) -> None:
        if self.default_urgency is not None and not self.default_urgency.strip():
            raise ValueError("default urgency cannot be blank")
        if not self.default_urgency_source.strip() or not self.policy_version.strip():
            raise ValueError("urgency source and policy version are required")


@dataclass(frozen=True, slots=True)
class BookingOutcome:
    """Result of a booking request. ``booking`` is None when no record was created."""

    booking: Booking | None
    reason_codes: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.booking is not None and self.booking.booking_state is BookingStatus.ELIGIBLE


class BookingService:
    """Prototype booking workflow. Persist its state in a real deployment."""

    def __init__(self, waitlist: WaitlistService, *,
                 policy: BookingPolicy | None = None) -> None:
        self.waitlist = waitlist
        self.policy = policy if policy is not None else BookingPolicy()
        self.rejection_reasons: dict[str, tuple[str, ...]] = {}
        self.hold_reasons: dict[str, tuple[str, ...]] = {}
        self.flags: dict[str, tuple[str, ...]] = {}
        # One lock for both services keeps booking and waitlist state consistent.
        self._lock = waitlist._lock

    # Shared state lives in the waitlist service.
    @property
    def bookings(self) -> dict[str, Booking]:
        return self.waitlist.bookings

    @property
    def prescriptions(self) -> dict[str, Prescription]:
        return self.waitlist.prescriptions

    @property
    def medication_requests(self) -> dict[str, MedicationRequest]:
        return self.waitlist.medication_requests

    @property
    def audit_events(self) -> list[AuditEvent]:
        return self.waitlist.audit_events

    def submit(self, principal: Principal, *, prescription_id: str,
               medication_request_id: str, quantity: int | None = None,
               at: datetime | None = None,
               booking_id: str | None = None) -> BookingOutcome:
        """Create a booking and decide whether it may progress automatically."""
        when = at if at is not None else utc_now()
        _aware(when)
        with self._lock:
            prescription = self.prescriptions.get(prescription_id)
            if prescription is None:
                # Same error as "not yours" so IDs cannot be probed.
                principal.require(BOOKING_CREATE_ANY, at=when)
                raise ValueError("prescription not found")
            authorize_patient_record(
                principal, prescription.patient_id, own_permission=BOOKING_CREATE_OWN,
                any_permission=BOOKING_CREATE_ANY, at=when,
            )
            request = self.medication_requests.get(medication_request_id)
            if request is None or request.prescription_id != prescription_id:
                return self._reject_request(principal, prescription_id,
                                            "MEDICINE_NOT_ON_PRESCRIPTION", when)
            requested = request.requested_quantity if quantity is None else quantity
            if isinstance(requested, bool) or not isinstance(requested, int) or requested <= 0:
                return self._reject_request(principal, prescription_id,
                                            "INVALID_QUANTITY", when)
            if requested != request.requested_quantity:
                return self._reject_request(principal, prescription_id,
                                            "QUANTITY_DOES_NOT_MATCH_PRESCRIPTION", when)

            new_id = booking_id if booking_id is not None else new_record_id()
            if new_id in self.bookings:
                raise ValueError("booking identifier already exists")
            booking = Booking(new_id, prescription.patient_id, prescription_id,
                              medication_request_id, requested,
                              created_at=when, updated_at=when)
            self._audit(principal, "BOOKING_SUBMITTED", new_id, when,
                        current=BookingStatus.SUBMITTED.value)
            booking = transition_booking(booking, BookingStatus.ELIGIBILITY_PENDING, at=when)

            reasons: list[str] = []
            flags: list[str] = []
            duplicate = self._find_duplicate(booking, request)
            if duplicate is not None:
                if self.policy.duplicate_action is DuplicateAction.REJECT:
                    reasons.append("DUPLICATE_ACTIVE_BOOKING")
                else:
                    flags.append("POSSIBLE_DUPLICATE")
                    self._audit(principal, "BOOKING_DUPLICATE_FLAGGED", new_id, when,
                                reason=f"existing_booking={duplicate}")
            if prescription.status in {PrescriptionStatus.INVALID, PrescriptionStatus.EXPIRED}:
                reasons.append(f"PRESCRIPTION_{prescription.status.value}")
            if request.request_status in _CLOSED_REQUEST:
                reasons.append("MEDICATION_REQUEST_NOT_ELIGIBLE")

            if reasons:
                booking = self._reject(principal, booking, tuple(reasons), when)
                result = tuple(reasons)
            else:
                hold = self._hold_reasons(prescription, request)
                if hold:
                    self.hold_reasons[new_id] = hold
                    self._audit(principal, "BOOKING_HELD_FOR_REVIEW", new_id, when,
                                current=BookingStatus.ELIGIBILITY_PENDING.value,
                                reason=",".join(hold))
                    result = hold
                else:
                    booking = self._make_eligible(principal, booking, when)
                    result = ()
            self.bookings[new_id] = booking
            if flags:
                self.flags[new_id] = tuple(flags)
            return BookingOutcome(booking, result, tuple(flags))

    def recheck_eligibility(self, principal: Principal, booking_id: str, *,
                            at: datetime | None = None) -> BookingOutcome:
        """Re-run eligibility after a pharmacist finishes prescription review."""
        when = at if at is not None else utc_now()
        _aware(when)
        principal.require(PRESCRIPTION_REVIEW, at=when)
        with self._lock:
            booking = self.bookings[booking_id]
            if booking.booking_state is not BookingStatus.ELIGIBILITY_PENDING:
                raise ValueError("only a booking awaiting eligibility can be rechecked")
            prescription = self.prescriptions[booking.prescription_id]
            request = self.medication_requests[booking.medication_request_id]
            reasons: list[str] = []
            if prescription.status in {PrescriptionStatus.INVALID, PrescriptionStatus.EXPIRED}:
                reasons.append(f"PRESCRIPTION_{prescription.status.value}")
            if request.request_status in _CLOSED_REQUEST:
                reasons.append("MEDICATION_REQUEST_NOT_ELIGIBLE")
            if reasons:
                self.hold_reasons.pop(booking_id, None)
                updated = self._reject(principal, booking, tuple(reasons), when)
                self.bookings[booking_id] = updated
                return BookingOutcome(updated, tuple(reasons))
            hold = self._hold_reasons(prescription, request)
            if hold:
                self.hold_reasons[booking_id] = hold
                return BookingOutcome(booking, hold)
            self.hold_reasons.pop(booking_id, None)
            updated = self._make_eligible(principal, booking, when)
            self.bookings[booking_id] = updated
            return BookingOutcome(updated, ())

    # --------------------------------------------------------------- helpers

    def _find_duplicate(self, booking: Booking, request: MedicationRequest) -> str | None:
        for other in self.bookings.values():
            if (other.patient_id != booking.patient_id
                    or other.booking_state not in ACTIVE_BOOKING_STATES):
                continue
            if self.policy.duplicate_match is DuplicateMatch.SAME_REQUEST:
                if other.medication_request_id == request.medication_request_id:
                    return other.booking_id
                continue
            other_request = self.medication_requests.get(other.medication_request_id)
            if other_request is not None and _same_medicine(other_request, request):
                return other.booking_id
        return None

    @staticmethod
    def _hold_reasons(prescription: Prescription,
                      request: MedicationRequest) -> tuple[str, ...]:
        if prescription.status in _REVIEW_PENDING:
            return ("PRESCRIPTION_AWAITING_REVIEW",)
        if request.request_status is MedicationRequestStatus.PENDING:
            return ("MEDICATION_REQUEST_PENDING",)
        return ()

    def _make_eligible(self, principal: Principal, booking: Booking,
                       when: datetime) -> Booking:
        updated = transition_booking(booking, BookingStatus.ELIGIBLE, at=when)
        self._audit(principal, "BOOKING_ELIGIBLE", booking.booking_id, when,
                    previous=BookingStatus.ELIGIBILITY_PENDING.value,
                    current=BookingStatus.ELIGIBLE.value)
        if (self.policy.default_urgency is not None
                and booking.booking_id not in self.waitlist.contexts):
            self.waitlist.contexts[booking.booking_id] = PriorityContext(
                booking.booking_id, self.policy.default_urgency,
                self.policy.default_urgency_source, ContinuityStatus.UNKNOWN,
                booking.created_at, ActorRole.SYSTEM, self.policy.policy_version,
                reason_codes=("DEFAULT_URGENCY",), evaluated_at=when,
            )
        return updated

    def _reject(self, principal: Principal, booking: Booking,
                reasons: tuple[str, ...], when: datetime) -> Booking:
        updated = transition_booking(booking, BookingStatus.REJECTED, at=when)
        self.rejection_reasons[booking.booking_id] = reasons
        self._audit(principal, "BOOKING_REJECTED", booking.booking_id, when,
                    previous=booking.booking_state.value,
                    current=BookingStatus.REJECTED.value, reason=",".join(reasons))
        return updated

    def _reject_request(self, principal: Principal, prescription_id: str,
                        reason: str, when: datetime) -> BookingOutcome:
        # No booking record exists, so the reason is kept against the prescription.
        self.audit_events.append(AuditEvent(
            new_record_id(), principal.actor_id, principal.actor_role,
            "BOOKING_REQUEST_REJECTED", "Prescription", prescription_id,
            timestamp=when, reason=reason,
        ))
        return BookingOutcome(None, (reason,))

    def _audit(self, principal: Principal, event_type: str, booking_id: str,
               when: datetime, *, previous: str | None = None,
               current: str | None = None, reason: str | None = None) -> None:
        self.audit_events.append(AuditEvent(
            new_record_id(), principal.actor_id, principal.actor_role, event_type,
            "Booking", booking_id, timestamp=when,
            previous_state={"state": previous} if previous is not None else None,
            new_state={"state": current} if current is not None else None,
            reason=reason,
        ))


def _same_medicine(left: MedicationRequest, right: MedicationRequest) -> bool:
    if left.medicine_code and right.medicine_code:
        return left.medicine_code == right.medicine_code
    if left.medicine_name and right.medicine_name:
        return left.medicine_name.strip().casefold() == right.medicine_name.strip().casefold()
    return False


__all__ = [
    "ACTIVE_BOOKING_STATES", "BookingOutcome", "BookingPolicy", "BookingService",
    "DuplicateAction", "DuplicateMatch",
]
