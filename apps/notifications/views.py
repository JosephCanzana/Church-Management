"""Notification screens: the full list, open one (mark read, then go to its page) and mark all read.

A notification only says that something happened. The page it links to does the permission check.
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import Role
from apps.notifications import read_state
from apps.notifications.display import decorate, target_url
from apps.notifications.models import Notification

PAGE_SIZE = 20
ALL_ROLES = (Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER)


def _safe_url(request, url, fallback):
    """Return ``url`` only when it stays on this site; otherwise the fallback."""
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return url
    return fallback


@role_required(*ALL_ROLES)
@require_GET
def notification_list(request):
    """Show the person's notifications, newest first, with All and Unread tabs."""
    unread_only = request.GET.get("filter") == "unread"
    queryset = Notification.objects.filter(user=request.user).select_related("actor")
    if unread_only:
        queryset = queryset.filter(read_at__isnull=True)
    page = Paginator(queryset.order_by("-created_at", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "notifications/list.html", {
        "items": decorate(list(page.object_list)),
        "page": page,
        "unread_only": unread_only,
        "querystring": "filter=unread" if unread_only else "",
    })


@role_required(*ALL_ROLES)
@require_POST
def notification_open(request, pk):
    """Mark one notification read, then send the person to the page it links to."""
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    read_state.mark_read(request.user, notification.pk)
    return redirect(target_url(notification) or reverse("notifications:list"))


@role_required(*ALL_ROLES)
@require_POST
def notification_read_all(request):
    """Mark everything read and return to the page the person was on."""
    changed = read_state.mark_all_read(request.user)
    if changed:
        messages.success(request, "All notifications marked as read.")
    else:
        messages.info(request, "You're already up to date.")
    return redirect(_safe_url(request, request.POST.get("next", ""), reverse("notifications:list")))
