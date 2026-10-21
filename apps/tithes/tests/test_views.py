"""tithes.tests.test_views: the role matrix per route, token replay, the locked draft and the wrong-namespace redirect.

Tests go through the HTTP client and the services only. The three `make_*` helpers below are local on purpose
(they do not depend on `helpers.py`); `make_sheet` still ASSUMES the `Attendance` field names. Everything else is plain behaviour.
"""
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Extension, Role, Status, User, UserSpecialRole
from apps.attendance.models import Attendance
from apps.core.services import new_submission_token

from .. import services
from ..models import SPECIAL_ROLE, TitheBypassRequest, TithesOffering

PASSWORD = "pass-12345-word"


def make_extension(name):
    """An extension (ADAPT if Extension needs more fields)."""
    return Extension.objects.create(name=name)


def make_user(label, role, extension=None, **extra):
    """An active account that can sign in. No email on purpose: an email needs a verified pair (DB constraint).

    Same shape as the events test helper: `must_change_password=False`, `status` active, person type left at the
    default (`member_account`). `label` only makes the first name readable in failures.
    """
    return User.objects.create_user(
        password=PASSWORD, role=role, extension=extension, status=Status.ACTIVE,
        first_name=label.split("@")[0].title(), last_name="Tester", must_change_password=False, **extra,
    )


def make_sheet(extension, label="Sunday Service", date="2026-10-18"):
    """A draft attendance sheet (one per extension, date and label)."""
    return Attendance.objects.create(extension=extension, attendance_date=date, label=label)


class TithesViewTests(TestCase):
    """Role matrix and the guards of the tithes screens."""

    @classmethod
    def setUpTestData(cls):
        """One extension with a coordinator, an admin, a super-admin and a seat holder; a second extension apart."""
        cls.ext, cls.other_ext = make_extension("north"), make_extension("south")
        cls.superadmin = make_user("super@x.test", Role.SUPER_ADMIN)
        cls.admin = make_user("admin@x.test", Role.ADMIN)
        cls.coord = make_user("coord@x.test", Role.COORDINATOR, cls.ext)
        cls.other_coord = make_user("coord2@x.test", Role.COORDINATOR, cls.other_ext)
        cls.seat = make_user("seat@x.test", Role.MEMBER, cls.ext)
        UserSpecialRole.objects.create(user=cls.seat, role_id=SPECIAL_ROLE, extension=cls.ext)
        cls.plain = make_user("plain@x.test", Role.MEMBER, cls.ext)
        cls.sheet = make_sheet(cls.ext)
        cls.report = services.create_report(cls.coord, cls.sheet)

    def go(self, user, name, namespace, *args):
        """GET url `name` in `namespace` as `user`."""
        self.client.force_login(user)
        return self.client.get(reverse(f"{namespace}:{name}", args=args))

    def texts(self, response):
        """Toast messages of the next response, as plain strings."""
        return [str(m) for m in get_messages(response.wsgi_request)]

    # ---- role matrix
    def test_list_opens_in_each_own_area(self):
        for user, ns in [(self.superadmin, "superadmin"), (self.admin, "admin_area"),
                         (self.coord, "coordinator_area"), (self.seat, "tithes")]:
            with self.subTest(ns=ns):
                self.assertEqual(self.go(user, "report_list", ns).status_code, 200)

    def test_member_without_the_seat_is_refused(self):
        response = self.go(self.plain, "report_list", "tithes")
        self.assertEqual(response.status_code, 302)
        self.assertIn("do not have access", " ".join(self.texts(response)))

    def test_wrong_namespace_goes_home_not_to_the_page(self):
        response = self.go(self.coord, "report_list", "superadmin")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("/superadmin/tithes/", response["Location"])

    def test_report_link_from_a_notification_is_forwarded_to_the_readers_area(self):
        """`/tithes/<id>/` (written without a role) lands an admin on their own report page."""
        response = self.go(self.admin, "report_detail", "tithes", self.report.pk)
        self.assertRedirects(
            response, reverse("admin_area:report_detail", args=[self.report.pk]), fetch_redirect_response=False,
        )

    def test_report_out_of_reach_looks_missing(self):
        response = self.go(self.other_coord, "report_detail", "coordinator_area", self.report.pk)
        self.assertRedirects(response, reverse("coordinator_area:report_list"), fetch_redirect_response=False)
        self.assertIn("no longer exists", " ".join(self.texts(response)))

    def test_workspace_opens_for_coordinator_and_seat_holder(self):
        self.assertEqual(self.go(self.coord, "report_detail", "coordinator_area", self.report.pk).status_code, 200)
        self.assertEqual(self.go(self.seat, "report_detail", "tithes", self.report.pk).status_code, 200)

    def test_pending_requests_is_for_admins_only(self):
        self.assertEqual(self.go(self.admin, "pending_requests", "admin_area").status_code, 200)
        self.assertEqual(self.go(self.coord, "pending_requests", "coordinator_area").status_code, 302)

    def test_households_are_closed_to_a_special_member(self):
        self.assertEqual(self.go(self.seat, "household_list", "tithes").status_code, 302)
        self.assertEqual(self.go(self.coord, "household_list", "coordinator_area").status_code, 200)

    # ---- actions
    def post(self, user, name, namespace, pk, data):
        """POST url `name` as `user`."""
        self.client.force_login(user)
        return self.client.post(reverse(f"{namespace}:{name}", args=[pk]), data)

    def test_special_member_may_edit_the_offering(self):
        self.post(self.seat, "report_offering", "tithes", self.report.pk, {"amount": "150.50"})
        self.report.refresh_from_db()
        self.assertEqual(str(self.report.offering_amount), "150.50")

    def test_bad_amount_is_a_toast_not_a_crash(self):
        response = self.post(self.coord, "report_offering", "coordinator_area", self.report.pk, {"amount": "abc"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("valid offering", " ".join(self.texts(response)))

    def test_special_member_cannot_finalize_or_decide(self):
        self.post(self.coord, "report_offering", "coordinator_area", self.report.pk, {"amount": "100"})
        self.post(self.coord, "report_breakdown", "coordinator_area", self.report.pk, {"count_100": "1"})
        response = self.post(self.seat, "report_finalize", "tithes", self.report.pk,
                             {"submit_token": new_submission_token()})
        self.assertIn("cannot do that", " ".join(self.texts(response)))
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, TithesOffering.Status.DRAFT)

    def test_finalize_token_replay_does_nothing_twice(self):
        self.post(self.coord, "report_offering", "coordinator_area", self.report.pk, {"amount": "100"})
        self.post(self.coord, "report_breakdown", "coordinator_area", self.report.pk, {"count_100": "1"})
        token = new_submission_token()
        self.post(self.coord, "report_finalize", "coordinator_area", self.report.pk, {"submit_token": token})
        again = self.post(self.coord, "report_finalize", "coordinator_area", self.report.pk, {"submit_token": token})
        self.assertIn("already submitted", " ".join(self.texts(again)))
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, TithesOffering.Status.FINALIZED)

    def test_mismatch_blocks_finalize_for_the_coordinator(self):
        self.post(self.coord, "report_offering", "coordinator_area", self.report.pk, {"amount": "100"})
        self.post(self.coord, "report_breakdown", "coordinator_area", self.report.pk, {"count_50": "1"})
        response = self.post(self.coord, "report_finalize", "coordinator_area", self.report.pk,
                             {"submit_token": new_submission_token()})
        self.assertIn("short", " ".join(self.texts(response)))

    def test_open_request_locks_the_draft_for_the_coordinator_but_not_the_admin(self):
        self.post(self.coord, "report_offering", "coordinator_area", self.report.pk, {"amount": "100"})
        self.post(self.coord, "report_breakdown", "coordinator_area", self.report.pk, {"count_50": "1"})
        self.post(self.coord, "bypass_request", "coordinator_area", self.report.pk,
                  {"message": "Short by 50", "submit_token": new_submission_token()})
        self.assertTrue(TitheBypassRequest.objects.filter(tithes_offering=self.report, status="pending").exists())
        locked = self.post(self.coord, "report_offering", "coordinator_area", self.report.pk, {"amount": "200"})
        self.assertIn("locked", " ".join(self.texts(locked)))
        self.post(self.admin, "report_offering", "admin_area", self.report.pk, {"amount": "200"})
        self.report.refresh_from_db()
        self.assertEqual(str(self.report.offering_amount), "200.00")
        # The admin's edit changed the difference, so the request is voided.
        self.assertTrue(TitheBypassRequest.objects.filter(tithes_offering=self.report, status="voided").exists())

    def test_only_the_coordinator_may_request_a_bypass(self):
        self.post(self.coord, "report_offering", "coordinator_area", self.report.pk, {"amount": "100"})
        self.post(self.admin, "bypass_request", "admin_area", self.report.pk,
                  {"message": "x", "submit_token": new_submission_token()})
        self.assertFalse(TitheBypassRequest.objects.exists())
