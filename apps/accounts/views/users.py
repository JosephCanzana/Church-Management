"""accounts.views.users: the people screens, shared by the super-admin, the admin and the coordinator.

One set of views serves all three roles. Each role has its own URL namespace
(`superadmin`, `admin_area`, `coordinator_area`, see accounts/urls/) and the views read the
namespace of the request, so every redirect stays inside the area the person came from; the
templates do the same through `{% role_url %}`.

Two guards, both required: `people_manager` on the view guards the page, and every service checks
permission again for the particular person (`accounts.permissions.can_act_on`). Everything a
person can see starts from `User.objects.for_user(viewer)`, so a coordinator never reaches
anyone outside their extension, even by typing an id. Someone out of reach looks exactly like
someone who no longer exists. Destructive actions are POST-only, and an action on someone who
is already in the wanted state answers calmly instead of failing.

Pages that can show a password (create, reset, bulk reset) are `never_cache` and render the
show-once dialog directly in the POST response. They are not redirected, because a redirect
would need the password to be kept somewhere. Re-posting such a form is harmless: its
one-time token was already used.
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
from apps.core.text import name_case, suffix_case, title_case

from ..address import ADDRESS_FIELDS, canonical_country
from ..decorators import role_required
from ..forms import GuestFilterForm, UserCreateForm, UserFilterForm, UserForm
from ..models import NO_ACCOUNT_TYPES, PersonType, Role, Status, User, UserExtensionHistory
from ..permissions import (
    EDIT, allowed_bulk_actions, can_assign_special_roles, can_change_placement, require_can_act,
)
from ..services.special_roles import attach_special_roles, filter_by_special_role, special_role_rows
from ..services.users import (
    archive_user,
    create_user,
    force_delete_user,
    grant_account,
    promote_guest,
    reset_password,
    restore_user,
    reveal_default_password,
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

LIST_TEMPLATE = "accounts/people/user_list.html"
LIST_RESULTS_TEMPLATE = "accounts/people/_user_list_results.html"
FORM_TEMPLATE = "accounts/people/user_form.html"
DETAIL_TEMPLATE = "accounts/people/user_detail.html"

people_manager = role_required(Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR)
super_admin_only = role_required(Role.SUPER_ADMIN)

SORTS = {
    "last_name": [Lower("last_name"), Lower("first_name")],
    "-last_name": [Lower("last_name").desc(), Lower("first_name").desc()],
    "first_name": [Lower("first_name"), Lower("last_name")],
    "-first_name": [Lower("first_name").desc(), Lower("last_name").desc()],
    "account_id": ["account_id"],
    "-account_id": ["-account_id"],
}

# What each bulk button says and asks. {n} becomes the number of people it applies to.
BULK_BUTTONS = {
    "archive": dict(label="Archive", icon="archive-box", past="archived", danger=False,
                    title="Archive {n} selected?",
                    message="They will be archived and can no longer log in. You can restore them later."),
    "suspend": dict(label="Suspend", icon="pause-circle", past="suspended", danger=False,
                    title="Suspend {n} selected?",
                    message="They cannot log in until the suspension is lifted. Nothing is deleted."),
    "unsuspend": dict(label="Unsuspend", icon="play-circle", past="unsuspended", danger=False,
                      title="Unsuspend {n} selected?",
                      message="They can log in again."),
    "reset_password": dict(label="Reset password", icon="key", past="given a new password", danger=False,
                           title="Reset the password of {n} selected?",
                           message="Each person gets a new password, shown once. Their open sessions end."),
    "restore": dict(label="Restore", icon="arrow-uturn-left", past="restored", danger=False,
                    title="Restore {n} selected?",
                    message="They are brought back from the archive."),
    "promote": dict(label="Promote", icon="user-plus", past="promoted", danger=False,
                    title="Promote {n} selected?",
                    message="Guests become members without an account. Anyone who is not an active guest is skipped."),
    "delete": dict(label="Delete", icon="trash", past="deleted", danger=True,
                   title="Delete {n} selected permanently?",
                   message="This cannot be undone. Only archived people can be deleted."),
}
BULK_ORDER = ("archive", "suspend", "unsuspend", "reset_password", "restore", "delete")
# The statuses each bulk action can do something for. The list page counts only the ticked rows
# in these statuses and hides a button when none of them apply, so Restore never shows for
# someone who is not archived.
_LIVE = (Status.ACTIVE, Status.SUSPENDED)
BULK_APPLIES = {
    "archive": _LIVE,
    "suspend": (Status.ACTIVE,),
    "unsuspend": (Status.SUSPENDED,),
    "reset_password": _LIVE,
    "restore": (Status.ARCHIVED,),
    "delete": (Status.ARCHIVED,),
}


# The bulk bar of the guests screen: single-purpose, so no reset password and no delete.
GUEST_BULK_ORDER = ("promote", "suspend", "unsuspend", "restore", "archive")


def _guest_bulk_buttons(status, viewer):
    """The bulk buttons the guests screen offers `viewer` for the list being shown."""
    allowed = allowed_bulk_actions(viewer)
    names = [n for n in GUEST_BULK_ORDER if n in allowed]
    if status == "archived":
        names = [n for n in names if n == "restore"]
    elif status != "all":
        names = [n for n in names if n != "restore"]
    applies = {**BULK_APPLIES, "promote": (Status.ACTIVE,)}
    return [{"value": n, "applies": " ".join(applies[n]), **BULK_BUTTONS[n]} for n in names]


def _bulk_buttons(status, viewer):
    """The bulk buttons this person may use on the list being shown."""
    allowed = allowed_bulk_actions(viewer)
    names = [n for n in BULK_ORDER if n in allowed]
    if status == "archived":
        names = [n for n in names if n in ("restore", "delete")]
    elif status != "all":
        names = [n for n in names if n not in ("restore", "delete")]
    return [{"value": n, "applies": " ".join(BULK_APPLIES[n]), **BULK_BUTTONS[n]} for n in names]


# --------------------------------------------------------------- namespaces
def _to(request, name, **kwargs):
    """Redirect to url `name` in the role area the request came from."""
    return redirect(f"{request.resolver_match.namespace}:{name}", **kwargs)


def _url(request, name, *args):
    return reverse(f"{request.resolver_match.namespace}:{name}", args=args)


def _person_or_none(viewer, pk):
    """The person, only if `viewer` may see them (None looks the same as 'deleted')."""
    return User.objects.for_user(viewer).select_related("extension").filter(pk=pk).first()


def _gone(request):
    messages.info(request, "That person no longer exists.")
    return _to(request, "user_list")


def _secrets(rows, title):
    """Payload for the show-once dialog (see ui.js showSecrets)."""
    return {"title": title, "message": SECRETS_MESSAGE, "headers": SECRETS_HEADERS, "rows": rows}


def _address_text(person):
    """The person's address on one line in title case, or '' when none was given."""
    parts = [
        title_case(person.building_number), title_case(person.street), title_case(person.barangay),
        title_case(person.municipality), title_case(person.province), person.postal_code,
        canonical_country(person.country) or title_case(person.country),
    ]
    return ", ".join(p for p in parts if p)


def _form_initial(person):
    """Saved values for the edit form, shown the way the pages show them (title case)."""
    initial = {
        "first_name": name_case(person.first_name), "middle_name": name_case(person.middle_name),
        "last_name": name_case(person.last_name), "suffix": suffix_case(person.suffix),
        "birth_date": person.birth_date, "gender": person.gender,
        "phone_number": person.phone_number,
        "role": person.role, "extension": person.extension_id,
        "postal_code": person.postal_code,
        "country": canonical_country(person.country) or title_case(person.country),
    }
    for name in ("province", "municipality", "barangay", "street", "building_number"):
        initial[name] = title_case(getattr(person, name))
    return initial


# ------------------------------------------------------------------- list
def _list_context(params, viewer):
    """Everything the people list page needs, from a QueryDict of filters.

    Only people `viewer` may see are listed, and `viewer` is left out.
    """
    form = UserFilterForm(params)
    show_role = viewer.role != Role.COORDINATOR          # a coordinator only has members
    show_extension = viewer.role != Role.COORDINATOR     # ...and only in their own extension
    if not show_role:
        del form.fields["role"]
    else:
        # Nobody is offered a role they cannot see.
        visible = [Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER]
        if viewer.role == Role.ADMIN:
            visible = [Role.COORDINATOR, Role.MEMBER]
        form.fields["role"].choices = [("", "All roles"), *[(r, Role(r).label) for r in visible]]
    if not show_extension:
        del form.fields["extension"]
    form.is_valid()
    data = getattr(form, "cleaned_data", {})
    status = data.get("status") or "current"
    role = data.get("role") or ""
    extension = data.get("extension")
    sort = data.get("sort") or "last_name"
    special_role = data.get("special_role") or ""
    person_type = data.get("person_type") or ""
    needs_change = data.get("needs_change") == "yes"

    queryset = User.objects.for_user(viewer).select_related("extension").exclude(pk=viewer.pk)
    if needs_change:
        queryset = queryset.filter(person_type=PersonType.MEMBER_ACCOUNT, must_change_password=True)
    # The People screens list people with an account unless the Type filter says otherwise.
    if person_type != "all":
        queryset = queryset.filter(person_type=person_type or PersonType.MEMBER_ACCOUNT)
    if status == "current":
        queryset = queryset.exclude(status=Status.ARCHIVED)
    elif status != "all":
        queryset = queryset.filter(status=status)
    if role:
        queryset = queryset.filter(role=role)
    if extension is not None:
        queryset = queryset.filter(extension=extension)
    queryset = filter_by_special_role(queryset, special_role)
    for term in (data.get("q") or "").split():
        queryset = queryset.filter(
            Q(first_name__icontains=term) | Q(middle_name__icontains=term)
            | Q(last_name__icontains=term) | Q(account_id__icontains=term)
        )
    queryset = queryset.order_by(*SORTS[sort], "pk")

    paginator = Paginator(queryset, PAGE_SIZE)
    page = paginator.get_page(params.get("page"))
    # One query for the whole page: the tags (Tithes member, Attendance member) read this.
    page.object_list = attach_special_roles(page.object_list)
    without_page = params.copy()
    without_page.pop("page", None)
    return {
        "form": form,
        "page": page,
        "total": paginator.count,
        "status": status,
        "show_role_filter": show_role,
        "show_extension_filter": show_extension,
        "bulk_buttons": _bulk_buttons(status, viewer),
        "querystring": without_page.urlencode(),
        "full_querystring": params.urlencode(),
        # How many filters differ from the defaults (badge on the Filters button).
        "active_filters": sum([
            bool(data.get("q")), status != "current", bool(role),
            extension is not None, bool(special_role), bool(person_type), needs_change, sort != "last_name",
        ]),
    }


@people_manager
@never_cache
@require_http_methods(["GET"])
def user_list(request):
    """People with search, filters, sorting, paging, bulk selection and a right-click menu."""
    context = _list_context(request.GET, request.user)
    context["submit_token"] = new_submission_token()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render(request, LIST_RESULTS_TEMPLATE, context)
    return render(request, LIST_TEMPLATE, context)


# ------------------------------------------------------ guests and no-account members
GUESTS_TEMPLATE = "accounts/people/guest_list.html"


def _guest_context(params, viewer):
    """Everything the guests screen needs: guests and members without an account `viewer` may see.

    Same scoping as the People screens (`for_user`), so a coordinator only sees their own extension.
    `viewer` is never a guest, but is left out anyway, like on the People list.
    """
    form = GuestFilterForm(params)
    show_extension = viewer.role != Role.COORDINATOR
    if not show_extension:
        del form.fields["extension"]
    form.is_valid()
    data = getattr(form, "cleaned_data", {})
    status = data.get("status") or "current"
    person_type = data.get("person_type") or ""
    extension = data.get("extension")
    sort = data.get("sort") or "last_name"

    queryset = (
        User.objects.for_user(viewer).select_related("extension").exclude(pk=viewer.pk)
        .filter(person_type__in=[person_type] if person_type else NO_ACCOUNT_TYPES)
    )
    if status == "current":
        queryset = queryset.exclude(status=Status.ARCHIVED)
    elif status != "all":
        queryset = queryset.filter(status=status)
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
        "show_extension_filter": show_extension,
        "bulk_buttons": _guest_bulk_buttons(status, viewer),
        "querystring": without_page.urlencode(),
        "full_querystring": params.urlencode(),
        "active_filters": sum([
            bool(data.get("q")), status != "current", bool(person_type),
            extension is not None, sort != "last_name",
        ]),
    }


@people_manager
@never_cache
@require_http_methods(["GET"])
def guest_list(request):
    """Guests and members without an account: search, filters, paging and the per-person buttons."""
    context = _guest_context(request.GET, request.user)
    context["submit_token"] = new_submission_token()
    return render(request, GUESTS_TEMPLATE, context)


@people_manager
@require_POST
def user_promote(request, pk):
    """Promote a guest to a member without an account."""
    return _run_user_action(request, pk, promote_guest,
                            "{name} is now a member (no account).", "{name} is not a guest.")


@people_manager
@never_cache
@require_POST
def user_grant_account(request, pk):
    """Give a member without an account a sign-in and show the password once."""
    person = _person_or_none(request.user, pk)
    if person is None:
        return _gone(request)
    try:
        result = grant_account(
            request.user, request, person, token=request.POST.get("submit_token", ""),
            typed=request.POST.get("password", ""),
        )
    except ServiceError as exc:
        messages.error(request, str(exc))
        return _to(request, "user_detail", pk=pk)
    if result.duplicate:
        messages.info(request, "That form was already submitted, so nothing was changed a second time.")
        return _to(request, "user_detail", pk=pk)
    messages.success(request, f"{person.full_name} now has an account. Account ID {person.account_id}.")
    return _render_detail(request, _person_or_none(request.user, pk), secrets=_secrets(
        [[person.full_name, person.account_id, result.password.display]], "Account created",
    ))


# -------------------------------------------------------------- create / edit
@people_manager
@never_cache
@require_http_methods(["GET", "POST"])
def user_create(request):
    """New-person form (GET) and save (POST). Shows the password once."""
    initial = {"role": Role.MEMBER}
    wanted = request.GET.get("extension", "")
    if wanted.isdigit() and can_change_placement(request.user):
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
                return _to(request, "user_list")
            person = result.user
            messages.success(request, f"{person.full_name} was created. Account ID {person.account_id}.")
            return _render_detail(request, person, secrets=_secrets(
                [[person.full_name, person.account_id, result.password.display]], "Account created",
            ))
    return render(request, FORM_TEMPLATE, {
        "form": form, "person": None, "submit_token": new_submission_token(),
    })


@people_manager
@never_cache
@require_http_methods(["GET", "POST"])
def user_edit(request, pk):
    """Edit details, contact, role and extension (GET shows the form, POST saves it)."""
    person = _person_or_none(request.user, pk)
    if person is None:
        return _gone(request)
    require_can_act(request.user, person, EDIT)
    if person.status == Status.ARCHIVED:
        messages.error(request, "Restore this person before editing them.")
        return _to(request, "user_detail", pk=pk)

    form = UserForm(
        request.POST or None, actor=request.user, initial=_form_initial(person),
        keep_extension=person.extension_id, keep_role=person.role, exclude_pk=person.pk,
        saved_address={name: getattr(person, name) for name in ADDRESS_FIELDS},
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
            return _to(request, "user_detail", pk=pk)
    return render(request, FORM_TEMPLATE, {"form": form, "person": person})


# ----------------------------------------------------------------- detail
def _render_detail(request, person, secrets=None):
    """The detail page, optionally with the show-once dialog."""
    attach_special_roles([person])      # the tags under the heading
    history = (
        UserExtensionHistory.objects.filter(user=person)
        .select_related("extension").order_by("-from_date", "-pk")[:20]
    )
    # Special roles are for members who can still sign in; the card is hidden otherwise.
    show_special = (
        person.role == Role.MEMBER and person.person_type == PersonType.MEMBER_ACCOUNT
        and person.extension_id is not None and person.status != Status.ARCHIVED
    )
    return render(request, DETAIL_TEMPLATE, {
        "person": person,
        "history": history,
        "special_roles": special_role_rows(person) if show_special else None,
        "can_assign_special": (
            show_special and person.status != Status.SUSPENDED
            and can_assign_special_roles(request.user, person.extension_id)
        ),
        "address": _address_text(person),
        "is_self": person.pk == request.user.pk,
        "secrets": secrets,
        "submit_token": new_submission_token(),
    })


@people_manager
@never_cache
@require_http_methods(["GET"])
def user_detail(request, pk):
    """One person: details, status, extension history and the action buttons."""
    person = _person_or_none(request.user, pk)
    if person is None:
        return _gone(request)
    return _render_detail(request, person)


# ---------------------------------------------------------------- actions
def _run_user_action(request, pk, service, done, nothing, *, to_list=False):
    """Run one POST action on a person and answer with a message.

    done / nothing use {name}. `nothing` is shown when the person was already
    in the wanted state (for example a second click). A person out of reach looks
    like a person who is gone; a person in reach but not allowed raises
    PermissionDenied in the service (HTTP 403).
    """
    person = _person_or_none(request.user, pk)
    if person is None:
        return _gone(request)
    name = person.full_name
    try:
        _, changed = service(request.user, request, person)
    except ServiceError as exc:
        messages.error(request, str(exc))
        return _to(request, "user_detail", pk=pk)
    if changed:
        messages.success(request, done.format(name=name))
    else:
        messages.info(request, nothing.format(name=name))
    if to_list:
        return _to(request, "user_list")
    return _to(request, "user_detail", pk=pk)


@people_manager
@require_POST
def user_archive(request, pk):
    return _run_user_action(request, pk, archive_user,
                            "{name} was archived.", "{name} is already archived.")


@people_manager
@require_POST
def user_restore(request, pk):
    return _run_user_action(request, pk, restore_user,
                            "{name} was restored.", "{name} is not archived.")


@people_manager
@require_POST
def user_suspend(request, pk):
    return _run_user_action(request, pk, suspend_user,
                            "{name} was suspended.", "{name} is already suspended.")


@people_manager
@require_POST
def user_unsuspend(request, pk):
    return _run_user_action(request, pk, unsuspend_user,
                            "{name} can log in again.", "{name} is not suspended.")


@super_admin_only
@require_POST
def user_delete(request, pk):
    """Force delete: super-admin only, and only for archived people."""
    return _run_user_action(request, pk, force_delete_user,
                            "{name} was deleted.", "{name} was not deleted.", to_list=True)


@people_manager
@never_cache
@require_POST
def user_reset_password(request, pk):
    """Give one person a new password and show it once."""
    person = _person_or_none(request.user, pk)
    if person is None:
        return _gone(request)
    try:
        result = reset_password(
            request.user, request, person, token=request.POST.get("submit_token", ""),
            typed=request.POST.get("password", ""),
        )
    except ServiceError as exc:
        messages.error(request, str(exc))
        return _to(request, "user_detail", pk=pk)
    if result.duplicate:
        messages.info(request, "That password reset was already done. Reset again if you need a new one.")
        return _to(request, "user_detail", pk=pk)
    messages.success(request, f"{person.full_name} has a new password.")
    return _render_detail(request, _person_or_none(request.user, pk), secrets=_secrets(
        [[person.full_name, person.account_id, result.password.display]], "New password",
    ))


# ----------------------------------------------------- default password (coordinator)
@role_required(Role.COORDINATOR)
@never_cache
@require_POST
def default_password_show(request):
    """A coordinator's Default passwords button: display the global member password, read-only.

    A coordinator has no screen of their own for this and cannot set a default. The list page is
    rendered again with the password in the show-once dialog (never cached, never redirected), and
    each look is written to the audit log without the password.
    """
    return_to = QueryDict(request.POST.get("qs", "")[:RETURN_QS_MAX])
    plain = reveal_default_password(request.user, request)
    if plain is None:
        messages.info(
            request,
            "No member password is set, or it cannot be shown. Ask an admin to set it. Until then "
            "new members get a generated password, shown once when you create them.",
        )
        url = _url(request, "user_list")
        query = return_to.urlencode()
        return redirect(f"{url}?{query}" if query else url)
    context = _list_context(return_to, request.user)
    context["submit_token"] = new_submission_token()
    context["secrets"] = {
        "title": "Member password",
        "message": (
            "This is the password new members get, and the one given when you reset a member's password. "
            "They must change it the first time they sign in. Share it only with the member."
        ),
        "headers": ["For", "Password"],
        "rows": [["New members and password resets", plain]],
    }
    return render(request, LIST_TEMPLATE, context)


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


@people_manager
@never_cache
@require_POST
def user_bulk(request):
    """Run one action on every ticked person (up to 100)."""
    action = request.POST.get("action", "")
    raw_ids = request.POST.getlist("ids")
    ids = [i for i in raw_ids if i.isdigit()]
    return_to = QueryDict(request.POST.get("qs", "")[:RETURN_QS_MAX])
    # The guests screen posts here too and comes back to itself (a fixed whitelist, never a posted url).
    back_name = "guest_list" if request.POST.get("back") == "guests" else "user_list"

    def back():
        url = _url(request, back_name)
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
        context = _list_context(return_to, request.user)
        context["submit_token"] = new_submission_token()
        context["secrets"] = _secrets(result.passwords, "New passwords")
        return render(request, LIST_TEMPLATE, context)
    return back()
