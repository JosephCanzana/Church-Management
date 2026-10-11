"""Tests for the reader views: access, version cookie, locked ESV, mark as read."""
from django.test import TestCase
from django.urls import reverse

from apps.bible.models import BibleChapterRead
from apps.bible.views import VERSION_COOKIE

from .helpers import add_chapter, make_user


class ReaderAccessTests(TestCase):
    """Everything needs a login; signed-in people see the books."""

    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("bible:reader"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_reader_lists_all_books_and_versions(self):
        self.client.force_login(make_user())
        response = self.client.get(reverse("bible:reader"))
        self.assertContains(response, "Genesis")
        self.assertContains(response, "Revelation")
        self.assertContains(response, "Ang Dating Biblia")

    def test_book_page_lists_chapters(self):
        self.client.force_login(make_user())
        response = self.client.get(reverse("bible:book", args=["Gen"]))
        self.assertEqual(len(response.context["chapters"]), 50)


class ChapterViewTests(TestCase):
    """Showing a chapter in the version from the cookie."""

    def setUp(self):
        self.client.force_login(make_user())
        add_chapter("KJV", verses=("In the beginning.", "And the earth."))
        add_chapter("ADB", verses=("Nang unang panahon.",))
        self.url = reverse("bible:chapter", args=["Gen", 1])

    def test_default_is_kjv(self):
        response = self.client.get(self.url)
        self.assertContains(response, "In the beginning.")
        self.assertContains(response, 'lang="en"')
        self.assertContains(response, "Public domain")

    def test_cookie_switches_to_tagalog(self):
        self.client.cookies[VERSION_COOKIE] = "ADB"
        response = self.client.get(self.url)
        self.assertContains(response, "Nang unang panahon.")
        self.assertContains(response, 'lang="tl"')

    def test_tampered_cookie_falls_back_to_default(self):
        self.client.cookies[VERSION_COOKIE] = "<script>"
        self.assertContains(self.client.get(self.url), "In the beginning.")

    def test_unknown_book_and_chapter_are_404(self):
        self.assertEqual(self.client.get(reverse("bible:chapter", args=["Nope", 1])).status_code, 404)
        self.assertEqual(self.client.get(reverse("bible:chapter", args=["Gen", 51])).status_code, 404)
        self.assertEqual(self.client.get(reverse("bible:chapter", args=["Gen", 0])).status_code, 404)

    def test_saved_esv_shows_locked_notice_not_text(self):
        self.client.cookies[VERSION_COOKIE] = "ESV"
        response = self.client.get(self.url)
        self.assertContains(response, "is locked right now")
        self.assertNotContains(response, "In the beginning.")

    def test_version_not_imported_shows_a_notice(self):
        self.client.cookies[VERSION_COOKIE] = "ADB"
        response = self.client.get(reverse("bible:chapter", args=["Exod", 1]))
        self.assertContains(response, "has not been loaded yet")

    def test_previous_and_next_links(self):
        response = self.client.get(reverse("bible:chapter", args=["Exod", 1]))
        self.assertEqual(response.context["previous"]["chapter"], 50)
        self.assertEqual(response.context["following"]["chapter"], 2)


class SetVersionTests(TestCase):
    """The picker saves the choice in a cookie, refuses locked or unknown versions."""

    def setUp(self):
        self.client.force_login(make_user())
        self.url = reverse("bible:set_version")

    def test_choosing_a_local_version_sets_the_cookie(self):
        response = self.client.post(self.url, {"version": "ADB", "next": "/bible/Gen/1/"})
        self.assertRedirects(response, "/bible/Gen/1/", fetch_redirect_response=False)
        self.assertEqual(response.cookies[VERSION_COOKIE].value, "ADB")
        self.assertTrue(response.cookies[VERSION_COOKIE]["httponly"])

    def test_locked_esv_is_refused(self):
        response = self.client.post(self.url, {"version": "ESV"}, follow=True)
        self.assertNotIn(VERSION_COOKIE, response.cookies)
        self.assertContains(response, "ESV is locked")

    def test_unknown_version_is_refused(self):
        response = self.client.post(self.url, {"version": "NIV"}, follow=True)
        self.assertNotIn(VERSION_COOKIE, response.cookies)
        self.assertContains(response, "does not exist")

    def test_open_redirect_is_blocked(self):
        response = self.client.post(self.url, {"version": "KJV", "next": "https://evil.example/"})
        self.assertRedirects(response, reverse("bible:reader"), fetch_redirect_response=False)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)


class MarkReadTests(TestCase):
    """The Mark as read button."""

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        add_chapter("KJV")
        self.url = reverse("bible:mark_read", args=["Gen", 1])

    def test_marks_once_and_is_idempotent(self):
        self.client.post(self.url)
        response = self.client.post(self.url, follow=True)
        self.assertEqual(BibleChapterRead.objects.filter(user=self.user).count(), 1)
        self.assertContains(response, "already marked as read")

    def test_chapter_page_switches_to_read_today(self):
        self.client.post(self.url)
        response = self.client.get(reverse("bible:chapter", args=["Gen", 1]))
        self.assertContains(response, "Read today")
        self.assertNotContains(response, "Mark as read")

    def test_get_is_not_allowed_and_bad_chapter_is_404(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(reverse("bible:mark_read", args=["Gen", 99])).status_code, 404)

    def test_other_people_do_not_see_my_read(self):
        self.client.post(self.url)
        self.client.force_login(make_user())
        self.assertContains(self.client.get(reverse("bible:chapter", args=["Gen", 1])), "Mark as read")