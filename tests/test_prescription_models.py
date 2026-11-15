"""MBA-6: prescription intake relationships and timestamped state changes."""

from dataclasses import FrozenInstanceError
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID
import unittest

from medicine_booking import (
    Booking,
    MedicationRequest,
    Patient,
    Prescription,
    PrescriptionRecord,
    PrescriptionSource,
    PrescriptionStateChange,
    PrescriptionStatus,
    new_record_id,
    transition_prescription,
)


CREATED = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)


def intake_record() -> PrescriptionRecord:
    patient = Patient("patient-1", "A Patient", created_at=CREATED)
    prescription = Prescription(
        "rx-1", patient.patient_id, PrescriptionSource.STRUCTURED_IMPORT,
        PrescriptionStatus.RECEIVED, source_identifier="external-rx-9",
        authored_on=CREATED - timedelta(days=1), prescriber_reference="doctor-1",
        created_at=CREATED,
    )
    requests = (
        MedicationRequest("request-1", "rx-1", 2, medicine_code="MED-1", created_at=CREATED),
        MedicationRequest("request-2", "rx-1", 1, medicine_name="Another medicine", created_at=CREATED),
    )
    booking = Booking("booking-1", patient.patient_id, "rx-1", "request-1", 2,
                      created_at=CREATED, updated_at=CREATED)
    return PrescriptionRecord(patient, prescription, requests, (booking,))


class PrescriptionModelTests(unittest.TestCase):
    def test_intake_links_multiple_requests_and_booking_and_preserves_source(self):
        record = intake_record()
        self.assertEqual(record.prescription.patient_id, record.patient.patient_id)
        self.assertEqual({item.prescription_id for item in record.medication_requests}, {"rx-1"})
        self.assertEqual(record.bookings[0].medication_request_id, "request-1")
        self.assertEqual(record.prescription.source_identifier, "external-rx-9")
        self.assertEqual(record.prescription.prescriber_reference, "doctor-1")
        self.assertEqual(record.medication_requests[0].created_at, CREATED)

    def test_ids_are_generated_as_unique_uuids_and_records_are_immutable(self):
        self.assertNotEqual(new_record_id(), new_record_id())
        self.assertEqual(UUID(new_record_id()).version, 4)
        with self.assertRaises(FrozenInstanceError):
            intake_record().prescription.patient_id = "other"

    def test_rejects_missing_or_duplicate_requests_and_broken_booking_links(self):
        record = intake_record()
        with self.assertRaisesRegex(ValueError, "prescription patient"):
            PrescriptionRecord(Patient("other-patient", "Another Patient"),
                               record.prescription, record.medication_requests)
        with self.assertRaisesRegex(ValueError, "at least one"):
            PrescriptionRecord(record.patient, record.prescription, ())
        with self.assertRaisesRegex(ValueError, "duplicate medication request"):
            PrescriptionRecord(record.patient, record.prescription,
                               (record.medication_requests[0], record.medication_requests[0]))
        with self.assertRaisesRegex(ValueError, "does not belong"):
            PrescriptionRecord(record.patient, record.prescription,
                               (MedicationRequest("request-x", "other-rx", 1, medicine_code="MED-1"),))
        with self.assertRaisesRegex(ValueError, "does not reference"):
            PrescriptionRecord(record.patient, record.prescription,
                               (record.medication_requests[1],), record.bookings)
        with self.assertRaisesRegex(ValueError, "duplicate booking"):
            PrescriptionRecord(record.patient, record.prescription,
                               record.medication_requests, record.bookings * 2)
        with self.assertRaisesRegex(ValueError, "booking does not belong"):
            PrescriptionRecord(record.patient, record.prescription,
                               record.medication_requests,
                               (replace(record.bookings[0], patient_id="other-patient"),))

    def test_prescription_state_changes_are_timestamped_and_ordered(self):
        original = intake_record().prescription
        pending_at = CREATED + timedelta(minutes=1)
        valid_at = CREATED + timedelta(minutes=2)
        pending = transition_prescription(original, PrescriptionStatus.VALIDATION_PENDING, at=pending_at)
        valid = transition_prescription(pending, PrescriptionStatus.VALID, at=valid_at,
                                        reason="Verified by pharmacist")
        self.assertEqual(original.status, PrescriptionStatus.RECEIVED)
        self.assertEqual([change.changed_at for change in valid.state_changes], [pending_at, valid_at])
        self.assertEqual(valid.state_changes[1].previous_status, PrescriptionStatus.VALIDATION_PENDING)
        self.assertEqual(valid.validation_reason, "Verified by pharmacist")
        with self.assertRaisesRegex(ValueError, "last state change"):
            transition_prescription(valid, PrescriptionStatus.EXPIRED, at=pending_at)
        with self.assertRaisesRegex(ValueError, "must change"):
            transition_prescription(valid, PrescriptionStatus.VALID, at=valid_at)

    def test_rejects_inconsistent_prescription_history(self):
        original = intake_record().prescription
        pending_at = CREATED + timedelta(minutes=1)
        pending = PrescriptionStateChange(PrescriptionStatus.RECEIVED,
                                          PrescriptionStatus.VALIDATION_PENDING, pending_at)
        invalid_next = PrescriptionStateChange(PrescriptionStatus.RECEIVED,
                                               PrescriptionStatus.VALID, pending_at)
        with self.assertRaisesRegex(ValueError, "continuous history"):
            replace(original, status=PrescriptionStatus.VALID,
                    state_changes=(pending, invalid_next))
        with self.assertRaisesRegex(ValueError, "latest state change"):
            replace(original, state_changes=(pending,))
        with self.assertRaisesRegex(ValueError, "chronological"):
            replace(original, status=PrescriptionStatus.VALIDATION_PENDING,
                    state_changes=(replace(pending, changed_at=CREATED - timedelta(seconds=1)),))

    def test_rejects_invalid_quantity_and_naive_timestamps(self):
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            MedicationRequest("request-x", "rx-1", 0, medicine_code="MED-1")
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            MedicationRequest("request-x", "rx-1", 1, medicine_code="MED-1",
                              created_at=datetime(2026, 11, 1))


if __name__ == "__main__":
    unittest.main()
