# Verification Report

**Result:** PASSED

- Completed: `2026-11-16T21:35:31+05:30` (Asia/Kolkata).
- Source baseline: `1cc7c7c029efa9dd4d0956f169c8e8410b06d8f0` on `refactor/booking-platform`.
- Source fingerprint (SHA-256; normalized text): `d835cabb0b09ff42ce38d00b7740ce6b2fb95423a14af343881812583f9257ec`.
- Environment: Python 3.11.9, Windows, SQLite 3.45.1.
- Application tests: 44; passed: 44; failed/errors: 0; skipped: 0.

The source baseline is the application commit at execution time. This report and its verification tooling are committed afterward; the fingerprint identifies the actual input files.

## Automated checks

| Check | Result | Duration (s) |
|---|---|---:|
| Python compilation | PASSED | 0.146 |
| Git whitespace validation | PASSED | 0.034 |
| JavaScript syntax | PASSED | 0.062 |
| Python line coverage | PASSED | 14.182 |

## Response-time measurements

| Operation | Observed (ms) | SRS target (ms) |
|---|---:|---:|
| Status read, p95 | 31.9 | 1000 |
| Booking submission, p95 | 43.4 | 2000 |
| Restock and queue processing | 60.6 | 3000 |

These measurements come from the uninstrumented performance test: 30 authenticated status reads, 20 sequential submissions, and one restock over the resulting small synthetic dataset. The p95 calculation uses nearest rank: ceil(0.95 * sample count). They establish local prototype behavior, not production capacity.

## Python line coverage

A separate traced run executed 43 non-performance tests. HTTP and allocation worker threads were included. Percentages are the standard-library tracer's rounded per-module line coverage; JavaScript coverage is not measured. A separate coverage.py run below measures Python statement and branch coverage.

| Module | Executable lines | Line coverage (%) |
|---|---:|---:|
| `medicine_booking.access` | 59 | 94 |
| `medicine_booking.booking` | 434 | 91 |
| `medicine_booking.domain` | 32 | 90 |
| `medicine_booking.notifications` | 45 | 95 |
| `medicine_booking.priority` | 15 | 100 |
| `medicine_booking.seed` | 44 | 97 |
| `medicine_booking.server` | 212 | 80 |
| `medicine_booking.storage` | 30 | 100 |

## Test-case results

| Test case | Result | Duration (s) |
|---|---|---:|
| `test_booking.BookingTests.test_additional_partial_reservation_creates_an_update` | PASSED | 0.144 |
| `test_booking.BookingTests.test_cancellation_releases_stock_and_promotes_next_request` | PASSED | 0.149 |
| `test_booking.BookingTests.test_concurrent_last_unit_is_reserved_once` | PASSED | 0.142 |
| `test_booking.BookingTests.test_duplicate_and_excess_quantity_are_rejected` | PASSED | 0.144 |
| `test_booking.BookingTests.test_expiry_releases_reserved_stock_and_promotes_waitlist` | PASSED | 0.157 |
| `test_booking.BookingTests.test_failed_transaction_leaves_no_orphan_reservation` | PASSED | 0.108 |
| `test_booking.BookingTests.test_fulfilment_consumes_inventory_and_prevents_overprescription` | PASSED | 0.153 |
| `test_booking.BookingTests.test_full_allocation_records_reservation_batch_location_and_expiry` | PASSED | 0.112 |
| `test_booking.BookingTests.test_invalid_pending_and_expired_prescriptions_cannot_allocate` | PASSED | 0.173 |
| `test_booking.BookingTests.test_invalidated_prescription_cannot_be_promoted_or_keep_reservation` | PASSED | 0.127 |
| `test_booking.BookingTests.test_lifecycle_notifications_are_scoped_and_timestamped` | PASSED | 0.138 |
| `test_booking.BookingTests.test_notification_failure_does_not_undo_reservation_and_can_retry` | PASSED | 0.145 |
| `test_booking.BookingTests.test_notification_templates_are_configurable` | PASSED | 0.087 |
| `test_booking.BookingTests.test_only_pharmacists_can_review_or_confirm_urgency` | PASSED | 0.083 |
| `test_booking.BookingTests.test_override_requires_pharmacist_reason_and_preserves_audit` | PASSED | 0.149 |
| `test_booking.BookingTests.test_partial_allocation_tracks_remaining_quantity_until_full_reservation` | PASSED | 0.138 |
| `test_booking.BookingTests.test_partial_disabled_never_reserves_insufficient_stock` | PASSED | 0.105 |
| `test_booking.BookingTests.test_partial_reservation_expiry_releases_quantity_and_closes_waitlist` | PASSED | 0.144 |
| `test_booking.BookingTests.test_password_hashes_sessions_and_expiry` | PASSED | 0.945 |
| `test_booking.BookingTests.test_patient_ownership_and_role_boundaries` | PASSED | 0.111 |
| `test_booking.BookingTests.test_prescription_intake_preserves_source_consent_links_and_validation_history` | PASSED | 0.072 |
| `test_booking.BookingTests.test_priority_ties_use_continuity_waiting_time_and_identifier_only` | PASSED | 0.323 |
| `test_booking.BookingTests.test_recall_releases_existing_reservation_and_requeues_booking` | PASSED | 0.122 |
| `test_booking.BookingTests.test_recalled_quarantined_expired_and_blocked_batches_are_excluded` | PASSED | 0.208 |
| `test_booking.BookingTests.test_restock_replay_is_idempotent_and_conflicting_replay_is_rejected` | PASSED | 0.113 |
| `test_booking.BookingTests.test_restricted_and_excluded_medicines_require_safety_review` | PASSED | 0.187 |
| `test_booking.BookingTests.test_review_change_re_evaluates_existing_waitlist` | PASSED | 0.160 |
| `test_booking.BookingTests.test_state_survives_reopening_database` | PASSED | 0.085 |
| `test_booking.BookingTests.test_structured_schema_quantity_and_duplicate_source_are_validated` | PASSED | 0.064 |
| `test_booking.BookingTests.test_upload_constraints_and_document_access` | PASSED | 0.106 |
| `test_booking.BookingTests.test_waitlist_uses_verified_urgency_continuity_and_current_policy` | PASSED | 0.137 |
| `test_performance.PerformanceTests.test_response_time_targets` | PASSED | 2.766 |
| `test_requirements.RequirementTests.test_functional_requirements_have_valid_test_references` | PASSED | 0.050 |
| `test_server.ServerTests.test_authentication_csrf_and_logout` | PASSED | 0.352 |
| `test_server.ServerTests.test_cross_origin_and_invalid_host_requests_are_blocked` | PASSED | 0.003 |
| `test_server.ServerTests.test_emergency_guidance_requires_configured_version` | PASSED | 0.006 |
| `test_server.ServerTests.test_http_booking_workflow` | PASSED | 0.687 |
| `test_server.ServerTests.test_malformed_json_is_rejected_before_authentication` | PASSED | 0.002 |
| `test_server.ServerTests.test_malformed_requests_return_safe_errors` | PASSED | 0.275 |
| `test_server.ServerTests.test_patient_cannot_call_staff_endpoints_or_access_other_records` | PASSED | 0.550 |
| `test_server.ServerTests.test_registration_assigns_patient_role` | PASSED | 0.572 |
| `test_server.ServerTests.test_server_refuses_an_in_use_port` | PASSED | 0.000 |
| `test_server.ServerTests.test_static_application_and_security_headers` | PASSED | 0.140 |
| `test_server.ServerTests.test_unexpected_service_error_returns_safe_message` | PASSED | 0.298 |

## Evidence and limits

- Reproduce with `python scripts/verify.py --coverage --report docs/TEST_REPORT.md`.
- Detailed results, JUnit XML, test output, and coverage files are generated under `build/verification/` and are excluded from Git. CI is configured to retain them as run artifacts.
- Test databases contain synthetic fixtures and are isolated from the running application's data.
- External provider failure is simulated; no real pharmacy, health-record, SMS, or email integration was exercised.
- Desktop browser checks were recorded during implementation. Full mobile/accessibility review and user acceptance remain pending.
- CI execution and branch publication require repository write access; this local run does not prove that GitHub CI has executed.

## Python statement and branch coverage

A separate coverage.py 7.17.3 run passed all 43 non-performance tests using branch measurement, thread coverage and source scope `src/medicine_booking`.

- Statement coverage: 717/803 (89.3%).
- Branch destinations covered: 214/272 (78.7%).
- The coverage.py combined percentage is not used as the statement percentage. Its executable-statement accounting differs from the standard-library line tracer above.
- JavaScript coverage, live-provider validation and formal user acceptance are not included.

| Module | Statements covered / total | Statement coverage | Branches covered / total | Branch coverage |
|---|---:|---:|---:|---:|
| `access.py` | 52/55 | 94.5% | 11/14 | 78.6% |
| `booking.py` | 371/406 | 91.4% | 132/168 | 78.6% |
| `domain.py` | 28/31 | 90.3% | 7/8 | 87.5% |
| `notifications.py` | 31/33 | 93.9% | 4/6 | 66.7% |
| `priority.py` | 11/11 | 100.0% | 2/2 | 100.0% |
| `seed.py` | 28/29 | 96.6% | 11/12 | 91.7% |
| `server.py` | 167/209 | 79.9% | 47/62 | 75.8% |
| `storage.py` | 29/29 | 100.0% | 0/0 | N/A |

Raw JSON, XML, HTML and test output are under `build/final-verification/branch-coverage/`. A compact versioned extract is retained in `docs/evidence/final-verification.json`.

Equivalent suite selection after installing coverage.py 7.17.3:

```powershell
python -m coverage run --branch --concurrency=thread --source=src/medicine_booking -m unittest discover -s tests -p "test_[brs]*.py"
python -m coverage json -o coverage.json
python -m coverage xml -o coverage.xml
python -m coverage html
```
