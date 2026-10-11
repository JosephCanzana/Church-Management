"""Talk to api.esv.org: ask for one chapter, get its verses back.

Only HTTP and text parsing live here (standard library only, no new package).
Limits, caching and locking are in ``apps/bible/esv.py``.

Crossway's terms (https://api.esv.org/): non-commercial use, at most 5,000
queries a day, 1,000 an hour and 60 a minute, never more than 500 verses stored,
and the ESV copyright notice plus a link to www.esv.org on every page that shows
the text.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .books import BOOKS_BY_ID

ENDPOINT = "https://api.esv.org/v3/passage/text/"
TIMEOUT_SECONDS = 8

# Ask for plain verses only: no headings, notes, references or "(ESV)" tag (the
# page shows the ESV label and the copyright notice itself).
REQUEST_OPTIONS = {
    "include-passage-references": "false",
    "include-verse-numbers": "true",
    "include-first-verse-numbers": "true",
    "include-footnotes": "false",
    "include-footnote-body": "false",
    "include-headings": "false",
    "include-short-copyright": "false",
    "include-copyright": "false",
    "include-passage-horizontal-lines": "false",
    "include-heading-horizontal-lines": "false",
    "include-selahs": "true",
    "line-length": "0",
}

_MARKER = re.compile(r"\[(\d+)\]")
_TRAILING_TAG = re.compile(r"\s*\(ESV\)\s*$")


class EsvError(Exception):
    """The ESV service could not give us the chapter (network, bad reply...)."""


class EsvThrottled(EsvError):
    """ESV asked us to slow down (HTTP 429). ``retry_after`` is seconds or None."""

    def __init__(self, retry_after=None):
        super().__init__("ESV throttled the request")
        self.retry_after = retry_after


class EsvAuthError(EsvError):
    """ESV refused our key (HTTP 401 or 403)."""


def build_query(osis_id, chapter):
    """Passage query for one chapter.

    A single-chapter book (Obadiah, Jude...) is asked for by name only, because
    "Jude 1" would be read as verse 1, not chapter 1.
    """
    book = BOOKS_BY_ID[osis_id]
    if book.chapters == 1:
        return book.name
    return f"{book.name} {chapter}"


def parse_passage_text(text):
    """Turn ESV plain text ("[1] In the beginning... [2] ...") into [(number, text)].

    Anything before the first verse number (a Psalm title, for example) is
    dropped. Raises EsvError when the text does not look like numbered verses.
    """
    parts = _MARKER.split(text or "")
    verses = []
    previous = 0
    for number, chunk in zip(parts[1::2], parts[2::2]):
        value = int(number)
        clean = " ".join(_TRAILING_TAG.sub("", chunk).split())
        if not clean:
            raise EsvError(f"Empty verse {value} in ESV reply")
        if value <= previous:
            raise EsvError(f"Verse numbers out of order near {value}")
        verses.append((value, clean))
        previous = value
    if not verses:
        raise EsvError("No verses found in ESV reply")
    return verses


def _retry_after(headers):
    """Seconds from a Retry-After header, or None when absent or not a number."""
    try:
        return max(1, int(headers.get("Retry-After", "")))
    except (TypeError, ValueError):
        return None


def fetch_chapter(api_key, osis_id, chapter):
    """Fetch one chapter from the ESV API and return [(verse number, text)]."""
    params = dict(REQUEST_OPTIONS, q=build_query(osis_id, chapter))
    request = urllib.request.Request(
        f"{ENDPOINT}?{urllib.parse.urlencode(params)}",
        headers={"Authorization": f"Token {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise EsvThrottled(_retry_after(error.headers)) from error
        if error.code in (401, 403):
            raise EsvAuthError(f"ESV rejected the key (HTTP {error.code})") from error
        raise EsvError(f"ESV answered HTTP {error.code}") from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise EsvError(f"Could not reach ESV: {error}") from error
    passages = payload.get("passages") if isinstance(payload, dict) else None
    if not passages or not isinstance(passages[0], str):
        raise EsvError("ESV returned no passage")
    return parse_passage_text(passages[0])
