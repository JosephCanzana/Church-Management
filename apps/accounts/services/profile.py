"""accounts.services.profile: what a signed-in person can change about THEMSELVES.

Four areas, all acting on the person passed in (never on someone else):
  * details   -- birth date and profile photo
  * email     -- add / change (pending until verified), resend, cancel, remove, verify
  * password  -- change it (current password required)

Rules that matter (docs: AGENTS.md, "Behaviour rules"):
  * `User.email` holds VERIFIED addresses only. An address that is waiting for
    a link click lives in `User.pending_email`.
  * A verified address is unique (case-insensitive). A pending address is NOT:
    when someone verifies it first, everyone else who was still waiting on it
    loses it and is told through the `email_released` signal.
  * Tokens are stored as SHA-256 hashes, never raw. They live VERIFY_LINK_MINUTES.
  * Every function locks the person's row, works in `transaction.atomic()` and
    writes an audit row with `log_action()`. Audit keys never contain the words
    "password" or "token" (log_action would drop them silently).
Views stay thin; they only call these functions and turn ServiceError into messages.
"""
import hashlib
import logging
import math
import secrets
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from PIL import Image, ImageOps

from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.core.services import ServiceError

from ..models import EmailToken, User
from ..signals import email_released
from .users import duplicate_message

logger = logging.getLogger(__name__)

VERIFY = EmailToken.Purpose.VERIFY_EMAIL

LINK_INVALID = "This link is invalid or has expired. Request a new one from your profile."
EMAIL_IN_USE = "That email address is already used by another account."

PHOTO_SIZE = 512          # photos are cropped to a square and shrunk to this many pixels
PHOTO_QUALITY = 85


# ----------------------------------------------------------------- settings
def link_minutes():
    """How long a verification link works (settings.VERIFY_LINK_MINUTES, default 60)."""
    return getattr(settings, "VERIFY_LINK_MINUTES", 60)


def _resend_seconds():
    return getattr(settings, "VERIFY_RESEND_SECONDS", 60)


def _max_per_hour():
    return getattr(settings, "VERIFY_MAX_PER_HOUR", 10)


# ------------------------------------------------------------------ helpers
def _hash_token(raw):
    """What is stored for a token. The raw value only ever exists in the email link."""
    return hashlib.sha256((raw or "").encode()).hexdigest()


def normalize_email(value):
    """Emails are stored trimmed and lowercase."""
    return (value or "").strip().lower()


def _lock_user(pk):
    """Re-read the person under a row lock (call inside transaction.atomic())."""
    return User.objects.select_for_update().get(pk=pk)


def _void_tokens(user_id, now):
    """Make every unused verification link of this person stop working."""
    EmailToken.objects.filter(
        user_id=user_id, purpose=VERIFY, used_at__isnull=True, expires_at__gt=now,
    ).update(expires_at=now)


def _delete_file_after_commit(storage, name):
    """Remove an old file from disk, but only if the database change was kept."""
    if not name:
        return

    def _delete():
        try:
            storage.delete(name)
        except OSError:
            logger.warning("Could not delete old profile file %s", name)

    transaction.on_commit(_delete)


def _entity(person):
    """The audit entity fields for a person."""
    return dict(entity_type="user", entity_id=person.pk, entity_label=str(person))


# ------------------------------------------------------------------ details
def _process_photo(upload):
    """Turn an uploaded image into a clean square JPEG.

    Re-encoding drops EXIF data (GPS, camera) and anything hidden in the file.
    Transparent images are flattened onto white. Raises ServiceError if the
    file is not a readable image.
    """
    try:
        image = ImageOps.exif_transpose(Image.open(upload))
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            flat = Image.new("RGB", rgba.size, "white")
            flat.paste(rgba, mask=rgba.getchannel("A"))
            image = flat
        else:
            image = image.convert("RGB")
        image = ImageOps.fit(image, (PHOTO_SIZE, PHOTO_SIZE), method=Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.save(buffer, "JPEG", quality=PHOTO_QUALITY, optimize=True)
    except Exception:  # Pillow raises many different errors for bad files
        raise ServiceError("That image could not be read. Try a JPG, PNG or WebP photo.")
    return ContentFile(buffer.getvalue())


def update_details(request, user, *, birth_date, photo=None):
    """Save a new birth date and/or a new photo. Returns True if anything changed.

    The birth date is part of the duplicate-name rule (same name is allowed only
    with different birth dates), so a change is checked with `duplicate_message`.
    The old photo file is deleted from disk after the change is committed.
    """
    with transaction.atomic():
        person = _lock_user(user.pk)
        fields, before, after = [], {}, {}

        if birth_date != person.birth_date:
            if birth_date is None:
                raise ServiceError("Birth date cannot be removed once it is set.")
            message = duplicate_message(
                {
                    "first_name": person.first_name,
                    "middle_name": person.middle_name,
                    "last_name": person.last_name,
                    "birth_date": birth_date,
                },
                exclude_pk=person.pk,
            )
            if message:
                raise ServiceError(message)
            before["birth_date"], after["birth_date"] = person.birth_date, birth_date
            person.birth_date = birth_date
            fields.append("birth_date")

        old_photo = None
        if photo is not None:
            content = _process_photo(photo)
            old_photo = person.profile_image.name
            person.profile_image.save(
                f"{person.account_id}-{uuid4().hex[:8]}.jpg", content, save=False,
            )
            after["photo"] = "changed"
            fields.append("profile_image")

        if not fields:
            return False
        person.save(update_fields=[*fields, "updated_at"])
        _delete_file_after_commit(person.profile_image.storage, old_photo)
        log_action(
            "account.profile_update", actor=person, request=request,
            before=before or None, after=after, **_entity(person),
        )
    return True


def remove_photo(request, user):
    """Delete the profile photo. Returns False when there was none (already done)."""
    with transaction.atomic():
        person = _lock_user(user.pk)
        if not person.profile_image:
            return False
        old_photo = person.profile_image.name
        storage = person.profile_image.storage
        person.profile_image = ""
        person.save(update_fields=["profile_image", "updated_at"])
        _delete_file_after_commit(storage, old_photo)
        log_action(
            "account.profile_update", actor=person, request=request,
            after={"photo": "removed"}, **_entity(person),
        )
    return True


# -------------------------------------------------------------------- email
def resend_wait_seconds(user):
    """Seconds left before another link may be requested (0 = allowed now)."""
    last = (
        EmailToken.objects.filter(user=user, purpose=VERIFY)
        .order_by("-created_at").values_list("created_at", flat=True).first()
    )
    if last is None:
        return 0
    left = _resend_seconds() - (timezone.now() - last).total_seconds()
    return max(0, math.ceil(left))


def _check_send_limits(person, now):
    """Refuse when links are requested too fast or too often."""
    wait = resend_wait_seconds(person)
    if wait:
        raise ServiceError(f"Please wait {wait} seconds before asking for another link.")
    recent = EmailToken.objects.filter(
        user=person, purpose=VERIFY, created_at__gte=now - timedelta(hours=1),
    ).count()
    if recent >= _max_per_hour():
        raise ServiceError("Too many verification emails were requested. Try again in an hour.")


def _send_verification_email(person, to_address, raw_token):
    """Email the link. Any failure becomes a friendly ServiceError (and rolls back the caller)."""
    path = reverse("accounts:verify_email", args=[raw_token])
    context = {
        "name": person.full_name,
        "link": settings.SITE_URL.rstrip("/") + path,
        "minutes": link_minutes(),
        "site_name": "Church Management",
    }
    message = EmailMultiAlternatives(
        subject="Verify your email address",
        body=render_to_string("accounts/email/verify_email.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_address],
    )
    message.attach_alternative(render_to_string("accounts/email/verify_email.html", context), "text/html")
    try:
        message.send()
    except Exception:
        logger.exception("Could not send the verification email")
        raise ServiceError("We could not send the email right now. Please try again in a few minutes.")


def request_email_verification(request, user, email=None):
    """Store `email` as the pending address and email a verification link. Returns the address.

    email=None means "resend to the address already waiting". Cases:
      * the address is already this person's verified email -> refused;
      * another account has it VERIFIED -> refused (EMAIL_IN_USE);
      * another account only has it pending -> allowed, the link is sent anyway;
      * too soon / too many sends -> refused (60 s apart, 10 per hour by default).
    Older unused links stop working. The mail is sent inside the transaction, so
    if sending fails nothing is saved and the cooldown is not used up.
    """
    with transaction.atomic():
        person = _lock_user(user.pk)
        if email is None:
            email = person.pending_email
            if not email:
                raise ServiceError("There is no email waiting for verification.")
        email = normalize_email(email)

        if person.email and person.email.lower() == email and person.email_verified_at:
            raise ServiceError("That is already your verified email.")
        if User.objects.filter(email__iexact=email).exclude(pk=person.pk).exists():
            raise ServiceError(EMAIL_IN_USE)

        now = timezone.now()
        _check_send_limits(person, now)

        changed = (person.pending_email or "").lower() != email
        person.pending_email = email
        person.save(update_fields=["pending_email", "updated_at"])
        _void_tokens(person.pk, now)

        raw = secrets.token_urlsafe(32)
        EmailToken.objects.create(
            user=person, purpose=VERIFY, email=email, token_hash=_hash_token(raw),
            expires_at=now + timedelta(minutes=link_minutes()),
        )
        _send_verification_email(person, email, raw)
        log_action(
            "account.email_verification_sent", actor=person, request=request,
            after={"new_address": changed}, **_entity(person),
        )
    return email


def cancel_pending_email(request, user):
    """Drop the address that is waiting for verification. False if there was none."""
    with transaction.atomic():
        person = _lock_user(user.pk)
        if not person.pending_email:
            return False
        person.pending_email = None
        person.save(update_fields=["pending_email", "updated_at"])
        _void_tokens(person.pk, timezone.now())
        log_action("account.email_verification_cancelled", actor=person, request=request, **_entity(person))
    return True


def remove_email(request, user):
    """Remove the verified AND the pending email. Login by email stops working. False if none."""
    with transaction.atomic():
        person = _lock_user(user.pk)
        if not person.email and not person.pending_email:
            return False
        had_verified = bool(person.email)
        person.email = None
        person.email_verified_at = None
        person.pending_email = None
        person.save(update_fields=["email", "email_verified_at", "pending_email", "updated_at"])
        _void_tokens(person.pk, timezone.now())
        log_action(
            "account.email_removed", actor=person, request=request,
            after={"had_verified_email": had_verified}, **_entity(person),
        )
    return True


def find_valid_token(user, raw_token):
    """The unused, unexpired token that belongs to this person and matches their pending address.

    Read-only: used by the confirmation page to show which address is being verified.
    """
    if not raw_token:
        return None
    token = EmailToken.objects.filter(
        token_hash=_hash_token(raw_token), purpose=VERIFY, user_id=user.pk,
        used_at__isnull=True, expires_at__gt=timezone.now(),
    ).first()
    if token and (user.pending_email or "").lower() == token.email.lower():
        return token
    return None


@dataclass
class VerifyResult:
    """changed is False when the address was already verified (a repeated click)."""

    changed: bool
    email: str


def verify_email(request, user, raw_token):
    """Confirm the pending address with the token from the email link.

    The token must belong to `user` (the signed-in person). On success the
    address moves from `pending_email` to `email`, and anyone else still
    waiting on the same address loses it and gets the `email_released` signal.
    Locks people in ascending id order (AGENTS.md lock rule). Repeating a link
    that already worked answers calmly instead of failing.
    """
    token_hash = _hash_token(raw_token)
    with transaction.atomic():
        token = EmailToken.objects.filter(token_hash=token_hash, purpose=VERIFY, user_id=user.pk).first()
        if token is None:
            raise ServiceError(LINK_INVALID)
        email = token.email.lower()

        waiting = list(
            User.objects.filter(pending_email__iexact=email).exclude(pk=user.pk).values_list("pk", flat=True)
        )
        locked = {
            u.pk: u
            for u in User.objects.select_for_update().filter(pk__in={user.pk, *waiting}).order_by("pk")
        }
        person = locked.pop(user.pk)
        token = EmailToken.objects.select_for_update().get(pk=token.pk)
        now = timezone.now()

        if token.used_at is not None:
            if person.email == email and person.email_verified_at:
                return VerifyResult(False, email)
            raise ServiceError(LINK_INVALID)
        if token.expires_at <= now or (person.pending_email or "").lower() != email:
            raise ServiceError(LINK_INVALID)
        if User.objects.filter(email__iexact=email).exclude(pk=person.pk).exists():
            raise ServiceError(EMAIL_IN_USE)

        replaced = bool(person.email) and person.email != email
        person.email = email
        person.email_verified_at = now
        person.pending_email = None
        try:
            with transaction.atomic():      # savepoint: a unique clash must not poison the outer transaction
                person.save(update_fields=["email", "email_verified_at", "pending_email", "updated_at"])
        except IntegrityError:
            raise ServiceError(EMAIL_IN_USE)
        EmailToken.objects.filter(pk=token.pk).update(used_at=now)
        _void_tokens(person.pk, now)

        # Everyone else who was only waiting on this address loses it.
        for other in locked.values():
            other.pending_email = None
            other.save(update_fields=["pending_email", "updated_at"])
            _void_tokens(other.pk, now)
            email_released.send(sender=User, user=other)
            log_action("account.email_released", actor=person, request=request, **_entity(other))

        log_action(
            "account.email_verified", actor=person, request=request,
            after={"replaced_old_email": replaced, "released_others": len(locked)}, **_entity(person),
        )
    return VerifyResult(True, email)


# ----------------------------------------------------------------- password
def change_password(request, user, current_password, new_password):
    """Change the signed-in person's password after checking the current one.

    The new password's shape (length, common, similar to name) is checked by the
    form. Other sessions end (the hash changes) while this one is re-signed.
    A wrong current password is audited as a failure and raises ServiceError.
    """
    if not user.check_password(current_password):
        log_action(
            "account.password_change_failed", actor=user, request=request,
            status=AuditLog.Result.FAILED, after={"reason": "wrong_current"}, **_entity(user),
        )
        raise ServiceError("Your current password is incorrect.")
    with transaction.atomic():
        person = _lock_user(user.pk)
        person.set_password(new_password)
        person.save(update_fields=["password", "updated_at"])
        update_session_auth_hash(request, person)
        log_action(
            "account.password_changed", actor=person, request=request,
            after={"changed": True}, **_entity(person),
        )