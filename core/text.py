"""core.text: small text helpers shared by every app.

Today: `clean_text` (what we store) and `title_case` (what we show).
Names and addresses are STORED in lowercase with single spaces, and SHOWN in
title case, so "SANTA  rosa" and "santa rosa" are never two different values.
"""
import re

# One word: letters only (any alphabet, so "ñ" works), with inner apostrophes
# kept together ("bataan's" is one word).
_WORD = re.compile(r"[^\W\d_]+(?:['\u2019][^\W\d_]+)*")
_LETTER = re.compile(r"[^\W\d_]")

# Little words kept in lowercase when they are not the first word.
_SMALL_WORDS = {"of", "the", "and", "ng", "sa"}

# Words shown in capitals: acronyms and the roman numerals used in barangay
# names ("Poblacion II"). Edit this set when a new acronym turns up.
_UPPER_WORDS = {
    "jil", "ncr",
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "xi", "xii", "xiii", "xiv", "xv",
}


def clean_text(value):
    """What gets stored: trimmed, single spaces, lowercase. None becomes ''."""
    if value is None:
        return ""
    return " ".join(str(value).split()).lower()


def title_case(value):
    """What gets shown: 'sta. rosa' -> 'Sta. Rosa', 'city of manila' -> 'City of Manila'.

    Django's built-in `|title` turns "bataan's" into "Bataan'S" and cannot keep
    "of" small or "ii" in capitals, so this is used instead.
    """
    if value is None:
        return ""
    text = " ".join(str(value).split())

    def fix(match):
        word = match.group(0)
        low = word.lower()
        start = match.start()
        first_word = not _LETTER.search(text[:start])
        if low in _UPPER_WORDS:
            return low.upper()
        if start > 0 and text[start - 1].isdigit():
            # "12a" -> "12A" but "3rd" -> "3rd"
            return low.upper() if len(low) == 1 else low
        if low in _SMALL_WORDS and not first_word:
            return low
        return low[:1].upper() + low[1:]

    return _WORD.sub(fix, text)
