"""accounts.views_superadmin: the super-admin management screens.

Today: extensions. User management is added here next.

Every view is wrapped in role_required(SUPER_ADMIN) (the page guard) and the
services check permission again (the action guard). Views stay thin: parse the
request, call a service, show a message, redirect. Destructive actions are
POST-only. A person double-clicking a button must never get an error page, so
actions on a record that is already in the wanted state (or already gone)
report that calmly instead of failing.
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from core.services import ServiceError

from .decorators import role_required
from .forms import AssignCoordinatorForm, ExtensionFilterForm, ExtensionForm
from .models import Extension, Role, Status, User
from .services import (
    archive_extension,
    assign_coordinator,
    create_extension,
    force_delete_extension,
    restore_extension,
    unassign_coordinator,
    update_extension,
)

PAGE_SIZE = 25
DETAIL_MEMBER_LIMIT = 50

super_admin_only = role_required(Role.SUPER_ADMIN)


def _extension_or_none(pk):
    """The extension with this id, or None (it may have just been deleted)."""
    return Extension.objects.filter(pk=pk).first()


def _gone(request):
    """Calm answer for an extension that no longer exists."""
    messages.info(request, "That extension no longer exists.")
    return redirect("superadmin:extension_list")


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
            messages.success(request, f"{extension.name} was created.")
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
                messages.success(request, f"{updated.name} was updated.")
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
    """One extension: details, coordinator, members and the action buttons."""
    extension = (
        Extension.objects.select_related("coordinator").filter(pk=pk).first()
    )
    if extension is None:
        return _gone(request)

    people = User.objects.filter(extension=extension).exclude(status=Status.ARCHIVED)
    return render(request, "accounts/superadmin/extension_detail.html", {
        "extension": extension,
        "members": people.order_by("last_name", "first_name", "pk")[:DETAIL_MEMBER_LIMIT],
        "member_count": people.count(),
        "archived_people_count": User.objects.filter(
            extension=extension, status=Status.ARCHIVED
        ).count(),
        "assign_form": (
            None if extension.is_archived
            else AssignCoordinatorForm(extension=extension)
        ),
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
    name = extension.name
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
    """Set the coordinator chosen in the detail page's form."""
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
            messages.success(request, f"{person.full_name} is now the coordinator of {extension.name}.")
        else:
            messages.info(request, f"{person.full_name} is already the coordinator.")
    return redirect("superadmin:extension_detail", pk=pk)
