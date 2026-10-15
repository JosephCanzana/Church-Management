"""notifications.listeners: turn events from other apps into bell notifications.

`notifications` depends on `accounts` (never the other way round), so it listens
to the signals accounts sends, and to `audit.signals.action_logged` for the admin alerts.
This file is imported once when Django starts (NotificationsConfig.ready()).

PRIVACY (AGENTS.md): a notification says THAT something happened. It never carries
the email address, who took it, or any private text; the bell shows a fixed sentence
chosen from `type`.
"""
from django.dispatch import receiver

from apps.accounts.models import Status, User
from apps.accounts.signals import email_released
from apps.audit.signals import action_logged

from .models import Notification, NotificationPreference, NotificationRule

EMAIL_RELEASED = "email_released"
#: Type of every bell item made from a `notification_rule`. `data["action"]` says which audit action.
ADMIN_ALERT = "admin_alert"


@receiver(email_released, dispatch_uid="notifications.email_released")
def notify_email_released(sender, user, **kwargs):
    """Tell someone their pending (unverified) email was removed because another account verified it.

    Honors the person's own in-app preference for this type (default: on).
    Runs inside the sender's transaction, so it is undone if that rolls back.
    """
    preference = NotificationPreference.objects.filter(user=user, type=EMAIL_RELEASED).first()
    if preference is not None and not preference.in_app:
        return
    Notification.objects.create(
        user=user,
        type=EMAIL_RELEASED,
        importance=Notification.Importance.IMPORTANT,
        entity_type="user",
        entity_id=user.pk,
        data={"reason": "verified_by_another_account"},
    )


@receiver(action_logged, dispatch_uid="notifications.admin_alert")
def notify_admin_alert(sender, entry, **kwargs):
    """Apply `notification_rule`: bell the people of the chosen roles when an audited action happens.

    Looks up an ENABLED rule for `entry.action`; no rule, nothing happens. The recipients are the
    people whose role is in the rule's `notify_roles` and who can still sign in, except the person who
    did the action (they know). Anyone who switched off in-app alerts of this type is skipped.

    PRIVACY: the item says THAT something happened. `data` holds only the audit action name; the
    linked page does the permission check. `notifications` never imports a feature app: the record
    is referred to by `entity_type` / `entity_id` only.
    Runs inside `log_action`'s transaction, so the alerts are undone if the action rolls back.
    """
    rule = NotificationRule.objects.filter(audit_action=entry.action, is_enabled=True).first()
    if rule is None or not rule.notify_roles:
        return
    recipients = (
        User.objects.filter(role__in=rule.notify_roles)
        .exclude(status__in=(Status.ARCHIVED, Status.SUSPENDED))
        .exclude(pk=entry.actor_id)
    )
    opted_out = set(
        NotificationPreference.objects.filter(type=ADMIN_ALERT, in_app=False).values_list("user_id", flat=True)
    )
    # The database requires entity_type and entity_id together or not at all.
    has_entity = entry.entity_type is not None and entry.entity_id is not None
    Notification.objects.bulk_create([
        Notification(
            user=person,
            type=ADMIN_ALERT,
            importance=Notification.Importance.IMPORTANT,
            actor_id=entry.actor_id,
            entity_type=entry.entity_type if has_entity else None,
            entity_id=entry.entity_id if has_entity else None,
            data={"action": entry.action},
            audit_log_id=entry.pk,
        )
        for person in recipients if person.pk not in opted_out
    ])
