"""accounts.contact: phone number cleaning for the optional contact details of a person.

Used by the person form (shape check) and by `services.users` (the same check
again on the server). A phone number is optional; when given it is stored as
digits only: a Philippine number as `09XXXXXXXXX` (so +63 9XX XXX XXXX and
0917-123-4567 are the same value), anything else with an optional leading `+`.
"""
import re

_PH_LOCAL = re.compile(r"^0\d{9,10}$")           # 09171234567, or a landline with area code
_INTERNATIONAL = re.compile(r"^\+\d{7,15}$")

PHONE_MESSAGE = "Enter a phone number such as 0917 123 4567 or +63 917 123 4567."


def clean_phone(value):
    """What gets stored: no spaces, dashes or brackets; +639... and 639... become 09...; '' when empty."""
    raw = re.sub(r"[\s\-().]", "", (value or "").strip())
    if not raw:
        return ""
    if raw.startswith("+63") and raw[1:].isdigit() and len(raw) in (12, 13):
        return "0" + raw[3:]
    if raw.startswith("63") and raw.isdigit() and len(raw) in (11, 12):
        return "0" + raw[2:]
    return raw


def is_valid_phone(value):
    """True for an empty value or a cleaned number in one of the accepted shapes."""
    cleaned = clean_phone(value)
    return not cleaned or bool(_PH_LOCAL.match(cleaned) or _INTERNATIONAL.match(cleaned))
