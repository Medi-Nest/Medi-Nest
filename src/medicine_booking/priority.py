"""Deterministic, explainable priority ordering using configured policy inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from .domain import ActorRole, Booking, BookingStatus, ContinuityStatus, PriorityContext, utc_now


AUTHORIZED_URGENCY_ROLES = frozenset({ActorRole.PHARMACIST, ActorRole.SYSTEM})
ACTIVE_PRIORITY_STATES = frozenset({BookingStatus.ELIGIBLE, BookingStatus.WAITLISTED})


@dataclass(frozen=True, slots=True)
class PriorityPolicy:
    version: str
    # Lower rank means higher priority. The categories/ranks are supplied by pharmacy policy.
    urgency_rank: Mapping[str, int]
    continuity_rank: Mapping[ContinuityStatus, int]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("policy version is required")
        if not self.urgency_rank or not self.continuity_rank:
            raise ValueError("urgency and continuity rankings must be configured")
        if ContinuityStatus.UNKNOWN not in self.continuity_rank:
            raise ValueError("continuity policy must define UNKNOWN explicitly")


@dataclass(frozen=True, slots=True)
class PriorityResult:
    booking_id: str
    rank: int
    policy_version: str
    reason_codes: tuple[str, ...]
    evaluated_at: datetime


def order_bookings(
    bookings: Iterable[Booking],
    contexts: Mapping[str, PriorityContext],
    policy: PriorityPolicy,
    *,
    evaluated_at: datetime | None = None,
) -> tuple[PriorityResult, ...]:
    """Rank active eligible bookings by urgency, continuity, then oldest wait.

    Booking ID is the final deterministic tie-break. No patient demographics,
    commercial value, payment data, or free-text urgency is accepted here.
    """
    when = evaluated_at or utc_now()
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")

    candidates: list[tuple[tuple[int, int, float, str], Booking, PriorityContext, tuple[str, ...]]] = []
    for booking in bookings:
        if booking.booking_state not in ACTIVE_PRIORITY_STATES:
            continue
        context = contexts.get(booking.booking_id)
        if context is None:
            raise ValueError(f"missing priority context for {booking.booking_id}")
        if context.booking_id != booking.booking_id:
            raise ValueError(f"priority context key does not match booking {booking.booking_id}")
        if context.actor_role not in AUTHORIZED_URGENCY_ROLES:
            raise ValueError(f"urgency for {booking.booking_id} is not from an authorized source")
        if context.policy_version != policy.version:
            raise ValueError(f"priority policy version mismatch for {booking.booking_id}")
        if context.verified_urgency not in policy.urgency_rank:
            raise ValueError(f"urgency category is not configured: {context.verified_urgency}")
        if context.continuity_status not in policy.continuity_rank:
            raise ValueError(f"continuity status is not configured: {context.continuity_status}")

        waited_seconds = max(0.0, (when - context.waiting_since).total_seconds())
        # More waiting time sorts earlier after the two policy ranks.
        key = (
            policy.urgency_rank[context.verified_urgency],
            policy.continuity_rank[context.continuity_status],
            -waited_seconds,
            booking.booking_id,
        )
        reasons = (
            f"VERIFIED_URGENCY:{context.verified_urgency}",
            f"CONTINUITY:{context.continuity_status.value}",
            "WAITING_TIME:OLDEST_FIRST",
            "TIE_BREAK:BOOKING_ID",
        )
        candidates.append((key, booking, context, reasons))

    candidates.sort(key=lambda item: item[0])
    return tuple(
        PriorityResult(
            booking_id=booking.booking_id,
            rank=index,
            policy_version=policy.version,
            reason_codes=reasons,
            evaluated_at=when,
        )
        for index, (_, booking, _, reasons) in enumerate(candidates, start=1)
    )
