"""Tests for `manage.py import_bible` and the bible_verse constraints."""
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.bible.models import BibleVerse

NS = "http://www.bibletechnologies.net/2003/OSIS/namespace"
SAMPLE = (
    f"<osis xmlns='{NS}'><osisText><div type='book' osisID='Gen'><chapter osisID='Gen.1'>"
    "<verse osisID='Gen.1.1'>In the beginning</verse>"
    "<verse osisID='Gen.1.2'>And the earth</verse></chapter></div></osisText></osis>"
)


def write_sample(text=SAMPLE):
    """Write OSIS text to a temp file and return its path."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


class ImportBibleTests(TestCase):
    """Importing loads verses, replaces them on a re-run and refuses bad input."""

    def tearDown(self):
        for path in Path(tempfile.gettempdir()).glob("tmp*.xml"):
            path.unlink(missing_ok=True)

    def test_partial_file_is_refused_by_default(self):
        with self.assertRaises(CommandError):
            call_command("import_bible", "KJV", file=write_sample())
        self.assertEqual(BibleVerse.objects.count(), 0)

    def test_import_and_reimport_replace_the_translation(self):
        path = write_sample()
        call_command("import_bible", "KJV", file=path, allow_partial=True)
        call_command("import_bible", "KJV", file=path, allow_partial=True)
        self.assertEqual(BibleVerse.objects.filter(translation="KJV").count(), 2)

    def test_other_translations_are_left_alone(self):
        call_command("import_bible", "ADB", file=write_sample(), allow_partial=True)
        call_command("import_bible", "KJV", file=write_sample(), allow_partial=True)
        self.assertEqual(BibleVerse.objects.filter(translation="ADB").count(), 2)

    def test_esv_cannot_be_imported(self):
        with self.assertRaises(CommandError):
            call_command("import_bible", "ESV", file=write_sample(), allow_partial=True)

    def test_database_refuses_esv_text(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            BibleVerse.objects.create(translation="ESV", book="Gen", chapter=1, verse=1, text="x")

    def test_database_refuses_duplicate_verse(self):
        BibleVerse.objects.create(translation="KJV", book="Gen", chapter=1, verse=1, text="x")
        with self.assertRaises(IntegrityError), transaction.atomic():
            BibleVerse.objects.create(translation="KJV", book="Gen", chapter=1, verse=1, text="y")