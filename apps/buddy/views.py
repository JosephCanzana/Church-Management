"""buddy.views: thin screens for finding a buddy, answering requests and the partner card.

Every view is wrapped in `buddy_screen` (sign-in, no cache, permission). Views read the
request, call a service and turn a `ServiceError` into a message; the rules live in services.
"""
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.services import ServiceError

from . import services
from .permissions import buddy_screen


def _back_to_home(request):
    """Redirect to the Buddy tab, keeping the search text and page the person was on.

    Only `q` and `page` are copied (rebuilt, never a raw `next` URL), so this cannot be
    used as an open redirect.
    """
    params = {}
    query = (request.POST.get("q") or "").strip()[:100]
    if query:
        params["q"] = query
    page = request.POST.get("page") or ""
    if page.isdigit() and int(page) > 1:
        params["page"] = page
    url = reverse("buddy:home")
    return redirect(f"{url}?{urlencode(params)}" if params else url)


@buddy_screen
def home(request):
    """The Buddy tab. With a live partner: the partner card. Without: search, list, requests."""
    user = request.user
    card = services.partner_card(user)
    context = {"card": card, "ending": services.partnership_state(user)}
    if card is None:
        outgoing = services.outgoing_requests(user)
        context.update(
            search=services.candidates(user, request.GET.get("q", ""), request.GET.get("page")),
            incoming=services.incoming_requests(user),
            outgoing=outgoing,
            max_pending=services.MAX_PENDING_REQUESTS,
            at_cap=len(outgoing) >= services.MAX_PENDING_REQUESTS,
        )
    return render(request, "buddy/home.html", context)


@buddy_screen
@require_POST
def request_send(request, user_id):
    """Send a buddy request to the person with this id."""
    try:
        services.send_request(request.user, user_id)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Request sent.")
    return _back_to_home(request)


@buddy_screen
@require_POST
def request_accept(request, request_id):
    """Accept a request that was sent to you."""
    try:
        row = services.accept_request(request.user, request_id)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        partner = services.partner_of(row, request.user)
        messages.success(request, f"You and {partner.full_name} are now buddies.")
    return _back_to_home(request)


@buddy_screen
@require_POST
def request_decline(request, request_id):
    """Decline a request that was sent to you."""
    try:
        services.decline_request(request.user, request_id)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Request declined.")
    return _back_to_home(request)


@buddy_screen
@require_POST
def request_cancel(request, request_id):
    """Cancel a request you sent."""
    try:
        services.cancel_request(request.user, request_id)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Request cancelled.")
    return _back_to_home(request)


def _back_to_buddy(request, ok_message, action, *args):
    """Run an ending service, show its message, and go back to the Buddy tab."""
    try:
        action(request.user, *args)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, ok_message)
    return redirect("buddy:home")


@buddy_screen
@require_POST
def end_request(request):
    """Ask your buddy to end the partnership (optional final note in `note`)."""
    return _back_to_buddy(
        request, "Your request was sent. Your buddy has to agree before it ends.",
        services.request_end, request.POST.get("note"),
    )


@buddy_screen
@require_POST
def end_cancel(request):
    """Take back your own end request."""
    return _back_to_buddy(request, "Your request to end was cancelled.", services.cancel_end_request)


@buddy_screen
@require_POST
def end_agree(request):
    """Agree to end the partnership your buddy asked to end (optional note in `note`)."""
    return _back_to_buddy(request, "The partnership has ended.", services.agree_end, request.POST.get("note"))


@buddy_screen
@require_POST
def end_inactive(request):
    """End a partnership whose other person can no longer sign in. No approval is needed."""
    return _back_to_buddy(
        request, "The partnership has ended.", services.end_without_approval, request.POST.get("note"),
    )

@buddy_screen
def partner_goals(request):
    """The "Their goals" tab: the live partner's goals, read-only. No id in the URL."""
    try:
        goals_view = services.partner_goals_for(request.user)
    except ServiceError as error:
        messages.error(request, str(error))
        return redirect("buddy:home")
    return render(request, "buddy/partner_goals.html", {"goals_view": goals_view})