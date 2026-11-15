"""Short demo of the medicine booking module, using only test doubles.

    python examples/demo.py

Walks through: prescription import, consent and continuity lookup, booking,
allocation, waitlist, a pharmacist confirming urgency, restock promotion,
reservation expiry, cancellation, notifications, and the manager overview.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from medicine_booking import (  # noqa: E402
    ActorRole, BookingPolicy, BookingService, ConsentStatus, ContinuityStatus,
    InMemoryInventoryGateway, Inventory, InventoryState, NotificationChannel, Patient,
    Principal, PriorityContext, PriorityPolicy, RestockEvent, WaitlistService,
    manager_overview, patient_booking_view,
)
from medicine_booking.contracts import (  # noqa: E402
    MockNotificationSender, MockPatientContextSource, MockPrescriptionSource,
    import_prescription,
)
from medicine_booking.notifications import (  # noqa: E402
    NotificationService, Recipient, notification_events,
)
from medicine_booking.patient_context import PatientContextService, refresh_continuity  # noqa: E402

START = datetime(2026, 11, 15, 9, tzinfo=timezone.utc)
clock = {"now": START}


def step(text):
    print(f"\n[{clock['now']:%H:%M}] {text}")


def advance(minutes):
    clock["now"] += timedelta(minutes=minutes)
    return clock["now"]


def main():
    names = ("asha", "bala", "chitra")
    patients = {n: Patient(f"patient-{n}", n.title(), external_patient_id=f"ABHA-{n}") for n in names}
    users = {n: Principal(f"user-{n}", ActorRole.PATIENT, True, patient_ids=frozenset({f"patient-{n}"}))
             for n in names}
    manager = Principal("mgr-1", ActorRole.PHARMACY_MANAGER, True)
    system = Principal("scheduler", ActorRole.SYSTEM, True)

    rx_source = MockPrescriptionSource({f"MR-{n}": {
        "source_system": "abdm", "source_identifier": f"MR-{n}",
        "patient_reference": f"ABHA-{n}", "status": "active",
        "authored_on": "2026-11-14T10:00:00+05:30", "prescriber_reference": "Practitioner/dr-9",
        "lines": [{"source_line_id": "1", "medicine_code": "INS-100",
                   "medicine_name": "Insulin glargine 100 IU/ml", "quantity": 2}],
    } for n in names})
    inventory = InMemoryInventoryGateway({("INS-100", "indiranagar"): Inventory(
        "stock-1", "INS-100", "indiranagar", 2, 0, InventoryState.IN_STOCK,
        batch_id="B-7", updated_at=START)})
    policy = PriorityPolicy("v1", {"URGENT": 0, "ROUTINE": 1},
                            {ContinuityStatus.CONTINUITY: 0, ContinuityStatus.NO_CONTINUITY: 1,
                             ContinuityStatus.UNKNOWN: 1},
                            trusted_urgency_sources=frozenset({"booking-service"}))
    waitlist = WaitlistService(bookings={}, prescriptions={}, medication_requests={}, contexts={},
                               inventory=inventory, policy=policy,
                               reservation_duration=timedelta(minutes=30))
    bookings = BookingService(waitlist, policy=BookingPolicy(
        reservation_duration=timedelta(minutes=30), default_urgency="ROUTINE"))
    sender = MockNotificationSender()
    notes = NotificationService(
        sender, bookings=waitlist.bookings,
        recipients={f"patient-{n}": Recipient(f"patient-{n}", f"phone-{n}", NotificationChannel.SMS)
                    for n in names},
        reservation_expiry=bookings.reservation_expiry, pharmacy_name="Medi-Nest Indiranagar",
        clock=lambda: clock["now"], audit_events=waitlist.audit_events)
    waitlist.notify = notes
    context = PatientContextService(MockPatientContextSource(
        {"patient-asha": {"continuity_of_therapy": True}}), audit_events=waitlist.audit_events)

    def book(name):
        record = import_prescription(rx_source, f"MR-{name}", patients[name], received_at=clock["now"])
        waitlist.prescriptions[record.prescription.prescription_id] = record.prescription
        request = record.medication_requests[0]
        waitlist.medication_requests[request.medication_request_id] = request
        context.record_consent(users[name], f"patient-{name}", ConsentStatus.GRANTED,
                               reference=f"consent-{name}", at=clock["now"])
        outcome = bookings.submit(users[name], prescription_id=record.prescription.prescription_id,
                                  medication_request_id=request.medication_request_id,
                                  at=clock["now"], booking_id=f"bk-{name}")
        result = refresh_continuity(bookings, context, f"bk-{name}", at=clock["now"])
        allocation = bookings.allocate(system, f"bk-{name}", "indiranagar", at=clock["now"])
        print(f"  {name.title()}: {outcome.booking.booking_state.value} -> "
              f"{allocation.booking_state.value} (continuity {result.continuity.value})")

    step("Stock: 2 units of insulin. Asha, Bala and Chitra each need 2.")
    book("asha")
    advance(1)
    book("bala")
    advance(1)
    book("chitra")

    step("Pharmacist verifies Chitra's request as URGENT.")
    waitlist.contexts["bk-chitra"] = PriorityContext(
        "bk-chitra", "URGENT", "pharmacist", ContinuityStatus.UNKNOWN,
        bookings.bookings["bk-chitra"].created_at, ActorRole.PHARMACIST, "v1",
        evaluated_at=clock["now"], urgency_actor_id="pharm-1", urgency_verified_at=clock["now"])

    advance(5)
    step("2 more units arrive.")
    result = waitlist.process_restock(RestockEvent("restock-1", "INS-100", "indiranagar", 2,
                                                   clock["now"]), at=clock["now"])
    print(f"  Promoted: {', '.join(result.promoted_booking_ids)} (urgent first, Bala still waits)")

    advance(26)
    step("Asha does not collect in 30 minutes; her reservation expires.")
    expired = bookings.expire_reservations(system, at=clock["now"])
    print(f"  Expired: {', '.join(expired)}; Bala is now "
          f"{bookings.bookings['bk-bala'].booking_state.value}")

    advance(1)
    step("Chitra cancels.")
    bookings.cancel(users["chitra"], "bk-chitra", at=clock["now"])

    step("What Bala sees in the app:")
    view = patient_booking_view(users["bala"], bookings, "bk-bala", at=clock["now"])
    print(f"  {view.status}: {view.message}")
    print(f"  Collect by {view.reservation_expires_at:%H:%M %Z}")

    step("Messages Bala received (no medicine names on the lock screen):")
    for record in notes.history("bk-bala"):
        print(f"  [{record.event.delivery_status.value}] {sender.delivered[record.event.notification_id].body}")

    step("Manager overview:")
    overview = manager_overview(manager, bookings, notifications=notification_events(notes),
                                at=clock["now"])
    print(f"  Active bookings: {dict(overview.active_bookings)}")
    print(f"  Expired: {overview.fulfilment.expired}, cancelled: {overview.fulfilment.cancelled}")
    print(f"  Notifications: {dict(overview.notifications.by_delivery_status)}")
    print(f"  Audit events recorded: {len(waitlist.audit_events)}")


if __name__ == "__main__":
    main()
