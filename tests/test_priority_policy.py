"""MBA-13: policy ordering, input authorization, and priority change records."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from medicine_booking import (
    ActorRole, Booking, BookingStatus, ContinuityStatus, PriorityContext,
    PriorityPolicy, order_bookings, re_evaluate_bookings,
)


NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
VERIFIED_AT = NOW - timedelta(hours=2)


def booking(identifier: str, state: BookingStatus = BookingStatus.ELIGIBLE) -> Booking:
    return Booking(identifier, f"patient-{identifier}", f"rx-{identifier}",
                   f"request-{identifier}", 1, booking_state=state,
                   created_at=NOW - timedelta(days=1), updated_at=NOW)


def context(identifier: str, *, urgency: str = "NORMAL",
            continuity: ContinuityStatus = ContinuityStatus.UNKNOWN,
            waiting_since: datetime = NOW - timedelta(hours=1),
            source: str = "pharmacist", role: ActorRole = ActorRole.PHARMACIST) -> PriorityContext:
    return PriorityContext(identifier, urgency, source, continuity, waiting_since,
                           role, "v1", evaluated_at=NOW,
                           urgency_actor_id="staff-1", urgency_verified_at=VERIFIED_AT)


def policy(version: str = "v1", *, normal_rank: int = 1,
           trusted_sources: frozenset[str] = frozenset()) -> PriorityPolicy:
    return PriorityPolicy(
        version, {"URGENT": 0, "NORMAL": normal_rank},
        {ContinuityStatus.CONTINUITY: 0, ContinuityStatus.NO_CONTINUITY: 1,
         ContinuityStatus.UNKNOWN: 2},
        trusted_urgency_sources=trusted_sources,
    )


class PriorityPolicyTests(unittest.TestCase):
    def test_urgency_continuity_waiting_and_booking_id_break_ties(self):
        bookings = [booking(name) for name in ("z", "b", "a", "old", "urgent", "continuity")]
        contexts = {
            "z": context("z"),
            "b": context("b"),
            "a": context("a"),
            "old": context("old", waiting_since=NOW - timedelta(hours=3)),
            "urgent": context("urgent", urgency="URGENT"),
            "continuity": context("continuity", continuity=ContinuityStatus.CONTINUITY),
        }
        result = order_bookings(reversed(bookings), contexts, policy(), evaluated_at=NOW)
        self.assertEqual([item.booking_id for item in result],
                         ["urgent", "continuity", "old", "a", "b", "z"])
        self.assertEqual([item.rank for item in result], [1, 2, 3, 4, 5, 6])
        self.assertIn("CONTINUITY:UNKNOWN", result[3].reason_codes)
        self.assertIn("TIE_BREAK:BOOKING_ID", result[3].reason_codes)
        self.assertEqual(result[3].urgency_actor_id, "staff-1")
        self.assertEqual(result[3].urgency_source, "pharmacist")
        self.assertEqual(result[3].urgency_verified_at, VERIFIED_AT)
        self.assertEqual(result[3].evaluated_at, NOW)

    def test_missing_context_and_unconfigured_unknown_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "missing priority context"):
            order_bookings([booking("a")], {}, policy(), evaluated_at=NOW)
        with self.assertRaisesRegex(ValueError, "UNKNOWN"):
            PriorityPolicy("v1", {"NORMAL": 1}, {ContinuityStatus.CONTINUITY: 0})
        with self.assertRaisesRegex(ValueError, "future"):
            order_bookings([booking("a")],
                           {"a": context("a", waiting_since=NOW + timedelta(seconds=1))},
                           policy(), evaluated_at=NOW)

    def test_only_authorized_pharmacy_actor_or_configured_system_source(self):
        candidate = booking("a")
        for role in (ActorRole.PATIENT, ActorRole.CAREGIVER, ActorRole.SYSTEM):
            with self.subTest(role=role), self.assertRaisesRegex(ValueError, "authorized source"):
                order_bookings([candidate], {"a": context("a", role=role)},
                               policy(), evaluated_at=NOW)
        trusted = context("a", role=ActorRole.SYSTEM, source="hospital-feed")
        result = order_bookings([candidate], {"a": trusted},
                                policy(trusted_sources=frozenset({"hospital-feed"})),
                                evaluated_at=NOW)
        self.assertEqual(result[0].urgency_source, "hospital-feed")
        with self.assertRaisesRegex(ValueError, "pharmacy roles"):
            PriorityPolicy("v1", {"NORMAL": 1}, {ContinuityStatus.UNKNOWN: 0},
                           authorized_urgency_roles=frozenset({ActorRole.PATIENT}))

    def test_re_evaluation_retains_previous_and_new_result(self):
        bookings = [booking("a"), booking("b")]
        contexts = {"a": context("a"), "b": context("b")}
        initial = order_bookings(bookings, contexts, policy(), evaluated_at=NOW)
        previous = {item.booking_id: item for item in initial}

        changed_contexts = {**contexts, "b": replace(contexts["b"], verified_urgency="URGENT")}
        updated = re_evaluate_bookings(bookings, changed_contexts, policy(), previous,
                                       evaluated_at=NOW + timedelta(minutes=1))
        self.assertEqual([item.booking_id for item in updated.results], ["b", "a"])
        self.assertEqual(len(updated.changes), 2)
        self.assertEqual(updated.changes[0].previous.rank, 2)
        self.assertEqual(updated.changes[0].current.rank, 1)
        self.assertIn("VERIFIED_URGENCY:URGENT", updated.changes[0].current.reason_codes)

        new_policy = policy("v2", normal_rank=0)
        changed_policy = re_evaluate_bookings(bookings, contexts, new_policy, previous,
                                              evaluated_at=NOW + timedelta(minutes=2))
        self.assertEqual(len(changed_policy.changes), 2)
        self.assertEqual(changed_policy.changes[0].previous.policy_version, "v1")
        self.assertEqual(changed_policy.changes[0].current.policy_version, "v2")
        same_policy = re_evaluate_bookings(bookings, contexts, policy(), previous,
                                           evaluated_at=NOW + timedelta(minutes=2))
        self.assertEqual(same_policy.changes, ())
        with self.assertRaisesRegex(ValueError, "previous priority result does not match"):
            re_evaluate_bookings(bookings, contexts, policy(), {"a": previous["b"]},
                                 evaluated_at=NOW)

    def test_policy_rank_change_reverses_order_and_records_reason(self):
        bookings = [booking("a"), booking("b")]
        contexts = {"a": context("a", continuity=ContinuityStatus.CONTINUITY),
                    "b": context("b", continuity=ContinuityStatus.UNKNOWN)}
        original = order_bookings(bookings, contexts, policy(), evaluated_at=NOW)
        changed = PriorityPolicy(
            "v2", {"URGENT": 0, "NORMAL": 1},
            {ContinuityStatus.CONTINUITY: 2, ContinuityStatus.UNKNOWN: 0},
        )
        evaluation = re_evaluate_bookings(bookings, contexts, changed,
                                          {item.booking_id: item for item in original},
                                          evaluated_at=NOW + timedelta(minutes=1))
        self.assertEqual([item.booking_id for item in evaluation.results], ["b", "a"])
        self.assertEqual(len(evaluation.changes), 2)
        self.assertIn("CONTINUITY_RANK:0", evaluation.changes[0].current.reason_codes)

    def test_commercial_and_unrelated_personal_data_cannot_enter_policy(self):
        candidate = booking("a")
        approved = context("a")
        for forbidden in ("payment_amount", "loyalty_status", "subscription_tier",
                          "marketing_engagement", "protected_characteristic"):
            with self.subTest(forbidden=forbidden), self.assertRaises(TypeError):
                replace(approved, **{forbidden: "VIP"})
        with self.assertRaises(TypeError):
            PriorityPolicy("v1", {"NORMAL": 1}, {ContinuityStatus.UNKNOWN: 0},
                           payment_rank={"VIP": 0})
        # Patient and booking identifiers are used only as relationships/tie-breaks.
        self.assertEqual(order_bookings([candidate], {"a": approved}, policy(),
                                        evaluated_at=NOW)[0].rank, 1)

    def test_policy_inputs_are_snapshotted_and_inactive_bookings_are_excluded(self):
        urgency = {"NORMAL": 1}
        configured = PriorityPolicy("v1", urgency, {ContinuityStatus.UNKNOWN: 0})
        urgency["NORMAL"] = 99
        active = booking("a")
        inactive = booking("b", BookingStatus.CANCELLED)
        result = order_bookings([inactive, active], {"a": context("a")},
                                configured, evaluated_at=NOW)
        self.assertEqual([item.booking_id for item in result], ["a"])
        self.assertIn("URGENCY_RANK:1", result[0].reason_codes)


if __name__ == "__main__":
    unittest.main()
