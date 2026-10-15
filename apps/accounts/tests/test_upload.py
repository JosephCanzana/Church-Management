"""Tests for attendance.services.upload_sheet: locking, frozen numbers, refusals, double submits."""
from datetime import date, timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import (
    Extension, Gender, Role, SpecialRole, Status, User, UserExtensionHistory, UserSpecialRole,
)
from apps.attendance import services
from apps.attendance.age import AgeNetwork
from apps.attendance.models import Attendance
from apps.attendance.summary import build_summary, summary_for
from apps.core.services import ServiceError, new_submission_token
from apps.dashboards import services as dashboard_services

DAY = date(2026, 6, 14)


def person(role, extension=None, first="x", complete=True, **extra):
    """An activated person; `complete` gives them a gender and a birth date so their tick needs nothing more."""
    if complete and role == Role.MEMBER:
        extra.setdefault("gender", Gender.MALE)
        extra.setdefault("birth_date", date(1990, 1, 1))
    return User.objects.create_user(
        password="pass-12345", first_name=first, last_name="tester", role=role, extension=extension,
        status=Status.ACTIVE, must_change_password=False, **extra,
    )


class UploadTests(TestCase):
    def setUp(self):
        self.a = Extension.objects.create(name="alpha")
        self.b = Extension.objects.create(name="beta")
        self.coord = person(Role.COORDINATOR, self.a, first="coord")
        self.a.coordinator = self.coord
        self.a.save()
        self.coord_b = person(Role.COORDINATOR, self.b, first="coordb")
        self.admin = person(Role.ADMIN, first="adm")
        seat, _ = SpecialRole.objects.get_or_create(code="attendance", defaults={"name": "Attendance"})
        self.special = person(Role.MEMBER, self.a, first="special")
        UserSpecialRole.objects.create(user=self.special, role=seat, extension=self.a)
        self.plain = person(Role.MEMBER, self.a, first="plain")

        self.one = person(Role.MEMBER, self.a, first="one")
        self.two = person(Role.MEMBER, self.a, first="two", last_attended_at=date(2026, 9, 6))
        self.visitor = person(Role.MEMBER, self.b, first="visitor")
        self.sheet = services.create_sheet(self.coord, self.a, DAY, "Sunday Service")
        for who in (self.one, self.two, self.visitor):
            services.mark_present(self.coord, self.sheet, who)

    def upload(self, actor=None, sheet=None, token=None):
        """Upload with a fresh token unless one is given."""
        return services.upload_sheet(actor or self.coord, (sheet or self.sheet).pk, token=token or new_submission_token())

    def add_first_timer(self, first="Ana", sheet=None):
        """A first timer who needs nothing more."""
        return services.add_first_timer(
            self.coord, sheet or self.sheet, first, "Lopez", Gender.FEMALE, AgeNetwork.YOUTH).first_timer

    # --- the happy path ---------------------------------------------------
    def test_coordinator_uploads_and_the_sheet_is_locked_with_frozen_numbers(self):
        self.add_first_timer()
        preview = build_summary(self.sheet)
        result = self.upload()
        self.assertFalse(result.duplicate)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, Attendance.Status.UPLOADED)
        self.assertTrue(self.sheet.is_locked)
        self.assertEqual((self.sheet.uploaded_by, self.sheet.taken_by), (self.coord, self.coord))
        self.assertIsNotNone(self.sheet.uploaded_at)
        self.assertEqual(
            (self.sheet.members_present, self.sheet.first_timers_count, self.sheet.total_present), (3, 1, 4))
        self.assertEqual(self.sheet.summary, preview.summary)                 # the draft preview equals the frozen value
        self.assertEqual(summary_for(self.sheet).total_present, 4)

    def test_frozen_numbers_do_not_follow_later_changes_to_people(self):
        self.upload()
        frozen = Attendance.objects.get(pk=self.sheet.pk).summary
        User.objects.filter(pk=self.one.pk).update(gender=Gender.FEMALE, birth_date=date(2015, 1, 1))
        self.assertEqual(Attendance.objects.get(pk=self.sheet.pk).summary, frozen)
        self.assertEqual(build_summary(self.sheet).summary, frozen)

    def test_a_sheet_with_only_first_timers_can_be_uploaded(self):
        sheet = services.create_sheet(self.coord, self.a, date(2026, 6, 7), "Sunday Service")
        self.add_first_timer(sheet=sheet)
        self.upload(sheet=sheet)
        sheet.refresh_from_db()
        self.assertEqual((sheet.members_present, sheet.first_timers_count, sheet.total_present), (0, 1, 1))

    def test_uploaded_numbers_reach_the_dashboard_trend(self):
        self.upload()
        rows = dashboard_services._attendance_trend(self.admin)
        self.assertEqual([(r["attendance_date"], r["present"]) for r in rows], [(DAY, 3)])

    def test_a_ticked_visitor_is_counted(self):
        self.upload()
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.members_present, 3)

    def test_repeat_visitor_moves_after_the_third_uploaded_visit(self):
        seat = SpecialRole.objects.get(code="attendance")
        UserSpecialRole.objects.create(user=self.visitor, role=seat, extension=self.b)
        for visit_number in range(2):
            earlier = services.create_sheet(
                self.coord, self.a, date(2026, 6, 7) - timedelta(days=visit_number * 7),
                f"Earlier Service {visit_number}",
            )
            services.mark_present(self.coord, earlier, self.visitor)
            services.upload_sheet(self.coord, earlier.pk, token=new_submission_token())

        self.upload()

        self.visitor.refresh_from_db()
        self.assertEqual(self.visitor.extension_id, self.a.pk)
        history = UserExtensionHistory.objects.filter(user=self.visitor).latest("pk")
        self.assertEqual(history.reason, UserExtensionHistory.Reason.AUTO_TRANSFER)
        self.assertEqual(history.extension_id, self.a.pk)
        self.assertFalse(UserSpecialRole.objects.filter(user=self.visitor).exists())

    def test_one_uploaded_visit_does_not_move_a_visitor(self):
        self.upload()
        self.visitor.refresh_from_db()
        self.assertEqual(self.visitor.extension_id, self.b.pk)
        self.assertFalse(UserExtensionHistory.objects.filter(
            user=self.visitor, reason=UserExtensionHistory.Reason.AUTO_TRANSFER,
        ).exists())

    # --- who may upload ---------------------------------------------------
    def test_only_the_coordinator_of_that_extension_may_upload(self):
        for actor in (self.special, self.plain, self.coord_b):
            with self.assertRaises(PermissionDenied, msg=actor):
                self.upload(actor=actor)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, Attendance.Status.DRAFT)
        self.assertIsNone(self.sheet.total_present)

    # --- refusals ---------------------------------------------------------
    def test_an_incomplete_sheet_is_refused_and_nothing_is_written(self):
        blank = person(Role.MEMBER, self.a, first="blank", complete=False)
        services.mark_present(self.coord, self.sheet, blank)
        with self.assertRaises(ServiceError) as caught:
            self.upload()
        self.assertIn("1 person still needs", str(caught.exception))
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, Attendance.Status.DRAFT)
        self.assertIsNone(self.sheet.summary)
        self.one.refresh_from_db()
        self.assertIsNone(self.one.last_attended_at)

    def test_the_refusal_names_how_many(self):
        for n in range(2):
            services.mark_present(self.coord, self.sheet, person(Role.MEMBER, self.a, first=f"blank{n}", complete=False))
        with self.assertRaises(ServiceError) as caught:
            self.upload()
        self.assertIn("2 people still need", str(caught.exception))

    def test_filling_in_the_category_lets_the_upload_through(self):
        blank = person(Role.MEMBER, self.a, first="blank", complete=False)
        row, _ = services.mark_present(self.coord, self.sheet, blank)
        services.set_member_category(self.coord, self.sheet, row.pk, Gender.FEMALE, AgeNetwork.ADULT)
        self.upload()

    def test_an_empty_sheet_is_refused(self):
        empty = services.create_sheet(self.coord, self.a, date(2026, 6, 7), "Sunday Service")
        with self.assertRaises(ServiceError):
            self.upload(sheet=empty)

    def test_an_unknown_sheet_is_a_friendly_error(self):
        with self.assertRaises(ServiceError):
            services.upload_sheet(self.coord, 999999, token=new_submission_token())

    def test_ticks_and_first_timers_are_refused_after_the_upload(self):
        self.upload()
        newcomer = person(Role.MEMBER, self.a, first="late")
        with self.assertRaises(ServiceError):
            services.mark_present(self.coord, self.sheet, newcomer)
        with self.assertRaises(ServiceError):
            self.add_first_timer("Late")

    # --- last_attended_at -------------------------------------------------
    def test_last_attended_moves_forward_and_never_back(self):
        self.upload()
        for who, expected in ((self.one, DAY), (self.visitor, DAY), (self.two, date(2026, 9, 6))):
            who.refresh_from_db()
            self.assertEqual(who.last_attended_at, expected, who.first_name)

    def test_people_who_were_not_ticked_are_untouched(self):
        self.upload()
        self.plain.refresh_from_db()
        self.assertIsNone(self.plain.last_attended_at)

    def test_a_future_dated_sheet_never_sets_a_future_attendance_date(self):
        future = timezone.localdate() + timedelta(days=7)
        sheet = services.create_sheet(self.coord, self.a, future, "Sunday Service")
        services.mark_present(self.coord, sheet, self.plain)
        self.upload(sheet=sheet)
        self.plain.refresh_from_db()
        self.assertEqual(self.plain.last_attended_at, timezone.localdate())

    # --- double submit ----------------------------------------------------
    def test_a_double_submit_uploads_once(self):
        token = new_submission_token()
        with mock.patch.object(services, "log_action") as log:
            first = self.upload(token=token)
            self.sheet.refresh_from_db()
            uploaded_at = self.sheet.uploaded_at
            second = self.upload(token=token)
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(second.sheet.pk, self.sheet.pk)
        self.assertEqual([c.args[0] for c in log.call_args_list].count("attendance.upload"), 1)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.uploaded_at, uploaded_at)

    def test_a_second_upload_with_a_new_token_is_refused(self):
        self.upload()
        with self.assertRaises(ServiceError):
            self.upload()

    def test_a_refused_upload_frees_the_token_so_the_same_form_can_be_sent_again(self):
        blank = person(Role.MEMBER, self.a, first="blank", complete=False)
        row, _ = services.mark_present(self.coord, self.sheet, blank)
        token = new_submission_token()
        with self.assertRaises(ServiceError):
            self.upload(token=token)
        services.set_member_category(self.coord, self.sheet, row.pk, Gender.MALE, AgeNetwork.ADULT)
        self.assertFalse(self.upload(token=token).duplicate)

    def test_a_missing_token_is_refused(self):
        with self.assertRaises(ServiceError):
            services.upload_sheet(self.coord, self.sheet.pk, token="")

    # --- audit ------------------------------------------------------------
    def test_upload_is_logged_with_counts_only(self):
        self.add_first_timer("Ana")
        with mock.patch.object(services, "log_action") as log:
            self.upload()
        call = log.call_args
        self.assertEqual(call.args[0], "attendance.upload")
        self.assertEqual(call.kwargs["after"], {"members_present": 3, "first_timers_count": 1, "total_present": 4})
        self.assertNotIn("Ana", repr(call.kwargs))
        self.assertNotIn("tester", repr(call.kwargs))

    def test_upload_writes_a_real_audit_row(self):
        from apps.audit.models import AuditLog
        self.upload()
        self.assertEqual(AuditLog.objects.filter(action="attendance.upload").count(), 1)