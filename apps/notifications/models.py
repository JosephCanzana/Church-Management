"""notifications: bell, per-type preferences, admin-alert rules.
NEVER put private text (prayer, goals, notes, tithe amounts) in `data`."""
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q

USER = settings.AUTH_USER_MODEL


def default_notify_roles():
    return ["admin", "super_admin"]


class Notification(models.Model):
    class Importance(models.TextChoices):
        NORMAL = "normal", "Normal"
        IMPORTANT = "important", "Important"

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="notifications")  # recipient
    type = models.CharField(max_length=60)          # no choices: the list keeps growing
    actor = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    entity_type = models.CharField(max_length=60, null=True, blank=True)
    entity_id = models.BigIntegerField(null=True, blank=True)   # deliberately not an FK
    data = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=200, null=True, blank=True)
    importance = models.CharField(max_length=10, choices=Importance.choices, default=Importance.NORMAL)
    audit_log_id = models.BigIntegerField(null=True, blank=True)  # plain value, no FK
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    email_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notification"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(entity_type__isnull=True, entity_id__isnull=True)
                    | Q(entity_type__isnull=False, entity_id__isnull=False)
                ),
                name="notification_entity_pair",
            ),
            models.UniqueConstraint(
                fields=["user", "dedupe_key"], condition=Q(dedupe_key__isnull=False),
                name="uq_notification_dedupe",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="ix_notification_user"),
            models.Index(
                fields=["user", "-created_at"], condition=Q(read_at__isnull=True),
                name="ix_notification_unread",
            ),
        ]


class NotificationPreference(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="notification_preferences")
    type = models.CharField(max_length=60)
    in_app = models.BooleanField(default=True)
    email = models.BooleanField(default=False)

    class Meta:
        db_table = "notification_preference"
        constraints = [
            models.UniqueConstraint(fields=["user", "type"], name="uq_notification_pref"),
        ]


class NotificationRule(models.Model):
    ROLE_CHOICES = [("super_admin", "Super admin"), ("admin", "Admin"), ("coordinator", "Coordinator")]

    audit_action = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    notify_roles = ArrayField(
        models.CharField(max_length=20, choices=ROLE_CHOICES), default=default_notify_roles,
    )  # at least one role: enforced by the form (blank=False)
    is_enabled = models.BooleanField(default=True)
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notification_rule"