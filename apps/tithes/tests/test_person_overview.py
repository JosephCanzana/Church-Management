"""Tests for tithes.person_overview: the pure summary and who may see it (no report rows needed)."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.accounts.models import PersonType, Role
from apps.tithes.permissions import can_view_person_giving
from apps.tithes.person_overview import person_tithes_overview, summarize_tithes


def entry(day, amount, rid=1, role="giver", shared=()):
    return {
        "report_id": rid, "entry_id": rid, "date": day, "label": "Sunday Service",
        "extension": "Palayan", "amount": Decimal(amount), "shared_with": list(shared), "role": role,
    }


def actor(role, **extra):
    return SimpleNamespace(is_authenticated=True, is_active=True, role=role, **extra)


HISTORY = [                                   # newest first, as member_tithe_history returns it
    entry(date(2026, 10, 25), "500", 4),
    entry(date(2026, 10, 11), "300", 3),
    entry(date(2026, 9, 6), "200", 2),
    entry(date(2025, 1, 5), "900", 1),
]


class SummarizeTests(SimpleTestCase):
    def test_empty_history(self):
        ov = summarize_tithes([], date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual((ov["window_total"], ov["window_count"], ov["all_count"], ov["outside"]), (0, 0, 0, 0))
        self.assertIsNone(ov["average"])
        self.assertIsNone(ov["last_date"])

    def test_only_envelopes_inside_the_range_are_counted_and_listed(self):
        ov = summarize_tithes(HISTORY, date(2026, 9, 1), date(2026, 10, 31))
        self.assertEqual((ov["window_total"], ov["window_count"]), (1000, 3))
        self.assertEqual(ov["average"], Decimal("333.33"))
        self.assertEqual([r["report_id"] for r in ov["rows"]], [4, 3, 2])      # newest first, all of them
        self.assertEqual((ov["all_count"], ov["outside"]), (4, 1))

    def test_range_edges_are_inclusive(self):
        ov = summarize_tithes(HISTORY, date(2026, 10, 11), date(2026, 10, 25))
        self.assertEqual(ov["window_count"], 2)

    def test_a_range_with_no_giving_still_reports_what_lies_outside(self):
        ov = summarize_tithes(HISTORY, date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual((ov["window_count"], ov["rows"], ov["outside"]), (0, [], 4))
        self.assertEqual(ov["last_date"], date(2026, 10, 25))     # their newest ever, not the range's

    def test_no_range_means_everything(self):
        self.assertEqual(summarize_tithes(HISTORY)["window_count"], 4)

    def test_there_is_no_chart_data(self):
        self.assertNotIn("bars", summarize_tithes(HISTORY))


class WhoMaySeeTests(SimpleTestCase):
    def test_only_admin_and_super_admin(self):
        self.assertTrue(can_view_person_giving(actor(Role.SUPER_ADMIN)))
        self.assertTrue(can_view_person_giving(actor(Role.ADMIN)))
        self.assertFalse(can_view_person_giving(actor(Role.COORDINATOR)))
        self.assertFalse(can_view_person_giving(actor(Role.MEMBER)))
        self.assertFalse(can_view_person_giving(None))

    def test_a_suspended_or_archived_admin_sees_nothing(self):
        self.assertFalse(can_view_person_giving(SimpleNamespace(is_authenticated=True, is_active=False, role=Role.ADMIN)))

    def test_coordinator_and_guest_get_no_card(self):
        person = SimpleNamespace(person_type=PersonType.MEMBER_ACCOUNT)
        self.assertIsNone(person_tithes_overview(actor(Role.COORDINATOR), person))
        guest = SimpleNamespace(person_type=PersonType.GUEST)
        self.assertIsNone(person_tithes_overview(actor(Role.ADMIN), guest))
