from datetime import date, datetime, timezone
import unittest

from medicine_booking import (
    ActorRole,
    Booking,
    BookingStatus,
    ContinuityStatus,
    Inventory,
    InventoryState,
    MedicationRequest,
    MedicationRequestStatus,
    Prescription,
    PrescriptionSource,
    PrescriptionStatus,
    PriorityContext,
    PriorityPolicy,
    check_allocation_safety,
    order_bookings,
)


NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def allocation_fixture(*, request_status=MedicationRequestStatus.ELIGIBLE):
    prescription = Prescription(
        "rx-1", "patient-1", PrescriptionSource.STRUCTURED_IMPORT,
        PrescriptionStatus.VALID, created_at=NOW,
    )
    request = MedicationRequest(
        "med-1", "rx-1", 2, medicine_code="MED-1",
        request_status=request_status,
    )
    booking = Booking(
        "booking-1", "patient-1", "rx-1", "med-1", 2,
        booking_state=BookingStatus.ELIGIBLE, created_at=NOW, updated_at=NOW,
    )
    inventory = Inventory(
        "stock-1", "MED-1", "branch-1", 5, 1,
        InventoryState.IN_STOCK, expiry_date=date(2027, 1, 1), updated_at=NOW,
    )
    return booking, prescription, request, inventory


class AllocationSafetyTests(unittest.TestCase):
    def test_allows_eligible_matching_request_with_available_stock(self):
        decision = check_allocation_safety(*allocation_fixture(), at=NOW)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.allocatable_quantity, 4)

    def test_rejects_pending_medication_request(self):
        decision = check_allocation_safety(
            *allocation_fixture(request_status=MedicationRequestStatus.PENDING), at=NOW
        )
        self.assertFalse(decision.allowed)
        self.assertIn("MEDICATION_REQUEST_NOT_ELIGIBLE", decision.reason_codes)

    def test_rejects_quantity_above_prescription_request(self):
        decision = check_allocation_safety(
            *allocation_fixture(), quantity_to_allocate=3, at=NOW
        )
        self.assertFalse(decision.allowed)
        self.assertIn("ALLOCATION_EXCEEDS_REQUESTED_QUANTITY", decision.reason_codes)

    def test_rejects_medicine_code_mismatch(self):
        booking, prescription, request, inventory = allocation_fixture()
        inventory = Inventory(
            "stock-1", "OTHER", "branch-1", 5, 1,
            InventoryState.IN_STOCK, updated_at=NOW,
        )
        decision = check_allocation_safety(booking, prescription, request, inventory, at=NOW)
        self.assertFalse(decision.allowed)
        self.assertIn("MEDICINE_CODE_MISMATCH", decision.reason_codes)


class PriorityTests(unittest.TestCase):
    def test_orders_by_policy_then_waiting_time(self):
        older = Booking(
            "booking-a", "patient-a", "rx-a", "med-a", 1,
            booking_state=BookingStatus.ELIGIBLE, created_at=NOW, updated_at=NOW,
        )
        newer = Booking(
            "booking-b", "patient-b", "rx-b", "med-b", 1,
            booking_state=BookingStatus.WAITLISTED, created_at=NOW, updated_at=NOW,
        )
        contexts = {
            "booking-a": PriorityContext(
                "booking-a", "NORMAL", "pharmacist", ContinuityStatus.UNKNOWN,
                datetime(2026, 11, 1, tzinfo=timezone.utc), ActorRole.PHARMACIST,
                "v1", evaluated_at=NOW,
            ),
            "booking-b": PriorityContext(
                "booking-b", "NORMAL", "pharmacist", ContinuityStatus.UNKNOWN,
                datetime(2026, 11, 2, 11, tzinfo=timezone.utc), ActorRole.PHARMACIST,
                "v1", evaluated_at=NOW,
            ),
        }
        policy = PriorityPolicy("v1", {"NORMAL": 1}, {ContinuityStatus.UNKNOWN: 0})
        result = order_bookings([newer, older], contexts, policy, evaluated_at=NOW)
        self.assertEqual([item.booking_id for item in result], ["booking-a", "booking-b"])


if __name__ == "__main__":
    unittest.main()
