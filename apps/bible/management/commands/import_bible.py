"""Load a public domain Bible (OSIS XML) into ``bible_verse``.

Examples (run inside the web container)::

    manage.py import_bible KJV --url https://raw.githubusercontent.com/seven1m/open-bibles/master/eng-kjv.osis.xml
    manage.py import_bible ADB --file /app/data/tgl-tagalog.osis.xml

Safe to run again: the whole translation is replaced in one transaction, so a
failed run leaves the old text untouched. Only public domain translations may
be imported (ESV and MBBTAG are refused).
"""
import tempfile
import urllib.request

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.bible.models import LOCAL_TRANSLATIONS, BibleVerse
from apps.bible.osis import OsisError, check_canon, parse_osis

BATCH_SIZE = 2000
DOWNLOAD_TIMEOUT_SECONDS = 60


class Command(BaseCommand):
    """manage.py import_bible <translation> (--file PATH | --url URL)."""

    help = "Import a public domain Bible from an OSIS XML file into bible_verse."

    def add_arguments(self, parser):
        """Declare the translation code and where the file comes from."""
        parser.add_argument("translation", choices=[t.value for t in LOCAL_TRANSLATIONS])
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--file", help="Path to an OSIS XML file.")
        source.add_argument("--url", help="URL of an OSIS XML file to download.")
        parser.add_argument(
            "--allow-partial", action="store_true",
            help="Import even if some books or chapters are missing (tests only).",
        )

    def handle(self, *args, **options):
        """Parse, check and replace the translation's verses in one transaction."""
        translation = options["translation"]
        try:
            result = self._parse(options)
        except (OsisError, OSError) as error:
            raise CommandError(f"Could not read the Bible file: {error}") from error

        problems = check_canon(result.verses)
        if problems and not options["allow_partial"]:
            shown = "; ".join(problems[:5])
            raise CommandError(
                f"The file is incomplete ({len(problems)} problem(s)): {shown}. Nothing was imported."
            )

        rows = [
            BibleVerse(translation=translation, book=v.book, chapter=v.chapter, verse=v.verse, text=v.text)
            for v in result.verses
        ]
        with transaction.atomic():
            BibleVerse.objects.filter(translation=translation).delete()
            BibleVerse.objects.bulk_create(rows, batch_size=BATCH_SIZE)

        if result.ignored_books:
            self.stdout.write(f"Ignored non-canonical books: {', '.join(sorted(result.ignored_books))}")
        self.stdout.write(self.style.SUCCESS(f"Imported {len(rows)} verses for {translation}."))

    def _parse(self, options):
        """Read the file from disk, or download it to a temp file first."""
        if options["file"]:
            return parse_osis(options["file"])
        with tempfile.TemporaryFile() as temp:
            with urllib.request.urlopen(options["url"], timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                temp.write(response.read())
            temp.seek(0)
            return parse_osis(temp)