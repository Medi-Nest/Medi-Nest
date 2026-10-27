"""Shared fixtures for the booking, access, and view tests."""

from datetime import datetime, timedelta, timezone

from medicine_booking import (
    ActorRole, BookingPolicy, BookingService, ContinuityStatus,
    InMemoryInventoryGateway, Inventory, InventoryState, MedicationRequest,
    MedicationRequestStatus, Prescription, PrescriptionSource, PrescriptionStatus,
    Principal, PriorityPolicy, WaitlistService,
)


NOW = datetime(2026, 11, 15, 9, tzinfo=timezone.utc)
LOCATION = "branch-1"


def patient(patient_id="patient-a"):
    return Principal(f"user-{patient_id}", ActorRole.PATIENT, True,
                     patient_ids=frozenset({patient_id}))


PHARMACIST = Principal("pharm-1", ActorRole.PHARMACIST, True)
MANAGER = Principal("mgr-1", ActorRole.PHARMACY_MANAGER, True)
SYSTEM = Principal("scheduler", ActorRole.SYSTEM, True)


def build(*, stock=10, policy=None, notify=None, prescriptions=None, requests=None):
    """A service with three patients (a, b, c), each with one valid prescription.

    Each prescription has one MED-1 request for 2 units, so patients can
    book and compete for the same stock.
    """
    rx = prescriptions if prescriptions is not None else {}
    reqs = requests if requests is not None else {}
    if prescriptions is None:
        for name in ("a", "b", "c"):
            rx[f"rx-{name}"] = Prescription(
                f"rx-{name}", f"patient-{name}", PrescriptionSource.STRUCTURED_IMPORT,
                PrescriptionStatus.VALID, created_at=NOW - timedelta(days=1))
            reqs[f"req-{name}"] = MedicationRequest(
                f"req-{name}", f"rx-{name}", 2, medicine_code="MED-1",
                medicine_name="Insulin glargine",
                request_status=MedicationRequestStatus.ELIGIBLE,
                created_at=NOW - timedelta(days=1))
    gateway = InMemoryInventoryGateway({
        ("MED-1", LOCATION): Inventory("stock-1", "MED-1", LOCATION, stock, 0,
                                       InventoryState.IN_STOCK, batch_id="B-7",
                                       updated_at=NOW - timedelta(hours=1)),
    })
    priority = PriorityPolicy(
        "v1", {"URGENT": 0, "ROUTINE": 1},
        {ContinuityStatus.CONTINUITY: 0, ContinuityStatus.NO_CONTINUITY: 1,
         ContinuityStatus.UNKNOWN: 1},
        trusted_urgency_sources=frozenset({"booking-service"}),
    )
    waitlist = WaitlistService(
        bookings={}, prescriptions=rx, medication_requests=reqs, contexts={},
        inventory=gateway, policy=priority, notify=notify,
        reservation_duration=timedelta(minutes=30),
    )
    rules = policy if policy is not None else BookingPolicy(
        reservation_duration=timedelta(minutes=30), default_urgency="ROUTINE")
    return BookingService(waitlist, policy=rules), gateway


def book(service, name, *, at=NOW, booking_id=None):
    return service.submit(patient(f"patient-{name}"), prescription_id=f"rx-{name}",
                          medication_request_id=f"req-{name}", at=at,
                          booking_id=booking_id or f"bk-{name}")
