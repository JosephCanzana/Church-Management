"""faith: goals, check-ins, daily activity, streaks."""
from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from core.models import ArchivableModel, archived_matches_status


class Goal(ArchivableModel):
    class Repeat(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        ARCHIVED = "archived", "Archived"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="goals")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)       # NULL = never ends
    repeat_type = models.CharField(max_length=10, choices=Repeat.choices)
    # 0 = Monday ... 6 = Sunday; only for weekly goals
    weekdays = ArrayField(
        models.SmallIntegerField(validators=[MinValueValidator(0), MaxValueValidator(6)]),
        size=7, null=True, blank=True,
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    previous_goal = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="next_goals",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "goal"
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="goal_end_after_start",
            ),
            models.CheckConstraint(
                condition=(
                    Q(repeat_type="daily", weekdays__isnull=True)
                    | Q(repeat_type="weekly", weekdays__isnull=False)
                ),
                name="goal_repeat_matches_weekdays",
            ),
            archived_matches_status("goal_archived_matches_status"),
        ]
        indexes = [models.Index(fields=["user", "status"], name="ix_goal_user_status")]

    def clean(self):
        # array length / duplicates are checked here, not in the database
        if self.repeat_type == self.Repeat.WEEKLY:
            if not self.weekdays:
                raise ValidationError({"weekdays": "Pick at least one weekday."})
            if len(set(self.weekdays)) != len(self.weekdays):
                raise ValidationError({"weekdays": "Weekdays must not repeat."})
        elif self.weekdays:
            raise ValidationError({"weekdays": "Daily goals have no weekdays."})

    def __str__(self):
        return self.title


class GoalCheckin(models.Model):
    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name="checkins")
    checkin_date = models.DateField()
    done = models.BooleanField(default=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "goal_checkin"
        constraints = [
            models.UniqueConstraint(fields=["goal", "checkin_date"], name="uq_goal_checkin_day"),
        ]


class UserDailyActivity(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_activity")
    activity_date = models.DateField()
    opened_app = models.BooleanField(default=False)
    chapters_read = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "user_daily_activity"
        constraints = [
            models.UniqueConstraint(fields=["user", "activity_date"], name="uq_user_daily_activity"),
        ]


class UserStreak(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, primary_key=True,
        on_delete=models.CASCADE, related_name="streak",
    )
    current_open_streak = models.PositiveIntegerField(default=0)
    longest_open_streak = models.PositiveIntegerField(default=0)
    last_open_date = models.DateField(null=True, blank=True)
    current_read_streak = models.PositiveIntegerField(default=0)
    longest_read_streak = models.PositiveIntegerField(default=0)
    last_read_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "user_streak"