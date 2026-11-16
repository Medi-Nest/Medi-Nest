# MediNest

A local pharmacy application for prescription review, medicine booking, stock reservation, and ordered waitlists. Requirements are maintained in the original [SRS.md](SRS.md).

## Run locally

Requires **Python 3.11 or later**. There are no third-party runtime dependencies or frontend build steps.

```powershell
python run.py --demo
```

Open **http://127.0.0.1:8080**. The application listens only on the local machine. The `--demo` flag initializes synthetic records only when the database has no users; subsequent starts preserve all changes.

| Account | Email | Password |
|---|---|---|
| Patient | patient@medinest.local | MediNest@2026 |
| Second patient | meera@medinest.local | MediNest@2026 |
| Pharmacist | pharmacist@medinest.local | MediNest@2026 |
| Pharmacy manager | manager@medinest.local | MediNest@2026 |

When sample accounts are enabled, the sign-in page offers workspace account buttons. Patient registration creates patient accounts only. Staff roles cannot be selected during registration.

To use another port or a separate demonstration dataset:

```powershell
python run.py --demo --port 8001 --database data/demo.db
```

Stop the server with `Ctrl+C`. SQLite data, prescription attachments, and sessions are kept under `data/` and excluded from Git. A running database must not be deleted or replaced.

## Workflows

- **Patient:** submit prescribed medicines as a structured record, optionally attach a PDF/PNG/JPEG, wait for pharmacist review, then book an accepted medication request. View reservation deadlines, cancel eligible bookings, and receive private in-app updates.
- **Pharmacist:** review prescriptions, confirm urgency and continuity, explicitly approve restricted categories, inspect priority reasons, record restocks, block batches, override a waiting request with a reason, and mark reservations ready and fulfilled.
- **Manager:** review bookings, inventory, waitlists, audit history, and notification status. Configure reservation duration and partial allocation. Manager accounts cannot perform clinical review or stock allocation actions.

Try restocking **Atorvastatin / MED-003** from the pharmacist inventory page. The synthetic higher-urgency request receives stock before the normal-urgency request. Cancelling a reservation releases its stock and re-evaluates the queue.

## Allocation policy

1. Verify current prescription eligibility and explicit restricted-medicine approval.
2. Order eligible requests by pharmacist override, verified urgency, confirmed therapy continuity, oldest booking timestamp, and booking UUID.
3. Reserve only allocatable stock at the same location, using batches with the earliest expiry first.
4. Maintain an ordered waitlist for unreserved quantities. Restocks, cancellations, reservation expiry, prescription review, and stock-state changes re-evaluate eligibility and ordering.

SQLite `BEGIN IMMEDIATE` transactions commit inventory, reservations, booking states, waitlist changes, audit events, and notification outbox entries together. Quantity constraints and a unique active-request index protect against overselling and duplicate bookings. Restock event identifiers persist across restarts; conflicting replays are rejected. Previously fulfilled quantity counts against the original prescription limit.

Partial allocation is disabled by default. When enabled, a partial reservation retains an active waitlist entry only for its remaining quantity. The full remaining quantity must be reserved before the booking can be marked ready for collection. Reservation expiry releases the outstanding reservation and closes that booking; it does not automatically rebook the same patient.

Background maintenance checks expiry and delivers in-app notifications every 10 seconds. Delivery failures are recorded separately and cannot reverse a reservation. Authorized staff can retry failed deliveries, up to five attempts. The default provider delivers to the persisted in-app inbox; SMS and email are not configured.

`MEDINEST_NOTIFICATION_TEMPLATES` may contain a JSON object overriding status templates, for example `{"WAITLISTED":"Your request is queued. Sign in for updates."}`. Keep configured notification content free of unnecessary health information.

## Structured prescription format

The prescription form can import a JSON file with this structure. The same object is accepted by `POST /api/prescriptions` after authentication. Submitted clinical validation or urgency fields are rejected. All imported prescriptions require pharmacist review.

```json
{
  "source_identifier": "clinic:prescription-001",
  "authored_on": "2026-11-16",
  "expires_on": "2026-12-16",
  "prescriber": "PRESCRIBER-001",
  "consent_reference": "optional-authorization-reference",
  "medicines": [
    { "medicine_code": "MED-002", "quantity": 30 }
  ]
}
```

`patient_id` may be supplied by an authenticated pharmacist; patients may submit only for themselves. An optional `document` object contains `filename` and base64-encoded `content`. Supported attachments are PDF, PNG, and JPEG up to 5 MiB, checked by extension and file signature. Downloads require authenticated ownership or a staff role and are served as attachments. Structural file checks do not establish clinical authenticity.

## API

All mutations use JSON, the `X-Requested-With: MediNest` header, an authenticated session cookie, and the `X-CSRF-Token` returned at sign-in. Sign-in and patient registration do not require an existing session. Cross-origin requests and unexpected hosts are rejected.

| Endpoint | Access / purpose |
|---|---|
| `GET /api/config` | Public application and demonstration configuration |
| `POST /api/auth/login`, `/api/auth/register` | Sign in / create a patient account |
| `POST /api/auth/logout` | End the current session |
| `GET /api/dashboard` | Role-filtered application data |
| `POST /api/prescriptions` | Patient or pharmacist intake |
| `GET /api/prescriptions/{id}/document` | Authorized document download |
| `POST /api/prescriptions/{id}/review` | Pharmacist validation and verified context |
| `POST /api/bookings` | Patient or pharmacist booking |
| `POST /api/bookings/{id}/cancel` | Owner or pharmacist cancellation |
| `POST /api/bookings/{id}/ready`, `/fulfil` | Pharmacist collection workflow |
| `POST /api/bookings/{id}/override` | Pharmacist rank override with reason |
| `POST /api/inventory/restock` | Pharmacist restock with unique `event_id` |
| `POST /api/inventory/{id}/state` | Pharmacist stock-state update with reason |
| `POST /api/settings` | Manager reservation and partial-allocation policy |
| `POST /api/notifications/{id}/retry` | Pharmacist or manager delivery retry |

## Project layout

```text
run.py                       Local entry point
SRS.md                       Original requirements and implementation traceability
src/medicine_booking/
  access.py                  Authentication, session expiry, authorization
  domain.py                  Shared validation, IDs, timestamps
  storage.py                 Schema and transaction boundaries
  priority.py                Deterministic priority policy
  booking.py                 Prescription, booking, inventory application service
  notifications.py           Durable outbox and delivery provider interface
  server.py                  HTTP API and background maintenance
  seed.py                    Synthetic demonstration fixtures
  static/                    HTML, CSS, JavaScript, icon
tests/                       Service, HTTP, and performance tests
```

## Validation

The [engineering record](docs/ENGINEERING.md) covers lifecycle status, architecture, design decisions, the test plan, acceptance activities and open risks. The [verification report](docs/TEST_REPORT.md) records the latest measured local results.

Run the complete evidence-producing verification command:

```powershell
python scripts/verify.py --coverage --report docs/TEST_REPORT.md
```

It creates JUnit XML, structured results, logs and Python line coverage under `build/verification/`, plus the reviewable report. A GitHub Actions workflow is configured to run the checks on Windows and Ubuntu after publication; no CI run is claimed before that happens.

Individual checks remain available:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q src run.py
node --check src/medicine_booking/static/app.js
git diff --check
```

Node is only needed for the optional JavaScript syntax check. Tests use isolated temporary SQLite databases and include concurrent last-unit allocation, transaction rollback, restock replay, cancellation and expiry promotion, partial allocation, unsafe inventory, review and override authorization, patient isolation, notification outages, persistence, and the HTTP collection workflow.

## Integration and deployment scope

This implementation is a local prototype with synthetic demonstration data. It does not diagnose, prescribe, infer urgency from patient text, substitute medicines, or provide emergency care. No external health-record system is connected; continuity is `UNKNOWN` until confirmed by a pharmacist. The structured JSON format is an internal adapter contract, not a complete FHIR or ABDM implementation.

The local HTTP server is not a production internet-facing server. Production deployment still requires a supported application server behind HTTPS, encrypted database/file storage, identity and staff provisioning, backups, configurable retention and consent enforcement for external health records, bounded external-service adapters, and operational monitoring. SQLite does not provide application-managed encryption at rest here. Demonstration credentials must never be deployed with real patient data.

Product-owner-approved emergency guidance may be provided through both `MEDINEST_EMERGENCY_GUIDANCE` and `MEDINEST_EMERGENCY_GUIDANCE_VERSION`. Without both values, the app only states its operational scope; it does not fabricate approved guidance or interpret emergency symptoms.

Branch names use standard prefixes such as `refactor/booking-platform`; commit messages follow Conventional Commits. Dates and stored timestamps use ISO 8601. Existing Git history is preserved, and commit timestamps reflect when changes are actually committed.
