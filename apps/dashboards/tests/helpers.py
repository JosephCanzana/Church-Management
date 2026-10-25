"""Small builders shared by the Brick 1 dashboard tests (no test cases live here)."""
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.accounts.models import Extension, PersonType, Status, User
from apps.attendance.age import AgeNetwork
from apps.attendance.models import Attendance
from apps.tithes.models import TithesOffering

GENDERS = ("male", "female")
GROUPS = ("first_timers", "guests", "members")


def make_person(role, extension=None, status=Status.ACTIVE, **extra):
    """Create an activated person who can sign in (`must_change_password=False`, `member_account`)."""
    return User.objects.create_user(
        password="pass-12345", first_name="test", last_name=role.replace("_", ""), role=role,
        extension=extension, status=status, must_change_password=False,
        person_type=PersonType.MEMBER_ACCOUNT, **extra,
    )


def make_extension(name):
    """Create an extension by name."""
    return Extension.objects.create(name=name)


def summary_json(cells=None):
    """Build a frozen-summary dict; ``cells`` maps (group, gender, network) to a count, the rest are 0."""
    summary = {
        group: {gender: {network: 0 for network in AgeNetwork.values} for gender in GENDERS} for group in GROUPS
    }
    for (group, gender, network), number in (cells or {}).items():
        summary[group][gender][network] = number
    return summary


def add_uploaded_sheet(extension, day, present, first_timers=0, cells=None, label="Sunday Service", summary=True):
    """Create an UPLOADED sheet with frozen totals and (unless ``summary=False``) a frozen breakdown."""
    return Attendance.objects.create(
        extension=extension, attendance_date=day, label=label, status=Attendance.Status.UPLOADED,
        total_present=present, first_timers_count=first_timers, members_present=present - first_timers,
        summary=summary_json(cells) if summary else None, uploaded_at=timezone.now(),
    )


def add_draft_sheet(extension, day, label="Sunday Service"):
    """Create a DRAFT sheet (no frozen numbers); dashboards must ignore it."""
    return Attendance.objects.create(extension=extension, attendance_date=day, label=label)


def add_finalized_report(sheet, tithes, offering):
    """Create a FINALIZED tithes report on ``sheet`` with the given frozen amounts."""
    tithes, offering = Decimal(tithes), Decimal(offering)
    return TithesOffering.objects.create(
        attendance=sheet, status=TithesOffering.Status.FINALIZED, offering_amount=offering,
        tithes_total=tithes, total_amount=tithes + offering, breakdown_total=tithes + offering,
        difference=Decimal("0"), tithers_count=1, finalized_at=timezone.now(),
    )


def sundays(count, last=date(2026, 10, 18)):
    """Return ``count`` weekly dates ending on ``last``, oldest first."""
    return [last - timedelta(weeks=count - 1 - i) for i in range(count)]
