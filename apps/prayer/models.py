"""prayer: member -> coordinator prayer requests. Never copy body into audit_log."""
from django.conf import settings
from django.db import models

from apps.core.models import ArchivableModel, archived_matches_status

USER = settings.AUTH_USER_MODEL


class PrayerRequest(ArchivableModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        REPLIED = "replied", "Replied"
        COMPLETED = "completed", "Completed"
        ARCHIVED = "archived", "Archived"

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="prayer_requests")
    coordinator = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assigned_prayer_requests",
    )  # NULL = visible to any coordinator of `extension`
    extension = models.ForeignKey(
        "accounts.Extension", on_delete=models.RESTRICT, related_name="prayer_requests",
    )  # sender's extension at send time
    body = models.TextField()
    is_anonymous = models.BooleanField(default=False)   # hides name in UI/API only
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "prayer_request"
        constraints = [archived_matches_status("prayer_archived_matches_status")]
        indexes = [
            models.Index(fields=["coordinator", "status"], name="ix_prayer_coord_status"),
            models.Index(fields=["extension", "status"], name="ix_prayer_ext_status"),
        ]


class PrayerReply(models.Model):
    prayer_request = models.ForeignKey(PrayerRequest, on_delete=models.CASCADE, related_name="replies")
    replier = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "prayer_reply"