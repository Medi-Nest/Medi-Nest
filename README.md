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

## Running the tests

```bash
pip install pytest
pytest
```

`pyproject.toml` puts `src/` on the path, so no `PYTHONPATH` is needed.
Without pytest: `PYTHONPATH=src:tests python -m unittest discover -s tests`.

## Access control (MBA-9)

`medicine_booking.access` holds the role-to-permission table. The host
application authenticates the user and builds a `Principal` from its verified
session, including the patients a patient or caregiver may act for. Services
call `principal.require(...)` and `authorize_patient_record(...)` before
touching data, so a known booking ID does not expose another patient's
record. Missing and foreign records fail with the same `AccessDenied` error.
`validate_upload` checks prescription file type, size, and leading bytes, and
`public_error_message` turns exceptions into text that is safe to show users.
Credentials belong in environment variables; `.env` files are git-ignored.

## Booking, allocation, cancellation, and expiry (MBA-12, MBA-17)

`BookingService` wraps a `WaitlistService` and shares its state, lock, and
audit trail:

1. `submit` creates a booking linked to patient, prescription, and medication
   request. It rejects bad quantities and medicines that are not on the
   prescription, rejects or flags duplicate active bookings (`BookingPolicy`),
   rejects invalid or expired prescriptions with a recorded reason, and holds
   bookings whose prescription is still under review.
2. `recheck_eligibility` lets a pharmacist release a held booking after review.
3. `allocate` re-reads stock and reserves it atomically. With too little stock
   it waitlists the booking, or reserves what is available when
   `partial_allocation` is on. The allocated and remaining quantities are
   stored separately, and the remainder is not waitlisted under the same
   booking, so no unit is counted twice.
4. `cancel` and `expire_reservations` release stock and offer it to the
   waitlist in priority order.

## Patient views (MBA-14)

`medicine_booking.patient_views` returns plain data for a UI or API to render.
`patient_booking_view` and `patient_bookings` show plain-language status,
reserved and outstanding quantity, reservation expiry, and whether the booking
can be cancelled. They never include rank, urgency, or other patients' data.

## Manager overview (MBA-20)

`medicine_booking.manager_views` gives managers active bookings, waitlist
sizes, allocation history, overrides, expiry events, fulfilment metrics, and
notification status through `manager_overview` and `audit_history`, with
patient account IDs masked.

## External service contracts (MBA-8)

`medicine_booking.contracts` defines the interface for each external system:
prescriptions (`parse_prescription_payload`, `to_internal`,
`import_prescription`), patient context (`ContextRequest`/`ContextResponse`),
inventory (`InventoryGateway`), authentication (`AuthProvider`), and
notifications (`NotificationRequest`/`DeliveryReceipt`). `call_external` adds a
configurable timeout and retries for transient failures, and turns unexpected
errors into `ExternalServiceError` without leaking remote error text.
Deterministic doubles (`MockPrescriptionSource`, `MockPatientContextSource`,
`MockAuthProvider`, `MockNotificationSender`, `FaultyInventoryGateway`) replace
real services in tests and demos.

### Known limits

- State is in memory. A deployment must persist bookings, allocations,
  waitlist entries, and audit events, and implement `InventoryGateway` with a
  real database transaction using parameterized queries.
- Marking a booking ready or fulfilled (consuming reserved stock) is not
  implemented yet, so fulfilment metrics stay at zero until it is.
