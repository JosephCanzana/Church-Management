"""Small builders shared by the events tests."""
import itertools
from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from apps.accounts.models import Extension, Role, Status, User
from apps.core.services import archive
from apps.events.models import Event

_counter = itertools.count(1)


def make_extension(name):
    return Extension.objects.create(name=name)


def make_user(role, extension=None, status=Status.ACTIVE):
    """An activated account (tests that log in need status active, must_change_password False)."""
    n = next(_counter)
    return User.objects.create_user(
        password="test-pass-12345", role=role, extension=extension, status=status,
        first_name=f"{role}{n}", last_name="tester", must_change_password=False,
    )


def archive_user(user):
    """Archive through the core helper so the archived_at / status constraint and trigger agree."""
    user.status = Status.ARCHIVED
    archive(user, extra_fields=["status"])
    return user


def make_event(extension=None, *, title="Sunday gathering", start_at=None, end_at=None, **kw):
    return Event.objects.create(
        extension=extension, title=title,
        start_at=start_at or in_days(3), end_at=end_at, **kw,
    )


def in_days(days, hours=0):
    return timezone.now() + timedelta(days=days, hours=hours)


def build_world():
    """Two extensions, each with a coordinator and members, plus an admin and a super-admin."""
    w = SimpleNamespace()
    w.ext_a = make_extension("Alpha")
    w.ext_b = make_extension("Bravo")
    w.super = make_user(Role.SUPER_ADMIN)
    w.admin = make_user(Role.ADMIN)
    w.coord_a = make_user(Role.COORDINATOR, w.ext_a)
    w.coord_b = make_user(Role.COORDINATOR, w.ext_b)
    Extension.objects.filter(pk=w.ext_a.pk).update(coordinator=w.coord_a)
    Extension.objects.filter(pk=w.ext_b.pk).update(coordinator=w.coord_b)
    w.member_a = make_user(Role.MEMBER, w.ext_a)
    w.member_a2 = make_user(Role.MEMBER, w.ext_a)
    w.member_b = make_user(Role.MEMBER, w.ext_b)
    return w
