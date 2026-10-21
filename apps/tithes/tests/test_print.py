"""Tests for the A4 print page (`tithes:report_print`): drafts, frozen numbers, privacy, bypass note, access."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Role, UserSpecialRole
from apps.core.services import new_submission_token
from apps.tithes import services
from apps.tithes.models import SPECIAL_ROLE, TithesOffering

from .support import finalize, make_extension, make_report, make_sheet, make_user

BALANCED = {"500": 1, "100": 1}   # 600 = offering 100 + one tithe of 500


class PrintTests(TestCase):
    """Who can print what, and what the page says."""

    def setUp(self):
        self.ext = make_extension()
        self.coord = make_user(Role.COORDINATOR, self.ext)
        self.admin = make_user(Role.ADMIN)
        self.giver = make_user(
            Role.MEMBER, self.ext, first="Juan", last="Cruz", birth_date=date(1990, 3, 14), phone_number="09171234567",
        )
        self.spouse = make_user(Role.MEMBER, self.ext, first="Maria", last="Cruz")
        self.sheet = make_sheet(self.ext, self.coord)

    def _get(self, user, report, namespace="coordinator_area"):
        self.client.force_login(user)
        return self.client.get(reverse(f"{namespace}:report_print", kwargs={"pk": report.pk}))

    def test_draft_says_draft_and_counts_live(self):
        report = make_report(self.coord, self.sheet, entries=[(self.giver, "500")], breakdown=BALANCED)
        response = self._get(self.coord, report)
        self.assertContains(response, "DRAFT, not finalized")
        self.assertContains(response, "500.00")
        self.assertContains(response, "Reports on Tithes &amp; Offering")

    def test_finalized_prints_frozen_numbers_without_draft_line(self):
        report = finalize(self.coord, make_report(
            self.coord, self.sheet, entries=[(self.giver, "500")], breakdown=BALANCED))
        response = self._get(self.coord, report)
        self.assertNotContains(response, "DRAFT, not finalized")
        self.assertEqual(report.total_amount, Decimal("600.00"))
        self.assertContains(response, "600.00")
        self.assertContains(response, "balanced")
        self.assertContains(response, "Finalized by")

    def test_shared_entry_prints_once_with_every_name(self):
        report = make_report(
            self.coord, self.sheet, entries=[(self.giver, "500", [self.spouse.pk])], breakdown=BALANCED)
        response = self._get(self.coord, report)
        self.assertContains(response, f"{self.giver.full_name} &amp; {self.spouse.full_name}", count=1)
        self.assertEqual(len(response.context["rows"]), 1)

    def test_no_birth_date_phone_or_address_reach_the_page(self):
        report = make_report(self.coord, self.sheet, entries=[(self.giver, "500")], breakdown=BALANCED)
        html = self._get(self.coord, report).content.decode()
        self.assertNotIn("09171234567", html)
        self.assertNotIn("1990", html)
        self.assertNotIn("March 14", html)

    def test_direct_admin_bypass_note_is_printed(self):
        report = make_report(self.admin, self.sheet, entries=[(self.giver, "500")], breakdown={"500": 1})
        finalize(self.admin, report, reason="Coins were donated to the bell")
        response = self._get(self.admin, report, "admin_area")
        self.assertContains(response, "Finalized with a difference of")
        self.assertContains(response, "short")
        self.assertContains(response, "Coins were donated to the bell")

    def test_approved_request_note_is_printed_and_names_the_approver(self):
        report = make_report(self.coord, self.sheet, entries=[(self.giver, "500")], breakdown={"500": 1})
        asked = services.request_bypass(
            self.coord, report.pk, message="Envelope torn", token=new_submission_token()).bypass_request
        services.decide_bypass(
            self.admin, asked.pk, approve=True, note="ok", token=new_submission_token())
        finalize(self.coord, report)
        response = self._get(self.coord, report)
        self.assertContains(response, "approved by")
        self.assertContains(response, self.admin.full_name)
        self.assertContains(response, "Envelope torn")
        self.assertNotContains(response, ">ok<")   # the decision note never reaches the page

    def test_balanced_report_has_no_bypass_note(self):
        report = finalize(self.coord, make_report(
            self.coord, self.sheet, entries=[(self.giver, "500")], breakdown=BALANCED))
        self.assertNotContains(self._get(self.coord, report), "Finalized with a difference of")

    def test_special_member_prints_drafts_only(self):
        seat = make_user(Role.MEMBER, self.ext)
        UserSpecialRole.objects.create(user=seat, role_id=SPECIAL_ROLE, extension=self.ext)
        report = make_report(self.coord, self.sheet, entries=[(self.giver, "500")], breakdown=BALANCED)
        self.assertEqual(self._get(seat, report, "tithes").status_code, 200)
        finalize(self.coord, report)
        self.assertEqual(self._get(seat, report, "tithes").status_code, 302)   # looks like a missing report

    def test_other_extension_coordinator_cannot_print(self):
        report = make_report(self.coord, self.sheet, entries=[(self.giver, "500")], breakdown=BALANCED)
        outsider = make_user(Role.COORDINATOR, make_extension())
        self.assertEqual(self._get(outsider, report).status_code, 302)

    def test_print_is_read_only(self):
        report = make_report(self.coord, self.sheet, entries=[(self.giver, "500")], breakdown=BALANCED)
        before = TithesOffering.objects.values().get(pk=report.pk)
        self._get(self.coord, report)
        self.assertEqual(TithesOffering.objects.values().get(pk=report.pk), before)

    def test_post_is_not_allowed(self):
        report = make_report(self.coord, self.sheet, entries=[(self.giver, "500")], breakdown=BALANCED)
        self.client.force_login(self.coord)
        response = self.client.post(reverse("coordinator_area:report_print", kwargs={"pk": report.pk}))
        self.assertEqual(response.status_code, 405)
