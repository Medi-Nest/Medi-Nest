"""MBA-17: allocation, partial allocation, reservation expiry, and cancellation."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
import unittest

from medicine_booking import (
    AccessDenied, BookingPolicy, BookingStatus, PrescriptionStatus, SafetyPolicy,
    WaitlistState,
)

from booking_support import LOCATION, MANAGER, NOW, PHARMACIST, SYSTEM, book, build, patient


def events(service, booking_id=None):
    return [e.event_type for e in service.audit_events
            if booking_id is None or e.object_id == booking_id]


class AllocationRulesTests(unittest.TestCase):
    def test_duplicate_is_allowed_again_after_cancellation(self):
        service, _ = build(stock=0)
        book(service, "a", booking_id="first")
        service.allocate(SYSTEM, "first", LOCATION, at=NOW)
        service.cancel(patient("patient-a"), "first", at=NOW)
        self.assertTrue(book(service, "a", booking_id="again").accepted)

    def test_rejected_booking_cannot_be_allocated(self):
        service, _ = build()
        service.prescriptions["rx-a"] = replace(service.prescriptions["rx-a"],
                                                status=PrescriptionStatus.EXPIRED)
        book(service, "a")
        with self.assertRaisesRegex(ValueError, "eligible booking"):
            service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)

    def test_patient_cannot_cancel_another_patients_booking(self):
        service, _ = build()
        book(service, "b")
        with self.assertRaises(AccessDenied):
            service.cancel(patient("patient-a"), "bk-b", at=NOW)
        self.assertEqual(service.bookings["bk-b"].booking_state.value, "ELIGIBLE")

    def test_patients_and_managers_cannot_run_allocation_or_expiry(self):
        service, _ = build()
        book(service, "a")
        for principal in (patient("patient-a"), MANAGER):
            with self.assertRaises(AccessDenied):
                service.allocate(principal, "bk-a", "branch-1", at=NOW)
            with self.assertRaises(AccessDenied):
                service.expire_reservations(principal, at=NOW)
        service.allocate(SYSTEM, "bk-a", "branch-1", at=NOW)



class AllocationTests(unittest.TestCase):
    def test_full_allocation_records_location_quantity_batch_and_expiry(self):
        service, gateway = build(stock=5)
        book(service, "a")
        outcome = service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        self.assertIs(outcome.booking_state, BookingStatus.RESERVED)
        allocation = outcome.allocation
        self.assertEqual((allocation.location_id, allocation.allocated_quantity,
                          allocation.remaining_quantity, allocation.batch_id),
                         (LOCATION, 2, 0, "B-7"))
        self.assertEqual(allocation.expires_at, NOW + timedelta(minutes=30))
        self.assertEqual(gateway.read("MED-1", LOCATION).reserved_quantity, 2)
        self.assertIn("STOCK_RESERVED", events(service, "bk-a"))

    def test_insufficient_stock_waitlists_full_request_when_partial_disabled(self):
        service, gateway = build(stock=1)
        book(service, "a")
        outcome = service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        self.assertIs(outcome.booking_state, BookingStatus.WAITLISTED)
        self.assertIsNone(outcome.allocation)
        self.assertEqual(gateway.read("MED-1", LOCATION).reserved_quantity, 0)
        self.assertEqual(events(service)[-2:], ["ALLOCATION_FAILED", "WAITLIST_ENTERED"])

    def test_partial_allocation_tracks_allocated_and_remaining_separately(self):
        service, gateway = build(stock=1, policy=BookingPolicy(partial_allocation=True))
        book(service, "a")
        outcome = service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        self.assertIs(outcome.booking_state, BookingStatus.RESERVED)
        self.assertEqual(outcome.reason_codes, ("PARTIAL_ALLOCATION",))
        self.assertEqual((outcome.allocation.allocated_quantity,
                          outcome.allocation.remaining_quantity), (1, 1))
        self.assertEqual(gateway.read("MED-1", LOCATION).reserved_quantity, 1)
        # The remaining unit is not also waitlisted (REQ-AW-27, BR-05).
        self.assertEqual(service.waitlist.entries, {})

    def test_partial_allocation_with_no_stock_still_waitlists(self):
        service, _ = build(stock=0, policy=BookingPolicy(partial_allocation=True))
        book(service, "a")
        self.assertIs(service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW).booking_state,
                      BookingStatus.WAITLISTED)

    def test_safety_block_keeps_booking_eligible_and_audits(self):
        service, gateway = build()
        service.waitlist.safety_policy = SafetyPolicy(restricted_medicine_codes=frozenset({"MED-1"}))
        book(service, "a")
        outcome = service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        self.assertIs(outcome.booking_state, BookingStatus.ELIGIBLE)
        self.assertIn("RESTRICTED_MEDICINE_REQUIRES_REVIEW", outcome.reason_codes)
        self.assertEqual(gateway.read("MED-1", LOCATION).reserved_quantity, 0)
        self.assertIn("ALLOCATION_BLOCKED", events(service, "bk-a"))

    def test_concurrent_requests_cannot_both_take_the_last_stock(self):
        for _ in range(20):
            service, gateway = build(stock=2)
            book(service, "a")
            book(service, "b")
            barrier = Barrier(2)

            def run(booking_id):
                barrier.wait()
                return service.allocate(SYSTEM, booking_id, LOCATION, at=NOW).booking_state

            with ThreadPoolExecutor(2) as pool:
                states = sorted(s.value for s in pool.map(run, ["bk-a", "bk-b"]))
            self.assertEqual(states, ["RESERVED", "WAITLISTED"])
            self.assertEqual(gateway.read("MED-1", LOCATION).reserved_quantity, 2)

    def test_notification_failure_does_not_undo_reservation(self):
        def broken(booking_id, event_type):
            raise ConnectionError("provider down")

        service, gateway = build(notify=broken)
        book(service, "a")
        outcome = service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        self.assertIs(outcome.booking_state, BookingStatus.RESERVED)
        self.assertEqual(gateway.read("MED-1", LOCATION).reserved_quantity, 2)
        self.assertIn("NOTIFICATION_FAILED", events(service, "bk-a"))


class CancelAndExpiryTests(unittest.TestCase):
    def setUp(self):
        # 2 units: a gets them directly, b waits.
        self.service, self.gateway = build(stock=2)
        book(self.service, "a")
        book(self.service, "b", at=NOW + timedelta(minutes=1))
        self.service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW + timedelta(minutes=1))
        self.service.allocate(SYSTEM, "bk-b", LOCATION, at=NOW + timedelta(minutes=1))

    def test_cancelling_direct_reservation_releases_and_promotes_waitlist(self):
        when = NOW + timedelta(minutes=5)
        cancelled = self.service.cancel(patient("patient-a"), "bk-a", at=when)
        self.assertIs(cancelled.booking_state, BookingStatus.CANCELLED)
        self.assertEqual(self.service.allocations["bk-a"].release_reason, "CANCELLED")
        self.assertIs(self.service.bookings["bk-b"].booking_state, BookingStatus.RESERVED)
        self.assertEqual(self.gateway.read("MED-1", LOCATION).reserved_quantity, 2)
        self.assertEqual(self.service.reservation_expiry("bk-b"), when + timedelta(minutes=30))

    def test_cancelling_waitlisted_booking_frees_its_entry(self):
        self.service.cancel(patient("patient-b"), "bk-b", at=NOW + timedelta(minutes=5))
        self.assertIs(self.service.bookings["bk-b"].booking_state, BookingStatus.CANCELLED)
        entry, = self.service.waitlist.entries.values()
        self.assertIs(entry.state, WaitlistState.CANCELLED)
        self.assertEqual(self.gateway.read("MED-1", LOCATION).reserved_quantity, 2)

    def test_cannot_cancel_closed_or_unstarted_booking(self):
        book(self.service, "c")
        with self.assertRaisesRegex(ValueError, "cannot be cancelled"):
            self.service.cancel(patient("patient-c"), "bk-c", at=NOW + timedelta(minutes=5))
        self.service.cancel(PHARMACIST, "bk-a", at=NOW + timedelta(minutes=5))
        with self.assertRaisesRegex(ValueError, "cannot be cancelled"):
            self.service.cancel(PHARMACIST, "bk-a", at=NOW + timedelta(minutes=6))

    def test_expiry_releases_stock_and_reoffers_it(self):
        self.assertEqual(self.service.expire_reservations(SYSTEM, at=NOW + timedelta(minutes=30)), ())
        when = NOW + timedelta(minutes=31)
        expired = self.service.expire_reservations(SYSTEM, at=when)
        self.assertEqual(expired, ("bk-a",))
        self.assertIs(self.service.bookings["bk-a"].booking_state, BookingStatus.EXPIRED)
        self.assertIs(self.service.bookings["bk-b"].booking_state, BookingStatus.RESERVED)
        self.assertEqual(self.gateway.read("MED-1", LOCATION).reserved_quantity, 2)
        self.assertIn("RESERVATION_EXPIRED", events(self.service, "bk-a"))
        # Running again changes nothing.
        self.assertEqual(self.service.expire_reservations(SYSTEM, at=when), ())

    def test_promoted_waitlist_reservations_also_expire(self):
        self.service.cancel(patient("patient-a"), "bk-a", at=NOW + timedelta(minutes=5))
        expired = self.service.expire_reservations(SYSTEM, at=NOW + timedelta(hours=1))
        self.assertEqual(expired, ("bk-b",))
        self.assertEqual(self.gateway.read("MED-1", LOCATION).reserved_quantity, 0)

    def test_reserved_never_exceeds_on_hand(self):
        inventory = self.gateway.read("MED-1", LOCATION)
        self.assertLessEqual(inventory.reserved_quantity, inventory.on_hand_quantity)


if __name__ == "__main__":
    unittest.main()
