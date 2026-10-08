"""core.services: small helpers shared by every app.

Archive, restore and force-delete live here so each app does not write its
own version. They only touch the record they are given; the CALLER is
responsible for `transaction.atomic()`, for locking the row first
(`select_for_update`), for permission checks and for `log_action()`.

Why `save(update_fields=...)` everywhere: the purge trigger
(core migration 0002) runs on `UPDATE OF archived_at`, so `archived_at` must be
in the column list for it to fire. And a plain `save()` would write the stale
in-memory `purge_at` back over the value the trigger just computed.
"""
import random
import re
import secrets
from datetime import timedelta

from django.db import connection
from django.utils import timezone

from .models import SubmissionToken


class ServiceError(Exception):
    """A business rule stopped an action. The message is safe to show people."""


def _fields(instance, names):
    """Column names to write: `names` plus updated_at when the model has it."""
    fields = list(dict.fromkeys(names))
    if any(f.name == "updated_at" for f in instance._meta.get_fields()):
        fields.append("updated_at")
    return fields


def archive(instance, *, extra_fields=()):
    """Mark an archivable record as archived now.

    `extra_fields` are other columns the caller already changed on the
    instance (a status, an archive reason). The trigger fills `purge_at`, so
    it is reloaded here to keep the instance accurate. Never set it from Django.
    """
    instance.archived_at = timezone.now()
    instance.save(update_fields=_fields(instance, ["archived_at", *extra_fields]))
    instance.refresh_from_db(fields=["purge_at"])


def restore(instance, *, extra_fields=()):
    """Clear `archived_at`; the trigger clears `purge_at` in the same UPDATE."""
    instance.archived_at = None
    instance.save(update_fields=_fields(instance, ["archived_at", *extra_fields]))
    instance.refresh_from_db(fields=["purge_at"])


def force_delete(instance):
    """Permanently delete an ARCHIVED record.

    May raise ProtectedError / RestrictedError when other rows still point at
    it; callers turn that into a friendly ServiceError. Write the audit row
    BEFORE calling this (AGENTS.md), and delete any files from disk first.
    """
    if not instance.is_archived:
        raise ServiceError("Only archived records can be deleted.")
    instance.delete()


# ------------------------------------------------------- one-time form tokens
# Stops the SAME form submission from running twice (double click, two tabs, a
# refresh that re-posts), including two requests that arrive at the same instant.
# The page puts new_submission_token() in a hidden field; the service calls
# consume_submission_token() INSIDE its transaction.atomic(). The token is the
# primary key of submission_token, so a second simultaneous insert waits for the
# first, then fails, and the duplicate returns False. If the first transaction
# rolls back (the action failed), its token disappears too, so retrying works.
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
TOKEN_KEEP_DAYS = 2


def new_submission_token():
    """A fresh random token for a form to carry in a hidden field."""
    return secrets.token_urlsafe(24)


def consume_submission_token(token):
    """Record `token` as used. True the first time, False for a replay.

    Must be called inside transaction.atomic(). A missing or oddly shaped token
    raises ServiceError (the form is stale or was tampered with).
    """
    if not connection.in_atomic_block:
        raise RuntimeError("consume_submission_token() must run inside transaction.atomic().")
    if not token or not TOKEN_PATTERN.match(token):
        raise ServiceError("This form has expired. Reload the page and try again.")
    _, created = SubmissionToken.objects.get_or_create(token=token)
    if random.random() < 0.02:  # tidy up now and then; no separate job needed
        cutoff = timezone.now() - timedelta(days=TOKEN_KEEP_DAYS)
        SubmissionToken.objects.filter(created_at__lt=cutoff).delete()
    return created
