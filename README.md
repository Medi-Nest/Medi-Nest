# Medicine-Booking-Application
Priority-Based Medicine Booking and Allocation Module for Pharma Apps - A module for pharmacy apps that prioritizes medicine bookings based on urgency, availability, and patient history. Include waitlist management, notifications, and integration with e-prescriptions.

## Waitlist workflow prototype

The `medicine_booking.waitlist` module provides `WaitlistService` for an
eligible booking that cannot be filled. Call `add_unfilled` after the allocation
check, then pass each inventory increase to `process_restock` with a stable
restock event ID. The service uses the current priority policy, rereads stock
and eligibility before each reservation, and records audit events. Cancelling a
promoted booking releases its reservation and reconsiders the remaining entries.

`InMemoryInventoryGateway` provides atomic, idempotent reservations for the
prototype. An application deployment must persist waitlist state, event IDs,
reservation keys, and audit events, and implement the `InventoryGateway`
contract with an atomic inventory transaction. The notification callback runs
after reservation and cannot roll it back.
