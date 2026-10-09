"""accounts.validators: the one password-strength rule used by every form."""
import re
import secrets
import string

from django.core.exceptions import ValidationError

SPECIALS = "@$!%*?&_"
STRONG_PASSWORD_RE = re.compile(r"(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&_])[A-Za-z\d@$!%*?&_]{8,}")
STRONG_PASSWORD_MESSAGE = (
    "Use at least 8 characters with a lowercase letter, an uppercase letter, "
    f"a number and a special character ({SPECIALS}). No spaces or other symbols."
)


def validate_strong_password(value):
    """Raise ValidationError unless `value` meets the strong-password rule."""
    # fullmatch, not match + `$`: `$` would let a trailing newline through.
    if not STRONG_PASSWORD_RE.fullmatch(value or ""):
        raise ValidationError(STRONG_PASSWORD_MESSAGE)


def generate_strong_password(length=12):
    """Random password that always passes validate_strong_password."""
    pools = [
        "abcdefghijkmnpqrstuvwxyz",
        "ABCDEFGHJKLMNPQRSTUVWXYZ",
        "23456789",
        SPECIALS,
    ]
    chars = [secrets.choice(p) for p in pools]
    allowed = "".join(pools)
    chars += [secrets.choice(allowed) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)