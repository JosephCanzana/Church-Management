"""Shared builders for the tithes tests: people, sheets, a ready-made scene and a few shortcuts.

Test users follow AGENTS.md: `must_change_password=False` and `person_type="member_account"` unless a test
needs a guest or a no-account member. Every sheet gets its own date, so no two share extension, date and label.
Builders write rows directly (they are fixtures); the behaviour under test always goes through `services`.
"""
import itertools
import json
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Extension, PersonType, Role, SpecialRole, Status, User, UserSpecialRole
from apps.attendance.models import Attendance
from apps.audit.models import AuditLog
from apps.core.services import new_submission_token
from apps.notifications.models import Notification
from apps.tithes import services
from apps.tithes.models import SPECIAL_ROLE, TithesOffering

_seq = itertools.count(1)
BASE_DATE = date(2026, 1, 4)


def make_extension():
    """A new extension with a unique name."""
    return Extension.objects.create(name=f"extension {next(_seq)}")


def make_user(extension=None, *, role=Role.MEMBER, person_type=PersonType.MEMBER_ACCOUNT,
              status=Status.ACTIVE, first_name=None, last_name="tester"):
    """A person. Admins and super-admins have no extension; everyone else needs one."""
    n = next(_seq)
    return User.objects.create_user(
        first_name=first_name or f"person{n}", last_name=last_name, role=role,
        extension=None if role in (Role.SUPER_ADMIN, Role.ADMIN) else extension,
        person_type=person_type, status=status, must_change_password=False,
    )


def make_sheet(extension, *, status=Attendance.Status.DRAFT):
    """An attendance sheet on a date no other test sheet uses (archived ones keep `archived_at` in step)."""
    extra = {"archived_at": timezone.now()} if status == Attendance.Status.ARCHIVED else {}
    return Attendance.objects.create(
        extension=extension, attendance_date=BASE_DATE + timedelta(days=7 * next(_seq)),
        label="Sunday Service", status=status, **extra,
    )


def token():
    """A fresh one-time form token."""
    return new_submission_token()


class TithesTestCase(TestCase):
    """A small scene: two extensions, their leaders, a seat holder, three givers, a sheet and its report."""

    def setUp(self):
        self.ext = make_extension()
        self.other_ext = make_extension()
        self.coordinator = make_user(self.ext, role=Role.COORDINATOR)
        self.other_coordinator = make_user(self.other_ext, role=Role.COORDINATOR)
        self.admin = make_user(role=Role.ADMIN)
        self.super_admin = make_user(role=Role.SUPER_ADMIN)
        self.plain_member = make_user(self.ext)
        self.special = make_user(self.ext)
        SpecialRole.objects.get_or_create(code=SPECIAL_ROLE, defaults={"name": "Tithes and offering"})
        UserSpecialRole.objects.create(user=self.special, role_id=SPECIAL_ROLE, extension=self.ext)
        self.alice = make_user(self.ext, first_name="anna", last_name="santos")
        self.bob = make_user(self.ext, first_name="ben", last_name="santos")
        self.carol = make_user(self.ext, first_name="cara", last_name="reyes")
        self.sheet = make_sheet(self.ext)
        self.report = services.create_report(self.coordinator, self.sheet)

    # ------------------------------------------------------------------ shortcuts
    def fresh(self, report=None):
        """The report as it is in the database now (with its sheet)."""
        return TithesOffering.objects.select_related("attendance").get(pk=(report or self.report).pk)

    def short_report(self):
        """Offering 100 + one tithe of 50 = Total 150, but only 100 counted: difference -50."""
        services.set_offering(self.coordinator, self.report, "100")
        services.add_entry(self.coordinator, self.report, self.alice, "50")
        services.set_breakdown(self.coordinator, self.report, {"100": 1})
        return self.fresh()

    def balanced_report(self):
        """Offering 100 + one tithe of 50 = Total 150, counted 150: difference 0."""
        services.set_offering(self.coordinator, self.report, "100")
        services.add_entry(self.coordinator, self.report, self.alice, "50")
        services.set_breakdown(self.coordinator, self.report, {"100": 1, "50": 1})
        return self.fresh()

    def file_request(self, message="Counted twice, still short."):
        """The coordinator files a bypass request on a short report; returns the request row."""
        return services.request_bypass(
            self.coordinator, self.report.pk, message=message, token=token(),
        ).bypass_request

    def finalize(self, actor=None, **kwargs):
        """Finalize through the service with a fresh token."""
        return services.finalize_report(actor or self.coordinator, self.report.pk, token=token(), **kwargs)

    # ------------------------------------------------------------------ privacy
    def assert_no_leak(self, *secrets):
        """No secret text or amount appears in any audit row or notification `data` (AGENTS.md privacy)."""
        blob = json.dumps(
            [[row.before, row.after, row.entity_label] for row in AuditLog.objects.all()], default=str,
        ) + json.dumps([row.data for row in Notification.objects.all()], default=str)
        for secret in secrets:
            self.assertNotIn(str(secret), blob)
