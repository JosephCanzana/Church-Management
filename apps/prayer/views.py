"""prayer.views: the member screens (send a request, see my requests). Views stay thin: form -> service -> toast."""
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import home_url_for, role_required
from apps.accounts.models import Role
from apps.core.services import ServiceError, new_submission_token

from . import permissions, services
from .forms import PrayerSendForm
from .models import PrayerRequest

PAGE_SIZE = 10
LIST_TEMPLATE = "prayer/list.html"


def member_screen(view):
    """Guard: signed in, role member, allowed to send. Anyone else goes to their own home page."""

    def guarded(request, *args, **kwargs):
        if not permissions.can_send(request.user):
            messages.error(request, "Prayer requests are for members.")
            return redirect(home_url_for(request.user))
        return view(request, *args, **kwargs)

    guarded.__name__ = view.__name__
    guarded.__doc__ = view.__doc__
    return role_required(Role.MEMBER)(never_cache(guarded))


def _list_context(request, form):
    """The send form plus the person's own requests (newest first) with their replies."""
    queryset = (
        PrayerRequest.objects.mine(request.user)
        .prefetch_related("replies__replier")
        .order_by("-created_at", "-pk")
    )
    page = Paginator(queryset, PAGE_SIZE).get_page(request.GET.get("page"))
    for item in page.object_list:
        item.sender = permissions.sender_label(item, request.user)
        item.reply_rows = [
            {"body": reply.body, "created_at": reply.created_at,
             "by": permissions.person_name(reply.replier) if reply.replier_id else "A leader"}
            for reply in item.replies.all()
        ]
    return {
        "form": form, "page": page, "querystring": "",
        "submit_token": new_submission_token(), "max_per_day": services.MAX_PER_DAY,
    }


@member_screen
@require_http_methods(["GET"])
def prayer_list(request):
    """The send form and "My requests"."""
    return render(request, LIST_TEMPLATE, _list_context(request, PrayerSendForm()))


@member_screen
@require_http_methods(["POST"])
def prayer_send(request):
    """Save a new request. A double tap carries the same one-time token, so it sends once and the repeat is quiet."""
    form = PrayerSendForm(request.POST)
    if form.is_valid():
        try:
            result = services.send_prayer_request(
                request.user, form.cleaned_data["body"], form.cleaned_data["is_anonymous"],
                request.POST.get("submit_token", ""),
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            if result.duplicate:
                messages.info(request, "Your prayer request was already sent.")
            else:
                messages.success(request, "Your prayer request was sent. We are praying with you.")
            return redirect("prayer:list")
    return render(request, LIST_TEMPLATE, _list_context(request, form))
