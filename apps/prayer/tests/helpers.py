"""Test helpers for the prayer tests (same shape as the tithes helpers).

Users follow AGENTS.md: `must_change_password=False` and `person_type="member_account"` unless a test needs a
guest. No email is set (the `email_verified_pair` constraint wants a verified time with it).
"""
import itertools

from apps.accounts.models import Extension, PersonType, Role, Status, User

_seq = itertools.count(1)


def make_extension(name=None):
    """A new extension with a unique name."""
    return Extension.objects.create(name=name or f"extension {next(_seq)}")


def make_user(role=Role.MEMBER, extension=None, *, person_type=PersonType.MEMBER_ACCOUNT,
              status=Status.ACTIVE, first_name=None, last_name=None):
    """A person. Admins and super-admins have no extension; everyone else gets the one given."""
    n = next(_seq)
    return User.objects.create_user(
        first_name=first_name or f"person{n}", last_name=last_name or f"tester{n}", role=role,
        extension=None if role in (Role.SUPER_ADMIN, Role.ADMIN) else extension,
        person_type=person_type, status=status, must_change_password=False,
    )
