"""bible.daily_verse_refs: the curated list the verse of the day is chosen from.

Only references live here (OSIS book id, chapter, first verse, optional last
verse), never any Bible text. The text is looked up when the page is shown, in
the version the reader picked, so the licensed ESV text is never stored.

Verse numbers follow the KJV (the reference translation, see
``apps/bible/daily_verse.py``). To change the rotation, edit this list: the verse
for a day is ``DAILY_VERSE_REFS[date.toordinal() % len(DAILY_VERSE_REFS)]``, so
adding or removing entries shifts which verse a future day gets, but days that
already have a ``daily_verse`` row keep theirs.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class VerseRef:
    """One passage: a single verse, or a short range when ``end`` is set."""

    book: str  # OSIS id, see apps/bible/books.py
    chapter: int
    start: int
    end: int | None = None  # None means a single verse


DAILY_VERSE_REFS = (
    VerseRef("John", 3, 16),
    VerseRef("Ps", 23, 1, 3),
    VerseRef("Prov", 3, 5, 6),
    VerseRef("Phil", 4, 13),
    VerseRef("Jer", 29, 11),
    VerseRef("Rom", 8, 28),
    VerseRef("Isa", 41, 10),
    VerseRef("Josh", 1, 9),
    VerseRef("Ps", 46, 1),
    VerseRef("Matt", 11, 28, 30),
    VerseRef("Rom", 12, 2),
    VerseRef("Gal", 2, 20),
    VerseRef("Eph", 2, 8, 9),
    VerseRef("2Tim", 1, 7),
    VerseRef("Heb", 11, 1),
    VerseRef("Ps", 119, 105),
    VerseRef("Matt", 6, 33),
    VerseRef("Phil", 4, 6, 7),
    VerseRef("1John", 1, 9),
    VerseRef("Rom", 5, 8),
    VerseRef("Ps", 27, 1),
    VerseRef("Isa", 40, 31),
    VerseRef("Lam", 3, 22, 23),
    VerseRef("Mic", 6, 8),
    VerseRef("Matt", 5, 16),
    VerseRef("John", 14, 6),
    VerseRef("John", 14, 27),
    VerseRef("Rom", 8, 38, 39),
    VerseRef("1Cor", 13, 4, 7),
    VerseRef("Ps", 121, 1, 2),
    VerseRef("Ps", 37, 4, 5),
    VerseRef("Prov", 16, 3),
    VerseRef("Prov", 22, 6),
    VerseRef("Ps", 34, 8),
    VerseRef("Ps", 100, 4, 5),
    VerseRef("Ps", 91, 1, 2),
    VerseRef("Ps", 139, 13, 14),
    VerseRef("Isa", 26, 3),
    VerseRef("Isa", 53, 5),
    VerseRef("Deut", 31, 6),
    VerseRef("Matt", 28, 19, 20),
    VerseRef("Acts", 1, 8),
    VerseRef("1Cor", 10, 13),
    VerseRef("2Cor", 5, 17),
    VerseRef("2Cor", 12, 9),
    VerseRef("Gal", 5, 22, 23),
    VerseRef("Col", 3, 23),
    VerseRef("1Thess", 5, 16, 18),
    VerseRef("Heb", 4, 12),
    VerseRef("Heb", 12, 1, 2),
    VerseRef("Jas", 1, 5),
    VerseRef("1Pet", 5, 7),
    VerseRef("Rev", 3, 20),
    VerseRef("Matt", 7, 7, 8),
    VerseRef("Luke", 1, 37),
    VerseRef("John", 15, 5),
    VerseRef("John", 10, 10),
    VerseRef("Rom", 10, 9),
    VerseRef("Ps", 118, 24),
    VerseRef("Prov", 18, 10),
    VerseRef("Num", 6, 24, 26),
    VerseRef("Ps", 19, 14),
    VerseRef("Zeph", 3, 17),
)
