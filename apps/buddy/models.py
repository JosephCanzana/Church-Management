"""buddy: accountability partners. One user_buddy row = one partnership period."""
from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Greatest, Least

USER = settings.AUTH_USER_MODEL


class UserBuddy(models.Model):
    # App sorts the two ids before inserting (user_a < user_b), and enforces
    # "one ACTIVE partner per user" inside a transaction (select_for_update
    # on both users) when a request is accepted.
    user_a = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="buddy_rows_as_a")
    user_b = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="buddy_rows_as_b")
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    final_note = models.TextField(null=True, blank=True)
    # Two-step ending (Brick 7): one person asks, the other agrees. Brick 6 only adds the columns.
    end_requested_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    end_requested_at = models.DateTimeField(null=True, blank=True)
    end_request_note = models.TextField(null=True, blank=True)
    partner_final_note = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "user_buddy"
        constraints = [
            models.CheckConstraint(condition=Q(user_a__lt=F("user_b")), name="user_buddy_ordered_pair"),
            models.CheckConstraint(
                condition=Q(ended_at__isnull=False) | Q(ended_by__isnull=True, final_note__isnull=True),
                name="user_buddy_end_fields",
            ),
            models.UniqueConstraint(
                fields=["user_a", "user_b"], condition=Q(ended_at__isnull=True),
                name="uq_user_buddy_active",
            ),
            # A requester always has a request time. The reverse is NOT required: deleting the
            # requester sets `end_requested_by` to NULL (SET_NULL) and must not break the row.
            models.CheckConstraint(
                condition=Q(end_requested_by__isnull=True) | Q(end_requested_at__isnull=False),
                name="user_buddy_end_request_time",
            ),
            # The partner's own final note only exists once the partnership has ended.
            models.CheckConstraint(
                condition=Q(ended_at__isnull=False) | Q(partner_final_note__isnull=True),
                name="user_buddy_partner_note_needs_end",
            ),
        ]


class BuddyRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        CANCELLED = "cancelled", "Cancelled"

    from_user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="sent_buddy_requests")
    to_user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="received_buddy_requests")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "buddy_request"
        constraints = [
            models.CheckConstraint(condition=~Q(from_user=F("to_user")), name="buddy_request_not_self"),
            # one pending request per pair, in either direction
            models.UniqueConstraint(
                Least("from_user", "to_user"), Greatest("from_user", "to_user"),
                condition=Q(status="pending"), name="uq_buddy_request_pending",
            ),
        ]


class BuddyNote(models.Model):
    class Kind(models.TextChoices):
        MESSAGE = "message", "Message"
        NUDGE = "nudge", "Nudge"
        PRAYED = "prayed", "I prayed for you"

    buddy = models.ForeignKey(UserBuddy, on_delete=models.CASCADE, related_name="notes")
    sender = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.MESSAGE)
    body = models.TextField(null=True, blank=True)    # NULL for a nudge or a prayed tap
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "buddy_note"
        indexes = [models.Index(fields=["buddy", "id"], name="ix_buddy_note_buddy_id")]
        constraints = [
            # A message needs text; a nudge or a prayed tap never has any.
            models.CheckConstraint(
                condition=(
                    Q(kind="message", body__isnull=False)
                    | Q(kind__in=["nudge", "prayed"], body__isnull=True)
                ),
                name="buddy_note_kind_body",
            ),
        ]
