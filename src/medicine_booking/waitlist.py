"""Ordered waitlist processing with atomic in-memory stock reservations."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from threading import RLock
from typing import Callable, Mapping, Protocol

from .domain import (
    ActorRole, AuditEvent, Booking, BookingStatus, Inventory, MedicationRequest,
    Prescription, PriorityContext, WaitlistEntry, WaitlistState,
    new_record_id, transition_booking, utc_now,
)
from .priority import PriorityPolicy, order_bookings
from .safety import check_allocation_safety


@dataclass(frozen=True, slots=True)
class RestockEvent:
    event_id: str
    medicine_code: str
    location_id: str
    quantity: int
    occurred_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not all((self.event_id.strip(), self.medicine_code.strip(), self.location_id.strip())):
            raise ValueError("restock event identifiers are required")
        if self.quantity <= 0:
            raise ValueError("restock quantity must be positive")
        _aware(self.occurred_at)


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


class InventoryGateway(Protocol):
    """Implement reserve atomically and idempotently by reservation key."""

    def apply_restock(self, event: RestockEvent) -> bool: ...
    def read(self, medicine_code: str, location_id: str) -> Inventory: ...
    def reservation_for(self, reservation_key: str) -> tuple[str, str, int] | None: ...
    def reserve(self, inventory_id: str, booking_id: str, quantity: int,
                reservation_key: str, *, at: datetime) -> bool: ...
    def release(self, reservation_key: str, *, at: datetime) -> bool: ...


class InMemoryInventoryGateway:
    """Prototype inventory adapter; one lock guards restocks and reservations."""

    def __init__(self, inventory: Mapping[tuple[str, str], Inventory]) -> None:
        self._inventory = dict(inventory)
        self._restock_events: dict[str, RestockEvent] = {}
        self.reservations: dict[str, tuple[str, str, int]] = {}
        self._released_keys: set[str] = set()
        self._lock = RLock()

    def apply_restock(self, event: RestockEvent) -> bool:
        with self._lock:
            if event.event_id in self._restock_events:
                if self._restock_events[event.event_id] != event:
                    raise ValueError("restock event ID reused with different data")
                return False
            key = (event.medicine_code, event.location_id)
            current = self._inventory[key]
            self._inventory[key] = replace(
                current, on_hand_quantity=current.on_hand_quantity + event.quantity,
                updated_at=max(current.updated_at, event.occurred_at),
            )
            self._restock_events[event.event_id] = event
            return True

    def read(self, medicine_code: str, location_id: str) -> Inventory:
        with self._lock:
            return self._inventory[(medicine_code, location_id)]

    def reservation_for(self, reservation_key: str) -> tuple[str, str, int] | None:
        with self._lock:
            return self.reservations.get(reservation_key)

    def reserve(self, inventory_id: str, booking_id: str, quantity: int,
                reservation_key: str, *, at: datetime) -> bool:
        _aware(at)
        with self._lock:
            expected = (inventory_id, booking_id, quantity)
            if reservation_key in self._released_keys:
                return False
            if reservation_key in self.reservations:
                return self.reservations[reservation_key] == expected
            if quantity <= 0:
                return False
            for key, current in self._inventory.items():
                if current.inventory_id == inventory_id:
                    if (current.allocatable_quantity < quantity or
                            current.expiry_date is not None and current.expiry_date < at.date()):
                        return False
                    self._inventory[key] = replace(
                        current, reserved_quantity=current.reserved_quantity + quantity,
                        updated_at=max(current.updated_at, at),
                    )
                    self.reservations[reservation_key] = expected
                    return True
            return False

    def release(self, reservation_key: str, *, at: datetime) -> bool:
        _aware(at)
        with self._lock:
            reservation = self.reservations.get(reservation_key)
            if reservation is None:
                return False
            inventory_id, _, quantity = reservation
            for key, current in self._inventory.items():
                if current.inventory_id == inventory_id:
                    if current.reserved_quantity < quantity:
                        raise ValueError("inventory reservation count is inconsistent")
                    self._inventory[key] = replace(
                        current, reserved_quantity=current.reserved_quantity - quantity,
                        updated_at=max(current.updated_at, at),
                    )
                    del self.reservations[reservation_key]
                    self._released_keys.add(reservation_key)
                    return True
            raise ValueError("reserved inventory record is missing")


@dataclass(frozen=True, slots=True)
class RestockResult:
    promoted_booking_ids: tuple[str, ...]
    skipped_booking_ids: tuple[str, ...]
    duplicate_event: bool = False


class WaitlistService:
    """Prototype workflow state. Persist this state and adapter keys in production."""

    def __init__(
        self, *, bookings: Mapping[str, Booking],
        prescriptions: Mapping[str, Prescription],
        medication_requests: Mapping[str, MedicationRequest],
        contexts: Mapping[str, PriorityContext],
        inventory: InventoryGateway,
        policy: PriorityPolicy,
        notify: Callable[[str, str], None] | None = None,
        reservation_duration: timedelta | None = None,
    ) -> None:
        if reservation_duration is not None and reservation_duration <= timedelta():
            raise ValueError("reservation duration must be positive")
        self.bookings = dict(bookings)
        self.prescriptions = dict(prescriptions)
        self.medication_requests = dict(medication_requests)
        self.contexts = dict(contexts)
        self.entries: dict[str, WaitlistEntry] = {}
        self.audit_events: list[AuditEvent] = []
        self.inventory = inventory
        self.policy = policy
        self.notify = notify
        self.reservation_duration = reservation_duration
        self._completed_restock_events: dict[str, RestockEvent] = {}
        self._lock = RLock()

    def _audit(self, event_type: str, entry: WaitlistEntry, at: datetime, *,
               previous: str | None = None, current: str | None = None,
               reason: str | None = None) -> None:
        self.audit_events.append(AuditEvent(
            new_record_id(), "waitlist-service", ActorRole.SYSTEM, event_type,
            "WaitlistEntry", entry.waitlist_id, timestamp=at,
            previous_state={"state": previous} if previous is not None else None,
            new_state={"state": current} if current is not None else None,
            reason=reason,
        ))

    def _notify(self, booking_id: str, event_type: str, entry: WaitlistEntry,
                at: datetime) -> None:
        if self.notify is None:
            return
        try:
            self.notify(booking_id, event_type)
        except Exception as exc:
            self._audit("NOTIFICATION_FAILED", entry, at, reason=type(exc).__name__)

    def add_unfilled(self, booking_id: str, medicine_code: str, location_id: str,
                     *, at: datetime | None = None) -> WaitlistEntry:
        """Waitlist an eligible booking only when its full quantity lacks stock."""
        when = at if at is not None else utc_now()
        _aware(when)
        with self._lock:
            booking = self.bookings[booking_id]
            if booking.booking_state is not BookingStatus.ELIGIBLE:
                raise ValueError("only an eligible booking can enter the waitlist")
            if any(entry.booking_id == booking_id and entry.state is WaitlistState.ACTIVE
                   for entry in self.entries.values()):
                raise ValueError("booking already has an active waitlist entry")
            request = self.medication_requests[booking.medication_request_id]
            inventory = self.inventory.read(medicine_code, location_id)
            if request.medicine_code != medicine_code or inventory.medicine_code != medicine_code:
                raise ValueError("waitlist medicine does not match the request")
            decision = check_allocation_safety(
                booking, self.prescriptions[booking.prescription_id], request,
                inventory, at=when,
            )
            if set(decision.reason_codes) != {"INSUFFICIENT_ALLOCATABLE_STOCK"}:
                raise ValueError(f"booking cannot be waitlisted: {decision.reason_codes}")
            updated = transition_booking(booking, BookingStatus.WAITLISTED, at=when)
            entry = WaitlistEntry(new_record_id(), booking_id, medicine_code,
                                  location_id, entered_at=when)
            self.bookings[booking_id] = updated
            self.entries[entry.waitlist_id] = entry
            self._audit("WAITLIST_ENTERED", entry, when, current=entry.state.value)
            self._notify(booking_id, "WAITLISTED", entry, when)
            return entry

    def process_restock(self, event: RestockEvent, *,
                        at: datetime | None = None) -> RestockResult:
        """Apply an idempotent restock and offer stock in current priority order."""
        when = at if at is not None else utc_now()
        _aware(when)
        if when < event.occurred_at:
            raise ValueError("restock cannot be processed before it occurred")
        with self._lock:
            if event.event_id in self._completed_restock_events:
                if self._completed_restock_events[event.event_id] != event:
                    raise ValueError("restock event ID reused with different data")
                return RestockResult((), (), duplicate_event=True)
            self.inventory.apply_restock(event)
            result = self._promote_available(event.medicine_code, event.location_id, when)
            self._completed_restock_events[event.event_id] = event
            return result

    def cancel_booking(self, booking_id: str, *,
                       at: datetime | None = None) -> RestockResult:
        """Release a promoted reservation and reconsider the remaining waitlist."""
        when = at if at is not None else utc_now()
        _aware(when)
        with self._lock:
            booking = self.bookings[booking_id]
            if booking.booking_state is BookingStatus.CANCELLED:
                return RestockResult((), (), duplicate_event=True)
            if when < booking.updated_at:
                raise ValueError("cancellation cannot precede booking update")
            entry = next((item for item in self.entries.values()
                          if item.booking_id == booking_id and item.state in
                          {WaitlistState.ACTIVE, WaitlistState.PROMOTED}), None)
            if entry is None or booking.booking_state not in {
                BookingStatus.WAITLISTED, BookingStatus.RESERVED,
            }:
                raise ValueError("booking has no cancellable waitlist entry")
            if ((entry.state is WaitlistState.ACTIVE) !=
                    (booking.booking_state is BookingStatus.WAITLISTED)):
                raise ValueError("booking and waitlist states disagree")
            released = False
            if entry.state is WaitlistState.PROMOTED:
                if not self.inventory.release(f"waitlist:{entry.waitlist_id}", at=when):
                    raise ValueError("promoted booking has no active reservation")
                released = True
                self._audit("STOCK_RELEASED", entry, when,
                            reason=f"booking={booking_id};quantity={booking.requested_quantity}")
            self.bookings[booking_id] = transition_booking(
                booking, BookingStatus.CANCELLED, at=max(when, booking.updated_at),
            )
            self.entries[entry.waitlist_id] = replace(entry, state=WaitlistState.CANCELLED)
            self._audit("WAITLIST_CANCELLED", entry, when,
                        previous=entry.state.value, current=WaitlistState.CANCELLED.value)
            self._notify(booking_id, "CANCELLED", entry, when)
            if released:
                return self._promote_available(entry.medicine_code, entry.location_id, when)
            return RestockResult((), ())

    def _promote_available(self, medicine_code: str, location_id: str,
                           when: datetime) -> RestockResult:
        """Re-evaluate active entries after inventory becomes available (lock held)."""
        candidates = [
            entry for entry in self.entries.values()
            if entry.state is WaitlistState.ACTIVE
            and (entry.medicine_code, entry.location_id) ==
                (medicine_code, location_id)
        ]
        skipped: list[str] = []
        rankable: list[Booking] = []
        for entry in candidates:
            booking = self.bookings.get(entry.booking_id)
            if booking is None or booking.booking_state is not BookingStatus.WAITLISTED:
                skipped.append(entry.booking_id)
                self._audit("WAITLIST_SKIPPED", entry, when,
                            reason="BOOKING_NOT_WAITLISTED")
                continue
            try:
                order_bookings((booking,), self.contexts, self.policy,
                               evaluated_at=when)
            except ValueError as exc:
                skipped.append(entry.booking_id)
                self._audit("WAITLIST_SKIPPED", entry, when,
                            reason=str(exc))
                continue
            rankable.append(booking)
        ranked = order_bookings(rankable, self.contexts, self.policy,
                                evaluated_at=when)
        by_booking = {entry.booking_id: entry for entry in candidates}
        promoted: list[str] = []
        for result in ranked:
            entry = by_booking[result.booking_id]
            booking = self.bookings[result.booking_id]
            reservation_key = f"waitlist:{entry.waitlist_id}"
            request = self.medication_requests.get(booking.medication_request_id)
            prescription = self.prescriptions.get(booking.prescription_id)
            if request is None or prescription is None:
                reasons = ("MISSING_PRESCRIPTION_OR_REQUEST",)
            elif (request.medicine_code != medicine_code or
                  entry.medicine_code != medicine_code):
                reasons = ("MEDICINE_CODE_MISMATCH",)
            else:
                current = self.inventory.read(medicine_code, location_id)
                reasons = check_allocation_safety(
                    booking, prescription, request, current, at=when,
                ).reason_codes
                if reasons == ("INSUFFICIENT_ALLOCATABLE_STOCK",) and (
                    self.inventory.reservation_for(reservation_key) ==
                    (current.inventory_id, booking.booking_id, booking.requested_quantity)
                ):
                    # An earlier attempt may have succeeded before its reply
                    # was lost. Reconcile its idempotency key on replay.
                    reasons = ()
            if reasons:
                skipped.append(booking.booking_id)
                self._audit("WAITLIST_SKIPPED", entry, when,
                            reason=",".join(reasons))
                continue

            reserved = self._reserve_with_recheck(
                entry, reservation_key, when,
            )
            if not reserved:
                skipped.append(booking.booking_id)
                # A transient failure with stock still available must not let
                # a lower-ranked request take the same stock.
                current = self.inventory.read(medicine_code, location_id)
                if current.allocatable_quantity >= booking.requested_quantity:
                    break
                continue
            updated = transition_booking(
                booking, BookingStatus.RESERVED,
                at=max(when, booking.updated_at),
            )
            self.bookings[booking.booking_id] = updated
            self.entries[entry.waitlist_id] = replace(
                entry, state=WaitlistState.PROMOTED, offered_at=when,
                reservation_expires_at=(
                    when + self.reservation_duration
                    if self.reservation_duration is not None else None
                ),
            )
            promoted.append(booking.booking_id)
            self._audit("STOCK_RESERVED", entry, when,
                        reason=f"inventory={current.inventory_id};quantity={booking.requested_quantity}")
            self._audit("WAITLIST_PROMOTED", entry, when,
                        previous=WaitlistState.ACTIVE.value,
                        current=WaitlistState.PROMOTED.value)
            self._notify(booking.booking_id, "RESERVED", entry, when)
        return RestockResult(tuple(promoted), tuple(skipped))

    def _reserve_with_recheck(self, entry: WaitlistEntry,
                              key: str, at: datetime) -> bool:
        for _ in range(2):
            booking = self.bookings.get(entry.booking_id)
            if booking is None:
                return False
            request = self.medication_requests.get(booking.medication_request_id)
            prescription = self.prescriptions.get(booking.prescription_id)
            if request is None or prescription is None or request.medicine_code != entry.medicine_code:
                return False
            current = self.inventory.read(entry.medicine_code, entry.location_id)
            existing = self.inventory.reservation_for(key)
            expected = (current.inventory_id, booking.booking_id, booking.requested_quantity)
            if existing is not None:
                if existing != expected:
                    raise ValueError("reservation key belongs to another allocation")
                return True
            if not check_allocation_safety(
                booking, prescription, request, current, at=at,
            ).allowed:
                return False
            try:
                if self.inventory.reserve(current.inventory_id, booking.booking_id,
                                          booking.requested_quantity, key, at=at):
                    return True
            except Exception as exc:
                failure = type(exc).__name__
            else:
                failure = "RESERVATION_CONFLICT"
            self._audit("ALLOCATION_FAILED", entry, at, reason=failure)
            # The next attempt rereads inventory after an atomic reservation failure.
        return False
