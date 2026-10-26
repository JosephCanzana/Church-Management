"""prayer.inbox_views: the inbox shared by coordinator, admin and super-admin (mounted from `area_urls.py`).

Three guards, all required: `role_required` (login), the area must belong to the person's role (a coordinator
who types the admin URL goes home), and `permissions.can_use_inbox`. Every request starts from
`PrayerRequest.objects.inbox_for(actor)`; a request that is missing and one that is out of reach look the same.
Sender names come ONLY from `permissions.sender_label`; templates never touch `request.user`.
"""
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import home_url_for, role_required
from apps.accounts.models import Role
from apps.core.services import ServiceError

from . import permissions, services
from .forms import PrayerReplyForm
from .models import PrayerRequest

PAGE_SIZE = 25
AREA_ROLES = {
    "superadmin": Role.SUPER_ADMIN,
    "admin_area": Role.ADMIN,
    "coordinator_area": Role.COORDINATOR,
}
TABS = (
    (PrayerRequest.Status.OPEN, "Open"),
    (PrayerRequest.Status.REPLIED, "Replied"),
)


def inbox_screen(view):
    """Guard for every inbox page: signed in, right area for the role, allowed into the inbox, never cached."""

    @wraps(view)
    def guarded(request, *args, **kwargs):
        if AREA_ROLES.get(request.resolver_match.namespace) != request.user.role:
            return redirect(home_url_for(request.user))
        if not permissions.can_use_inbox(request.user):
            messages.error(request, "You do not have access to the prayer inbox.")
            return redirect(home_url_for(request.user))
        return view(request, *args, **kwargs)

    return role_required(*AREA_ROLES.values())(never_cache(guarded))


def _url(request, name, *args):
    """URL of an inbox page in the area the request came from (never a hardcoded namespace)."""
    return reverse(f"{request.resolver_match.namespace}:{name}", args=args)


def _gone(request):
    """The answer for a request that is missing OR out of reach (the two look the same on purpose)."""
    messages.info(request, "That prayer request no longer exists.")
    return redirect(_url(request, "prayer_inbox"))


def _open(request, pk):
    """The request with this id IF it is in the person's inbox, else None."""
    return (
        PrayerRequest.objects.inbox_for(request.user)
        .select_related("user").prefetch_related("replies__replier").filter(pk=pk).first()
    )


@inbox_screen
@require_http_methods(["GET"])
def inbox(request):
    """The inbox with status tabs (Open, Replied) and pagination."""
    status = request.GET.get("status")
    if status not in {value for value, _label in TABS}:
        status = PrayerRequest.Status.OPEN
    base = PrayerRequest.objects.inbox_for(request.user)
    counts = {row["status"]: row["n"] for row in base.values("status").annotate(n=Count("pk"))}
    page = Paginator(base.filter(status=status).select_related("user").order_by("-created_at", "-pk"), PAGE_SIZE) \
        .get_page(request.GET.get("page"))
    for item in page.object_list:
        item.sender = permissions.sender_label(item, request.user)
        item.detail_url = _url(request, "prayer_detail", item.pk)
    return render(request, "prayer/inbox.html", {
        "page": page, "status": status, "querystring": f"status={status}",
        "tabs": [{"value": v, "label": label, "count": counts.get(v, 0), "active": v == status,
                  "href": f"{_url(request, 'prayer_inbox')}?status={v}"} for v, label in TABS],
        "no_coordinator_note": request.user.role in (Role.ADMIN, Role.SUPER_ADMIN),
    })


def _detail_context(request, found, form):
    """Everything the detail page needs, with the sender already masked."""
    return {
        "item": found, "sender": permissions.sender_label(found, request.user), "form": form,
        "replies": [{"body": r.body, "created_at": r.created_at,
                     "by": permissions.person_name(r.replier) if r.replier_id else "A leader"}
                    for r in found.replies.all()],
        "back_url": _url(request, "prayer_inbox"),
        "reply_url": _url(request, "prayer_reply", found.pk),
    }


@inbox_screen
@require_http_methods(["GET"])
def inbox_detail(request, pk):
    """One request with its replies and the reply form."""
    found = _open(request, pk)
    if found is None:
        return _gone(request)
    return render(request, "prayer/inbox_detail.html", _detail_context(request, found, PrayerReplyForm()))


@inbox_screen
@require_http_methods(["POST"])
def inbox_reply(request, pk):
    """Add a reply (the service marks it replied and tells the sender, with no text)."""
    found = _open(request, pk)
    if found is None:
        return _gone(request)
    form = PrayerReplyForm(request.POST)
    if form.is_valid():
        try:
            services.reply_to_request(request.user, pk, form.cleaned_data["body"])
        except (ServiceError, PermissionDenied) as exc:
            form.add_error(None, str(exc) if isinstance(exc, ServiceError) else "You cannot reply to this request.")
        else:
            messages.success(request, "Reply sent.")
            return redirect(_url(request, "prayer_detail", pk))
    return render(request, "prayer/inbox_detail.html", _detail_context(request, found, form))

