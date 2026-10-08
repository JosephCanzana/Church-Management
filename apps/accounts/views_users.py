"""accounts.views_users: the super-admin screens for managing people.

Same rules as views_superadmin.py: every view is wrapped in
role_required(SUPER_ADMIN), the services check permission again, destructive
actions are POST-only, and an action on someone who is already in the wanted
state (or already gone) answers calmly instead of failing.

Pages that can show a password (create, reset, bulk reset) are `never_cache`
and render the show-once dialog directly in the POST response. They are not
redirected, because a redirect would need the password to be kept somewhere.
Re-posting such a form is harmless: its one-time token was already used.
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.services import ServiceError, new_submission_token

from .decorators import role_required
from .forms import UserCreateForm, UserEditForm, UserFilterForm
from .models import Role, Status, User, UserExtensionHistory
from .services_users import (
    archive_user,
    create_user,
    deactivate_user,
    force_delete_user,
    reset_password,
    restore_user,
    run_bulk,
    suspend_user,
    unsuspend_user,
    update_user,
)

PAGE_SIZE = 25
RETURN_QS_MAX = 1000               # longest "return to this filtered list" string accepted
SECRETS_HEADERS = ["Name", "Account ID", "Password"]
SECRETS_MESSAGE = (
    "This is the only time the password is shown. Copy it now and give it to the person "
    "through a private channel. They will be asked to change it."
)

super_admin_only = role_required(Role.SUPER_ADMIN)

SORTS = {
    "last_name": [Lower("last_name"), Lower("first_name")],
    "-last_name": [Lower("last_name").desc(), Lower("first_name").desc()],
    "first_name": [Lower("first_name"), Lower("last_name")],
    "-first_name": [Lower("first_name").desc(), Lower("last_name").desc()],
    "account_id": ["account_id"],
    "-account_id": ["-account_id"],
}

# What each bulk button says and asks. {n} becomes the number of ticked people (ui.js).
BULK_BUTTONS = {
    "archive": dict(label="Archive", icon="archive-box", past="archived", danger=False,
                    title="Archive {n} selected?",
                    message="They will be archived and can no longer log in. You can restore them later."),
    "suspend": dict(label="Suspend", icon="pause-circle", past="suspended", danger=False,
                    title="Suspend {n} selected?",
                    message="They cannot log in until you lift the suspension. Nothing is deleted."),
    "unsuspend": dict(label="Unsuspend", icon="play-circle", past="unsuspended", danger=False,
                      title="Unsuspend {n} selected?",
                      message="They can log in again."),
    "deactivate": dict(label="Deactivate", icon="power", past="deactivated", danger=False,
                       title="Deactivate {n} selected?",
                       message="They go back to 'not activated' and must activate again."),
    "reset_password": dict(label="Reset password", icon="key", past="given a new password", danger=False,
                           title="Reset the password of {n} selected?",
                           message="Each person gets a new password, shown once. Their open sessions end."),
    "restore": dict(label="Restore", icon="arrow-uturn-left", past="restored", danger=False,
                    title="Restore {n} selected?",
                    message="They are brought back from the archive."),
    "delete": dict(label="Delete", icon="trash", past="deleted", danger=True,
                   title="Delete {n} selected permanently?",
                   message="This cannot be undone. Only archived people can be deleted."),
}


def _bulk_buttons(status):
    """The bulk buttons that make sense for the list being shown."""
    if status == "archived":
        names = ["restore", "delete"]
    elif status == "all":
        names = ["archive", "suspend", "unsuspend", "deactivate", "reset_password", "restore", "delete"]
    else:
        names = ["archive", "suspend", "unsuspend", "deactivate", "reset_password"]
    return [{"value": n, **BULK_BUTTONS[n]} for n in names]


def _person_or_none(pk):
    return User.objects.select_related("extension").filter(pk=pk).first()


def _gone(request):
    messages.info(request, "That person no longer exists.")
    return redirect("superadmin:user_list")


def _secrets(rows, title):
    """Payload for the show-once dialog (see ui.js showSecrets)."""
    return {"title": title, "message": SECRETS_MESSAGE, "headers": SECRETS_HEADERS, "rows": rows}


# ------------------------------------------------------------------- list
def _list_context(params):
    """Everything the people list page needs, from a QueryDict of filters."""
    form = UserFilterForm(params)
    form.is_valid()
    data = getattr(form, "cleaned_data", {})
    status = data.get("status") or "current"
    role = data.get("role") or ""
    extension = data.get("extension")
    sort = data.get("sort") or "last_name"

    queryset = User.objects.select_related("extension")
    if status == "current":
        queryset = queryset.exclude(status=Status.ARCHIVED)
    elif status != "all":
        queryset = queryset.filter(status=status)
    if role:
        queryset = queryset.filter(role=role)
    if extension is not None:
        queryset = queryset.filter(extension=extension)
    for term in (data.get("q") or "").split():
        queryset = queryset.filter(
            Q(first_name__icontains=term) | Q(middle_name__icontains=term)
            | Q(last_name__icontains=term) | Q(account_id__icontains=term)
        )
    queryset = queryset.order_by(*SORTS[sort], "pk")

    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(params.get("page"))
    without_page = params.copy()
    without_page.pop("page", None)
    return {
        "form": form,
        "page": page,
        "total": paginator.count,
        "status": status,
        "bulk_buttons": _bulk_buttons(status),
        "querystring": without_page.urlencode(),
        "full_querystring": params.urlencode(),
    }


@super_admin_only
@never_cache
@require_http_methods(["GET"])
def user_list(request):
    """People with search, filters, sorting, paging and bulk selection."""
    context = _list_context(request.GET)
    context["submit_token"] = new_submission_token()
    return render(request, "accounts/superadmin/user_list.html", context)


# -------------------------------------------------------------- create / edit
@super_admin_only
@never_cache
@require_http_methods(["GET", "POST"])
def user_create(request):
    """New-person form (GET) and save (POST). Shows the password once."""
    initial = {"role": Role.MEMBER}
    wanted = request.GET.get("extension", "")
    if wanted.isdigit():
        initial["extension"] = int(wanted)
    form = UserCreateForm(request.POST or None, actor=request.user, initial=initial)

    if request.method == "POST" and form.is_valid():
        try:
            result = create_user(
                request.user, request, form.cleaned_data,
                token=request.POST.get("submit_token", ""),
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            if result.duplicate:
                messages.info(request, "That form was already submitted, so nothing was created a second time.")
                return redirect("superadmin:user_list")
            person = result.user
            messages.success(request, f"{person.full_name} was created. Account ID {person.account_id}.")
            return _render_detail(request, person, secrets=_secrets(
                [[person.full_name, person.account_id, result.password.display]], "Account created",
            ))
    return render(request, "accounts/superadmin/user_form.html", {
        "form": form, "person": None, "submit_token": new_submission_token(),
    })


@super_admin_only
@never_cache
@require_http_methods(["GET", "POST"])
def user_edit(request, pk):
    """Edit details, role and extension (GET shows the form, POST saves it)."""
    person = _person_or_none(pk)
    if person is None:
        return _gone(request)
    if person.status == Status.ARCHIVED:
        messages.error(request, "Restore this person before editing them.")
        return redirect("superadmin:user_detail", pk=pk)

    initial = {
        "first_name": person.first_name, "middle_name": person.middle_name,
        "last_name": person.last_name, "birth_date": person.birth_date,
        "role": person.role, "extension": person.extension_id,
    }
    form = UserEditForm(
        request.POST or None, actor=request.user, initial=initial,
        keep_extension=person.extension_id,
    )
    if request.method == "POST" and form.is_valid():
        try:
            updated, changed = update_user(request.user, request, person, form.cleaned_data)
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            if changed:
                messages.success(request, f"{updated.full_name} was updated.")
            else:
                messages.info(request, "No changes to save.")
            return redirect("superadmin:user_detail", pk=pk)
    return render(request, "accounts/superadmin/user_form.html", {
        "form": form, "person": person,
    })


# ----------------------------------------------------------------- detail
def _render_detail(request, person, secrets=None):
    """The detail page, optionally with the show-once dialog."""
    history = (
        UserExtensionHistory.objects.filter(user=person)
        .select_related("extension").order_by("-from_date", "-pk")[:20]
    )
    return render(request, "accounts/superadmin/user_detail.html", {
        "person": person,
        "history": history,
        "is_self": person.pk == request.user.pk,
        "secrets": secrets,
        "submit_token": new_submission_token(),
    })


@super_admin_only
@never_cache
@require_http_methods(["GET"])
def user_detail(request, pk):
    """One person: details, status, extension history and the action buttons."""
    person = _person_or_none(pk)
    if person is None:
        return _gone(request)
    return _render_detail(request, person)


# ---------------------------------------------------------------- actions
def _run_user_action(request, pk, service, done, nothing, *, to_list=False):
    """Run one POST action on a person and answer with a message.

    done / nothing use {name}. `nothing` is shown when the person was already
    in the wanted state (for example a second click).
    """
    person = _person_or_none(pk)
    if person is None:
        return _gone(request)
    name = person.full_name
    try:
        _, changed = service(request.user, request, person)
    except ServiceError as exc:
        messages.error(request, str(exc))
        return redirect("superadmin:user_detail", pk=pk)
    if changed:
        messages.success(request, done.format(name=name))
    else:
        messages.info(request, nothing.format(name=name))
    if to_list:
        return redirect("superadmin:user_list")
    return redirect("superadmin:user_detail", pk=pk)


@super_admin_only
@require_POST
def user_archive(request, pk):
    return _run_user_action(request, pk, archive_user,
                            "{name} was archived.", "{name} is already archived.")


@super_admin_only
@require_POST
def user_restore(request, pk):
    return _run_user_action(request, pk, restore_user,
                            "{name} was restored.", "{name} is not archived.")


@super_admin_only
@require_POST
def user_suspend(request, pk):
    return _run_user_action(request, pk, suspend_user,
                            "{name} was suspended.", "{name} is already suspended.")


@super_admin_only
@require_POST
def user_unsuspend(request, pk):
    return _run_user_action(request, pk, unsuspend_user,
                            "{name} can log in again.", "{name} is not suspended.")


@super_admin_only
@require_POST
def user_deactivate(request, pk):
    return _run_user_action(request, pk, deactivate_user,
                            "{name} must activate again.", "{name} is not activated yet.")


@super_admin_only
@require_POST
def user_delete(request, pk):
    return _run_user_action(request, pk, force_delete_user,
                            "{name} was deleted.", "{name} was not deleted.", to_list=True)


@super_admin_only
@never_cache
@require_POST
def user_reset_password(request, pk):
    """Give one person a new password and show it once."""
    person = _person_or_none(pk)
    if person is None:
        return _gone(request)
    try:
        result = reset_password(
            request.user, request, person, token=request.POST.get("submit_token", ""),
        )
    except ServiceError as exc:
        messages.error(request, str(exc))
        return redirect("superadmin:user_detail", pk=pk)
    if result.duplicate:
        messages.info(request, "That password reset was already done. Reset again if you need a new one.")
        return redirect("superadmin:user_detail", pk=pk)
    messages.success(request, f"{person.full_name} has a new password.")
    return _render_detail(request, _person_or_none(pk), secrets=_secrets(
        [[person.full_name, person.account_id, result.password.display]], "New password",
    ))


# ------------------------------------------------------------------- bulk
def _bulk_messages(request, action, result):
    """Turn a BulkResult into one success toast and one warning toast."""
    past = BULK_BUTTONS[action]["past"]
    if result.done:
        count = len(result.done)
        messages.success(request, f"{count} {'person was' if count == 1 else 'people were'} {past}.")
    if result.skipped:
        shown = "; ".join(f"{label} ({reason})" for label, reason in result.skipped[:5])
        extra = len(result.skipped) - 5
        more = f"; and {extra} more" if extra > 0 else ""
        messages.warning(request, f"Skipped {len(result.skipped)}: {shown}{more}.")


@super_admin_only
@never_cache
@require_POST
def user_bulk(request):
    """Run one action on every ticked person (up to 100)."""
    action = request.POST.get("action", "")
    raw_ids = request.POST.getlist("ids")
    ids = [i for i in raw_ids if i.isdigit()]
    return_to = QueryDict(request.POST.get("qs", "")[:RETURN_QS_MAX])

    def back():
        url = reverse("superadmin:user_list")
        query = return_to.urlencode()
        return redirect(f"{url}?{query}" if query else url)

    try:
        result = run_bulk(request.user, request, action, ids, token=request.POST.get("submit_token", ""))
    except ServiceError as exc:
        messages.error(request, str(exc))
        return back()
    if result.duplicate:
        messages.info(request, "That selection was already processed, so nothing was done a second time.")
        return back()

    _bulk_messages(request, action, result)
    if result.passwords:
        context = _list_context(return_to)
        context["submit_token"] = new_submission_token()
        context["secrets"] = _secrets(result.passwords, "New passwords")
        return render(request, "accounts/superadmin/user_list.html", context)
    return back()
