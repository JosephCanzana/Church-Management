"""Row rules for bulk add (Site settings > Bulk add). Pure: no Django, no database.

The service (`accounts/services/bulk_import.py`) injects everything that needs the
database: the extension lookup, the duplicate check and the existing address and
contact rules. That keeps these rules testable without a database and means the
SAME function powers the preview and the final import.

A row ends in exactly one status:
    ok         will be added
    warning    will be added, but something was auto-fixed or looks odd
    duplicate  skipped: the extension / person already exists (or repeats in the file)
    error      skipped: something must be fixed in the file
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Mapping

from .parser import RawRow, Spec

TEXT_MAX = 100  # generous cap; the models may be stricter and the service has the last word
NAME_MAX = 100
FORMULA_START = ("=", "+", "-", "@")  # a spreadsheet would run these as formulas
PLACEHOLDERS = frozenset({"n/a", "none", "null", "nil", "unknown", "test", "tbd", "xxx"})
MAX_AGE_YEARS = 110

ADDRESS_FIELDS = ("country", "province", "municipality", "barangay", "postal_code", "street", "building_number")

EXTENSION_SPEC = Spec(
    required=("name", "country", "province", "municipality", "barangay", "postal_code"),
    optional=("street", "building_number"),
    aliases={
        "extension": "name", "extension_name": "name", "branch": "name", "branch_name": "name",
        "city": "municipality", "town": "municipality", "zip": "postal_code", "zip_code": "postal_code",
        "postcode": "postal_code", "house_number": "building_number", "building": "building_number",
        "street_address": "street",
    },
)

PEOPLE_SPEC = Spec(
    required=("first_name", "last_name", "gender", "extension"),
    optional=("middle_name", "suffix", "birth_date", "person_type", "role", "phone_number") + ADDRESS_FIELDS,
    aliases={
        "firstname": "first_name", "given_name": "first_name", "first": "first_name",
        "middlename": "middle_name", "middle": "middle_name",
        "lastname": "last_name", "surname": "last_name", "family_name": "last_name",
        "sex": "gender", "birthday": "birth_date", "birthdate": "birth_date", "date_of_birth": "birth_date",
        "dob": "birth_date", "phone": "phone_number", "mobile": "phone_number", "mobile_number": "phone_number",
        "contact_number": "phone_number", "type": "person_type", "member_type": "person_type",
        "extension_name": "extension", "branch": "extension", "church": "extension",
        "city": "municipality", "town": "municipality", "zip": "postal_code", "zip_code": "postal_code",
        "postcode": "postal_code", "house_number": "building_number", "building": "building_number",
        "street_address": "street",
    },
)

_GENDERS = {"m": "male", "male": "male", "man": "male", "boy": "male",
            "f": "female", "female": "female", "woman": "female", "girl": "female"}
_PERSON_TYPES = {
    "": "member_account", "member": "member_account", "member_account": "member_account", "account": "member_account",
    "member_record": "member_record", "record": "member_record", "member_no_account": "member_record",
    "no_account": "member_record",
    "guest": "guest",
}


@dataclass
class Issue:
    """One thing noticed about a row. `code` is 'duplicate' for skip-as-duplicate."""

    level: str  # "error" | "warning"
    field: str
    message: str
    code: str = ""


@dataclass
class RowResult:
    """The verdict for one data row, plus the cleaned values the service will import."""

    line: int
    values: dict[str, str]
    label: str = ""
    data: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    def error(self, name: str, message: str) -> None:
        self.issues.append(Issue("error", name, message))

    def warn(self, name: str, message: str) -> None:
        self.issues.append(Issue("warning", name, message))

    def duplicate(self, message: str) -> None:
        self.issues.append(Issue("error", "", message, code="duplicate"))

    @property
    def has_errors(self) -> bool:
        return any(i.level == "error" for i in self.issues)

    @property
    def status(self) -> str:
        if any(i.code == "duplicate" for i in self.issues):
            return "duplicate"
        if self.has_errors:
            return "error"
        return "warning" if self.issues else "ok"

    @property
    def importable(self) -> bool:
        return self.status in ("ok", "warning")

    @property
    def reasons(self) -> list[str]:
        """Plain sentences for the table; field-less issues stand alone."""
        return [f"{i.field.replace('_', ' ').capitalize()}: {i.message}" if i.field else i.message for i in self.issues]


def norm(value: str) -> str:
    """The comparison form of a name: case and spacing never make two things different."""
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def summarize(results: list[RowResult]) -> dict[str, int]:
    """Counts for the preview header."""
    out = {"total": len(results), "ok": 0, "warning": 0, "duplicate": 0, "error": 0}
    for r in results:
        out[r.status] += 1
    out["importable"] = out["ok"] + out["warning"]
    return out


# --- shared field checks -------------------------------------------------------------------------------------------

def _text(res: RowResult, name: str, label: str, *, required: bool = False, max_len: int = TEXT_MAX) -> str:
    """Check one free-text cell (length, spreadsheet formulas) and return it."""
    value = res.values.get(name, "")
    if not value:
        if required:
            res.error(name, f"{label} is required.")
        return ""
    if value[0] in FORMULA_START:
        res.error(name, f"starts with “{value[0]}”, which spreadsheets run as a formula. Remove it.")
    if len(value) > max_len:
        res.error(name, f"is {len(value)} characters; the limit is {max_len}.")
    return value


def _person_name(res: RowResult, name: str, label: str, *, required: bool) -> str:
    """Check a first, middle or last name: letters, spaces, hyphen, apostrophe, full stop."""
    value = _text(res, name, label, required=required, max_len=NAME_MAX)
    if not value:
        return ""
    if value.casefold() in PLACEHOLDERS:
        if required:
            res.error(name, f"“{value}” looks like a placeholder, not a real name.")
            return ""
        res.warn(name, f"“{value}” was treated as blank.")
        return ""
    ok_chars = all(unicodedata.category(c)[0] in "LM" or c in " -'’." for c in value)
    if not ok_chars or not any(unicodedata.category(c)[0] == "L" for c in value):
        res.error(name, "contains digits or symbols. Names may use letters, spaces, - ' and .")
    return value


def parse_date(text: str, today: date) -> tuple[date | None, str, str]:
    """Return (date, warning, error). Accepts ISO and month/day/year; refuses Excel serials and 2-digit years."""
    if not text:
        return None, "", ""
    if re.fullmatch(r"\d{4,6}", text):
        return None, "", "looks like an Excel serial number. Write the date as YYYY-MM-DD."
    if re.fullmatch(r"\d{8}", text):
        return None, "", "has no separators. Write the date as YYYY-MM-DD."
    warning = ""
    parsed: date | None = None
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", text)
        if m:
            a, b, y = (int(g) for g in m.groups())
            if a > 12 and b <= 12:
                d, mo = a, b  # only day/month/year fits
            else:
                mo, d = a, b
                if a <= 12 and b <= 12 and a != b:
                    warning = f"was read as month/day/year ({a}/{b}). Use YYYY-MM-DD to be sure."
        elif re.fullmatch(r"\d{1,2}[-/]\d{1,2}[-/]\d{2}", text):
            return None, "", "has a two-digit year. Use a four-digit year."
        else:
            for fmt in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
                try:
                    parsed = datetime.strptime(text, fmt).date()
                    break
                except ValueError:
                    continue
            if parsed is None:
                return None, "", "is not a date I can read. Write it as YYYY-MM-DD."
            y, mo, d = parsed.year, parsed.month, parsed.day
    try:
        parsed = date(y, mo, d)
    except ValueError:
        return None, "", "is not a real calendar date."
    if parsed > today:
        return None, "", "is in the future."
    if parsed.year < today.year - MAX_AGE_YEARS:
        return None, "", f"would make the person over {MAX_AGE_YEARS} years old. Check the year."
    return parsed, warning, ""


def normalize_phone(res: RowResult, value: str) -> str:
    """Return digits only. Fix the leading zero Excel drops; refuse scientific notation."""
    if not value:
        return ""
    if re.search(r"\d[eE][+-]?\d", value) and "." in value:
        res.error("phone_number", "was changed by Excel into scientific notation. Format the column as Text and retype it.")
        return ""
    digits = re.sub(r"[\s().-]", "", value)
    if digits.startswith("+63"):
        digits = "0" + digits[3:]
    elif digits.startswith("63") and len(digits) == 12:
        digits = "0" + digits[2:]
    if not digits.isdigit():
        res.error("phone_number", "may only contain digits, spaces, + - ( ) and a full stop.")
        return ""
    if len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
        res.warn("phone_number", "was missing its leading 0 (Excel drops it). Added.")
    elif not re.fullmatch(r"09\d{9}", digits):
        res.warn("phone_number", "is not an 11-digit mobile number starting with 09. Kept as typed.")
    return digits


def _address(res: RowResult) -> dict[str, str]:
    """Text checks for the address columns; a partly filled address needs a country."""
    out = {f: _text(res, f, f.replace("_", " ").capitalize()) for f in ADDRESS_FIELDS if f in res.values}
    if any(out.values()) and not out.get("country"):
        res.error("country", "is required when any address part is filled in.")
    return out


# --- extensions ----------------------------------------------------------------------------------------------------

def validate_extensions(
    rows: list[RawRow],
    existing: Mapping[str, bool],
    external_check: Callable[[dict], list[str]] | None = None,
) -> list[RowResult]:
    """Validate extension rows.

    `existing` maps norm(name) -> is_archived for every extension in the database.
    `external_check(data)` returns error messages from the real address rules.
    """
    results: list[RowResult] = []
    seen: dict[str, int] = {}
    for raw in rows:
        res = RowResult(line=raw.line, values=raw.cells, label=raw.cells.get("name", ""))
        if raw.shape_error:
            res.error("", raw.shape_error)
        name = _text(res, "name", "Name", required=True)
        for f in ("country", "province", "municipality", "barangay", "postal_code"):
            _text(res, f, f.replace("_", " ").capitalize(), required=True)
        for f in ("street", "building_number"):
            _text(res, f, f.replace("_", " ").capitalize())
        postal = res.values.get("postal_code", "")
        if postal.isdigit() and len(postal) == 3 and norm(res.values.get("country", "")) in ("philippines", "ph"):
            res.error("postal_code", "has 3 digits. Excel may have dropped a leading zero; format the column as Text.")
        data = {f: res.values.get(f, "") for f in EXTENSION_SPEC.known}
        if not res.has_errors and external_check:
            for msg in external_check(data):
                res.error("", msg)
        if not res.has_errors:
            key = norm(name)
            if key in existing:
                where = " It is archived: restore it instead of adding it again." if existing[key] else ""
                res.duplicate(f"An extension named “{name}” already exists.{where}")
            elif key in seen:
                res.duplicate(f"Same extension name as line {seen[key]} of this file.")
            else:
                seen[key] = raw.line
        res.data = data
        results.append(res)
    return results


# --- people --------------------------------------------------------------------------------------------------------

def validate_people(
    rows: list[RawRow],
    extensions: Mapping[str, list[tuple[int, bool]]],
    today: date,
    external_check: Callable[[dict], list[str]] | None = None,
    db_duplicates: Callable[[dict], list[str]] | None = None,
) -> list[RowResult]:
    """Validate people rows.

    `extensions` maps norm(name) -> [(extension id, is_archived)].
    `external_check(data)` returns error messages from the real contact and address rules.
    `db_duplicates(data)` returns one message per existing person who matches.
    """
    results: list[RowResult] = []
    seen: dict[tuple, list[tuple[int, date | None]]] = {}
    for raw in rows:
        res = RowResult(line=raw.line, values=raw.cells)
        if raw.shape_error:
            res.error("", raw.shape_error)
        first = _person_name(res, "first_name", "First name", required=True)
        middle = _person_name(res, "middle_name", "Middle name", required=False)
        last = _person_name(res, "last_name", "Last name", required=True)
        res.label = " ".join(p for p in (first, last) if p)

        suffix = _text(res, "suffix", "Suffix", max_len=10).rstrip(".")
        gender = _GENDERS.get(norm(res.values.get("gender", "")))
        if not gender:
            res.error("gender", "must be male or female.")

        birth, warning, problem = parse_date(res.values.get("birth_date", ""), today)
        if problem:
            res.error("birth_date", problem)
        elif warning:
            res.warn("birth_date", warning)
        elif not res.values.get("birth_date"):
            res.warn("birth_date", "is blank. The age group stays unknown until it is set.")

        type_key = re.sub(r"[^a-z0-9]+", "_", norm(res.values.get("person_type", ""))).strip("_")
        person_type = _PERSON_TYPES.get(type_key)
        if not person_type:
            res.error("person_type", "must be member, member_record (no account) or guest.")
        role = norm(res.values.get("role", ""))
        if role and role != "member":
            res.error("role", "Bulk add creates members only. Add leaders from the People screen.")

        phone = normalize_phone(res, res.values.get("phone_number", ""))
        address = _address(res)
        extension_id = _resolve_extension(res, extensions)

        data = {
            "first_name": first, "middle_name": middle, "last_name": last, "suffix": suffix,
            "gender": gender or "", "birth_date": birth, "person_type": person_type or "",
            "phone_number": phone, "extension_id": extension_id, **address,
        }
        res.data = data
        if not res.has_errors and external_check:
            for msg in external_check(data):
                res.error("", msg)
        if not res.has_errors:
            _check_people_duplicates(res, seen, (norm(first), norm(middle), norm(last), norm(suffix)), birth, db_duplicates)
        results.append(res)
    return results


def _resolve_extension(res: RowResult, extensions: Mapping[str, list[tuple[int, bool]]]) -> int | None:
    """Find the extension by name. Explains typos, archived and ambiguous matches."""
    wanted = res.values.get("extension", "")
    if not wanted:
        res.error("extension", "is required.")
        return None
    matches = extensions.get(norm(wanted), [])
    active = [pk for pk, archived in matches if not archived]
    if len(active) == 1:
        return active[0]
    if len(active) > 1:
        res.error("extension", f"“{wanted}” matches more than one extension. Rename one of them first.")
    elif matches:
        res.error("extension", f"“{wanted}” is archived. Restore it before adding people to it.")
    else:
        close = difflib.get_close_matches(norm(wanted), [k for k, v in extensions.items() if any(not a for _, a in v)], n=1, cutoff=0.75)
        hint = f" Did you mean “{close[0]}”?" if close else " Add the extension first."
        res.error("extension", f"there is no extension named “{wanted}”.{hint}")
    return None


def _check_people_duplicates(res, seen, key, birth, db_duplicates) -> None:
    """Flag a repeat inside the file, then a match in the database; remember clean rows."""
    for line, other_birth in seen.get(key, []):
        # Same rule as the People screen: different birth dates mean different people.
        if birth is None or other_birth is None or birth == other_birth:
            res.duplicate(f"Same person as line {line} of this file (same name{' and birth date' if birth else ''}).")
            return
    if db_duplicates:
        messages = db_duplicates(res.data)
        if messages:
            for msg in messages:
                res.duplicate(msg)
            return
    seen.setdefault(key, []).append((res.line, birth))
