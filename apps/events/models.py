"""events: church events. extension NULL = applies to all extensions (a global event).

Cancel is final: a cancelled event stays on record with `cancelled_at` set. It is
not archived (archive/purge is the generic retention path), it is just marked.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.core.models import ArchivableModel, TimestampedModel


class Event(TimestampedModel, ArchivableModel):
    extension = models.ForeignKey(
        "accounts.Extension", null=True, blank=True,
        on_delete=models.CASCADE, related_name="events",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "event"
        constraints = [
            models.CheckConstraint(
                condition=Q(end_at__isnull=True) | Q(end_at__gt=F("start_at")),
                name="event_end_after_start",
            ),
            models.CheckConstraint(
                condition=Q(cancelled_by__isnull=True) | Q(cancelled_at__isnull=False),
                name="event_cancelled_by_needs_time",
            ),
        ]
        indexes = [models.Index(fields=["start_at"], name="ix_event_start")]

    @property
    def is_cancelled(self):
        return self.cancelled_at is not None

    def __str__(self):
        return self.title

class EventReminder(models.Model):
    """One reminder already sent: a person, an event, a lead time and the start time it was for.

    The unique row is what makes the job safe to run twice. A rescheduled event has a new
    `event_start`, so it reminds again; a cancelled one is skipped by the job itself.
    """
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="reminders")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    lead_minutes = models.PositiveIntegerField()
    event_start = models.DateTimeField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "event_reminder"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "user", "lead_minutes", "event_start"],
                name="uq_event_reminder",
            ),
        ]