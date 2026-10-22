"""tithes: the money record for one attendance sheet, plus per-person tithes.

One `TithesOffering` per attendance sheet (two services in a day = two reports). The flow is:
offering (one unnamed number) -> each person's tithe (`TitheEntry`) -> Total = offering + tithes ->
denomination counts -> the breakdown total must equal the Total.

Like `Attendance`, a report is a DRAFT until it is finalized; finalizing freezes the numbers
(`tithers_count`, `tithes_total`, `total_amount`, `breakdown_total`, `difference`) and they are NULL
while the report is a draft. Money is always `Decimal(12,2)`, never float.

Entries keep SNAPSHOTS of names, so a finalized report still reads the same after a person is purged.
A shared tithe (couple, household) is ONE entry with optional co-givers copied onto it; the standing
household link only pre-fills them, so later household changes never rewrite history.

Dependency direction: `accounts` <- `attendance` <- `tithes`.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q

from apps.accounts.models import Role, UserSpecialRole
from apps.core.models import ArchivableModel, TimestampedModel, archived_matches_status

#: Code of the special role (see accounts `special_role`) that gives a member a seat on tithes.
SPECIAL_ROLE = "tithes_offering"


class TithesOfferingQuerySet(models.QuerySet):
    """Queries on reports, narrowed by who is asking."""

    def for_user(self, actor):
        """The reports `actor` may open. The ONE place reports are narrowed by who is asking.

        Views and services start from this, never from an ad hoc `.filter(attendance__extension=...)`,
        so a report the person cannot reach looks exactly like one that does not exist.
          super-admin, admin -> every report
          coordinator        -> the reports of sheets in their own extension
          tithes special member -> DRAFT reports of the extension(s) where they hold the seat
          anyone else, or someone who cannot sign in -> nothing
        "Own extension" is the extension of the report's SHEET. Mirrors `tithes.permissions.can_view`;
        a test keeps the two in step. The sheet is loaded with the report (`select_related`) because
        every permission check looks at it.
        """
        if actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active:
            return self.none()
        reports = self.select_related("attendance")
        if actor.role in (Role.SUPER_ADMIN, Role.ADMIN):
            return reports
        if actor.role == Role.COORDINATOR:
            if not actor.extension_id:
                return reports.none()
            return reports.filter(attendance__extension_id=actor.extension_id)
        seats = UserSpecialRole.objects.filter(user=actor, role_id=SPECIAL_ROLE).values("extension_id")
        return reports.filter(status=TithesOffering.Status.DRAFT, attendance__extension_id__in=seats)


class TithesOffering(ArchivableModel):
    """The money record for one attendance sheet (a draft until finalized)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINALIZED = "finalized", "Finalized"

    attendance = models.OneToOneField(
        "attendance.Attendance", on_delete=models.CASCADE, related_name="tithes_offering",
    )
    # Step 1 of the flow: one number, unnamed (anyone may give it, guests included).
    offering_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Step 4: denomination -> count (1000/500/200/100/50/20/10/5/1). Editable while a draft.
    denomination_breakdown = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    # --- frozen at finalize, NULL while the report is a draft ------------------------------
    # Never recalculated afterwards; a reopen clears them again.
    tithers_count = models.PositiveIntegerField(null=True, blank=True)    # entries (one envelope = one)
    tithes_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)  # offering + tithes
    breakdown_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # SIGNED: breakdown_total - total_amount (negative = the cash counted is short).
    difference = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    # Only when an admin bypassed a mismatch DIRECTLY, with a typed reason. A bypass that went through
    # a request keeps its message and note on the request row, never here (privacy).
    bypass_reason = models.TextField(blank=True, default="")
    bypassed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TithesOfferingQuerySet.as_manager()

    class Meta:
        db_table = "tithes_offering"
        constraints = [
            models.CheckConstraint(condition=Q(offering_amount__gte=0), name="offering_amount_nonneg"),
            # A report is never archived on its own: the SHEET's archive makes it read-only. The mixin
            # columns stay (a core trigger may watch them), so this keeps them empty.
            archived_matches_status("tithes_archived_matches_status"),
            models.CheckConstraint(
                condition=Q(status__in=["draft", "finalized"]), name="tithes_status_valid",
            ),
            # Draft: every frozen value is empty.
            models.CheckConstraint(
                condition=Q(status="finalized") | Q(
                    tithers_count__isnull=True, tithes_total__isnull=True, total_amount__isnull=True,
                    breakdown_total__isnull=True, difference__isnull=True, finalized_at__isnull=True,
                    finalized_by__isnull=True, bypassed_by__isnull=True, bypass_reason="",
                ),
                name="tithes_draft_has_no_frozen_values",
            ),
            # Finalized: the numbers are all there (`finalized_by` may be NULL if that person was purged).
            models.CheckConstraint(
                condition=Q(status="draft") | Q(
                    tithers_count__isnull=False, tithes_total__isnull=False, total_amount__isnull=False,
                    breakdown_total__isnull=False, difference__isnull=False, finalized_at__isnull=False,
                ),
                name="tithes_finalized_has_frozen_values",
            ),
            models.CheckConstraint(
                condition=Q(status="draft") | Q(total_amount=F("offering_amount") + F("tithes_total")),
                name="tithes_total_is_offering_plus_tithes",
            ),
            models.CheckConstraint(
                condition=Q(status="draft") | Q(difference=F("breakdown_total") - F("total_amount")),
                name="tithes_difference_is_signed_gap",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(tithes_total__isnull=True) | Q(tithes_total__gte=0))
                    & (Q(breakdown_total__isnull=True) | Q(breakdown_total__gte=0))
                ),
                name="tithes_frozen_amounts_nonneg",
            ),
            # A direct bypass always carries its reason (the reverse is not enforced: `bypassed_by`
            # becomes NULL if that admin is purged, and the reason stays).
            models.CheckConstraint(
                condition=Q(bypassed_by__isnull=True) | ~Q(bypass_reason=""),
                name="tithes_bypass_has_reason",
            ),
        ]

    def __str__(self):
        return f"Tithes report for {self.attendance_id} ({self.status})"


class TitheEntry(models.Model):
    """One envelope: one amount, one giver (the name on the envelope), optional co-givers.

    The totals count an entry once, however many co-givers it has.
    """

    tithes_offering = models.ForeignKey(TithesOffering, on_delete=models.CASCADE, related_name="entries")
    # Only a `member_account` or `member_record` (never a guest: promote the guest first). That is a
    # cross-table rule, so the service enforces it; the database cannot see `person_type` from here.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="tithe_entries",
    )
    giver_name = models.CharField(max_length=255)   # snapshot, stays readable if the account is purged
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = "tithe_entry"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="tithe_amount_positive"),
            models.UniqueConstraint(fields=["tithes_offering", "user"], name="uq_tithe_entry_user"),
        ]

    def __str__(self):
        return f"{self.giver_name}: {self.amount}"


class TitheEntryGiver(models.Model):
    """A co-giver copied onto an entry (a snapshot, never a live link to a household).

    "A person appears at most once per report, as giver or as co-giver" spans two tables, so the service
    enforces it; the database only stops the same person twice on one entry.
    """

    entry = models.ForeignKey(TitheEntry, on_delete=models.CASCADE, related_name="co_givers")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="tithe_co_giver_entries",
    )
    name_snapshot = models.CharField(max_length=255)

    class Meta:
        db_table = "tithe_entry_giver"
        constraints = [
            models.UniqueConstraint(fields=["entry", "user"], name="uq_tithe_entry_giver_user"),
        ]

    def __str__(self):
        return self.name_snapshot
class TitheHousehold(TimestampedModel):
    """A standing group of people who usually give together (a couple, a family).

    It only PRE-FILLS the co-givers of a new entry. Not archivable, so it has no retention policy.
    Disbanding is soft: `disbanded_on` is set, every membership is ended, and the row stays for history.
    """

    extension = models.ForeignKey(
        "accounts.Extension", on_delete=models.RESTRICT, related_name="tithe_households",
    )
    name = models.CharField(max_length=150, blank=True)
    # NULL means the household is still active; a date means it was disbanded that day.
    disbanded_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        db_table = "tithe_household"

    def __str__(self):
        return self.name or f"Household {self.pk}"

class TitheHouseholdMember(models.Model):
    """A dated membership in a household. `ended_on` NULL means the person is in it now."""

    household = models.ForeignKey(TitheHousehold, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tithe_household_memberships",
    )
    started_on = models.DateField()
    ended_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "tithe_household_member"
        constraints = [
            # A person is in at most one household at a time (any number of ended memberships).
            models.UniqueConstraint(
                fields=["user"], condition=Q(ended_on__isnull=True),
                name="uq_tithe_household_member_active",
            ),
            models.CheckConstraint(
                condition=Q(ended_on__isnull=True) | Q(ended_on__gte=F("started_on")),
                name="tithe_household_member_dates_ordered",
            ),
        ]


class TitheBypassRequest(TimestampedModel):
    """A coordinator's request to finalize a report whose breakdown does not match the Total.

    Tied to the exact `difference` at the time of the request: if the difference changes, the request
    is voided. An approval is used once. The message and the decision note live ONLY here, never in
    `audit_log` or a notification.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        VOIDED = "voided", "Voided"
        USED = "used", "Used"

    #: The two states that count as "open": while one exists the draft is locked for non-admins.
    OPEN_STATUSES = (Status.PENDING, Status.APPROVED)

    tithes_offering = models.ForeignKey(TithesOffering, on_delete=models.CASCADE, related_name="bypass_requests")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    message = models.TextField()
    difference = models.DecimalField(max_digits=12, decimal_places=2)   # signed, as requested
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "tithes_bypass_request"
        constraints = [
            # At most one open (pending or approved) request per report.
            models.UniqueConstraint(
                fields=["tithes_offering"], condition=Q(status__in=["pending", "approved"]),
                name="uq_tithes_bypass_one_open",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["pending", "approved", "rejected", "withdrawn", "voided", "used"]),
                name="tithes_bypass_status_valid",
            ),
            # Nothing to bypass when the difference is zero.
            models.CheckConstraint(condition=~Q(difference=0), name="tithes_bypass_difference_nonzero"),
            models.CheckConstraint(condition=~Q(message=""), name="tithes_bypass_message_required"),
            # A decision has a time; a pending request has none. (`decided_by` may be NULL if purged.)
            models.CheckConstraint(
                condition=(
                    Q(status="pending", decided_at__isnull=True)
                    | Q(status__in=["approved", "rejected"], decided_at__isnull=False)
                    | Q(status__in=["withdrawn", "voided", "used"])
                ),
                name="tithes_bypass_decided_at_matches_status",
            ),
        ]

    def __str__(self):
        return f"Bypass request {self.pk} ({self.status})"
