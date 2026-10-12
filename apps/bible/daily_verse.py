"""bible.daily_verse: choose, store and read the verse of the day.

How it works:

* The verse for a Manila calendar day is picked from the curated list in
  ``daily_verse_refs.py`` by the date, so every person sees the same verse that
  day and the choice never needs a scheduled job.
* The choice is saved as a ``daily_verse`` row (a reference only). A row that
  already exists for the day always wins, so a verse set by hand later is kept.
* ``verse_text`` is never written. The text is looked up each time through
  ``services.get_chapter()`` in the version the reader chose, so the version
  switches, the ESV locks and the ESV cache rules all apply, and licensed ESV
  text is never stored here.
* ``daily_verse.translation`` is required by the table, so rows are saved with
  KJV as the *reference* translation (its verse numbers). It does not decide
  which version is shown.
"""
from dataclasses import dataclass

from django.utils import timezone

from . import services
from .books import BOOKS_BY_ID
from .daily_verse_refs import DAILY_VERSE_REFS
from .errors import BibleError, TextNotImported, VersionLocked
from .models import DailyVerse, Translation

# Stored in daily_verse.translation; says whose verse numbers the reference uses.
REFERENCE_TRANSLATION = Translation.KJV


@dataclass(frozen=True)
class DailyVerseText:
    """A verse of the day ready to show.

    ``text`` is empty when the chosen version cannot be read right now; then
    ``note`` says why (shown instead of text, never a silent switch of version).
    """

    reference: str
    version: object
    text: str
    note: str = ""

    @property
    def available(self):
        """True when there is text to show."""
        return bool(self.text)


def verse_for_date(day):
    """Return the curated VerseRef for a date (same date, same verse, always)."""
    return DAILY_VERSE_REFS[day.toordinal() % len(DAILY_VERSE_REFS)]


def get_daily_verse(day=None):
    """Return the ``DailyVerse`` row for ``day`` (default: today, Manila), creating it once.

    ``get_or_create`` makes this safe when two requests arrive together: the
    primary key is the date, so only one row can exist.
    """
    day = day or timezone.localdate()
    ref = verse_for_date(day)
    row, _created = DailyVerse.objects.get_or_create(
        verse_date=day,
        defaults={
            "translation": REFERENCE_TRANSLATION,
            "book": ref.book,
            "chapter": ref.chapter,
            "verse_start": ref.start,
            "verse_end": ref.end,
        },
    )
    return row


def reference_label(row):
    """Return the reference as people read it, for example ``John 3:16`` or ``Psalms 23:1–3``."""
    book = BOOKS_BY_ID.get(row.book)
    label = f"{book.name if book else row.book} {row.chapter}:{row.verse_start}"
    if row.verse_end and row.verse_end != row.verse_start:
        label += f"–{row.verse_end}"
    return label


def get_daily_verse_text(row, version):
    """Return the verse's text in ``version`` as a ``DailyVerseText``; never raises for reader problems.

    A locked, switched-off or not-yet-imported version gives an empty text and a
    note, because a saved version that cannot be read must show a notice, not
    another version (see the Bible section of AGENTS.md).
    """
    reference = reference_label(row)
    try:
        chapter = services.get_chapter(version.code, row.book, row.chapter)
    except (VersionLocked, TextNotImported, BibleError) as error:
        return DailyVerseText(reference, version, "", str(error))
    last = row.verse_end or row.verse_start
    parts = [text.strip() for number, text in chapter.verses if row.verse_start <= number <= last]
    if not parts:
        return DailyVerseText(reference, version, "", f"{version.label} text for this verse is not available.")
    return DailyVerseText(reference, version, " ".join(parts))
