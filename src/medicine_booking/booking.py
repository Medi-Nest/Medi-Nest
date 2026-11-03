"""MBA-12 and MBA-17: booking creation, direct allocation, partial allocation,
reservation expiry, and cancellation.

``BookingService`` shares state, lock, and audit trail with ``WaitlistService``
so a booking can move between direct reservation and the waitlist without two
copies of its state disagreeing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import Enum

from .access import (
    ALLOCATION_RUN, BOOKING_CANCEL_ANY, BOOKING_CANCEL_OWN, BOOKING_CREATE_ANY,
    BOOKING_CREATE_OWN, PRESCRIPTION_REVIEW, RESERVATION_EXPIRE, Principal,
    authorize_patient_record,
)
from .domain import (
    ActorRole, AuditEvent, Booking, BookingStatus, ContinuityStatus,
    MedicationRequest, MedicationRequestStatus, Prescription, PrescriptionStatus,
    PriorityContext, WaitlistEntry, WaitlistState, new_record_id,
    transition_booking, utc_now,
)
from .safety import check_allocation_safety
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
_STOCK_HOLDING = frozenset({BookingStatus.RESERVED, BookingStatus.READY})


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class BookingPolicy:
    """Configurable booking rules (REQ-PB-25/26, REQ-AW-21/24)."""

    duplicate_action: DuplicateAction = DuplicateAction.REJECT
    duplicate_match: DuplicateMatch = DuplicateMatch.SAME_MEDICINE
    partial_allocation: bool = False
    reservation_duration: timedelta | None = None
    # When set, an eligible booking gets this starting urgency from a trusted
    # system source until a pharmacist confirms a different one. Patients
    # never supply urgency.
    default_urgency: str | None = None
    default_urgency_source: str = "booking-service"
    policy_version: str = "v1"

    def __post_init__(self) -> None:
        if self.reservation_duration is not None and self.reservation_duration <= timedelta():
            raise ValueError("reservation duration must be positive")
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


@dataclass(frozen=True, slots=True)
class Allocation:
    """A direct stock reservation for one booking (REQ-AW-06/07/08/26)."""

    booking_id: str
    inventory_id: str
    medicine_code: str
    location_id: str
    requested_quantity: int
    allocated_quantity: int
    reservation_key: str
    reserved_at: datetime
    expires_at: datetime | None = None
    batch_id: str | None = None
    batch_expiry_date: date | None = None
    released_at: datetime | None = None
    release_reason: str | None = None

    def __post_init__(self) -> None:
        if not 0 < self.allocated_quantity <= self.requested_quantity:
            raise ValueError("allocated quantity must be between 1 and the requested quantity")
        _aware(self.reserved_at)
        for value in (self.expires_at, self.released_at):
            if value is not None:
                _aware(value)

    @property
    def remaining_quantity(self) -> int:
        """Quantity still unfilled. It is never also waitlisted (REQ-AW-27)."""
        return self.requested_quantity - self.allocated_quantity

    @property
    def active(self) -> bool:
        return self.released_at is None


@dataclass(frozen=True, slots=True)
class AllocationOutcome:
    booking_id: str
    booking_state: BookingStatus
    allocation: Allocation | None = None
    waitlist_entry: WaitlistEntry | None = None
    reason_codes: tuple[str, ...] = ()


class BookingService:
    """Prototype booking workflow. Persist its state in a real deployment."""

    def __init__(self, waitlist: WaitlistService, *,
                 policy: BookingPolicy | None = None) -> None:
        self.waitlist = waitlist
        self.policy = policy if policy is not None else BookingPolicy()
        self.allocations: dict[str, Allocation] = {}
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

    # ------------------------------------------------------------------ MBA-12

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

    # ------------------------------------------------------------------ MBA-17

    def allocate(self, principal: Principal, booking_id: str, location_id: str, *,
                 at: datetime | None = None) -> AllocationOutcome:
        """Reserve stock for an eligible booking, or waitlist it (SRS 5.3.2 A, B, D)."""
        when = at if at is not None else utc_now()
        _aware(when)
        principal.require(ALLOCATION_RUN, at=when)
        with self._lock:
            booking = self.bookings[booking_id]
            if booking.booking_state is not BookingStatus.ELIGIBLE:
                raise ValueError("only an eligible booking can be allocated")
            prescription = self.prescriptions[booking.prescription_id]
            request = self.medication_requests[booking.medication_request_id]
            if not request.medicine_code:
                raise ValueError("a medicine code is required for allocation")
            key = f"booking:{booking_id}"
            for _ in range(2):
                # Re-read inventory on every attempt (REQ-AW-01, REQ-AW-32).
                inventory = self.waitlist.inventory.read(request.medicine_code, location_id)
                decision = check_allocation_safety(
                    booking, prescription, request, inventory, at=when,
                    policy=self.waitlist.safety_policy,
                    review=self.waitlist.reviews.get(booking_id),
                )
                quantity = booking.requested_quantity
                if not decision.allowed:
                    if set(decision.reason_codes) != {"INSUFFICIENT_ALLOCATABLE_STOCK"}:
                        self._audit(principal, "ALLOCATION_BLOCKED", booking_id, when,
                                    reason=",".join(decision.reason_codes))
                        return AllocationOutcome(booking_id, booking.booking_state,
                                                 reason_codes=decision.reason_codes)
                    available = decision.allocatable_quantity
                    if not (self.policy.partial_allocation and available > 0):
                        return self._waitlist(principal, booking_id, request.medicine_code,
                                              location_id, when)
                    quantity = available
                    partial = check_allocation_safety(
                        booking, prescription, request, inventory, at=when,
                        quantity_to_allocate=quantity,
                        policy=self.waitlist.safety_policy,
                        review=self.waitlist.reviews.get(booking_id),
                    )
                    if not partial.allowed:
                        return self._waitlist(principal, booking_id, request.medicine_code,
                                              location_id, when)
                try:
                    reserved = self.waitlist.inventory.reserve(
                        inventory.inventory_id, booking_id, quantity, key, at=when)
                    failure = "RESERVATION_CONFLICT"
                except Exception as exc:  # adapter failure must not corrupt state
                    reserved = False
                    failure = type(exc).__name__
                if reserved:
                    return self._record_reservation(principal, booking, inventory,
                                                     quantity, key, location_id, when)
                self._audit(principal, "ALLOCATION_FAILED", booking_id, when, reason=failure)
            return AllocationOutcome(booking_id, booking.booking_state,
                                     reason_codes=("RESERVATION_CONFLICT",))

    def cancel(self, principal: Principal, booking_id: str, *,
               at: datetime | None = None) -> Booking:
        """Cancel a booking, release any stock, and re-offer it (SRS 5.3.2 F)."""
        when = at if at is not None else utc_now()
        _aware(when)
        with self._lock:
            booking = self.bookings.get(booking_id)
            if booking is None:
                principal.require(BOOKING_CANCEL_ANY, at=when)
                raise ValueError("booking not found")
            authorize_patient_record(
                principal, booking.patient_id, own_permission=BOOKING_CANCEL_OWN,
                any_permission=BOOKING_CANCEL_ANY, at=when,
            )
            if when < booking.updated_at:
                raise ValueError("cancellation cannot precede the last booking update")
            allocation = self.allocations.get(booking_id)
            if allocation is not None and allocation.active and booking.booking_state in _STOCK_HOLDING:
                self._release(allocation, "CANCELLED", when)
                updated = transition_booking(booking, BookingStatus.CANCELLED, at=when)
                self.bookings[booking_id] = updated
                self._audit(principal, "BOOKING_CANCELLED", booking_id, when,
                            previous=booking.booking_state.value,
                            current=BookingStatus.CANCELLED.value)
                self._notify(booking_id, "CANCELLED", when)
                self.waitlist.reevaluate(allocation.medicine_code, allocation.location_id, at=when)
                return updated
            if booking.booking_state in {BookingStatus.WAITLISTED, BookingStatus.RESERVED}:
                # Waitlist-managed booking: the waitlist releases and re-offers stock.
                self.waitlist.cancel_booking(booking_id, at=when)
                self._audit(principal, "BOOKING_CANCELLED", booking_id, when,
                            previous=booking.booking_state.value,
                            current=BookingStatus.CANCELLED.value)
                return self.bookings[booking_id]
            raise ValueError("booking cannot be cancelled in its current state")

    def expire_reservations(self, principal: Principal, *,
                            at: datetime | None = None) -> tuple[str, ...]:
        """Release expired direct and waitlist reservations (SRS 5.3.2 E)."""
        when = at if at is not None else utc_now()
        _aware(when)
        principal.require(RESERVATION_EXPIRE, at=when)
        with self._lock:
            expired: list[str] = []
            affected: set[tuple[str, str]] = set()
            for booking_id, allocation in sorted(self.allocations.items()):
                booking = self.bookings[booking_id]
                if (not allocation.active or allocation.expires_at is None
                        or allocation.expires_at > when
                        or booking.booking_state is not BookingStatus.RESERVED):
                    continue
                self._release(allocation, "RESERVATION_EXPIRED", when)
                self.bookings[booking_id] = transition_booking(
                    booking, BookingStatus.EXPIRED, at=max(when, booking.updated_at))
                self._audit(principal, "RESERVATION_EXPIRED", booking_id, when,
                            previous=BookingStatus.RESERVED.value,
                            current=BookingStatus.EXPIRED.value)
                self._notify(booking_id, "EXPIRED", when)
                expired.append(booking_id)
                affected.add((allocation.medicine_code, allocation.location_id))
            for medicine_code, location_id in sorted(affected):
                self.waitlist.reevaluate(medicine_code, location_id, at=when)
            expired.extend(self.waitlist.expire_reservations(at=when))
            return tuple(expired)

    def reservation_expiry(self, booking_id: str) -> datetime | None:
        """Expiry of the booking's active reservation, direct or via the waitlist."""
        allocation = self.allocations.get(booking_id)
        if allocation is not None and allocation.active:
            return allocation.expires_at
        for entry in self.waitlist.entries.values():
            if entry.booking_id == booking_id and entry.state is WaitlistState.PROMOTED:
                return entry.reservation_expires_at
        return None

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

    def _waitlist(self, principal: Principal, booking_id: str, medicine_code: str,
                  location_id: str, when: datetime) -> AllocationOutcome:
        self._audit(principal, "ALLOCATION_FAILED", booking_id, when,
                    reason="INSUFFICIENT_ALLOCATABLE_STOCK")
        entry = self.waitlist.add_unfilled(booking_id, medicine_code, location_id, at=when)
        return AllocationOutcome(booking_id, BookingStatus.WAITLISTED, waitlist_entry=entry,
                                 reason_codes=("INSUFFICIENT_ALLOCATABLE_STOCK",))

    def _record_reservation(self, principal: Principal, booking: Booking, inventory,
                            quantity: int, key: str, location_id: str,
                            when: datetime) -> AllocationOutcome:
        duration = self.policy.reservation_duration
        allocation = Allocation(
            booking.booking_id, inventory.inventory_id, inventory.medicine_code,
            location_id, booking.requested_quantity, quantity, key, when,
            expires_at=when + duration if duration is not None else None,
            batch_id=inventory.batch_id, batch_expiry_date=inventory.expiry_date,
        )
        self.allocations[booking.booking_id] = allocation
        updated = transition_booking(booking, BookingStatus.RESERVED, at=when)
        self.bookings[booking.booking_id] = updated
        self._audit(principal, "STOCK_RESERVED", booking.booking_id, when,
                    previous=BookingStatus.ELIGIBLE.value,
                    current=BookingStatus.RESERVED.value,
                    reason=f"inventory={inventory.inventory_id};quantity={quantity}")
        reasons: tuple[str, ...] = ()
        if allocation.remaining_quantity:
            reasons = ("PARTIAL_ALLOCATION",)
            self._audit(principal, "PARTIAL_ALLOCATION", booking.booking_id, when,
                        reason=(f"allocated={quantity};"
                                f"remaining={allocation.remaining_quantity}"))
        # Notification runs after the reservation and cannot undo it (REQ-AW-33).
        self._notify(booking.booking_id, "RESERVED", when)
        return AllocationOutcome(booking.booking_id, BookingStatus.RESERVED,
                                 allocation=allocation, reason_codes=reasons)

    def _release(self, allocation: Allocation, reason: str, when: datetime) -> None:
        if not self.waitlist.inventory.release(allocation.reservation_key, at=when):
            raise ValueError("booking has no active reservation to release")
        self.allocations[allocation.booking_id] = replace(
            allocation, released_at=when, release_reason=reason)
        self.audit_events.append(AuditEvent(
            new_record_id(), "booking-service", ActorRole.SYSTEM, "STOCK_RELEASED",
            "Booking", allocation.booking_id, timestamp=when,
            reason=f"{reason};quantity={allocation.allocated_quantity}",
        ))

    def _notify(self, booking_id: str, event_type: str, when: datetime) -> None:
        if self.waitlist.notify is None:
            return
        try:
            self.waitlist.notify(booking_id, event_type)
        except Exception as exc:
            self.audit_events.append(AuditEvent(
                new_record_id(), "booking-service", ActorRole.SYSTEM,
                "NOTIFICATION_FAILED", "Booking", booking_id, timestamp=when,
                reason=type(exc).__name__,
            ))

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
    "ACTIVE_BOOKING_STATES", "Allocation", "AllocationOutcome",
    "BookingOutcome", "BookingPolicy", "BookingService", "DuplicateAction",
    "DuplicateMatch",
]
