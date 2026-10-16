"""Tests for manager-only landing content forms and service behavior."""
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from apps.accounts.models import Extension, Role, Status, User
from apps.audit.models import AuditLog
from apps.core.services import ServiceError
from apps.pages.manage_services import landing_content_blocks, save_landing_content
from apps.pages.models import ChurchContent


def make_user(role, extension=None):
    """Create an activated role account for landing content tests."""
    return User.objects.create_user(
        password="valid-test-password",
        first_name="Test",
        last_name=role,
        role=role,
        extension=extension,
        status=Status.ACTIVE,
        must_change_password=False,
    )


class LandingContentServiceTests(TestCase):
    """Check save/reset transactions, English-only keys, and audit privacy."""

    def setUp(self):
        self.extension = Extension.objects.create(name="content-test")
        self.admin = make_user(Role.ADMIN)
        self.super_admin = make_user(Role.SUPER_ADMIN)
        self.coordinator = make_user(Role.COORDINATOR, self.extension)

    def test_only_admin_and_super_admin_can_read_or_write_management_data(self):
        with self.assertRaises(PermissionDenied):
            landing_content_blocks(self.coordinator)
        with self.assertRaises(PermissionDenied):
            save_landing_content(self.coordinator, ChurchContent.Key.MISSION, "Not allowed")
        self.assertFalse(ChurchContent.objects.exists())

    def test_each_block_reports_custom_or_default_content(self):
        blocks = landing_content_blocks(self.admin)
        self.assertEqual(len(blocks), 5)
        hero = next(block for block in blocks if block["key"] == ChurchContent.Key.LANDING_DESCRIPTION)
        donation = next(block for block in blocks if block["key"] == ChurchContent.Key.DONATION_INSTRUCTIONS)
        self.assertFalse(hero["is_custom"])
        self.assertTrue(hero["body"])
        self.assertFalse(donation["is_custom"])
        self.assertEqual(donation["body"], "")

    def test_save_creates_english_row_and_audit_never_contains_text(self):
        body = "Line one\n<script>alert('not html')</script>"
        self.assertTrue(save_landing_content(self.admin, ChurchContent.Key.MISSION, body))
        row = ChurchContent.objects.get(key=ChurchContent.Key.MISSION)
        self.assertEqual(row.language, ChurchContent.Language.EN)
        self.assertEqual(row.body, body)
        self.assertEqual(row.version, 1)

        log = AuditLog.objects.get(action="pages.content_saved")
        self.assertEqual(log.after, {"key": ChurchContent.Key.MISSION, "version": 1, "custom": True})
        self.assertNotIn("Line one", str(log.before) + str(log.after))
        self.assertNotIn("<script>", str(log.before) + str(log.after))

    def test_save_is_idempotent_and_update_increments_version(self):
        self.assertTrue(save_landing_content(self.admin, ChurchContent.Key.VISION, "First text"))
        self.assertFalse(save_landing_content(self.admin, ChurchContent.Key.VISION, "First text"))
        self.assertTrue(save_landing_content(self.admin, ChurchContent.Key.VISION, "Updated text"))
        row = ChurchContent.objects.get(key=ChurchContent.Key.VISION)
        self.assertEqual(row.version, 2)
        self.assertEqual(AuditLog.objects.filter(action="pages.content_saved").count(), 2)

    def test_blank_body_resets_to_default_and_is_idempotent(self):
        save_landing_content(self.admin, ChurchContent.Key.CORE_VALUES, "Custom values")
        row = ChurchContent.objects.get(key=ChurchContent.Key.CORE_VALUES)
        row_id = row.pk
        self.assertTrue(save_landing_content(self.super_admin, ChurchContent.Key.CORE_VALUES, " \n "))
        self.assertFalse(ChurchContent.objects.filter(pk=row_id).exists())
        log = AuditLog.objects.get(action="pages.content_reset")
        self.assertEqual(log.entity_id, row_id)
        self.assertEqual(log.after, {"key": ChurchContent.Key.CORE_VALUES, "custom": False})
        self.assertFalse(save_landing_content(self.admin, ChurchContent.Key.CORE_VALUES, ""))

    def test_unknown_or_non_english_keys_are_not_saved(self):
        with self.assertRaises(ServiceError):
            save_landing_content(self.admin, "terms", "Terms text")
        ChurchContent.objects.create(
            key=ChurchContent.Key.MISSION,
            language=ChurchContent.Language.FIL,
            body="Filipino text",
        )
        self.assertFalse(any(block["is_custom"] for block in landing_content_blocks(self.admin)))


class LandingContentViewTests(TestCase):
    """Exercise manager access, inline validation, and safe text rendering."""

    def setUp(self):
        extension = Extension.objects.create(name="view-test")
        self.admin = make_user(Role.ADMIN)
        self.super_admin = make_user(Role.SUPER_ADMIN)
        self.coordinator = make_user(Role.COORDINATOR, extension)
        self.member = make_user(Role.MEMBER, extension)

    def test_only_admin_roles_open_management_and_public_roles_redirect_home(self):
        list_url = reverse("pages:pages_manage:content_list")
        self.assertEqual(self.client.get(list_url).status_code, 302)
        for actor, dashboard in (
            (self.coordinator, "dashboards:coordinator"),
            (self.member, "dashboards:member"),
        ):
            self.client.force_login(actor)
            response = self.client.get(list_url)
            self.assertRedirects(response, reverse(dashboard), fetch_redirect_response=False)

        for actor in (self.admin, self.super_admin):
            self.client.force_login(actor)
            self.assertEqual(self.client.get(list_url).status_code, 200)

    def test_editor_escapes_script_text_and_preserves_line_breaks(self):
        self.client.force_login(self.admin)
        url = reverse("pages:pages_manage:content_edit", args=[ChurchContent.Key.MISSION])
        response = self.client.post(url, {"body": "First line\n<script>alert('x')</script>"})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.client.logout()
        landing = self.client.get(reverse("pages:landing"))
        self.assertContains(landing, "First line")
        self.assertContains(landing, escape("<script>alert('x')</script>"), html=False)
        self.assertNotContains(landing, "<script>alert('x')</script>", html=False)

    def test_blank_save_resets_custom_block_to_default(self):
        self.client.force_login(self.admin)
        key = ChurchContent.Key.MISSION
        ChurchContent.objects.create(key=key, language=ChurchContent.Language.EN, body="Custom mission")
        url = reverse("pages:pages_manage:content_edit", args=[key])
        response = self.client.post(url, {"body": ""})
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertFalse(ChurchContent.objects.filter(key=key, language=ChurchContent.Language.EN).exists())

    def test_editor_page_includes_confirmation_and_reset_instructions(self):
        self.client.force_login(self.admin)
        url = reverse("pages:pages_manage:content_edit", args=[ChurchContent.Key.MISSION])
        response = self.client.get(url)
        self.assertContains(response, "data-confirm=")
        self.assertContains(response, "Save changes?")
        self.assertContains(response, "Leave empty to reset this block to its default.")