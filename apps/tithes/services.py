"""tithes.services: everything done on a tithes report, its entries, households and bypass requests.

Same conventions as `attendance.services`: every function checks permission through `tithes.permissions`
(the only place that decides), does its work in `transaction.atomic()`, and reports a rule failure as
`ServiceError` (a message that is safe to show). A missing permission raises `PermissionDenied`.

Every action on a report locks the report row (and its sheet row) with `select_for_update()` and
re-checks permission on the LOCKED row, so a stale copy held by a slow second device can never win.
Actions that must not run twice (finalize, reopen, request a bypass, decide a request) take a one-time
form token and consume it INSIDE their transaction; a refusal rolls the token back with everything else.

AUDIT: only finalize (also for "no tithes & offering"), reopen and the four bypass actions are written to `audit_log`, with ids, counts and
a `balanced` flag. Entries, the offering, the breakdown and households are working data and are NOT audited.
PRIVACY: no tithe amount, bypass message, decision note or bypass reason ever reaches `audit_log` or
`notification.data`. The message and the note live only on the request row.

Dependency direction: `accounts` <- `attendance` <- `tithes`; `tithes` also uses `audit` and `notifications`.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils import timezone

from apps.accounts.models import Extension, PersonType, Role, Status, User
from apps.attendance.age import effective_age_network
from apps.attendance.models import Attendance, AttendanceFirstTimer, AttendanceMember
from apps.attendance.services import SEARCH_MAX_RESULTS, SEARCH_MIN_LENGTH
from apps.audit.services import log_action
from apps.core.services import ServiceError, consume_submission_token
from apps.core.text import clean_text, title_case
from apps.notifications.services import notify

from .models import (
    TitheBypassRequest, TitheEntry, TitheEntryGiver, TitheHousehold, TitheHouseholdMember, TithesOffering,
)
from .permissions import (
    can_create, can_decide_bypass, can_edit_draft, can_finalize, can_manage_households, can_mark_no_tithes,
    can_reopen, can_request_bypass, can_view, has_open_bypass_request, needs_finalize_approval, require,
)

#: Bills and coins on the paper form, largest first.
DENOMINATIONS = (1000, 500, 200, 100, 50, 20, 10, 5, 1)
_DENOMINATION_KEYS = {str(d) for d in DENOMINATIONS}
MAX_COUNT = 1_000_000                       # per denomination; stops a typo from overflowing Decimal(12,2)
CENT = Decimal("0.01")
MAX_MONEY = Decimal("9999999999.99")        # the largest value a Decimal(12,2) column holds
MESSAGE_MAX_LENGTH = 500
NOTE_MAX_LENGTH = 500
REASON_MAX_LENGTH = 500
NAME_MAX_LENGTH = 150
#: Stored as the message of a finalize request that needs no explanation (the numbers balance).
DEFAULT_BALANCED_MESSAGE = "The numbers are balanced. Ready to finalize."
DEFAULT_EMPTY_MESSAGE = "No tithes and offering were collected at this service."

#: Only these two person types can give a tithe (a guest is promoted first).
GIVER_TYPES = (PersonType.MEMBER_ACCOUNT, PersonType.MEMBER_RECORD)
ADMIN_ROLES = (Role.SUPER_ADMIN, Role.ADMIN)

ENTITY_TYPE = "tithes_offering"
#: Audit action names. The reopen one MUST equal the action of the existing "reopen tithes" `notification_rule`
#: (seeded in core migration 0002); `test_finalize.ReopenTests.test_reopen_action_has_an_alert_rule` checks it.
FINALIZE_ACTION = "tithes.finalize"
REOPEN_ACTION = "tithes.reopen"
REQUEST_ACTION = "tithes.bypass_request"
APPROVE_ACTION = "tithes.bypass_approve"
REJECT_ACTION = "tithes.bypass_reject"
WITHDRAW_ACTION = "tithes.bypass_withdraw"
#: Notification types (free strings in `notification.type`).
NOTIFY_REQUESTED = "tithes_bypass_requested"
NOTIFY_APPROVED = "tithes_bypass_approved"
NOTIFY_REJECTED = "tithes_bypass_rejected"


# ============================================================================ totals
@dataclass(frozen=True)
class Totals:
    """The numbers of one report. `difference` is SIGNED: breakdown_total - total (negative = cash is short)."""

    tithers: int
    tithes: Decimal
    offering: Decimal
    total: Decimal
    breakdown_total: Decimal
    difference: Decimal

    @property
    def balanced(self):
        """True when the bills and coins add up to the Total."""
        return self.difference == 0

    @property
    def is_empty(self):
        """True when there is nothing to finalize: no Total and no breakdown."""
        return self.total == 0 and self.breakdown_total == 0


def _breakdown_value(counts):
    """Money value of a `{denomination: count}` dict. Unknown keys and bad counts are ignored, never trusted."""
    total = Decimal(0)
    for key, count in (counts or {}).items():
        try:
            denomination, number = int(key), int(count)
        except (TypeError, ValueError):
            continue
        if denomination in DENOMINATIONS and number > 0:
            total += denomination * number
    return total.quantize(CENT)


def build_totals(report):
    """The live numbers of `report`: tithers, tithes, offering, total, breakdown total and difference.

    ONE function for the screen preview and for the freeze at finalize (like attendance `build_summary`),
    so what the person saw is what gets frozen. A tither is an ENTRY (one envelope), however many co-givers
    it has. Reads the entries from the database and the offering and breakdown from `report`, so pass a
    fresh row (the locked one inside a service).
    """
    found = TitheEntry.objects.filter(tithes_offering=report).aggregate(n=Count("pk"), s=Sum("amount"))
    tithes = Decimal(found["s"] or 0).quantize(CENT)
    offering = Decimal(report.offering_amount or 0).quantize(CENT)
    total = offering + tithes
    breakdown_total = _breakdown_value(report.denomination_breakdown)
    return Totals(
        tithers=found["n"], tithes=tithes, offering=offering, total=total,
        breakdown_total=breakdown_total, difference=breakdown_total - total,
    )


def is_no_tithes(report):
    """True for a FINALIZED report that recorded nothing: no tithers and a Total of 0 ("no tithes & offering").

    Needs no extra column: `finalize_no_tithes` freezes exactly these numbers, and `finalize_report` refuses
    an empty report, so a finalized report with a Total of 0 can only come from that button. Reads the frozen
    values (never a recount), so it is safe on a list page.
    """
    return (
        report.status == TithesOffering.Status.FINALIZED
        and not report.total_amount and not report.tithers_count
    )


def frozen_totals_for(report):
    """The numbers saved when `report` was finalized. Never recalculated: they are read back as stored."""
    if report.status != TithesOffering.Status.FINALIZED:
        raise ServiceError("This report is not finalized yet, so it has no frozen numbers.")
    return Totals(
        tithers=report.tithers_count, tithes=report.tithes_total, offering=report.offering_amount,
        total=report.total_amount, breakdown_total=report.breakdown_total, difference=report.difference,
    )


# ============================================================================ helpers
def _pk(value):
    """A model instance or an id, as an id."""
    return getattr(value, "pk", value)


def _save(instance, *names):
    """`save(update_fields=...)`, plus `updated_at` when the model has one (so auto_now still fires)."""
    fields = list(names)
    if any(f.name == "updated_at" for f in instance._meta.get_fields()):
        fields.append("updated_at")
    instance.save(update_fields=fields)


def _money(value, what, *, allow_zero):
    """`value` as a 2-decimal Decimal, or a ServiceError. Never a float: a float is read through its text."""
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ServiceError(f"Enter a valid {what}.")
    if not amount.is_finite():
        raise ServiceError(f"Enter a valid {what}.")
    if abs(amount) > MAX_MONEY:
        raise ServiceError(f"That {what} is too large.")
    if amount != amount.quantize(CENT):
        raise ServiceError(f"The {what} can have at most 2 decimal places.")
    if amount < 0 or (amount == 0 and not allow_zero):
        raise ServiceError(
            f"The {what} cannot be negative." if allow_zero else f"The {what} must be more than zero."
        )
    return amount.quantize(CENT)


def _count(value, denomination):
    """A bill or coin count as an int (blank means 0), or a ServiceError."""
    if isinstance(value, bool):
        raise ServiceError(f"Enter a whole number of {denomination} bills or coins.")
    text = str(value).strip() if value is not None else ""
    if text == "":
        return 0
    if not (text.isascii() and text.isdigit()) or int(text) > MAX_COUNT:
        raise ServiceError(f"Enter a whole number of {denomination} bills or coins (at most {MAX_COUNT:,}).")
    return int(text)


def _read_report(report_id):
    """The report with its sheet, WITHOUT a lock (read-only helpers)."""
    report = TithesOffering.objects.select_related("attendance").filter(pk=report_id).first()
    if report is None:
        raise ServiceError("This report no longer exists.")
    return report


def _lock_report(report_id):
    """Lock the report row AND its sheet row, then return the fresh report.

    The sheet row is locked too because "archived sheet" makes a report read-only (`archive_sheet` locks
    the sheet), so an archive that lands at the same moment either finishes first or waits for this.
    Call inside `transaction.atomic()`.
    """
    locked = (
        TithesOffering.objects.select_related("attendance")
        .select_for_update(of=("self", "attendance")).filter(pk=report_id).first()
    )
    if locked is None:
        raise ServiceError("This report no longer exists.")
    return locked


def _refuse_unless_draft(report):
    """Friendly refusal for a report that is finalized or whose sheet is archived (a draft passes)."""
    if report.status == TithesOffering.Status.FINALIZED:
        raise ServiceError("This report is finalized and locked. Only an admin can reopen it.")
    if report.attendance.status == Attendance.Status.ARCHIVED:
        raise ServiceError("This sheet is archived.")


def _check_editable(actor, report):
    """Raise unless `actor` may change the entries, offering and breakdown of this report right now.

    PermissionDenied for someone who may not open it; ServiceError (a friendly message) when they may but
    the report is finalized, archived or locked by an open bypass request (only an admin edits then).
    """
    require(can_view, actor, report)
    _refuse_unless_draft(report)
    if actor.role not in ADMIN_ROLES and has_open_bypass_request(report):
        raise ServiceError(
            "This report is locked while a bypass request is open. Withdraw the request, or ask an admin."
        )
    require(can_edit_draft, actor, report)


def _lock_draft(actor, report):
    """Lock the report, re-read it and check `actor` may edit it. Call inside `transaction.atomic()`."""
    locked = _lock_report(report.pk)
    _check_editable(actor, locked)
    return locked


def _void_stale_requests(report, totals):
    """Void an open bypass request whose stored difference no longer equals the live one.

    A request is tied to the exact difference it was filed for; any edit that changes the difference
    (only an admin can edit while one is open) makes it void, so an old approval can never cover new numbers.
    """
    stale = (
        report.bypass_requests.select_for_update()
        .filter(status__in=TitheBypassRequest.OPEN_STATUSES).exclude(difference=totals.difference)
    )
    for bypass_request in stale:
        bypass_request.status = TitheBypassRequest.Status.VOIDED
        _save(bypass_request, "status")


def _after_edit(report):
    """Run after every draft edit, inside the same transaction: void a request the edit made stale."""
    _void_stale_requests(report, build_totals(report))


def _log(action, actor, request, report, *, before=None, after=None):
    """Write one audit row about `report`. Callers pass ids, counts and flags only, never money or text."""
    log_action(
        action, actor=actor, request=request,
        entity_type=ENTITY_TYPE, entity_id=report.pk, entity_label=str(report.attendance),
        extension_id=report.attendance.extension_id, before=before, after=after,
    )


def _report_url(report):
    """Relative link used by notifications. Update it here if the tithes screens use another path."""
    return f"/tithes/{report.pk}/"


def _giver_row(person, on_date):
    """The few fields a screen may show about a person: id, name, where they belong, age network.

    Never the birth date, phone number or address. Same shape as attendance `search_people`.
    """
    network = effective_age_network(person, on_date)
    return {
        "id": person.pk,
        "name": person.full_name,
        "home_extension": title_case(person.extension.name) if person.extension_id else "-",
        "age_network": network.value if network else None,
    }


def _people_on_report(report, *, exclude_entry_id=None):
    """Ids of the people already on `report`, as giver or as co-giver (a person appears at most once)."""
    entries = TitheEntry.objects.filter(tithes_offering=report, user__isnull=False)
    co_givers = TitheEntryGiver.objects.filter(entry__tithes_offering=report, user__isnull=False)
    if exclude_entry_id is not None:
        entries = entries.exclude(pk=exclude_entry_id)
        co_givers = co_givers.exclude(entry_id=exclude_entry_id)
    return set(entries.values_list("user_id", flat=True)) | set(co_givers.values_list("user_id", flat=True))


def _eligible_giver(person_id, taken):
    """The person with this id if they may give a tithe on this report, else a ServiceError.

    Needs an ACTIVE `member_account` or `member_record` who is not already in `taken`. A guest is refused
    with a hint to promote them first.
    """
    person = User.objects.select_related("extension").filter(pk=person_id).first()
    if person is None:
        raise ServiceError("That person no longer exists.")
    if person.person_type == PersonType.GUEST:
        raise ServiceError(f"{person.full_name} is a guest. Promote them to a member before recording a tithe.")
    if person.person_type not in GIVER_TYPES or person.status != Status.ACTIVE:
        raise ServiceError(f"{person.full_name} cannot give a tithe (only active members can).")
    if person.pk in taken:
        raise ServiceError(f"{person.full_name} is already on this report.")
    return person


# ============================================================================ the report
def create_report(actor, sheet):
    """Start the tithes report for an attendance sheet and return it. One report per sheet; no audit row.

    The sheet row is locked, so two clicks cannot both create it. An archived sheet is refused. Allowed for
    a tithes special member of the sheet's extension, its coordinator, or an admin (`can_create`).
    """
    with transaction.atomic():
        locked = Attendance.objects.select_for_update().filter(pk=_pk(sheet)).first()
        if locked is None:
            raise ServiceError("This attendance sheet no longer exists.")
        if locked.status == Attendance.Status.ARCHIVED:
            raise ServiceError("This sheet is archived, so it cannot get a tithes report.")
        require(can_create, actor, locked)
        if TithesOffering.objects.filter(attendance=locked).exists():
            raise ServiceError("This sheet already has a tithes report.")
        report = TithesOffering.objects.create(attendance=locked, created_by=actor)
    return report


def set_offering(actor, report, amount):
    """Set the offering (one unnamed number, zero or more). Returns the report. Not audited (draft data)."""
    with transaction.atomic():
        locked = _lock_draft(actor, report)
        locked.offering_amount = _money(amount, "offering", allow_zero=True)
        locked.save(update_fields=["offering_amount"])
        _after_edit(locked)
    return locked


def set_breakdown(actor, report, counts):
    """Set the bills-and-coins counts, `{denomination: count}`. Returns the report. Not audited.

    A missing denomination counts as 0, an unknown one is refused. Stored as all nine denominations with
    text keys, so the JSON always has the same shape.
    """
    with transaction.atomic():
        locked = _lock_draft(actor, report)
        if not isinstance(counts, dict):
            raise ServiceError("Enter how many bills and coins of each kind.")
        stored = {str(d): 0 for d in DENOMINATIONS}
        for key, value in counts.items():
            if str(key) not in _DENOMINATION_KEYS:
                raise ServiceError(f"{key} is not one of the bills or coins on the form.")
            stored[str(key)] = _count(value, key)
        locked.denomination_breakdown = stored
        locked.save(update_fields=["denomination_breakdown"])
        _after_edit(locked)
    return locked


# ============================================================================ entries
def add_entry(actor, report, giver, amount, co_givers=()):
    """Add one envelope: a giver, an amount and optional co-givers. Returns the `TitheEntry`.

    The giver and every co-giver must be an active `member_account` or `member_record` (never a guest), and
    nobody may already be on the report (as giver or co-giver). The giver need NOT be present on the sheet.
    Co-givers are COPIED onto the entry with their names as of now (a snapshot), so a later household change
    or rename never rewrites it. Not audited (working data, and it holds amounts).
    """
    with transaction.atomic():
        locked = _lock_draft(actor, report)
        amount = _money(amount, "tithe amount", allow_zero=False)
        giver_id = _pk(giver)
        co_ids = list(dict.fromkeys(_pk(c) for c in co_givers))
        if giver_id in co_ids:
            raise ServiceError("The giver cannot also be a co-giver.")
        taken = _people_on_report(locked)
        person = _eligible_giver(giver_id, taken)
        co_people = [_eligible_giver(pid, taken) for pid in co_ids]
        entry = TitheEntry.objects.create(
            tithes_offering=locked, user=person, giver_name=person.full_name, amount=amount,
        )
        TitheEntryGiver.objects.bulk_create([
            TitheEntryGiver(entry=entry, user=p, name_snapshot=p.full_name) for p in co_people
        ])
        _after_edit(locked)
    return entry


def update_entry(actor, report, entry_id, *, amount=None, giver=None, co_givers=None):
    """Change one entry; `None` leaves that part as it is. Returns the entry. Not audited.

    `co_givers` is the WHOLE new list (an empty list clears it). Co-givers who stay keep their stored
    snapshot (a rename does not rewrite them); only people who are new are checked and snapshotted.
    """
    with transaction.atomic():
        locked = _lock_draft(actor, report)
        entry = TitheEntry.objects.filter(pk=entry_id, tithes_offering=locked).first()
        if entry is None:
            raise ServiceError("That entry is no longer on this report.")
        if amount is None and giver is None and co_givers is None:
            raise ServiceError("There is nothing to change.")
        fields = []
        if amount is not None:
            entry.amount = _money(amount, "tithe amount", allow_zero=False)
            fields.append("amount")
        taken = _people_on_report(locked, exclude_entry_id=entry.pk)
        current = {g.user_id: g for g in entry.co_givers.all() if g.user_id}
        giver_id = entry.user_id if giver is None else _pk(giver)
        co_ids = list(current) if co_givers is None else list(dict.fromkeys(_pk(c) for c in co_givers))
        if giver_id is not None and giver_id in co_ids:
            raise ServiceError("The giver cannot also be a co-giver.")
        if giver_id != entry.user_id:
            person = _eligible_giver(giver_id, taken)
            entry.user, entry.giver_name = person, person.full_name
            fields += ["user", "giver_name"]
        new_people = [_eligible_giver(pid, taken) for pid in co_ids if pid not in current]
        if fields:
            entry.save(update_fields=fields)
        if co_givers is not None:
            TitheEntryGiver.objects.filter(entry=entry).exclude(user_id__in=co_ids).delete()
            TitheEntryGiver.objects.bulk_create([
                TitheEntryGiver(entry=entry, user=p, name_snapshot=p.full_name) for p in new_people
            ])
        _after_edit(locked)
    return entry


def remove_entry(actor, report, entry_id):
    """Take an entry (and its co-givers) off the draft. Returns True if removed, False if it was not there."""
    with transaction.atomic():
        locked = _lock_draft(actor, report)
        removed, _ = TitheEntry.objects.filter(pk=entry_id, tithes_offering=locked).delete()
        _after_edit(locked)
    return bool(removed)


# ============================================================================ finding givers
def search_givers(actor, report, term):
    """Members matching `term`, for the giver and co-giver pickers (any extension: a visitor may give).

    DELIBERATE EXCEPTION to `for_user`, like attendance `search_people`: it needs the right to edit this
    draft, asks for at least 3 characters (fewer returns an empty list), returns at most 20 people, and gives
    back only id, name, home extension and age network, never a birth date, phone number or address.
    Restricted to active `member_account` and `member_record` people (a guest cannot give). Every word typed
    must match the first, middle or last name.
    """
    current = _read_report(_pk(report))
    _check_editable(actor, current)
    words = (term or "").split()
    if len(" ".join(words)) < SEARCH_MIN_LENGTH:
        return []
    people = User.objects.filter(status=Status.ACTIVE, person_type__in=GIVER_TYPES).select_related("extension")
    for word in words:
        word = clean_text(word)
        people = people.filter(
            Q(first_name__icontains=word) | Q(middle_name__icontains=word) | Q(last_name__icontains=word)
        )
    people = people.order_by("last_name", "first_name", "pk")[:SEARCH_MAX_RESULTS]
    return [_giver_row(person, current.attendance.attendance_date) for person in people]


def present_candidates(report):
    """Members ticked present on the report's sheet who are not on the report yet, to offer first.

    Includes first timers already promoted to a member record. A giver does NOT have to be present (the
    screen only badges "not on today's sheet"). Same four fields as `search_givers`. No permission check of
    its own: only a screen that already opened the report through `for_user` calls it.
    """
    sheet = report.attendance
    ids = list(
        AttendanceMember.objects.filter(attendance=sheet, user__isnull=False).values_list("user_id", flat=True)
    ) + list(
        AttendanceFirstTimer.objects.filter(attendance=sheet, converted_user__isnull=False)
        .values_list("converted_user_id", flat=True)
    )
    people = (
        User.objects.filter(pk__in=ids, status=Status.ACTIVE, person_type__in=GIVER_TYPES)
        .exclude(pk__in=_people_on_report(report)).select_related("extension")
        .order_by("last_name", "first_name", "pk")
    )
    return [_giver_row(person, sheet.attendance_date) for person in people]


# ============================================================================ households
def create_household(actor, extension, *, name=""):
    """Create a household in `extension` and return it. Coordinator of that extension, or admin. Not audited."""
    require(can_manage_households, actor, _pk(extension))
    name = " ".join((name or "").split())
    if len(name) > NAME_MAX_LENGTH:
        raise ServiceError(f"The name can have at most {NAME_MAX_LENGTH} characters.")
    with transaction.atomic():
        locked = Extension.objects.select_for_update().filter(pk=_pk(extension)).first()
        if locked is None or locked.archived_at is not None:
            raise ServiceError("That extension no longer exists.")
        household = TitheHousehold.objects.create(extension=locked, name=name, created_by=actor)
    return household


def add_household_member(actor, household, user, *, started_on=None):
    """Put a person in a household from `started_on` (default today). Returns the membership.

    The person must be an active `member_account` or `member_record` OF THIS HOUSEHOLD'S EXTENSION, and in
    no other household right now (end that membership first).
    """
    started_on = started_on or timezone.localdate()
    if not isinstance(started_on, date):
        raise ServiceError("Enter the date the person joined.")
    with transaction.atomic():
        locked = (
            TitheHousehold.objects.select_related("extension").select_for_update(of=("self",))
            .filter(pk=_pk(household)).first()
        )
        if locked is None:
            raise ServiceError("This household no longer exists.")
        require(can_manage_households, actor, locked.extension_id)
        person = User.objects.filter(pk=_pk(user)).first()
        if (
            person is None or person.person_type not in GIVER_TYPES or person.status != Status.ACTIVE
            or person.extension_id != locked.extension_id
        ):
            raise ServiceError(
                f"Only active members of {title_case(locked.extension.name)} can join this household."
            )
        active = TitheHouseholdMember.objects.filter(user=person, ended_on__isnull=True).first()
        if active is not None:
            raise ServiceError(
                f"{person.full_name} is already in this household." if active.household_id == locked.pk
                else f"{person.full_name} is already in another household. End that one first."
            )
        try:
            with transaction.atomic():  # a savepoint: the partial unique index is the last line of defence
                membership = TitheHouseholdMember.objects.create(
                    household=locked, user=person, started_on=started_on,
                )
        except IntegrityError:
            raise ServiceError(f"{person.full_name} is already in a household.")
    return membership


def end_household_member(actor, membership_id, *, ended_on=None):
    """End a membership on `ended_on` (default today) and return it. Past entries are never touched."""
    ended_on = ended_on or timezone.localdate()
    if not isinstance(ended_on, date):
        raise ServiceError("Enter the date the person left.")
    with transaction.atomic():
        membership = (
            TitheHouseholdMember.objects.select_related("household").select_for_update(of=("self",))
            .filter(pk=membership_id).first()
        )
        if membership is None:
            raise ServiceError("That household member no longer exists.")
        require(can_manage_households, actor, membership.household.extension_id)
        if membership.ended_on is not None:
            raise ServiceError("This person has already left the household.")
        if ended_on < membership.started_on:
            raise ServiceError("The end date cannot be before the start date.")
        membership.ended_on = ended_on
        membership.save(update_fields=["ended_on"])
    return membership


def household_for(user):
    """The household `user` is in right now, or None. No permission check: a screen calls it after its own."""
    return TitheHousehold.objects.filter(
        members__user=_pk(user), members__ended_on__isnull=True,
    ).select_related("extension").first()


def household_prefill(user):
    """The other CURRENT members of `user`'s household, to pre-fill the co-givers of a new entry.

    Only people who could still be co-givers (active `member_account` or `member_record`). The screen lets
    the person untick them; saving copies whoever is left onto the entry. Rows hold id and name only.
    """
    household = household_for(user)
    if household is None:
        return []
    others = (
        User.objects.filter(
            tithe_household_memberships__household=household, tithe_household_memberships__ended_on__isnull=True,
            status=Status.ACTIVE, person_type__in=GIVER_TYPES,
        ).exclude(pk=_pk(user)).order_by("last_name", "first_name", "pk")
    )
    return [{"id": person.pk, "name": person.full_name} for person in others]


# ============================================================================ finalize and reopen
@dataclass
class FinalizeResult:
    """What `finalize_report` did. `duplicate` is True when this form submission was already processed."""

    report: TithesOffering = None
    duplicate: bool = False


def _mismatch_message(report, totals, actor):
    """The refusal for a report whose breakdown does not match the Total: how much, and what to do next."""
    gap = f"\u20b1{abs(totals.difference):,.2f}"
    how = (
        f"The bills and coins are {gap} short of the Total."
        if totals.difference < 0 else f"The bills and coins are {gap} over the Total."
    )
    if report.bypass_requests.filter(status=TitheBypassRequest.Status.PENDING).exists():
        return f"{how} A bypass request on this report is still waiting for a decision."
    return f"{how} Fix the numbers, or give a reason to finalize anyway."


def _approval_message(report, totals):
    """The refusal for a coordinator who tries to finalize without an approved request."""
    if report.bypass_requests.filter(status=TitheBypassRequest.Status.PENDING).exists():
        return "Your finalize request is still waiting for an admin's decision."
    text = "An admin must approve before you can finalize. Send a finalize request first."
    if not totals.balanced:
        gap = f"\u20b1{abs(totals.difference):,.2f}"
        text = f"The bills and coins are {gap} {'short of' if totals.difference < 0 else 'over'} the Total. {text}"
    return text


def _refuse_while_request_pending(actor, report):
    """Stop an admin from finalizing on their own while a coordinator's request is waiting.

    The admin decides the request instead: approving it finalizes the report (`decide_bypass`). This keeps the
    server in step with the review page, which hides the admin's Finalize button while a request is pending.
    A coordinator is never affected (they need an approval anyway).
    """
    if needs_finalize_approval(actor):
        return
    if report.bypass_requests.filter(status=TitheBypassRequest.Status.PENDING).exists():
        raise ServiceError(
            "A coordinator's finalize request is waiting on this report. "
            "Approve or reject it; approving finalizes the report."
        )


def _freeze_report(locked, actor, request, totals, *, approved=None, direct_reason=""):
    """Write the frozen numbers of a reviewed, NON-empty draft. Call inside `transaction.atomic()` on the LOCKED draft.

    Shared by `finalize_report` (a direct finalize, or a coordinator using an approved request) and by
    `decide_bypass` (an admin approves a request, which finalizes at once). The caller has already checked
    permission, that the report is a draft, and the approval. `approved` is the request being used: it becomes
    `used`. `direct_reason` is an admin's typed reason for a mismatch (stored with `bypassed_by`). Any other open
    request is voided. Logs `tithes.finalize` with the tither count and a `balanced` flag only (no amounts).
    """
    locked.tithers_count = totals.tithers
    locked.tithes_total = totals.tithes
    locked.total_amount = totals.total
    locked.breakdown_total = totals.breakdown_total
    locked.difference = totals.difference
    locked.finalized_by = actor
    locked.finalized_at = timezone.now()
    locked.status = TithesOffering.Status.FINALIZED
    fields = [
        "tithers_count", "tithes_total", "total_amount", "breakdown_total", "difference",
        "finalized_by", "finalized_at", "status",
    ]
    if direct_reason:
        locked.bypass_reason, locked.bypassed_by = direct_reason, actor
        fields += ["bypass_reason", "bypassed_by"]
    locked.save(update_fields=fields)

    if approved is not None:
        approved.status = TitheBypassRequest.Status.USED
        _save(approved, "status")
    # Anything still open would lock the draft again after a reopen, so close it now.
    for leftover in locked.bypass_requests.select_for_update().filter(
            status__in=TitheBypassRequest.OPEN_STATUSES):
        leftover.status = TitheBypassRequest.Status.VOIDED
        _save(leftover, "status")

    _log(
        FINALIZE_ACTION, actor, request, locked,
        before={"status": TithesOffering.Status.DRAFT},
        after={
            "status": TithesOffering.Status.FINALIZED,
            "tithers_count": totals.tithers,
            "balanced": totals.balanced,
        },
    )


def finalize_report(actor, report_id, *, token, bypass_reason="", request=None):
    """Freeze a reviewed draft. Returns a `FinalizeResult` with the `report` and `duplicate`.

    Allowed for the coordinator of the sheet's extension or an admin (`can_finalize`). In practice a coordinator
    now finalizes through `decide_bypass` (an admin's approval finalizes at once); the approved-request path below
    stays for a request that was approved before that change. An admin cannot finalize here while a coordinator's
    request is PENDING (`_refuse_while_request_pending`): they decide it instead. ONE transaction, in
    this order, so a refusal leaves the report untouched (and gives the token back):
      1. consume the form's one-time `token` (a replay returns `duplicate=True` and does nothing);
      2. lock the report row; require a draft on a sheet that is not archived, and `can_finalize`;
      3. refuse an empty report (Total and breakdown both 0; that is "No tithes & offering"), then `build_totals`;
      4. approval: a coordinator ALWAYS needs an APPROVED finalize request whose stored difference equals the live
         one (balanced or not; it becomes `used`: an approval works once). An admin or super-admin needs nothing
         when the report balances; for a mismatch they need that approval OR a non-empty `bypass_reason` (stored
         with `bypassed_by`). Otherwise refuse. A reason typed by anyone else, or on a balanced report, is ignored;
      5. write the frozen numbers, `finalized_by`, `finalized_at` and the status; any other open request dies;
      6. log `tithes.finalize` with the tither count and a `balanced` flag only (no amounts, no reason).
    """
    with transaction.atomic():
        if not consume_submission_token(token):
            return FinalizeResult(report=TithesOffering.objects.filter(pk=report_id).first(), duplicate=True)
        locked = _lock_report(report_id)
        require(can_view, actor, locked)
        _refuse_unless_draft(locked)
        require(can_finalize, actor, locked)
        _refuse_while_request_pending(actor, locked)

        totals = build_totals(locked)
        if totals.is_empty:
            raise ServiceError(
                "There is nothing to finalize yet. Enter the offering and the tithes first, "
                "or use \"No tithes & offering\" if nothing was collected."
            )
        if totals.total > MAX_MONEY:
            raise ServiceError("The Total is too large to save.")

        approved, direct_reason = None, ""
        if needs_finalize_approval(actor) or not totals.balanced:
            approved = (
                locked.bypass_requests.select_for_update()
                .filter(status=TitheBypassRequest.Status.APPROVED, difference=totals.difference).first()
            )
            if approved is None:
                if needs_finalize_approval(actor):
                    raise ServiceError(_approval_message(locked, totals))
                reason = (bypass_reason or "").strip()
                if not reason:
                    raise ServiceError(_mismatch_message(locked, totals, actor))
                if len(reason) > REASON_MAX_LENGTH:
                    raise ServiceError(f"The reason can have at most {REASON_MAX_LENGTH} characters.")
                direct_reason = reason

        _freeze_report(locked, actor, request, totals, approved=approved, direct_reason=direct_reason)
    return FinalizeResult(report=locked)


def _freeze_no_tithes(locked, actor, request, *, approved=None):
    """Write the frozen zeros of a "no tithes & offering" report. Call inside `transaction.atomic()` on the LOCKED draft.

    Shared by `finalize_no_tithes` (an admin presses the button, or a coordinator uses an approved request) and by
    `decide_bypass` (an admin approves a no-tithes request, which finalizes at once). The caller has already checked
    permission, that the report is an empty draft, and any approval. `approved` is the request being used: it becomes
    `used`. Any other open request is voided. Logs `tithes.finalize` with `no_tithes: True` (no amounts).
    """
    zero = Decimal(0).quantize(CENT)
    locked.offering_amount = zero
    locked.tithers_count = 0
    locked.tithes_total = locked.total_amount = locked.breakdown_total = locked.difference = zero
    locked.finalized_by = actor
    locked.finalized_at = timezone.now()
    locked.status = TithesOffering.Status.FINALIZED
    locked.save(update_fields=[
        "offering_amount", "tithers_count", "tithes_total", "total_amount", "breakdown_total",
        "difference", "finalized_by", "finalized_at", "status",
    ])
    if approved is not None:
        approved.status = TitheBypassRequest.Status.USED
        _save(approved, "status")
    for leftover in locked.bypass_requests.select_for_update().filter(status__in=TitheBypassRequest.OPEN_STATUSES):
        leftover.status = TitheBypassRequest.Status.VOIDED
        _save(leftover, "status")
    _log(
        FINALIZE_ACTION, actor, request, locked,
        before={"status": TithesOffering.Status.DRAFT},
        after={"status": TithesOffering.Status.FINALIZED, "tithers_count": 0, "balanced": True, "no_tithes": True},
    )


def finalize_no_tithes(actor, sheet, *, request=None):
    """Close a service where nothing was collected: a finalized report with no tithers and a Total of 0.

    `sheet` is the attendance sheet (or its id). Works whether or not the sheet already has a report: when
    there is none it is created here, in the same transaction, so a refusal leaves no half-made report.
    An admin or super-admin closes a sheet on their own. A coordinator (`needs_finalize_approval`) needs an APPROVED
    finalize request on an empty report, so they start the report first and ask; a sheet with no report cannot be
    closed by a coordinator in one step. Never a special member and never on an archived sheet.

    ONE transaction: (1) find or create the report; (2) lock the report row and its sheet; require a draft;
    (3) refuse when anything was already entered (an offering, a tithe or bills and coins), so the button can
    never hide real money; (3b) a coordinator needs the approved request, which becomes `used`; (4) freeze zeros
    (tithers 0, every amount 0, difference 0), `finalized_by` and `finalized_at`; void any stale open request; (5) log `tithes.finalize` with `no_tithes: True` (no amounts).
    A second press finds a finalized report and gets the usual "finalized and locked" refusal, so it needs no
    one-time token. An admin can reopen the report like any other finalized report. Returns the report.
    """
    sheet_id = _pk(sheet)
    with transaction.atomic():
        report = TithesOffering.objects.filter(attendance_id=sheet_id).first()
        if report is None:
            # Same order as `create_report`: lock the sheet, re-check, then create.
            locked_sheet = Attendance.objects.select_for_update().filter(pk=sheet_id).first()
            if locked_sheet is None:
                raise ServiceError("This attendance sheet no longer exists.")
            if locked_sheet.status == Attendance.Status.ARCHIVED:
                raise ServiceError("This sheet is archived, so it cannot get a tithes report.")
            require(can_mark_no_tithes, actor, locked_sheet)
            if needs_finalize_approval(actor):
                raise ServiceError(
                    "Start the report first, then send a finalize request. "
                    "An admin must approve before it can be closed as no tithes and offering."
                )
            report = (
                TithesOffering.objects.filter(attendance=locked_sheet).first()
                or TithesOffering.objects.create(attendance=locked_sheet, created_by=actor)
            )
        locked = _lock_report(report.pk)
        require(can_view, actor, locked)
        _refuse_unless_draft(locked)
        require(can_finalize, actor, locked)
        _refuse_while_request_pending(actor, locked)

        totals = build_totals(locked)
        if not totals.is_empty:
            raise ServiceError(
                "This report already has an offering, tithes or bills and coins. "
                "Remove them first, or finalize the report normally."
            )
        approved = None
        if needs_finalize_approval(actor):
            approved = (
                locked.bypass_requests.select_for_update()
                .filter(status=TitheBypassRequest.Status.APPROVED, difference=totals.difference).first()
            )
            if approved is None:
                raise ServiceError(_approval_message(locked, totals))

        _freeze_no_tithes(locked, actor, request, approved=approved)
    return locked


@dataclass
class ReopenResult:
    """What `reopen_report` did. `duplicate` is True when this form submission was already processed."""

    report: TithesOffering = None
    duplicate: bool = False


def reopen_report(actor, report_id, *, token, request=None):
    """Send a finalized report back to draft so it can be corrected. Returns a `ReopenResult`.

    Admin or super-admin only (`can_reopen`), on a finalized report whose sheet is not archived. ONE
    transaction: consume the token (a replay returns `duplicate=True`); lock the row and require it finalized;
    clear every frozen field and set the status back to draft (entries, offering and breakdown stay);
    log with the old tither count in `before`. No amounts. The audit action is the one the existing
    "reopen tithes" `notification_rule` listens for, so no new rule is needed.
    """
    with transaction.atomic():
        if not consume_submission_token(token):
            return ReopenResult(report=TithesOffering.objects.filter(pk=report_id).first(), duplicate=True)
        locked = _lock_report(report_id)
        require(can_view, actor, locked)
        if locked.status != TithesOffering.Status.FINALIZED:
            raise ServiceError("This report is still a draft, so there is nothing to reopen.")
        if locked.attendance.status == Attendance.Status.ARCHIVED:
            raise ServiceError("This sheet is archived.")
        require(can_reopen, actor, locked)

        old_tithers = locked.tithers_count
        locked.status = TithesOffering.Status.DRAFT
        locked.tithers_count = locked.tithes_total = locked.total_amount = None
        locked.breakdown_total = locked.difference = None
        locked.finalized_by = locked.finalized_at = locked.bypassed_by = None
        locked.bypass_reason = ""
        locked.save(update_fields=[
            "status", "tithers_count", "tithes_total", "total_amount", "breakdown_total", "difference",
            "finalized_by", "finalized_at", "bypassed_by", "bypass_reason",
        ])
        _log(
            REOPEN_ACTION, actor, request, locked,
            before={"status": TithesOffering.Status.FINALIZED, "tithers_count": old_tithers},
            after={"status": TithesOffering.Status.DRAFT},
        )
    return ReopenResult(report=locked)


# ============================================================================ bypass requests
@dataclass
class BypassRequestResult:
    """What `request_bypass` did. `duplicate` is True when this form submission was already processed."""

    bypass_request: TitheBypassRequest = None
    duplicate: bool = False


@dataclass
class BypassDecisionResult:
    """What `decide_bypass` did.

    `voided` is True when the difference had changed since the request was filed: the request was closed as
    voided instead of decided (a result, not an error, so the voiding is kept). `duplicate` is a replay.
    `finalized` is True when the approval also finalized the report (every approval does).
    """

    bypass_request: TitheBypassRequest = None
    duplicate: bool = False
    voided: bool = False
    finalized: bool = False


def _lock_request(request_id):
    """Lock the report row first, then the request row (the same order everywhere). Returns both."""
    report_id = (
        TitheBypassRequest.objects.filter(pk=request_id).values_list("tithes_offering_id", flat=True).first()
    )
    if report_id is None:
        raise ServiceError("That finalize request no longer exists.")
    report = _lock_report(report_id)
    bypass_request = TitheBypassRequest.objects.select_for_update().filter(pk=request_id).first()
    if bypass_request is None:
        raise ServiceError("That finalize request no longer exists.")
    return report, bypass_request


def _tell(user_ids, kind, actor, report, bypass_request, push):
    """Create one generic bell item (and push) per person. It says THAT something happened, never what."""
    for user_id in user_ids:
        notify(
            user_id, kind, actor=actor, entity_type=ENTITY_TYPE, entity_id=report.pk,
            data={"bypass_request_id": bypass_request.pk},
            dedupe_key=f"{kind}:{bypass_request.pk}", push=push,
        )


def _file_request(locked, actor, message, request):
    """Create a finalize request on a LOCKED draft and tell the admins. Call inside `transaction.atomic()`.

    The caller has already checked permission (`can_request_bypass`) and that the report is an open draft. Here: the
    message is required only for a mismatch (a balanced report, or an empty one, gets a default text when blank), at
    most 500 characters; one open request at a time; the request is tied to the difference as it is now (0 =
    balanced). Logs `tithes.bypass_request` with ids only. The admins' notice says THAT a request waits, never what.
    """
    totals = build_totals(locked)
    message = (message or "").strip()
    if not message:
        if not totals.balanced:
            raise ServiceError("Write a message to explain the difference.")
        message = DEFAULT_EMPTY_MESSAGE if totals.is_empty else DEFAULT_BALANCED_MESSAGE
    if len(message) > MESSAGE_MAX_LENGTH:
        raise ServiceError(f"The message can have at most {MESSAGE_MAX_LENGTH} characters.")
    if has_open_bypass_request(locked):
        raise ServiceError("This report already has an open finalize request.")

    bypass_request = TitheBypassRequest.objects.create(
        tithes_offering=locked, requested_by=actor, message=message, difference=totals.difference,
    )
    _log(
        REQUEST_ACTION, actor, request, locked,
        after={"bypass_request_id": bypass_request.pk, "status": bypass_request.status},
    )
    admins = [u.pk for u in User.objects.filter(role__in=ADMIN_ROLES).exclude(pk=actor.pk) if u.can_sign_in]
    _tell(
        admins, NOTIFY_REQUESTED, actor, locked, bypass_request,
        {
            "title": "A tithes report needs a decision",
            "body": (
                "A no tithes and offering request is waiting for review." if totals.is_empty
                else "A finalize request is waiting for review."
            ),
            "url": _report_url(locked),
        },
    )
    return bypass_request


def request_bypass(actor, report_id, *, message, token, request=None):
    """The coordinator asks an admin to approve finalizing this report. Returns a `BypassRequestResult`.

    (Called a "finalize request" on screen; "bypass" is the historical name of the table and the audit actions.)
    Coordinator of the sheet's extension only (`can_request_bypass`), on a draft with no open request, whether the
    numbers balance or not; see `_file_request` for the message and difference rules. When the report is EMPTY this
    is a "no tithes" request: the admin's approval finalizes it at once (`decide_bypass`).
    """
    with transaction.atomic():
        if not consume_submission_token(token):
            existing = TitheBypassRequest.objects.filter(
                tithes_offering_id=report_id, requested_by=actor,
            ).order_by("-pk").first()
            return BypassRequestResult(bypass_request=existing, duplicate=True)
        locked = _lock_report(report_id)
        require(can_view, actor, locked)
        _refuse_unless_draft(locked)
        require(can_request_bypass, actor, locked)
        bypass_request = _file_request(locked, actor, message, request)
    return BypassRequestResult(bypass_request=bypass_request)


def request_no_tithes(actor, sheet, *, request=None):
    """The coordinator asks, in ONE step, to close a sheet as "no tithes and offering". Returns the request.

    Works whether or not the sheet already has a report: when there is none it is created here, in the same
    transaction (so a refusal leaves no half-made report). Coordinator of the sheet's extension only
    (`can_create` and `can_request_bypass`); an admin uses `finalize_no_tithes` instead. Refused when anything is
    already entered on the report (an offering, a tithe or bills and coins). The admin's approval finalizes the report
    at once. No one-time token: a second press finds the open request and is refused with a friendly message.
    """
    sheet_id = _pk(sheet)
    with transaction.atomic():
        report = TithesOffering.objects.filter(attendance_id=sheet_id).first()
        if report is None:
            locked_sheet = Attendance.objects.select_for_update().filter(pk=sheet_id).first()
            if locked_sheet is None:
                raise ServiceError("This attendance sheet no longer exists.")
            if locked_sheet.status == Attendance.Status.ARCHIVED:
                raise ServiceError("This sheet is archived, so it cannot get a tithes report.")
            require(can_create, actor, locked_sheet)
            report = (
                TithesOffering.objects.filter(attendance=locked_sheet).first()
                or TithesOffering.objects.create(attendance=locked_sheet, created_by=actor)
            )
        locked = _lock_report(report.pk)
        require(can_view, actor, locked)
        _refuse_unless_draft(locked)
        require(can_request_bypass, actor, locked)
        if not build_totals(locked).is_empty:
            raise ServiceError(
                "This report already has an offering, tithes or bills and coins, so it is not a no tithes report."
            )
        bypass_request = _file_request(locked, actor, "", request)
    return bypass_request


def withdraw_bypass(actor, request_id, *, request=None):
    """The coordinator takes back an open (pending or approved) request, which unlocks the draft. Returns it.

    Same right as filing one (`can_request_bypass`). Logs `tithes.bypass_withdraw` with ids only. Repeating it
    on a request that is no longer open is a friendly ServiceError, so a double tap is harmless.
    """
    with transaction.atomic():
        report, bypass_request = _lock_request(request_id)
        require(can_view, actor, report)
        _refuse_unless_draft(report)
        if bypass_request.status not in TitheBypassRequest.OPEN_STATUSES:
            raise ServiceError("This finalize request is no longer open.")
        require(can_request_bypass, actor, report)
        bypass_request.status = TitheBypassRequest.Status.WITHDRAWN
        _save(bypass_request, "status")
        _log(
            WITHDRAW_ACTION, actor, request, report,
            after={"bypass_request_id": bypass_request.pk, "status": bypass_request.status},
        )
    return bypass_request


def decide_bypass(actor, request_id, *, approve, note, token, request=None):
    """An admin or super-admin approves or rejects a PENDING request. Returns a `BypassDecisionResult`.

    Consumes the one-time token first (a replay returns `duplicate=True`). Re-checks that the report's
    difference still equals the one the request was filed for: if not, the request is voided and the result
    says `voided=True` (nothing is raised, or the voiding would roll back). The optional `note` (at most 500
    characters) lives only on the request row. Logs `tithes.bypass_approve` or `tithes.bypass_reject` with ids
    only, and tells the requester (never the actor) with a generic notice.

    APPROVING a request ALWAYS finalizes the report in the same transaction, balanced or not: an empty report is
    frozen with zeros (`_freeze_no_tithes`), any other with its live numbers (`_freeze_report`). `finalized_by` is
    the admin who approved, the request becomes `used`, and `finalized=True` is returned. The coordinator does not
    press Finalize any more. If the report cannot be frozen (a Total too large to save) nothing is decided.
    """
    with transaction.atomic():
        if not consume_submission_token(token):
            return BypassDecisionResult(
                bypass_request=TitheBypassRequest.objects.filter(pk=request_id).first(), duplicate=True,
            )
        report, bypass_request = _lock_request(request_id)
        require(can_view, actor, report)
        _refuse_unless_draft(report)
        require(can_decide_bypass, actor, report)
        if bypass_request.status != TitheBypassRequest.Status.PENDING:
            raise ServiceError("This finalize request has already been decided or is no longer open.")
        note = (note or "").strip()
        if len(note) > NOTE_MAX_LENGTH:
            raise ServiceError(f"The note can have at most {NOTE_MAX_LENGTH} characters.")

        totals = build_totals(report)
        if totals.difference != bypass_request.difference:
            bypass_request.status = TitheBypassRequest.Status.VOIDED
            _save(bypass_request, "status")
            return BypassDecisionResult(bypass_request=bypass_request, voided=True)
        if approve and not totals.is_empty and totals.total > MAX_MONEY:
            raise ServiceError("The Total is too large to save.")

        approved = bool(approve)
        bypass_request.status = TitheBypassRequest.Status.APPROVED if approved else TitheBypassRequest.Status.REJECTED
        bypass_request.decided_by = actor
        bypass_request.decided_at = timezone.now()
        bypass_request.decision_note = note
        _save(bypass_request, "status", "decided_by", "decided_at", "decision_note")
        _log(
            APPROVE_ACTION if approved else REJECT_ACTION, actor, request, report,
            after={"bypass_request_id": bypass_request.pk, "status": bypass_request.status},
        )
        auto_finalized = approved
        if approved:
            if totals.is_empty:
                _freeze_no_tithes(report, actor, request, approved=bypass_request)
            else:
                _freeze_report(report, actor, request, totals, approved=bypass_request)
        requester = User.objects.filter(pk=bypass_request.requested_by_id).first()
        if requester is not None and requester.can_sign_in and requester.pk != actor.pk:
            _tell(
                [requester.pk], NOTIFY_APPROVED if approved else NOTIFY_REJECTED, actor, report, bypass_request,
                {
                    "title": (
                        "Your no tithes request was approved" if auto_finalized and totals.is_empty
                        else "Your finalize request was approved" if auto_finalized
                        else "Your finalize request was decided"
                    ),
                    "body": "The report is finalized." if auto_finalized else "Open the report to see the decision.",
                    "url": _report_url(report),
                },
            )
    return BypassDecisionResult(bypass_request=bypass_request, finalized=bool(auto_finalized))


# ============================================================================ my tithes
def member_tithe_history(user):
    """The envelopes `user` gave or shared in, from FINALIZED reports only, newest first.

    Same idea as attendance `member_attendance_history`: nothing is copied, so a reopened report (a draft
    again) leaves the history until it is finalized once more, and a report on an ARCHIVED sheet is left out,
    just as its numbers are left out of the dashboards. An entry counts when `user` is its giver OR one of its
    co-givers. The FULL amount of the envelope is shown, never split between the people on it.
    Each entry: `{"report_id", "entry_id", "date", "label", "extension", "amount", "shared_with", "role"}`
    where `shared_with` is the list of the OTHER names on the envelope (stored snapshots) and `role` is
    "giver" or "co_giver". Only the person's OWN envelopes are read (`user` comes from the signed-in request,
    never from a URL), and nothing about another person is returned except the name printed on the envelope.
    """
    user_id = _pk(user)
    as_co_giver = TitheEntryGiver.objects.filter(entry=OuterRef("pk"), user_id=user_id)
    entries = (
        TitheEntry.objects.select_related("tithes_offering__attendance__extension")
        .prefetch_related("co_givers")
        .filter(
            Q(user_id=user_id) | Exists(as_co_giver),
            tithes_offering__status=TithesOffering.Status.FINALIZED,
        )
        .exclude(tithes_offering__attendance__status=Attendance.Status.ARCHIVED)
    )
    history = []
    for entry in entries:
        sheet = entry.tithes_offering.attendance
        is_giver = entry.user_id == user_id
        others = [g.name_snapshot for g in entry.co_givers.all() if g.user_id != user_id]
        if not is_giver:
            others.insert(0, entry.giver_name)   # the name on the envelope comes first
        history.append({
            "report_id": entry.tithes_offering_id,
            "entry_id": entry.pk,
            "date": sheet.attendance_date,
            "label": sheet.label,
            "extension": title_case(sheet.extension.name),
            "amount": entry.amount,
            "shared_with": others,
            "role": "giver" if is_giver else "co_giver",
        })
    history.sort(key=lambda e: (e["date"], e["report_id"], e["entry_id"]), reverse=True)
    return history


def entry_names(entry):
    """The names printed for one envelope: the giver, then the co-givers ("Juan Cruz & Maria Cruz").

    Uses the stored name snapshots only. Two names read "A & B"; three or more read "A, B & C".
    """
    names = [entry.giver_name] + [g.name_snapshot for g in entry.co_givers.all()]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " & " + names[-1]


def bypass_note_for(report):
    """What the printed page says about a mismatch that was let through, or None.

    Returns `{"difference", "by", "reason", "kind"}` for a FINALIZED report whose difference is not 0:
    `kind` is "direct" (an admin typed a reason: `bypass_reason` and `bypassed_by`) or "approved" (the USED
    request whose stored difference equals the frozen one; its message is the reason and `decided_by` the
    approver). None when the report is a draft or balanced. Read-only: the print screen is the only place
    these words are shown, and they are never written to `audit_log` or `notification.data`.
    """
    if report.status != TithesOffering.Status.FINALIZED or not report.difference:
        return None
    if report.bypass_reason:
        by = report.bypassed_by.full_name if report.bypassed_by_id else "an admin"
        return {"difference": report.difference, "by": by, "reason": report.bypass_reason, "kind": "direct"}
    used = (
        report.bypass_requests.select_related("decided_by")
        .filter(status=TitheBypassRequest.Status.USED, difference=report.difference).order_by("-pk").first()
    )
    if used is None:
        return None
    by = used.decided_by.full_name if used.decided_by_id else "an admin"
    return {"difference": report.difference, "by": by, "reason": used.message, "kind": "approved"}

def disband_household(actor, household_id):
    """Disband a household: remove it and all its memberships. Past entries are never touched."""
    with transaction.atomic():
        extension_id = (
            TitheHousehold.objects.filter(pk=household_id).values_list("extension_id", flat=True).first()
        )
        if extension_id is None:
            raise ServiceError("That household no longer exists.")
        Extension.objects.select_for_update().get(pk=extension_id)  # lock order: extension first
        household = TitheHousehold.objects.select_for_update().filter(pk=household_id).first()
        if household is None:
            raise ServiceError("That household no longer exists.")
        require(can_manage_households, actor, household.extension_id)
        household.delete()  # the memberships cascade; entries hold snapshots, so history is safe