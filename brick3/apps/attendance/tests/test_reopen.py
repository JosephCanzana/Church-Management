"""Tests for attendance.services.reopen_sheet (brick 7): who may reopen, what it clears and logs, the alert."""
from datetime import date

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Extension, Gender, Role, SpecialRole, Status, User, UserSpecialRole
from apps.attendance import services
from apps.attendance.age import AgeNetwork
from apps.attendance.models import Attendance
from apps.audit.models import AuditLog
from apps.core.services import ServiceError, new_submission_token  # adjust if the helper lives elsewhere
from apps.dashboards import services as dashboard_services
from apps.notifications.models import Notification, NotificationRule


def person(role, extension=None, name="x"):
    """An activated person who can sign in."""
    return User.objects.create_user(
        password="pass-12345", first_name=name, last_name=role.replace("_", ""), role=role,
        extension=extension, status=Status.ACTIVE, must_change_password=False,
    )


class ReopenTests(TestCase):
    """Alpha has a coordinator; two admins and a super-admin exist, so the alert has recipients."""

    def setUp(self):
        self.a = Extension.objects.create(name="alpha")
        self.coord = person(Role.COORDINATOR, self.a, name="coord")
        self.a.coordinator = self.coord
        self.a.save()
        self.admin = person(Role.ADMIN, name="adm")
        self.admin2 = person(Role.ADMIN, name="adm2")
        self.super_admin = person(Role.SUPER_ADMIN, name="sup")
        self.member1 = person(Role.MEMBER, self.a, name="m1")
        self.member2 = person(Role.MEMBER, self.a, name="m2")
        role, _ = SpecialRole.objects.get_or_create(code="attendance", defaults={"name": "Attendance"})
        self.special = person(Role.MEMBER, self.a, name="spec")
        UserSpecialRole.objects.create(user=self.special, role=role, extension=self.a)
        self.sheet = self.uploaded_sheet()

    def uploaded_sheet(self):
        """A sheet with two members ticked, uploaded by the coordinator through the real services."""
        sheet = services.create_sheet(self.coord, self.a, date(2026, 9, 6))
        self.row1, _ = services.mark_present(
            self.coord, sheet, self.member1, gender=Gender.MALE, age_network=AgeNetwork.ADULT)
        self.row2, _ = services.mark_present(
            self.coord, sheet, self.member2, gender=Gender.FEMALE, age_network=AgeNetwork.YOUTH)
        services.upload_sheet(self.coord, sheet.pk, token=new_submission_token())
        sheet.refresh_from_db()
        return sheet

    def reopen(self, actor=None, sheet=None):
        """Reopen with a fresh form token."""
        return services.reopen_sheet((actor or self.admin), (sheet or self.sheet).pk, token=new_submission_token())

    # --- who may ------------------------------------------------------------
    def test_admin_and_super_admin_can_reopen(self):
        for actor in (self.admin, self.super_admin):
            sheet = Attendance.objects.get(pk=self.sheet.pk)
            if sheet.status != Attendance.Status.UPLOADED:
                services.upload_sheet(self.coord, sheet.pk, token=new_submission_token())
            result = self.reopen(actor)
            self.assertEqual(result.sheet.status, Attendance.Status.DRAFT)

    def test_coordinator_special_member_and_plain_member_are_refused(self):
        for actor in (self.coord, self.special, self.member1):
            with self.assertRaises(PermissionDenied, msg=actor):
                self.reopen(actor)
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, Attendance.Status.UPLOADED)

    def test_a_draft_cannot_be_reopened(self):
        draft = services.create_sheet(self.coord, self.a, date(2026, 9, 13))
        with self.assertRaises(ServiceError):
            self.reopen(self.admin, draft)

    # --- what it does ------------------------------------------------------------
    def test_reopen_returns_to_draft_and_clears_the_frozen_values(self):
        self.reopen()
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, Attendance.Status.DRAFT)
        for field in ("members_present", "first_timers_count", "total_present", "summary",
                      "uploaded_by", "uploaded_at"):
            self.assertIsNone(getattr(self.sheet, field), field)
        self.assertEqual(self.sheet.members.count(), 2)          # the ticks stay
        self.assertEqual(self.sheet.taken_by, self.coord)

    def test_last_attended_at_is_not_rolled_back(self):
        before = User.objects.get(pk=self.member1.pk).last_attended_at
        self.assertIsNotNone(before)
        self.reopen()
        self.assertEqual(User.objects.get(pk=self.member1.pk).last_attended_at, before)

    def test_a_double_submit_reopens_once(self):
        token = new_submission_token()
        first = services.reopen_sheet(self.admin, self.sheet.pk, token=token)
        again = services.reopen_sheet(self.admin, self.sheet.pk, token=token)
        self.assertFalse(first.duplicate)
        self.assertTrue(again.duplicate)
        self.assertEqual(AuditLog.objects.filter(action="attendance.reopen").count(), 1)

    def test_a_refusal_gives_the_token_back(self):
        token = new_submission_token()
        with self.assertRaises(PermissionDenied):
            services.reopen_sheet(self.coord, self.sheet.pk, token=token)
        result = services.reopen_sheet(self.admin, self.sheet.pk, token=token)   # same form, allowed person
        self.assertFalse(result.duplicate)

    # --- audit and alert --------------------------------------------------------------
    def test_reopen_is_logged_with_counts_only(self):
        self.reopen()
        entry = AuditLog.objects.get(action="attendance.reopen")
        self.assertEqual((entry.entity_type, entry.entity_id), ("attendance", self.sheet.pk))
        self.assertEqual(entry.actor_id, self.admin.pk)
        self.assertEqual(entry.before["total_present"], 2)
        text = f"{entry.before} {entry.after}".lower()
        for word in ("m1", "m2", "spec", "coord"):
            self.assertNotIn(word, text)

    def test_reopen_alerts_the_other_admins_and_super_admin(self):
        self.reopen()
        alerted = set(Notification.objects.filter(type="admin_alert").values_list("user_id", flat=True))
        self.assertEqual(alerted, {self.admin2.pk, self.super_admin.pk})   # not the actor, not the coordinator
        item = Notification.objects.get(user=self.admin2, type="admin_alert")
        self.assertEqual(item.data, {"action": "attendance.reopen"})
        self.assertEqual((item.entity_type, item.entity_id), ("attendance", self.sheet.pk))
        self.assertEqual(item.importance, Notification.Importance.IMPORTANT)

    def test_a_disabled_rule_sends_no_alert(self):
        NotificationRule.objects.filter(audit_action="attendance.reopen").update(is_enabled=False)
        self.reopen()
        self.assertFalse(Notification.objects.filter(type="admin_alert").exists())

    # --- the flow the plan asks for -----------------------------------------------------
    def test_reopen_change_one_tick_and_upload_again_gives_the_corrected_numbers(self):
        self.reopen()
        services.unmark_present(self.coord, self.sheet, self.row2.pk)
        services.upload_sheet(self.coord, self.sheet.pk, token=new_submission_token())
        self.sheet.refresh_from_db()
        self.assertEqual(self.sheet.status, Attendance.Status.UPLOADED)
        self.assertEqual((self.sheet.members_present, self.sheet.total_present), (1, 1))
        self.assertEqual(self.sheet.uploaded_by, self.coord)

    def test_the_sheet_leaves_the_dashboard_until_it_is_uploaded_again(self):
        self.assertIn(self.sheet, set(dashboard_services._scope_attendance(self.admin)))
        self.reopen()
        self.assertNotIn(self.sheet, set(dashboard_services._scope_attendance(self.admin)))
        services.upload_sheet(self.coord, self.sheet.pk, token=new_submission_token())
        self.assertIn(self.sheet, set(dashboard_services._scope_attendance(self.admin)))
