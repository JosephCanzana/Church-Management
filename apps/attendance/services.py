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

Brick 10 adds promotion of a first timer guest (`convert_first_timer`), the "visited
before" hint (`previous_first_timer_visits`) and the personal history (`member_attendance_history`).
"""
from dataclasses import dataclass, field
from datetime import date

from django.db import transaction
from django.db.models import ProtectedError, Q, RestrictedError, Case, IntegerField, Value, When
from django.utils import timezone
from django.core.exceptions import PermissionDenied


from apps.accounts.models import Extension, Gender, PersonType, Role, Status, User, UserExtensionHistory
from apps.accounts.services.presence import mark_attended, remember_age_network
from apps.accounts.services.users import (
    auto_transfer_visitor, check_contact, find_duplicate_people, normalize_details, promote_guest,
)
from apps.audit.services import log_action
from apps.core.services import ServiceError, consume_submission_token
from apps.core.text import clean_suffix, clean_text, title_case

from .age import AgeNetwork, age_network_for, effective_age_network
from .models import Attendance, AttendanceFirstTimer, AttendanceMember
from .permissions import (
    can_archive, can_convert, can_create, can_edit_draft, can_force_delete, can_reopen, can_restore, can_set_taken_by,
    can_upload, can_view, has_attendance_role, require,
)
from .summary import build_summary, incomplete_rows

DEFAULT_LABEL = "Sunday Service"
LABEL_MAX_LENGTH = 100
SEARCH_MIN_LENGTH = 3
SEARCH_MAX_RESULTS = 20
MATCH_LIMIT = 10
AUTO_TRANSFER_VISITS = 3
ROSTER_MAX_PEOPLE = 500
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

    Never the birth date, phone number or address. The age network is worked out as of `on_date`
    (from the birth date, else the one stored on the person).
    """
    network = effective_age_network(person, on_date)
    return {
        "id": person.pk,
        "name": person.full_name,
        "person_type": person.person_type,
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
    belongs to another one, so this searches active people of every type (not archived or suspended).
    To keep that safe it needs the right
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
    people = User.objects.filter(status=Status.ACTIVE).select_related("extension")
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
    return [
        dict(_person_row(p, on_date), archived=p.status == Status.ARCHIVED, active=p.status == Status.ACTIVE)
        for p in people
    ]

def roster_people(actor, sheet):
    """The checklist shown when the workspace opens: people who are not on the sheet yet, no typing needed.

    Same rules and same DELIBERATE EXCEPTION to `for_user` as `search_people` (active people of every
    type, visitors from other extensions included, needs the right to edit this draft, only the few
    fields `_person_row` gives back). People of the sheet's own extension come first, then everyone
    else, each group by name. Capped at ROSTER_MAX_PEOPLE; anyone beyond it is still found by search.
    """
    current = Attendance.objects.filter(pk=sheet.pk).first()
    if current is None:
        raise ServiceError("This sheet no longer exists.")
    _check_draft(actor, current)
    on_sheet = AttendanceMember.objects.filter(attendance=current).values("user_id")
    people = (
        User.objects.filter(status=Status.ACTIVE)
        .exclude(pk__in=on_sheet)
        .exclude(role=Role.SUPER_ADMIN)
        .select_related("extension")
        .annotate(
            home_first=Case(
                When(extension_id=current.extension_id, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("home_first", "last_name", "first_name", "pk")[:ROSTER_MAX_PEOPLE]
    )
    return [_person_row(person, current.attendance_date) for person in people]

# ------------------------------------------------------------------ the ticks
def mark_present(actor, sheet, user, gender=None, age_network=None):
    """Mark `user` present. Returns (row, created); a second tick of the same person changes nothing.

    The row snapshots the name, home extension, gender and age network. The age network is worked
    out from the person's birth date AS OF THE SHEET DATE. `gender` and `age_network` only fill what
    the person's own record lacks. A gender is stored on the row only. An age network picked here is
    also kept on the person (`User.age_network`) when they have no birth date, so they are not asked
    again next time; a birth date, or an age network already stored, always wins. When something is
    still missing the row is saved with it blank and flagged on the screen: the taker fills it in
    with `set_member_category`, and the upload refuses a sheet with blanks.
    A person who cannot sign in (archived or suspended) cannot be marked.
    """
    with transaction.atomic():
        locked = _lock_draft(actor, sheet)
        person = User.objects.select_related("extension").filter(pk=user.pk).first()  # fresh, not the caller's copy
        if person is None or person.status != Status.ACTIVE:
            raise ServiceError("Only active people can be marked present.")
        existing = AttendanceMember.objects.filter(attendance=locked, user=person).first()
        if existing is not None:
            return existing, False
        # A converted first timer already counts on this sheet (as a first timer). A member row too
        # would count the same person twice in the frozen numbers.
        if AttendanceFirstTimer.objects.filter(attendance=locked).filter(
                Q(converted_user=person) | Q(guest=person)).exists():
            raise ServiceError(
                f"{person.full_name} is already on this sheet as a first timer."
            )
        if gender not in (None, ""):
            _pick(gender, Gender, "gender")
        if age_network not in (None, ""):
            _pick(age_network, AgeNetwork, "age network")
        known = effective_age_network(person, locked.attendance_date)
        row = AttendanceMember.objects.create(
            attendance=locked, user=person, member_name=person.full_name,
            person_type=person.person_type,
            gender=person.gender or gender or "",
            age_network=(known.value if known else None) or age_network or "",
            home_extension=person.extension,
            home_extension_name=title_case(person.extension.name) if person.extension_id else "",
        )
        if known is None and age_network:
            remember_age_network(person.pk, age_network)   # no birth date: ask once, not every Sunday
    return row, True


def unmark_present(actor, sheet, member_id):
    """Take a person off the sheet. Returns True if removed, False if they were not on it."""
    with transaction.atomic():
        locked = _lock_draft(actor, sheet)
        removed, _ = AttendanceMember.objects.filter(attendance=locked, pk=member_id).delete()
    return bool(removed)


def set_member_category(actor, sheet, member_id, gender=None, age_network=None):
    """Set the gender and/or age network on one ticked row; `None` leaves that one unchanged.

    Changes the row. A new age network is also kept on the person (`User.age_network`) when they have
    no birth date, so the next sheet starts with it; a person with a birth date is never changed (the
    birth date decides). The gender is never written to the person. Returns the row.
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
        if age_network is not None and row.user_id:
            remember_age_network(row.user_id, row.age_network)
    return row


# ----------------------------------------------------------------- first timers
@dataclass
class AddFirstTimerResult:
    """What `add_first_timer` did: the saved `first_timer`, or `matches` when it saved nothing."""

    first_timer: AttendanceFirstTimer = None
    matches: list = field(default_factory=list)
    # Earlier first-timer rows with the same name in this extension (see `previous_first_timer_visits`):
    # the screen shows "Visited before on [date]" and offers to convert. Empty for a truly new person.
    previous_visits: list = field(default_factory=list)

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


def _visits_for(sheet, data):
    """`previous_first_timer_visits` for the first timer described by cleaned `data`, before this sheet."""
    return previous_first_timer_visits(
        sheet.extension, data["first_name"], data["last_name"], suffix=data["suffix"],
        before_date=sheet.attendance_date, exclude_sheet_id=sheet.pk,
    )


def add_first_timer(
    actor, sheet, first_name, last_name, gender, age_network=None, birth_date=None,
    middle_name="", suffix="", phone_number="", confirm_new=False,
):
    """Add a first timer. Returns an `AddFirstTimerResult`.

    When existing people (or an earlier first timer of this extension, `previous_visits`) share the
    name and `confirm_new` is false, NOTHING is saved and the result carries `matches` and
    `previous_visits`, so the screen can ask "same person?". Same person means
    `mark_present` for that member, or `convert_first_timer` for an earlier first timer (listed in
    `previous_visits`).
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
            visits = _visits_for(locked, data)
            # An earlier first-timer row counts like an existing person: the taker decides "same person?"
            # (convert the earlier visit, then tick them) or "a new person" (confirm_new).
            if matches or visits:
                return AddFirstTimerResult(matches=matches, previous_visits=visits)
        guest = User.objects.create_user(
            first_name=data["first_name"], middle_name=data["middle_name"],
            last_name=data["last_name"], suffix=data["suffix"],
            birth_date=data["birth_date"], gender=data["gender"], age_network=data["age_network"],
            phone_number=data["phone_number"], role=Role.MEMBER, person_type=PersonType.GUEST,
            status=Status.ACTIVE, extension=locked.extension, must_change_password=False,
            created_by=actor,
        )
        UserExtensionHistory.objects.create(
            user=guest, extension=locked.extension,
            from_date=min(locked.attendance_date, timezone.localdate()),
            reason=UserExtensionHistory.Reason.INITIAL,
        )
        row = AttendanceFirstTimer.objects.create(attendance=locked, guest=guest, **data)
        visits = _visits_for(locked, data)
    return AddFirstTimerResult(first_timer=row, previous_visits=visits)


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
    """Take a first timer off the draft sheet. Returns True if removed, False if they were not on it.

    A first timer who has since become a member (a member record or an account) can be removed too. Only this
    visit is deleted, never the person, and that visit is also the "First visit" entry in the person's
    history, so a mistaken entry can be undone. An uploaded sheet cannot be changed here (`_lock_draft`
    refuses it; reopen the sheet first).
    """
    with transaction.atomic():
        locked = _lock_draft(actor, sheet)
        removed, _ = AttendanceFirstTimer.objects.filter(attendance=locked, pk=first_timer_id).delete()
    return bool(removed)


# -------------------------------------------------- guest first-visit helpers
def previous_first_timer_visits(
    extension, first_name, last_name, *, suffix="", before_date=None, exclude_sheet_id=None,
):
    """Earlier visits of a first timer with this name in `extension`, newest first.

    Feeds the "Visited before on [date]" hint of `add_first_timer`. Each entry is
    `{"first_timer_id", "sheet_id", "date"}`; the id lets the screen offer `convert_first_timer`
    for that earlier row. Only rows whose linked person is still a guest are listed (a promoted
    person is found by the "same person?" matching), on sheets that are not archived.
    `before_date` keeps the list to sheets dated earlier than the one being worked on, and
    `exclude_sheet_id` leaves that sheet out. Names are compared ignoring capitals.
    No permission check: only `add_first_timer` calls it, after its own checks. Names and dates only
    are read; nothing here returns a phone number or birth date.
    """
    rows = AttendanceFirstTimer.objects.select_related("attendance").filter(
        attendance__extension=extension,
        converted_user__isnull=True,
        first_name__iexact=clean_text(first_name),
        last_name__iexact=clean_text(last_name),
        suffix__iexact=clean_suffix(suffix),
    ).filter(
        Q(guest__isnull=True) | Q(guest__person_type=PersonType.GUEST),
    ).exclude(attendance__status=Attendance.Status.ARCHIVED)
    if before_date is not None:
        rows = rows.filter(attendance__attendance_date__lt=before_date)
    if exclude_sheet_id is not None:
        rows = rows.exclude(attendance_id=exclude_sheet_id)
    return [
        {"first_timer_id": row.pk, "sheet_id": row.attendance_id, "date": row.attendance.attendance_date}
        for row in rows.order_by("-attendance__attendance_date", "-pk")
    ]


@dataclass
class ConvertResult:
    """What `convert_first_timer` did.

    `user` is the promoted member record. This action does not create an account or password.
    `duplicate` is True when this very form submission was already processed: nothing was done again.
    """

    user: object = None
    first_timer: AttendanceFirstTimer = None
    duplicate: bool = False


def convert_first_timer(actor, first_timer_id, *, token, request=None):
    """Promote a first timer's linked guest to a member record, keeping their visit history.

    Allowed for the coordinator of the sheet's extension, or an admin or super-admin, on a draft
    or an uploaded sheet (never an archived one, and never a special member). Everything runs in
    ONE transaction, in this order:
      1. consume the form's one-time `token` (a replay returns `duplicate=True`);
      2. lock the sheet row, then the first-timer row; refuse if it is already converted;
      3. use its linked guest (creating one for a legacy row without a link), then promote that same row;
      4. link all visits for that guest to the promoted row for attendance history;
      5. log `attendance.first_timer_convert` with ids only (no name, birth date or phone).
    No sign-in account or password is created, and NO member row is added to the sheet: the person already
    counts once, as a first timer, so a member row would count them twice in the frozen numbers.
    """
    with transaction.atomic():
        if not consume_submission_token(token):
            return ConvertResult(duplicate=True)
        # Lock order: sheet row first, then the first timer (like every other action on a sheet).
        sheet_id = AttendanceFirstTimer.objects.filter(pk=first_timer_id).values_list("attendance_id", flat=True).first()
        sheet = Attendance.objects.select_related("extension").select_for_update(of=("self",)).filter(pk=sheet_id).first() \
            if sheet_id else None
        row = AttendanceFirstTimer.objects.select_for_update().filter(pk=first_timer_id).first() if sheet else None
        if sheet is None or row is None:
            raise ServiceError("That first timer is no longer on a sheet.")
        require(can_view, actor, sheet)
        if sheet.status == Attendance.Status.ARCHIVED:
            raise ServiceError("This sheet is archived.")
        require(can_convert, actor, sheet)
        if row.converted_user_id:
            raise ServiceError(f"{row.full_name} has already been promoted.")

        person = User.objects.select_for_update().filter(pk=row.guest_id).first() if row.guest_id else None
        if person is None:
            # Legacy attendance rows predate guest links. Give the visit one guest identity now,
            # then promote it in place; never create a second member or a sign-in account.
            person = User.objects.create_user(
                first_name=row.first_name, middle_name=row.middle_name, last_name=row.last_name,
                suffix=row.suffix, birth_date=row.birth_date, gender=row.gender,
                age_network=row.age_network, phone_number=row.phone_number,
                role=Role.MEMBER, person_type=PersonType.GUEST, status=Status.ACTIVE,
                extension=sheet.extension, must_change_password=False, created_by=actor,
            )
            UserExtensionHistory.objects.create(
                user=person, extension=sheet.extension,
                from_date=min(sheet.attendance_date, timezone.localdate()),
                reason=UserExtensionHistory.Reason.INITIAL,
            )
            row.guest = person
        if person.person_type == PersonType.GUEST:
            person, _changed = promote_guest(actor, request, person)
        elif person.person_type not in (PersonType.MEMBER_RECORD, PersonType.MEMBER_ACCOUNT):
            raise ServiceError("This first timer cannot be promoted from their current person type.")
        AttendanceFirstTimer.objects.filter(guest=person, converted_user__isnull=True).update(converted_user=person)
        row.converted_user = person
        row.guest = person
        row.save(update_fields=["converted_user", "guest"])
        log_action(
            "attendance.first_timer_convert", actor=actor, request=request,
            entity_type="attendance_first_timer", entity_id=row.pk, entity_label=str(sheet),
            extension_id=sheet.extension_id,
            after={"attendance_id": sheet.pk, "user_id": person.pk},
        )
    return ConvertResult(user=person, first_timer=row)


def member_attendance_history(user):
    """The services `user` was at, from UPLOADED sheets only, newest first.

    Combines their ticks ("Present") with the first-timer rows promoted into this person record
    ("First visit"). Nothing is copied between tables, so a reopened sheet leaves the history until it
    is uploaded again. Each entry: `{"sheet_id", "date", "label", "extension", "kind"}` where `kind` is
    "present" or "first_visit". Only the person's OWN rows are read (`user` comes from the signed-in
    request, never from a URL). No other person's name, counts, birth date or phone are returned.
    """
    uploaded = Attendance.Status.UPLOADED
    entries = []
    ticks = AttendanceMember.objects.select_related("attendance__extension").filter(
        user=user, attendance__status=uploaded,
    )
    for row in ticks:
        entries.append(_history_entry(row.attendance, "present"))
    firsts = AttendanceFirstTimer.objects.select_related("attendance__extension").filter(
        Q(converted_user=user) | Q(guest=user, guest__person_type__in=(PersonType.MEMBER_RECORD, PersonType.MEMBER_ACCOUNT)),
        attendance__status=uploaded,
    )
    for row in firsts:
        entries.append(_history_entry(row.attendance, "first_visit"))
    entries.sort(key=lambda e: (e["date"], e["sheet_id"]), reverse=True)
    return entries


def _history_entry(sheet, kind):
    """One line of `member_attendance_history`."""
    return {
        "sheet_id": sheet.pk, "date": sheet.attendance_date, "label": sheet.label,
        "extension": title_case(sheet.extension.name), "kind": kind,
    }


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
            7. transfer active members after their third uploaded visit to this extension;
            8. log `attendance.upload` with counts only (no names).
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

        _auto_transfer_repeat_visitors(actor, locked, attended_on, request)

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


def _auto_transfer_repeat_visitors(actor, sheet, on_date, request):
    """Move active members after three distinct uploaded visits to this extension.

    A single visit never changes someone's home extension. Counts use the extension snapshot saved
    when they were marked, so visits made before a prior move cannot trigger another transfer.
    """
    candidates = AttendanceMember.objects.filter(
        attendance=sheet, user__isnull=False,
    ).exclude(home_extension_id=sheet.extension_id).values_list("user_id", "home_extension_id")
    for person_id, home_extension_id in candidates:
        if home_extension_id is None:
            continue
        person = User.objects.filter(
            pk=person_id, role=Role.MEMBER, status=Status.ACTIVE, extension_id=home_extension_id,
        ).only("pk").first()
        if person is None:
            continue
        visits = AttendanceMember.objects.filter(
            user_id=person_id,
            home_extension_id=home_extension_id,
            attendance__extension_id=sheet.extension_id,
            attendance__status=Attendance.Status.UPLOADED,
        ).values("attendance_id").distinct().count()
        if visits >= AUTO_TRANSFER_VISITS:
            auto_transfer_visitor(actor, request, person_id, sheet.extension_id, on_date)


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



# ------------------------------------------------------- archive and force delete
def archive_sheet(actor, sheet_id, *, request=None):
    """Archive a draft or uploaded sheet and return it. Logs `attendance.archive` (a draft) or
    `attendance.archive_uploaded` (an uploaded sheet: it takes counted numbers out of the dashboards, so only
    that action has a `notification_rule` that alerts the admins).

    Admin or super-admin: a draft or an uploaded sheet. Coordinator: a draft of their own extension only
    (`can_archive`). ONE transaction: lock the sheet row, re-check permission on the locked row, then set
    `status` and `archived_at` together (a database constraint ties them) with `archived_at` in
    `update_fields`, so the purge trigger sets `purge_at`. The frozen counts of an uploaded sheet are kept
    (an archived sheet can still be read by an admin) but the sheet leaves the dashboards and the repeat-visitor
    count, which read uploaded sheets only. `last_attended_at` is NOT rolled back. Repeating the action on an
    archived sheet is a friendly ServiceError, so a double tap is harmless.
    The audit row carries the old status and, for an uploaded sheet, the three frozen counts (no names).
    """
    with transaction.atomic():
        locked = Attendance.objects.select_for_update().filter(pk=sheet_id).first()
        if locked is None:
            raise ServiceError("This sheet no longer exists.")
        require(can_view, actor, locked)
        if locked.status == Attendance.Status.ARCHIVED:
            raise ServiceError("This sheet is already archived.")
        require(can_archive, actor, locked)

        before = {"status": locked.status}
        was_uploaded = locked.status == Attendance.Status.UPLOADED
        if was_uploaded:
            before.update(
                members_present=locked.members_present,
                first_timers_count=locked.first_timers_count,
                total_present=locked.total_present,
            )
        locked.status = Attendance.Status.ARCHIVED
        locked.archived_at = timezone.now()
        locked.save(update_fields=["status", "archived_at"])

        log_action(
            "attendance.archive_uploaded" if was_uploaded else "attendance.archive",
            actor=actor, request=request,
            entity_type="attendance", entity_id=locked.pk, entity_label=str(locked),
            extension_id=locked.extension_id,
            before=before, after={"status": Attendance.Status.ARCHIVED},
        )
    return locked


def restore_sheet(actor, sheet_id, *, request=None):
    """Bring an ARCHIVED sheet back and return it. Logs `attendance.restore` (ids and status only, no alert).

    Admin or super-admin: any archived sheet. Coordinator: one of their own extension that was a draft when
    archived (`can_restore`). ONE transaction: lock the sheet row, re-check permission on the locked row, then
    clear `archived_at` and set the status back together (a database constraint ties them; the purge trigger
    clears `purge_at`). Archiving keeps `uploaded_at`, the frozen counts and the summary, and a reopen clears
    them, so `uploaded_at` tells what the sheet was: set means it returns as UPLOADED (its numbers count on the
    dashboards again), empty means it returns as a DRAFT. The unique extension, date and label never clashes,
    because an archived sheet kept holding it. A repeat on a sheet that is no longer archived is a friendly
    ServiceError, so a double tap is harmless.
    """
    with transaction.atomic():
        locked = Attendance.objects.select_for_update().filter(pk=sheet_id).first()
        if locked is None:
            raise ServiceError("This sheet no longer exists.")
        require(can_view, actor, locked)
        if locked.status != Attendance.Status.ARCHIVED:
            raise ServiceError("This sheet is not archived.")
        require(can_restore, actor, locked)

        locked.status = Attendance.Status.UPLOADED if locked.uploaded_at else Attendance.Status.DRAFT
        locked.archived_at = None
        locked.save(update_fields=["status", "archived_at"])

        log_action(
            "attendance.restore", actor=actor, request=request,
            entity_type="attendance", entity_id=locked.pk, entity_label=str(locked),
            extension_id=locked.extension_id,
            before={"status": Attendance.Status.ARCHIVED}, after={"status": locked.status},
        )
    return locked


def force_delete_sheet(actor, sheet_id, *, request=None):
    """Permanently delete an ARCHIVED sheet and its ticks and first-timer rows. Logs `attendance.force_delete`.

    Admin or super-admin only (`can_force_delete`). A draft or uploaded sheet is refused with a message to
    archive it first. ONE transaction: lock the sheet row, re-check on the locked row, write the audit row
    BEFORE deleting (counts only, no names), then delete the child rows and the sheet through the ORM
    (never raw SQL). People are never deleted: guests created from first timers stay as people records, and
    their `last_attended_at` is untouched. If another table still protects the sheet (a tithes record linked
    to it), nothing is deleted, the audit row rolls back with it and the person gets a friendly message.
    """
    try:
        with transaction.atomic():
            locked = Attendance.objects.select_for_update().filter(pk=sheet_id).first()
            if locked is None:
                raise ServiceError("This sheet no longer exists.")
            require(can_view, actor, locked)
            if locked.status != Attendance.Status.ARCHIVED:
                raise ServiceError("Archive this sheet first. Only an archived sheet can be deleted permanently.")
            require(can_force_delete, actor, locked)

            members = AttendanceMember.objects.filter(attendance=locked)
            first_timers = AttendanceFirstTimer.objects.filter(attendance=locked)
            log_action(
                "attendance.force_delete", actor=actor, request=request,
                entity_type="attendance", entity_id=locked.pk, entity_label=str(locked),
                extension_id=locked.extension_id,
                before={
                    "status": locked.status,
                    "members_present": locked.members_present,
                    "first_timers_count": locked.first_timers_count,
                    "total_present": locked.total_present,
                    "member_rows": members.count(),
                    "first_timer_rows": first_timers.count(),
                },
            )
            members.delete()
            first_timers.delete()
            locked.delete()
    except (ProtectedError, RestrictedError):
        raise ServiceError("This sheet is still used by another record, so it cannot be deleted.")


@transaction.atomic
def edit_first_timer(actor, sheet, first_timer_id, *, first_name, last_name, gender=None, age_network=None):
    """Change the name, gender and age network of one first timer on a draft sheet (this row only).

    The sheet row is locked first, so an upload that lands at the same moment either finishes before this
    (and this is refused as "not a draft") or after it. Raises `PermissionDenied` when the actor may not edit
    the draft, and `ServiceError` for a missing row or a bad value.
    """
    locked = Attendance.objects.select_for_update().get(pk=sheet.pk)
    if not permissions.can_edit_draft(actor, locked):
        raise PermissionDenied
    row = AttendanceFirstTimer.objects.filter(pk=first_timer_id, attendance=locked).first()
    if row is None:
        raise ServiceError("That first timer was not on the sheet.")
    first_name = title_case(" ".join((first_name or "").split()))
    last_name = title_case(" ".join((last_name or "").split()))
    if not first_name:
        raise ServiceError("Type a first name.")
    if gender not in Gender.values:
        raise ServiceError("Pick a gender.")
    if age_network not in AgeNetwork.values:
        raise ServiceError("Pick an age network.")
    row.first_name, row.last_name = first_name, last_name
    row.gender, row.age_network = gender, age_network
    row.save(update_fields=["first_name", "last_name", "gender", "age_network"])
    return row