"""accounts.views.superadmin: the super-admin extension management screens.

Names shown in messages go through `title_case` because extension text is
stored in lowercase (see `core.text`).

Every view is wrapped in role_required(SUPER_ADMIN) (the page guard) and the
services check permission again (the action guard). Views stay thin: parse the
request, call a service, show a message, redirect. Destructive actions are
POST-only. A person double-clicking a button must never get an error page, so
actions on a record that is already in the wanted state (or already gone)
report that calmly instead of failing.
"""
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.services import ServiceError, new_submission_token
from apps.core.text import title_case

from ..decorators import role_required
from ..forms import (
    AddPeopleForm, AssignCoordinatorForm, ExtensionFilterForm, ExtensionForm,
)
from ..models import Extension, Role, Status, User
from ..services import (
    archive_extension,
    assign_coordinator,
    bulk_archive_extensions,
    bulk_delete_extensions,
    create_extension,
    force_delete_extension,
    restore_extension,
    unassign_coordinator,
    update_extension,
)
from ..services.users import transfer_people_in

PAGE_SIZE = 25
PEOPLE_PAGE_SIZE = 25     # people shown per page on the detail screen
ADD_OPTION_LIMIT = 1000   # most people offered in the "Add a person" datalist
BULK_LIMIT = 100          # most rows one bulk request will touch
NAMES_IN_MESSAGE = 3      # how many names a toast lists before "and N more"
LIST_PARAMS = ("q", "status", "sort", "page")

super_admin_only = role_required(Role.SUPER_ADMIN)


def _extension_or_none(pk):
    """The extension with this id, or None (it may have just been deleted)."""
    return Extension.objects.filter(pk=pk).first()


def _gone(request):
    """Calm answer for an extension that no longer exists."""
    messages.info(request, "That extension no longer exists.")
    return redirect("superadmin:extension_list")


def _list_query(source):
    """The list's own GET parameters (search, status, sort, page), re-encoded.

    Anything else is dropped, so the value is safe to send back to the page.
    """
    return urlencode({k: source.get(k, "") for k in LIST_PARAMS if source.get(k)})


def _list_url(source):
    """URL of the extension list with only the known list parameters kept."""
    query = _list_query(source)
    base = reverse("superadmin:extension_list")
    return f"{base}?{query}" if query else base


# ------------------------------------------------------------------- list
@super_admin_only
@require_http_methods(["GET"])
def extension_list(request):
    """Extensions with search, a status filter, sorting and paging."""
    form = ExtensionFilterForm(request.GET)
    form.is_valid()
    data = getattr(form, "cleaned_data", {})
    q = data.get("q", "")
    status = data.get("status") or "active"
    sort = data.get("sort") or "name"

    queryset = (
        Extension.objects.select_related("coordinator")
        .annotate(member_count=Count("members", filter=~Q(members__status=Status.ARCHIVED)))
    )
    if status == "active":
        queryset = queryset.filter(archived_at__isnull=True)
    elif status == "archived":
        queryset = queryset.filter(archived_at__isnull=False)
    if q:
        queryset = queryset.filter(
            Q(name__icontains=q) | Q(barangay__icontains=q) | Q(municipality__icontains=q)
        )
    queryset = queryset.order_by(sort, "pk")

    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "accounts/superadmin/extension_list.html", {
        "form": form,
        "page": page,
        "total": paginator.count,
        "status": status,
        "querystring": params.urlencode(),
        # How many filters differ from the defaults (badge on the Filters button).
        "active_filters": sum([bool(q), status != "active", sort != "name"]),
        # Sent back with a bulk action so the person returns to the same view.
        "current_query": _list_query(request.GET),
    })


# ----------------------------------------------------------- create / edit
@super_admin_only
@require_http_methods(["GET", "POST"])
def extension_create(request):
    """Show the new-extension form (GET) and save it (POST)."""
    form = ExtensionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            extension = create_extension(request.user, request, form.cleaned_data)
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{title_case(extension.name)} was created.")
            return redirect("superadmin:extension_detail", pk=extension.pk)
    return render(request, "accounts/superadmin/extension_form.html", {
        "form": form, "extension": None,
    })


@super_admin_only
@require_http_methods(["GET", "POST"])
def extension_edit(request, pk):
    """Show the edit form (GET) and save it (POST)."""
    extension = _extension_or_none(pk)
    if extension is None:
        return _gone(request)
    if extension.is_archived:
        messages.error(request, "Restore this extension before editing it.")
        return redirect("superadmin:extension_detail", pk=pk)

    form = ExtensionForm(request.POST or None, instance=extension)
    if request.method == "POST" and form.is_valid():
        try:
            updated, changed = update_extension(
                request.user, request, extension, form.cleaned_data
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            if changed:
                messages.success(request, f"{title_case(updated.name)} was updated.")
            else:
                messages.info(request, "No changes to save.")
            return redirect("superadmin:extension_detail", pk=pk)
    return render(request, "accounts/superadmin/extension_form.html", {
        "form": form, "extension": extension,
    })


# ----------------------------------------------------------------- detail
@super_admin_only
@require_http_methods(["GET"])
def extension_detail(request, pk):
    """One extension: details, coordinator, people (transfer in) and the action buttons."""
    extension = (
        Extension.objects.select_related("coordinator").filter(pk=pk).first()
    )
    if extension is None:
        return _gone(request)

    people = User.objects.filter(extension=extension).exclude(status=Status.ARCHIVED)
    paginator = Paginator(people.order_by("last_name", "first_name", "pk"), PEOPLE_PAGE_SIZE)
    people_page = paginator.get_page(request.GET.get("page"))

    add_options = []
    if not extension.is_archived:
        # Everyone who could be added, from any extension (the page asks before moving them).
        candidates = (
            User.objects.filter(role__in=[Role.MEMBER, Role.COORDINATOR])
            .exclude(status=Status.ARCHIVED)
            .exclude(extension=extension)
            .select_related("extension")
            .order_by("last_name", "first_name", "pk")[:ADD_OPTION_LIMIT]
        )
        add_options = [
            {
                "id": p.pk,
                "name": p.full_name,
                "account_id": p.account_id,
                "label": f"{p.full_name} ({p.account_id})",
                "ext": title_case(p.extension.name) if p.extension else "",
            }
            for p in candidates
        ]

    return render(request, "accounts/superadmin/extension_detail.html", {
        "extension": extension,
        "members": list(people_page.object_list),
        "people_page": people_page,
        "member_count": paginator.count,
        "archived_people_count": User.objects.filter(
            extension=extension, status=Status.ARCHIVED
        ).count(),
        "assign_form": (
            None if extension.is_archived
            else AssignCoordinatorForm(extension=extension)
        ),
        "add_options": add_options,
        "submit_token": new_submission_token(),
    })


# ---------------------------------------------------------------- actions
def _run_extension_action(request, pk, service, done, nothing, *, to_list=False):
    """Run one POST action on an extension and answer with a message.

    done / nothing are message templates using {name}. `nothing` is shown when
    the record was already in the wanted state (for example a second click).
    """
    extension = _extension_or_none(pk)
    if extension is None:
        return _gone(request)
    name = title_case(extension.name)
    try:
        _, changed = service(request.user, request, extension)
    except ServiceError as exc:
        messages.error(request, str(exc))
        return redirect("superadmin:extension_detail", pk=pk)
    if changed:
        messages.success(request, done.format(name=name))
    else:
        messages.info(request, nothing.format(name=name))
    if to_list:
        return redirect("superadmin:extension_list")
    return redirect("superadmin:extension_detail", pk=pk)


@super_admin_only
@require_POST
def extension_archive(request, pk):
    """Archive an extension (blocked while people still belong to it)."""
    return _run_extension_action(
        request, pk, archive_extension,
        "{name} was archived.", "{name} is already archived.",
    )


@super_admin_only
@require_POST
def extension_restore(request, pk):
    """Restore an archived extension."""
    return _run_extension_action(
        request, pk, restore_extension,
        "{name} was restored.", "{name} is not archived.",
    )


@super_admin_only
@require_POST
def extension_delete(request, pk):
    """Permanently delete an archived extension."""
    return _run_extension_action(
        request, pk, force_delete_extension,
        "{name} was deleted.", "{name} was not deleted.", to_list=True,
    )


@super_admin_only
@require_POST
def extension_unassign_coordinator(request, pk):
    """Remove the coordinator (they stay in the extension as a member)."""
    return _run_extension_action(
        request, pk, unassign_coordinator,
        "{name} no longer has a coordinator.", "{name} has no coordinator.",
    )


@super_admin_only
@require_POST
def extension_assign_coordinator(request, pk):
    """Set the coordinator chosen in the detail page's form (replaces the current one)."""
    extension = _extension_or_none(pk)
    if extension is None:
        return _gone(request)
    form = AssignCoordinatorForm(request.POST, extension=extension)
    if not form.is_valid():
        messages.error(request, "Choose a member of this extension from the list.")
        return redirect("superadmin:extension_detail", pk=pk)
    person = form.cleaned_data["user"]
    try:
        _, changed = assign_coordinator(request.user, request, extension, person)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        if changed:
            messages.success(
                request, f"{person.full_name} is now the coordinator of {title_case(extension.name)}."
            )
        else:
            messages.info(request, f"{person.full_name} is already the coordinator.")
    return redirect("superadmin:extension_detail", pk=pk)


# ------------------------------------------------- people of one extension
@super_admin_only
@require_POST
def extension_person_add(request, pk):
    """Transfer people INTO this extension (POST: people (ids), confirmed, submit_token).

    `confirmed` is "1" once the dialog said the people may leave their current
    extension; without it the service skips anyone who belongs to another one.
    """
    extension = _extension_or_none(pk)
    if extension is None:
        return _gone(request)
    back = redirect("superadmin:extension_detail", pk=pk)
    form = AddPeopleForm(request.POST, extension=extension)
    if not form.is_valid():
        messages.error(request, "Choose people from the list.")
        return back
    people = form.cleaned_data["people"][:BULK_LIMIT]
    try:
        result = transfer_people_in(
            request.user, request, extension, [p.pk for p in people],
            token=request.POST.get("submit_token", ""),
            confirmed=request.POST.get("confirmed") == "1",
        )
    except ServiceError as exc:
        messages.error(request, str(exc))
        return back
    _report_people_result(request, result, f"transferred to {title_case(extension.name)}")
    return back


def _report_people_result(request, result, verb):
    """Turn a people BulkResult into a success toast and a warning toast."""
    if result.duplicate:
        messages.info(request, "That request was already handled.")
        return
    if result.done:
        count = len(result.done)
        noun = "person was" if count == 1 else "people were"
        messages.success(request, f"{count} {noun} {verb}: {_names_sentence(result.done)}.")
    if result.skipped:
        shown = [f"{label}: {reason}." for label, reason in result.skipped[: NAMES_IN_MESSAGE + 2]]
        more = len(result.skipped) - len(shown)
        messages.warning(
            request,
            f"Skipped {len(result.skipped)}. " + " ".join(shown) + (f" And {more} more." if more > 0 else ""),
        )


# ------------------------------------------------------------ bulk actions
def _selected_ids(request):
    """Extension ids ticked on the list: whole numbers only, no repeats, capped."""
    ids = []
    for raw in request.POST.getlist("ids"):
        if raw.isdigit() and int(raw) not in ids:
            ids.append(int(raw))
    return ids[:BULK_LIMIT]


def _names_sentence(names):
    """'A, B, C and 4 more' for a toast."""
    shown = ", ".join(names[:NAMES_IN_MESSAGE])
    extra = len(names) - NAMES_IN_MESSAGE
    return f"{shown} and {extra} more" if extra > 0 else shown


@super_admin_only
@require_POST
def extension_bulk(request):
    """Archive or delete the ticked extensions (POST: action, ids, qs).

    Rows that do not qualify are skipped and explained, never an error page:
    archiving skips extensions that still have people; deleting skips ones
    that are not archived or are still in use.
    """
    back = _list_url(QueryDict(request.POST.get("qs", "")[:500]))
    action = request.POST.get("action")
    ids = _selected_ids(request)
    if action not in ("archive", "delete"):
        messages.error(request, "Choose archive or delete.")
        return redirect(back)
    if not ids:
        messages.info(request, "Tick at least one extension first.")
        return redirect(back)

    if action == "archive":
        result, verb = bulk_archive_extensions(request.user, request, ids), "archived"
    else:
        result, verb = bulk_delete_extensions(request.user, request, ids), "deleted"

    if result.done:
        count = len(result.done)
        noun = "extension was" if count == 1 else "extensions were"
        messages.success(request, f"{count} {noun} {verb}: {_names_sentence(result.done)}.")
    if result.skipped:
        count = len(result.skipped)
        messages.warning(
            request, f"Skipped {count}. " + " ".join(result.skipped[: NAMES_IN_MESSAGE + 2])
            + (f" And {count - NAMES_IN_MESSAGE - 2} more." if count > NAMES_IN_MESSAGE + 2 else "")
        )
    if not result.done and not result.skipped:
        messages.info(request, "Those extensions no longer exist.")
    return redirect(back)