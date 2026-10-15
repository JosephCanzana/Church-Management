"""attendance: one sheet per gathering per extension.

A sheet (`Attendance`) is a draft until the coordinator uploads it; upload freezes
the counts and the `summary`. The two row tables keep SNAPSHOTS (names, gender, age
network, home extension) so an uploaded sheet still reads the same after a person
changes extension, gets a birthday or is purged.

Dependency direction: `accounts` <- `attendance`, so `Gender` comes from accounts and
`AgeNetwork` lives in `attendance.age` (models import it without a circular import).
"""
from django.conf import settings
from django.db import models

from apps.accounts.contact import clean_phone
from apps.accounts.models import Gender, Role, UserSpecialRole
from apps.core.models import ArchivableModel, archived_matches_status
from apps.core.text import clean_suffix, clean_text, person_name

from .age import AgeNetwork


class AttendanceQuerySet(models.QuerySet):
    """Queries on sheets, narrowed by who is asking."""

    def for_user(self, actor):
        """The sheets `actor` may open. The ONE place sheets are narrowed by who is asking.

        Views and services start from this, never from an ad hoc `.filter(extension=...)`.
          super-admin, admin -> every sheet
          coordinator        -> the sheets of their own extension
          attendance special member -> DRAFT sheets of the extension(s) where they hold the role
          anyone else, or someone who cannot sign in -> nothing
        Mirrors `attendance.permissions.can_view`; a test keeps the two in step.
        """
        if actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active:
            return self.none()
        if actor.role in (Role.SUPER_ADMIN, Role.ADMIN):
            return self.all()
        if actor.role == Role.COORDINATOR:
            return self.filter(extension_id=actor.extension_id) if actor.extension_id else self.none()
        seats = UserSpecialRole.objects.filter(user=actor, role_id="attendance").values("extension_id")
        return self.filter(status=Attendance.Status.DRAFT, extension_id__in=seats)


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
    # frozen on upload; total_present = members_present + first_timers_count
    total_present = models.PositiveIntegerField(null=True, blank=True)
    members_present = models.PositiveIntegerField(null=True, blank=True)
    first_timers_count = models.PositiveIntegerField(null=True, blank=True)
    # Frozen breakdown by gender and age network for first timers and members
    # (built by the summary calculation). NULL while the sheet is a draft.
    summary = models.JSONField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    # Who took the attendance. Defaults to the creator (set by the service); the
    # coordinator can change it. Shown on the sheet and the printout.
    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    # Set at upload: the coordinator who locked the sheet.
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)

    objects = AttendanceQuerySet.as_manager()

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

    @property
    def is_locked(self):
        """True once the sheet is uploaded: its rows can no longer change (until an admin reopens it)."""
        return self.status == self.Status.UPLOADED

    def __str__(self):
        return f"{self.label} {self.attendance_date}"


class AttendanceMember(models.Model):
    """One person marked present on a sheet, with a snapshot taken at tick time."""

    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="attendance_records",
    )  # NULL if the account was purged; member_name keeps the record readable
    member_name = models.CharField(max_length=255)
    # Snapshot at tick time, computed as of the sheet date. Blank means the person had no
    # gender or birth date yet; the sheet cannot be uploaded until the taker fills it in.
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    age_network = models.CharField(max_length=20, choices=AgeNetwork.choices, blank=True)
    # Where the person belongs (a visitor's differs from the sheet's extension). The text
    # copy stays readable if the extension is deleted, like member_name.
    home_extension = models.ForeignKey(
        "accounts.Extension", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    home_extension_name = models.CharField(max_length=150, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "attendance_member"
        constraints = [
            models.UniqueConstraint(fields=["attendance", "user"], name="uq_attendance_member"),
        ]


class AttendanceFirstTimer(models.Model):
    """A first-time visitor on a sheet. Not an account until the coordinator converts them."""

    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name="first_timers")
    # Names are stored lowercase with single spaces and shown through `full_name`.
    first_name = models.CharField(max_length=150)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150)
    suffix = models.CharField(max_length=10, blank=True)
    # One tap each on the sheet, so both are required here (unlike on AttendanceMember).
    gender = models.CharField(max_length=10, choices=Gender.choices)
    age_network = models.CharField(max_length=20, choices=AgeNetwork.choices)
    phone_number = models.CharField(max_length=20, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    converted_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "attendance_first_timer"

    @property
    def full_name(self):
        """Name for display, built by the same helper as `User.full_name`."""
        return person_name(self.first_name, self.middle_name, self.last_name, self.suffix)

    def save(self, *args, **kwargs):
        """Store names, suffix and phone the way the people forms do, whichever code wrote them."""
        self.first_name = clean_text(self.first_name)
        self.middle_name = clean_text(self.middle_name)
        self.last_name = clean_text(self.last_name)
        self.suffix = clean_suffix(self.suffix)
        self.phone_number = clean_phone(self.phone_number)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name
