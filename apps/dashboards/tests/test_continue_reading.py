"""Tests for the dashboard "Continue reading" card (Brick 2): it follows the bookmark."""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Extension, Role, User
from apps.bible.models import BibleBookmark
from apps.dashboards import services


class ContinueReadingBookmarkTests(TestCase):
    """The card is the person's bookmark; no bookmark means a Start reading link to Genesis 1."""

    def setUp(self):
        extension = Extension.objects.create(name="alpha")
        self.member = User.objects.create_user(
            password="pass-12345", first_name="test", last_name="member", role=Role.MEMBER,
            extension=extension, must_change_password=False, person_type="member_account",
        )
        self.client.force_login(self.member)

    def test_bookmark_is_shown(self):
        BibleBookmark.objects.create(user=self.member, book="Exod", chapter=20)
        card = services.get_dashboard_context(self.member, None, "/")["continue_reading"]
        self.assertEqual(card["label"], "Exodus 20")
        self.assertEqual(card["url"], reverse("bible:chapter", args=["Exod", 20]))

    def test_no_bookmark_is_none_and_page_offers_start_reading(self):
        context = services.get_dashboard_context(self.member, None, "/")
        self.assertIsNone(context["continue_reading"])
        self.assertEqual(context["start_url"], reverse("bible:chapter", args=["Gen", 1]))
        response = self.client.get(reverse("dashboards:member"))
        self.assertContains(response, "Start reading")
        self.assertContains(response, reverse("bible:chapter", args=["Gen", 1]))

    def test_page_links_to_the_bookmark(self):
        BibleBookmark.objects.create(user=self.member, book="Exod", chapter=1)
        response = self.client.get(reverse("dashboards:member"))
        self.assertContains(response, "Exodus 1")
        self.assertNotContains(response, "Start reading")

    def test_unknown_book_in_bookmark_falls_back_to_start(self):
        BibleBookmark.objects.create(user=self.member, book="Zzz", chapter=1)
        self.assertIsNone(services.get_dashboard_context(self.member, None, "/")["continue_reading"])
