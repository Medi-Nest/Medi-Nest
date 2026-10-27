"""MBA-14: privacy-safe booking, status, and waitlist views for patients.

These functions return plain data for a host UI or API to render. Each one
checks the caller's role and record ownership before reading anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from .access import BOOKING_READ_ANY, BOOKING_READ_OWN, Principal, authorize_patient_record
from .booking import BookingService
from .domain import Booking, BookingStatus, utc_now
from .safety import EmergencyGuidance, patient_status_message


# Plain-language labels from SRS 3.1.1.
STATUS_LABELS: Mapping[BookingStatus, str] = MappingProxyType({
    BookingStatus.SUBMITTED: "Booking Submitted",
    BookingStatus.ELIGIBILITY_PENDING: "Prescription Under Review",
    BookingStatus.ELIGIBLE: "Prescription Accepted",
    BookingStatus.RESERVED: "Medicine Reserved",
    BookingStatus.WAITLISTED: "Waitlisted",
    BookingStatus.CONFIRMED: "Medicine Reserved",
    BookingStatus.READY: "Ready for Pickup",
    BookingStatus.FULFILLED: "Collected",
    BookingStatus.REJECTED: "Booking Rejected",
    BookingStatus.CANCELLED: "Cancelled",
    BookingStatus.EXPIRED: "Reservation Expired",
})

_STATUS_MESSAGES: Mapping[BookingStatus, str] = MappingProxyType({
    BookingStatus.SUBMITTED: "We have received your booking.",
    BookingStatus.ELIGIBILITY_PENDING: "A pharmacist is checking your prescription. We will update you when it is done.",
    BookingStatus.ELIGIBLE: "Your prescription is accepted. We are checking stock for your medicine.",
    BookingStatus.RESERVED: "Your medicine is reserved for you. Please collect it before the reservation ends.",
    BookingStatus.WAITLISTED: "Your medicine is not in stock right now. You are on the waitlist and we will tell you when it is reserved.",
    BookingStatus.CONFIRMED: "Your reservation is confirmed.",
    BookingStatus.READY: "Your medicine is ready to collect.",
    BookingStatus.FULFILLED: "You have collected this medicine.",
    BookingStatus.REJECTED: "This booking could not be accepted.",
    BookingStatus.CANCELLED: "This booking was cancelled.",
    BookingStatus.EXPIRED: "Your reservation ended before the medicine was collected. You can make a new booking.",
})

# Validation and error messages from SRS 3.1.4. Unknown codes fall back to a
# generic message so internal reason codes are never shown to patients.
REASON_MESSAGES: Mapping[str, str] = MappingProxyType({
    "INVALID_QUANTITY": "The requested quantity is not valid.",
    "QUANTITY_DOES_NOT_MATCH_PRESCRIPTION": "The quantity must match your prescription.",
    "MEDICINE_NOT_ON_PRESCRIPTION": "This medicine is not on the selected prescription.",
    "DUPLICATE_ACTIVE_BOOKING": "You already have an active booking for this medicine.",
    "PRESCRIPTION_INVALID": "Your prescription could not be accepted. Please contact the pharmacy.",
    "PRESCRIPTION_EXPIRED": "Your prescription has expired. Please get a new prescription.",
    "PRESCRIPTION_AWAITING_REVIEW": "A pharmacist is reviewing your prescription.",
    "MEDICATION_REQUEST_PENDING": "A pharmacist is reviewing this medicine request.",
    "MEDICATION_REQUEST_NOT_ELIGIBLE": "This medicine request cannot be booked.",
    "INSUFFICIENT_ALLOCATABLE_STOCK": "This medicine is not in stock right now.",
    "PARTIAL_ALLOCATION": "Only part of your requested quantity is in stock and has been reserved.",
})
_GENERIC_REASON = "Your booking could not be completed. Please contact the pharmacy."

_CANCELLABLE = frozenset({BookingStatus.WAITLISTED, BookingStatus.RESERVED, BookingStatus.READY})


def reason_message(code: str) -> str:
    return REASON_MESSAGES.get(code, _GENERIC_REASON)


@dataclass(frozen=True, slots=True)
class PatientBookingView:
    """What a patient may see about one of their own bookings.

    It deliberately has no rank, urgency, continuity, waitlist position, or
    any field about another patient (REQ-PB-28, REQ-AW-35).
    """

    booking_id: str
    medicine: str
    status: str
    message: str
    requested_quantity: int
    reserved_quantity: int
    outstanding_quantity: int
    reservation_expires_at: datetime | None
    can_cancel: bool
    notices: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


def patient_booking_view(principal: Principal, service: BookingService, booking_id: str, *,
                         at: datetime | None = None, emergency_indicator: bool = False,
                         guidance: EmergencyGuidance | None = None) -> PatientBookingView:
    """Return one booking for its owner or for pharmacy staff."""
    when = at if at is not None else utc_now()
    booking = service.bookings.get(booking_id)
    if booking is None:
        # A missing record looks the same as somebody else's record.
        principal.require(BOOKING_READ_ANY, at=when)
        raise KeyError(booking_id)
    authorize_patient_record(principal, booking.patient_id, own_permission=BOOKING_READ_OWN,
                             any_permission=BOOKING_READ_ANY, at=when)
    return _build_view(service, booking, emergency_indicator, guidance)


def patient_bookings(principal: Principal, service: BookingService,
                     patient_id: str | None = None, *,
                     at: datetime | None = None) -> tuple[PatientBookingView, ...]:
    """List a patient's bookings, newest first."""
    when = at if at is not None else utc_now()
    if patient_id is None:
        if len(principal.patient_ids) != 1:
            raise ValueError("patient_id is required when the caller has several linked patients")
        (patient_id,) = principal.patient_ids
    authorize_patient_record(principal, patient_id, own_permission=BOOKING_READ_OWN,
                             any_permission=BOOKING_READ_ANY, at=when)
    own = [b for b in service.bookings.values() if b.patient_id == patient_id]
    own.sort(key=lambda b: (b.created_at, b.booking_id), reverse=True)
    return tuple(_build_view(service, booking, False, None) for booking in own)


def _build_view(service: BookingService, booking: Booking, emergency_indicator: bool,
                guidance: EmergencyGuidance | None) -> PatientBookingView:
    request = service.medication_requests.get(booking.medication_request_id)
    medicine = "Prescribed medicine"
    if request is not None:
        medicine = request.medicine_name or request.medicine_code or medicine

    allocation = service.allocations.get(booking.booking_id)
    reserved = 0
    if booking.booking_state in {BookingStatus.RESERVED, BookingStatus.CONFIRMED,
                                 BookingStatus.READY, BookingStatus.FULFILLED}:
        reserved = (allocation.allocated_quantity if allocation is not None
                    else booking.requested_quantity)
    if booking.booking_state in {BookingStatus.CANCELLED, BookingStatus.REJECTED,
                                 BookingStatus.EXPIRED}:
        outstanding = 0
    elif reserved:
        outstanding = allocation.remaining_quantity if allocation is not None else 0
    else:
        outstanding = booking.requested_quantity

    codes = (service.rejection_reasons.get(booking.booking_id, ())
             + service.hold_reasons.get(booking.booking_id, ()))
    if allocation is not None and allocation.active and allocation.remaining_quantity:
        codes += ("PARTIAL_ALLOCATION",)
    if booking.booking_state is BookingStatus.WAITLISTED:
        codes += ("INSUFFICIENT_ALLOCATABLE_STOCK",)
    notices = tuple(dict.fromkeys(reason_message(code) for code in codes))

    if emergency_indicator:
        # Approved emergency copy only; never inferred from patient input.
        message = patient_status_message(booking.booking_state, emergency_indicator=True,
                                         guidance=guidance)
    else:
        message = _STATUS_MESSAGES[booking.booking_state]
    return PatientBookingView(
        booking_id=booking.booking_id,
        medicine=medicine,
        status=STATUS_LABELS[booking.booking_state],
        message=message,
        requested_quantity=booking.requested_quantity,
        reserved_quantity=reserved,
        outstanding_quantity=outstanding,
        reservation_expires_at=(service.reservation_expiry(booking.booking_id)
                                if booking.booking_state is BookingStatus.RESERVED else None),
        can_cancel=booking.booking_state in _CANCELLABLE,
        notices=notices,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


__all__ = [
    "PatientBookingView", "REASON_MESSAGES", "STATUS_LABELS",
    "patient_booking_view", "patient_bookings", "reason_message",
]
