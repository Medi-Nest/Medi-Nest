"""Safety gates for automatic medicine allocation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .domain import (
    Booking,
    BookingStatus,
    Inventory,
    InventoryState,
    MedicationRequest,
    MedicationRequestStatus,
    Prescription,
    PrescriptionStatus,
    utc_now,
)


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
    at: datetime | None = None,
) -> AllocationDecision:
    """Fail closed unless prescription, booking, review, and stock are eligible."""
    when = at or utc_now()
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("at must be timezone-aware")

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
    if restricted_medicine and not manual_review_completed:
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
