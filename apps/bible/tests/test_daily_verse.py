"""Tests for the verse of the day (apps/bible/daily_verse.py)."""
from datetime import date
from unittest.mock import patch

from django.test import TestCase

from apps.bible import services
from apps.bible.daily_verse import (
    REFERENCE_TRANSLATION,
    get_daily_verse,
    get_daily_verse_text,
    reference_label,
    verse_for_date,
)
from apps.bible.daily_verse_refs import DAILY_VERSE_REFS
from apps.bible.errors import VersionLocked
from apps.bible.models import BibleVerse, DailyVerse
from apps.bible.versions import VERSIONS_BY_CODE


class CuratedListTests(TestCase):
    """The curated list must only contain real, unique passages."""

    def test_list_has_enough_entries(self):
        """Roughly two months of verses before the rotation repeats."""
        self.assertGreaterEqual(len(DAILY_VERSE_REFS), 60)

    def test_every_reference_is_a_real_chapter_and_a_sane_range(self):
        """Each book exists, the chapter exists and the range does not run backwards."""
        for ref in DAILY_VERSE_REFS:
            services.validate_reference(ref.book, ref.chapter)  # raises BibleError if wrong
            self.assertGreaterEqual(ref.start, 1)
            if ref.end is not None:
                self.assertGreater(ref.end, ref.start, ref)

    def test_no_duplicates(self):
        """A passage appears once in the rotation."""
        self.assertEqual(len(DAILY_VERSE_REFS), len(set(DAILY_VERSE_REFS)))


class PickingTests(TestCase):
    """Choosing and saving the verse for a day."""

    def test_same_date_same_verse(self):
        """The choice depends only on the date."""
        day = date(2026, 10, 12)
        self.assertEqual(verse_for_date(day), verse_for_date(day))

    def test_rotation_wraps_around(self):
        """After a full cycle the list starts again."""
        day = date(2026, 10, 12)
        cycle = date.fromordinal(day.toordinal() + len(DAILY_VERSE_REFS))
        self.assertEqual(verse_for_date(day), verse_for_date(cycle))

    def test_row_is_saved_once_as_reference_only(self):
        """One row per day, KJV as reference translation, and never any text."""
        day = date(2026, 10, 12)
        first = get_daily_verse(day)
        second = get_daily_verse(day)
        self.assertEqual(DailyVerse.objects.filter(verse_date=day).count(), 1)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.translation, REFERENCE_TRANSLATION)
        self.assertEqual(first.verse_text, "")

    def test_existing_row_is_kept(self):
        """A verse already set for the day (for example by hand) is never replaced."""
        day = date(2026, 10, 12)
        DailyVerse.objects.create(verse_date=day, translation="KJV", book="Ruth", chapter=1, verse_start=16)
        row = get_daily_verse(day)
        self.assertEqual((row.book, row.chapter, row.verse_start), ("Ruth", 1, 16))

    def test_reference_label(self):
        """Single verses and ranges read naturally, with the book's display name."""
        single = DailyVerse(verse_date=date(2026, 1, 1), book="John", chapter=3, verse_start=16)
        ranged = DailyVerse(verse_date=date(2026, 1, 2), book="Ps", chapter=23, verse_start=1, verse_end=3)
        self.assertEqual(reference_label(single), "John 3:16")
        self.assertEqual(reference_label(ranged), "Psalms 23:1–3")


class TextLookupTests(TestCase):
    """Reading the verse text in the reader's version."""

    def setUp(self):
        self.row = DailyVerse.objects.create(
            verse_date=date(2026, 10, 12), translation="KJV", book="Ps", chapter=23, verse_start=1, verse_end=2,
        )
        for number, text in ((1, "first verse"), (2, "second verse"), (3, "third verse")):
            BibleVerse.objects.create(translation="KJV", book="Ps", chapter=23, verse=number, text=text)

    def test_range_is_joined_and_stops_at_the_end(self):
        """Verses 1-2 are shown, verse 3 is not."""
        result = get_daily_verse_text(self.row, VERSIONS_BY_CODE["KJV"])
        self.assertTrue(result.available)
        self.assertEqual(result.text, "first verse second verse")

    def test_version_not_loaded_gives_a_note_not_an_error(self):
        """A version without text shows a notice and does not raise."""
        result = get_daily_verse_text(self.row, VERSIONS_BY_CODE["ADB"])
        self.assertFalse(result.available)
        self.assertIn("has not been loaded yet", result.note)

    def test_locked_version_gives_a_note_not_another_version(self):
        """A locked version is reported, never silently swapped."""
        with patch("apps.bible.services.get_chapter", side_effect=VersionLocked("Locked for now.")):
            result = get_daily_verse_text(self.row, VERSIONS_BY_CODE["KJV"])
        self.assertFalse(result.available)
        self.assertEqual(result.note, "Locked for now.")
        self.assertEqual(result.version.code, "KJV")

    def test_missing_verse_in_chapter(self):
        """If the verses are not in the chapter text, say so."""
        far = DailyVerse.objects.create(
            verse_date=date(2026, 10, 13), translation="KJV", book="Ps", chapter=23, verse_start=40,
        )
        result = get_daily_verse_text(far, VERSIONS_BY_CODE["KJV"])
        self.assertFalse(result.available)
        self.assertIn("not available", result.note)

    def test_esv_text_comes_from_the_esv_service_and_is_never_stored(self):
        """ESV text is fetched at view time (mocked here) and not written to daily_verse."""
        with patch("apps.bible.esv.get_verses", return_value=[(1, "esv placeholder one"), (2, "esv placeholder two")]):
            result = get_daily_verse_text(self.row, VERSIONS_BY_CODE["ESV"])
        self.assertEqual(result.text, "esv placeholder one esv placeholder two")
        self.row.refresh_from_db()
        self.assertEqual(self.row.verse_text, "")
        self.assertFalse(BibleVerse.objects.filter(translation="ESV").exists())
