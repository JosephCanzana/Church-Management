"""Shared helpers for the buddy tests (not a test module itself)."""
import itertools

from django.utils import timezone

from apps.accounts.models import Extension, PersonType, Role, Status, User

_counter = itertools.count(1)


def make_extension(name=None):
    """Create an extension with a unique name."""
    return Extension.objects.create(name=name or f"Extension {next(_counter)}")


def make_user(
    *, first="Ana", last=None, middle="", role=Role.MEMBER, extension=None,
    person_type=PersonType.MEMBER_ACCOUNT, status=Status.ACTIVE,
):
    """Create a person who can sign in unless `person_type` or `status` says otherwise.

    Members and coordinators need an extension (a database rule), so one is made when missing.
    Test users never need a password change, as AGENTS.md requires.
    """
    n = next(_counter)
    if role in (Role.MEMBER, Role.COORDINATOR) and extension is None:
        extension = make_extension()
    extra = {}
    if status == Status.ARCHIVED:
        extra["archived_at"] = timezone.now()     # the archived/status constraint needs both
    user = User.objects.create_user(
        password="pass-12345",
        first_name=first.lower(), last_name=(last or f"Tester{n}").lower(), middle_name=middle.lower(),
        role=role, extension=extension, person_type=person_type, status=status,
        must_change_password=False, **extra,
    )
    return user
