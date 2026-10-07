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
from django.utils import timezone


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
