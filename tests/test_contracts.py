"""MBA-8: external service contracts, payload validation, timeouts, and mocks."""

from datetime import datetime, timedelta, timezone
import unittest

from medicine_booking import (
    AccessDenied, ActorRole, BookingStatus, DeliveryStatus, MedicationRequestStatus,
    NotificationChannel, Patient, PrescriptionSource, PrescriptionStatus, Principal,
)
from medicine_booking.contracts import (
    ContextRequest, ExternalServiceError, FaultyInventoryGateway, InvalidPayload,
    MockAuthProvider, MockNotificationSender, MockPatientContextSource,
    MockPrescriptionSource, NotificationRequest, ServiceTimeout, call_external,
    import_prescription, parse_prescription_payload,
)

from booking_support import LOCATION, NOW, SYSTEM, book, build


def payload(**changes):
    data = {
        "source_system": "abdm",
        "source_identifier": "MR-1001",
        "patient_reference": "ABHA-77",
        "status": "active",
        "authored_on": "2026-11-14T10:00:00+05:30",
        "prescriber_reference": "Practitioner/dr-9",
        "lines": [
            {"source_line_id": "1", "medicine_code": "MED-1",
             "medicine_name": "Insulin glargine", "quantity": 2},
            {"source_line_id": "2", "medicine_name": "Metformin 500 mg", "quantity": 30,
             "dosage_text": "1 tablet twice daily"},
        ],
    }
    data.update(changes)
    return data


PATIENT = Patient("patient-a", "A. Patient", external_patient_id="ABHA-77")


class PrescriptionContractTests(unittest.TestCase):
    def test_valid_payload_maps_to_linked_internal_records(self):
        source = MockPrescriptionSource({"MR-1001": payload()})
        record = import_prescription(source, "MR-1001", PATIENT, received_at=NOW)
        rx = record.prescription
        self.assertEqual((rx.patient_id, rx.source, rx.status, rx.source_identifier,
                          rx.prescriber_reference),
                         ("patient-a", PrescriptionSource.STRUCTURED_IMPORT,
                          PrescriptionStatus.VALID, "abdm:MR-1001", "Practitioner/dr-9"))
        self.assertEqual(rx.authored_on, datetime(2026, 11, 14, 4, 30, tzinfo=timezone.utc))
        self.assertEqual([r.requested_quantity for r in record.medication_requests], [2, 30])
        self.assertTrue(all(r.prescription_id == rx.prescription_id
                            for r in record.medication_requests))
        self.assertTrue(all(r.request_status is MedicationRequestStatus.ELIGIBLE
                            for r in record.medication_requests))

    def test_external_status_maps_to_review_or_rejection(self):
        for external, internal in (("on-hold", PrescriptionStatus.MANUAL_REVIEW_REQUIRED),
                                   ("completed", PrescriptionStatus.EXPIRED),
                                   ("entered-in-error", PrescriptionStatus.INVALID)):
            with self.subTest(external=external):
                source = MockPrescriptionSource({"MR-1001": payload(status=external)})
                record = import_prescription(source, "MR-1001", PATIENT, received_at=NOW)
                self.assertIs(record.prescription.status, internal)
                self.assertTrue(all(r.request_status is MedicationRequestStatus.PENDING
                                    for r in record.medication_requests))

    def test_schema_violations_are_rejected_with_reasons(self):
        cases = [
            (payload(lines=[]), "LINES_REQUIRED"),
            (payload(status="paused"), "STATUS_NOT_SUPPORTED"),
            (payload(authored_on="yesterday"), "AUTHORED_ON_INVALID"),
            (payload(authored_on="2026-12-01T00:00:00+00:00"), "AUTHORED_ON_IN_FUTURE"),
            (payload(diagnosis="F32"), "UNEXPECTED_FIELD"),
            (payload(lines=[{"source_line_id": "1", "quantity": 0, "medicine_code": "X"}]),
             "LINE_QUANTITY_INVALID"),
            (payload(lines=[{"source_line_id": "1", "quantity": 1}]), "LINE_MEDICINE_REQUIRED"),
            ({k: v for k, v in payload().items() if k != "patient_reference"},
             "PATIENT_REFERENCE_REQUIRED"),
        ]
        for data, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(InvalidPayload) as ctx:
                    parse_prescription_payload(data, received_at=NOW)
                self.assertIn(reason, ctx.exception.reasons)

    def test_payload_for_another_patient_is_refused(self):
        source = MockPrescriptionSource({"MR-1001": payload(patient_reference="ABHA-99")})
        with self.assertRaises(InvalidPayload) as ctx:
            import_prescription(source, "MR-1001", PATIENT, received_at=NOW)
        self.assertEqual(ctx.exception.reasons, ("PATIENT_REFERENCE_MISMATCH",))

    def test_transient_failure_is_retried_but_invalid_payload_is_not(self):
        flaky = MockPrescriptionSource({"MR-1001": payload()}, fail_times=1)
        import_prescription(flaky, "MR-1001", PATIENT, received_at=NOW, attempts=2)
        self.assertEqual(flaky.calls, 2)
        bad = MockPrescriptionSource({"MR-1001": payload(lines=[])})
        with self.assertRaises(InvalidPayload):
            import_prescription(bad, "MR-1001", PATIENT, received_at=NOW, attempts=3)
        self.assertEqual(bad.calls, 1)


class TimeoutAndErrorTests(unittest.TestCase):
    def test_slow_service_times_out(self):
        slow = MockPrescriptionSource({"MR-1001": payload()}, delay=0.3)
        with self.assertRaises(ServiceTimeout):
            import_prescription(slow, "MR-1001", PATIENT, received_at=NOW,
                                timeout=0.05, attempts=1)

    def test_unexpected_exceptions_become_controlled_errors(self):
        def boom():
            raise RuntimeError("password=hunter2 for db")

        with self.assertRaises(ExternalServiceError) as ctx:
            call_external("inventory", boom, timeout=1)
        self.assertEqual(ctx.exception.code, "UNAVAILABLE")
        self.assertNotIn("hunter2", str(ctx.exception))

    def test_timeout_and_attempts_must_be_positive(self):
        with self.assertRaises(ValueError):
            call_external("x", lambda: 1, timeout=0)
        with self.assertRaises(ValueError):
            call_external("x", lambda: 1, timeout=1, attempts=0)

    def test_inventory_failure_leaves_booking_and_stock_consistent(self):
        service, gateway = build(stock=5)
        service.waitlist.inventory = FaultyInventoryGateway(gateway, fail_reserve_times=2)
        book(service, "a")
        outcome = service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        self.assertIs(outcome.booking_state, BookingStatus.ELIGIBLE)
        self.assertEqual(outcome.reason_codes, ("RESERVATION_CONFLICT",))
        self.assertEqual(gateway.read("MED-1", LOCATION).reserved_quantity, 0)
        # Once the service recovers, the same booking allocates normally.
        self.assertIs(service.allocate(SYSTEM, "bk-a", LOCATION, at=NOW).booking_state,
                      BookingStatus.RESERVED)


class OtherDoubleTests(unittest.TestCase):
    def test_patient_context_mock_returns_only_requested_attributes(self):
        source = MockPatientContextSource({"patient-a": {
            "continuity_of_therapy": True, "hiv_status": "positive"}})
        response = source.fetch(ContextRequest("patient-a",
                                               frozenset({"continuity_of_therapy"}),
                                               "medicine-allocation"))
        self.assertEqual(dict(response.values), {"continuity_of_therapy": True})
        self.assertEqual(source.fetch(ContextRequest(
            "nobody", frozenset({"continuity_of_therapy"}), "x")).status, "UNKNOWN")

    def test_auth_mock_denies_unknown_and_expired_tokens(self):
        live = Principal("pharm-1", ActorRole.PHARMACIST, True,
                         session_expires_at=NOW + timedelta(hours=1))
        auth = MockAuthProvider({"tok-ok": live})
        self.assertEqual(auth.authenticate("tok-ok", at=NOW).actor_id, "pharm-1")
        with self.assertRaises(AccessDenied):
            auth.authenticate("forged", at=NOW)
        with self.assertRaises(AccessDenied):
            auth.authenticate("tok-ok", at=NOW + timedelta(hours=2))

    def test_notification_mock_is_idempotent_per_notification_id(self):
        sender = MockNotificationSender(fail_times=1)
        request = NotificationRequest("n-1", "bk-a", "contact-a", NotificationChannel.SMS,
                                      "reserved-v1", "Your booking is ready.", created_at=NOW)
        with self.assertRaises(ExternalServiceError):
            sender.send(request)
        first = sender.send(request)
        again = sender.send(request)
        self.assertIs(first.status, DeliveryStatus.SENT)
        self.assertEqual(first, again)
        self.assertEqual(len(sender.delivered), 1)

    def test_notification_request_rejects_long_or_non_text_parameters(self):
        with self.assertRaises(ValueError):
            NotificationRequest("n", "b", "r", NotificationChannel.SMS, "t", "body",
                                parameters={"x": "y" * 101}, created_at=NOW)
        with self.assertRaises(ValueError):
            NotificationRequest("n", "b", "r", NotificationChannel.SMS, "t", "body",
                                parameters={"x": 5}, created_at=NOW)


if __name__ == "__main__":
    unittest.main()
