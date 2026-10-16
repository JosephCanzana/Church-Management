"""Shared helpers for the pages management tests.

`make_user` creates an ACTIVATED user (status active, no forced password change),
because `ActivationRequiredMiddleware` redirects every request of anyone else.
The field names follow the account model described in AGENTS.md; if your `User`
needs more required fields (for example an extension for a coordinator), add
them here once and every test in this package picks them up.
"""
import itertools
import shutil
import tempfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from apps.accounts.models import Extension, Role, User

_counter = itertools.count(1)


def make_extension(**extra):
    """Create a bare extension; only the name is set, so these tests do not depend on address rules."""
    fields = {"name": f"pages test extension {next(_counter)}"}
    fields.update(extra)
    return Extension.objects.create(**fields)


def make_user(role, **extra):
    """Create and return an activated user with the given role.

    The database requires members and coordinators to belong to an extension
    (constraint `app_user_extension_required`), so one is created unless the
    caller passes `extension=`. Admins and super-admins never have one.
    """
    number = next(_counter)
    if role in (Role.MEMBER, Role.COORDINATOR) and "extension" not in extra:
        extra["extension"] = make_extension()
    fields = {
        "role": role,
        "status": "active",
        "must_change_password": False,
        "first_name": f"tester{number}",
        "last_name": "pages",
        "gender": "male",
    }
    fields.update(extra)
    user = User(**fields)
    user.set_password("test-pass-12345")
    user.save()
    return user


def png_upload(name="qr.png"):
    """A tiny real PNG, so ImageField's Pillow check accepts it."""
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class TempMediaMixin:
    """Point MEDIA_ROOT at a throwaway folder so tests never write into real uploads."""

    @classmethod
    def setUpClass(cls):
        cls._media_dir = tempfile.mkdtemp(prefix="pages-test-media-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)