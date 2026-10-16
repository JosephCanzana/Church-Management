"""Tests for the in-app /about/ page, its context and its nav item."""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.navigation import ADMIN, COORDINATOR, MEMBER, SUPER_ADMIN, build_nav
from apps.pages.models import ChurchContent
from apps.pages.services import DEFAULT_ABOUT, get_about_context


class AboutContextTests(TestCase):
    def test_defaults_when_nothing_is_managed(self):
        about = get_about_context()["about"]
        self.assertEqual(about["mission"]["body"], DEFAULT_ABOUT["mission"]["body"])
        self.assertEqual(about["vision"]["title"], DEFAULT_ABOUT["vision"]["title"])

    def test_managed_content_wins(self):
        ChurchContent.objects.create(key="mission", language="en", title="", body="Managed mission")
        about = get_about_context()["about"]
        self.assertEqual(about["mission"]["body"], "Managed mission")
        self.assertEqual(about["mission"]["title"], DEFAULT_ABOUT["mission"]["title"])

    def test_core_values_are_one_item_per_line(self):
        ChurchContent.objects.create(key="core_values", language="en", body="Faith\n\n  Hope \nLove\n")
        self.assertEqual(get_about_context()["about"]["values"]["items"], ["Faith", "Hope", "Love"])


class AboutAccessTests(TestCase):
    def test_logged_out_visitor_is_sent_to_login(self):
        response = self.client.get(reverse("pages:about"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_url_is_about(self):
        self.assertEqual(reverse("pages:about"), "/about/")


class AboutNavTests(TestCase):
    def test_every_role_has_an_enabled_about_item(self):
        for role in (SUPER_ADMIN, ADMIN, COORDINATOR, MEMBER):
            nav = build_nav(role, "/")
            items = [e for section in nav["sections"] for e in section["items"] if e["label"] == "About"]
            self.assertEqual(len(items), 1, role)
            self.assertFalse(items[0]["disabled"], role)
            self.assertEqual(items[0]["href"], reverse("pages:about"))
