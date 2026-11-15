"""MBA-20: pharmacy manager audit and fulfilment overview.

Managers see operational activity without patient names or health data.
Patient and caregiver account IDs are masked; staff and system actors stay
identifiable so overrides remain attributable.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Iterable, Mapping

from .access import AUDIT_READ, Principal
from .booking import ACTIVE_BOOKING_STATES, BookingService
from .domain import (
    ActorRole, AuditEvent, BookingStatus, DeliveryStatus, NotificationEvent,
    WaitlistState, utc_now,
)


ALLOCATION_EVENTS = frozenset({
    "STOCK_RESERVED", "STOCK_RELEASED", "PARTIAL_ALLOCATION", "ALLOCATION_FAILED",
    "ALLOCATION_BLOCKED", "WAITLIST_ENTERED", "WAITLIST_PROMOTED", "WAITLIST_SKIPPED",
    "WAITLIST_CANCELLED",
})
OVERRIDE_EVENTS = frozenset({"PRIORITY_OVERRIDE"})
EXPIRY_EVENTS = frozenset({"RESERVATION_EXPIRED"})

_PATIENT_SIDE = frozenset({ActorRole.PATIENT, ActorRole.CAREGIVER})


@dataclass(frozen=True, slots=True)
class AuditRow:
    """An audit event with patient-side actor IDs masked.

    Staff and system actors stay identifiable so overrides remain attributable.
    """

    timestamp: datetime
    event_type: str
    object_type: str
    object_id: str
    actor: str
    actor_role: ActorRole
    previous_state: Mapping[str, object] | None
    new_state: Mapping[str, object] | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class WaitlistSummary:
    medicine_code: str
    location_id: str
    active_entries: int
    waiting_quantity: int
    oldest_entry_at: datetime


@dataclass(frozen=True, slots=True)
class FulfilmentMetrics:
    total_bookings: int
    by_status: Mapping[str, int]
    reserved_or_ready: int
    fulfilled: int
    cancelled: int
    expired: int
    rejected: int
    partially_allocated: int
    # Fulfilled share of bookings that have closed after reaching stock decisions.
    fulfilment_rate: float | None


@dataclass(frozen=True, slots=True)
class NotificationSummary:
    by_delivery_status: Mapping[str, int]
    failed_attempts_in_audit: int


@dataclass(frozen=True, slots=True)
class ManagerOverview:
    generated_at: datetime
    active_bookings: Mapping[str, int]
    active_waitlists: tuple[WaitlistSummary, ...]
    allocation_history: tuple[AuditRow, ...]
    overrides: tuple[AuditRow, ...]
    expiry_events: tuple[AuditRow, ...]
    fulfilment: FulfilmentMetrics
    notifications: NotificationSummary


def _row(event: AuditEvent) -> AuditRow:
    actor = (event.actor_role.value if event.actor_role in _PATIENT_SIDE
             else event.actor_id)
    return AuditRow(event.timestamp, event.event_type, event.object_type, event.object_id,
                    actor, event.actor_role, event.previous_state, event.new_state,
                    event.reason)


def audit_history(principal: Principal, service: BookingService, *,
                  object_id: str | None = None,
                  event_types: Iterable[str] | None = None,
                  since: datetime | None = None, until: datetime | None = None,
                  at: datetime | None = None) -> tuple[AuditRow, ...]:
    """Filtered audit trail for managers, oldest first."""
    principal.require(AUDIT_READ, at=at)
    wanted = frozenset(event_types) if event_types is not None else None
    rows = [
        _row(event) for event in service.audit_events
        if (object_id is None or event.object_id == object_id)
        and (wanted is None or event.event_type in wanted)
        and (since is None or event.timestamp >= since)
        and (until is None or event.timestamp <= until)
    ]
    rows.sort(key=lambda row: row.timestamp)
    return tuple(rows)


def manager_overview(principal: Principal, service: BookingService, *,
                     notifications: Iterable[NotificationEvent] = (),
                     at: datetime | None = None) -> ManagerOverview:
    """Operational summary for SRS 3.1.3 without patient names or health data."""
    when = at if at is not None else utc_now()
    principal.require(AUDIT_READ, at=when)
    with service._lock:
        bookings = tuple(service.bookings.values())
        entries = tuple(service.waitlist.entries.values())
        events = tuple(service.audit_events)
        allocations = tuple(service.allocations.values())

    states = Counter(booking.booking_state for booking in bookings)
    active = {state.value: states[state] for state in BookingStatus
              if state in ACTIVE_BOOKING_STATES and states[state]}

    by_booking = {booking.booking_id: booking for booking in bookings}
    groups: dict[tuple[str, str], list] = {}
    for entry in entries:
        if entry.state is WaitlistState.ACTIVE:
            groups.setdefault((entry.medicine_code, entry.location_id), []).append(entry)
    waitlists = tuple(
        WaitlistSummary(
            medicine_code=code, location_id=location, active_entries=len(group),
            waiting_quantity=sum(by_booking[e.booking_id].requested_quantity
                                 for e in group if e.booking_id in by_booking),
            oldest_entry_at=min(e.entered_at for e in group),
        )
        for (code, location), group in sorted(groups.items())
    )

    rows = sorted((_row(event) for event in events), key=lambda row: row.timestamp)
    fulfilled = states[BookingStatus.FULFILLED]
    closed = fulfilled + states[BookingStatus.CANCELLED] + states[BookingStatus.EXPIRED]
    metrics = FulfilmentMetrics(
        total_bookings=len(bookings),
        by_status=MappingProxyType({state.value: count for state, count in states.items()}),
        reserved_or_ready=sum(states[s] for s in (BookingStatus.RESERVED,
                                                  BookingStatus.CONFIRMED,
                                                  BookingStatus.READY)),
        fulfilled=fulfilled,
        cancelled=states[BookingStatus.CANCELLED],
        expired=states[BookingStatus.EXPIRED],
        rejected=states[BookingStatus.REJECTED],
        partially_allocated=sum(1 for a in allocations if a.remaining_quantity),
        fulfilment_rate=(fulfilled / closed) if closed else None,
    )
    delivery = Counter(event.delivery_status for event in notifications)
    notes = NotificationSummary(
        by_delivery_status=MappingProxyType({status.value: delivery[status]
                                             for status in DeliveryStatus}),
        failed_attempts_in_audit=sum(1 for e in events if e.event_type == "NOTIFICATION_FAILED"),
    )
    return ManagerOverview(
        generated_at=when,
        active_bookings=MappingProxyType(active),
        active_waitlists=waitlists,
        allocation_history=tuple(r for r in rows if r.event_type in ALLOCATION_EVENTS),
        overrides=tuple(r for r in rows if r.event_type in OVERRIDE_EVENTS),
        expiry_events=tuple(r for r in rows if r.event_type in EXPIRY_EVENTS),
        fulfilment=metrics,
        notifications=notes,
    )


__all__ = [
    "AuditRow", "FulfilmentMetrics", "ManagerOverview", "NotificationSummary",
    "WaitlistSummary", "audit_history", "manager_overview",
]
