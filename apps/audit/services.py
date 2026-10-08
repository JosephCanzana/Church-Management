"""audit.services: the one place that writes to the audit trail.

Every other app calls `log_action()` instead of creating `AuditLog` rows
itself, so the privacy rules and the `action_logged` signal are applied in one
spot. The trail is append-only: this module only ever inserts.
"""
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from .models import AuditLog
from .signals import action_logged

# PRIVACY (AGENTS.md): passwords, tokens, goal text, notes, prayer text and
# tithe amounts must never reach audit_log. Callers should not pass them, but
# this is the safety net that removes them anyway.
#   - a key CONTAINING any of these words is dropped (password_hash, new_token...)
#   - a key EQUAL to one of these names is dropped
# Extend SENSITIVE_KEYS when a new model gets free-text or money fields.
SENSITIVE_SUBSTRINGS = ("password", "token", "secret")
SENSITIVE_KEYS = {"goal_text", "note", "notes", "prayer_text", "message", "body", "amount"}

USER_AGENT_MAX_LENGTH = 1000


def _is_sensitive(key):
    """True if a dict key names a value that must not be logged."""
    key = str(key).lower()
    return key in SENSITIVE_KEYS or any(word in key for word in SENSITIVE_SUBSTRINGS)


def _scrub(value):
    """Return a copy of `value` with sensitive keys removed at every depth."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if not _is_sensitive(k)}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def _clean_json(data):
    """Scrub `data` and make it safe for a JSONField.

    Dates, Decimals and similar values are converted to strings by Django's
    JSON encoder, so a caller passing a `date` does not crash the log write.
    Returns None when there is nothing to store.
    """
    if data is None:
        return None
    return json.loads(json.dumps(_scrub(data), cls=DjangoJSONEncoder))


def log_action(
    action,
    *,
    actor=None,
    request=None,
    entity_type=None,
    entity_id=None,
    entity_label=None,
    extension_id=None,
    before=None,
    after=None,
    status=AuditLog.Result.SUCCESS,
    source=AuditLog.Source.WEB,
):
    """Write one audit row and announce it with the `action_logged` signal.

    action        -- dotted name such as "account.archive" or "account.login_failed".
    actor         -- the User doing it, or None (failed login, system job).
                     The name, role and extension are copied onto the row now,
                     so the history stays readable if the user is renamed or deleted.
    request       -- the HttpRequest, if any; supplies ip_address and user_agent.
    entity_*      -- the record the action was about. Refer to it by type and id
                     only (no foreign keys, so audit never depends on other apps).
    extension_id  -- which extension this belongs to. Defaults to the actor's own
                     extension; pass it explicitly when the target is elsewhere.
    before/after  -- changed fields only. Sensitive keys are stripped.
    status        -- AuditLog.Result.SUCCESS or FAILED.
    source        -- WEB by default. Management commands must pass
                     AuditLog.Source.SYSTEM_JOB and no actor.

    Runs in its own `transaction.atomic()` (a savepoint when the caller already
    has one), so the row and anything the signal receivers write commit or roll
    back together. A receiver that raises will propagate, on purpose: a broken
    alert rule should be noticed, not silently swallowed.
    """
    if extension_id is None and actor is not None:
        extension_id = getattr(actor, "extension_id", None)

    ip_address = user_agent = None
    if request is not None:
        # REMOTE_ADDR is the direct peer. Behind a reverse proxy this is the
        # proxy's address; if you deploy behind one, set it up properly
        # (trusted proxy + X-Forwarded-For handling) instead of trusting the
        # header blindly here, because clients can forge it.
        ip_address = request.META.get("REMOTE_ADDR") or None
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:USER_AGENT_MAX_LENGTH] or None

    with transaction.atomic():
        entry = AuditLog.objects.create(
            actor_id=actor.pk if actor is not None else None,
            actor_label=str(actor) if actor is not None else None,
            actor_role=getattr(actor, "role", None) if actor is not None else None,
            extension_id=extension_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_label=entity_label,
            before=_clean_json(before),
            after=_clean_json(after),
            ip_address=ip_address,
            user_agent=user_agent,
            source=source,
            status=status,
        )
        action_logged.send(sender=AuditLog, entry=entry)
    return entry