"""MBA-12: create and track eligible medicine bookings."""

from dataclasses import replace
from datetime import timedelta
import unittest

from medicine_booking import (
    AccessDenied, ActorRole, BookingPolicy, BookingStatus, DuplicateAction,
    DuplicateMatch, MedicationRequest, MedicationRequestStatus, Prescription,
    PrescriptionSource, PrescriptionStatus, order_bookings, transition_prescription,
)

from booking_support import NOW, PHARMACIST, book, build, patient


def events(service, booking_id=None):
    return [e.event_type for e in service.audit_events
            if booking_id is None or e.object_id == booking_id]


class CreateBookingTests(unittest.TestCase):
    def test_eligible_booking_has_links_timestamp_status_and_audit(self):
        service, _ = build()
        outcome = book(service, "a")
        self.assertTrue(outcome.accepted)
        booking = outcome.booking
        self.assertEqual((booking.patient_id, booking.prescription_id,
                          booking.medication_request_id, booking.requested_quantity),
                         ("patient-a", "rx-a", "req-a", 2))
        self.assertEqual(booking.created_at, NOW)
        self.assertIs(booking.booking_state, BookingStatus.ELIGIBLE)
        self.assertEqual(events(service, "bk-a"), ["BOOKING_SUBMITTED", "BOOKING_ELIGIBLE"])
        # The starting urgency comes from a trusted system source, not the patient.
        context = service.waitlist.contexts["bk-a"]
        self.assertIs(context.actor_role, ActorRole.SYSTEM)
        self.assertEqual(context.verified_urgency, "ROUTINE")

    def test_generated_ids_are_unique(self):
        service, _ = build(policy=BookingPolicy(duplicate_action=DuplicateAction.FLAG))
        ids = {service.submit(patient("patient-a"), prescription_id="rx-a",
                              medication_request_id="req-a", at=NOW).booking.booking_id
               for _ in range(5)}
        self.assertEqual(len(ids), 5)

    def test_invalid_or_expired_prescription_is_rejected_with_reason(self):
        for status in (PrescriptionStatus.INVALID, PrescriptionStatus.EXPIRED):
            with self.subTest(status=status):
                service, _ = build()
                service.prescriptions["rx-a"] = replace(service.prescriptions["rx-a"], status=status)
                outcome = book(service, "a")
                self.assertIs(outcome.booking.booking_state, BookingStatus.REJECTED)
                self.assertEqual(service.rejection_reasons["bk-a"],
                                 (f"PRESCRIPTION_{status.value}",))
                self.assertIn("BOOKING_REJECTED", events(service, "bk-a"))

    def test_prescription_under_review_holds_booking_until_recheck(self):
        service, _ = build()
        rx = service.prescriptions["rx-a"]
        service.prescriptions["rx-a"] = replace(rx, status=PrescriptionStatus.MANUAL_REVIEW_REQUIRED)
        outcome = book(service, "a")
        self.assertIs(outcome.booking.booking_state, BookingStatus.ELIGIBILITY_PENDING)
        self.assertEqual(outcome.reason_codes, ("PRESCRIPTION_AWAITING_REVIEW",))
        with self.assertRaises(AccessDenied):
            service.recheck_eligibility(patient("patient-a"), "bk-a", at=NOW)
        # Still under review: stays held.
        held = service.recheck_eligibility(PHARMACIST, "bk-a", at=NOW)
        self.assertIs(held.booking.booking_state, BookingStatus.ELIGIBILITY_PENDING)
        service.prescriptions["rx-a"] = transition_prescription(
            service.prescriptions["rx-a"], PrescriptionStatus.VALID, at=NOW)
        later = service.recheck_eligibility(PHARMACIST, "bk-a", at=NOW + timedelta(minutes=5))
        self.assertIs(later.booking.booking_state, BookingStatus.ELIGIBLE)
        self.assertNotIn("bk-a", service.hold_reasons)

    def test_bad_quantity_or_medicine_is_rejected_without_a_booking(self):
        service, _ = build()
        cases = [
            ({"quantity": 0}, "INVALID_QUANTITY"),
            ({"quantity": -3}, "INVALID_QUANTITY"),
            ({"quantity": True}, "INVALID_QUANTITY"),
            ({"quantity": 5}, "QUANTITY_DOES_NOT_MATCH_PRESCRIPTION"),
            ({"medication_request_id": "req-b"}, "MEDICINE_NOT_ON_PRESCRIPTION"),
            ({"medication_request_id": "nope"}, "MEDICINE_NOT_ON_PRESCRIPTION"),
        ]
        for extra, reason in cases:
            with self.subTest(extra=extra):
                args = {"prescription_id": "rx-a", "medication_request_id": "req-a", "at": NOW}
                args.update(extra)
                outcome = service.submit(patient("patient-a"), **args)
                self.assertIsNone(outcome.booking)
                self.assertEqual(outcome.reason_codes, (reason,))
        self.assertEqual(service.bookings, {})
        rejected = [e for e in service.audit_events if e.event_type == "BOOKING_REQUEST_REJECTED"]
        self.assertEqual(len(rejected), len(cases))

    def test_duplicate_active_booking_is_rejected(self):
        service, _ = build()
        book(service, "a", booking_id="first")
        second = book(service, "a", booking_id="second")
        self.assertIs(second.booking.booking_state, BookingStatus.REJECTED)
        self.assertEqual(second.reason_codes, ("DUPLICATE_ACTIVE_BOOKING",))
        self.assertEqual(service.rejection_reasons["second"], ("DUPLICATE_ACTIVE_BOOKING",))

    def test_rejected_booking_does_not_count_as_a_duplicate(self):
        service, _ = build()
        valid = service.prescriptions["rx-a"]
        service.prescriptions["rx-a"] = replace(valid, status=PrescriptionStatus.EXPIRED)
        book(service, "a", booking_id="first")
        service.prescriptions["rx-a"] = valid
        self.assertTrue(book(service, "a", booking_id="second").accepted)

    def test_patient_cannot_create_booking_on_someone_elses_prescription(self):
        service, _ = build()
        with self.assertRaises(AccessDenied):
            service.submit(patient("patient-a"), prescription_id="rx-b",
                           medication_request_id="req-b", at=NOW)
        self.assertEqual(service.bookings, {})

    def test_duplicate_can_be_flagged_instead_of_rejected(self):
        service, _ = build(policy=BookingPolicy(duplicate_action=DuplicateAction.FLAG))
        book(service, "a", booking_id="first")
        second = book(service, "a", booking_id="second")
        self.assertTrue(second.accepted)
        self.assertEqual(second.flags, ("POSSIBLE_DUPLICATE",))
        flagged = [e for e in service.audit_events if e.event_type == "BOOKING_DUPLICATE_FLAGGED"]
        self.assertEqual(flagged[0].reason, "existing_booking=first")

    def test_same_request_mode_allows_same_medicine_on_another_prescription(self):
        service, _ = build(policy=BookingPolicy(duplicate_match=DuplicateMatch.SAME_REQUEST))
        service.prescriptions["rx-a2"] = Prescription(
            "rx-a2", "patient-a", PrescriptionSource.UPLOAD, PrescriptionStatus.VALID,
            created_at=NOW - timedelta(hours=1))
        service.medication_requests["req-a2"] = MedicationRequest(
            "req-a2", "rx-a2", 2, medicine_code="MED-1",
            request_status=MedicationRequestStatus.ELIGIBLE, created_at=NOW - timedelta(hours=1))
        book(service, "a")
        other = service.submit(patient("patient-a"), prescription_id="rx-a2",
                               medication_request_id="req-a2", at=NOW)
        self.assertTrue(other.accepted)
        repeat = book(service, "a", booking_id="again")
        self.assertEqual(repeat.reason_codes, ("DUPLICATE_ACTIVE_BOOKING",))

    def test_terminal_bookings_are_excluded_from_priority_order(self):
        service, _ = build()
        book(service, "a")
        service.prescriptions["rx-b"] = replace(service.prescriptions["rx-b"],
                                                status=PrescriptionStatus.EXPIRED)
        book(service, "b")
        ranked = order_bookings(service.bookings.values(), service.waitlist.contexts,
                                service.waitlist.policy, evaluated_at=NOW)
        self.assertEqual([r.booking_id for r in ranked], ["bk-a"])


if __name__ == "__main__":
    unittest.main()
