"""Tests for attendance.permissions and Attendance.objects.for_user: roles by sheet status."""
from datetime import date

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Extension, Role, SpecialRole, Status, User, UserSpecialRole
from apps.attendance import permissions as perms
from apps.attendance.models import Attendance
from apps.dashboards import services as dashboard_services


def person(role, extension=None, status=Status.ACTIVE, name="x"):
    """An activated person (an archived one also needs `archived_at`, a database rule)."""
    extra = {"archived_at": timezone.now()} if status == Status.ARCHIVED else {}
    return User.objects.create_user(
        password="pass-12345", first_name=name, last_name=role.replace("_", ""), role=role,
        extension=extension, status=status, must_change_password=False, **extra,
    )


class PermissionMatrixTests(TestCase):
    """One sheet per (extension, status); every role is checked against every sheet."""

    def setUp(self):
        self.a = Extension.objects.create(name="alpha")
        self.b = Extension.objects.create(name="beta")
        self.role, _ = SpecialRole.objects.get_or_create(code="attendance", defaults={"name": "Attendance"})

        self.super_admin = person(Role.SUPER_ADMIN, name="sup")
        self.admin = person(Role.ADMIN, name="adm")
        self.coord_a = person(Role.COORDINATOR, self.a, name="ca")
        self.coord_b = person(Role.COORDINATOR, self.b, name="cb")
        self.a.coordinator = self.coord_a
        self.a.save()           # alpha has a coordinator, beta has none
        self.plain = person(Role.MEMBER, self.a, name="plain")
        self.special_a = self.special(self.a, name="sa")
        self.special_b = self.special(self.b, name="sb")
        self.suspended = self.special(self.a, status=Status.SUSPENDED, name="susp")
        self.archived = self.special(self.a, status=Status.ARCHIVED, name="arch")

        day = date(2026, 6, 14)
        self.draft_a = Attendance.objects.create(extension=self.a, attendance_date=day)
        self.up_a = Attendance.objects.create(
            extension=self.a, attendance_date=date(2026, 6, 7), status=Attendance.Status.UPLOADED)
        self.draft_b = Attendance.objects.create(extension=self.b, attendance_date=day)
        self.up_b = Attendance.objects.create(
            extension=self.b, attendance_date=date(2026, 6, 7), status=Attendance.Status.UPLOADED)
        self.sheets = [self.draft_a, self.up_a, self.draft_b, self.up_b]

    def special(self, extension, status=Status.ACTIVE, name="s"):
        """A member who holds the attendance seat in `extension`."""
        member = person(Role.MEMBER, extension, status=status, name=name)
        UserSpecialRole.objects.create(user=member, role=self.role, extension=extension)
        return member

    def visible(self, actor):
        """The sheets for_user returns, as a set."""
        return set(Attendance.objects.for_user(actor))

    # --- for_user ---------------------------------------------------------
    def test_admin_and_super_admin_see_every_sheet(self):
        for actor in (self.super_admin, self.admin):
            self.assertEqual(self.visible(actor), set(self.sheets))

    def test_coordinator_sees_only_their_extension_in_any_status(self):
        self.assertEqual(self.visible(self.coord_a), {self.draft_a, self.up_a})
        self.assertEqual(self.visible(self.coord_b), {self.draft_b, self.up_b})

    def test_special_member_sees_only_drafts_of_their_extension(self):
        self.assertEqual(self.visible(self.special_a), {self.draft_a})
        self.assertEqual(self.visible(self.special_b), {self.draft_b})

    def test_plain_member_suspended_archived_and_anonymous_see_nothing(self):
        for actor in (self.plain, self.suspended, self.archived, AnonymousUser(), None):
            self.assertEqual(self.visible(actor), set(), actor)

    def test_for_user_and_can_view_agree_for_everyone(self):
        actors = [self.super_admin, self.admin, self.coord_a, self.coord_b, self.plain,
                  self.special_a, self.special_b, self.suspended, self.archived, AnonymousUser()]
        for actor in actors:
            allowed = self.visible(actor)
            for sheet in self.sheets:
                self.assertEqual(perms.can_view(actor, sheet), sheet in allowed, (actor, sheet))

    # --- the permissions table ---------------------------------------------
    def test_edit_draft(self):
        yes = {self.super_admin, self.admin, self.coord_a, self.special_a}
        everyone = [self.super_admin, self.admin, self.coord_a, self.coord_b, self.plain,
                    self.special_a, self.special_b, self.suspended, self.archived]
        for actor in everyone:
            self.assertEqual(perms.can_edit_draft(actor, self.draft_a), actor in yes, actor)
        for actor in everyone:     # an uploaded sheet is locked for everybody
            self.assertFalse(perms.can_edit_draft(actor, self.up_a), actor)

    def test_review(self):
        self.assertTrue(perms.can_review(self.special_a, self.draft_a))
        self.assertFalse(perms.can_review(self.special_a, self.up_a))      # S: uploaded sheets are closed
        self.assertTrue(perms.can_review(self.coord_a, self.up_a))
        self.assertTrue(perms.can_review(self.admin, self.up_b))
        self.assertFalse(perms.can_review(self.coord_a, self.up_b))        # another extension

    def test_review_refuses_an_archived_sheet(self):
        gone = Attendance.objects.create(extension=self.a, attendance_date=date(2026, 5, 1))
        gone.status = Attendance.Status.ARCHIVED
        self.assertFalse(perms.can_review(self.admin, gone))

    def test_upload_is_the_coordinator_of_that_extension_on_a_draft(self):
        self.assertTrue(perms.can_upload(self.coord_a, self.draft_a))
        self.assertFalse(perms.can_upload(self.coord_a, self.up_a))        # not a draft
        self.assertFalse(perms.can_upload(self.coord_a, self.draft_b))     # other extension
        self.assertFalse(perms.can_upload(self.coord_b, self.draft_a))
        for actor in (self.special_a, self.plain, self.suspended):
            self.assertFalse(perms.can_upload(actor, self.draft_a), actor)

    def test_admin_may_upload_only_while_the_extension_has_no_coordinator(self):
        self.assertFalse(perms.can_upload(self.admin, self.draft_a))       # alpha has one
        self.assertTrue(perms.can_upload(self.admin, self.draft_b))        # beta has none
        self.assertTrue(perms.can_upload(self.super_admin, self.draft_b))
        self.assertFalse(perms.can_upload(self.admin, self.up_b))          # still needs a draft

    def test_reopen_is_admin_only_and_needs_an_uploaded_sheet(self):
        for actor in (self.admin, self.super_admin):
            self.assertTrue(perms.can_reopen(actor, self.up_a))
            self.assertFalse(perms.can_reopen(actor, self.draft_a))
        for actor in (self.coord_a, self.special_a, self.plain):
            self.assertFalse(perms.can_reopen(actor, self.up_a), actor)

    def test_print_follows_what_the_person_can_open(self):
        self.assertTrue(perms.can_print(self.special_a, self.draft_a))
        self.assertFalse(perms.can_print(self.special_a, self.up_a))
        self.assertTrue(perms.can_print(self.coord_a, self.up_a))
        self.assertFalse(perms.can_print(self.coord_a, self.up_b))
        self.assertTrue(perms.can_print(self.admin, self.up_b))
        self.assertFalse(perms.can_print(self.plain, self.draft_a))

    def test_create(self):
        self.assertTrue(perms.can_create(self.admin, self.b.pk))
        self.assertTrue(perms.can_create(self.coord_a, self.a.pk))
        self.assertFalse(perms.can_create(self.coord_a, self.b.pk))
        self.assertTrue(perms.can_create(self.special_a, self.a.pk))
        self.assertFalse(perms.can_create(self.special_a, self.b.pk))
        for actor in (self.plain, self.suspended, self.archived):
            self.assertFalse(perms.can_create(actor, self.a.pk), actor)

    def test_a_special_seat_in_one_extension_gives_nothing_in_another(self):
        self.assertTrue(perms.has_attendance_role(self.special_a, self.a.pk))
        self.assertFalse(perms.has_attendance_role(self.special_a, self.b.pk))
        self.assertFalse(perms.has_attendance_role(self.suspended, self.a.pk))
        self.assertFalse(perms.has_attendance_role(self.plain, self.a.pk))

    def test_require_raises_permission_denied(self):
        perms.require(perms.can_upload, self.coord_a, self.draft_a)       # no error
        with self.assertRaises(PermissionDenied):
            perms.require(perms.can_upload, self.plain, self.draft_a)
        with self.assertRaises(PermissionDenied):
            perms.require(perms.can_create, self.plain, self.a.pk)

    # --- dashboards ---------------------------------------------------------
    def test_dashboard_scope_is_uploaded_sheets_the_person_may_see(self):
        self.assertEqual(set(dashboard_services._scope_attendance(self.coord_a)), {self.up_a})
        self.assertEqual(set(dashboard_services._scope_attendance(self.admin)), {self.up_a, self.up_b})
        self.assertEqual(set(dashboard_services._scope_attendance(self.special_a)), set())
