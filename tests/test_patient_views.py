"""MBA-14: patient booking, status, and waitlist views."""

from dataclasses import fields, replace
from datetime import timedelta
import unittest

from medicine_booking import (
    AccessDenied, ActorRole, BookingPolicy, EmergencyGuidance, PatientBookingView,
    Principal, PrescriptionStatus, patient_booking_view, patient_bookings,
)

from booking_support import LOCATION, NOW, PHARMACIST, SYSTEM, book, build, patient


class PatientViewTests(unittest.TestCase):
    def setUp(self):
        # a reserved directly, b waitlisted behind a, c prescription expired.
        self.service, _ = build(stock=2)
        book(self.service, "a")
        book(self.service, "b")
        self.service.prescriptions["rx-c"] = replace(self.service.prescriptions["rx-c"],
                                                     status=PrescriptionStatus.EXPIRED)
        book(self.service, "c")
        self.service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        self.service.allocate(SYSTEM, "bk-b", LOCATION, at=NOW)

    def test_reserved_view_shows_plain_status_and_expiry(self):
        view = patient_booking_view(patient("patient-a"), self.service, "bk-a", at=NOW)
        self.assertEqual(view.status, "Medicine Reserved")
        self.assertEqual(view.medicine, "Insulin glargine")
        self.assertEqual((view.reserved_quantity, view.outstanding_quantity), (2, 0))
        self.assertEqual(view.reservation_expires_at, NOW + timedelta(minutes=30))
        self.assertTrue(view.can_cancel)

    def test_waitlisted_view_has_no_position_rank_or_other_patients(self):
        view = patient_booking_view(patient("patient-b"), self.service, "bk-b", at=NOW)
        self.assertEqual(view.status, "Waitlisted")
        self.assertIsNone(view.reservation_expires_at)
        self.assertIn("This medicine is not in stock right now.", view.notices)
        names = {f.name for f in fields(PatientBookingView)}
        for forbidden in ("rank", "priority", "urgency", "continuity", "position", "patient_id"):
            self.assertFalse(any(forbidden in name for name in names), forbidden)
        rendered = repr(view)
        for other in ("patient-a", "bk-a", "rx-a", "ROUTINE", "URGENCY"):
            self.assertNotIn(other, rendered)

    def test_rejected_view_explains_reason_in_plain_language(self):
        view = patient_booking_view(patient("patient-c"), self.service, "bk-c", at=NOW)
        self.assertEqual(view.status, "Booking Rejected")
        self.assertEqual(view.notices,
                         ("Your prescription has expired. Please get a new prescription.",))
        self.assertFalse(view.can_cancel)
        for notice in view.notices:
            self.assertNotIn("_", notice)

    def test_partial_allocation_is_explained(self):
        service, _ = build(stock=1, policy=BookingPolicy(partial_allocation=True))
        book(service, "a")
        service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        view = patient_booking_view(patient("patient-a"), service, "bk-a", at=NOW)
        self.assertEqual((view.reserved_quantity, view.outstanding_quantity), (1, 1))
        self.assertIn("Only part of your requested quantity is in stock and has been reserved.",
                      view.notices)

    def test_list_contains_only_the_callers_bookings(self):
        views = patient_bookings(patient("patient-a"), self.service, at=NOW)
        self.assertEqual([v.booking_id for v in views], ["bk-a"])
        with self.assertRaises(AccessDenied):
            patient_bookings(patient("patient-a"), self.service, "patient-b", at=NOW)

    def test_caregiver_with_several_patients_must_choose_one(self):
        carer = Principal("carer", ActorRole.CAREGIVER, True,
                          patient_ids=frozenset({"patient-a", "patient-b"}))
        with self.assertRaisesRegex(ValueError, "patient_id is required"):
            patient_bookings(carer, self.service, at=NOW)
        self.assertEqual(len(patient_bookings(carer, self.service, "patient-b", at=NOW)), 1)

    def test_known_booking_id_does_not_expose_another_patients_booking(self):
        with self.assertRaises(AccessDenied):
            patient_booking_view(patient("patient-a"), self.service, "bk-b", at=NOW)

    def test_missing_and_foreign_records_fail_identically_for_patients(self):
        errors = []
        for booking_id in ("bk-b", "does-not-exist"):
            with self.assertRaises(AccessDenied) as ctx:
                patient_booking_view(patient("patient-a"), self.service, booking_id, at=NOW)
            errors.append(str(ctx.exception))
        self.assertEqual(errors[0], errors[1])
        self.assertNotIn("bk-b", errors[0])


    def test_pharmacist_may_read_any_booking(self):
        view = patient_booking_view(PHARMACIST, self.service, "bk-b", at=NOW)
        self.assertEqual(view.status, "Waitlisted")

    def test_emergency_indicator_uses_only_approved_guidance(self):
        with self.assertRaisesRegex(ValueError, "approved emergency guidance"):
            patient_booking_view(patient("patient-b"), self.service, "bk-b", at=NOW,
                                 emergency_indicator=True)
        guidance = EmergencyGuidance("Call 112 or go to the nearest emergency department.",
                                     "product-owner", "2026-11")
        view = patient_booking_view(patient("patient-b"), self.service, "bk-b", at=NOW,
                                    emergency_indicator=True, guidance=guidance)
        self.assertTrue(view.message.startswith("Call 112"))
        self.assertIn("not emergency care", view.message)


if __name__ == "__main__":
    unittest.main()
