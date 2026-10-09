"""accounts.services.password_reset: forgot password and reset by emailed link.

Flow (docs: AGENTS.md "Behaviour rules", docs/flows.md):
  1. `request_password_reset()` -- someone types an account id or a verified
     email. A link is emailed ONLY when the account is `active` and has a
     VERIFIED email. In every other case nothing happens, and the caller shows
     the same message, so the page never reveals whether an account exists.
  2. `get_valid_reset_token()` -- read-only check used by the reset page (a GET
     never uses up a token, because mail scanners open links automatically).
  3. `reset_password()` -- sets the new password under row locks. The token is
     single use; a second click or a parallel request fails.

Rules that matter:
  * Tokens are stored as SHA-256 hashes (same helper as email verification).
  * Not-activated, suspended and archived people cannot reset, and neither can
    people without a verified email; they use the admin / coordinator reset.
  * A link stops working if the person is no longer active or their verified
    email changed after it was sent.
  * Mail is sent AFTER the transaction commits, and a mail failure is logged,
    never shown, so it cannot change what the visitor sees.
  * Audit keys never contain "password" or "token" (log_action drops them).
    The typed identifier, the token and the password are never logged.
"""
import logging
import math
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.audit.services import log_action
from apps.core.services import ServiceError

from ..models import EmailToken, Status, User
# One hashing implementation for every emailed token.
from .profile import _hash_token, _lock_user

logger = logging.getLogger(__name__)

RESET = EmailToken.Purpose.RESET_PASSWORD

LINK_INVALID = "This link is invalid or has expired. Request a new one."

# Same shape as the `app_user_account_id_format` database constraint. Copied
# (not imported from forms) because services never import forms.
ACCOUNT_ID_RE = re.compile(r"^[1-9][0-9]{7,}$")


# ----------------------------------------------------------------- settings
def link_minutes():
    """How long a reset link works (settings.RESET_LINK_MINUTES, default 30)."""
    return getattr(settings, "RESET_LINK_MINUTES", 30)


def _resend_seconds():
    return getattr(settings, "RESET_RESEND_SECONDS", 60)


def _max_per_hour():
    return getattr(settings, "RESET_MAX_PER_HOUR", 5)


# ------------------------------------------------------------------ helpers
def _entity(person):
    """The audit entity fields for a person."""
    return dict(entity_type="user", entity_id=person.pk, entity_label=str(person))


def _find_person(identifier):
    """The person an account id or verified email points to, or None.

    `User.email` holds verified addresses only, so a pending address never matches.
    """
    value = (identifier or "").strip()
    if "@" in value:
        return User.objects.filter(email__iexact=value).first()
    if ACCOUNT_ID_RE.match(value):
        return User.objects.filter(account_id=value).first()
    return None


def _can_reset(person):
    """Only active people with a verified email may reset by email."""
    return person.status == Status.ACTIVE and bool(person.email) and bool(person.email_verified_at)


def _token_usable(token, person):
    """The person is still resettable and the link went to their CURRENT verified email."""
    return _can_reset(person) and person.email.lower() == token.email.lower()


def _void_tokens(user_id, now):
    """Make every unused reset link of this person stop working."""
    EmailToken.objects.filter(
        user_id=user_id, purpose=RESET, used_at__isnull=True, expires_at__gt=now,
    ).update(expires_at=now)


def _limited(person, now):
    """True when links are requested too fast or too often (checked silently)."""
    last = (
        EmailToken.objects.filter(user=person, purpose=RESET)
        .order_by("-created_at").values_list("created_at", flat=True).first()
    )
    if last is not None and (now - last).total_seconds() < _resend_seconds():
        return True
    recent = EmailToken.objects.filter(
        user=person, purpose=RESET, created_at__gte=now - timedelta(hours=1),
    ).count()
    return recent >= _max_per_hour()


def resend_wait_seconds(person):
    """Seconds until another link would be sent (0 = now). For tests and tooling."""
    last = (
        EmailToken.objects.filter(user=person, purpose=RESET)
        .order_by("-created_at").values_list("created_at", flat=True).first()
    )
    if last is None:
        return 0
    return max(0, math.ceil(_resend_seconds() - (timezone.now() - last).total_seconds()))


def _mail_after_commit(to_address, subject, template, context):
    """Send an email once the surrounding transaction is committed.

    `template` is a name without extension: <template>.txt and <template>.html
    under templates/accounts/email/. Any failure is logged and swallowed.
    """
    def _send():
        try:
            message = EmailMultiAlternatives(
                subject=subject,
                body=render_to_string(f"accounts/email/{template}.txt", context),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[to_address],
            )
            message.attach_alternative(render_to_string(f"accounts/email/{template}.html", context), "text/html")
            message.send()
        except Exception:
            logger.exception("Could not send the %s email", template)

    transaction.on_commit(_send)


# -------------------------------------------------------------- 1. request
def request_password_reset(request, identifier):
    """Email a reset link if (and only if) the identifier matches a resettable account.

    Returns True when a link was issued. CALLERS MUST NOT USE THE RESULT to
    change what the visitor sees (that would allow account enumeration); it
    exists for tests. Unknown id, no verified email, not active, and being
    rate limited all return False without any error.
    """
    person = _find_person(identifier)
    if person is None:
        return False

    now = timezone.now()
    with transaction.atomic():
        person = _lock_user(person.pk)
        if not _can_reset(person) or _limited(person, now):
            return False

        _void_tokens(person.pk, now)
        raw = secrets.token_urlsafe(32)
        EmailToken.objects.create(
            user=person, purpose=RESET, email=person.email, token_hash=_hash_token(raw),
            expires_at=now + timedelta(minutes=link_minutes()),
        )
        log_action(
            "account.password_reset_requested", actor=None, request=request,
            extension_id=person.extension_id, after={"link_sent": True}, **_entity(person),
        )
        _mail_after_commit(
            person.email, "Reset your password", "password_reset",
            {
                "name": person.full_name,
                "link": settings.SITE_URL.rstrip("/") + reverse("accounts:reset_password", args=[raw]),
                "minutes": link_minutes(),
                "site_name": "Church Management",
            },
        )
    return True


# ------------------------------------------------------------ 2. check link
def get_valid_reset_token(raw_token):
    """The unused, unexpired token for a still-resettable person, or None.

    Read-only: the reset page calls it on GET and POST. `token.user` is loaded.
    """
    if not raw_token:
        return None
    token = (
        EmailToken.objects.select_related("user")
        .filter(
            token_hash=_hash_token(raw_token), purpose=RESET,
            used_at__isnull=True, expires_at__gt=timezone.now(),
        )
        .first()
    )
    if token is not None and _token_usable(token, token.user):
        return token
    return None


# ----------------------------------------------------------------- 3. reset
def reset_password(request, raw_token, new_password):
    """Set `new_password` using the emailed token. Raises ServiceError(LINK_INVALID) if it cannot.

    The token row is locked first, then the person's row, and everything is
    re-checked under the locks, so two simultaneous submits cannot both win.
    The new password's SHAPE is checked by `ResetPasswordForm`.
    On success: the password changes (which ends every open session of this
    person), `must_change_password` is cleared because they just chose the
    password themselves, all their reset links stop working, and a
    "your password was changed" email is sent after commit.
    """
    if not raw_token:
        raise ServiceError(LINK_INVALID)
    with transaction.atomic():
        found = (
            EmailToken.objects.filter(token_hash=_hash_token(raw_token), purpose=RESET)
            .values_list("pk", "user_id").first()
        )
        if found is None:
            raise ServiceError(LINK_INVALID)
        token = EmailToken.objects.select_for_update().get(pk=found[0])
        person = _lock_user(found[1])
        now = timezone.now()

        if token.used_at is not None or token.expires_at <= now or not _token_usable(token, person):
            raise ServiceError(LINK_INVALID)

        person.set_password(new_password)
        person.must_change_password = False
        person.save(update_fields=["password", "must_change_password", "updated_at"])
        EmailToken.objects.filter(pk=token.pk).update(used_at=now)
        _void_tokens(person.pk, now)

        log_action(
            "account.password_reset", actor=person, request=request,
            after={"changed": True}, **_entity(person),
        )
        _mail_after_commit(
            token.email, "Your password was changed", "password_changed",
            {
                "name": person.full_name,
                "when": timezone.localtime(now).strftime("%B %d, %Y, %I:%M %p"),
                "site_name": "Church Management",
            },
        )
    return True
