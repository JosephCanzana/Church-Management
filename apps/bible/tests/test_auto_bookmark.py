"""Tests for the auto bookmark (Brick 2): quiet 204 for background posts, GET never writes, no timer without text."""
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Extension, Role, User
from apps.bible.models import BibleBookmark, BibleChapterRead, BibleVerse
from apps.bible.services import get_bookmark


def make_reader(username_part="reader"):
    """Create a signed-in-capable member (activated, no password change pending)."""
    extension, _ = Extension.objects.get_or_create(name="alpha")
    return User.objects.create_user(
        password="pass-12345", first_name=username_part, last_name="test", role=Role.MEMBER,
        extension=extension, must_change_password=False, person_type="member_account",
    )


class AutoBookmarkTests(TestCase):
    """bible:bookmark answers background requests quietly and normal forms as before."""

    def setUp(self):
        self.user = make_reader()
        self.client.force_login(self.user)
        self.url = reverse("bible:bookmark", args=["Gen", 3])

    def test_background_post_returns_204_and_moves_bookmark_without_message(self):
        response = self.client.post(self.url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 204)
        bookmark = get_bookmark(self.user)
        self.assertEqual((bookmark.book, bookmark.chapter), ("Gen", 3))
        self.assertEqual(list(get_messages(response.wsgi_request)), [])

    def test_json_accept_header_also_gets_204(self):
        response = self.client.post(self.url, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(get_bookmark(self.user).chapter, 3)

    def test_background_post_never_counts_a_read(self):
        self.client.post(self.url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertFalse(BibleChapterRead.objects.filter(user=self.user).exists())

    def test_normal_post_still_redirects_with_a_message(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)], ["Bookmarked Genesis 3."])

    def test_get_is_not_allowed_and_writes_nothing(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)
        self.assertFalse(BibleBookmark.objects.filter(user=self.user).exists())

    def test_unknown_chapter_is_404_even_for_background_posts(self):
        response = self.client.post(reverse("bible:bookmark", args=["Gen", 51]), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 404)

    def test_anonymous_cannot_bookmark(self):
        self.client.logout()
        response = self.client.post(self.url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertNotEqual(response.status_code, 204)
        self.assertFalse(BibleBookmark.objects.exists())


class ChapterPageTests(TestCase):
    """The chapter page: timer only with text, GET writes nothing, removed buttons are gone."""

    def setUp(self):
        self.user = make_reader()
        self.client.force_login(self.user)
        self.client.cookies["bible_version"] = "KJV"
        self.url = reverse("bible:chapter", args=["Gen", 1])

    def test_text_showing_renders_the_timer_component(self):
        BibleVerse.objects.create(translation="KJV", book="Gen", chapter=1, verse=1, text="In the beginning.")
        response = self.client.get(self.url)
        self.assertContains(response, "data-auto-bookmark")
        self.assertContains(response, reverse("bible:bookmark", args=["Gen", 1]))

    def test_get_never_writes(self):
        BibleVerse.objects.create(translation="KJV", book="Gen", chapter=1, verse=1, text="In the beginning.")
        self.client.get(self.url)
        self.assertFalse(BibleBookmark.objects.filter(user=self.user).exists())
        self.assertFalse(BibleChapterRead.objects.filter(user=self.user).exists())

    def test_no_text_means_no_timer(self):
        """Text not imported takes the same branch as a locked version: no article, no component."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-auto-bookmark")

    def test_removed_controls_are_gone_and_next_stays(self):
        BibleVerse.objects.create(translation="KJV", book="Gen", chapter=1, verse=1, text="In the beginning.")
        response = self.client.get(self.url)
        self.assertNotContains(response, "done for now")
        self.assertNotContains(response, "mark as read")
        self.assertContains(response, 'name="after" value="next"')
        self.assertContains(response, ">Bookmark<")
