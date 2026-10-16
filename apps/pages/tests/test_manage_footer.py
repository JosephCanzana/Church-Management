"""Tests for the managed footer links (brick 7): URL rules, defaults, reset, guards and screens."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.core.models import SiteSetting
from apps.core.services import ServiceError
from apps.pages import manage_services
from apps.pages.services import FOOTER, get_landing_context
from apps.pages.site_settings import (
    AUDIT_RESET,
    AUDIT_SAVED,
    FOOTER_LINKS_KEY,
    MAX_FOOTER_LINKS,
    get_footer_links,
    replace_footer_links,
    validate_footer_url,
)

from .helpers import make_user

DEFAULT_LABELS = [link["label"] for link in FOOTER["more"]]


class FooterUrlValidationTests(SimpleTestCase):
    """validate_footer_url accepts only http(s), mailto, tel, a /path or a #anchor."""

    def test_accepts_supported_addresses(self):
        for value in (
            "https://example.com", "http://example.com/a?b=1", "mailto:info@example.org",
            "tel:+63446001234", "/privacy/", "#contact", "#", "HTTPS://Example.com",
        ):
            with self.subTest(value=value):
                self.assertEqual(validate_footer_url(value), value)

    def test_strips_surrounding_spaces(self):
        self.assertEqual(validate_footer_url("  https://example.com  "), "https://example.com")

    def test_rejects_unsafe_or_incomplete_addresses(self):
        for value in (
            "", "   ", "javascript:alert(1)", "JaVaScRiPt:alert(1)", "java\tscript:alert(1)",
            "data:text/html,hi", "vbscript:x", "//evil.example", "/\\evil.example", "https://",
            "example.com", "ftp://example.com", "mailto:", "tel:", "https://exa mple.com",
            "http://[::1", "http://:80", "/a\nb", "x" * 600,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_footer_url(value)


class FooterLinkServiceTests(TestCase):
    """The footer list: defaults, saved override, reorder, delete, reset and audit rows."""

    def setUp(self):
        self.admin = make_user(Role.ADMIN)

    def labels(self):
        return [link["label"] for link in get_footer_links()]

    def audit_count(self, action):
        return AuditLog.objects.filter(action=action).count()

    def test_defaults_are_used_when_nothing_is_saved(self):
        links, is_custom = manage_services.footer_links_for_manager(self.admin)
        self.assertFalse(is_custom)
        self.assertEqual([link["label"] for link in links], DEFAULT_LABELS)

    def test_adding_a_link_saves_an_override_and_the_landing_page_uses_it(self):
        manage_services.save_footer_link(self.admin, label="Facebook", url="https://example.com/fb")
        links, is_custom = manage_services.footer_links_for_manager(self.admin)
        self.assertTrue(is_custom)
        self.assertEqual([link["label"] for link in links], DEFAULT_LABELS + ["Facebook"])
        landing_more = [link["label"] for link in get_landing_context()["footer"]["more"]]
        self.assertEqual(landing_more, DEFAULT_LABELS + ["Facebook"])

    def test_javascript_address_is_refused_and_nothing_is_saved(self):
        with self.assertRaises(ServiceError):
            manage_services.save_footer_link(self.admin, label="Bad", url="javascript:alert(1)")
        self.assertFalse(SiteSetting.objects.filter(key=FOOTER_LINKS_KEY).exists())
        self.assertEqual(self.audit_count(AUDIT_SAVED), 0)

    def test_blank_label_and_too_many_links_are_refused(self):
        with self.assertRaises(ServiceError):
            manage_services.save_footer_link(self.admin, label="   ", url="/ok/")
        too_many = [{"id": str(i), "label": f"L{i}", "url": "/ok/"} for i in range(MAX_FOOTER_LINKS + 1)]
        with self.assertRaises(ServiceError):
            replace_footer_links(self.admin, too_many)

    def test_editing_a_default_link_keeps_the_others(self):
        first = get_footer_links()[0]
        manage_services.save_footer_link(self.admin, link_id=first["id"], label="Contact", url="/contact/")
        links = get_footer_links()
        self.assertEqual(links[0]["label"], "Contact")
        self.assertEqual(links[0]["url"], "/contact/")
        self.assertEqual([link["label"] for link in links][1:], DEFAULT_LABELS[1:])

    def test_saving_the_same_values_again_changes_nothing_and_logs_nothing(self):
        first = get_footer_links()[0]
        self.assertTrue(manage_services.save_footer_link(
            self.admin, link_id=first["id"], label="Contact", url="/contact/"))
        self.assertFalse(manage_services.save_footer_link(
            self.admin, link_id=first["id"], label="Contact", url="/contact/"))
        self.assertEqual(self.audit_count(AUDIT_SAVED), 1)

    def test_unchanged_defaults_are_not_materialised(self):
        self.assertFalse(replace_footer_links(self.admin, get_footer_links()))
        self.assertFalse(SiteSetting.objects.filter(key=FOOTER_LINKS_KEY).exists())

    def test_audit_row_holds_before_and_after_lists(self):
        manage_services.save_footer_link(self.admin, label="Facebook", url="https://example.com/fb")
        entry = AuditLog.objects.get(action=AUDIT_SAVED)
        self.assertFalse(entry.before["custom"])
        self.assertTrue(entry.after["custom"])
        self.assertEqual(len(entry.after["links"]), len(entry.before["links"]) + 1)

    def test_delete_removes_one_link_and_unknown_id_is_refused(self):
        target = get_footer_links()[1]
        manage_services.delete_footer_link(self.admin, target["id"])
        self.assertNotIn(target["label"], self.labels())
        with self.assertRaises(ServiceError):
            manage_services.delete_footer_link(self.admin, "no-such-id")

    def test_deleting_every_link_leaves_an_empty_footer_until_reset(self):
        for link in get_footer_links():
            manage_services.delete_footer_link(self.admin, link["id"])
        self.assertEqual(get_footer_links(), [])
        _links, is_custom = manage_services.footer_links_for_manager(self.admin)
        self.assertTrue(is_custom)
        manage_services.reset_footer_links_to_default(self.admin)
        self.assertEqual(self.labels(), DEFAULT_LABELS)

    def test_reorder_moves_a_link_and_stops_at_the_ends(self):
        first = get_footer_links()[0]
        self.assertTrue(manage_services.reorder_footer_link(self.admin, first["id"], "down"))
        self.assertEqual(self.labels()[1], first["label"])
        self.assertTrue(manage_services.reorder_footer_link(self.admin, first["id"], "up"))
        self.assertFalse(manage_services.reorder_footer_link(self.admin, first["id"], "up"))
        with self.assertRaises(ServiceError):
            manage_services.reorder_footer_link(self.admin, first["id"], "sideways")

    def test_reset_returns_to_defaults_and_is_idempotent(self):
        manage_services.save_footer_link(self.admin, label="Facebook", url="https://example.com/fb")
        self.assertTrue(manage_services.reset_footer_links_to_default(self.admin))
        self.assertEqual(self.labels(), DEFAULT_LABELS)
        self.assertFalse(manage_services.reset_footer_links_to_default(self.admin))
        self.assertEqual(self.audit_count(AUDIT_RESET), 1)

    def test_a_tampered_row_falls_back_to_defaults(self):
        SiteSetting.objects.create(
            key=FOOTER_LINKS_KEY,
            value=[{"id": "x", "label": "Evil", "url": "javascript:alert(1)"}],
        )
        self.assertEqual(self.labels(), DEFAULT_LABELS)

    def test_non_managers_are_refused_by_every_service(self):
        member = make_user(Role.MEMBER)
        for call in (
            lambda: manage_services.footer_links_for_manager(member),
            lambda: manage_services.save_footer_link(member, label="X", url="/x/"),
            lambda: manage_services.delete_footer_link(member, "default-copyright"),
            lambda: manage_services.reorder_footer_link(member, "default-copyright", "down"),
            lambda: manage_services.reset_footer_links_to_default(member),
        ):
            with self.subTest():
                with self.assertRaises(PermissionDenied):
                    call()


class FooterLinkScreenTests(TestCase):
    """The management screens: access, inline errors, POST-only actions."""

    def setUp(self):
        self.admin = make_user(Role.ADMIN)
        self.list_url = reverse("pages:pages_manage:footer_links")
        self.create_url = reverse("pages:pages_manage:footer_link_create")

    def test_anonymous_and_members_cannot_open_the_screens(self):
        self.assertEqual(self.client.get(self.list_url).status_code, 302)
        self.client.force_login(make_user(Role.MEMBER))
        self.assertNotEqual(self.client.get(self.list_url).status_code, 200)
        self.assertNotEqual(self.client.get(self.create_url).status_code, 200)

    def test_admin_sees_the_default_links(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        for label in DEFAULT_LABELS:
            self.assertContains(response, label)

    def test_javascript_address_shows_an_inline_error(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, {"label": "Bad", "url": "javascript:alert(1)"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("url", response.context["form"].errors)
        self.assertFalse(SiteSetting.objects.filter(key=FOOTER_LINKS_KEY).exists())

    def test_valid_link_is_saved_and_redirects(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, {"label": "Facebook", "url": "https://example.com/fb"})
        self.assertRedirects(response, self.list_url)
        self.assertIn("Facebook", [link["label"] for link in get_footer_links()])

    def test_move_delete_and_reset_refuse_get(self):
        self.client.force_login(self.admin)
        link_id = get_footer_links()[0]["id"]
        for name, args in (
            ("footer_link_move", [link_id, "down"]),
            ("footer_link_delete", [link_id]),
            ("footer_links_reset", []),
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(f"pages:pages_manage:{name}", args=args))
                self.assertEqual(response.status_code, 405)

    def test_editing_an_unknown_link_is_a_404(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("pages:pages_manage:footer_link_edit", args=["nope"]))
        self.assertEqual(response.status_code, 404)

    def test_reset_button_only_shows_for_a_saved_list(self):
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(self.list_url), "Reset to default")
        manage_services.save_footer_link(self.admin, label="Facebook", url="https://example.com/fb")
        self.assertContains(self.client.get(self.list_url), "Reset to default")
