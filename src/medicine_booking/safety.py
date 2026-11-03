"""Safety gates for automatic medicine allocation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from .domain import (
    ActorRole,
    AuditEvent,
    Booking,
    BookingStatus,
    Inventory,
    InventoryState,
    MedicationRequest,
    MedicationRequestStatus,
    Prescription,
    PrescriptionStatus,
    new_record_id,
    utc_now,
)
from .priority import PriorityResult


@dataclass(frozen=True, slots=True)
class SafetyPolicy:
    """Configured medicine code/category restrictions; never substitute codes."""

    restricted_medicine_codes: frozenset[str] = frozenset()
    excluded_medicine_codes: frozenset[str] = frozenset()
    restricted_categories: frozenset[str] = frozenset()
    excluded_categories: frozenset[str] = frozenset()
    medicine_categories: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        restricted = frozenset(self.restricted_medicine_codes)
        excluded = frozenset(self.excluded_medicine_codes)
        restricted_categories = frozenset(self.restricted_categories)
        excluded_categories = frozenset(self.excluded_categories)
        categories = dict(self.medicine_categories)
        if (any(not code.strip() for code in restricted | excluded | set(categories))
                or any(not category.strip() for category in
                       restricted_categories | excluded_categories | set(categories.values()))):
            raise ValueError("medicine restriction codes and categories cannot be blank")
        object.__setattr__(self, "restricted_medicine_codes", restricted)
        object.__setattr__(self, "excluded_medicine_codes", excluded)
        object.__setattr__(self, "restricted_categories", restricted_categories)
        object.__setattr__(self, "excluded_categories", excluded_categories)
        object.__setattr__(self, "medicine_categories", MappingProxyType(categories))


@dataclass(frozen=True, slots=True)
class PharmacistPrincipal:
    """Authenticated server-side identity; the caller must verify its claims."""

    actor_id: str
    actor_role: ActorRole
    authenticated: bool
    permissions: frozenset[str]

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise ValueError("actor identifier is required")
        object.__setattr__(self, "permissions", frozenset(self.permissions))

    def require(self, permission: str) -> None:
        if (not self.authenticated or self.actor_role is not ActorRole.PHARMACIST
                or permission not in self.permissions):
            raise PermissionError("authenticated pharmacist authorization required")


@dataclass(frozen=True, slots=True)
class ManualReview:
    booking_id: str
    medicine_code: str
    principal: PharmacistPrincipal
    reviewed_at: datetime

    def __post_init__(self) -> None:
        if not self.booking_id.strip() or not self.medicine_code.strip():
            raise ValueError("review booking and medicine are required")
        self.principal.require("prescription:review")
        _aware(self.reviewed_at)


@dataclass(frozen=True, slots=True)
class PriorityOverride:
    previous: PriorityResult
    current: PriorityResult
    audit_event: AuditEvent


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


def override_priority(
    previous: PriorityResult, new_rank: int,
    principal: PharmacistPrincipal, reason: str, *,
    at: datetime | None = None,
) -> PriorityOverride:
    """Record an authorized priority override; allocation safety still applies."""
    principal.require("allocation:override")
    if not reason or not reason.strip():
        raise ValueError("override reason is required")
    if isinstance(new_rank, bool) or not isinstance(new_rank, int) or new_rank < 0:
        raise ValueError("override rank must be a non-negative integer")
    when = at if at is not None else utc_now()
    _aware(when)
    if when < previous.evaluated_at:
        raise ValueError("override cannot precede priority evaluation")
    current = replace(
        previous, rank=new_rank, evaluated_at=when,
        reason_codes=previous.reason_codes + ("PHARMACIST_OVERRIDE",),
    )
    event = AuditEvent(
        new_record_id(), principal.actor_id, principal.actor_role,
        "PRIORITY_OVERRIDE", "Booking", previous.booking_id, timestamp=when,
        previous_state={"rank": previous.rank, "policy_version": previous.policy_version},
        new_state={"rank": new_rank, "policy_version": previous.policy_version},
        reason=reason.strip(),
    )
    return PriorityOverride(previous, current, event)


@dataclass(frozen=True, slots=True)
class EmergencyGuidance:
    """Text approved outside this module by the product owner."""

    text: str
    approved_by: str
    version: str

    def __post_init__(self) -> None:
        if not all((self.text.strip(), self.approved_by.strip(), self.version.strip())):
            raise ValueError("approved emergency guidance and provenance are required")


def patient_status_message(state: BookingStatus, *, emergency_indicator: bool = False,
                           guidance: EmergencyGuidance | None = None) -> str:
    """Use only configured emergency copy; never infer urgency from patient text."""
    if emergency_indicator:
        if guidance is None:
            raise ValueError("product-owner-approved emergency guidance is required")
        return f"{guidance.text} Medicine booking and waitlist updates are not emergency care."
    return {
        BookingStatus.WAITLISTED: "Your request is on the waitlist. We will update you if stock becomes available.",
        BookingStatus.RESERVED: "Your medicine request has been reserved. Check your booking for the next step.",
    }.get(state, "Check your booking for its current status.")


@dataclass(frozen=True, slots=True)
class AllocationDecision:
    allowed: bool
    reason_codes: tuple[str, ...]
    allocatable_quantity: int


def check_allocation_safety(
    booking: Booking,
    prescription: Prescription,
    medication_request: MedicationRequest,
    inventory: Inventory,
    *,
    quantity_to_allocate: int | None = None,
    restricted_medicine: bool = False,
    manual_review_completed: bool = False,
    policy: SafetyPolicy | None = None,
    review: ManualReview | None = None,
    at: datetime | None = None,
) -> AllocationDecision:
    """Fail closed unless prescription, booking, review, and stock are eligible.

    The legacy manual_review_completed flag is intentionally insufficient;
    restricted medicines require a scoped ManualReview attestation.
    """
    when = at if at is not None else utc_now()
    _aware(when)

    reasons: list[str] = []
    quantity = quantity_to_allocate if quantity_to_allocate is not None else booking.requested_quantity
    if quantity <= 0:
        reasons.append("INVALID_QUANTITY")
    if booking.prescription_id != prescription.prescription_id or booking.patient_id != prescription.patient_id:
        reasons.append("PRESCRIPTION_BOOKING_MISMATCH")
    if booking.medication_request_id != medication_request.medication_request_id:
        reasons.append("MEDICATION_REQUEST_MISMATCH")
    if medication_request.prescription_id != prescription.prescription_id:
        reasons.append("MEDICATION_REQUEST_PRESCRIPTION_MISMATCH")
    if medication_request.request_status is not MedicationRequestStatus.ELIGIBLE:
        reasons.append("MEDICATION_REQUEST_NOT_ELIGIBLE")
    if booking.booking_state not in {BookingStatus.ELIGIBLE, BookingStatus.WAITLISTED}:
        reasons.append("BOOKING_NOT_ELIGIBLE")
    if prescription.status is not PrescriptionStatus.VALID:
        reasons.append("PRESCRIPTION_NOT_VALID")
    if booking.requested_quantity != medication_request.requested_quantity:
        reasons.append("REQUESTED_QUANTITY_MISMATCH")
    rules = policy if policy is not None else SafetyPolicy()
    medicine_code = medication_request.medicine_code
    category = rules.medicine_categories.get(medicine_code)
    if (rules.restricted_categories or rules.excluded_categories) and category is None:
        reasons.append("MEDICINE_CATEGORY_UNKNOWN")
    if (medicine_code in rules.excluded_medicine_codes
            or category in rules.excluded_categories):
        reasons.append("MEDICINE_EXCLUDED_FROM_AUTOMATIC_ALLOCATION")
    restricted = (restricted_medicine or medicine_code in rules.restricted_medicine_codes
                  or category in rules.restricted_categories)
    approved_review = (
        review is not None and review.booking_id == booking.booking_id
        and review.medicine_code == medicine_code
        and review.reviewed_at <= when
        and review.principal.authenticated
    )
    # A caller-supplied boolean is not evidence of an authorized review.
    if restricted and not approved_review:
        reasons.append("RESTRICTED_MEDICINE_REQUIRES_REVIEW")
    if inventory.inventory_state is not InventoryState.IN_STOCK:
        reasons.append("INVENTORY_NOT_ALLOCATABLE")
    if inventory.expiry_date is not None and inventory.expiry_date < when.date():
        reasons.append("INVENTORY_EXPIRED")
    if not medication_request.medicine_code:
        reasons.append("MEDICINE_CODE_REQUIRED_FOR_ALLOCATION")
    elif medication_request.medicine_code != inventory.medicine_code:
        reasons.append("MEDICINE_CODE_MISMATCH")

    if quantity > booking.requested_quantity or quantity > medication_request.requested_quantity:
        reasons.append("ALLOCATION_EXCEEDS_REQUESTED_QUANTITY")

    available = inventory.allocatable_quantity
    if quantity > available:
        reasons.append("INSUFFICIENT_ALLOCATABLE_STOCK")

    return AllocationDecision(
        allowed=not reasons,
        reason_codes=tuple(reasons),
        allocatable_quantity=available,
    )
