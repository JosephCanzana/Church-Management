"""Shared builders for the tithes tests. Everything goes through `tithes.services`, never raw model writes."""
from datetime import date, timedelta
from itertools import count

from apps.accounts.models import Extension, PersonType, Role, Status, User
from apps.attendance.models import Attendance
from apps.core.services import new_submission_token
from apps.tithes import services

_n = count(1)


def make_extension(name=None):
    """An extension with a unique name."""
    return Extension.objects.create(name=name or f"Test Extension {next(_n)}")


def make_user(role=Role.MEMBER, extension=None, first="Pat", last=None, person_type=PersonType.MEMBER_ACCOUNT, **extra):
    """An active user who can sign in without changing a password."""
    return User.objects.create_user(
        first_name=first, last_name=last or f"Tester{next(_n)}", role=role, person_type=person_type,
        status=Status.ACTIVE, extension=extension, must_change_password=False, **extra,
    )


def make_sheet(extension, actor, day=None, label=None):
    """A draft attendance sheet (never two with the same extension, date and label)."""
    return Attendance.objects.create(
        extension=extension, attendance_date=day or date(2026, 1, 4) + timedelta(days=7 * next(_n)),
        label=label or f"Service {next(_n)}", created_by=actor, taken_by=actor,
    )


def make_report(actor, sheet, offering="100", entries=(), breakdown=None):
    """A draft report with an offering, entries `[(giver, amount, [co_givers])]` and a breakdown."""
    report = services.create_report(actor, sheet)
    services.set_offering(actor, report, offering)
    for giver, amount, *rest in entries:
        services.add_entry(actor, report, giver, amount, rest[0] if rest else ())
    if breakdown is not None:
        services.set_breakdown(actor, report, breakdown)
    return report


def finalize(actor, report, reason=""):
    """Finalize through the service (one fresh token) and return the report as stored."""
    services.finalize_report(actor, report.pk, token=new_submission_token(), bypass_reason=reason)
    report.refresh_from_db()
    return report
