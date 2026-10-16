"""events.views: the Events screens (calendar, list, detail, create, edit, cancel).

Views stay thin: they pick the events with `permissions.visible_events` (anything outside it is a
404, so a hidden event looks like one that does not exist), read filters, and hand the work to
`services.py`. Hiding a button is not access control: every service checks again.

The chosen view (calendar or list) is remembered per device in the `events_view` cookie, the
same way the Bible version is. `events:index` reads it and redirects.
"""
import datetime as dt
from dataclasses import dataclass
from itertools import groupby

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import Extension, Role
from apps.core.services import ServiceError

from . import permissions, services
from .calendar import (
    WEEKDAYS, build_month, events_in_window, month_param, month_window, parse_month, shift_month,
)
from .forms import EventForm
from .permissions import available_actions, visible_events

VIEW_COOKIE = "events_view"
VIEW_COOKIE_AGE = 60 * 60 * 24 * 365
PER_PAGE = 25
DEFAULT_START = dt.time(9, 0)           # a clicked calendar day opens the form at 9:00 AM (assumed)

SCOPE_ALL, SCOPE_GLOBAL, SCOPE_MINE = "all", "global", "mine"
ALL_ROLES = (Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER)
_ADMINS = (Role.SUPER_ADMIN, Role.ADMIN)


def events_screen(view):
    """Every events page: signed in (any role), never cached (cancel state changes)."""
    return never_cache(role_required(*ALL_ROLES)(view))


# ----------------------------------------------------------------- small helpers
def _url(name, *args, **params):
    """reverse() plus a querystring that skips empty values."""
    url = reverse(name, args=args)
    params = {k: v for k, v in params.items() if v not in (None, "")}
    return f"{url}?{urlencode(params)}" if params else url


def _safe_next(request, default):
    """The posted `next` address when it stays on this site, else `default`."""
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        return target
    return default


def _remember(response, view_name):
    """Remember calendar or list for this device (a cookie, not an account setting)."""
    response.set_cookie(VIEW_COOKIE, view_name, max_age=VIEW_COOKIE_AGE, samesite="Lax")
    return response


def _with_actions(events, actor):
    """Attach the actions each event offers to `actor` (templates read `event.actions`)."""
    for event in events:
        event.actions = available_actions(actor, event)
    return events


@dataclass(frozen=True)
class Filters:
    """Which events to show: everyone's, global only, my extension, or (admins) one extension."""

    scope: str = SCOPE_ALL
    extension_id: int | None = None
    can_mine: bool = False

    def apply(self, queryset, actor):
        if self.extension_id is not None:
            return queryset.filter(extension_id=self.extension_id)
        if self.scope == SCOPE_GLOBAL:
            return queryset.filter(extension__isnull=True)
        if self.scope == SCOPE_MINE:
            return queryset.filter(extension_id=actor.extension_id)
        return queryset

    def params(self):
        """The filter as querystring parameters (nothing for the default)."""
        if self.extension_id is not None:
            return {"extension": self.extension_id}
        return {"scope": self.scope} if self.scope != SCOPE_ALL else {}


def _parse_filters(request):
    """Read ?scope= and ?extension= safely: unknown values fall back to "all"."""
    user = request.user
    is_admin = user.role in _ADMINS
    can_mine = not is_admin and user.extension_id is not None
    scope = request.GET.get("scope", SCOPE_ALL)
    if scope not in (SCOPE_ALL, SCOPE_GLOBAL) and not (scope == SCOPE_MINE and can_mine):
        scope = SCOPE_ALL
    extension_id = None
    raw = request.GET.get("extension", "")
    if is_admin and raw.isascii() and raw.isdigit() and len(raw) <= 9:
        extension_id = int(raw)               # a made-up id simply shows nothing
    return Filters(scope=scope, extension_id=extension_id, can_mine=can_mine)


def _toolbar(request, filters, current, keep):
    """Everything `_toolbar.html` draws: view switch, filter pills, extension picker, New event.

    `keep` holds the parameters that stay when a filter changes (the month, or Past).
    """
    user = request.user
    route = f"events:{current}"
    pills = []
    for label, scope in (("All events", SCOPE_ALL), ("Global", SCOPE_GLOBAL), ("My extension", SCOPE_MINE)):
        if scope == SCOPE_MINE and not filters.can_mine:
            continue
        pills.append({
            "label": label,
            "url": _url(route, **keep, **({"scope": scope} if scope != SCOPE_ALL else {})),
            "active": filters.extension_id is None and filters.scope == scope,
        })
    extensions = None
    if user.role in _ADMINS:
        extensions = Extension.objects.filter(archived_at__isnull=True).order_by("name")
    return {
        "current": current,
        "calendar_url": _url("events:calendar", **filters.params()),
        "list_url": _url("events:list", **filters.params()),
        "pills": pills,
        "extensions": extensions,
        "extension_id": filters.extension_id,
        "hidden": list(keep.items()),
        "new_url": reverse("events:create") if permissions.can_manage_events(user) else None,
    }


# ----------------------------------------------------------------- calendar and list
@events_screen
def index_view(request):
    """Send the person to the view they used last on this device (calendar by default)."""
    target = "events:list" if request.COOKIES.get(VIEW_COOKIE) == "list" else "events:calendar"
    return redirect(_url(target, **request.GET.dict()))


@events_screen
def calendar_view(request):
    """Month grid in Manila time: chips on desktop, dots plus a day agenda on phones."""
    user = request.user
    today = timezone.localdate()
    year, month = parse_month(request.GET.get("month"), today)
    filters = _parse_filters(request)

    start, end = month_window(year, month)
    queryset = filters.apply(visible_events(user), user).select_related("extension")
    events = _with_actions(list(events_in_window(queryset, start, end)), user)
    weeks = build_month(year, month, events, today)

    keep_filters = filters.params()
    this_month = (year, month) == (today.year, today.month)
    selected = today if this_month else dt.date(year, month, 1)
    prev_year, prev_month = shift_month(year, month, -1)
    next_year, next_month = shift_month(year, month, 1)

    response = render(request, "events/calendar.html", {
        "weeks": weeks,
        "weekdays": WEEKDAYS,
        "month_label": dt.date(year, month, 1),
        "selected_iso": selected.isoformat(),
        "prev_url": _url("events:calendar", month=month_param(prev_year, prev_month), **keep_filters),
        "next_url": _url("events:calendar", month=month_param(next_year, next_month), **keep_filters),
        "today_url": _url("events:calendar", **keep_filters),
        "here": request.get_full_path(),
        "can_create": permissions.can_manage_events(user),
        "toolbar": _toolbar(request, filters, "calendar", {} if this_month else {"month": month_param(year, month)}),
    })
    return _remember(response, "calendar")


@events_screen
def list_view(request):
    """Events grouped by date: Upcoming (soonest first) or Past (latest first)."""
    user = request.user
    when = "past" if request.GET.get("when") == "past" else "upcoming"
    filters = _parse_filters(request)

    queryset = (
        filters.apply(visible_events(user), user)
        .select_related("extension")
        .annotate(ends_at=Coalesce("end_at", "start_at"))     # same "is it over" rule as cancel_event
    )
    now = timezone.now()
    if when == "upcoming":
        queryset = queryset.filter(ends_at__gte=now).order_by("start_at", "pk")
    else:
        queryset = queryset.filter(ends_at__lt=now).order_by("-start_at", "-pk")

    page = Paginator(queryset, PER_PAGE).get_page(request.GET.get("page"))
    events = _with_actions(list(page.object_list), user)
    groups = [
        {"date": day, "events": list(items)}
        for day, items in groupby(events, key=lambda e: timezone.localtime(e.start_at).date())
    ]

    query = request.GET.copy()
    query.pop("page", None)
    keep = {"when": "past"} if when == "past" else {}
    tabs = [
        {"label": "Upcoming", "url": _url("events:list", **filters.params()), "active": when == "upcoming"},
        {"label": "Past", "url": _url("events:list", when="past", **filters.params()), "active": when == "past"},
    ]
    response = render(request, "events/list.html", {
        "groups": groups,
        "page": page,
        "querystring": query.urlencode(),
        "when": when,
        "tabs": tabs,
        "here": request.get_full_path(),
        "toolbar": _toolbar(request, filters, "list", keep),
    })
    return _remember(response, "list")


# ----------------------------------------------------------------- one event
@events_screen
def detail_view(request, pk):
    """One event. 404 for anything the person may not see."""
    event = get_object_or_404(visible_events(request.user).select_related("extension"), pk=pk)
    return render(request, "events/detail.html", {
        "event": event,
        "actions": available_actions(request.user, event),
        "here": request.get_full_path(),
    })


def _initial_from_date(request):
    """Prefill the start from ?date=YYYY-MM-DD (a clicked calendar day); ignore anything else."""
    try:
        day = dt.date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        return {}
    return {"start_at": dt.datetime.combine(day, DEFAULT_START)}


@events_screen
def create_view(request):
    """New event form (admin, super-admin, or a coordinator for their own extension)."""
    user = request.user
    if not permissions.can_manage_events(user):
        messages.error(request, "You cannot create events.")
        return redirect("events:index")

    if request.method == "POST":
        form = EventForm(request.POST, actor=user)
        if form.is_valid():
            data = form.cleaned_data
            try:
                event = services.create_event(
                    user, extension_id=form.extension_id, title=data["title"],
                    start_at=data["start_at"], end_at=data["end_at"],
                    description=data["description"], location=data["location"],
                    token=data["token"] or None, request=request,
                )
            except PermissionDenied:
                messages.error(request, "You cannot create an event for that extension.")
                return redirect("events:index")
            except ServiceError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"{event.title} was created.")
                return redirect("events:detail", pk=event.pk)
    else:
        form = EventForm(actor=user, initial=_initial_from_date(request))

    return render(request, "events/form.html", {
        "form": form,
        "event": None,
        "form_title": "New event",
        "confirm_title": "Save event",
        "confirm_message": "Save this event?",
    })


@events_screen
def edit_view(request, pk):
    """Edit form. Scope is fixed; a started event keeps its start."""
    user = request.user
    event = get_object_or_404(visible_events(user).select_related("extension"), pk=pk)
    if not permissions.can_edit(user, event):
        messages.error(
            request,
            "A cancelled event cannot be changed." if event.is_cancelled else "You cannot edit this event.",
        )
        return redirect("events:detail", pk=event.pk)

    if request.method == "POST":
        form = EventForm(request.POST, actor=user, event=event)
        if form.is_valid():
            data = form.cleaned_data
            try:
                services.update_event(
                    user, event, title=data["title"], start_at=data["start_at"], end_at=data["end_at"],
                    description=data["description"], location=data["location"], request=request,
                )
            except PermissionDenied:
                messages.error(request, "You cannot edit this event.")
                return redirect("events:detail", pk=event.pk)
            except ServiceError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"{data['title']} was saved.")
                return redirect("events:detail", pk=event.pk)
    else:
        form = EventForm(actor=user, event=event)

    return render(request, "events/form.html", {
        "form": form,
        "event": event,
        "form_title": "Edit event",
        "confirm_title": "Save changes",
        "confirm_message": "Save your changes? If you changed the time or place, people will be notified.",
    })


@events_screen
@require_POST
def cancel_view(request, pk):
    """Cancel an event for good (POST only), then go back to where the person came from."""
    event = get_object_or_404(visible_events(request.user), pk=pk)
    try:
        services.cancel_event(request.user, event, request=request)
    except PermissionDenied:
        messages.error(request, "You cannot cancel this event.")
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{event.title} was cancelled.")
    return redirect(_safe_next(request, reverse("events:detail", args=[event.pk])))
