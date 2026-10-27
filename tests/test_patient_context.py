"""MBA-11: minimal patient context with consent metadata and UNKNOWN fallback."""

from dataclasses import fields
from datetime import timedelta
import unittest

from medicine_booking import AccessDenied, ConsentStatus, ContinuityStatus, order_bookings
from medicine_booking.contracts import MockPatientContextSource
from medicine_booking.patient_context import (
    CONTINUITY_ATTRIBUTE, PatientContextPolicy, PatientContextResult,
    PatientContextService, apply_to_context, refresh_continuity,
)

from booking_support import NOW, PHARMACIST, book, build, patient


RECORDS = {
    "patient-a": {CONTINUITY_ATTRIBUTE: True, "diagnosis": "E11", "hiv_status": "positive"},
    "patient-b": {CONTINUITY_ATTRIBUTE: False},
    "patient-c": {"diagnosis": "E11"},
}


def service(**kwargs):
    source_kwargs = {k: kwargs.pop(k) for k in list(kwargs)
                     if k in {"fail_times", "delay", "leak_unrequested"}}
    source = MockPatientContextSource(RECORDS, source_timestamp=NOW - timedelta(days=2),
                                      **source_kwargs)
    return PatientContextService(source, **kwargs), source


def grant(svc, patient_id="patient-a", **kwargs):
    return svc.record_consent(patient(patient_id), patient_id, ConsentStatus.GRANTED,
                              reference=f"consent-{patient_id}", at=NOW, **kwargs)


class ConsentTests(unittest.TestCase):
    def test_no_consent_means_unknown_and_no_source_call(self):
        svc, source = service()
        result = svc.lookup("patient-a", at=NOW)
        self.assertIs(result.continuity, ContinuityStatus.UNKNOWN)
        self.assertEqual(result.reason, "CONSENT_NOT_GRANTED")
        self.assertEqual(source.requests, [])

    def test_consent_metadata_is_recorded_and_sent_with_the_request(self):
        svc, source = service()
        consent = grant(svc)
        self.assertEqual((consent.recorded_by, consent.purpose, consent.recorded_at),
                         ("user-patient-a", "medicine-allocation", NOW))
        result = svc.lookup("patient-a", at=NOW)
        self.assertEqual(result.consent_reference, "consent-patient-a")
        self.assertEqual(source.requests[0].consent_reference, "consent-patient-a")
        self.assertIn("CONSENT_RECORDED", [e.event_type for e in svc.audit_events])

    def test_revoked_or_expired_consent_stops_lookups(self):
        svc, source = service()
        grant(svc, expires_at=NOW + timedelta(days=1))
        self.assertEqual(svc.lookup("patient-a", at=NOW + timedelta(days=2)).reason,
                         "CONSENT_NOT_GRANTED")
        svc.record_consent(PHARMACIST, "patient-a", ConsentStatus.REVOKED,
                           reference="revoke-1", at=NOW + timedelta(hours=1))
        self.assertEqual(svc.lookup("patient-a", at=NOW + timedelta(hours=2)).reason,
                         "CONSENT_NOT_GRANTED")
        self.assertEqual(len(svc.consent_history), 2)

    def test_patient_cannot_record_consent_for_someone_else(self):
        svc, _ = service()
        with self.assertRaises(AccessDenied):
            svc.record_consent(patient("patient-a"), "patient-b", ConsentStatus.GRANTED,
                               reference="x", at=NOW)

    def test_consent_can_be_switched_off_by_policy(self):
        svc, _ = service(policy=PatientContextPolicy(consent_required=False))
        self.assertIs(svc.lookup("patient-b", at=NOW).continuity, ContinuityStatus.NO_CONTINUITY)


class MinimalContextTests(unittest.TestCase):
    def test_returns_only_configured_attributes(self):
        svc, source = service()
        grant(svc)
        result = svc.lookup("patient-a", at=NOW)
        self.assertIs(result.continuity, ContinuityStatus.CONTINUITY)
        self.assertEqual(dict(result.values), {CONTINUITY_ATTRIBUTE: True})
        self.assertEqual(source.requests[0].attributes, frozenset({CONTINUITY_ATTRIBUTE}))

    def test_unrequested_fields_from_a_careless_source_are_dropped(self):
        svc, _ = service(leak_unrequested=True)
        grant(svc)
        result = svc.lookup("patient-a", at=NOW)
        self.assertEqual(set(result.values), {CONTINUITY_ATTRIBUTE})
        text = repr(result) + repr(svc.audit_events)
        for leaked in ("E11", "hiv_status", "positive", "diagnosis"):
            self.assertNotIn(leaked, text)
        lookup = [e for e in svc.audit_events if e.event_type == "PATIENT_CONTEXT_LOOKUP"][-1]
        self.assertIn("discarded_unrequested_fields=2", lookup.reason)

    def test_result_type_has_no_room_for_history_fields(self):
        names = {f.name for f in fields(PatientContextResult)}
        self.assertFalse(names & {"diagnosis", "history", "conditions", "medications"})

    def test_missing_value_is_unknown_and_never_inferred(self):
        svc, _ = service(leak_unrequested=True)
        grant(svc, "patient-c")
        # patient-c has a diabetes diagnosis but no continuity flag: stays UNKNOWN.
        result = svc.lookup("patient-c", at=NOW)
        self.assertIs(result.continuity, ContinuityStatus.UNKNOWN)
        self.assertEqual(result.reason, "CONTINUITY_NOT_PROVIDED")

    def test_non_boolean_values_are_not_interpreted(self):
        for value in ("yes", 1, "true", None):
            with self.subTest(value=value):
                source = MockPatientContextSource({"p": {CONTINUITY_ATTRIBUTE: value}})
                svc = PatientContextService(source, policy=PatientContextPolicy(
                    consent_required=False))
                self.assertIs(svc.lookup("p", at=NOW).continuity, ContinuityStatus.UNKNOWN)

    def test_unavailable_or_slow_source_gives_unknown(self):
        down, _ = service(fail_times=5)
        grant(down)
        self.assertEqual(down.lookup("patient-a", at=NOW).reason, "SOURCE_UNAVAILABLE")
        slow, _ = service(delay=0.3, policy=PatientContextPolicy(timeout_seconds=0.05))
        grant(slow)
        self.assertEqual(slow.lookup("patient-a", at=NOW).reason, "SOURCE_TIMEOUT")
        unknown_patient, _ = service(policy=PatientContextPolicy(consent_required=False))
        self.assertEqual(unknown_patient.lookup("nobody", at=NOW).reason, "SOURCE_HAS_NO_CONTEXT")


class PriorityIntegrationTests(unittest.TestCase):
    def test_continuity_feeds_priority_order(self):
        bookings, _ = build()
        book(bookings, "b")
        book(bookings, "a", at=NOW + timedelta(minutes=5))  # a waited less
        ctx, _ = service(audit_events=bookings.audit_events)
        grant(ctx, "patient-a")
        grant(ctx, "patient-b")
        before = order_bookings(bookings.bookings.values(), bookings.waitlist.contexts,
                                bookings.waitlist.policy, evaluated_at=NOW + timedelta(minutes=6))
        self.assertEqual([r.booking_id for r in before], ["bk-b", "bk-a"])
        when = NOW + timedelta(minutes=10)
        for booking_id in ("bk-a", "bk-b"):
            refresh_continuity(bookings, ctx, booking_id, at=when)
        after = order_bookings(bookings.bookings.values(), bookings.waitlist.contexts,
                               bookings.waitlist.policy, evaluated_at=when)
        self.assertEqual([r.booking_id for r in after], ["bk-a", "bk-b"])
        self.assertIn("CONTINUITY:CONTINUITY", after[0].reason_codes)
        self.assertIn("PATIENT_CONTEXT_LOOKUP", [e.event_type for e in bookings.audit_events])

    def test_apply_replaces_previous_context_code(self):
        bookings, _ = build()
        book(bookings, "a")
        ctx, _ = service()
        grant(ctx)
        first = apply_to_context(bookings.waitlist.contexts["bk-a"],
                                 ctx.lookup("patient-a", at=NOW + timedelta(minutes=1)))
        second = apply_to_context(first, ctx.lookup("patient-a", at=NOW + timedelta(minutes=2)))
        self.assertEqual([c for c in second.reason_codes if c.startswith("CONTEXT_")],
                         ["CONTEXT_AVAILABLE"])
        with self.assertRaises(ValueError):
            apply_to_context(second, ctx.lookup("patient-a", at=NOW))


if __name__ == "__main__":
    unittest.main()
