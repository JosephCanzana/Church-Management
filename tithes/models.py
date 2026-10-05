"""tithes: money record for one attendance sheet + per-person tithes."""
from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import ArchivableModel, archived_matches_status


class TithesOffering(ArchivableModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UPLOADED = "uploaded", "Uploaded"
        ARCHIVED = "archived", "Archived"

    attendance = models.OneToOneField(
        "attendance.Attendance", on_delete=models.CASCADE, related_name="tithes_offering",
    )
    offering_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    denomination_breakdown = models.JSONField(default=dict, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tithes_offering"
        constraints = [
            models.CheckConstraint(condition=Q(offering_amount__gte=0), name="offering_amount_nonneg"),
            archived_matches_status("tithes_archived_matches_status"),
            models.CheckConstraint(
                condition=Q(status="draft") | Q(total_amount__isnull=False),
                name="tithes_total_when_not_draft",
            ),
        ]


class TitheEntry(models.Model):
    tithes_offering = models.ForeignKey(TithesOffering, on_delete=models.CASCADE, related_name="entries")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="tithe_entries",
    )
    giver_name = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = "tithe_entry"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="tithe_amount_positive"),
            models.UniqueConstraint(fields=["tithes_offering", "user"], name="uq_tithe_entry_user"),
        ]