"""MBA-20: pharmacy manager audit and fulfilment overview."""

from dataclasses import replace
from datetime import timedelta
import unittest

from medicine_booking import (
    AccessDenied, ActorRole, DeliveryStatus, NotificationChannel, NotificationEvent,
    Principal, audit_history, manager_overview,
)

from booking_support import LOCATION, MANAGER, NOW, PHARMACIST, SYSTEM, book, build, patient


class ManagerOverviewTests(unittest.TestCase):
    def setUp(self):
        self.service, _ = build(stock=2)
        book(self.service, "a")
        book(self.service, "b")
        book(self.service, "c")
        for name in ("a", "b", "c"):
            self.service.allocate(SYSTEM, f"bk-{name}", LOCATION, at=NOW)
        later = NOW + timedelta(minutes=5)
        # A pharmacist moves c ahead of b on the waitlist.
        self.service.waitlist.set_priority_override(
            "bk-c", 0, PHARMACIST.as_pharmacist(at=later), "Clinic called: dose due today",
            at=later)
        # a expires; its stock goes to c because of the override.
        self.service.expire_reservations(SYSTEM, at=NOW + timedelta(minutes=31))

    def test_overview_covers_srs_313_items(self):
        sent = NotificationEvent("n1", "bk-a", "contact-ref-1", NotificationChannel.SMS,
                                 "reserved-v1", DeliveryStatus.SENT, created_at=NOW)
        failed = replace(sent, notification_id="n2", delivery_status=DeliveryStatus.FAILED)
        overview = manager_overview(MANAGER, self.service, notifications=[sent, failed],
                                    at=NOW + timedelta(hours=1))
        self.assertEqual(dict(overview.active_bookings), {"RESERVED": 1, "WAITLISTED": 1})
        waitlist, = overview.active_waitlists
        self.assertEqual((waitlist.medicine_code, waitlist.location_id,
                          waitlist.active_entries, waitlist.waiting_quantity),
                         ("MED-1", LOCATION, 1, 2))
        self.assertIn("STOCK_RESERVED", {r.event_type for r in overview.allocation_history})
        self.assertIn("WAITLIST_PROMOTED", {r.event_type for r in overview.allocation_history})
        override, = overview.overrides
        self.assertEqual((override.actor, override.object_id), ("pharm-1", "bk-c"))
        self.assertEqual(override.reason, "Clinic called: dose due today")
        self.assertEqual([r.object_id for r in overview.expiry_events], ["bk-a"])
        self.assertEqual(overview.fulfilment.expired, 1)
        self.assertEqual(overview.fulfilment.fulfilment_rate, 0.0)
        self.assertEqual(overview.notifications.by_delivery_status["SENT"], 1)
        self.assertEqual(overview.notifications.by_delivery_status["FAILED"], 1)
        self.assertEqual(self.service.bookings["bk-c"].booking_state.value, "RESERVED")

    def test_patient_actor_ids_are_masked_and_no_names_shown(self):
        overview = manager_overview(MANAGER, self.service, at=NOW + timedelta(hours=1))
        rows = audit_history(MANAGER, self.service, at=NOW + timedelta(hours=1))
        submitted = [r for r in rows if r.event_type == "BOOKING_SUBMITTED"]
        self.assertTrue(submitted)
        self.assertTrue(all(r.actor == "PATIENT" for r in submitted))
        text = repr(overview) + repr(rows)
        for private in ("user-patient-a", "patient-a", "Insulin glargine", "rx-a"):
            self.assertNotIn(private, text)

    def test_audit_history_filters_by_object_and_type(self):
        rows = audit_history(MANAGER, self.service, object_id="bk-a",
                             event_types={"RESERVATION_EXPIRED", "STOCK_RELEASED"},
                             at=NOW + timedelta(hours=1))
        self.assertEqual(sorted(r.event_type for r in rows),
                         ["RESERVATION_EXPIRED", "STOCK_RELEASED"])
        self.assertTrue(all(r.timestamp and r.event_type and r.object_id for r in rows))
        self.assertEqual(rows, tuple(sorted(rows, key=lambda r: r.timestamp)))

    def test_only_managers_and_admins_can_open_it(self):
        for principal in (patient("patient-a"), PHARMACIST, SYSTEM):
            with self.subTest(role=principal.actor_role):
                with self.assertRaises(AccessDenied):
                    manager_overview(principal, self.service, at=NOW)
                with self.assertRaises(AccessDenied):
                    audit_history(principal, self.service, at=NOW)
        admin = Principal("admin-1", ActorRole.ADMINISTRATOR, True)
        manager_overview(admin, self.service, at=NOW + timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
