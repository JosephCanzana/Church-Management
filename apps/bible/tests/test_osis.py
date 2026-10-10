"""Tests for the OSIS reader (apps/bible/osis.py): both verse formats, notes,
ignored books and the refusals that keep a bad file from half-importing."""
import io

from django.test import SimpleTestCase

from apps.bible.books import BOOKS, TOTAL_CHAPTERS
from apps.bible.osis import OsisError, ParsedVerse, check_canon, parse_osis

NS = "http://www.bibletechnologies.net/2003/OSIS/namespace"


def osis(body):
    """Wrap book markup in a minimal OSIS document and return a file object."""
    xml = f"<osis xmlns='{NS}'><osisText>{body}</osisText></osis>"
    return io.BytesIO(xml.encode("utf-8"))


class BookTableTests(SimpleTestCase):
    """The static book table must describe the standard 66-book canon."""

    def test_counts(self):
        self.assertEqual(len(BOOKS), 66)
        self.assertEqual(TOTAL_CHAPTERS, 1189)


class ParseFormatsTests(SimpleTestCase):
    """Container and milestone verses give the same plain result."""

    def test_container_verses(self):
        result = parse_osis(osis(
            "<div type='book' osisID='Gen'><chapter osisID='Gen.1'>"
            "<verse osisID='Gen.1.1'>In the  beginning</verse>"
            "<verse osisID='Gen.1.2'>And the earth</verse></chapter></div>"
        ))
        self.assertEqual(result.verses[0], ParsedVerse("Gen", 1, 1, "In the beginning"))
        self.assertEqual(len(result.verses), 2)

    def test_milestone_verses_with_inline_markup_and_notes(self):
        result = parse_osis(osis(
            "<div type='book' osisID='John'><chapter sID='John.3' osisID='John.3'/>"
            "<p><verse sID='a' osisID='John.3.16'/>For God so "
            "<transChange type='added'>loved</transChange> the world"
            "<note>a translator note</note>, that he gave.<verse eID='a'/>"
            "<verse sID='b' osisID='John.3.17'/>For God sent not.<verse eID='b'/></p>"
            "<chapter eID='John.3'/></div>"
        ))
        texts = {(v.chapter, v.verse): v.text for v in result.verses}
        self.assertEqual(texts[(3, 16)], "For God so loved the world, that he gave.")
        self.assertEqual(texts[(3, 17)], "For God sent not.")

    def test_text_between_verses_is_not_attached_to_a_verse(self):
        result = parse_osis(osis(
            "<div type='book' osisID='Ps'><chapter osisID='Ps.3'>"
            "<title>A Psalm of David</title>"
            "<verse osisID='Ps.3.1'>Lord, how are they increased</verse></chapter></div>"
        ))
        self.assertEqual([v.text for v in result.verses], ["Lord, how are they increased"])

    def test_non_canonical_books_are_ignored_and_reported(self):
        result = parse_osis(osis(
            "<div type='book' osisID='Tob'><chapter osisID='Tob.1'>"
            "<verse osisID='Tob.1.1'>Tobit text</verse></chapter></div>"
        ))
        self.assertEqual(result.verses, [])
        self.assertEqual(result.ignored_books, {"Tob"})


class ParseRefusalTests(SimpleTestCase):
    """Anything ambiguous raises instead of guessing."""

    def test_verse_range_is_refused(self):
        with self.assertRaises(OsisError):
            parse_osis(osis("<verse osisID='Gen.1.1-Gen.1.2'>text</verse>"))

    def test_empty_verse_is_refused(self):
        with self.assertRaises(OsisError):
            parse_osis(osis("<verse osisID='Gen.1.1'>   </verse>"))

    def test_invalid_xml_is_refused(self):
        with self.assertRaises(OsisError):
            parse_osis(io.BytesIO(b"<osis><broken>"))


class CheckCanonTests(SimpleTestCase):
    """check_canon reports missing books, short books and duplicates."""

    def test_reports_everything_missing_for_an_empty_text(self):
        self.assertEqual(len(check_canon([])), 66)

    def test_reports_short_book_and_duplicate(self):
        verses = [ParsedVerse("Gen", 1, 1, "a"), ParsedVerse("Gen", 1, 1, "b")]
        problems = check_canon(verses)
        self.assertIn("Duplicate verse Gen 1:1", problems)
        self.assertTrue(any(p.startswith("Gen: expected chapters 1-50") for p in problems))