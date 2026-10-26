"""buddy.message_views: the chat endpoints of the Buddy page (Brick 8).

The chat is no longer a page of its own: the Buddy hub page (`views.home`) draws it from
`chat_context()` below. `thread` only keeps the old /buddy/messages/ address alive (it redirects).
The small endpoints the chat calls with fetch are here: send, nudge, prayed, poll, earlier and read. Every view is login-guarded, `never_cache`, and only ever
works on the signed-in person's OWN open partnership. No view takes a partner or partnership id from
the request, so there is nothing to tamper with.

The endpoints answer JSON when the request is a fetch (`X-Requested-With` or `Accept: application/json`)
and otherwise redirect back with a Django message.
"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.core.services import ServiceError
from apps.core.services import new_submission_token  # VERIFY: where the project defines this helper

from . import messaging
from .permissions import can_use_buddy


def _guarded(*methods):
    """Login required, only these HTTP methods, never cached, and only people who may use Buddy."""
    def decorate(view):
        @login_required
        @never_cache
        @require_http_methods(list(methods))
        @wraps(view)
        def inner(request, *args, **kwargs):
            if not can_use_buddy(request.user):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return inner
    return decorate


def _wants_json(request):
    """True for the page's fetch calls."""
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


def _int_param(request, name):
    """A non-negative integer query parameter, or None when missing or not a number."""
    try:
        value = int(request.GET.get(name, ""))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _reply(request, *, ok, error="", status=200, **extra):
    """JSON for fetch calls; a message and a redirect for plain form posts."""
    if _wants_json(request):
        return JsonResponse({"ok": ok, "error": error, **extra}, status=status if not ok else 200)
    (messages.success if ok else messages.error)(request, error or "Done.")
    return redirect("buddy:home")


def chat_context(user):
    """`{"chat": ..., "payload": ...}` for the Buddy page: the thread and the JSON the chat starts from.

    `chat` is `messaging.thread_for(user)` (its `active` flag is False without an open partnership).
    `payload` is None when there is no chat; otherwise it holds the first page of notes, a one-time
    token, the limits and the endpoint URLs. A GET never writes anything (nothing is marked read).
    """
    data = messaging.thread_for(user)
    payload = None
    if data["active"]:
        payload = {
            "items": data["items"],
            "has_more": data["has_more"],
            "oldest_id": data["oldest_id"],
            "newest_id": data["newest_id"] or 0,
            "read_only": data["read_only"],
            "partner_name": data["partner_name"],
            "last_mine_id": data["last_mine_id"],
            "last_mine_seen": data["last_mine_seen"],
            "token": new_submission_token(),
            **messaging.limits_for(user),
            "urls": {
                "send": reverse("buddy:message_send"),
                "nudge": reverse("buddy:message_nudge"),
                "prayed": reverse("buddy:message_prayed"),
                "poll": reverse("buddy:message_poll"),
                "earlier": reverse("buddy:message_earlier"),
                "read": reverse("buddy:message_read"),
            },
        }
    return {"chat": data, "payload": payload}


@_guarded("GET")
def thread(request):
    """The old Messages address: the chat now lives on the Buddy page, so go there."""
    return redirect("buddy:home")


def _write(request, action):
    """Run one write service and shape the reply (shared by nudge and prayed)."""
    try:
        note = action(request.user)
    except ServiceError as exc:
        return _reply(request, ok=False, error=str(exc), status=400)
    return _reply(
        request, ok=True, note=messaging.note_item(note, request.user.pk),
        **messaging.limits_for(request.user), **messaging.seen_state(request.user),
    )


@_guarded("POST")
def send(request):
    """Send a text message. The reply carries a fresh one-time token for the next message."""
    try:
        note = messaging.send_message(request.user, request.POST.get("body", ""), request.POST.get("token", ""))
    except ServiceError as exc:
        # The refused message gave its token back, but a fresh one is harmless and keeps the page simple.
        return _reply(request, ok=False, error=str(exc), status=400, token=new_submission_token())
    extra = {"token": new_submission_token(), **messaging.seen_state(request.user)}
    if note is None:                         # a replayed token: nothing was sent twice
        return _reply(request, ok=True, duplicate=True, **extra)
    return _reply(request, ok=True, note=messaging.note_item(note, request.user.pk), **extra)


@_guarded("POST")
def nudge(request):
    """Send a 👋."""
    return _write(request, messaging.send_nudge)


@_guarded("POST")
def prayed(request):
    """Send "I prayed for you"."""
    return _write(request, messaging.send_prayed)


@_guarded("GET")
def poll(request):
    """Notes newer than `?after=<id>` (the page asks every ~10 seconds while it is visible)."""
    after = _int_param(request, "after") or 0
    return JsonResponse({"ok": True, **messaging.poll_since(request.user, after)})


@_guarded("GET")
def earlier(request):
    """The page of notes before `?before=<id>` ("Load earlier messages")."""
    before = _int_param(request, "before")
    if before is None:
        return JsonResponse({"ok": False, "error": "Missing position."}, status=400)
    data = messaging.thread_for(request.user, before=before)
    return JsonResponse({
        "ok": True, "items": data["items"], "has_more": data["has_more"], "oldest_id": data["oldest_id"],
    })


@_guarded("POST")
def read(request):
    """Mark the partner's notes as read. The page calls this while the thread is open and visible."""
    return JsonResponse({"ok": True, "marked": messaging.mark_read(request.user)})
