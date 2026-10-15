"""Tests for bible.services and the chapter-navigation helper."""
from datetime import date
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from apps.bible.books import neighbor_chapters
from apps.bible.models import BibleChapterRead, BibleBookmark
from apps.bible.services import (
    BibleError, TextNotImported, VersionLocked, get_chapter, record_chapter_read, version_statuses, get_bookmark, finish_chapter
)
from apps.bible.signals import chapter_read

from .helpers import add_chapter, make_user


class NeighborChapterTests(SimpleTestCase):
    """Previous/next chapter links, including across books."""

    def test_inside_a_book(self):
        self.assertEqual(neighbor_chapters("Gen", 2), (("Gen", 1), ("Gen", 3)))

    def test_start_and_end_of_the_bible(self):
        self.assertEqual(neighbor_chapters("Gen", 1), (None, ("Gen", 2)))
        self.assertEqual(neighbor_chapters("Rev", 22), (("Rev", 21), None))

    def test_across_books(self):
        self.assertEqual(neighbor_chapters("Gen", 50)[1], ("Exod", 1))
        self.assertEqual(neighbor_chapters("Exod", 1)[0], ("Gen", 50))
        self.assertEqual(neighbor_chapters("Mal", 4)[1], ("Matt", 1))


@override_settings(ESV_API_KEY="")
class VersionStatusTests(SimpleTestCase):
    """Local versions are open; ESV is locked while it has no key."""

    def test_statuses(self):
        by_code = {s.version.code: s for s in version_statuses()}
        self.assertTrue(by_code["KJV"].available)
        self.assertTrue(by_code["ADB"].available)
        self.assertFalse(by_code["ESV"].available)
        self.assertTrue(by_code["ESV"].reason)


@override_settings(ESV_API_KEY="")
class GetChapterTests(TestCase):
    """Reading a chapter from the local text."""

    def test_returns_verses_in_order(self):
        add_chapter(verses=("One.", "Two.", "Three."))
        chapter = get_chapter("KJV", "Gen", 1)
        self.assertEqual(chapter.verses, [(1, "One."), (2, "Two."), (3, "Three.")])
        self.assertEqual(chapter.book.name, "Genesis")

    def test_bad_references(self):
        with self.assertRaises(BibleError):
            get_chapter("KJV", "Nope", 1)
        with self.assertRaises(BibleError):
            get_chapter("KJV", "Gen", 51)
        with self.assertRaises(BibleError):
            get_chapter("XYZ", "Gen", 1)

    def test_locked_and_not_imported(self):
        with self.assertRaises(VersionLocked):
            get_chapter("ESV", "Gen", 1)
        with self.assertRaises(TextNotImported):
            get_chapter("ADB", "Gen", 1)

    def test_versions_do_not_mix(self):
        add_chapter("KJV", verses=("English.",))
        add_chapter("ADB", verses=("Tagalog.",))
        self.assertEqual(get_chapter("ADB", "Gen", 1).verses, [(1, "Tagalog.")])


class RecordChapterReadTests(TestCase):
    """One row per person, book, chapter and Manila day, whatever the version."""

    def setUp(self):
        self.user = make_user()
        self.received = []
        chapter_read.connect(self._receiver, dispatch_uid="test_receiver")
        self.addCleanup(chapter_read.disconnect, dispatch_uid="test_receiver")

    def _receiver(self, sender, entry, **kwargs):
        self.received.append(entry)

    def test_first_read_creates_and_signals(self):
        entry, created = record_chapter_read(self.user, "KJV", "Gen", 1)
        self.assertTrue(created)
        self.assertEqual(entry.translation, "KJV")
        self.assertEqual(self.received, [entry])

    def test_second_press_and_other_version_count_once(self):
        record_chapter_read(self.user, "KJV", "Gen", 1)
        _, created_again = record_chapter_read(self.user, "KJV", "Gen", 1)
        _, created_other = record_chapter_read(self.user, "ADB", "Gen", 1)
        self.assertFalse(created_again)
        self.assertFalse(created_other)
        self.assertEqual(BibleChapterRead.objects.count(), 1)
        self.assertEqual(len(self.received), 1)

    def test_a_new_day_is_a_new_read(self):
        with mock.patch("apps.bible.services.timezone.localdate", return_value=date(2026, 10, 10)):
            record_chapter_read(self.user, "KJV", "Gen", 1)
        with mock.patch("apps.bible.services.timezone.localdate", return_value=date(2026, 10, 11)):
            _, created = record_chapter_read(self.user, "KJV", "Gen", 1)
        self.assertTrue(created)
        self.assertEqual(BibleChapterRead.objects.count(), 2)

    def test_refuses_bad_reference_and_unknown_version(self):
        with self.assertRaises(BibleError):
            record_chapter_read(self.user, "KJV", "Gen", 99)
        with self.assertRaises(BibleError):
            record_chapter_read(self.user, "KJV", "Nope", 1)
        with self.assertRaises(BibleError):
            record_chapter_read(self.user, "XYZ", "Gen", 1)
        self.assertEqual(BibleChapterRead.objects.count(), 0)
        self.assertEqual(self.received, [])

    @override_settings(ESV_API_KEY="")
    def test_a_locked_version_does_not_block_recording(self):
        # The page only offers the button after showing text, which can come from the
        # ESV cache while ESV is locked.
        _, created = record_chapter_read(self.user, "ESV", "Gen", 1)
        self.assertTrue(created)

class FinishChapterTests(TestCase):
    """Next counts the chapter as read and moves the bookmark."""

    def setUp(self):
        self.user = make_user()

    def test_counts_read_and_moves_bookmark(self):
        _, created, following = finish_chapter(self.user, "KJV", "Gen", 1)
        self.assertTrue(created)
        self.assertEqual(following, ("Gen", 2))
        bookmark = get_bookmark(self.user)
        self.assertEqual((bookmark.book, bookmark.chapter), ("Gen", 2))

    def test_crosses_books_and_keeps_one_row(self):
        finish_chapter(self.user, "KJV", "Gen", 50)
        self.assertEqual((get_bookmark(self.user).book, get_bookmark(self.user).chapter), ("Exod", 1))
        self.assertEqual(BibleBookmark.objects.filter(user=self.user).count(), 1)

    def test_last_chapter_keeps_bookmark(self):
        _, _, following = finish_chapter(self.user, "KJV", "Rev", 22)
        self.assertIsNone(following)
        self.assertEqual(get_bookmark(self.user).chapter, 22)

    def test_repeat_does_not_count_twice_but_still_moves(self):
        finish_chapter(self.user, "KJV", "Gen", 1)
        _, created, _ = finish_chapter(self.user, "KJV", "Gen", 1)
        self.assertFalse(created)
        self.assertEqual(BibleChapterRead.objects.count(), 1)