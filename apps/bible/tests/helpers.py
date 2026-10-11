"""Shared helpers for the bible tests."""
from django.contrib.auth import get_user_model

from apps.accounts.models import Role
from apps.bible.models import BibleVerse


def make_user(role=Role.SUPER_ADMIN, **extra):
    """Create an activated user ready for ``client.force_login``.

    Defaults to a super-admin because admins and super-admins never belong to an
    extension, so no Extension row is needed. The bible pages are open to every
    role, so the role does not change what these tests check. The status and
    must_change_password values keep ActivationRequiredMiddleware from
    redirecting the test client to the activation page.

    If your User model needs more fields (first name, email...), add them here:
    every bible test goes through this one function.
    """
    fields = {"role": role, "status": "active", "must_change_password": False}
    fields.update(extra)
    return get_user_model().objects.create_user(password="x-pass-12345", **fields)


def add_chapter(translation="KJV", book="Gen", chapter=1, verses=("First verse.", "Second verse.")):
    """Store a tiny chapter of local text and return the rows."""
    return [
        BibleVerse.objects.create(
            translation=translation, book=book, chapter=chapter, verse=number, text=text,
        )
        for number, text in enumerate(verses, start=1)
    ]