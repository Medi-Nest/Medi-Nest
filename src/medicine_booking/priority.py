"""Deterministic priority ordering using verified, policy-approved inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Iterable, Mapping

from .domain import ActorRole, Booking, BookingStatus, ContinuityStatus, PriorityContext, utc_now


ACTIVE_PRIORITY_STATES = frozenset({BookingStatus.ELIGIBLE, BookingStatus.WAITLISTED})
ALLOWED_URGENCY_ROLES = frozenset({ActorRole.PHARMACIST, ActorRole.PHARMACY_MANAGER})


@dataclass(frozen=True, slots=True)
class PriorityPolicy:
    version: str
    # Lower rank means higher priority; equal ranks are resolved by later factors.
    urgency_rank: Mapping[str, int]
    continuity_rank: Mapping[ContinuityStatus, int]
    authorized_urgency_roles: frozenset[ActorRole] = frozenset({ActorRole.PHARMACIST})
    trusted_urgency_sources: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("policy version is required")
        if not self.urgency_rank or not self.continuity_rank:
            raise ValueError("urgency and continuity rankings must be configured")
        if ContinuityStatus.UNKNOWN not in self.continuity_rank:
            raise ValueError("continuity policy must define UNKNOWN explicitly")
        if any(not isinstance(rank, int) or isinstance(rank, bool) or rank < 0
               for rank in (*self.urgency_rank.values(), *self.continuity_rank.values())):
            raise ValueError("priority ranks must be non-negative integers")
        roles = frozenset(self.authorized_urgency_roles)
        if not roles.issubset(ALLOWED_URGENCY_ROLES):
            raise ValueError("only pharmacy roles may verify urgency")
        sources = frozenset(self.trusted_urgency_sources)
        if any(not source.strip() for source in sources):
            raise ValueError("trusted urgency sources cannot be blank")
        # A frozen policy must not change when a caller mutates its original dicts.
        object.__setattr__(self, "urgency_rank", MappingProxyType(dict(self.urgency_rank)))
        object.__setattr__(self, "continuity_rank", MappingProxyType(dict(self.continuity_rank)))
        object.__setattr__(self, "authorized_urgency_roles", roles)
        object.__setattr__(self, "trusted_urgency_sources", sources)


@dataclass(frozen=True, slots=True)
class PriorityResult:
    booking_id: str
    rank: int
    policy_version: str
    reason_codes: tuple[str, ...]
    evaluated_at: datetime
    urgency_source: str = ""
    urgency_actor_id: str | None = None
    urgency_verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PriorityChange:
    """The two stored results for an auditable priority re-evaluation."""

    previous: PriorityResult
    current: PriorityResult


@dataclass(frozen=True, slots=True)
class PriorityEvaluation:
    results: tuple[PriorityResult, ...]
    changes: tuple[PriorityChange, ...]


def order_bookings(
    bookings: Iterable[Booking],
    contexts: Mapping[str, PriorityContext],
    policy: PriorityPolicy,
    *,
    evaluated_at: datetime | None = None,
) -> tuple[PriorityResult, ...]:
    """Rank active bookings by urgency, continuity, oldest wait, then booking ID.

    The caller must authenticate the actor or trusted integration supplying a
    context. Only fields explicitly present in PriorityContext enter this policy.
    """
    when = evaluated_at if evaluated_at is not None else utc_now()
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")

    candidates: list[tuple[tuple[int, int, datetime, str], Booking, PriorityContext, tuple[str, ...]]] = []
    seen: set[str] = set()
    for booking in bookings:
        if booking.booking_id in seen:
            raise ValueError(f"duplicate booking identifier: {booking.booking_id}")
        seen.add(booking.booking_id)
        if booking.booking_state not in ACTIVE_PRIORITY_STATES:
            continue
        context = contexts.get(booking.booking_id)
        if context is None:
            raise ValueError(f"missing priority context for {booking.booking_id}")
        if context.booking_id != booking.booking_id:
            raise ValueError(f"priority context key does not match booking {booking.booking_id}")
        if not (context.actor_role in policy.authorized_urgency_roles or
                context.actor_role is ActorRole.SYSTEM and
                context.urgency_source in policy.trusted_urgency_sources):
            raise ValueError(f"urgency for {booking.booking_id} is not from an authorized source")
        if context.verified_urgency not in policy.urgency_rank:
            raise ValueError(f"urgency category is not configured: {context.verified_urgency}")
        if context.continuity_status not in policy.continuity_rank:
            raise ValueError(f"continuity status is not configured: {context.continuity_status}")
        if context.waiting_since > when:
            raise ValueError(f"waiting time is in the future for {booking.booking_id}")
        if context.urgency_verified_at is not None and context.urgency_verified_at > when:
            raise ValueError(f"urgency verification is in the future for {booking.booking_id}")

        key = (
            policy.urgency_rank[context.verified_urgency],
            policy.continuity_rank[context.continuity_status],
            context.waiting_since,
            booking.booking_id,
        )
        reasons = (
            f"VERIFIED_URGENCY:{context.verified_urgency}",
            f"URGENCY_RANK:{key[0]}",
            f"CONTINUITY:{context.continuity_status.value}",
            f"CONTINUITY_RANK:{key[1]}",
            f"WAITING_SINCE:{context.waiting_since.isoformat()}",
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
            urgency_source=context.urgency_source,
            urgency_actor_id=context.urgency_actor_id,
            urgency_verified_at=context.urgency_verified_at,
        )
        for index, (_, booking, context, reasons) in enumerate(candidates, start=1)
    )


def re_evaluate_bookings(
    bookings: Iterable[Booking],
    contexts: Mapping[str, PriorityContext],
    policy: PriorityPolicy,
    previous_results: Mapping[str, PriorityResult],
    *,
    evaluated_at: datetime | None = None,
) -> PriorityEvaluation:
    """Recalculate current ranks and retain old/new results for changed bookings.

    Call this when a configured context, active booking set, or policy changes.
    Evaluation time alone does not create a priority-change record.
    """
    results = order_bookings(bookings, contexts, policy, evaluated_at=evaluated_at)
    changes = []
    for current in results:
        previous = previous_results.get(current.booking_id)
        if previous is not None and previous.booking_id != current.booking_id:
            raise ValueError(f"previous priority result does not match {current.booking_id}")
        if previous is not None and (
            previous.rank != current.rank or
            previous.policy_version != current.policy_version or
            previous.reason_codes != current.reason_codes or
            previous.urgency_source != current.urgency_source or
            previous.urgency_actor_id != current.urgency_actor_id or
            previous.urgency_verified_at != current.urgency_verified_at
        ):
            changes.append(PriorityChange(previous, current))
    return PriorityEvaluation(results, tuple(changes))
