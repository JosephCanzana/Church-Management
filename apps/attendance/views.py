"""attendance.views: the sheet screens, shared by every role (brick 8).

One set of views serves four places: `superadmin`, `admin_area`, `coordinator_area` (the role areas) and
`attendance` (the member root, for attendance special members). The views read the namespace of the request
(`request.resolver_match.namespace`), so every redirect stays inside the area the person came from; the
templates do the same through `{% role_url %}` (accounts/templatetags/people_tags.py).

Three guards, all required (hiding a link is not access control):
  1. `role_required` (login) plus `AREA_ROLES`: each area belongs to ONE role, so a coordinator who types an
     admin URL, or an admin who types the member URL, is sent to their own home.
  2. `permissions.can_use_area`: the page-level gate (a member without the attendance seat is refused here).
  3. Every sheet starts from `Attendance.objects.for_user(actor)`, and every service checks again.
Someone out of reach of a sheet sees "no longer exists", exactly like a deleted one.

Views stay thin: validate the form, call a service, show a toast. Business rules live in `services.py`.
Built screen by screen: list, create, the draft workspace, the read-only review, reopen, archive, restore and
force delete, and the A4 print page.

The workspace uses plain POST forms and a redirect (no JavaScript needed): search is a GET (`?q=`), every
tap (mark, remove, category, first timer, upload) is a POST handled by a thin view that calls ONE service.
A refusal (`ServiceError`, or `PermissionDenied` turned into a message) becomes a toast and sends the person
back to the sheet, which then shows the truth (a locked sheet opens on the review page).
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count
from django.db.models.functions import Lower
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import home_url_for, role_required
from apps.accounts.models import Gender, PersonType, Role, User
from apps.core.services import ServiceError, new_submission_token
from apps.core.text import title_case

from . import permissions, services
from .age import AgeNetwork
from .forms import FirstTimerForm, SheetCreateForm, SheetFilterForm
from .models import Attendance, AttendanceFirstTimer, AttendanceMember
from .summary import incomplete_rows, summary_for, summary_table

CONVERT_RESULT_TEMPLATE = "attendance/convert_result.html"
MINE_TEMPLATE = "attendance/mine.html"
MINE_PAGE_SIZE = 25

PAGE_SIZE = 25

LIST_TEMPLATE = "attendance/sheet_list.html"
FORM_TEMPLATE = "attendance/sheet_form.html"
DETAIL_TEMPLATE = "attendance/sheet_detail.html"
WORKSPACE_TEMPLATE = "attendance/sheet_workspace.html"
REVIEW_TEMPLATE = "attendance/sheet_review.html"
PRINT_TEMPLATE = "attendance/print.html"

#: Code -> label, for showing a stored gender or age network as words.
GENDER_LABELS = dict(Gender.choices)
AGE_LABELS = dict(AgeNetwork.choices)

# Which role each URL namespace belongs to. Keep in step with church_management/urls.py and the role url modules.
AREA_ROLES = {
    "superadmin": Role.SUPER_ADMIN,
    "admin_area": Role.ADMIN,
    "coordinator_area": Role.COORDINATOR,
    "attendance": Role.MEMBER,
}
GENDERS = [(code, GENDER_LABELS[code]) for code in (Gender.MALE.value, Gender.FEMALE.value)]
AGES = [(code, AGE_LABELS[code]) for code in AgeNetwork.values]



def attendance_screen(view):
    """Guard for every attendance page: signed in, in the right area, allowed into attendance, never cached.

    A person in the wrong area, or without access, goes to their own home page (the same rule as
    `role_required`). A refused member also gets a toast that says why.
    """

    @wraps(view)
    def guarded(request, *args, **kwargs):
        if AREA_ROLES.get(request.resolver_match.namespace) != request.user.role:
            return redirect(home_url_for(request.user))
        if not permissions.can_use_area(request.user):
            messages.error(request, "You do not have access to attendance.")
            return redirect(home_url_for(request.user))
        return view(request, *args, **kwargs)

    return role_required(*AREA_ROLES.values())(never_cache(guarded))


def _to(request, name, **kwargs):
    """Redirect to url `name` in the area the request came from."""
    return redirect(f"{request.resolver_match.namespace}:{name}", **kwargs)


def _gone(request):
    """The answer for a sheet that is missing OR out of reach (the two look the same on purpose)."""
    messages.info(request, "That sheet no longer exists.")
    return _to(request, "sheet_list")


def _sheet_people(sheet):
    """The decorated people and first timers of one sheet, with `is_returning` set on each first timer."""
    members = [
        _decorate_member(row, sheet)
        for row in AttendanceMember.objects.filter(attendance=sheet).order_by("member_name", "pk")
    ]
    first_timers = [
        _decorate_first_timer(row)
        for row in AttendanceFirstTimer.objects.select_related("guest")
        .filter(attendance=sheet).order_by("last_name", "first_name", "pk")
    ]
    returning = _returning_first_timers(sheet, first_timers)
    for row in first_timers:
        row.is_returning = row.pk in returning
    return members, first_timers


def build_simple_summary(members, first_timers):
    first_timer_rows = [p for p in first_timers if not p.is_returning]
    guest_rows = [m for m in members if m.is_guest] + [p for p in first_timers if p.is_returning]
    member_rows = [m for m in members if not m.is_guest]

    def block(title, people):
        rows = [(label, sum(1 for p in people if p.gender == code)) for code, label in GENDERS]
        rows += [(label, sum(1 for p in people if p.age_network == code)) for code, label in AGES]
        return {"title": title, "rows": rows, "total": len(people)}

    return [
        block("First timer", first_timer_rows),
        block("Guest", guest_rows),
        block("Member", member_rows),
    ]

# ------------------------------------------------------------------- list
def _with_counts(sheets):
    """Attach the numbers a list row shows: frozen values for an uploaded sheet, live counts for a draft.

    A reopened sheet is a draft again with its frozen values cleared, so it shows live counts too.
    """
    rows = list(sheets)
    for sheet in rows:
        if sheet.is_locked:
            sheet.members_shown = sheet.members_present or 0
            sheet.first_timers_shown = sheet.first_timers_count or 0
        else:
            sheet.members_shown = sheet.live_members
            sheet.first_timers_shown = sheet.live_first_timers
        sheet.total_shown = sheet.members_shown + sheet.first_timers_shown
    return rows


def _with_actions(sheets, viewer):
    """Attach what the row menu (three dots and right click) offers on each listed sheet.

    `can_archive`, `can_restore` and `can_force_delete` come from the same permission functions the services use,
    and `has_actions` is true when the menu has anything to offer. Only a hint for the screen: every service
    checks again.
    """
    for sheet in sheets:
        sheet.can_archive = permissions.can_archive(viewer, sheet)
        sheet.can_restore = permissions.can_restore(viewer, sheet)
        sheet.can_force_delete = permissions.can_force_delete(viewer, sheet)
        sheet.has_actions = sheet.can_archive or sheet.can_restore or sheet.can_force_delete
    return sheets


def _list_context(params, viewer):
    """Everything the sheet list needs, from a QueryDict of filters.

    Only sheets `viewer` may open are listed (`for_user`). An admin or super-admin also gets the extension
    filter. A special member sees drafts only, so the status filter is left out for them.
    """
    show_extension = viewer.role in (Role.SUPER_ADMIN, Role.ADMIN)
    show_status = viewer.role != Role.MEMBER
    form = SheetFilterForm(params)
    if not show_extension:
        del form.fields["extension"]
    if not show_status:
        del form.fields["status"]
    form.is_valid()
    data = getattr(form, "cleaned_data", {})
    status = data.get("status") or "current"
    extension = data.get("extension")
    date_from, date_to = data.get("date_from"), data.get("date_to")

    queryset = (
        Attendance.objects.for_user(viewer)
        .select_related("extension", "taken_by")
        .annotate(live_members=Count("members", distinct=True), live_first_timers=Count("first_timers", distinct=True))
    )
    if show_status:
        if status == "current":
            queryset = queryset.exclude(status=Attendance.Status.ARCHIVED)
        elif status != "all":
            queryset = queryset.filter(status=status)
    if extension is not None:
        queryset = queryset.filter(extension=extension)
    if date_from:
        queryset = queryset.filter(attendance_date__gte=date_from)
    if date_to:
        queryset = queryset.filter(attendance_date__lte=date_to)
    queryset = queryset.order_by("-attendance_date", "label", "pk")

    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(params.get("page"))
    page.object_list = _with_actions(_with_counts(page.object_list), viewer)
    without_page = params.copy()
    without_page.pop("page", None)
    return {
        "form": form,
        "page": page,
        "total": paginator.count,
        "querystring": without_page.urlencode(),
        "show_extension": show_extension,
        "show_extension_filter": show_extension,
        "show_status_filter": show_status,
        "can_create": viewer.role in (Role.SUPER_ADMIN, Role.ADMIN) or (
            viewer.role == Role.COORDINATOR and viewer.extension_id is not None
        ),
        # How many filters differ from the defaults (badge on the Filters button).
        "active_filters": sum([
            show_status and status != "current", extension is not None, bool(date_from), bool(date_to),
        ]),
    }


@attendance_screen
@require_http_methods(["GET"])
def sheet_list(request):
    """Sheets the person may open, newest first, with date range, status and (admin) extension filters."""
    context = _list_context(request.GET, request.user)
    # The row menu posts this back as `next`, so an action keeps the person on the same filters and page.
    context["return_to"] = request.get_full_path()
    return render(request, LIST_TEMPLATE, context)


# ----------------------------------------------------------------- create
@attendance_screen
@require_http_methods(["GET", "POST"])
def sheet_create(request):
    """New-sheet form (GET) and save (POST). The date starts as today in Manila, the label as Sunday Service.

    A repeat of extension, date and label is refused by the service with a friendly message, so a double
    tap cannot make two sheets. A new sheet opens on its detail page.
    """
    extensions = permissions.creatable_extensions(request.user)
    if not extensions.exists():
        messages.error(request, "You cannot start an attendance sheet.")
        return _to(request, "sheet_list")

    form = SheetCreateForm(request.POST or None, extensions=extensions)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            sheet = services.create_sheet(
                request.user, data["extension"], data["attendance_date"], data["label"], request=request,
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Sheet created: {sheet.label}, {sheet.attendance_date:%d %b %Y}.")
            return _to(request, "sheet_detail", pk=sheet.pk)
    return render(request, FORM_TEMPLATE, {"form": form, "single_extension": form.single_extension})


# -------------------------------------------------------------- shared helpers
def _open_sheet(request, pk):
    """The sheet with this id IF the person may open it (always starts from `for_user`), else None."""
    return (
        Attendance.objects.for_user(request.user)
        .select_related("extension", "taken_by", "uploaded_by")
        .filter(pk=pk)
        .first()
    )


def _sheet_url(request, sheet, name="sheet_detail"):
    """URL of one of the sheet's pages in the area the request came from."""
    return reverse(f"{request.resolver_match.namespace}:{name}", kwargs={"pk": sheet.pk})


def _back(request, sheet, anchor=""):
    """Redirect to the sheet's page (the workspace for a draft, the review once it is uploaded)."""
    return redirect(_sheet_url(request, sheet) + anchor)


def _to_int(value):
    """An int from a posted value, or None when it is not a whole number (never raises)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _danger_flags(request, sheet):
    """Whether to show the Archive, Restore and Delete buttons (`attendance/_sheet_danger_actions.html`).

    Only a hint for the screen: `services.archive_sheet` and `services.force_delete_sheet` check again.
    """
    return {
        "can_archive": permissions.can_archive(request.user, sheet),
        "can_restore": permissions.can_restore(request.user, sheet),
        "can_force_delete": permissions.can_force_delete(request.user, sheet),
    }


def _decorate_member(row, sheet):
    """Add what the templates show about one ticked row: words for the codes, visitor flag, missing category."""
    row.gender_label = GENDER_LABELS.get(row.gender, "")
    row.age_label = AGE_LABELS.get(row.age_network, "")
    row.needs_category = not (row.gender in Gender.values and row.age_network in AgeNetwork.values)
    # A visitor is just someone whose home extension is not the sheet's (nothing extra is stored).
    row.is_visitor = row.home_extension_id is not None and row.home_extension_id != sheet.extension_id
    row.is_guest = row.person_type == PersonType.GUEST
    row.has_account = row.person_type == PersonType.MEMBER_ACCOUNT
    return row


def _decorate_first_timer(row):
    """Add the words for a first timer's gender and age network."""
    row.gender_label = GENDER_LABELS.get(row.gender, "")
    row.age_label = AGE_LABELS.get(row.age_network, "")
    row.promoted = bool(
        row.converted_user_id or (
            row.guest_id and row.guest.person_type in (PersonType.MEMBER_RECORD, PersonType.MEMBER_ACCOUNT)
        )
    )
    return row


def _returning_first_timers(sheet, first_timers):
    """Ids of the first-timer rows on this sheet whose person attended before (a guest now, not a first timer).

    A row is returning when the same guest record, or the same first name, last name, gender and age network,
    appears on another sheet dated on or before this one. Read-only; the stored rows are not changed.
    """
    if not first_timers:
        return set()
    earlier = {"attendance__attendance_date__lte": sheet.attendance_date}
    guest_ids = {row.guest_id for row in first_timers if row.guest_id}
    seen_guests = set()
    if guest_ids:
        seen_guests |= set(
            AttendanceFirstTimer.objects.filter(guest_id__in=guest_ids, **earlier)
            .exclude(attendance=sheet).values_list("guest_id", flat=True)
        )
        seen_guests |= set(
            AttendanceMember.objects.filter(user_id__in=guest_ids, **earlier)
            .exclude(attendance=sheet).values_list("user_id", flat=True)
        )
    lasts = {row.last_name.strip().lower() for row in first_timers}
    seen_names = {
        (first.strip().lower(), last.strip().lower(), gender, network)
        for first, last, gender, network in (
            AttendanceFirstTimer.objects.exclude(attendance=sheet).filter(**earlier)
            .annotate(last_lower=Lower("last_name")).filter(last_lower__in=lasts)
            .values_list("first_name", "last_name", "gender", "age_network")
        )
    }
    returning = set()
    for row in first_timers:
        key = (row.first_name.strip().lower(), row.last_name.strip().lower(), row.gender, row.age_network)
        if row.guest_id in seen_guests or key in seen_names:
            returning.add(row.pk)
    return returning


def _can_edit_first_timer(request, sheet):
    """True when the first-timer edit page exists in this area (so the workspace can show its form).

    Reversing the URL means the form appears by itself once `sheet_first_timer_edit` is routed, and a missing
    route can never crash the workspace.
    """
    try:
        reverse(f"{request.resolver_match.namespace}:sheet_first_timer_edit", kwargs={"pk": sheet.pk})
    except NoReverseMatch:
        return False
    return permissions.can_edit_draft(request.user, sheet)


def _list_next(request):
    """The sheet-list URL (with its filters and page) a list form posted as `next`, or None.

    Only THIS area's own list URL is accepted (the exact path, optionally with a query string), never another
    page or site, so a forged `next` cannot redirect anywhere else.
    """
    target = request.POST.get("next", "")
    list_url = reverse(f"{request.resolver_match.namespace}:sheet_list")
    if target == list_url or target.startswith(f"{list_url}?"):
        return target
    return None


def _list_redirect(request):
    """Back to the list the person came from (same filters), else the plain list."""
    return redirect(_list_next(request) or reverse(f"{request.resolver_match.namespace}:sheet_list"))


def sheet_action(anchor=""):
    """Decorator for the POST-only views that act on one sheet: load it, run the view, report refusals.

    The decorated function takes `(request, sheet)`; it calls ONE service and adds its own success message,
    then returns None (the person goes back to the workspace, at `anchor`) or a response of its own.
    A `ServiceError` becomes an error toast. A `PermissionDenied` becomes a plain "cannot do that" toast, so a
    refused tap reads like every other refusal instead of a bare 403 page. A sheet out of reach looks deleted.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(request, pk):
            sheet = _open_sheet(request, pk)
            if sheet is None:
                return _gone(request)
            try:
                response = view(request, sheet)
            except ServiceError as exc:
                messages.error(request, str(exc))
            except PermissionDenied:
                messages.error(request, "You cannot do that on this sheet.")
            else:
                if response is not None:
                    return response
            # A form on the list says where it came from (`next`); everything else goes back to the sheet.
            return _list_redirect(request) if _list_next(request) else _back(request, sheet, anchor)

        return attendance_screen(require_http_methods(["POST"])(wrapped))

    return decorator


# -------------------------------------------------------------- the workspace
def _search_context(request, sheet, members):
    """The people-search part of the workspace: the typed text, a hint when it is too short, and the results.

    Search is a GET (`?q=`), so it works without JavaScript and a reload keeps the results. Each result says
    whether the person is already ticked, and whether they would be a visitor.
    """
    text = " ".join(request.GET.get("q", "").split())[:60]
    hint, results = "", []
    if text:
        if len(text) < services.SEARCH_MIN_LENGTH:
            hint = f"Type at least {services.SEARCH_MIN_LENGTH} letters to search."
        else:
            try:
                results = services.search_people(request.user, sheet, text)
            except (ServiceError, PermissionDenied):
                hint = "Search is not available on this sheet."
            else:
                ticked = {row.user_id for row in members}
                here = title_case(sheet.extension.name)
                for person in results:
                    person["marked"] = person["id"] in ticked
                    person["person_type_label"] = {
                        PersonType.GUEST: "Guest",
                        PersonType.MEMBER_RECORD: "Member (no account)",
                        PersonType.MEMBER_ACCOUNT: "Member",
                    }.get(person["person_type"], "")
                    person["age_label"] = AGE_LABELS.get(person["age_network"], "")
                    person["visitor"] = person["home_extension"] not in (services.NO_EXTENSION_LABEL, here)
                if not results:
                    hint = "Nobody found. Try another spelling, or add them as a first timer below."
    return {"q": text, "search_hint": hint, "results": results}

def _order_roster(roster, sheet):
    """Flag people of the sheet's extension and list them first, then everyone else by name."""
    home = (sheet.extension.name or "").strip().lower()
    rows = list(roster or [])

    def get(person, key):
        return person[key] if isinstance(person, dict) else getattr(person, key, "")

    for person in rows:
        flag = str(get(person, "home_extension") or "").strip().lower() == home
        if isinstance(person, dict):
            person["same_extension"] = flag
        else:
            person.same_extension = flag
    rows.sort(key=lambda p: (not get(p, "same_extension"), str(get(p, "name")).lower()))
    return rows

def _roster_context(request, sheet):
    """The member checklist, filled when the page opens: this extension's people first, then everyone else."""
    try:
        people = services.roster_people(request.user, sheet)
    except (ServiceError, PermissionDenied):
        return []
    here = title_case(sheet.extension.name)
    for person in people:
        person["person_type_label"] = {
            PersonType.GUEST: "Guest",
            PersonType.MEMBER_RECORD: "Member (no account)",
            PersonType.MEMBER_ACCOUNT: "Member",
        }.get(person["person_type"], "")
        person["same_extension"] = person["home_extension"] == here
    return people

def _workspace_context(request, sheet, form=None, pending=None):
    """Everything the draft workspace shows.

    `form` is the first-timer form (bound with its errors after a refused post); `pending` is the "same
    person?" prompt (the typed fields and the matches) when `add_first_timer` saved nothing.
    """
    members, first_timers = _sheet_people(sheet)
    # People who still need a category come first, so they are not missed on a long sheet.
    members.sort(key=lambda row: not row.needs_category)
    summary = summary_for(sheet)  # a draft is counted live from its rows
    form = form or FirstTimerForm()
    blocker = ""
    if summary.incomplete:
        blocker = (
            f"{summary.incomplete} {'person needs' if summary.incomplete == 1 else 'people need'} "
            "a gender and an age network before the sheet can be uploaded."
        )
    elif summary.total_present == 0:
        blocker = "Mark at least one person before uploading."
    can_upload = permissions.can_upload(request.user, sheet)
    context = {
        "sheet": sheet,
        "summary": summary,
        "table": summary_table(summary.summary),
        "members": members,
        "first_timers": first_timers,
        "can_edit_first_timer": _can_edit_first_timer(request, sheet),
        "form": form,
        "pending": pending,
        # The "more" fold opens by itself when it holds something (an error or a value to keep).
        "more_open": any(form[name].errors or form[name].value() for name in FirstTimerForm.MORE_FIELDS),
        "gender_choices": Gender.choices,
        "age_choices": AgeNetwork.choices,
        "can_review": permissions.can_review(request.user, sheet),
        "can_upload": can_upload,
        "upload_blocker": blocker,
        "submit_token": new_submission_token() if can_upload else "",
        # Brick 10: the convert buttons (see attendance/_convert_button.html and _previous_visits.html).
        "can_convert": permissions.can_convert(request.user, sheet),
        "convert_token": new_submission_token() if permissions.can_convert(request.user, sheet) else "",
    }
    context.update(_search_context(request, sheet, members))
    context["roster"] = _roster_context(request, sheet)
    context["roster"] = _order_roster(context.get("roster"), sheet)
    context.update(_danger_flags(request, sheet))
    return context


def _sheet_people(sheet):
    """The decorated people and first timers of one sheet, with `is_returning` set on each first timer."""
    members = [
        _decorate_member(row, sheet)
        for row in AttendanceMember.objects.filter(attendance=sheet).order_by("member_name", "pk")
    ]
    first_timers = [
        _decorate_first_timer(row)
        for row in AttendanceFirstTimer.objects.select_related("guest")
        .filter(attendance=sheet).order_by("last_name", "first_name", "pk")
    ]
    returning = _returning_first_timers(sheet, first_timers)
    for row in first_timers:
        row.is_returning = row.pk in returning
    return members, first_timers


def build_simple_summary(members, first_timers):
    """First timer / Guest / Member blocks: gender rows, age network rows and a total for each."""
    first_timer_rows = [p for p in first_timers if not p.is_returning]
    guest_rows = [m for m in members if m.is_guest] + [p for p in first_timers if p.is_returning]
    member_rows = [m for m in members if not m.is_guest]

    def block(title, people):
        rows = [(label, sum(1 for p in people if p.gender == code)) for code, label in GENDERS]
        rows += [(label, sum(1 for p in people if p.age_network == code)) for code, label in AGES]
        return {"title": title, "rows": rows, "total": len(people)}

    return [block("First timer", first_timer_rows), block("Guest", guest_rows), block("Member", member_rows)]


def _render_workspace(request, sheet, form=None, pending=None):
    """Render the draft workspace (used by the GET page and by a first-timer post that needs to talk back)."""
    return render(request, WORKSPACE_TEMPLATE, _workspace_context(request, sheet, form, pending))


# ----------------------------------------------------------------- detail
@attendance_screen
@require_http_methods(["GET"])
def sheet_detail(request, pk):
    """One sheet: the draft workspace while it is a draft, the read-only review once it is uploaded.

    Anything else a person may still open (an archived sheet) gets the plain header page.
    """
    sheet = _open_sheet(request, pk)
    if sheet is None:
        return _gone(request)
    if permissions.can_edit_draft(request.user, sheet):
        return _render_workspace(request, sheet)
    if permissions.can_review(request.user, sheet):
        return redirect(_sheet_url(request, sheet, "sheet_review"))
    return render(request, DETAIL_TEMPLATE, {
        "sheet": sheet, "summary": summary_for(sheet), **_danger_flags(request, sheet),
    })


# ----------------------------------------------------------------- review
@attendance_screen
@require_http_methods(["GET"])
def sheet_review(request, pk):
    """Read-only review of a draft or an uploaded sheet: the same content the print page will show.

    Names and where each person belongs, the first timers, and the summary table. Never a birth date, phone
    number or address. A coordinator also gets the upload button here, since review comes right before it.
    """
    sheet = _open_sheet(request, pk)
    if sheet is None:
        return _gone(request)
    if not permissions.can_review(request.user, sheet):
        messages.error(request, "You cannot review this sheet.")
        return _back(request, sheet)
    members, first_timers = _sheet_people(sheet)
    summary = summary_for(sheet)
    can_upload = permissions.can_upload(request.user, sheet)
    return render(request, REVIEW_TEMPLATE, {
        "sheet": sheet,
        "summary": summary,
        "table": summary_table(summary.summary),
        "members": members,
        "first_timers": first_timers,
        "needs_category": len(incomplete_rows(sheet)) if sheet.status == Attendance.Status.DRAFT else 0,
        "can_edit": permissions.can_edit_draft(request.user, sheet),
        "can_upload": can_upload,
        "can_reopen": permissions.can_reopen(request.user, sheet),
        "can_print": permissions.can_print(request.user, sheet),
        "upload_blocker": (
            "Some people still need a gender and an age network." if summary.incomplete
            else "Mark at least one person before uploading." if summary.total_present == 0 else ""
        ),
        "submit_token": new_submission_token() if (can_upload or permissions.can_reopen(request.user, sheet)) else "",
        # Brick 10: the convert buttons on the first-timer lines.
        "can_convert": permissions.can_convert(request.user, sheet),
        "convert_token": new_submission_token() if permissions.can_convert(request.user, sheet) else "",
        **_danger_flags(request, sheet),
    })


# ------------------------------------------------------------------ print
#: Short column heads of the paper sheet, by age network code (the full words are in the legend).
PRINT_HEADS = {"adult": "Adult", "young_adult": "YA", "youth": "Youth", "children": "C"}


def _print_rows(sheet):
    """The numbered lines of the printed grid: people by name, then first timers by name.

    Each line is a dict with `name`, `belongs` (the home extension, only for a visitor, else blank),
    `first_timer`, `guest`, `needs_category` and `cells`: eight booleans, men then women, each in age-network order,
    so the template only draws a tick where a cell is true. Only names, home extension, gender and age
    network are read: never a birth date, phone number or address.
    """
    genders = (Gender.MALE.value, Gender.FEMALE.value)
    rows = []
    members = AttendanceMember.objects.filter(attendance=sheet).order_by("member_name", "pk")
    for row in members:
        _decorate_member(row, sheet)
        rows.append({
            "name": row.member_name,
            "first_timer": False,
            "guest": row.person_type == PersonType.GUEST,
            "needs_category": row.needs_category,
            "gender": row.gender,
            "network": row.age_network,
        })
    first_timers = list(AttendanceFirstTimer.objects.filter(attendance=sheet).order_by("last_name", "first_name", "pk"))
    returning = _returning_first_timers(sheet, first_timers)
    for row in first_timers:
        again = row.pk in returning
        rows.append({
            "name": row.full_name, "first_timer": not again, "guest": again, "needs_category": False,
            "gender": row.gender, "network": row.age_network,
        })
    for number, line in enumerate(rows, start=1):
        line["number"] = number
        line["cells"] = [
            line["gender"] == gender and line["network"] == network
            for gender in genders for network in AgeNetwork.values
        ]
    return rows


@attendance_screen
@require_http_methods(["GET"])
def sheet_print(request, pk):
    """The standalone A4 page for the browser's print dialog ("Save as PDF" is a print destination).

    Opens for anyone who may open the sheet (`can_print`, same rule as `can_view`): a special member
    gets drafts only. A draft prints with a plain "DRAFT, not uploaded" line; an uploaded sheet prints
    its FROZEN numbers (`summary_for`). Read-only: it changes nothing and logs nothing.
    """
    sheet = _open_sheet(request, pk)
    if sheet is None:
        return _gone(request)
    if not permissions.can_print(request.user, sheet):
        messages.error(request, "You cannot print this sheet.")
        return _back(request, sheet)
    summary = summary_for(sheet)
    members, first_timers = _sheet_people(sheet)
    return render(request, PRINT_TEMPLATE, {
        "sheet": sheet,
        "summary": summary,
        "simple_summary": build_simple_summary(members, first_timers),
        "rows": _print_rows(sheet),
        "heads": [(code, PRINT_HEADS.get(code, code)) for code in AgeNetwork.values],
        "back_url": _sheet_url(request, sheet, "sheet_review" if permissions.can_review(request.user, sheet) else "sheet_detail"),
    })


# ---------------------------------------------------------- actions on a draft
@sheet_action("#find")
def sheet_mark(request, sheet):
    """Tick one person present (a search result, or "yes, same person" from the first-timer prompt).

    `gender` and `age_network` are optional posted values that only fill what the person's own record lacks
    (stored on the row, never on the person). A second tick of the same person changes nothing.
    """
    person = User.objects.filter(pk=_to_int(request.POST.get("user_id"))).first()
    if person is None:
        raise ServiceError("That person was not found.")
    row, created = services.mark_present(
        request.user, sheet, person,
        gender=request.POST.get("gender") or None, age_network=request.POST.get("age_network") or None,
    )
    if created:
        messages.success(request, f"Marked present: {row.member_name}.")
    else:
        messages.info(request, f"{row.member_name} is already on the sheet.")


@sheet_action("#marked")
def sheet_unmark(request, sheet):
    """Take one ticked person off the sheet (a mis-tap)."""
    if services.unmark_present(request.user, sheet, _to_int(request.POST.get("member_id"))):
        messages.success(request, "Taken off the sheet.")
    else:
        messages.info(request, "That person was not on the sheet.")


@sheet_action("#marked")
def sheet_member_category(request, sheet):
    """Save the gender and/or age network picked inline on one ticked row (the row only, never the person).

    A member with an account gets their gender and age network from the account, so a value the account
    already has is never changed here; only a missing one can be filled in.
    """
    member_id = _to_int(request.POST.get("member_id"))
    gender = request.POST.get("gender") or None
    age_network = request.POST.get("age_network") or None
    row = AttendanceMember.objects.filter(pk=member_id, attendance=sheet).first()
    if row is not None and row.person_type == PersonType.MEMBER_ACCOUNT:
        if row.gender in Gender.values:
            gender = None
        if row.age_network in AgeNetwork.values:
            age_network = None
        if gender is None and age_network is None:
            raise ServiceError("The gender and age network of a member with an account come from the account.")
    services.set_member_category(request.user, sheet, member_id, gender=gender, age_network=age_network)
    messages.success(request, "Saved.")


@sheet_action("#first-timers")
def sheet_first_timer_remove(request, sheet):
    """Take one first timer off the sheet."""
    if services.remove_first_timer(request.user, sheet, _to_int(request.POST.get("first_timer_id"))):
        messages.success(request, "First timer removed.")
    else:
        messages.info(request, "That first timer was not on the sheet.")


@sheet_action("#first-timers")
def sheet_first_timer_edit(request, sheet):
    """Save the name, gender and age network of one first timer or returning guest (this sheet's row only)."""
    row = services.edit_first_timer(
        request.user, sheet, _to_int(request.POST.get("first_timer_id")),
        first_name=request.POST.get("first_name", ""), last_name=request.POST.get("last_name", ""),
        gender=request.POST.get("gender") or None, age_network=request.POST.get("age_network") or None,
    )
    messages.success(request, f"Saved: {row.full_name}.")


@attendance_screen
@require_http_methods(["POST"])
def sheet_first_timer_add(request, pk):
    """Add a first timer. May talk back instead of redirecting, so it is not a plain `sheet_action`.

    Three outcomes: saved (toast and redirect); a form or service refusal (the workspace again, with the
    errors beside the fields and the typing kept); or `add_first_timer` found people with the same name and
    saved nothing (the workspace again, with the "same person?" prompt on top).
    """
    sheet = _open_sheet(request, pk)
    if sheet is None:
        return _gone(request)
    form = FirstTimerForm(request.POST)
    pending = None
    if form.is_valid():
        data = form.cleaned_data
        try:
            result = services.add_first_timer(
                request.user, sheet, first_name=data["first_name"], last_name=data["last_name"],
                gender=data["gender"], age_network=data["age_network"] or None,
                birth_date=data["birth_date"], middle_name=data["middle_name"], suffix=data["suffix"],
                phone_number=data["phone_number"], confirm_new=data["confirm_new"],
            )
        except PermissionDenied:
            messages.error(request, "You cannot do that on this sheet.")
            return _back(request, sheet)
        except ServiceError as exc:
            sheet.refresh_from_db()
            if sheet.status != Attendance.Status.DRAFT:  # uploaded while this page was open
                messages.error(request, str(exc))
                return _back(request, sheet)
            form.add_error(None, str(exc))
        else:
            if result.needs_confirmation:
                for match in result.matches:
                    match["age_label"] = AGE_LABELS.get(match["age_network"], "")
                    match["person_type_label"] = {
                        PersonType.GUEST: "Guest",
                        PersonType.MEMBER_RECORD: "Member (no account)",
                        PersonType.MEMBER_ACCOUNT: "Member",
                    }.get(match["person_type"], "")
                pending = {
                    "name": f"{data['first_name']} {data['last_name']}".strip(),
                    "matches": result.matches,
                    "previous_visits": result.previous_visits,
                    "gender": data["gender"],
                    "age_network": data["age_network"],
                    # Posted back by "no, a new person", so nothing the taker typed is lost.
                    "fields": [(name, form.data.get(name, "")) for name in FirstTimerForm.REPOST_FIELDS],
                }
            else:
                messages.success(request, f"First timer added: {result.first_timer.full_name}.")
                if result.previous_visits:  # only after "no, a new person": still worth a nudge
                    messages.info(request, "This name visited before. You can promote that earlier guest and keep the visit together.")
                return _back(request, sheet, "#first-timers")
    return _render_workspace(request, sheet, form=form, pending=pending)


@sheet_action()
def sheet_upload(request, sheet):
    """Save the reviewed draft as the locked record (the coordinator; see `services.upload_sheet`).

    The form carries a one-time token, so a double tap uploads once and the repeat is quiet. On success the
    person lands on the read-only review of the now-locked sheet.
    """
    result = services.upload_sheet(
        request.user, sheet.pk, token=request.POST.get("submit_token", ""), request=request,
    )
    if result.duplicate:
        messages.info(request, "This sheet was already uploaded.")
    else:
        messages.success(request, f"Uploaded: {result.sheet.total_present} present.")


@sheet_action()
def sheet_reopen(request, sheet):
    """Send an uploaded sheet back to draft so it can be corrected (admin or super-admin; `services.reopen_sheet`).

    The only way to edit an uploaded sheet: reopen, change it on the workspace, and the coordinator uploads
    again. The confirm dialog is a UX guard; the service enforces the rule. A one-time token makes a double tap
    reopen once. The admins are alerted by the audit listener, not here. Lands on the workspace of the draft.
    """
    result = services.reopen_sheet(
        request.user, sheet.pk, token=request.POST.get("submit_token", ""), request=request,
    )
    if result.duplicate:
        messages.info(request, "This sheet was already reopened.")
    else:
        messages.success(request, "Sheet reopened. Correct it, then the coordinator uploads it again.")


# ------------------------------------------------------- archive and force delete
@sheet_action()
def sheet_archive(request, sheet):
    """Archive a draft or uploaded sheet (`services.archive_sheet`; a coordinator: a draft of their own extension).

    The confirm dialog is a UX guard; the service enforces the rule. No token: archiving twice is harmless (the
    repeat gets "already archived"). Lands on the list it came from (the sheet is now hidden unless the Archived
    filter is on), or the plain list when it came from the sheet's own page.
    """
    services.archive_sheet(request.user, sheet.pk, request=request)
    messages.success(request, f"Archived: {sheet.label}, {sheet.attendance_date:%d %b %Y}.")
    return _list_redirect(request)


@sheet_action()
def sheet_restore(request, sheet):
    """Bring an archived sheet back (`services.restore_sheet`; a coordinator: one archived as a draft).

    It returns as a draft or as uploaded, whatever it was when archived. From the list the person stays on the
    list; from the sheet's own page they land on the restored sheet.
    """
    restored = services.restore_sheet(request.user, sheet.pk, request=request)
    messages.success(
        request,
        f"Restored: {restored.label}, {restored.attendance_date:%d %b %Y} ({restored.get_status_display().lower()}).",
    )
    return _list_redirect(request) if _list_next(request) else None


@sheet_action()
def sheet_force_delete(request, sheet):
    """Permanently delete an ARCHIVED sheet (`services.force_delete_sheet`; admin or super-admin only).

    A draft or uploaded sheet is refused by the service ("archive it first"). Nothing can be undone, so the
    confirm dialog is marked as danger. Lands on the list it came from, else the plain list.
    """
    label, day = sheet.label, sheet.attendance_date
    services.force_delete_sheet(request.user, sheet.pk, request=request)
    messages.success(request, f"Deleted permanently: {label}, {day:%d %b %Y}.")
    return _list_redirect(request)


# ---------------------------------------------------------- promote first timer
@sheet_action("#first-timers")
def sheet_first_timer_convert(request, sheet):
    """Promote a first timer's guest record to a member without creating a sign-in account.

    The first timer may be on THIS sheet or an EARLIER one (from the "visited before" prompt); the service
    checks permission on the first timer's own sheet. A one-time token makes a double tap promote once.
    With `mark_here` (the prompt for an earlier visit) the person is also ticked present on this draft.
    """
    result = services.convert_first_timer(
        request.user, _to_int(request.POST.get("first_timer_id")),
        token=request.POST.get("submit_token", ""), request=request,
    )
    if result.duplicate:
        messages.warning(request, "That form was already used. Reload the page and try again.")
        return None
    member = result.user
    if request.POST.get("mark_here") and sheet.status == Attendance.Status.DRAFT:
        try:
            services.mark_present(
                request.user, sheet, member,
                gender=request.POST.get("gender") or None, age_network=request.POST.get("age_network") or None,
            )
        except (ServiceError, PermissionDenied) as exc:
            messages.warning(request, f"The person was promoted, but they were not ticked present: {exc}")
    return render(request, CONVERT_RESULT_TEMPLATE, {
        "sheet": sheet, "member": member, "sheet_url": _sheet_url(request, sheet),
    })


# ------------------------------------------------------- my attendance (brick 14)
@login_required
@never_cache
@require_http_methods(["GET"])
def my_attendance(request):
    """The signed-in person's own attendance history: every role, no sheet access needed.

    Mounted ONLY at the member root (`attendance:mine`), never under a role area, so it never mixes with
    sheet management and skips `attendance_screen` on purpose (that guard is about sheets). It reads
    `request.user` and nothing else: no id comes from the URL, the query string or a form, so nobody can
    open someone else's history. Anonymous visitors go to sign in; archived or suspended people cannot
    sign in at all. Uploaded sheets only (`services.member_attendance_history`).
    """
    entries = services.member_attendance_history(request.user)
    page = Paginator(entries, MINE_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, MINE_TEMPLATE, {"page": page, "total": len(entries), "querystring": ""})