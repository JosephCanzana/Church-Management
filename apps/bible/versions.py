"""The Bible versions people can pick, in the order the picker shows them.

``source`` says where the text comes from: ``local`` versions are public domain
and read from ``bible_verse``; ``esv`` is fetched from api.esv.org (and can be
locked, see ``apps/bible/services.py``). Adding MBBTAG later means adding one
entry here plus a client; nothing else in the reader changes.
"""
from dataclasses import dataclass

SOURCE_LOCAL = "local"
SOURCE_ESV = "esv"


@dataclass(frozen=True)
class BibleVersion:
    """One selectable Bible version."""

    code: str      # stored in the cookie and in bible_chapter_read.translation
    label: str     # shown to people
    language: str  # HTML lang attribute of the text
    source: str    # SOURCE_LOCAL or SOURCE_ESV
    notice: str    # copyright line shown under the text


VERSIONS = (
    # The ESV copyright notice must be added (and checked against api.esv.org)
    # when the ESV client is built; ESV text is never shown before that.
    BibleVersion("ESV", "ESV", "en", SOURCE_ESV, ""),
    BibleVersion("KJV", "KJV", "en", SOURCE_LOCAL, "King James Version (KJV). Public domain."),
    BibleVersion("ADB", "Ang Dating Biblia", "tl", SOURCE_LOCAL, "Ang Dating Biblia (1905). Public domain."),
)
VERSIONS_BY_CODE = {version.code: version for version in VERSIONS}

# Always available (stored locally), so it is the fallback for a missing or
# tampered cookie.
DEFAULT_VERSION = "KJV"
