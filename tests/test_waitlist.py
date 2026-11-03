"""MBA-16: waitlist entry, current-order promotion, restock recovery."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID
import unittest

from medicine_booking import (
    ActorRole, Booking, BookingStatus, ContinuityStatus, InMemoryInventoryGateway,
    Inventory, InventoryState, MedicationRequest, MedicationRequestStatus,
    PharmacistPrincipal, Prescription, PrescriptionSource, PrescriptionStatus, PriorityContext,
    PriorityPolicy, RestockEvent, WaitlistService, WaitlistState,
)


NOW = datetime(2026, 11, 1, 17, tzinfo=timezone.utc)
PROCESSED = NOW + timedelta(minutes=2)


def fixtures(names=("a", "b"), quantities=None, *, inventory=None, notify=None):
    quantities = quantities or {name: 1 for name in names}
    bookings = {}
    prescriptions = {}
    requests = {}
    contexts = {}
    for name in names:
        rx = f"rx-{name}"
        request_id = f"request-{name}"
        patient = f"patient-{name}"
        bookings[name] = Booking(name, patient, rx, request_id, quantities[name],
                                 booking_state=BookingStatus.ELIGIBLE,
                                 created_at=NOW - timedelta(days=1),
                                 updated_at=NOW - timedelta(hours=1))
        prescriptions[rx] = Prescription(
            rx, patient, PrescriptionSource.STRUCTURED_IMPORT,
            PrescriptionStatus.VALID, created_at=NOW - timedelta(days=1),
        )
        requests[request_id] = MedicationRequest(
            request_id, rx, quantities[name], medicine_code="MED-1",
            request_status=MedicationRequestStatus.ELIGIBLE,
        )
        contexts[name] = PriorityContext(
            name, "NORMAL", "pharmacist", ContinuityStatus.UNKNOWN,
            NOW - timedelta(hours=2), ActorRole.PHARMACIST, "v1",
            evaluated_at=NOW, urgency_actor_id="staff-1", urgency_verified_at=NOW,
        )
    stock = Inventory("stock-1", "MED-1", "branch-1", 0, 0,
                      InventoryState.IN_STOCK, updated_at=NOW - timedelta(hours=1))
    gateway = inventory or InMemoryInventoryGateway({("MED-1", "branch-1"): stock})
    policy = PriorityPolicy("v1", {"NORMAL": 1, "URGENT": 0},
                            {ContinuityStatus.CONTINUITY: 0,
                             ContinuityStatus.UNKNOWN: 1})
    service = WaitlistService(
        bookings=bookings, prescriptions=prescriptions,
        medication_requests=requests, contexts=contexts,
        inventory=gateway, policy=policy, notify=notify,
        reservation_duration=timedelta(minutes=30),
    )
    return service, gateway


def restock(event_id="restock-1", quantity=1, *, location="branch-1"):
    return RestockEvent(event_id, "MED-1", location, quantity, NOW + timedelta(minutes=1))


class WaitlistTests(unittest.TestCase):
    def test_creates_linked_unique_entries_only_for_insufficient_stock(self):
        service, _ = fixtures()
        a = service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        b = service.add_unfilled("b", "MED-1", "branch-1", at=NOW)
        self.assertNotEqual(a.waitlist_id, b.waitlist_id)
        self.assertEqual(UUID(a.waitlist_id).version, 4)
        self.assertEqual((a.booking_id, a.medicine_code, a.location_id, a.entered_at),
                         ("a", "MED-1", "branch-1", NOW))
        self.assertEqual(service.bookings["a"].booking_state, BookingStatus.WAITLISTED)
        self.assertEqual([event.event_type for event in service.audit_events],
                         ["WAITLIST_ENTERED", "WAITLIST_ENTERED"])
        with self.assertRaisesRegex(ValueError, "eligible booking"):
            service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        service.prescriptions["rx-b"] = replace(
            service.prescriptions["rx-b"], status=PrescriptionStatus.EXPIRED)
        # Already waitlisted requests are not duplicated; invalid new requests fail.
        with self.assertRaisesRegex(ValueError, "eligible booking"):
            service.add_unfilled("b", "MED-1", "branch-1", at=NOW)

    def test_restock_uses_current_policy_and_promotes_in_order(self):
        service, gateway = fixtures(("a", "b", "c"))
        entries = {name: service.add_unfilled(name, "MED-1", "branch-1", at=NOW)
                   for name in ("a", "b", "c")}
        service.contexts["c"] = replace(
            service.contexts["c"], verified_urgency="URGENT")
        result = service.process_restock(restock(quantity=2), at=PROCESSED)
        self.assertEqual(result.promoted_booking_ids, ("c", "a"))
        self.assertEqual(service.bookings["c"].booking_state, BookingStatus.RESERVED)
        self.assertEqual(service.entries[entries["c"].waitlist_id].state,
                         WaitlistState.PROMOTED)
        self.assertEqual(service.entries[entries["c"].waitlist_id].offered_at,
                         PROCESSED)
        self.assertEqual(service.entries[entries["c"].waitlist_id].reservation_expires_at,
                         PROCESSED + timedelta(minutes=30))
        self.assertEqual(service.entries[entries["b"].waitlist_id].state, WaitlistState.ACTIVE)
        self.assertEqual(gateway.read("MED-1", "branch-1").reserved_quantity, 2)
        self.assertIn("WAITLIST_PROMOTED", [event.event_type for event in service.audit_events])
        self.assertIn("STOCK_RESERVED", [event.event_type for event in service.audit_events])

    def test_stale_prescription_is_skipped_and_lower_ranked_booking_is_offered(self):
        service, _ = fixtures()
        service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        service.add_unfilled("b", "MED-1", "branch-1", at=NOW)
        service.prescriptions["rx-a"] = replace(
            service.prescriptions["rx-a"], status=PrescriptionStatus.EXPIRED)
        result = service.process_restock(restock(), at=PROCESSED)
        self.assertEqual(result.promoted_booking_ids, ("b",))
        self.assertEqual(result.skipped_booking_ids, ("a",))
        self.assertEqual(service.bookings["a"].booking_state, BookingStatus.WAITLISTED)
        self.assertTrue(any(event.reason == "PRESCRIPTION_NOT_VALID"
                            for event in service.audit_events))

    def test_fulfilled_booking_cannot_remain_actively_waitlisted(self):
        service, _ = fixtures(("a",))
        entry = service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        service.bookings["a"] = replace(
            service.bookings["a"], booking_state=BookingStatus.FULFILLED)
        result = service.process_restock(restock(), at=PROCESSED)
        self.assertEqual(result.promoted_booking_ids, ())
        self.assertEqual(result.skipped_booking_ids, ("a",))
        self.assertEqual(service.entries[entry.waitlist_id].state, WaitlistState.FULFILLED)

    def test_changed_policy_is_used_at_restock_time(self):
        service, _ = fixtures()
        service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        service.add_unfilled("b", "MED-1", "branch-1", at=NOW)
        service.contexts["b"] = replace(
            service.contexts["b"], continuity_status=ContinuityStatus.CONTINUITY)
        service.policy = PriorityPolicy(
            "v2", {"NORMAL": 1}, {ContinuityStatus.CONTINUITY: 0,
                                  ContinuityStatus.UNKNOWN: 1},
        )
        result = service.process_restock(restock(), at=PROCESSED)
        self.assertEqual(result.promoted_booking_ids, ("b",))

    def test_authorized_override_changes_order_but_cannot_bypass_safety(self):
        service, _ = fixtures()
        service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        service.add_unfilled("b", "MED-1", "branch-1", at=NOW)
        principal = PharmacistPrincipal(
            "pharmacist-1", ActorRole.PHARMACIST, True,
            frozenset({"allocation:override"}),
        )
        with self.assertRaisesRegex(ValueError, "reason"):
            service.set_priority_override("b", 0, principal, " ", at=NOW)
        override = service.set_priority_override(
            "b", 0, principal, "Verified exception", at=NOW)
        self.assertEqual(override.audit_event.event_type, "PRIORITY_OVERRIDE")
        self.assertEqual(service.process_restock(restock(), at=PROCESSED).promoted_booking_ids,
                         ("b",))

        another, _ = fixtures()
        another.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        another.add_unfilled("b", "MED-1", "branch-1", at=NOW)
        another.set_priority_override("b", 0, principal, "Verified exception", at=NOW)
        another.prescriptions["rx-b"] = replace(
            another.prescriptions["rx-b"], status=PrescriptionStatus.INVALID)
        result = another.process_restock(restock(), at=PROCESSED)
        self.assertEqual(result.promoted_booking_ids, ("a",))
        self.assertEqual(result.skipped_booking_ids, ("b",))

    def test_delayed_event_checks_expiry_at_processing_time(self):
        stock = Inventory("stock-1", "MED-1", "branch-1", 0, 0,
                          InventoryState.IN_STOCK, expiry_date=NOW.date(),
                          updated_at=NOW)
        gateway = InMemoryInventoryGateway({("MED-1", "branch-1"): stock})
        service, _ = fixtures(("a",), inventory=gateway)
        service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        result = service.process_restock(restock(), at=NOW + timedelta(days=1))
        self.assertEqual(result.promoted_booking_ids, ())
        self.assertEqual(result.skipped_booking_ids, ("a",))
        self.assertEqual(gateway.read("MED-1", "branch-1").reserved_quantity, 0)

    def test_duplicate_restock_never_adds_stock_or_reserves_twice(self):
        service, gateway = fixtures()
        service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        event = restock()
        self.assertEqual(service.process_restock(event, at=PROCESSED).promoted_booking_ids, ("a",))
        self.assertTrue(service.process_restock(event, at=PROCESSED).duplicate_event)
        self.assertEqual(gateway.read("MED-1", "branch-1").on_hand_quantity, 1)
        self.assertEqual(gateway.read("MED-1", "branch-1").reserved_quantity, 1)
        self.assertEqual(len(gateway.reservations), 1)
        with self.assertRaisesRegex(ValueError, "reused"):
            service.process_restock(replace(event, quantity=2), at=PROCESSED)

    def test_reservation_failure_rereads_inventory_and_retries(self):
        class FlakyGateway(InMemoryInventoryGateway):
            def __init__(self, inventory):
                super().__init__(inventory)
                self.reads = 0
                self.attempts = 0

            def read(self, medicine_code, location_id):
                self.reads += 1
                return super().read(medicine_code, location_id)

            def reserve(self, inventory_id, booking_id, quantity, reservation_key, *, at):
                self.attempts += 1
                if self.attempts == 1:
                    return False
                return super().reserve(inventory_id, booking_id, quantity,
                                       reservation_key, at=at)

        stock = Inventory("stock-1", "MED-1", "branch-1", 0, 0,
                          InventoryState.IN_STOCK, updated_at=NOW)
        gateway = FlakyGateway({("MED-1", "branch-1"): stock})
        service, _ = fixtures(("a",), inventory=gateway)
        service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        before = gateway.reads
        result = service.process_restock(restock(), at=PROCESSED)
        self.assertEqual(result.promoted_booking_ids, ("a",))
        self.assertEqual(gateway.attempts, 2)
        self.assertGreaterEqual(gateway.reads - before, 3)
        self.assertIn("ALLOCATION_FAILED", [event.event_type for event in service.audit_events])

    def test_competing_reservation_exhausts_stock_before_retry(self):
        class CompetingGateway(InMemoryInventoryGateway):
            def reserve(self, inventory_id, booking_id, quantity, reservation_key, *, at):
                if booking_id == "a" and "competing" not in self.reservations:
                    super().reserve(inventory_id, "other", quantity, "competing", at=at)
                    return False
                return super().reserve(inventory_id, booking_id, quantity,
                                       reservation_key, at=at)

        stock = Inventory("stock-1", "MED-1", "branch-1", 0, 0,
                          InventoryState.IN_STOCK, updated_at=NOW)
        gateway = CompetingGateway({("MED-1", "branch-1"): stock})
        service, _ = fixtures(("a",), inventory=gateway)
        service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        result = service.process_restock(restock(), at=PROCESSED)
        self.assertEqual(result.promoted_booking_ids, ())
        self.assertEqual(service.bookings["a"].booking_state, BookingStatus.WAITLISTED)
        self.assertEqual(gateway.read("MED-1", "branch-1").reserved_quantity, 1)
        self.assertEqual(len(gateway.reservations), 1)

    def test_lost_reservation_reply_reconciles_existing_key(self):
        class LostReplyGateway(InMemoryInventoryGateway):
            def __init__(self, inventory):
                super().__init__(inventory)
                self.first = True

            def reserve(self, inventory_id, booking_id, quantity, reservation_key, *, at):
                saved = super().reserve(inventory_id, booking_id, quantity,
                                        reservation_key, at=at)
                if self.first:
                    self.first = False
                    raise TimeoutError("reply lost after reservation")
                return saved

        stock = Inventory("stock-1", "MED-1", "branch-1", 0, 0,
                          InventoryState.IN_STOCK, updated_at=NOW)
        gateway = LostReplyGateway({("MED-1", "branch-1"): stock})
        service, _ = fixtures(("a",), inventory=gateway)
        service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        result = service.process_restock(restock(), at=PROCESSED)
        self.assertEqual(result.promoted_booking_ids, ("a",))
        self.assertEqual(service.bookings["a"].booking_state, BookingStatus.RESERVED)
        self.assertEqual(gateway.read("MED-1", "branch-1").reserved_quantity, 1)
        self.assertEqual(len(gateway.reservations), 1)
        self.assertIn("ALLOCATION_FAILED", [event.event_type for event in service.audit_events])

    def test_cancellation_releases_stock_and_promotes_next_waiting_booking(self):
        service, gateway = fixtures()
        entries = {name: service.add_unfilled(name, "MED-1", "branch-1", at=NOW)
                   for name in ("a", "b")}
        service.process_restock(restock(), at=PROCESSED)
        result = service.cancel_booking("a", at=PROCESSED + timedelta(minutes=1))
        self.assertEqual(result.promoted_booking_ids, ("b",))
        self.assertEqual(service.bookings["a"].booking_state, BookingStatus.CANCELLED)
        self.assertEqual(service.entries[entries["a"].waitlist_id].state,
                         WaitlistState.CANCELLED)
        self.assertEqual(service.bookings["b"].booking_state, BookingStatus.RESERVED)
        self.assertEqual(gateway.read("MED-1", "branch-1").reserved_quantity, 1)
        self.assertIn("STOCK_RELEASED", [event.event_type for event in service.audit_events])
        self.assertTrue(service.cancel_booking("a", at=PROCESSED + timedelta(minutes=2)).duplicate_event)
        self.assertEqual(len(gateway.reservations), 1)

    def test_notification_failure_does_not_reverse_reservation(self):
        def failed_notification(booking_id, event_type):
            raise RuntimeError("delivery unavailable")

        service, gateway = fixtures(("a",), notify=failed_notification)
        service.add_unfilled("a", "MED-1", "branch-1", at=NOW)
        result = service.process_restock(restock(), at=PROCESSED)
        self.assertEqual(result.promoted_booking_ids, ("a",))
        self.assertEqual(gateway.read("MED-1", "branch-1").reserved_quantity, 1)
        self.assertEqual(service.bookings["a"].booking_state, BookingStatus.RESERVED)
        self.assertEqual(sum(event.event_type == "NOTIFICATION_FAILED"
                             for event in service.audit_events), 2)

    def test_atomic_inventory_reservation_allows_only_one_last_unit(self):
        stock = Inventory("stock-1", "MED-1", "branch-1", 1, 0,
                          InventoryState.IN_STOCK, updated_at=NOW)
        gateway = InMemoryInventoryGateway({("MED-1", "branch-1"): stock})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda name: gateway.reserve("stock-1", name, 1, f"key-{name}", at=NOW),
                ("a", "b"),
            ))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(gateway.read("MED-1", "branch-1").reserved_quantity, 1)


if __name__ == "__main__":
    unittest.main()
