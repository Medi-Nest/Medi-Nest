"""MBA-23: end-to-end scenarios across intake, priority, allocation, waitlist,
notification, cancellation, expiry, and audit, using only test doubles."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from statistics import quantiles
from time import perf_counter
import unittest

from medicine_booking import (
    AccessDenied, ActorRole, BookingPolicy, BookingService, BookingStatus,
    ConsentStatus, ContinuityStatus, DeliveryStatus, InMemoryInventoryGateway,
    Inventory, InventoryState, MedicationRequestStatus, NotificationChannel, Patient,
    PrescriptionStatus, Principal, PriorityContext, PriorityPolicy, RestockEvent,
    SafetyPolicy, WaitlistService, manager_overview, patient_booking_view,
    transition_prescription,
)
from medicine_booking.contracts import (
    FaultyInventoryGateway, MockAuthProvider, MockNotificationSender,
    MockPatientContextSource, MockPrescriptionSource, import_prescription,
)
from medicine_booking.notifications import NotificationService, Recipient, notification_events
from medicine_booking.patient_context import PatientContextService, refresh_continuity

NOW = datetime(2026, 11, 15, 9, tzinfo=timezone.utc)
LOCATION = "branch-1"


def rx_payload(source_id, patient_ref, status="active", code="MED-1", quantity=2):
    return {
        "source_system": "abdm", "source_identifier": source_id,
        "patient_reference": patient_ref, "status": status,
        "authored_on": "2026-11-14T10:00:00+05:30",
        "prescriber_reference": "Practitioner/dr-9",
        "lines": [{"source_line_id": "1", "medicine_code": code,
                   "medicine_name": "Prescribed medicine", "quantity": quantity}],
    }


class Pharmacy:
    """A complete in-memory deployment wired only to test doubles."""

    def __init__(self, *, stock=2, notification_failures=0, context_failures=0,
                 inventory_failures=0, safety=None, payload_status=None):
        self.clock = NOW
        names = ("a", "b", "c")
        self.patients = {n: Patient(f"patient-{n}", f"Patient {n.upper()}",
                                    external_patient_id=f"ABHA-{n}") for n in names}
        statuses = payload_status or {}
        self.rx_source = MockPrescriptionSource({
            f"MR-{n}": rx_payload(f"MR-{n}", f"ABHA-{n}", statuses.get(n, "active"))
            for n in names})
        self.tokens = {f"tok-{n}": Principal(f"user-{n}", ActorRole.PATIENT, True,
                                             patient_ids=frozenset({f"patient-{n}"}))
                       for n in names}
        self.tokens.update({
            "tok-pharm": Principal("pharm-1", ActorRole.PHARMACIST, True),
            "tok-mgr": Principal("mgr-1", ActorRole.PHARMACY_MANAGER, True),
            "tok-sys": Principal("scheduler", ActorRole.SYSTEM, True),
        })
        self.auth = MockAuthProvider(self.tokens)

        inner = InMemoryInventoryGateway({("MED-1", LOCATION): Inventory(
            "stock-1", "MED-1", LOCATION, stock, 0, InventoryState.IN_STOCK,
            batch_id="B-7", updated_at=NOW - timedelta(hours=1))})
        self.inventory = inner
        gateway = (FaultyInventoryGateway(inner, fail_reserve_times=inventory_failures)
                   if inventory_failures else inner)
        policy = PriorityPolicy("v1", {"URGENT": 0, "ROUTINE": 1},
                                {ContinuityStatus.CONTINUITY: 0,
                                 ContinuityStatus.NO_CONTINUITY: 1,
                                 ContinuityStatus.UNKNOWN: 1},
                                trusted_urgency_sources=frozenset({"booking-service"}))
        self.waitlist = WaitlistService(
            bookings={}, prescriptions={}, medication_requests={}, contexts={},
            inventory=gateway, policy=policy, reservation_duration=timedelta(minutes=30),
            safety_policy=safety)
        self.bookings = BookingService(self.waitlist, policy=BookingPolicy(
            reservation_duration=timedelta(minutes=30), default_urgency="ROUTINE"))
        self.sender = MockNotificationSender(fail_times=notification_failures)
        self.notes = NotificationService(
            self.sender, bookings=self.waitlist.bookings,
            recipients={f"patient-{n}": Recipient(f"patient-{n}", f"contact-{n}",
                                                  NotificationChannel.PUSH) for n in names},
            reservation_expiry=self.bookings.reservation_expiry,
            clock=lambda: self.clock, audit_events=self.waitlist.audit_events)
        self.waitlist.notify = self.notes
        self.context_source = MockPatientContextSource(
            {"patient-a": {"continuity_of_therapy": True},
             "patient-b": {"continuity_of_therapy": False}},
            source_timestamp=NOW - timedelta(days=1), fail_times=context_failures)
        self.context = PatientContextService(self.context_source,
                                             audit_events=self.waitlist.audit_events)

    def who(self, token):
        return self.auth.authenticate(token, at=self.clock)

    def tick(self, minutes):
        self.clock = self.clock + timedelta(minutes=minutes)
        return self.clock

    def intake(self, name):
        record = import_prescription(self.rx_source, f"MR-{name}", self.patients[name],
                                     received_at=self.clock)
        rx = record.prescription
        self.waitlist.prescriptions[rx.prescription_id] = rx
        for request in record.medication_requests:
            self.waitlist.medication_requests[request.medication_request_id] = request
        return rx.prescription_id, record.medication_requests[0].medication_request_id

    def request_booking(self, name):
        rx_id, request_id = self.intake(name)
        self.context.record_consent(self.who(f"tok-{name}"), f"patient-{name}",
                                    ConsentStatus.GRANTED, reference=f"consent-{name}",
                                    at=self.clock)
        outcome = self.bookings.submit(self.who(f"tok-{name}"), prescription_id=rx_id,
                                       medication_request_id=request_id, at=self.clock,
                                       booking_id=f"bk-{name}")
        if outcome.booking.booking_state is BookingStatus.ELIGIBLE:
            refresh_continuity(self.bookings, self.context, f"bk-{name}", at=self.clock)
        return outcome

    def allocate(self, name):
        return self.bookings.allocate(self.who("tok-sys"), f"bk-{name}", LOCATION, at=self.clock)

    def state(self, name):
        return self.bookings.bookings[f"bk-{name}"].booking_state

    def note_kinds(self, name):
        return [r.event_type for r in self.notes.history(f"bk-{name}")]


class FullWorkflowTests(unittest.TestCase):
    def test_booking_review_priority_allocation_waitlist_and_audit(self):
        p = Pharmacy(stock=2, payload_status={"c": "on-hold"})

        # 1. Patient A books; continuity from the context source; stock reserved.
        self.assertTrue(p.request_booking("a").accepted)
        self.assertIs(p.waitlist.contexts["bk-a"].continuity_status, ContinuityStatus.CONTINUITY)
        self.assertIs(p.allocate("a").booking_state, BookingStatus.RESERVED)

        # 2. Patient B books; no stock left, so B waits.
        p.tick(1)
        self.assertTrue(p.request_booking("b").accepted)
        self.assertIs(p.allocate("b").booking_state, BookingStatus.WAITLISTED)

        # 3. Patient C's prescription needs review; the pharmacist approves it.
        p.tick(1)
        held = p.request_booking("c")
        self.assertIs(held.booking.booking_state, BookingStatus.ELIGIBILITY_PENDING)
        rx_id = held.booking.prescription_id
        p.waitlist.prescriptions[rx_id] = transition_prescription(
            p.waitlist.prescriptions[rx_id], PrescriptionStatus.VALID, at=p.clock,
            reason="Pharmacist confirmed with prescriber")
        req_id = held.booking.medication_request_id
        p.waitlist.medication_requests[req_id] = replace(
            p.waitlist.medication_requests[req_id],
            request_status=MedicationRequestStatus.ELIGIBLE)
        p.tick(1)
        self.assertIs(p.bookings.recheck_eligibility(p.who("tok-pharm"), "bk-c",
                                                     at=p.clock).booking.booking_state,
                      BookingStatus.ELIGIBLE)
        self.assertIs(p.allocate("c").booking_state, BookingStatus.WAITLISTED)

        # 4. The pharmacist verifies C as urgent, so C now outranks B.
        p.waitlist.contexts["bk-c"] = PriorityContext(
            "bk-c", "URGENT", "pharmacist", ContinuityStatus.UNKNOWN,
            p.bookings.bookings["bk-c"].created_at, ActorRole.PHARMACIST, "v1",
            evaluated_at=p.clock, urgency_actor_id="pharm-1", urgency_verified_at=p.clock)

        # 5. A restock of 2 units goes to C first.
        p.tick(1)
        result = p.waitlist.process_restock(
            RestockEvent("restock-1", "MED-1", LOCATION, 2, p.clock), at=p.clock)
        self.assertEqual(result.promoted_booking_ids, ("bk-c",))
        self.assertIs(p.state("b"), BookingStatus.WAITLISTED)

        # 6. A lets the reservation lapse (30 minutes); the released stock goes to B.
        p.tick(27)
        self.assertIn("bk-a", p.bookings.expire_reservations(p.who("tok-sys"), at=p.clock))
        self.assertIs(p.state("b"), BookingStatus.RESERVED)

        # 7. C cancels; stock is released.
        p.tick(1)
        p.bookings.cancel(p.who("tok-c"), "bk-c", at=p.clock)
        self.assertIs(p.state("c"), BookingStatus.CANCELLED)
        stock = p.inventory.read("MED-1", LOCATION)
        self.assertEqual((stock.on_hand_quantity, stock.reserved_quantity), (4, 2))

        # 8. Every user was told about every change.
        self.assertEqual(p.note_kinds("a"), ["SUBMITTED", "RESERVED", "EXPIRED"])
        self.assertEqual(p.note_kinds("b"), ["SUBMITTED", "WAITLISTED", "RESERVED"])
        self.assertEqual(p.note_kinds("c"), ["SUBMITTED", "WAITLISTED", "RESERVED", "CANCELLED"])

        # 9. Patients see only their own booking, in plain language.
        view = patient_booking_view(p.who("tok-b"), p.bookings, "bk-b", at=p.clock)
        self.assertEqual(view.status, "Medicine Reserved")
        with self.assertRaises(AccessDenied):
            patient_booking_view(p.who("tok-b"), p.bookings, "bk-c", at=p.clock)

        # 10. The manager sees the whole history without patient identities.
        overview = manager_overview(p.who("tok-mgr"), p.bookings,
                                    notifications=notification_events(p.notes), at=p.clock)
        self.assertEqual(dict(overview.active_bookings), {"RESERVED": 1})
        self.assertEqual(overview.fulfilment.expired, 1)
        self.assertEqual(overview.fulfilment.cancelled, 1)
        self.assertEqual(overview.notifications.by_delivery_status["SENT"], 10)
        types = {e.event_type for e in p.waitlist.audit_events}
        for expected in ("BOOKING_SUBMITTED", "BOOKING_HELD_FOR_REVIEW", "BOOKING_ELIGIBLE",
                         "STOCK_RESERVED", "WAITLIST_ENTERED", "WAITLIST_PROMOTED",
                         "RESERVATION_EXPIRED", "STOCK_RELEASED", "BOOKING_CANCELLED",
                         "CONSENT_RECORDED", "PATIENT_CONTEXT_LOOKUP"):
            self.assertIn(expected, types)
        self.assertNotIn("Patient A", repr(overview))

    def test_restricted_medicine_waits_for_pharmacist_review(self):
        p = Pharmacy(stock=5, safety=SafetyPolicy(restricted_medicine_codes=frozenset({"MED-1"})))
        p.request_booking("a")
        blocked = p.allocate("a")
        self.assertIn("RESTRICTED_MEDICINE_REQUIRES_REVIEW", blocked.reason_codes)
        self.assertIs(p.state("a"), BookingStatus.ELIGIBLE)
        p.tick(2)
        p.waitlist.approve_manual_review("bk-a", "MED-1",
                                         p.who("tok-pharm").as_pharmacist(at=p.clock), at=p.clock)
        self.assertIs(p.allocate("a").booking_state, BookingStatus.RESERVED)
        self.assertIn("MANUAL_REVIEW_COMPLETED", [e.event_type for e in p.waitlist.audit_events])

    def test_pharmacist_override_is_audited_and_changes_order(self):
        p = Pharmacy(stock=0)
        for name in ("a", "b"):
            p.request_booking(name)
            p.allocate(name)
            p.tick(1)
        # A has continuity so ranks first; the pharmacist moves B ahead.
        p.waitlist.set_priority_override("bk-b", 0, p.who("tok-pharm").as_pharmacist(at=p.clock),
                                         "Patient travelling tomorrow", at=p.clock)
        p.tick(1)
        result = p.waitlist.process_restock(
            RestockEvent("r-1", "MED-1", LOCATION, 2, p.clock), at=p.clock)
        self.assertEqual(result.promoted_booking_ids, ("bk-b",))
        override, = manager_overview(p.who("tok-mgr"), p.bookings, at=p.clock).overrides
        self.assertEqual((override.actor, override.reason),
                         ("pharm-1", "Patient travelling tomorrow"))


class FailureScenarioTests(unittest.TestCase):
    def test_external_outages_degrade_safely(self):
        p = Pharmacy(stock=2, notification_failures=2, context_failures=5, inventory_failures=1)
        self.assertTrue(p.request_booking("a").accepted)
        # Context source down: continuity is UNKNOWN, booking still ranks.
        self.assertIs(p.waitlist.contexts["bk-a"].continuity_status, ContinuityStatus.UNKNOWN)
        # First reservation attempt fails, the retry rereads and succeeds.
        self.assertIs(p.allocate("a").booking_state, BookingStatus.RESERVED)
        self.assertEqual(p.inventory.read("MED-1", LOCATION).reserved_quantity, 2)
        # Notifications failed but did not block anything, and recover on retry.
        self.assertTrue(all(r.event.delivery_status is DeliveryStatus.FAILED
                            for r in p.notes.history("bk-a")))
        p.notes.retry_due(at=p.tick(2))
        self.assertTrue(all(r.event.delivery_status is DeliveryStatus.SENT
                            for r in p.notes.history("bk-a")))

    def test_duplicate_restock_event_is_ignored(self):
        p = Pharmacy(stock=0)
        p.request_booking("a")
        p.allocate("a")
        event = RestockEvent("r-1", "MED-1", LOCATION, 2, p.tick(1))
        p.waitlist.process_restock(event, at=p.clock)
        again = p.waitlist.process_restock(event, at=p.tick(1))
        self.assertTrue(again.duplicate_event)
        stock = p.inventory.read("MED-1", LOCATION)
        self.assertEqual((stock.on_hand_quantity, stock.reserved_quantity), (2, 2))

    def test_forged_token_cannot_act(self):
        p = Pharmacy()
        with self.assertRaises(AccessDenied):
            p.who("tok-forged")


class PerformanceSmokeTests(unittest.TestCase):
    """NFR-P-01..03 at prototype scale: 300 bookings, in-memory adapters."""

    def test_status_reads_submissions_and_restock_meet_targets(self):
        p = Pharmacy(stock=0)
        submit_times, read_times = [], []
        names = [f"p{i}" for i in range(300)]
        for i, name in enumerate(names):
            p.patients[name] = Patient(f"patient-{name}", name, external_patient_id=f"ABHA-{name}")
            p.rx_source.payloads[f"MR-{name}"] = rx_payload(f"MR-{name}", f"ABHA-{name}")
            p.tokens[f"tok-{name}"] = Principal(f"user-{name}", ActorRole.PATIENT, True,
                                                patient_ids=frozenset({f"patient-{name}"}))
            p.notes.recipients[f"patient-{name}"] = Recipient(
                f"patient-{name}", f"contact-{name}", NotificationChannel.PUSH)
        p.auth = MockAuthProvider(p.tokens)
        for name in names:
            start = perf_counter()
            p.request_booking(name)
            p.allocate(name)
            submit_times.append(perf_counter() - start)
        for name in names:
            start = perf_counter()
            patient_booking_view(p.who(f"tok-{name}"), p.bookings, f"bk-{name}", at=p.clock)
            read_times.append(perf_counter() - start)
        start = perf_counter()
        result = p.waitlist.process_restock(
            RestockEvent("big", "MED-1", LOCATION, 600, p.tick(1)), at=p.clock)
        restock_seconds = perf_counter() - start
        self.assertEqual(len(result.promoted_booking_ids), 300)
        self.assertLess(quantiles(submit_times, n=20)[-1], 2.0)   # p95 submission
        self.assertLess(quantiles(read_times, n=20)[-1], 1.0)     # p95 status read
        self.assertLess(restock_seconds, 3.0)


if __name__ == "__main__":
    unittest.main()
