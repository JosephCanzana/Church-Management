"""core.text: small text helpers shared by every app.

Today: `clean_text` (what we store), `title_case` (what we show for places and
other text) and `name_case` / `person_name` (what we show for people).
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


# Suffixes written in capitals when they end a name part ("Santos III").
_NAME_SUFFIXES = {"ii", "iii", "iv"}
_LETTER_RUN = re.compile(r"[^\W\d_]+")


def name_case(value):
    """What gets shown for ONE part of a person's name: "o'brien" -> "O'Brien".

    `title_case` is made for places and is wrong for people: it keeps "ng" and
    "sa" small ("Maria ng") and writes "xi" and "vi" as roman numerals
    ("XI Chen"). Here every run of letters starts with a capital, so a hyphen
    or an apostrophe starts a new one ("dela-cruz" -> "Dela-Cruz"). "ii", "iii"
    and "iv" stay in capitals only as the last word of a part with several
    words ("santos iii" -> "Santos III").
    """
    if value is None:
        return ""
    words = " ".join(str(value).split()).lower().split(" ")
    suffix = ""
    if len(words) > 1 and words[-1] in _NAME_SUFFIXES:
        suffix = " " + words.pop().upper()
    shown = _LETTER_RUN.sub(lambda m: m.group(0)[:1].upper() + m.group(0)[1:], " ".join(words))
    return shown + suffix


def person_name(first_name, middle_name, last_name):
    """A person's full name for display: first, middle and last, each through `name_case`."""
    parts = (name_case(first_name), name_case(middle_name), name_case(last_name))
    return " ".join(p for p in parts if p)