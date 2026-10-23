"""prayer: member -> coordinator prayer requests. Never copy body into audit_log."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Exists, OuterRef, Q

from apps.accounts.models import Role
from apps.core.models import ArchivableModel, archived_matches_status

USER = settings.AUTH_USER_MODEL


def signin_filter():
    """Q object that matches people who can sign in (mirrors `User.can_sign_in`, which is a Python property).

    (verify) Keep this in step with `accounts.models.User.can_sign_in`; a fresh copy of the rule is a footgun,
    so prefer an existing queryset helper if the repo has one.
    """
    from apps.accounts.models import PersonType, Status

    return Q(person_type=PersonType.MEMBER_ACCOUNT) & ~Q(status__in=[Status.ARCHIVED, Status.SUSPENDED])


class PrayerRequestQuerySet(models.QuerySet):
    """Who may see which prayer request. The ONLY place these two scoping rules live."""

    def mine(self, user):
        """The person's own requests that are not archived (newest first is the caller's choice)."""
        return self.filter(user=user, archived_at__isnull=True)

    def inbox_for(self, actor):
        """Requests `actor` may handle, never archived.

        coordinator          requests of their own extension
        admin, super-admin   requests whose extension has no coordinator who can sign in
        anyone else          nothing
        """
        live = self.filter(archived_at__isnull=True)
        if actor is None or not getattr(actor, "is_authenticated", False):
            return self.none()
        if actor.role == Role.COORDINATOR:
            if actor.extension_id is None:
                return self.none()
            return live.filter(extension_id=actor.extension_id)
        if actor.role in (Role.SUPER_ADMIN, Role.ADMIN):
            has_coordinator = get_user_model().objects.filter(
                role=Role.COORDINATOR, extension_id=OuterRef("extension_id"),
            ).filter(signin_filter())
            return live.annotate(_has_coord=Exists(has_coordinator)).filter(_has_coord=False)
        return self.none()


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

    objects = PrayerRequestQuerySet.as_manager()

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