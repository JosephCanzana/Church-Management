"""Pure CSV reader for the bulk add screens (Site settings > Bulk add).

No Django and no database here, so it can be tested on its own. It turns uploaded
bytes or pasted text into rows keyed by canonical column names and reports
FILE-level problems (wrong format, missing columns, too many rows). Whether a
row's VALUES are acceptable is decided in `rules.py`.

Anomalies handled here: Excel .xlsx uploaded instead of CSV, binary data, a UTF-8
BOM, Windows-1252 text, comma / semicolon / tab separators (pasting from Excel
gives tabs), header spelling variants, repeated or missing columns, blank lines,
rows with too few or too many cells, unclosed quotes, invisible characters.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field

MAX_BYTES = 1_000_000  # one megabyte is far more than 200 rows need
MAX_ROWS = 200  # same spirit as the 100-row cap of the other bulk actions

# Zero-width and byte-order characters that survive copy and paste from chat apps.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\u2060\ufeff"), None)


class FileProblem(ValueError):
    """The input cannot be read at all. The message is shown to the person."""


@dataclass(frozen=True)
class Spec:
    """Which columns a kind of file accepts and which header spellings map to them."""

    required: tuple[str, ...]
    optional: tuple[str, ...]
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def known(self) -> tuple[str, ...]:
        return self.required + self.optional


@dataclass
class RawRow:
    """One data row: its starting line in the file, its cells and any shape problem."""

    line: int
    cells: dict[str, str]
    shape_error: str = ""


@dataclass
class ParsedFile:
    """The result of reading a file: rows, blocking problems and harmless notes."""

    rows: list[RawRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # block the whole file
    notes: list[str] = field(default_factory=list)  # shown, never block


def decode(raw: bytes) -> tuple[str, list[str]]:
    """Return (text, notes) for uploaded bytes, or raise FileProblem."""
    if not raw.strip():
        raise FileProblem("The file is empty.")
    if len(raw) > MAX_BYTES:
        raise FileProblem("The file is larger than 1 MB. Split it into smaller files.")
    if raw[:2] == b"PK" or b"\x00" in raw:
        raise FileProblem(
            "This is not a plain CSV file (it looks like an Excel .xlsx or another binary file). "
            "In Excel use Save As > CSV UTF-8, then upload that."
        )
    notes: list[str] = []
    try:
        return raw.decode("utf-8-sig"), notes
    except UnicodeDecodeError:
        notes.append(
            "The file is not UTF-8, so it was read as Windows-1252. Check that accented letters "
            "(for example ñ) look right; save as CSV UTF-8 to be safe."
        )
        return raw.decode("cp1252", errors="replace"), notes


def clean_cell(value: str) -> str:
    """Strip invisible characters, controls and extra spaces from one cell."""
    value = unicodedata.normalize("NFC", (value or "").translate(_INVISIBLE))
    value = value.replace("\u00a0", " ")
    value = "".join(" " if ch in "\t\r\n" else ch for ch in value if ch in "\t\r\n" or unicodedata.category(ch) != "Cc")
    return " ".join(value.split())


def _canonical(header: str, spec: Spec) -> str:
    """Map a header cell to a column name: 'First Name', 'firstname' and 'FIRST_NAME' agree."""
    key = re.sub(r"[^a-z0-9]+", "_", clean_cell(header).lower()).strip("_")
    return spec.aliases.get(key, key)


def _sniff_delimiter(first_line: str) -> str:
    """Pick the separator that appears most in the header line (comma wins a tie)."""
    counts = {d: first_line.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=lambda d: (counts[d], d == ","))
    return best if counts[best] else ","


def parse(text: str, spec: Spec) -> ParsedFile:
    """Read CSV text into a ParsedFile. Never raises for bad content; it reports it."""
    out = ParsedFile()
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    first = next((ln for ln in text.split("\n") if ln.strip()), "")
    if not first:
        out.problems.append("The file has no rows.")
        return out
    delimiter = _sniff_delimiter(first)
    if delimiter != ",":
        out.notes.append("Columns were separated by " + ("semicolons." if delimiter == ";" else "tabs."))

    header: list[str] | None = None
    index: dict[str, int] = {}
    previous_line = 0
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
        for cells in reader:
            start, previous_line = previous_line + 1, reader.line_num
            if not any(clean_cell(c) for c in cells):
                continue  # blank line (Excel often leaves trailing ones)
            if header is None:
                header = cells
                index, problems, ignored = _map_header(cells, spec)
                out.problems.extend(problems)
                if ignored:
                    out.notes.append("Ignored columns this screen does not use: " + ", ".join(ignored) + ".")
                if problems:
                    return out
                continue
            if len(out.rows) >= MAX_ROWS:
                out.problems.append(
                    f"More than {MAX_ROWS} rows. Split the file so each part has at most {MAX_ROWS}; nothing was checked."
                )
                out.rows.clear()
                return out
            out.rows.append(_make_row(start, cells, header, index))
    except csv.Error:
        out.problems.append(
            f"Line {previous_line + 1}: a quotation mark is not closed or the text is not valid CSV. "
            "Check that values with commas are wrapped in quotes."
        )
        out.rows.clear()
        return out

    if not out.rows:
        out.problems.append("The file has a header but no rows under it.")
    return out


def _map_header(cells: list[str], spec: Spec) -> tuple[dict[str, int], list[str], list[str]]:
    """Return (column index, blocking problems, ignored column names)."""
    index: dict[str, int] = {}
    problems: list[str] = []
    ignored: list[str] = []
    for pos, raw in enumerate(cells):
        name = _canonical(raw, spec)
        if not name:
            continue  # an empty header cell (trailing comma)
        if name not in spec.known:
            ignored.append(clean_cell(raw))
        elif name in index:
            problems.append(f"The column “{name}” appears twice in the header.")
        else:
            index[name] = pos
    missing = [c for c in spec.required if c not in index]
    if missing:
        found = ", ".join(clean_cell(c) for c in cells if clean_cell(c)) or "none"
        problems.append(f"Missing column(s): {', '.join(missing)}. Columns found: {found}.")
    return index, problems, ignored


def _make_row(line: int, cells: list[str], header: list[str], index: dict[str, int]) -> RawRow:
    """Build a RawRow and flag cell counts that do not match the header."""
    shape = ""
    if len(cells) > len(header) and any(clean_cell(c) for c in cells[len(header):]):
        shape = (
            f"Has {len(cells)} cells but the header has {len(header)}. "
            "A value containing a comma probably needs quotation marks around it."
        )
    elif len(cells) < len(header):
        shape = f"Has {len(cells)} cells but the header has {len(header)}. A cell may be missing or merged."
    values = {col: clean_cell(cells[pos]) if pos < len(cells) else "" for col, pos in index.items()}
    return RawRow(line=line, cells=values, shape_error=shape)


def read_input(upload: bytes | None, pasted: str) -> tuple[str, list[str]]:
    """Choose the uploaded file over pasted text. Raises FileProblem when both are empty."""
    if upload:
        return decode(upload)
    if pasted and pasted.strip():
        if len(pasted.encode("utf-8")) > MAX_BYTES:
            raise FileProblem("The pasted text is larger than 1 MB. Split it into smaller parts.")
        return pasted, []
    raise FileProblem("Choose a CSV file or paste rows (with the header line) first.")
