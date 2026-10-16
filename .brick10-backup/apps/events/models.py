"""events: church events. extension NULL = applies to all extensions."""
from django.conf import settings
from django.db import models

from apps.core.models import ArchivableModel, TimestampedModel


class Event(TimestampedModel, ArchivableModel):
    extension = models.ForeignKey(
        "accounts.Extension", null=True, blank=True,
        on_delete=models.CASCADE, related_name="events",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "event"

    def __str__(self):
        return self.title