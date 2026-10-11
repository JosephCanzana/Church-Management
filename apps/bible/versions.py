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
    notice_url: str = ""  # link shown next to the notice (ESV requires esv.org)


VERSIONS = (
    # Notice wording is one of the three Crossway lists on https://api.esv.org/
    # (the "copyright page" notice). Pages that show ESV text must also link to esv.org.
    BibleVersion(
        "ESV", "ESV", "en", SOURCE_ESV,
        "Scripture quotations are from the ESV\u00ae Bible (The Holy Bible, English Standard Version\u00ae), "
        "\u00a9 2001 by Crossway, a publishing ministry of Good News Publishers. Used by permission. "
        "All rights reserved. The ESV text may not be quoted in any publication made available to the "
        "public by a Creative Commons license. The ESV may not be translated into any other language. "
        "Users may not copy or download more than 500 verses of the ESV Bible or more than one half of "
        "any book of the ESV Bible.",
        "https://www.esv.org/",
    ),
    BibleVersion("KJV", "KJV", "en", SOURCE_LOCAL, "King James Version (KJV). Public domain."),
    BibleVersion("ADB", "Ang Dating Biblia", "tl", SOURCE_LOCAL, "Ang Dating Biblia (1905). Public domain."),
)
VERSIONS_BY_CODE = {version.code: version for version in VERSIONS}

# Always available (stored locally), so it is the fallback for a missing or
# tampered cookie.
DEFAULT_VERSION = "KJV"
