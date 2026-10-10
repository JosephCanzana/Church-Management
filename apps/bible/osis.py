"""Read an OSIS XML Bible (the format used by seven1m/open-bibles) into plain verses.

Two ways of marking a verse exist and both are supported:

* container: ``<verse osisID="Gen.1.1">In the beginning...</verse>`` (the Tagalog file)
* milestone: ``<verse sID=".." osisID="Gen.1.1"/> text <verse eID=".."/>`` (the KJV file)

Only the 66 canonical books are kept; anything else (the Apocrypha in the KJV
file) is counted and ignored. Translator notes are dropped; other inline markup
(italics for added words, quotes, poetry lines) is reduced to plain text.
Nothing is silently guessed: an unreadable verse id, an empty verse or a verse
range raises ``OsisError`` so a bad file never half-imports.
"""
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass

from .books import BOOKS, BOOKS_BY_ID

# "Gen.1.1" -> book, chapter, verse. A second part (range) is refused on purpose.
_VERSE_ID = re.compile(r"^(?P<book>[1-3A-Za-z]+)\.(?P<chapter>\d+)\.(?P<verse>\d+)$")
_SPACES = re.compile(r"\s+")


class OsisError(ValueError):
    """The file cannot be read safely (bad verse id, empty verse, range, ...)."""


@dataclass(frozen=True)
class ParsedVerse:
    """One verse of one canonical book."""

    book: str
    chapter: int
    verse: int
    text: str


@dataclass
class ParseResult:
    """Verses found plus what was left out."""

    verses: list
    ignored_books: set


def _local(tag):
    """Tag name without its XML namespace."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _split_id(osis_id):
    """Split ``Gen.1.1`` into (book, chapter, verse); refuse ranges and odd ids."""
    match = _VERSE_ID.match((osis_id or "").strip())
    if not match:
        raise OsisError(f"Unsupported verse id: {osis_id!r} (ranges are not supported)")
    return match["book"], int(match["chapter"]), int(match["verse"])


class _Collector:
    """Walks the XML in document order and gathers the text of each verse."""

    def __init__(self):
        self.verses = []
        self.ignored_books = set()
        self._open_id = None   # id of the milestone verse being read
        self._parts = []       # text pieces of that verse

    def _add_text(self, text):
        """Add text to the open milestone verse (text outside a verse is dropped)."""
        if self._open_id is not None and text:
            self._parts.append(text)

    def _store(self, osis_id, text):
        """Keep one finished verse (canonical books only)."""
        book, chapter, verse = _split_id(osis_id)
        if book not in BOOKS_BY_ID:
            self.ignored_books.add(book)
            return
        clean = _SPACES.sub(" ", text).strip()
        if not clean:
            raise OsisError(f"Empty verse: {osis_id}")
        self.verses.append(ParsedVerse(book, chapter, verse, clean))

    def _close(self):
        """Finish the open milestone verse, if any."""
        if self._open_id is not None:
            self._store(self._open_id, "".join(self._parts))
        self._open_id = None
        self._parts = []

    @staticmethod
    def _plain_text(elem):
        """All text inside an element, without translator notes."""
        if _local(elem.tag) == "note":
            return ""
        pieces = [elem.text or ""]
        for child in elem:
            pieces.append(_Collector._plain_text(child))
            pieces.append(child.tail or "")
        return "".join(pieces)

    def walk(self, elem):
        """Visit one element, its children and its tail text."""
        name = _local(elem.tag)
        if name == "verse":
            osis_id, start, end = elem.get("osisID"), elem.get("sID"), elem.get("eID")
            if end:
                self._close()
            elif start:
                self._close()
                self._open_id = osis_id
            elif osis_id:
                self._close()
                self._store(osis_id, self._plain_text(elem))
            self._add_text(elem.tail)
            return
        if name == "note":
            self._add_text(elem.tail)
            return
        self._add_text(elem.text)
        for child in elem:
            self.walk(child)
        self._add_text(elem.tail)


def parse_osis(source):
    """Parse an OSIS file (path or file object) into a ParseResult."""
    try:
        root = ET.parse(source).getroot()
    except ET.ParseError as error:
        raise OsisError(f"Not valid XML: {error}") from error
    collector = _Collector()
    collector.walk(root)
    collector._close()
    return ParseResult(verses=collector.verses, ignored_books=collector.ignored_books)


def check_canon(verses):
    """Return a list of problems that make a text incomplete (empty list = OK).

    Checks every one of the 66 books is present, has the right number of
    chapters, has no gaps in its chapter numbers and has no duplicate verse.
    """
    problems = []
    seen = set()
    chapters = defaultdict(set)
    for item in verses:
        key = (item.book, item.chapter, item.verse)
        if key in seen:
            problems.append(f"Duplicate verse {item.book} {item.chapter}:{item.verse}")
        seen.add(key)
        chapters[item.book].add(item.chapter)
    for book in BOOKS:
        found = chapters.get(book.osis_id)
        if not found:
            problems.append(f"Missing book {book.osis_id}")
        elif found != set(range(1, book.chapters + 1)):
            problems.append(
                f"{book.osis_id}: expected chapters 1-{book.chapters}, found {len(found)}"
            )
    return problems