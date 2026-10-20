"""MBA-9: role-based access, object-level checks, uploads, and safe errors."""

from datetime import timedelta
import unittest

from medicine_booking import (
    AccessDenied, ActorRole, Principal, UploadPolicy, authorize_patient_record,
    public_error_message, validate_upload,
)
from medicine_booking.access import (
    ALLOCATION_OVERRIDE, AUDIT_READ, BOOKING_READ_ANY, BOOKING_READ_OWN,
    PRESCRIPTION_REVIEW, ROLE_PERMISSIONS, URGENCY_CONFIRM,
)

from datetime import datetime, timezone

NOW = datetime(2026, 11, 15, 9, tzinfo=timezone.utc)
PHARMACIST = Principal("pharm-1", ActorRole.PHARMACIST, True)
MANAGER = Principal("mgr-1", ActorRole.PHARMACY_MANAGER, True)


def patient(patient_id):
    return Principal(f"user-{patient_id}", ActorRole.PATIENT, True,
                     patient_ids=frozenset({patient_id}))


def check(principal, patient_id):
    authorize_patient_record(principal, patient_id, own_permission=BOOKING_READ_OWN,
                             any_permission=BOOKING_READ_ANY, at=NOW)


class RolePermissionTests(unittest.TestCase):
    def test_only_pharmacy_staff_can_review_confirm_urgency_or_override(self):
        for role, perms in ROLE_PERMISSIONS.items():
            pharmacy = role is ActorRole.PHARMACIST
            for permission in (PRESCRIPTION_REVIEW, URGENCY_CONFIRM, ALLOCATION_OVERRIDE):
                with self.subTest(role=role, permission=permission):
                    self.assertEqual(permission in perms, pharmacy)

    def test_audit_is_manager_and_admin_only(self):
        holders = {role for role, perms in ROLE_PERMISSIONS.items() if AUDIT_READ in perms}
        self.assertEqual(holders, {ActorRole.PHARMACY_MANAGER, ActorRole.ADMINISTRATOR})

    def test_unauthenticated_or_expired_sessions_are_denied(self):
        anonymous = Principal("x", ActorRole.PHARMACIST, False)
        expired = Principal("y", ActorRole.PHARMACIST, True,
                            session_expires_at=NOW - timedelta(seconds=1))
        for principal in (anonymous, expired):
            with self.assertRaises(AccessDenied):
                principal.require(PRESCRIPTION_REVIEW, at=NOW)
        live = Principal("z", ActorRole.PHARMACIST, True,
                         session_expires_at=NOW + timedelta(minutes=5))
        live.require(PRESCRIPTION_REVIEW, at=NOW)

    def test_staff_cannot_carry_patient_links(self):
        with self.assertRaisesRegex(ValueError, "patients and caregivers"):
            Principal("p", ActorRole.PHARMACIST, True, patient_ids=frozenset({"patient-a"}))

    def test_pharmacist_bridge_is_only_for_pharmacists(self):
        self.assertTrue(PHARMACIST.as_pharmacist(at=NOW).authenticated)
        with self.assertRaises(AccessDenied):
            MANAGER.as_pharmacist(at=NOW)


class ObjectAccessTests(unittest.TestCase):
    def test_patient_reaches_own_record_and_is_denied_others(self):
        check(patient("patient-a"), "patient-a")
        with self.assertRaises(AccessDenied):
            check(patient("patient-a"), "patient-b")

    def test_caregiver_reaches_only_linked_patients(self):
        carer = Principal("carer-1", ActorRole.CAREGIVER, True,
                          patient_ids=frozenset({"patient-a", "patient-c"}))
        check(carer, "patient-c")
        with self.assertRaises(AccessDenied):
            check(carer, "patient-b")


class UploadAndErrorTests(unittest.TestCase):
    def test_accepts_matching_types_within_size(self):
        self.assertEqual(validate_upload("application/pdf", b"%PDF-1.7 ..."), ())
        self.assertEqual(validate_upload("image/png; charset=binary",
                                         b"\x89PNG\r\n\x1a\n...."), ())
        self.assertEqual(validate_upload("IMAGE/JPEG", b"\xff\xd8\xff\xe0...."), ())

    def test_rejects_bad_uploads_with_reasons(self):
        policy = UploadPolicy(max_bytes=10)
        self.assertEqual(validate_upload("application/pdf", b""), ("UPLOAD_EMPTY",))
        self.assertEqual(validate_upload("application/pdf", b"%PDF-" + b"x" * 20, policy=policy),
                         ("UPLOAD_TOO_LARGE",))
        self.assertEqual(validate_upload("text/html", b"<script>"), ("UPLOAD_TYPE_NOT_ALLOWED",))
        self.assertEqual(validate_upload("application/pdf", b"MZ\x90\x00exe"),
                         ("UPLOAD_CONTENT_MISMATCH",))

    def test_public_errors_hide_internal_detail(self):
        secret = "postgres://admin:hunter2@db/patients patient-a rx-9"
        for error in (ValueError(secret), KeyError(secret), RuntimeError(secret), AccessDenied()):
            message = public_error_message(error)
            self.assertNotIn("hunter2", message)
            self.assertNotIn("patient-a", message)
            self.assertNotIn("Traceback", message)


if __name__ == "__main__":
    unittest.main()
