"""tithes.views: the tithes screens, shared by every role (brick 3).

Same shape as `attendance.views`: ONE set of views serves four places, `superadmin`, `admin_area`,
`coordinator_area` (the role areas) and `tithes` (the member root, for tithes special members). Every redirect
follows `request.resolver_match.namespace`; templates do the same through `{% role_url %}`.

Three guards, all required (hiding a button is not access control):
  1. `tithes_screen`: signed in (`role_required`), the right AREA for the person's role (`AREA_ROLES`), the
     page-level gate `permissions.can_use_area`, and `never_cache`.
  2. Every report starts from `TithesOffering.objects.for_user(actor)` (`_open_report`): a report out of reach
     looks exactly like one that does not exist.
  3. Every service checks permission again on the LOCKED row.

Views stay thin: validate a small form, call ONE service, show a toast. A refusal (`ServiceError`, or
`PermissionDenied` turned into a message) goes back to the report, which then shows the truth. The server is
the source of truth for every number (`services.build_totals`); Alpine in the workspace only previews.
PRIVACY: nothing here writes an amount, a bypass message or a decision note anywhere but the screen.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import home_url_for, role_required
from apps.accounts.models import Extension, Role, Status, User
from apps.attendance.models import Attendance
from apps.core.services import ServiceError, new_submission_token

from . import permissions, services
from .forms import (
    BreakdownForm, DecisionForm, EntryForm, EntryRemoveForm, EntryUpdateForm, FinalizeForm, HouseholdForm,
    HouseholdMemberForm, OfferingForm, ReopenForm, ReportFilterForm, RequestForm, WithdrawForm,
)
from .models import (
    TitheBypassRequest, TitheEntry, TitheEntryGiver, TitheHousehold, TitheHouseholdMember, TithesOffering,
)

PAGE_SIZE = 25
LIST_TEMPLATE = "tithes/report_list.html"
WORKSPACE_TEMPLATE = "tithes/report_workspace.html"
REVIEW_TEMPLATE = "tithes/report_review.html"
HOUSEHOLD_TEMPLATE = "tithes/household_list.html"
PENDING_TEMPLATE = "tithes/pending_requests.html"
PRINT_TEMPLATE = "tithes/print.html"
MINE_TEMPLATE = "tithes/mine.html"
MINE_PAGE_SIZE = 25

ADMIN_ROLES = (Role.SUPER_ADMIN, Role.ADMIN)

# Which role each URL namespace belongs to. Keep in step with church_management/urls.py and the role url modules.
AREA_ROLES = {
    "superadmin": Role.SUPER_ADMIN,
    "admin_area": Role.ADMIN,
    "coordinator_area": Role.COORDINATOR,
    "tithes": Role.MEMBER,
}
#: The reverse: the namespace each role works in.
ROLE_AREAS = {role: namespace for namespace, role in AREA_ROLES.items()}


# ============================================================================ guards and small helpers
def tithes_screen(view):
    """Guard for every tithes page: signed in, in the right area, allowed into tithes, never cached.

    A person in the wrong area goes to their own home page, with ONE exception: a link to a single report
    (`report_detail`), such as the one in a bypass notification (`/tithes/<id>/`, written without knowing the
    reader's role), is forwarded to the same report in the reader's own area. A person without access to the
    tithes screens gets a toast saying why.
    """

    @wraps(view)
    def guarded(request, *args, **kwargs):
        namespace = request.resolver_match.namespace
        if AREA_ROLES.get(namespace) != request.user.role:
            own = ROLE_AREAS.get(request.user.role)
            if own and request.resolver_match.url_name == "report_detail" and "pk" in kwargs:
                return redirect(f"{own}:report_detail", pk=kwargs["pk"])
            return redirect(home_url_for(request.user))
        if not permissions.can_use_area(request.user):
            messages.error(request, "You do not have access to tithes.")
            return redirect(home_url_for(request.user))
        return view(request, *args, **kwargs)

    return role_required(*AREA_ROLES.values())(never_cache(guarded))


def _to(request, name, **kwargs):
    """Redirect to url `name` in the area the request came from."""
    return redirect(f"{request.resolver_match.namespace}:{name}", **kwargs)


def _url(request, name, *args):
    """URL of `name` in the area the request came from."""
    return reverse(f"{request.resolver_match.namespace}:{name}", args=args)


def _gone(request):
    """The answer for a report that is missing OR out of reach (the two look the same on purpose)."""
    messages.info(request, "That report no longer exists.")
    return _to(request, "report_list")


def _open_report(request, pk):
    """The report with this id IF the person may open it (always starts from `for_user`), else None."""
    return (
        TithesOffering.objects.for_user(request.user)
        .select_related("attendance__extension", "finalized_by")
        .filter(pk=pk).first()
    )


def _to_int(value):
    """An int from a posted value, or None when it is not a whole number (never raises)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ints(values):
    """The whole numbers in `values`, in order, without repeats (bad entries are skipped)."""
    return list(dict.fromkeys(n for n in (_to_int(v) for v in values) if n is not None))


def _form_error(request, form):
    """Show the first error of an invalid form as a toast. Returns None so a view can `return` it."""
    for errors in form.errors.values():
        messages.error(request, errors[0])
        break
    return None


def _taken_ids(report):
    """Ids of the people already on the report, as giver or co-giver (read-only, for the picker)."""
    giving = TitheEntry.objects.filter(tithes_offering=report, user__isnull=False).values_list("user_id", flat=True)
    sharing = TitheEntryGiver.objects.filter(
        entry__tithes_offering=report, user__isnull=False,
    ).values_list("user_id", flat=True)
    return set(giving) | set(sharing)


def _count_of(value):
    """A stored denomination count as a non-negative int (anything odd reads as 0)."""
    number = _to_int(value)
    return number if number and number > 0 else 0


def _sheet_link(request, sheet):
    """URL of the attendance sheet in this area, or None for the member root (the seat there is a different one)."""
    namespace = request.resolver_match.namespace
    if namespace == "tithes":
        return None
    try:
        return reverse(f"{namespace}:sheet_detail", kwargs={"pk": sheet.pk})
    except NoReverseMatch:
        return None


def report_action(anchor=""):
    """Decorator for the POST-only views that act on one report: load it, run the view, report refusals.

    The decorated function takes `(request, report)`, calls ONE service and adds its own success message,
    then returns None (the person goes back to the report, at `anchor`) or a response of its own.
    A `ServiceError` becomes an error toast; a `PermissionDenied` becomes a plain "cannot do that" toast
    (not a bare 403 page). A report out of reach looks deleted.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(request, pk):
            report = _open_report(request, pk)
            if report is None:
                return _gone(request)
            try:
                response = view(request, report)
            except ServiceError as exc:
                messages.error(request, str(exc))
            except PermissionDenied:
                messages.error(request, "You cannot do that on this report.")
            else:
                if response is not None:
                    return response
            return _to(request, "report_detail", pk=report.pk) if not anchor else redirect(
                _url(request, "report_detail", report.pk) + anchor
            )

        return tithes_screen(require_http_methods(["POST"])(wrapped))

    return decorator


# ============================================================================ list
def _listable_sheets(user):
    """Attendance sheets the person may see on the tithes list (not archived), narrowed by who is asking.

    Admins: every sheet. Coordinator: their extension. Special member: the extension(s) of their seat, and
    only sheets with no report yet or a DRAFT one. `permissions.can_create` / `can_view` still decide what
    each row offers, and every service checks again.
    """
    sheets = Attendance.objects.exclude(status=Attendance.Status.ARCHIVED)
    if user.role in ADMIN_ROLES:
        return sheets
    if user.role == Role.COORDINATOR:
        return sheets.filter(extension_id=user.extension_id)
    return sheets.filter(extension_id__in=permissions.seat_extension_ids(user)).filter(
        Q(tithes_offering__isnull=True) | Q(tithes_offering__status=TithesOffering.Status.DRAFT)
    )


def _decorate_sheet(sheet, user):
    """Attach `report`, `can_open` and `can_start` to a listed sheet (hints only; the services check again)."""
    try:
        report = sheet.tithes_offering
    except ObjectDoesNotExist:
        report = None
    if report is not None:
        report.attendance = sheet  # already loaded: saves a query in every permission check
    sheet.report = report
    sheet.can_open = bool(report and permissions.can_view(user, report))
    sheet.can_start = report is None and permissions.can_create(user, sheet)
    return sheet


@tithes_screen
@require_http_methods(["GET"])
def report_list(request):
    """Sheets with the state of their tithes report, newest first, with a Start button where none exists."""
    user = request.user
    is_admin = user.role in ADMIN_ROLES
    form = ReportFilterForm(request.GET)
    if not is_admin:
        del form.fields["extension"]
    if user.role == Role.MEMBER:
        del form.fields["state"]  # a special member only ever reaches drafts
    form.is_valid()
    data = getattr(form, "cleaned_data", {})
    state = data.get("state") or "all"

    open_request = TitheBypassRequest.objects.filter(
        tithes_offering__attendance=OuterRef("pk"), status__in=TitheBypassRequest.OPEN_STATUSES,
    )
    sheets = (
        _listable_sheets(user).select_related("extension", "tithes_offering").annotate(bypass_open=Exists(open_request))
    )
    if state == "none":
        sheets = sheets.filter(tithes_offering__isnull=True)
    elif state in ("draft", "finalized"):
        sheets = sheets.filter(tithes_offering__status=state)
    if data.get("extension") is not None:
        sheets = sheets.filter(extension=data["extension"])
    if data.get("date_from"):
        sheets = sheets.filter(attendance_date__gte=data["date_from"])
    if data.get("date_to"):
        sheets = sheets.filter(attendance_date__lte=data["date_to"])
    sheets = sheets.order_by("-attendance_date", "label", "pk")

    paginator = Paginator(sheets, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    page.object_list = [_decorate_sheet(sheet, user) for sheet in page.object_list]
    without_page = request.GET.copy()
    without_page.pop("page", None)
    can_households = is_admin or permissions.can_manage_households(user, user.extension_id)
    return render(request, LIST_TEMPLATE, {
        "form": form,
        "page": page,
        "total": paginator.count,
        "querystring": without_page.urlencode(),
        "show_extension": is_admin,
        "active_filters": sum([state != "all", data.get("extension") is not None,
                               bool(data.get("date_from")), bool(data.get("date_to"))]),
        "can_households": can_households,
        "pending_count": (
            TitheBypassRequest.objects.filter(status=TitheBypassRequest.Status.PENDING).count() if is_admin else 0
        ),
    })


@tithes_screen
@require_http_methods(["POST"])
def report_create(request, sheet_pk):
    """Start the report of one sheet (POST) and open its workspace. A double tap is refused by the service."""
    sheet = _listable_sheets(request.user).filter(pk=sheet_pk).first()
    if sheet is None:
        messages.info(request, "That sheet no longer exists.")
        return _to(request, "report_list")
    try:
        report = services.create_report(request.user, sheet)
    except ServiceError as exc:
        messages.error(request, str(exc))
        return _to(request, "report_list")
    except PermissionDenied:
        messages.error(request, "You cannot start a tithes report for this sheet.")
        return _to(request, "report_list")
    messages.success(request, "Tithes report started.")
    return _to(request, "report_detail", pk=report.pk)


# ============================================================================ the report pages
def _report_context(request, report):
    """Everything the workspace and the review share: numbers, rows, flags and one-time tokens.

    A draft shows the LIVE numbers (`build_totals`); a finalized report shows its FROZEN numbers. The flags
    come from `tithes.permissions` (the same functions the services use) and are only hints for the screen.
    """
    user = request.user
    draft = report.status == TithesOffering.Status.DRAFT
    totals = services.build_totals(report) if draft else services.frozen_totals_for(report)
    entries = list(report.entries.prefetch_related("co_givers").order_by("giver_name", "pk"))
    stored = report.denomination_breakdown or {}
    denominations = []
    for value in services.DENOMINATIONS:
        count = _count_of(stored.get(str(value)))
        denominations.append({"value": value, "count": count, "line": value * count})

    open_request = report.bypass_requests.filter(
        status__in=TitheBypassRequest.OPEN_STATUSES).select_related("requested_by").first()
    last_request = open_request or report.bypass_requests.order_by("-pk").first()
    is_admin = user.role in ADMIN_ROLES
    may_request = permissions.can_request_bypass(user, report)
    can_finalize = permissions.can_finalize(user, report)
    can_decide = (
        permissions.can_decide_bypass(user, report)
        and open_request is not None and open_request.status == TitheBypassRequest.Status.PENDING
    )
    approved = bool(
        open_request and open_request.status == TitheBypassRequest.Status.APPROVED
        and open_request.difference == totals.difference
    )
    can_request = may_request and draft and not totals.balanced and not totals.is_empty and open_request is None
    can_withdraw = may_request and open_request is not None
    can_reopen = permissions.can_reopen(user, report)
    return {
        "report": report,
        "sheet": report.attendance,
        "sheet_url": _sheet_link(request, report.attendance),
        "totals": totals,
        "difference_abs": abs(totals.difference),
        "entries": entries,
        "denominations": denominations,
        "is_draft": draft,
        "is_admin": is_admin,
        "open_request": open_request,
        "last_request": last_request,
        # The message and the note are shown only to the people who file or decide a request.
        "can_see_request": may_request or permissions.can_decide_bypass(user, report),
        "locked_for_you": draft and open_request is not None and not is_admin,
        "can_edit": permissions.can_edit_draft(user, report),
        "can_finalize": can_finalize,
        "finalize_ready": totals.balanced or approved or is_admin,
        "has_approved": approved,
        "can_request": can_request,
        "can_withdraw": can_withdraw,
        "can_decide": can_decide,
        "can_reopen": can_reopen,
        # Brick 4: the "Print or save as PDF" button on the review page (opens the A4 page in a new tab).
        "can_print": permissions.can_print(user, report),
        "finalize_token": new_submission_token() if can_finalize else "",
        "request_token": new_submission_token() if can_request else "",
        "decision_token": new_submission_token() if can_decide else "",
        "reopen_token": new_submission_token() if can_reopen else "",
    }


@tithes_screen
@require_http_methods(["GET"])
def report_detail(request, pk):
    """One report: the editable workspace for a draft the person may edit, the review for everything else."""
    report = _open_report(request, pk)
    if report is None:
        return _gone(request)
    if permissions.can_edit_draft(request.user, report):
        context = _report_context(request, report)
        # Read once by the workspace script; money travels as text so a float never touches an amount.
        context["ws_config"] = {
            "offering": str(context["totals"].offering),
            "tithes": str(context["totals"].tithes),
            "denoms": list(services.DENOMINATIONS),
            "counts": {str(row["value"]): row["count"] for row in context["denominations"]},
            "present": services.present_candidates(report),
            "taken": sorted(_taken_ids(report)),
            "searchUrl": _url(request, "giver_search", report.pk),
            "prefillUrl": _url(request, "giver_prefill", report.pk),
        }
        return render(request, WORKSPACE_TEMPLATE, context)
    if permissions.can_review(request.user, report):
        return _to(request, "report_review", pk=report.pk)
    messages.info(request, "That report is closed because its sheet is archived.")
    return _to(request, "report_list")


@tithes_screen
@require_http_methods(["GET"])
def report_review(request, pk):
    """Read-only view of a report (a finalized one, or a draft that is locked for this person)."""
    report = _open_report(request, pk)
    if report is None:
        return _gone(request)
    if not permissions.can_review(request.user, report):
        messages.error(request, "You cannot review this report.")
        return _to(request, "report_list")
    return render(request, REVIEW_TEMPLATE, _report_context(request, report))


# ============================================================================ print
def _print_rows(report):
    """The numbered lines of the printed grid: one per envelope, `{"number", "name", "amount"}`.

    A shared envelope prints ONCE with every name on it ("Juan Cruz & Maria Cruz") and its full amount.
    Only names and amounts are read: never a birth date, phone number or address.
    """
    entries = report.entries.prefetch_related("co_givers").order_by("giver_name", "pk")
    return [
        {"number": number, "name": services.entry_names(entry), "amount": entry.amount}
        for number, entry in enumerate(entries, start=1)
    ]


@tithes_screen
@require_http_methods(["GET"])
def report_print(request, pk):
    """The standalone A4 page for the browser's print dialog ("Save as PDF" is a print destination).

    Opens for anyone who may open the report (`can_print`, same rule as `can_view`): a special member gets
    drafts only. A finalized report prints its FROZEN numbers (`frozen_totals_for`); a draft counts live
    (`build_totals`) and prints "DRAFT, not finalized". Read-only: it changes nothing and logs nothing.
    """
    report = _open_report(request, pk)
    if report is None:
        return _gone(request)
    if not permissions.can_print(request.user, report):
        messages.error(request, "You cannot print this report.")
        return _to(request, "report_list")
    draft = report.status == TithesOffering.Status.DRAFT
    totals = services.build_totals(report) if draft else services.frozen_totals_for(report)
    stored = report.denomination_breakdown or {}
    denominations = []
    for value in services.DENOMINATIONS:
        count = _count_of(stored.get(str(value)))
        denominations.append({"value": value, "count": count, "line": value * count})
    back = "report_review" if permissions.can_review(request.user, report) else "report_detail"
    return render(request, PRINT_TEMPLATE, {
        "report": report,
        "sheet": report.attendance,
        "is_draft": draft,
        "totals": totals,
        "difference_abs": abs(totals.difference),
        "rows": _print_rows(report),
        "denominations": denominations,
        "bypass_note": services.bypass_note_for(report),
        "back_url": _url(request, back, report.pk),
    })


# ============================================================================ giver picker (JSON)
@tithes_screen
@require_http_methods(["GET"])
def giver_search(request, pk):
    """JSON `{"results": [...]}` of members matching `?q=` for the giver picker (id, name, home extension only)."""
    report = _open_report(request, pk)
    if report is None:
        return JsonResponse({"results": [], "error": "gone"}, status=404)
    try:
        results = services.search_givers(request.user, report, request.GET.get("q", ""))
    except (ServiceError, PermissionDenied):
        return JsonResponse({"results": [], "error": "not available"}, status=403)
    return JsonResponse({"results": results})


@tithes_screen
@require_http_methods(["GET"])
def giver_prefill(request, pk):
    """JSON `{"results": [{id, name}]}`: the giver's household mates, minus anyone already on the report."""
    report = _open_report(request, pk)
    if report is None or not permissions.can_edit_draft(request.user, report):
        return JsonResponse({"results": []}, status=404)
    user_id = _to_int(request.GET.get("user"))
    mates = services.household_prefill(user_id) if user_id else []
    taken = _taken_ids(report)
    return JsonResponse({"results": [mate for mate in mates if mate["id"] not in taken]})


# ============================================================================ actions on a draft
@report_action("#numbers")
def report_offering(request, report):
    """Save the offering (one number)."""
    form = OfferingForm(request.POST)
    if not form.is_valid():
        return _form_error(request, form)
    services.set_offering(request.user, report, form.cleaned_data["amount"] or "0")
    messages.success(request, "Offering saved.")


@report_action("#breakdown")
def report_breakdown(request, report):
    """Save the bills-and-coins counts."""
    form = BreakdownForm(request.POST)
    if not form.is_valid():
        return _form_error(request, form)
    services.set_breakdown(request.user, report, form.counts())
    messages.success(request, "Bills and coins saved.")


@report_action("#entries")
def entry_add(request, report):
    """Add one envelope: giver, amount and the co-givers left ticked in the picker."""
    form = EntryForm(request.POST)
    if not form.is_valid():
        return _form_error(request, form)
    services.add_entry(
        request.user, report, form.cleaned_data["giver"], form.cleaned_data["amount"],
        _ints(request.POST.getlist("co_giver")),
    )
    messages.success(request, "Tithe added.")


@report_action("#entries")
def entry_update(request, report):
    """Change the amount of one entry (to change the giver or co-givers, remove it and add it again)."""
    form = EntryUpdateForm(request.POST)
    if not form.is_valid():
        return _form_error(request, form)
    services.update_entry(request.user, report, form.cleaned_data["entry_id"], amount=form.cleaned_data["amount"])
    messages.success(request, "Tithe updated.")


@report_action("#entries")
def entry_remove(request, report):
    """Take one entry off the draft."""
    form = EntryRemoveForm(request.POST)
    if not form.is_valid():
        return _form_error(request, form)
    if services.remove_entry(request.user, report, form.cleaned_data["entry_id"]):
        messages.success(request, "Tithe removed.")
    else:
        messages.info(request, "That tithe was already removed.")


# ============================================================================ bypass, finalize, reopen
def _own_request(report, request_id):
    """The id back if the request belongs to THIS report, else a ServiceError (the service checks the rest)."""
    if not report.bypass_requests.filter(pk=request_id).exists():
        raise ServiceError("That bypass request no longer exists.")
    return request_id


@report_action("#bypass")
def bypass_request(request, report):
    """The coordinator files a bypass request with a required message."""
    form = RequestForm(request.POST)
    if not form.is_valid():
        return _form_error(request, form)
    result = services.request_bypass(
        request.user, report.pk, message=form.cleaned_data["message"],
        token=form.cleaned_data["submit_token"], request=request,
    )
    if result.duplicate:
        messages.info(request, "That request was already sent.")
    else:
        messages.success(request, "Request sent to the admins. The draft is locked until it is decided or withdrawn.")


@report_action("#bypass")
def bypass_withdraw(request, report):
    """The coordinator takes back an open request (this unlocks the draft)."""
    form = WithdrawForm(request.POST)
    if not form.is_valid():
        return _form_error(request, form)
    services.withdraw_bypass(request.user, _own_request(report, form.cleaned_data["request_id"]), request=request)
    messages.success(request, "Request withdrawn. The draft is unlocked.")


@report_action("#bypass")
def bypass_decide(request, report):
    """An admin approves or rejects a pending request, with an optional note."""
    form = DecisionForm(request.POST)
    if not form.is_valid():
        return _form_error(request, form)
    data = form.cleaned_data
    result = services.decide_bypass(
        request.user, _own_request(report, data["request_id"]), approve=data["decision"] == "approve",
        note=data["note"], token=data["submit_token"], request=request,
    )
    if result.duplicate:
        messages.info(request, "That decision was already recorded.")
    elif result.voided:
        messages.warning(request, "The numbers changed after the request was sent, so it was voided. Nothing was decided.")
    elif data["decision"] == "approve":
        messages.success(request, "Request approved. The coordinator can now finalize once.")
    else:
        messages.success(request, "Request rejected.")


@report_action()
def report_finalize(request, report):
    """Freeze the report (balanced, with an approved request, or an admin's typed reason)."""
    form = FinalizeForm(request.POST)
    if not form.is_valid():
        return _form_error(request, form)
    result = services.finalize_report(
        request.user, report.pk, token=form.cleaned_data["submit_token"],
        bypass_reason=form.cleaned_data["bypass_reason"], request=request,
    )
    if result.duplicate:
        messages.info(request, "That was already submitted.")
    else:
        messages.success(request, "Report finalized. The numbers are now locked.")


@report_action()
def report_reopen(request, report):
    """Send a finalized report back to draft (admin and super-admin only)."""
    form = ReopenForm(request.POST)
    if not form.is_valid():
        return _form_error(request, form)
    result = services.reopen_report(request.user, report.pk, token=form.cleaned_data["submit_token"], request=request)
    if result.duplicate:
        messages.info(request, "That was already submitted.")
    else:
        messages.success(request, "Report reopened. It is a draft again.")


# ============================================================================ admin: pending requests
@tithes_screen
@require_http_methods(["GET"])
def pending_requests(request):
    """Requests waiting for a decision (admin and super-admin). Each row links to its report, where they decide."""
    if request.user.role not in ADMIN_ROLES:
        messages.error(request, "Only admins can decide bypass requests.")
        return _to(request, "report_list")
    waiting = (
        TitheBypassRequest.objects.filter(
            status=TitheBypassRequest.Status.PENDING,
            tithes_offering__in=TithesOffering.objects.for_user(request.user),
        )
        .select_related("tithes_offering__attendance__extension", "requested_by").order_by("pk")
    )
    return render(request, PENDING_TEMPLATE, {"requests": list(waiting)})


# ============================================================================ households
def _households_url(request, extension_id):
    """The household page of an extension (admins pick the extension with `?extension=`)."""
    url = _url(request, "household_list")
    return f"{url}?extension={extension_id}" if extension_id and request.user.role in ADMIN_ROLES else url


def _household_action(request, extension_id, run, success):
    """Run one household service call, show its toast and go back to the household page."""
    try:
        run()
    except ServiceError as exc:
        messages.error(request, str(exc))
    except PermissionDenied:
        messages.error(request, "You cannot manage households here.")
    else:
        messages.success(request, success)
    return redirect(_households_url(request, extension_id))


@tithes_screen
@require_http_methods(["GET"])
def household_list(request):
    """Households of one extension: the coordinator's own, or the one an admin picks. Add people, end memberships."""
    user = request.user
    is_admin = user.role in ADMIN_ROLES
    if not (is_admin or permissions.can_manage_households(user, user.extension_id)):
        messages.error(request, "You cannot manage households.")
        return _to(request, "report_list")
    extensions = list(Extension.objects.filter(archived_at__isnull=True).order_by("name")) if is_admin else []
    if is_admin:
        extension = next((e for e in extensions if e.pk == _to_int(request.GET.get("extension"))), None)
    else:
        extension = Extension.objects.filter(pk=user.extension_id, archived_at__isnull=True).first()
    context = {"extensions": extensions, "extension": extension, "households": [], "candidates": []}
    if extension is not None and permissions.can_manage_households(user, extension.pk):
        active = TitheHouseholdMember.objects.filter(ended_on__isnull=True).select_related("user")
        context["households"] = list(
            TitheHousehold.objects.filter(extension=extension)
            .prefetch_related(Prefetch("members", queryset=active, to_attr="active_members")).order_by("name", "pk")
        )
        # Active members of this extension who are in no household right now.
        in_household = TitheHouseholdMember.objects.filter(ended_on__isnull=True).values("user_id")
        context["candidates"] = list(
            User.objects.filter(extension=extension, status=Status.ACTIVE, person_type__in=services.GIVER_TYPES)
            .exclude(pk__in=in_household).order_by("last_name", "first_name", "pk")
        )
    return render(request, HOUSEHOLD_TEMPLATE, context)


@tithes_screen
@require_http_methods(["POST"])
def household_create(request):
    """Create a household in an extension (the service checks the right to do so)."""
    form = HouseholdForm(request.POST)
    if not form.is_valid():
        _form_error(request, form)
        return redirect(_households_url(request, None))
    extension_id = form.cleaned_data["extension"]
    return _household_action(
        request, extension_id,
        lambda: services.create_household(request.user, extension_id, name=form.cleaned_data["name"]),
        "Household created.",
    )


@tithes_screen
@require_http_methods(["POST"])
def household_member_add(request, pk):
    """Put a person in a household (they must belong to its extension and be in no other household)."""
    household = TitheHousehold.objects.filter(pk=pk).first()
    extension_id = household.extension_id if household else None
    form = HouseholdMemberForm(request.POST)
    if household is None or not form.is_valid():
        if household is not None:
            _form_error(request, form)
        else:
            messages.info(request, "That household no longer exists.")
        return redirect(_households_url(request, extension_id))
    return _household_action(
        request, extension_id,
        lambda: services.add_household_member(
            request.user, household, form.cleaned_data["user"], started_on=form.cleaned_data["started_on"],
        ),
        "Person added to the household.",
    )


@tithes_screen
@require_http_methods(["POST"])
def household_member_end(request, pk):
    """End one membership today. Past entries are never touched."""
    membership = TitheHouseholdMember.objects.select_related("household").filter(pk=pk).first()
    extension_id = membership.household.extension_id if membership else None
    return _household_action(
        request, extension_id, lambda: services.end_household_member(request.user, pk), "Membership ended.",
    )


# ============================================================================ my tithes
@login_required
@never_cache
@require_http_methods(["GET"])
def my_tithes(request):
    """The signed-in person's own giving history: every role, no report access needed.

    Mounted ONLY at the member root (`tithes:mine`), never under a role area, so it never mixes with report
    management and skips `tithes_screen` on purpose (that guard is about reports and seats). It reads
    `request.user` and nothing else: no id comes from the URL, the query string or a form, so nobody can open
    someone else's history. Finalized reports only (`services.member_tithe_history`); the full amount of a
    shared envelope is shown, never split.
    """
    entries = services.member_tithe_history(request.user)
    page = Paginator(entries, MINE_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, MINE_TEMPLATE, {"page": page, "total": len(entries), "querystring": ""})
