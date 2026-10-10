"""The 66 books of the Protestant canon, in reading order.

The ``osis_id`` is the key used everywhere in the ``bible`` app (``bible_verse``,
``bible_chapter_read``, ``daily_verse``): it is short, stable and the same in
every translation, so a chapter read in KJV counts as the same chapter read in
ESV. ``name`` is the English display name (also what the ESV API expects in a
passage query). ``chapters`` is used to check an imported text is complete.
"""
from dataclasses import dataclass

OLD = "old"
NEW = "new"


@dataclass(frozen=True)
class Book:
    """One book of the Bible."""

    osis_id: str
    name: str
    chapters: int
    testament: str


# (osis_id, English name, number of chapters). Old Testament first (39 books).
_OLD_TESTAMENT = [
    ("Gen", "Genesis", 50), ("Exod", "Exodus", 40), ("Lev", "Leviticus", 27),
    ("Num", "Numbers", 36), ("Deut", "Deuteronomy", 34), ("Josh", "Joshua", 24),
    ("Judg", "Judges", 21), ("Ruth", "Ruth", 4), ("1Sam", "1 Samuel", 31),
    ("2Sam", "2 Samuel", 24), ("1Kgs", "1 Kings", 22), ("2Kgs", "2 Kings", 25),
    ("1Chr", "1 Chronicles", 29), ("2Chr", "2 Chronicles", 36), ("Ezra", "Ezra", 10),
    ("Neh", "Nehemiah", 13), ("Esth", "Esther", 10), ("Job", "Job", 42),
    ("Ps", "Psalms", 150), ("Prov", "Proverbs", 31), ("Eccl", "Ecclesiastes", 12),
    ("Song", "Song of Solomon", 8), ("Isa", "Isaiah", 66), ("Jer", "Jeremiah", 52),
    ("Lam", "Lamentations", 5), ("Ezek", "Ezekiel", 48), ("Dan", "Daniel", 12),
    ("Hos", "Hosea", 14), ("Joel", "Joel", 3), ("Amos", "Amos", 9),
    ("Obad", "Obadiah", 1), ("Jonah", "Jonah", 4), ("Mic", "Micah", 7),
    ("Nah", "Nahum", 3), ("Hab", "Habakkuk", 3), ("Zeph", "Zephaniah", 3),
    ("Hag", "Haggai", 2), ("Zech", "Zechariah", 14), ("Mal", "Malachi", 4),
]

# New Testament (27 books).
_NEW_TESTAMENT = [
    ("Matt", "Matthew", 28), ("Mark", "Mark", 16), ("Luke", "Luke", 24),
    ("John", "John", 21), ("Acts", "Acts", 28), ("Rom", "Romans", 16),
    ("1Cor", "1 Corinthians", 16), ("2Cor", "2 Corinthians", 13),
    ("Gal", "Galatians", 6), ("Eph", "Ephesians", 6), ("Phil", "Philippians", 4),
    ("Col", "Colossians", 4), ("1Thess", "1 Thessalonians", 5),
    ("2Thess", "2 Thessalonians", 3), ("1Tim", "1 Timothy", 6),
    ("2Tim", "2 Timothy", 4), ("Titus", "Titus", 3), ("Phlm", "Philemon", 1),
    ("Heb", "Hebrews", 13), ("Jas", "James", 5), ("1Pet", "1 Peter", 5),
    ("2Pet", "2 Peter", 3), ("1John", "1 John", 5), ("2John", "2 John", 1),
    ("3John", "3 John", 1), ("Jude", "Jude", 1), ("Rev", "Revelation", 22),
]

BOOKS = tuple(
    [Book(i, n, c, OLD) for i, n, c in _OLD_TESTAMENT]
    + [Book(i, n, c, NEW) for i, n, c in _NEW_TESTAMENT]
)
BOOKS_BY_ID = {book.osis_id: book for book in BOOKS}
BOOK_ORDER = {book.osis_id: index for index, book in enumerate(BOOKS)}

TOTAL_CHAPTERS = sum(book.chapters for book in BOOKS)  # 1,189


def get_book(osis_id):
    """Return the Book for an OSIS id, or None when it is not in the canon."""
    return BOOKS_BY_ID.get(osis_id)