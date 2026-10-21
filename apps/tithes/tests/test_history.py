"""Tests for "My tithes": `services.member_tithe_history` and the `tithes:mine` page."""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Role
from apps.attendance import services as attendance_services
from apps.core.services import new_submission_token
from apps.tithes import services

from .support import finalize, make_extension, make_report, make_sheet, make_user

BALANCED_500 = {"500": 1, "100": 1}
BALANCED_300 = {"200": 1, "100": 2}   # offering 100 + tithe 300 = 400 -> 200 + 100 + 100


class HistoryTests(TestCase):
    """Own entries only, newest first, finalized only, full amount for a shared envelope."""

    def setUp(self):
        self.ext = make_extension()
        self.coord = make_user(Role.COORDINATOR, self.ext)
        self.admin = make_user(Role.ADMIN)
        self.me = make_user(Role.MEMBER, self.ext, first="Ana", last="Reyes")
        self.partner = make_user(Role.MEMBER, self.ext, first="Ben", last="Reyes")
        self.other = make_user(Role.MEMBER, self.ext, first="Cara", last="Lim")

    def _finalized(self, entries, breakdown=BALANCED_500, **sheet_kwargs):
        sheet = make_sheet(self.ext, self.coord, **sheet_kwargs)
        return finalize(self.coord, make_report(self.coord, sheet, entries=entries, breakdown=breakdown))

    def test_only_my_own_entries(self):
        self._finalized([(self.me, "500")])
        self._finalized([(self.other, "300")], BALANCED_300)
        history = services.member_tithe_history(self.me)
        self.assertEqual([str(h["amount"]) for h in history], ["500.00"])

    def test_giver_sees_co_givers_with_full_amount(self):
        self._finalized([(self.me, "500", [self.partner.pk])])
        (entry,) = services.member_tithe_history(self.me)
        self.assertEqual(entry["role"], "giver")
        self.assertEqual(str(entry["amount"]), "500.00")
        self.assertEqual(entry["shared_with"], [self.partner.full_name])

    def test_co_giver_sees_the_shared_entry_with_the_full_amount(self):
        self._finalized([(self.me, "500", [self.partner.pk])])
        (entry,) = services.member_tithe_history(self.partner)
        self.assertEqual(entry["role"], "co_giver")
        self.assertEqual(str(entry["amount"]), "500.00")   # never split in half
        self.assertEqual(entry["shared_with"], [self.me.full_name])

    def test_draft_report_is_excluded(self):
        make_report(self.coord, make_sheet(self.ext, self.coord), entries=[(self.me, "500")], breakdown=BALANCED_500)
        self.assertEqual(services.member_tithe_history(self.me), [])

    def test_reopened_report_leaves_the_history_until_finalized_again(self):
        report = self._finalized([(self.me, "500")])
        services.reopen_report(self.admin, report.pk, token=new_submission_token())
        self.assertEqual(services.member_tithe_history(self.me), [])
        finalize(self.coord, report)
        self.assertEqual(len(services.member_tithe_history(self.me)), 1)

    def test_report_on_an_archived_sheet_is_excluded(self):
        report = self._finalized([(self.me, "500")])
        attendance_services.archive_sheet(self.admin, report.attendance_id)
        self.assertEqual(services.member_tithe_history(self.me), [])

    def test_newest_first(self):
        from datetime import date
        self._finalized([(self.me, "500")], day=date(2026, 2, 1))
        self._finalized([(self.me, "300")], BALANCED_300, day=date(2026, 3, 1))
        dates = [h["date"] for h in services.member_tithe_history(self.me)]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual(len(dates), 2)

    def test_page_shows_only_the_signed_in_persons_history(self):
        self._finalized([(self.me, "500")])
        self._finalized([(self.other, "300")], BALANCED_300)
        self.client.force_login(self.me)
        response = self.client.get(reverse("tithes:mine"))
        self.assertContains(response, "500.00")
        self.assertNotContains(response, "300.00")

    def test_page_ignores_a_user_id_in_the_query_string(self):
        self._finalized([(self.other, "300")], BALANCED_300)
        self.client.force_login(self.me)
        response = self.client.get(reverse("tithes:mine"), {"user": self.other.pk, "user_id": self.other.pk})
        self.assertNotContains(response, "300.00")

    def test_every_role_can_open_the_page(self):
        for person in (self.me, self.coord, self.admin):
            self.client.force_login(person)
            self.assertEqual(self.client.get(reverse("tithes:mine")).status_code, 200)

    def test_anonymous_is_sent_to_sign_in(self):
        self.assertEqual(self.client.get(reverse("tithes:mine")).status_code, 302)
