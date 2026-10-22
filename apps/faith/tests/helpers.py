"""Shared test helpers for the faith app."""
from itertools import count

from django.contrib.auth import get_user_model

from apps.accounts.models import Extension, PersonType, Role

_counter = count(1)


def make_extension(**overrides):
    """Create an extension (Extension.name is unique, so each one gets a number)."""
    fields = {"name": f"Faith test extension {next(_counter)}"}
    fields.update(overrides)
    return Extension.objects.create(**fields)


def make_user(**overrides):
    """Create a member who can sign in.

    A member must belong to an extension (check constraint app_user_extension_required),
    so one is created unless the test passes `extension=`.
    """
    fields = {
        "account_id": "",  # blank -> User.save() generates it from account_number_seq
        "first_name": "Faith",
        "last_name": "Tester",
        "role": Role.MEMBER,
        "person_type": PersonType.MEMBER_ACCOUNT,
        "must_change_password": False,
    }
    fields.update(overrides)
    if fields["role"] not in (Role.SUPER_ADMIN, Role.ADMIN):
        fields.setdefault("extension", make_extension())
    return get_user_model().objects.create_user(password="pass-12345-xyz", **fields)