"""The role table of tithes, checked cell by cell, plus `for_user` and "unreachable looks missing"."""
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from apps.accounts.models import PersonType, Role, Status
from apps.attendance.models import Attendance
from apps.tithes import permissions as perms
from apps.tithes.models import TitheBypassRequest, TithesOffering

from .helpers import (
    finalized, grant_seat, make_extension, make_report, make_request, make_sheet, make_user,
)

ADMINS = {"super_admin", "admin"}
RequestStatus = TitheBypassRequest.Status


class TithesPermissionTestCase(TestCase):
    """Builds two extensions, every kind of actor and one report per situation."""

    @classmethod
    def setUpTestData(cls):
        cls.ext_a = make_extension("A")
        cls.ext_b = make_extension("B")
        a, b = cls.ext_a, cls.ext_b

        seat_a = make_user(extension=a)
        grant_seat(seat_a, a)
        seat_b = make_user(extension=b)
        grant_seat(seat_b, b)
        attendance_seat = make_user(extension=a)
        grant_seat(attendance_seat, a, code="attendance")          # another special role: no tithes access
        suspended_seat = make_user(extension=a, status=Status.SUSPENDED)
        grant_seat(suspended_seat, a)
        guest_seat = make_user(extension=a, person_type=PersonType.GUEST)
        grant_seat(guest_seat, a)                                  # a guest never holds a seat in practice
        seat_in_b_only_for_a_member = make_user(extension=a)       # lives in A, seat is for B
        grant_seat(seat_in_b_only_for_a_member, b)

        cls.actors = {
            "super_admin": make_user(role=Role.SUPER_ADMIN),
            "admin": make_user(role=Role.ADMIN),
            "coord_a": make_user(extension=a, role=Role.COORDINATOR),
            "coord_b": make_user(extension=b, role=Role.COORDINATOR),
            "seat_a": seat_a,
            "seat_b": seat_b,
            "seat_for_b_lives_in_a": seat_in_b_only_for_a_member,
            "plain_a": make_user(extension=a),
            "attendance_seat_a": attendance_seat,
            "suspended_seat_a": suspended_seat,
            "guest_seat_a": guest_seat,
            "anonymous": AnonymousUser(),
            "none": None,
        }
        cls.sheet_a = make_sheet(a)
        cls.sheet_b = make_sheet(b)
        cls.sheet_a_archived = make_sheet(a, status=Attendance.Status.ARCHIVED)
        cls.reports = {
            "draft_a": make_report(cls.sheet_a),
            "final_a": make_report(make_sheet(a), **finalized()),
            "draft_b": make_report(cls.sheet_b),
            "final_b": make_report(make_sheet(b), **finalized()),
            "draft_a_archived": make_report(cls.sheet_a_archived),
            "final_a_archived": make_report(make_sheet(a, status=Attendance.Status.ARCHIVED), **finalized()),
        }

    def assert_matrix(self, check, expected):
        """For every report named in `expected`, exactly the named actors pass `check`."""
        for report_name, allowed in expected.items():
            for actor_name, actor in self.actors.items():
                with self.subTest(check=check.__name__, report=report_name, actor=actor_name):
                    self.assertEqual(check(actor, self.reports[report_name]), actor_name in allowed)


class RoleTableTests(TithesPermissionTestCase):
    """One test per row of the role table (the reports above cover draft, finalized, other extension, archived sheet)."""

    def test_view(self):
        self.assert_matrix(perms.can_view, {
            "draft_a": ADMINS | {"coord_a", "seat_a"},
            "final_a": ADMINS | {"coord_a"},                    # a special member never sees a finalized report
            "draft_b": ADMINS | {"coord_b", "seat_b", "seat_for_b_lives_in_a"},
            "final_b": ADMINS | {"coord_b"},
            "draft_a_archived": ADMINS | {"coord_a", "seat_a"},  # read-only, but still readable
            "final_a_archived": ADMINS | {"coord_a"},
        })

    def test_print_is_view(self):
        self.assert_matrix(perms.can_print, {
            "draft_a": ADMINS | {"coord_a", "seat_a"},          # special member: drafts only
            "final_a": ADMINS | {"coord_a"},
            "draft_b": ADMINS | {"coord_b", "seat_b", "seat_for_b_lives_in_a"},
            "final_a_archived": ADMINS | {"coord_a"},
        })

    def test_review_never_on_an_archived_sheet(self):
        self.assert_matrix(perms.can_review, {
            "draft_a": ADMINS | {"coord_a", "seat_a"},
            "final_a": ADMINS | {"coord_a"},
            "draft_b": ADMINS | {"coord_b", "seat_b", "seat_for_b_lives_in_a"},
            "draft_a_archived": set(),
            "final_a_archived": set(),
        })

    def test_edit_draft(self):
        self.assert_matrix(perms.can_edit_draft, {
            "draft_a": ADMINS | {"coord_a", "seat_a"},
            "final_a": set(),
            "draft_b": ADMINS | {"coord_b", "seat_b", "seat_for_b_lives_in_a"},
            "final_b": set(),
            "draft_a_archived": set(),
            "final_a_archived": set(),
        })

    def test_finalize_is_coordinator_or_admin_never_special_member(self):
        self.assert_matrix(perms.can_finalize, {
            "draft_a": ADMINS | {"coord_a"},
            "final_a": set(),
            "draft_b": ADMINS | {"coord_b"},
            "draft_a_archived": set(),
            "final_a_archived": set(),
        })

    def test_reopen_is_admin_only(self):
        self.assert_matrix(perms.can_reopen, {
            "final_a": ADMINS,
            "final_b": ADMINS,
            "draft_a": set(),
            "draft_b": set(),
            "final_a_archived": set(),
        })

    def test_request_bypass_is_only_the_coordinator(self):
        self.assert_matrix(perms.can_request_bypass, {
            "draft_a": {"coord_a"},                              # not even an admin
            "draft_b": {"coord_b"},
            "final_a": set(),
            "draft_a_archived": set(),
        })

    def test_decide_bypass_is_admin_only(self):
        self.assert_matrix(perms.can_decide_bypass, {
            "draft_a": ADMINS,
            "draft_b": ADMINS,
            "final_a": set(),
            "draft_a_archived": set(),
        })

    def test_create_a_report_for_a_sheet(self):
        for name, actor in self.actors.items():
            for sheet_name, sheet, allowed in (
                ("sheet_a", self.sheet_a, ADMINS | {"coord_a", "seat_a"}),
                ("sheet_b", self.sheet_b, ADMINS | {"coord_b", "seat_b", "seat_for_b_lives_in_a"}),
                ("sheet_a_archived", self.sheet_a_archived, set()),
            ):
                with self.subTest(actor=name, sheet=sheet_name):
                    self.assertEqual(perms.can_create(actor, sheet), name in allowed)

    def test_create_needs_a_sheet(self):
        self.assertFalse(perms.can_create(self.actors["admin"], None))

    def test_manage_households(self):
        for name, actor in self.actors.items():
            for label, extension_id, allowed in (
                ("ext_a", self.ext_a.pk, ADMINS | {"coord_a"}),
                ("ext_b", self.ext_b.pk, ADMINS | {"coord_b"}),
                ("none", None, set()),
            ):
                with self.subTest(actor=name, extension=label):
                    self.assertEqual(perms.can_manage_households(actor, extension_id), name in allowed)

    def test_use_area(self):
        allowed = ADMINS | {"coord_a", "coord_b", "seat_a", "seat_b", "seat_for_b_lives_in_a"}
        for name, actor in self.actors.items():
            with self.subTest(actor=name):
                self.assertEqual(perms.can_use_area(actor), name in allowed)

    def test_seat_is_per_extension_and_per_role(self):
        seat_a = self.actors["seat_a"]
        self.assertTrue(perms.has_tithes_role(seat_a, self.ext_a.pk))
        self.assertFalse(perms.has_tithes_role(seat_a, self.ext_b.pk))
        self.assertFalse(perms.has_tithes_role(seat_a, None))
        self.assertFalse(perms.has_tithes_role(self.actors["attendance_seat_a"], self.ext_a.pk))
        self.assertFalse(perms.has_tithes_role(self.actors["suspended_seat_a"], self.ext_a.pk))
        self.assertFalse(perms.has_tithes_role(self.actors["guest_seat_a"], self.ext_a.pk))
        self.assertFalse(perms.has_tithes_role(None, self.ext_a.pk))


class BypassLockTests(TithesPermissionTestCase):
    """While a request is pending or approved the draft is locked for everyone but an admin."""

    def lock(self, status):
        """Open (or close) a request on draft_a and return the report."""
        report = self.reports["draft_a"]
        make_request(report, status=status)
        return report

    def test_open_request_locks_the_draft_for_the_coordinator_and_the_special_member(self):
        for status in (RequestStatus.PENDING, RequestStatus.APPROVED):
            with self.subTest(status=status):
                report = self.lock(status)
                for name in ("coord_a", "seat_a"):
                    self.assertFalse(perms.can_edit_draft(self.actors[name], report), name)
                for name in ADMINS:
                    self.assertTrue(perms.can_edit_draft(self.actors[name], report), name)
                report.bypass_requests.all().delete()

    def test_closed_request_does_not_lock(self):
        for status in (RequestStatus.REJECTED, RequestStatus.WITHDRAWN, RequestStatus.VOIDED, RequestStatus.USED):
            with self.subTest(status=status):
                report = self.lock(status)
                for name in ADMINS | {"coord_a", "seat_a"}:
                    self.assertTrue(perms.can_edit_draft(self.actors[name], report), name)
                report.bypass_requests.all().delete()

    def test_lock_does_not_touch_other_reports(self):
        self.lock(RequestStatus.PENDING)
        self.assertTrue(perms.can_edit_draft(self.actors["coord_b"], self.reports["draft_b"]))

    def test_coordinator_can_still_withdraw_finalize_and_view_while_locked(self):
        report = self.lock(RequestStatus.APPROVED)
        coordinator = self.actors["coord_a"]
        self.assertTrue(perms.can_request_bypass(coordinator, report))   # also covers withdrawing
        self.assertTrue(perms.can_finalize(coordinator, report))         # finalize with the approval
        self.assertTrue(perms.can_view(coordinator, report))
        self.assertTrue(perms.can_view(self.actors["seat_a"], report))
        self.assertFalse(perms.can_finalize(self.actors["seat_a"], report))


class ForUserTests(TithesPermissionTestCase):
    """`TithesOffering.objects.for_user` is the only scoping and agrees with `can_view` everywhere."""

    def ids(self, actor_name):
        """Names of the reports `for_user` returns for this actor."""
        wanted = set(TithesOffering.objects.for_user(self.actors[actor_name]).values_list("pk", flat=True))
        return {name for name, report in self.reports.items() if report.pk in wanted}

    def test_super_admin_and_admin_see_everything(self):
        for name in ADMINS:
            self.assertEqual(self.ids(name), set(self.reports))

    def test_coordinator_sees_their_extension_only(self):
        self.assertEqual(self.ids("coord_a"), {"draft_a", "final_a", "draft_a_archived", "final_a_archived"})
        self.assertEqual(self.ids("coord_b"), {"draft_b", "final_b"})

    def test_special_member_sees_drafts_of_the_seat_extension_only(self):
        self.assertEqual(self.ids("seat_a"), {"draft_a", "draft_a_archived"})
        self.assertEqual(self.ids("seat_b"), {"draft_b"})
        # the seat decides, not the extension the person lives in
        self.assertEqual(self.ids("seat_for_b_lives_in_a"), {"draft_b"})

    def test_everyone_else_sees_nothing(self):
        for name in ("plain_a", "attendance_seat_a", "suspended_seat_a", "guest_seat_a", "anonymous", "none"):
            with self.subTest(actor=name):
                self.assertEqual(self.ids(name), set())

    def test_for_user_and_can_view_agree_for_every_actor_and_report(self):
        for actor_name, actor in self.actors.items():
            visible = set(TithesOffering.objects.for_user(actor).values_list("pk", flat=True))
            for report_name, report in self.reports.items():
                with self.subTest(actor=actor_name, report=report_name):
                    self.assertEqual(report.pk in visible, perms.can_view(actor, report))

    def test_an_unreachable_report_looks_exactly_like_a_missing_one(self):
        report = self.reports["draft_b"]
        for name in ("coord_a", "seat_a", "plain_a", "anonymous"):
            scoped = TithesOffering.objects.for_user(self.actors[name])
            with self.subTest(actor=name):
                self.assertFalse(scoped.filter(pk=report.pk).exists())
                with self.assertRaises(TithesOffering.DoesNotExist) as unreachable:
                    scoped.get(pk=report.pk)
                with self.assertRaises(TithesOffering.DoesNotExist) as missing:
                    scoped.get(pk=0)
                self.assertEqual(
                    str(unreachable.exception).replace(str(report.pk), "N"),
                    str(missing.exception).replace("0", "N"),
                )

    def test_a_special_member_loses_a_report_when_it_is_finalized(self):
        report = self.reports["draft_a"]
        seat = self.actors["seat_a"]
        self.assertTrue(TithesOffering.objects.for_user(seat).filter(pk=report.pk).exists())
        TithesOffering.objects.filter(pk=report.pk).update(**finalized())
        self.assertFalse(TithesOffering.objects.for_user(seat).filter(pk=report.pk).exists())
        TithesOffering.objects.filter(pk=report.pk).update(
            status=TithesOffering.Status.DRAFT, tithers_count=None, tithes_total=None, total_amount=None,
            breakdown_total=None, difference=None, finalized_at=None,
        )

    def test_sheet_is_loaded_with_the_report(self):
        """The permission checks read `report.attendance`; for_user fetches it in the same query."""
        report = TithesOffering.objects.for_user(self.actors["admin"]).get(pk=self.reports["draft_a"].pk)
        with self.assertNumQueries(0):
            report.attendance.extension_id


class RequireTests(TithesPermissionTestCase):
    """`require` raises PermissionDenied when the check fails and returns quietly otherwise."""

    def test_denied(self):
        with self.assertRaises(PermissionDenied):
            perms.require(perms.can_finalize, self.actors["seat_a"], self.reports["draft_a"])

    def test_allowed(self):
        perms.require(perms.can_finalize, self.actors["coord_a"], self.reports["draft_a"])

    def test_denied_for_nobody(self):
        with self.assertRaises(PermissionDenied):
            perms.require(perms.can_reopen, None, self.reports["final_a"])

    def test_create_takes_the_sheet(self):
        perms.require(perms.can_create, self.actors["seat_a"], self.sheet_a)
        with self.assertRaises(PermissionDenied):
            perms.require(perms.can_create, self.actors["seat_a"], self.sheet_b)

    def test_households_take_the_extension_id(self):
        perms.require(perms.can_manage_households, self.actors["coord_a"], self.ext_a.pk)
        with self.assertRaises(PermissionDenied):
            perms.require(perms.can_manage_households, self.actors["coord_a"], self.ext_b.pk)
        with self.assertRaises(PermissionDenied):
            perms.require(perms.can_manage_households, self.actors["admin"], None)
