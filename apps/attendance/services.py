"""attendance.services: everything done on a DRAFT attendance sheet.

Same conventions as accounts.services: every function checks permission first (through
`attendance.permissions`, the only place that decides), does its work in `transaction.atomic()`,
and reports a rule failure as `ServiceError` (a message that is safe to show). A missing
permission raises `PermissionDenied`, as everywhere else.

Every action on a sheet's rows locks the sheet row with `select_for_update()` and refuses unless the
sheet is still a draft. The upload (brick 6) takes the same lock, so a tick sent from a slow second
device after the upload is refused instead of changing a locked sheet.

AUDIT: only `attendance.create` is written here. Ticks, first timers and "taken by" changes are
working data on a draft and are NOT audited; the upload and reopen (bricks 6 and 7) are. No name,
birth date or phone number ever goes into `audit_log`.

A visitor is simply a person whose home extension differs from the sheet's extension. Nothing
extra is stored for it.
"""
from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Extension, Gender, Role, Status, User
from apps.accounts.services.presence import mark_attended
from apps.accounts.services.users import check_contact, find_duplicate_people, normalize_details
from apps.audit.services import log_action
from apps.core.services import ServiceError, consume_submission_token
from apps.core.text import clean_suffix, clean_text, title_case

from .age import AgeNetwork, age_network_for
from .models import Attendance, AttendanceFirstTimer, AttendanceMember
from .permissions import (
    can_create, can_edit_draft, can_reopen, can_set_taken_by, can_upload, can_view, has_attendance_role,
    require,
)
from .summary import build_summary, incomplete_rows

DEFAULT_LABEL = "Sunday Service"
LABEL_MAX_LENGTH = 100
SEARCH_MIN_LENGTH = 3
SEARCH_MAX_RESULTS = 20
MATCH_LIMIT = 10
#: Shown as "belongs to" for an admin or super-admin, who have no home extension.
NO_EXTENSION_LABEL = "-"
#: Fields of a first timer a taker can change (`update_first_timer`).
FIRST_TIMER_FIELDS = (
    "first_name", "middle_name", "last_name", "suffix", "gender", "age_network", "birth_date", "phone_number",
)
_NAME_LIMITS = (("first_name", 150), ("middle_name", 150), ("last_name", 150), ("suffix", 10))
# People who cannot sign in cannot be on the checklist (same rule as the special roles).
_CANNOT_SIGN_IN = (Status.ARCHIVED, Status.SUSPENDED)


# --------------------------------------------------------------------- helpers
def _check_draft(actor, sheet):
    """Raise unless `actor` may work on this sheet AND it is still a draft.

    PermissionDenied for someone who may not open the sheet at all; ServiceError (a friendly
    message) when they may, but the sheet is locked or archived.
    """
    require(can_view, actor, sheet)
    if sheet.status == Attendance.Status.UPLOADED:
        raise ServiceError("This sheet has been uploaded and is locked. Only an admin can reopen it.")
    if sheet.status != Attendance.Status.DRAFT:
        raise ServiceError("This sheet is archived.")
    require(can_edit_draft, actor, sheet)


def _lock_draft(actor, sheet):
    """Lock the sheet row, re-read it, and check it is still a draft. Call inside `transaction.atomic()`.

    The permission check runs on the freshly locked row, never on the caller's possibly stale copy.
    """
    locked = Attendance.objects.select_for_update().filter(pk=sheet.pk).first()
    if locked is None:
        raise ServiceError("This sheet no longer exists.")
    _check_draft(actor, locked)
    return locked


def _pick(value, choices, what):
    """`value` if it is one of the TextChoices `choices`, else a ServiceError."""
    if value not in choices.values:
        raise ServiceError(f"Choose a valid {what}.")
    return value


def _person_row(person, on_date):
    """The few fields a screen may show about a person: id, name, where they belong, age network.

    Never the birth date, phone number or address. The age network is worked out as of `on_date`.
    """
    network = age_network_for(person.birth_date, on_date)
    return {
        "id": person.pk,
        "name": person.full_name,
        "home_extension": title_case(person.extension.name) if person.extension_id else NO_EXTENSION_LABEL,
        "age_network": network.value if network else None,
    }


# ----------------------------------------------------------------------- sheets
def create_sheet(actor, extension, attendance_date, label=DEFAULT_LABEL, event=None, *, request=None):
    """Create a draft sheet for `extension` and return it. Logs `attendance.create`.

    `created_by` and `taken_by` are the actor. One sheet per extension, date and label: a repeat
    (ignoring capitals, and counting archived sheets, which still hold the database constraint)
    is a friendly ServiceError. The extension row is locked, so two clicks cannot both create it.
    """
    require(can_create, actor, extension.pk)
    label = " ".join((label or "").split()) or DEFAULT_LABEL
    if len(label) > LABEL_MAX_LENGTH:
        raise ServiceError(f"The label can have at most {LABEL_MAX_LENGTH} characters.")
    if not isinstance(attendance_date, date):
        raise ServiceError("Enter the date of the gathering.")
    with transaction.atomic():
        locked = Extension.objects.select_for_update().filter(pk=extension.pk).first()
        if locked is None or locked.archived_at is not None:
            raise ServiceError("That extension no longer exists.")
        if Attendance.objects.filter(
                extension=locked, attendance_date=attendance_date, label__iexact=label).exists():
            raise ServiceError(
                f"A sheet called \"{label}\" for {attendance_date:%d %b %Y} already exists in "
                f"{title_case(locked.name)} (it may be archived)."
            )
        sheet = Attendance.objects.create(
            extension=locked, event=event, attendance_date=attendance_date, label=label,
            created_by=actor, taken_by=actor,
        )
        log_action(
            "attendance.create", actor=actor, request=request,
            entity_type="attendance", entity_id=sheet.pk, entity_label=str(sheet),
            extension_id=locked.pk, after={"attendance_date": attendance_date, "label": label},
        )
    return sheet


def set_taken_by(actor, sheet, user):
    """Change who took the attendance. Returns True if changed, False if it was already them.

    Only the coordinator of the sheet's extension (or an admin) may, on a draft. The new person
    must be that extension's coordinator or an attendance special member who can sign in.
    Not audited: it is working data on a draft.
    """
    with transaction.atomic():
        locked = _lock_draft(actor, sheet)
        require(can_set_taken_by, actor, locked)
        person = User.objects.select_for_update().filter(pk=user.pk).first()
        is_coordinator = (
            person is not None and person.role == Role.COORDINATOR
            and person.extension_id == locked.extension_id
        )
        if person is None or person.status in _CANNOT_SIGN_IN or not (
                is_coordinator or has_attendance_role(person, locked.extension_id)):
            raise ServiceError("Choose the coordinator or an attendance member of this extension.")
        if locked.taken_by_id == person.pk:
            return False
        locked.taken_by = person
        locked.save(update_fields=["taken_by"])
    return True


# ------------------------------------------------------------- finding people
def search_people(actor, sheet, text):
    """People matching `text`, for the checklist (visitors from other extensions included).

    DELIBERATE EXCEPTION to `for_user`: that stops at a coordinator's own extension, but a visitor
    belongs to another one, so this searches everyone who can sign in: members, coordinators,
    admins and the super-admin (not archived, not suspended). To keep that safe it needs the right
    to edit this draft, asks for at least 3 characters (fewer returns an empty list), returns at
    most 20 people, and gives back only id, name, where they belong and age network. An admin
    or super-admin has no extension, so their "belongs to" is a dash. Every word typed must
    match the first, middle or last name.
    """
    current = Attendance.objects.filter(pk=sheet.pk).first()
    if current is None:
        raise ServiceError("This sheet no longer exists.")
    _check_draft(actor, current)
    words = (text or "").split()
    if len(" ".join(words)) < SEARCH_MIN_LENGTH:
        return []
    people = User.objects.exclude(status__in=_CANNOT_SIGN_IN).select_related("extension")
    for word in words:
        word = clean_text(word)
        people = people.filter(
            Q(first_name__icontains=word) | Q(middle_name__icontains=word) | Q(last_name__icontains=word)
        )
    people = people.order_by("last_name", "first_name", "pk")[:SEARCH_MAX_RESULTS]
    return [_person_row(person, current.attendance_date) for person in people]


def find_possible_matches(first_name, last_name, birth_date=None, suffix="", *, on_date=None):
    """Existing people who may be the same person as a first timer, for the "same person?" prompt.

    Uses the duplicate-name rule of user create and edit (`find_duplicate_people`), so there is one
    rule, not two: same first name, last name and suffix; archived people count; a match is
    dropped when both sides have a birth date and the dates differ. The middle name is not
    compared (a first timer rarely gives one). Returns id, name, where they belong, age network
    (as of `on_date`, default today in Manila) and whether the account is archived.

    No permission check of its own: only `add_first_timer` calls it, after its own checks.
    """
    people = find_duplicate_people(
        clean_text(first_name), None, clean_text(last_name), birth_date,
        suffix=clean_suffix(suffix), limit=MATCH_LIMIT,
    )
    on_date = on_date or timezone.localdate()
    return [dict(_person_row(p, on_date), archived=p.status == Status.ARCHIVED) for p in people]


# ------------------------------------------------------------------ the ticks
def mark_present(actor, sheet, user, gender=None, age_network=None):
    """Mark `user` present. Returns (row, created); a second tick of the same person changes nothing.

    The row snapshots the name, home extension, gender and age network. The age network is worked
    out from the person's birth date AS OF THE SHEET DATE. `gender` and `age_network` only fill what
    the person's own record lacks, and are stored on the row only, never on the user. When
    something is still missing the row is saved with it blank and flagged on the screen: the taker
    fills it in with `set_member_category`, and the upload refuses a sheet with blanks.
    A person who cannot sign in (archived or suspended) cannot be marked.
    """
    with transaction.atomic():
        locked = _lock_draft(actor, sheet)
        person = User.objects.select_related("extension").filter(pk=user.pk).first()  # fresh, not the caller's copy
        if person is None or person.status in _CANNOT_SIGN_IN:
            raise ServiceError("Only people who can sign in can be marked present.")
        existing = AttendanceMember.objects.filter(attendance=locked, user=person).first()
        if existing is not None:
            return existing, False
        if gender not in (None, ""):
            _pick(gender, Gender, "gender")
        if age_network not in (None, ""):
            _pick(age_network, AgeNetwork, "age network")
        computed = age_network_for(person.birth_date, locked.attendance_date)
        row = AttendanceMember.objects.create(
            attendance=locked, user=person, member_name=person.full_name,
            gender=person.gender or gender or "",
            age_network=(computed.value if computed else None) or age_network or "",
            home_extension=person.extension,
            home_extension_name=title_case(person.extension.name) if person.extension_id else "",
        )
    return row, True


def unmark_present(actor, sheet, member_id):
    """Take a person off the sheet. Returns True if removed, False if they were not on it."""
    with transaction.atomic():
        locked = _lock_draft(actor, sheet)
        removed, _ = AttendanceMember.objects.filter(attendance=locked, pk=member_id).delete()
    return bool(removed)


def set_member_category(actor, sheet, member_id, gender=None, age_network=None):
    """Set the gender and/or age network on one ticked row; `None` leaves that one unchanged.

    Changes the ROW only, never the person's own record. Returns the row.
    """
    if gender is None and age_network is None:
        raise ServiceError("Choose a gender or an age network.")
    with transaction.atomic():
        locked = _lock_draft(actor, sheet)
        row = AttendanceMember.objects.filter(attendance=locked, pk=member_id).first()
        if row is None:
            raise ServiceError("That person is no longer on the sheet.")
        fields = []
        if gender is not None:
            row.gender = _pick(gender, Gender, "gender")
            fields.append("gender")
        if age_network is not None:
            row.age_network = _pick(age_network, AgeNetwork, "age network")
            fields.append("age_network")
        row.save(update_fields=fields)
    return row


# ----------------------------------------------------------------- first timers
@dataclass
class AddFirstTimerResult:
    """What `add_first_timer` did: the saved `first_timer`, or `matches` when it saved nothing."""

    first_timer: AttendanceFirstTimer = None
    matches: list = field(default_factory=list)

    @property
    def needs_confirmation(self):
        """True when nothing was saved because the screen must ask "same person?" first."""
        return self.first_timer is None


def _clean_first_timer(raw, sheet_date):
    """Validated, stored-form values for a first timer, from `raw` (a dict of FIRST_TIMER_FIELDS).

    Names, suffix and phone are cleaned the way the people forms do. A birth date, when given,
    DECIDES the age network (clear it to pick the network by hand).
    """
    data = normalize_details({
        "first_name": raw.get("first_name") or "", "middle_name": raw.get("middle_name") or "",
        "last_name": raw.get("last_name") or "", "suffix": raw.get("suffix") or "",
        "phone_number": raw.get("phone_number") or "",
    })
    if not data["first_name"] or not data["last_name"]:
        raise ServiceError("First name and last name are required.")
    for name, limit in _NAME_LIMITS:
        if len(data[name]) > limit:
            raise ServiceError(f"That name is too long (at most {limit} characters).")
    check_contact({"phone_number": data["phone_number"]})
    data["gender"] = _pick(raw.get("gender"), Gender, "gender")
    birth_date = raw.get("birth_date") or None
    if birth_date:
        network = age_network_for(birth_date, sheet_date)
        if network is None:
            raise ServiceError("The birth date is after the date of this sheet.")
        data["age_network"] = network.value
    else:
        data["age_network"] = _pick(raw.get("age_network"), AgeNetwork, "age network")
    data["birth_date"] = birth_date
    return data


def _refuse_same_sheet(sheet, data, exclude_id=None):
    """Refuse a first timer who is already on THIS sheet (a double tap), unless birth dates tell them apart."""
    others = AttendanceFirstTimer.objects.filter(
        attendance=sheet, first_name=data["first_name"], middle_name=data["middle_name"],
        last_name=data["last_name"], suffix=data["suffix"],
    )
    if exclude_id is not None:
        others = others.exclude(pk=exclude_id)
    for other in others:
        if data["birth_date"] and other.birth_date and data["birth_date"] != other.birth_date:
            continue
        raise ServiceError(
            f"{other.full_name} is already on this sheet as a first timer. "
            "If these are two different people, give both a birth date."
        )


def add_first_timer(
    actor, sheet, first_name, last_name, gender, age_network=None, birth_date=None,
    middle_name="", suffix="", phone_number="", confirm_new=False,
):
    """Add a first timer. Returns an `AddFirstTimerResult`.

    When existing people share the name and `confirm_new` is false, NOTHING is saved and the
    result carries `matches`, so the screen can ask "same person?". Same person means
    `mark_present` for that member (the conversion of an earlier first timer comes in brick 10).
    The screen sends `confirm_new=True` once the taker says "no, a new person".
    Not audited (a draft's working data, and it holds names and a phone number).
    """
    with transaction.atomic():
        locked = _lock_draft(actor, sheet)
        data = _clean_first_timer({
            "first_name": first_name, "middle_name": middle_name, "last_name": last_name,
            "suffix": suffix, "gender": gender, "age_network": age_network,
            "birth_date": birth_date, "phone_number": phone_number,
        }, locked.attendance_date)
        _refuse_same_sheet(locked, data)
        if not confirm_new:
            matches = find_possible_matches(
                data["first_name"], data["last_name"], data["birth_date"], data["suffix"],
                on_date=locked.attendance_date,
            )
            if matches:
                return AddFirstTimerResult(matches=matches)
        row = AttendanceFirstTimer.objects.create(attendance=locked, **data)
    return AddFirstTimerResult(first_timer=row)


def update_first_timer(actor, sheet, first_timer_id, data):
    """Change a first timer. `data` holds only the FIRST_TIMER_FIELDS to change; returns the row.

    The result is checked as a whole, like a new first timer (a birth date decides the age
    network). No "same person?" prompt here: that was answered when the row was added.
    """
    with transaction.atomic():
        locked = _lock_draft(actor, sheet)
        row = AttendanceFirstTimer.objects.filter(pk=first_timer_id, attendance=locked).first()
        if row is None:
            raise ServiceError("That first timer is no longer on this sheet.")
        merged = {name: getattr(row, name) for name in FIRST_TIMER_FIELDS}
        merged.update({name: value for name, value in data.items() if name in FIRST_TIMER_FIELDS})
        values = _clean_first_timer(merged, locked.attendance_date)
        _refuse_same_sheet(locked, values, exclude_id=row.pk)
        for name, value in values.items():
            setattr(row, name, value)
        row.save()
    return row


def remove_first_timer(actor, sheet, first_timer_id):
    """Take a first timer off the sheet. Returns True if removed, False if they were not on it."""
    with transaction.atomic():
        locked = _lock_draft(actor, sheet)
        removed, _ = AttendanceFirstTimer.objects.filter(attendance=locked, pk=first_timer_id).delete()
    return bool(removed)


# ------------------------------------------------------------------ the upload
@dataclass
class UploadResult:
    """What `upload_sheet` did.

    `sheet` is the sheet as it is now. `duplicate` is True when this very form submission was already
    processed (a double tap, two tabs): nothing was done a second time and nothing is wrong.
    """

    sheet: Attendance = None
    duplicate: bool = False


def upload_sheet(actor, sheet_id, *, token, request=None):
    """Save a reviewed draft as the locked record, with its numbers frozen. Returns an `UploadResult`.

    Only the coordinator of the sheet's extension may (`can_upload`). Everything runs in ONE
    transaction, in this order, so a failure leaves the sheet untouched:
      1. consume the form's one-time `token` (a replay returns `duplicate=True` and does nothing);
      2. lock the sheet row and check it is still a draft (an uploaded sheet is refused, so a
         second device cannot upload it again with a fresh token);
      3. refuse while any ticked member still lacks a gender or an age network, or nobody is marked;
      4. build the summary from the stored rows (the same function the draft preview uses);
      5. write the three totals, `summary`, `uploaded_by`, `uploaded_at` and the uploaded status;
      6. update `last_attended_at` of the ticked people, forward only;
      7. log `attendance.upload` with counts only (no names).
    A refusal rolls the token back too, so the same form can be sent again once the problem is fixed.
    """
    with transaction.atomic():
        if not consume_submission_token(token):
            return UploadResult(sheet=Attendance.objects.filter(pk=sheet_id).first(), duplicate=True)
        locked = Attendance.objects.select_for_update().filter(pk=sheet_id).first()
        if locked is None:
            raise ServiceError("This sheet no longer exists.")
        _check_draft(actor, locked)
        require(can_upload, actor, locked)

        missing = len(incomplete_rows(locked))
        if missing:
            raise ServiceError(
                f"{missing} {'person still needs' if missing == 1 else 'people still need'} "
                "a gender and an age network. Fill them in before uploading."
            )
        snapshot = build_summary(locked)
        if snapshot.total_present == 0:
            raise ServiceError("Nobody is marked present yet. Mark at least one person before uploading.")

        locked.members_present = snapshot.members_present
        locked.first_timers_count = snapshot.first_timers_count
        locked.total_present = snapshot.total_present
        locked.summary = snapshot.summary
        locked.uploaded_by = actor
        locked.uploaded_at = timezone.now()
        locked.status = Attendance.Status.UPLOADED
        locked.save(update_fields=[
            "members_present", "first_timers_count", "total_present", "summary",
            "uploaded_by", "uploaded_at", "status",
        ])

        # Forward only (see accounts.services.presence). Capped at today in Manila so a sheet dated
        # in the future by mistake can never make someone look present in the future.
        attended_on = min(locked.attendance_date, timezone.localdate())
        mark_attended(
            AttendanceMember.objects.filter(attendance=locked, user__isnull=False).values_list("user_id", flat=True),
            attended_on,
        )

        log_action(
            "attendance.upload", actor=actor, request=request,
            entity_type="attendance", entity_id=locked.pk, entity_label=str(locked),
            extension_id=locked.extension_id,
            after={
                "members_present": snapshot.members_present,
                "first_timers_count": snapshot.first_timers_count,
                "total_present": snapshot.total_present,
            },
        )
    return UploadResult(sheet=locked)


# ------------------------------------------------------------------- the reopen
@dataclass
class ReopenResult:
    """What `reopen_sheet` did.

    `sheet` is the sheet as it is now. `duplicate` is True when this very form submission was already
    processed (a double tap, two tabs): nothing was done a second time and nothing is wrong.
    """

    sheet: Attendance = None
    duplicate: bool = False


def reopen_sheet(actor, sheet_id, *, token, request=None):
    """Send an uploaded sheet back to draft so it can be corrected. Returns a `ReopenResult`.

    This is the ONLY way to edit an uploaded sheet: reopen, change it with the draft services above,
    and let the coordinator upload it again. Only an admin or super-admin may (`can_reopen`).
    Everything runs in ONE transaction, in this order, so a failure leaves the sheet untouched:
      1. consume the form's one-time `token` (a replay returns `duplicate=True` and does nothing);
      2. lock the sheet row and check it is uploaded (a draft or archived sheet is refused);
      3. set the status back to draft and clear the frozen counts, `summary`, `uploaded_by`
         and `uploaded_at`;
      4. log `attendance.reopen` (counts only, no reason text), which the `notifications` listener
         turns into an alert for the admins through the `notification_rule` row.
    A person's `last_attended_at` is NOT rolled back: the next upload only moves it forward.
    Until it is uploaded again the sheet is left out of the dashboards, which count uploaded sheets only.
    """
    with transaction.atomic():
        if not consume_submission_token(token):
            return ReopenResult(sheet=Attendance.objects.filter(pk=sheet_id).first(), duplicate=True)
        locked = Attendance.objects.select_for_update().filter(pk=sheet_id).first()
        if locked is None:
            raise ServiceError("This sheet no longer exists.")
        require(can_view, actor, locked)
        if locked.status != Attendance.Status.UPLOADED:
            raise ServiceError(
                "This sheet is archived." if locked.status == Attendance.Status.ARCHIVED
                else "This sheet is still a draft, so there is nothing to reopen."
            )
        require(can_reopen, actor, locked)

        # Remember what was frozen, so the audit row shows what the reopen took away (counts only).
        frozen = {
            "members_present": locked.members_present,
            "first_timers_count": locked.first_timers_count,
            "total_present": locked.total_present,
        }
        locked.status = Attendance.Status.DRAFT
        locked.members_present = None
        locked.first_timers_count = None
        locked.total_present = None
        locked.summary = None
        locked.uploaded_by = None
        locked.uploaded_at = None
        locked.save(update_fields=[
            "status", "members_present", "first_timers_count", "total_present", "summary",
            "uploaded_by", "uploaded_at",
        ])

        log_action(
            "attendance.reopen", actor=actor, request=request,
            entity_type="attendance", entity_id=locked.pk, entity_label=str(locked),
            extension_id=locked.extension_id,
            before=frozen, after={"status": Attendance.Status.DRAFT},
        )
    return ReopenResult(sheet=locked)
