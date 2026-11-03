"""MBA-21: enforce safety gates, override authorization, and patient copy."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from medicine_booking import (
    ActorRole, Booking, BookingStatus, ContinuityStatus, EmergencyGuidance,
    InMemoryInventoryGateway, Inventory, InventoryState, ManualReview,
    MedicationRequest, MedicationRequestStatus, PharmacistPrincipal,
    Prescription, PrescriptionSource, PrescriptionStatus, PriorityContext,
    PriorityPolicy, PriorityResult, RestockEvent, SafetyPolicy, WaitlistService,
    check_allocation_safety, override_priority, patient_status_message,
)


NOW = datetime(2026, 11, 3, 13, tzinfo=timezone.utc)


def pharmacist(*, authenticated=True, permissions=frozenset({
    "prescription:review", "allocation:override",
}), role=ActorRole.PHARMACIST):
    return PharmacistPrincipal("pharmacist-1", role, authenticated, permissions)


def allocation():
    booking = Booking("booking-1", "patient-1", "rx-1", "request-1", 1,
                      booking_state=BookingStatus.ELIGIBLE,
                      created_at=NOW - timedelta(days=1), updated_at=NOW)
    prescription = Prescription("rx-1", "patient-1", PrescriptionSource.STRUCTURED_IMPORT,
                                PrescriptionStatus.VALID, created_at=NOW)
    request = MedicationRequest("request-1", "rx-1", 1, medicine_code="MED-1",
                                request_status=MedicationRequestStatus.ELIGIBLE)
    stock = Inventory("stock-1", "MED-1", "branch-1", 2, 0,
                      InventoryState.IN_STOCK, updated_at=NOW)
    return booking, prescription, request, stock


def waitlist_service():
    booking, prescription, request, stock = allocation()
    booking = replace(booking, updated_at=NOW - timedelta(minutes=1))
    stock = replace(stock, on_hand_quantity=0)
    context = PriorityContext(
        booking.booking_id, "NORMAL", "pharmacist", ContinuityStatus.UNKNOWN,
        NOW - timedelta(hours=1), ActorRole.PHARMACIST, "v1",
        evaluated_at=NOW, urgency_actor_id="pharmacist-1", urgency_verified_at=NOW,
    )
    policy = PriorityPolicy("v1", {"NORMAL": 1}, {ContinuityStatus.UNKNOWN: 0})
    gateway = InMemoryInventoryGateway({("MED-1", "branch-1"): stock})
    service = WaitlistService(
        bookings={booking.booking_id: booking},
        prescriptions={prescription.prescription_id: prescription},
        medication_requests={request.medication_request_id: request},
        contexts={booking.booking_id: context}, inventory=gateway,
        policy=policy, safety_policy=SafetyPolicy(frozenset({"MED-1"})),
        reservation_duration=timedelta(minutes=10),
    )
    return service, gateway


class SafetyRuleTests(unittest.TestCase):
    def test_invalid_or_expired_prescriptions_cannot_be_allocated_even_after_review(self):
        booking, prescription, request, stock = allocation()
        review = ManualReview("booking-1", "MED-1", pharmacist(), NOW)
        for status in (PrescriptionStatus.INVALID, PrescriptionStatus.EXPIRED,
                       PrescriptionStatus.MANUAL_REVIEW_REQUIRED):
            with self.subTest(status=status):
                result = check_allocation_safety(
                    booking, replace(prescription, status=status), request, stock,
                    policy=SafetyPolicy(frozenset({"MED-1"})), review=review, at=NOW,
                )
                self.assertFalse(result.allowed)
                self.assertIn("PRESCRIPTION_NOT_VALID", result.reason_codes)

    def test_restricted_review_requires_scoped_authenticated_pharmacist(self):
        booking, prescription, request, stock = allocation()
        rules = SafetyPolicy(frozenset({"MED-1"}))
        unreviewed = check_allocation_safety(
            booking, prescription, request, stock, policy=rules,
            manual_review_completed=True, at=NOW,
        )
        self.assertIn("RESTRICTED_MEDICINE_REQUIRES_REVIEW", unreviewed.reason_codes)
        for principal in (
            pharmacist(authenticated=False),
            pharmacist(role=ActorRole.PATIENT),
            pharmacist(permissions=frozenset({"allocation:override"})),
        ):
            with self.subTest(principal=principal), self.assertRaises(PermissionError):
                ManualReview("booking-1", "MED-1", principal, NOW)
        wrong_booking = ManualReview("other", "MED-1", pharmacist(), NOW)
        self.assertFalse(check_allocation_safety(
            booking, prescription, request, stock, policy=rules,
            review=wrong_booking, at=NOW,
        ).allowed)
        review = ManualReview("booking-1", "MED-1", pharmacist(), NOW)
        self.assertTrue(check_allocation_safety(
            booking, prescription, request, stock, policy=rules,
            review=review, at=NOW,
        ).allowed)
        excluded = SafetyPolicy(frozenset({"MED-1"}), frozenset({"MED-1"}))
        self.assertIn("MEDICINE_EXCLUDED_FROM_AUTOMATIC_ALLOCATION",
                      check_allocation_safety(
                          booking, prescription, request, stock, policy=excluded,
                          review=review, at=NOW,
                      ).reason_codes)
        category_rules = SafetyPolicy(
            restricted_categories=frozenset({"controlled"}),
            medicine_categories={"MED-1": "controlled"},
        )
        self.assertIn("RESTRICTED_MEDICINE_REQUIRES_REVIEW",
                      check_allocation_safety(
                          booking, prescription, request, stock,
                          policy=category_rules, at=NOW,
                      ).reason_codes)
        self.assertTrue(check_allocation_safety(
            booking, prescription, request, stock, policy=category_rules,
            review=review, at=NOW,
        ).allowed)
        unknown_category = SafetyPolicy(restricted_categories=frozenset({"controlled"}))
        self.assertIn("MEDICINE_CATEGORY_UNKNOWN", check_allocation_safety(
            booking, prescription, request, stock, policy=unknown_category, at=NOW,
        ).reason_codes)

    def test_blocked_stock_and_medicine_substitution_always_fail(self):
        booking, prescription, request, stock = allocation()
        for state in (InventoryState.EXPIRED, InventoryState.RECALLED,
                      InventoryState.QUARANTINED, InventoryState.BLOCKED):
            with self.subTest(state=state):
                decision = check_allocation_safety(
                    booking, prescription, request,
                    replace(stock, inventory_state=state), at=NOW,
                )
                self.assertIn("INVENTORY_NOT_ALLOCATABLE", decision.reason_codes)
        expired_batch = replace(stock, expiry_date=(NOW - timedelta(days=1)).date())
        self.assertIn("INVENTORY_EXPIRED", check_allocation_safety(
            booking, prescription, request, expired_batch, at=NOW,
        ).reason_codes)
        substituted = replace(stock, medicine_code="OTHER-MEDICINE")
        self.assertIn("MEDICINE_CODE_MISMATCH", check_allocation_safety(
            booking, prescription, request, substituted, at=NOW,
        ).reason_codes)

    def test_emergency_indicator_uses_only_product_owner_guidance(self):
        with self.assertRaisesRegex(ValueError, "product-owner-approved"):
            patient_status_message(BookingStatus.WAITLISTED, emergency_indicator=True)
        guidance = EmergencyGuidance("Follow the approved emergency-care instructions.",
                                     "product-owner", "v1")
        message = patient_status_message(
            BookingStatus.WAITLISTED, emergency_indicator=True, guidance=guidance,
        )
        self.assertIn(guidance.text, message)
        self.assertIn("not emergency care", message)
        self.assertNotIn("stock becomes available", message)
        ordinary = patient_status_message(BookingStatus.WAITLISTED)
        self.assertIn("waitlist", ordinary)
        with self.assertRaises(TypeError):
            patient_status_message(BookingStatus.WAITLISTED, patient_free_text="urgent")

    def test_override_requires_authorization_reason_and_audit(self):
        original = PriorityResult("booking-1", 2, "v1", ("CONTINUITY:UNKNOWN",), NOW)
        for principal in (
            pharmacist(authenticated=False),
            pharmacist(role=ActorRole.PATIENT),
            pharmacist(permissions=frozenset({"prescription:review"})),
        ):
            with self.subTest(principal=principal), self.assertRaises(PermissionError):
                override_priority(original, 0, principal, "Reviewed need", at=NOW)
        with self.assertRaisesRegex(ValueError, "reason"):
            override_priority(original, 0, pharmacist(), "  ", at=NOW)
        record = override_priority(original, 0, pharmacist(), "Reviewed need",
                                   at=NOW + timedelta(minutes=1))
        self.assertEqual((record.previous.rank, record.current.rank), (2, 0))
        self.assertIn("PHARMACIST_OVERRIDE", record.current.reason_codes)
        self.assertEqual(record.audit_event.actor_id, "pharmacist-1")
        self.assertEqual(record.audit_event.reason, "Reviewed need")
        self.assertEqual(record.audit_event.previous_state["rank"], 2)
        self.assertEqual(record.audit_event.new_state["rank"], 0)

    def test_waitlist_review_and_expiry_release_respect_safety_policy(self):
        service, gateway = waitlist_service()
        with self.assertRaisesRegex(ValueError, "RESTRICTED_MEDICINE_REQUIRES_REVIEW"):
            service.add_unfilled("booking-1", "MED-1", "branch-1", at=NOW)
        service.approve_manual_review("booking-1", "MED-1", pharmacist(), at=NOW)
        service.add_unfilled("booking-1", "MED-1", "branch-1", at=NOW)
        event = RestockEvent("restock-1", "MED-1", "branch-1", 1,
                             NOW + timedelta(minutes=1))
        promoted_at = NOW + timedelta(minutes=2)
        self.assertEqual(service.process_restock(event, at=promoted_at).promoted_booking_ids,
                         ("booking-1",))
        self.assertEqual(service.expire_reservations(at=promoted_at + timedelta(minutes=9)), ())
        self.assertEqual(service.expire_reservations(at=promoted_at + timedelta(minutes=10)),
                         ("booking-1",))
        self.assertEqual(gateway.read("MED-1", "branch-1").reserved_quantity, 0)
        self.assertEqual(service.bookings["booking-1"].booking_state, BookingStatus.EXPIRED)
        self.assertIn("STOCK_RELEASED", [item.event_type for item in service.audit_events])
        self.assertEqual(service.expire_reservations(at=promoted_at + timedelta(minutes=11)), ())


if __name__ == "__main__":
    unittest.main()
