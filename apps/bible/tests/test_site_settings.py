"""Tests for the super-admin Bible switches (apps/bible/site_settings.py and its screen).

No test calls api.esv.org: ``esv.get_verses`` is mocked, and ESV_API_KEY is set
with override_settings so a real key in .env never changes the result.
"""
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.bible import esv, services, switches
from apps.bible.errors import VersionLocked
from apps.bible.site_settings import (
    AUDIT_VERSION, MSG_LAST_ONE, set_online_services_enabled, set_version_enabled,
)
from apps.bible.versions import VERSIONS_BY_CODE
from apps.core.models import SiteSetting
from apps.core.services import ServiceError

from .helpers import add_chapter, make_user


def _fake_actor(role):
    """A signed-in, active actor with this role, without touching the database.

    Coordinators and members need an extension to exist; the permission check
    only reads these three attributes, so a stand-in is enough here.
    """
    return SimpleNamespace(is_authenticated=True, is_active=True, role=role)


def _value(key):
    """The stored value of a switch row, or None when the row does not exist."""
    row = SiteSetting.objects.filter(key=key).first()
    return None if row is None else row.value


class SwitchKeyTests(TestCase):
    """The ESV switch keeps the key the rest of the code already uses."""

    def test_esv_key_is_unchanged(self):
        self.assertEqual(switches.switch_key(VERSIONS_BY_CODE["ESV"]), esv.KEY_ESV)

    def test_everything_is_on_when_no_rows_exist(self):
        state = switches.read_switches()
        self.assertTrue(state.master)
        self.assertTrue(all(state.versions.values()))


class SetVersionTests(TestCase):
    """The service that changes one version's switch."""

    def setUp(self):
        self.admin = make_user(Role.SUPER_ADMIN)

    def test_turning_off_saves_value_and_writes_one_audit_row(self):
        self.assertTrue(set_version_enabled(self.admin, "KJV", False))
        self.assertIs(_value("bible_kjv_enabled"), False)
        entries = AuditLog.objects.filter(action=AUDIT_VERSION)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().after, {"enabled": False})

    def test_second_identical_request_changes_nothing(self):
        set_version_enabled(self.admin, "KJV", False)
        self.assertFalse(set_version_enabled(self.admin, "KJV", False))
        self.assertEqual(AuditLog.objects.filter(action=AUDIT_VERSION).count(), 1)

    def test_turning_back_on(self):
        set_version_enabled(self.admin, "ADB", False)
        self.assertTrue(set_version_enabled(self.admin, "ADB", True))
        self.assertIs(_value("bible_adb_enabled"), True)

    def test_unknown_version_is_refused(self):
        with self.assertRaises(ServiceError):
            set_version_enabled(self.admin, "NOPE", False)

    def test_only_the_super_admin_may_change_a_switch(self):
        for role in (Role.ADMIN, Role.COORDINATOR, Role.MEMBER):
            with self.subTest(role=role), self.assertRaises(PermissionDenied):
                set_version_enabled(_fake_actor(role), "KJV", False)
        self.assertIsNone(_value("bible_kjv_enabled"))

    def test_the_last_readable_version_cannot_be_turned_off(self):
        set_version_enabled(self.admin, "ESV", False)
        set_version_enabled(self.admin, "ADB", False)
        with self.assertRaisesMessage(ServiceError, MSG_LAST_ONE):
            set_version_enabled(self.admin, "KJV", False)
        self.assertIsNot(_value("bible_kjv_enabled"), False)

    def test_master_cannot_be_turned_off_when_only_esv_is_left(self):
        set_version_enabled(self.admin, "KJV", False)
        set_version_enabled(self.admin, "ADB", False)
        with self.assertRaisesMessage(ServiceError, MSG_LAST_ONE):
            set_online_services_enabled(self.admin, False)

    def test_master_can_be_turned_off_when_a_local_version_is_on(self):
        self.assertTrue(set_online_services_enabled(self.admin, False))
        self.assertIs(_value("bible_api_enabled"), False)


@override_settings(ESV_API_KEY="test-key")
class EnforcementTests(TestCase):
    """A switched-off version is blocked completely."""

    def setUp(self):
        self.admin = make_user(Role.SUPER_ADMIN)

    def test_local_version_off_is_refused(self):
        add_chapter("KJV", "Gen", 1)
        set_version_enabled(self.admin, "KJV", False)
        with self.assertRaises(VersionLocked):
            services.get_chapter("KJV", "Gen", 1)

    def test_local_version_on_still_reads(self):
        add_chapter("ADB", "Gen", 1, verses=("Una.",))
        set_version_enabled(self.admin, "KJV", False)
        self.assertEqual(services.get_chapter("ADB", "Gen", 1).verses, [(1, "Una.")])

    def test_esv_off_never_reaches_the_cache_or_the_api(self):
        set_version_enabled(self.admin, "ESV", False)
        with mock.patch("apps.bible.esv.get_verses") as get_verses:
            with self.assertRaises(VersionLocked):
                services.get_chapter("ESV", "Gen", 1)
        get_verses.assert_not_called()

    def test_master_off_blocks_esv(self):
        set_online_services_enabled(self.admin, False)
        with mock.patch("apps.bible.esv.get_verses") as get_verses:
            with self.assertRaises(VersionLocked):
                services.get_chapter("ESV", "Gen", 1)
        get_verses.assert_not_called()

    def test_picker_statuses_show_the_reason(self):
        set_version_enabled(self.admin, "KJV", False)
        by_code = {s.version.code: s for s in services.version_statuses()}
        self.assertFalse(by_code["KJV"].available)
        self.assertIn("turned off", by_code["KJV"].reason)
        self.assertTrue(by_code["ADB"].available)

    @override_settings(ESV_API_KEY="")
    def test_default_version_falls_back_to_one_that_can_be_read(self):
        set_version_enabled(self.admin, "KJV", False)
        self.assertEqual(services.resolve_version("").code, "ADB")


class ScreenTests(TestCase):
    """The page and the POST buttons."""

    def setUp(self):
        self.admin = make_user(Role.SUPER_ADMIN)
        self.page = reverse("bible_superadmin:site_settings")

    def test_super_admin_sees_a_button_for_every_version(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.page)
        self.assertEqual(response.status_code, 200)
        for code in ("ESV", "KJV", "ADB"):
            self.assertContains(response, reverse("bible_superadmin:version_toggle", args=[code]))
        self.assertContains(response, reverse("bible_superadmin:online_toggle"))

    def test_toggle_off_then_on_through_the_screen(self):
        self.client.force_login(self.admin)
        url = reverse("bible_superadmin:version_toggle", args=["KJV"])
        self.client.post(url, {"enabled": "0"})
        self.assertIs(_value("bible_kjv_enabled"), False)
        self.client.post(url, {"enabled": "1"})
        self.assertIs(_value("bible_kjv_enabled"), True)

    def test_a_bad_value_changes_nothing(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("bible_superadmin:version_toggle", args=["KJV"]), {"enabled": "maybe"})
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(_value("bible_kjv_enabled"))

    def test_get_on_a_toggle_is_not_allowed(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("bible_superadmin:version_toggle", args=["KJV"]))
        self.assertEqual(response.status_code, 405)

    def test_an_admin_is_sent_away_and_nothing_changes(self):
        self.client.force_login(make_user(Role.ADMIN))
        self.assertEqual(self.client.get(self.page).status_code, 302)
        self.client.post(reverse("bible_superadmin:version_toggle", args=["KJV"]), {"enabled": "0"})
        self.assertIsNone(_value("bible_kjv_enabled"))

    def test_a_signed_out_visitor_is_sent_to_login(self):
        self.assertEqual(self.client.get(self.page).status_code, 302)
