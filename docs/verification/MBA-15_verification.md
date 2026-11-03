# MBA-15 Verification: Concurrency-Safe Inventory Reservation Service

**Date Range:** 2026-10-27 to 2026-11-03  
**Status:** ✅ IMPLEMENTED & VERIFIED

## Acceptance Criteria Verification

| # | Criterion | Implementation | Tests |
|---|-----------|----------------|-------|
| 1 | Read current allocatable quantity, excluding already-reserved, expired, recalled, quarantined, or blocked stock | `Inventory.allocatable_quantity` property returns 0 for non-IN_STOCK states; subtracts reserved_quantity | `test_waitlist.py::WaitlistTests` |
| 2 | Reserve stock with a concurrency-safe transaction/operation and prevent reservations above allocatable quantity | `InMemoryInventoryGateway.reserve()` uses RLock; checks `allocatable_quantity >= quantity` | `test_waitlist.py::test_atomic_inventory_reservation_allows_only_one_last_unit` |
| 3 | Record location, reserved quantity, and batch/expiry details when supplied | `Allocation` dataclass records: `location_id`, `allocated_quantity`, `batch_id`, `batch_expiry_date` | `test_allocation.py::test_full_allocation_records_location_quantity_batch_and_expiry` |
| 4 | Set booking to RESERVED only after a valid reservation succeeds; recover cleanly from failure | `BookingService.allocate()` transitions to RESERVED only after `inventory.reserve()` returns True; retries on conflict | `test_allocation.py::AllocationTests` |
| 5 | Concurrency tests prove two requests cannot reserve the same last unit | `test_atomic_inventory_reservation_allows_only_one_last_unit` uses threads to verify | `test_waitlist.py::test_atomic_inventory_reservation_allows_only_one_last_unit` |

## Key Implementation Files

- `src/medicine_booking/waitlist.py` - Lines 54-130: InMemoryInventoryGateway with RLock
- `src/medicine_booking/booking.py` - Lines 289-346: BookingService.allocate with retry logic
- `src/medicine_booking/domain.py` - Inventory.allocatable_quantity, Allocation dataclass
- `tests/test_waitlist.py` - WaitlistTests
- `tests/test_allocation.py` - AllocationTests

## Requirements Traceability

- REQ-AW-01 → COVERED
- REQ-AW-02 → COVERED
- REQ-AW-03 → COVERED
- REQ-AW-04 → COVERED
- REQ-AW-05 → COVERED
- REQ-AW-06 → COVERED
- REQ-AW-07 → COVERED
- REQ-AW-08 → COVERED
- REQ-AW-09 → COVERED
- NFR-P-04 → COVERED
- NFR-QA-01 → COVERED
- BR-03 → COVERED
- BR-04 → COVERED

## Verification Date

2026-11-03