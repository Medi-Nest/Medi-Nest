"""MBA-19: booking, waitlist, reservation, and cancellation notifications."""

from dataclasses import replace
from datetime import timedelta
import unittest

from medicine_booking import (
    BookingStatus, DeliveryStatus, NotificationChannel, PrescriptionStatus,
    RestockEvent, manager_overview,
)
from medicine_booking.contracts import MockNotificationSender
from medicine_booking.notifications import (
    NotificationService, NotificationTemplate, Recipient, RetryPolicy, notification_events,
)

from booking_support import LOCATION, MANAGER, NOW, SYSTEM, book, build, patient


def setup(*, stock=2, fail_times=0, delay=0.0, retry=None, channel=NotificationChannel.SMS):
    bookings, gateway = build(stock=stock)
    sender = MockNotificationSender(fail_times=fail_times, delay=delay)
    clock = {"now": NOW}
    notes = NotificationService(
        sender, bookings=bookings.bookings,
        recipients={f"patient-{n}": Recipient(f"patient-{n}", f"+91-90000-0000{i}", channel)
                    for i, n in enumerate("abc")},
        retry=retry, reservation_expiry=bookings.reservation_expiry,
        pharmacy_name="Medi-Nest Indiranagar", clock=lambda: clock["now"],
        audit_events=bookings.audit_events,
    )
    bookings.waitlist.notify = notes
    return bookings, gateway, notes, sender, clock


def kinds(notes, booking_id):
    return [r.event_type for r in notes.history(booking_id)]


class EventCoverageTests(unittest.TestCase):
    def test_every_lifecycle_change_creates_one_notification(self):
        bookings, _, notes, sender, clock = setup(stock=2)
        book(bookings, "a")
        book(bookings, "b")
        bookings.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        bookings.allocate(SYSTEM, "bk-b", LOCATION, at=NOW)
        clock["now"] = NOW + timedelta(minutes=5)
        bookings.cancel(patient("patient-a"), "bk-a", at=clock["now"])   # b gets promoted
        clock["now"] = NOW + timedelta(hours=1)
        bookings.expire_reservations(SYSTEM, at=clock["now"])          # b expires
        self.assertEqual(kinds(notes, "bk-a"), ["SUBMITTED", "RESERVED", "CANCELLED"])
        self.assertEqual(kinds(notes, "bk-b"), ["SUBMITTED", "WAITLISTED", "RESERVED", "EXPIRED"])
        self.assertEqual(len(sender.delivered), 7)

    def test_rejection_is_notified(self):
        bookings, _, notes, _, _ = setup()
        bookings.prescriptions["rx-a"] = replace(bookings.prescriptions["rx-a"],
                                                 status=PrescriptionStatus.EXPIRED)
        book(bookings, "a")
        self.assertEqual(kinds(notes, "bk-a"), ["REJECTED"])

    def test_reservation_message_includes_expiry(self):
        bookings, _, notes, sender, _ = setup()
        book(bookings, "a")
        bookings.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        reserved, = [r for r in notes.history("bk-a") if r.event_type == "RESERVED"]
        body = sender.delivered[reserved.event.notification_id].body
        self.assertIn("15 Nov 2026, 09:30 UTC", body)
        self.assertIn("Medi-Nest Indiranagar", body)

    def test_waitlist_promotion_after_restock_is_notified(self):
        bookings, _, notes, _, clock = setup(stock=0)
        book(bookings, "a")
        bookings.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        clock["now"] = NOW + timedelta(minutes=2)
        bookings.waitlist.process_restock(
            RestockEvent("r-1", "MED-1", LOCATION, 5, NOW + timedelta(minutes=1)),
            at=clock["now"])
        self.assertEqual(kinds(notes, "bk-a")[-1], "RESERVED")


class DeliveryRecordTests(unittest.TestCase):
    def test_records_channel_times_and_status(self):
        bookings, _, notes, _, _ = setup()
        book(bookings, "a")
        record, = notes.history("bk-a")
        self.assertEqual((record.event.channel, record.event.created_at,
                          record.event.delivery_status), (NotificationChannel.SMS, NOW,
                                                          DeliveryStatus.SENT))
        self.assertEqual(record.event.delivered_at, NOW)
        attempt, = record.attempts
        self.assertEqual((attempt.attempt_number, attempt.channel, attempt.attempted_at),
                         (1, NotificationChannel.SMS, NOW))

    def test_failure_is_retried_on_schedule_then_delivered(self):
        bookings, _, notes, sender, _ = setup(fail_times=2)
        book(bookings, "a")
        record, = notes.history("bk-a")
        self.assertIs(record.event.delivery_status, DeliveryStatus.FAILED)
        self.assertEqual(notes.retry_due(at=NOW + timedelta(seconds=30)), ())
        notes.retry_due(at=NOW + timedelta(minutes=1))          # attempt 2 fails
        self.assertEqual(notes.retry_due(at=NOW + timedelta(minutes=3)), ())
        notes.retry_due(at=NOW + timedelta(minutes=6))          # attempt 3 succeeds
        record, = notes.history("bk-a")
        self.assertIs(record.event.delivery_status, DeliveryStatus.SENT)
        self.assertEqual([a.status for a in record.attempts],
                         [DeliveryStatus.FAILED, DeliveryStatus.FAILED, DeliveryStatus.SENT])
        self.assertEqual(len(sender.delivered), 1)

    def test_gives_up_after_max_attempts(self):
        bookings, _, notes, _, _ = setup(fail_times=10, retry=RetryPolicy(max_attempts=2))
        book(bookings, "a")
        notes.retry_due(at=NOW + timedelta(minutes=1))
        self.assertEqual(notes.retry_due(at=NOW + timedelta(days=1)), ())
        self.assertIn("NOTIFICATION_ABANDONED", [e.event_type for e in bookings.audit_events])

    def test_outage_or_timeout_never_undoes_a_reservation(self):
        for kwargs in ({"fail_times": 99},
                       {"delay": 0.3, "retry": RetryPolicy(timeout_seconds=0.05)}):
            with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
                bookings, gateway, notes, _, _ = setup(**kwargs)
                book(bookings, "a")
                outcome = bookings.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
                self.assertIs(outcome.booking_state, BookingStatus.RESERVED)
                self.assertEqual(gateway.read("MED-1", LOCATION).reserved_quantity, 2)
                self.assertTrue(all(r.event.delivery_status is DeliveryStatus.FAILED
                                    for r in notes.history("bk-a")))

    def test_manager_overview_counts_delivery_status(self):
        bookings, _, notes, _, _ = setup(fail_times=1)
        book(bookings, "a")
        book(bookings, "b")
        overview = manager_overview(MANAGER, bookings, notifications=notification_events(notes),
                                    at=NOW + timedelta(minutes=1))
        self.assertEqual(overview.notifications.by_delivery_status["SENT"], 1)
        self.assertEqual(overview.notifications.by_delivery_status["FAILED"], 1)


class PrivacyTests(unittest.TestCase):
    def test_messages_never_name_the_medicine_or_patient(self):
        bookings, _, notes, sender, _ = setup()
        book(bookings, "a")
        bookings.allocate(SYSTEM, "bk-a", LOCATION, at=NOW)
        for request in sender.delivered.values():
            for private in ("Insulin", "glargine", "MED-1", "rx-a", "patient-a"):
                self.assertNotIn(private, request.body)
                self.assertNotIn(private, " ".join(request.parameters.values()))

    def test_templates_cannot_use_sensitive_parameters(self):
        with self.assertRaisesRegex(ValueError, "not allowed"):
            NotificationTemplate("t", "RESERVED", NotificationChannel.PUSH,
                                 "Your {medicine} for {diagnosis} is ready")
        with self.assertRaisesRegex(ValueError, "unknown notification event"):
            NotificationTemplate("t", "PROMO", NotificationChannel.PUSH, "Sale!")

    def test_each_patient_gets_only_their_own_notifications(self):
        bookings, _, notes, sender, _ = setup()
        book(bookings, "a")
        book(bookings, "b")
        to = {r.event.booking_id: r.event.recipient_reference for r in notes.history()}
        self.assertEqual(to, {"bk-a": "+91-90000-00000", "bk-b": "+91-90000-00001"})

    def test_recipient_table_cannot_point_one_patient_at_another_contact(self):
        bookings, _ = build()
        with self.assertRaisesRegex(ValueError, "must match"):
            NotificationService(MockNotificationSender(), bookings=bookings.bookings,
                                recipients={"patient-a": Recipient("patient-b", "x",
                                                                   NotificationChannel.SMS)})

    def test_patient_without_verified_contact_is_skipped_and_audited(self):
        bookings, _, notes, sender, _ = setup()
        notes.recipients.pop("patient-c")
        book(bookings, "c")
        self.assertEqual(notes.history("bk-c"), ())
        self.assertIn("NOTIFICATION_SKIPPED", [e.event_type for e in bookings.audit_events])


if __name__ == "__main__":
    unittest.main()
