"""attendance: one sheet per gathering per extension."""
from django.conf import settings
from django.db import models

from apps.core.models import ArchivableModel, archived_matches_status


class Attendance(ArchivableModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UPLOADED = "uploaded", "Uploaded"
        ARCHIVED = "archived", "Archived"

    extension = models.ForeignKey(
        "accounts.Extension", on_delete=models.RESTRICT, related_name="attendances",
    )
    event = models.ForeignKey(
        "events.Event", null=True, blank=True, on_delete=models.SET_NULL, related_name="attendances",
    )
    attendance_date = models.DateField()
    label = models.CharField(max_length=100, default="Sunday Service")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    # frozen on upload
    total_present = models.PositiveIntegerField(null=True, blank=True)
    members_present = models.PositiveIntegerField(null=True, blank=True)
    first_timers_count = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "attendance"
        constraints = [
            models.UniqueConstraint(
                fields=["extension", "attendance_date", "label"], name="uq_attendance_ext_date_label",
            ),
            archived_matches_status("attendance_archived_matches_status"),
        ]
        indexes = [
            models.Index(fields=["extension", "-attendance_date"], name="ix_attendance_ext_date"),
        ]

    def __str__(self):
        return f"{self.label} {self.attendance_date}"


class AttendanceMember(models.Model):
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="attendance_records",
    )  # NULL if the account was purged; member_name keeps the record readable
    member_name = models.CharField(max_length=255)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "attendance_member"
        constraints = [
            models.UniqueConstraint(fields=["attendance", "user"], name="uq_attendance_member"),
        ]


class AttendanceFirstTimer(models.Model):
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name="first_timers")
    name = models.CharField(max_length=255)
    birth_date = models.DateField(null=True, blank=True)
    converted_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "attendance_first_timer"