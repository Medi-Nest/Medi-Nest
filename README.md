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

## Safety boundaries

`SafetyPolicy` configures restricted and excluded medicine codes or categories. Restricted
requests need a matching `ManualReview` from a server-verified pharmacist
principal before allocation; invalid prescriptions and unusable stock remain
blocked. A priority override requires the `allocation:override` permission and
a reason, produces an audit event, and cannot bypass allocation safety checks.

The patient status helper accepts an emergency indicator supplied by the host
application and requires versioned, product-owner-approved emergency guidance.
It does not interpret patient text, diagnose, prescribe, or suggest a substitute
medicine. The host application must authenticate principals and load approved
guidance from its trusted configuration; this module does not provide login or
clinical review services.
