from django.conf import settings
from django.db import models
from django.db.models import Q


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ArchivableModel(models.Model):
    """archived_at / purge_at. purge_at is filled by the set_purge_at()
    database trigger (see core migration 0002), not by Django."""

    archived_at = models.DateTimeField(null=True, blank=True)
    purge_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_archived(self):
        return self.archived_at is not None


def archived_matches_status(name):
    """CHECK ((status = 'archived') = (archived_at IS NOT NULL))."""
    return models.CheckConstraint(
        condition=(
            Q(status="archived", archived_at__isnull=False)
            | (~Q(status="archived") & Q(archived_at__isnull=True))
        ),
        name=name,
    )


class SiteSetting(models.Model):
    key = models.CharField(max_length=100, primary_key=True)
    value = models.JSONField()
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "site_setting"

    def __str__(self):
        return self.key


class RetentionPolicy(models.Model):
    # entity_type must equal the real table name (the trigger looks it up
    # with TG_TABLE_NAME), so every archivable model sets db_table.
    entity_type = models.CharField(max_length=100, primary_key=True)
    purge_after_days = models.PositiveIntegerField(null=True, blank=True)  # NULL = never
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "retention_policy"
        constraints = [
            models.CheckConstraint(
                condition=Q(purge_after_days__isnull=True) | Q(purge_after_days__gt=0),
                name="retention_days_positive",
            ),
        ]

    def __str__(self):
        return self.entity_type


class SubmissionToken(models.Model):
    """A form submission that has already been processed (see core.services).

    The token is the primary key, so the database itself refuses to record the
    same submission twice, even for two requests arriving at the same moment.
    Old rows are removed by consume_submission_token() as it goes.
    """
    token = models.CharField(max_length=64, primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "submission_token"
        indexes = [models.Index(fields=["created_at"], name="ix_submission_token_created")]

    def __str__(self):
        return self.token[:8]
