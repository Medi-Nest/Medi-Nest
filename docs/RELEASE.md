# Prototype Release Notes

Priority-Based Medicine Booking and Allocation Module, prototype release for
review. This file covers setup, how to run the demo, test results, the
requirement coverage summary, and known limitations.

## Setup

Requires Python 3.10 or newer. The module has no runtime dependencies.

```bash
git clone https://github.com/Medi-Nest/Medi-Nest.git
cd Medi-Nest
python -m pip install pytest
pytest
python examples/demo.py
```

Without pytest: `PYTHONPATH=src:tests python -m unittest discover -s tests`.

## Deployment notes

The package is a library that a pharmacy application embeds. To deploy it:

1. **Authentication.** Implement `contracts.AuthProvider` against the identity
   provider and build each request's `Principal` from the verified session.
   Never take roles or linked patients from request data.
2. **Storage.** Replace the in-memory dictionaries with a database. Persist
   bookings, allocations, waitlist entries, restock event IDs, reservation
   keys, consent records, notification records, and audit events. Use
   parameterized queries for every operation (NFR-SEC-09) and enable
   encryption at rest (NFR-SEC-07).
3. **Inventory.** Implement `InventoryGateway` with a database transaction
   that checks and reserves stock atomically and is idempotent by reservation
   key.
4. **External services.** Implement the prescription, patient-context, and
   notification interfaces in `contracts.py`. Keep credentials in environment
   variables or a secret store; `.env` and key files are git-ignored
   (NFR-SEC-14).
5. **Transport.** Serve the host API over HTTPS/TLS only (NFR-SEC-01).
6. **Scheduled jobs.** Run `BookingService.expire_reservations` and
   `NotificationService.retry_due` every minute.
7. **Errors.** Show users `access.public_error_message(...)`, never raw
   exceptions (NFR-SEC-15).

## Demo flow

`python examples/demo.py` runs this story with test doubles in place of real
services:

1. Three patients each import a prescription for 2 units of insulin; only 2
   units are in stock.
2. Asha has continuity of therapy on record (with consent) and is reserved;
   Bala and Chitra are waitlisted.
3. A pharmacist verifies Chitra's request as urgent.
4. A restock of 2 units goes to Chitra first.
5. Asha's reservation expires after 30 minutes; the stock goes to Bala.
6. Chitra cancels.
7. Bala's app shows "Medicine Reserved" with a collection deadline, and her
   text messages never mention the medicine.
8. The manager overview shows active bookings, expiries, cancellations,
   notification delivery, and the audit trail.

## Test results

Run on 15 Nov 2026 with Python 3.13 (also passes on 3.11 and 3.12):

| Suite | Tests |
|---|---|
| Full suite (`pytest`) | 141 passed, 67 subtests passed, 0 failed |
| End-to-end scenarios (`tests/test_end_to_end.py`) | 7 passed |
| Traceability check (`python tools/traceability.py --check`) | 0 problems |

Performance smoke test at prototype scale (300 bookings, in-memory adapters,
external latency excluded as the SRS allows):

| Target | Requirement | Measured |
|---|---|---|
| 95% of status reads under 1 s | NFR-P-01 | p95 under 0.01 ms |
| 95% of submissions under 2 s | NFR-P-02 | p95 about 2 ms (submit, context lookup, allocate, notify) |
| Restock re-evaluates the waitlist within 3 s | NFR-P-03 | about 0.1 s for 300 waitlisted bookings |

## Requirement coverage

`docs/TRACEABILITY.md` maps all 161 SRS requirements to code and tests.
`tests/test_traceability.py` fails if any requirement is missing or points to
a test that does not exist.

- **154 covered** by automated tests.
- **3 partly covered** (REQ-PB-17, NFR-QA-13, NFR-QA-14): the module provides
  the priority reason codes and override markers; drawing them on a
  pharmacist screen is the host UI's job.
- **4 excluded** (NFR-SEC-01, 06, 07, 09): HTTPS, password storage,
  encryption at rest, and parameterized queries depend on the deployment, not
  this library. See the deployment notes above.

No release-blocking defects are open.

## Known limitations

- All state is in memory and is lost when the process stops.
- Marking a booking ready or fulfilled (consuming reserved stock) is not
  implemented, so fulfilment metrics stay at zero.
- There is no user interface; patient and manager views return data for a
  host UI to render.
- Notifications are sent while the booking lock is held. The send timeout
  bounds the delay; production should queue messages instead.
- A pharmacist sets verified urgency by writing a `PriorityContext`; there is
  no dedicated urgency-confirmation function yet.
- With partial allocation on, the unfilled remainder is reported but not
  waitlisted automatically; the patient makes a new booking for it.
- `call_external` cannot stop a hung call; it stops waiting and discards the
  result.
