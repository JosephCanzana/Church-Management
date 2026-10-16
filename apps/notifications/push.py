"""notifications.push: Web Push delivery (Brick 12).

The bell is always the source of truth. A push is an extra nudge that reaches a phone or desktop when the
app is closed, so everything here is best effort and must never raise into the caller.

Rules (AGENTS.md):
- PRIVACY: the payload is a title, a short body and a relative link. No amounts, no prayer text, no names
  of private records. Subscription endpoints and keys are never logged: failures log the subscription id
  and the HTTP status only.
- VAPID keys live in `.env` (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`). Without them
  `is_enabled()` is False and every function here does nothing.
- `notifications` never imports a feature app: callers pass plain ids and a message mapping.
"""
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone

from .models import PushSubscription

logger = logging.getLogger(__name__)

DEFAULT_URL = "/notifications/"
TITLE_MAX = 80
BODY_MAX = 160
SEND_TIMEOUT = 10       # seconds per device; a slow push service must not hang a worker

_executor = None        # created on first use (see `_get_executor`)


# ---------------------------------------------------------------- configuration
def is_enabled():
    """True when all three VAPID settings are filled in. Without them push is hidden and skipped."""
    return bool(
        getattr(settings, "VAPID_PUBLIC_KEY", "")
        and getattr(settings, "VAPID_PRIVATE_KEY", "")
        and getattr(settings, "VAPID_SUBJECT", "")
    )


def hash_endpoint(endpoint):
    """SHA-256 hex of an endpoint URL: the stable lookup key of a device."""
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def endpoint_allowed(endpoint):
    """Only real push services may be called.

    The server POSTs to whatever endpoint a browser hands over, so without this check anyone signed in
    could aim the server at an internal address (SSRF). The allowed hosts are `PUSH_ENDPOINT_HOSTS`:
    an entry starting with "." matches any subdomain, any other entry must match exactly.
    """
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return False
    if port not in (None, 443):
        return False
    host = parts.hostname.lower()
    for allowed in settings.PUSH_ENDPOINT_HOSTS:
        allowed = allowed.lower()
        if allowed.startswith("."):
            if host.endswith(allowed):
                return True
        elif host == allowed:
            return True
    return False


def device_label(user_agent):
    """A short human label such as "Chrome on Android" from a user agent string (the raw string is not kept)."""
    ua = user_agent or ""
    if "Edg/" in ua or "EdgA/" in ua or "EdgiOS/" in ua:
        browser = "Edge"
    elif "OPR/" in ua:
        browser = "Opera"
    elif "SamsungBrowser" in ua:
        browser = "Samsung Internet"
    elif "Firefox/" in ua or "FxiOS/" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS/" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "Browser"
    # Order matters: iPhone and Android agents also mention "Mac OS X" and "Linux".
    if "iPhone" in ua:
        system = "iPhone"
    elif "iPad" in ua:
        system = "iPad"
    elif "Android" in ua:
        system = "Android"
    elif "Windows" in ua:
        system = "Windows"
    elif "CrOS" in ua:
        system = "ChromeOS"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        system = "macOS"
    elif "Linux" in ua:
        system = "Linux"
    else:
        system = "unknown system"
    return f"{browser} on {system}"[:80]


# ---------------------------------------------------------------- message
def clean_message(message):
    """Return a safe payload dict from a mapping with `title`, `body`, `url` and optional `tag`.

    Text is flattened to one line and cut short. The link must be a path on this site: anything else
    (another host, `//host`, a backslash trick) falls back to the notifications page.
    """
    message = message or {}
    title = " ".join(str(message.get("title") or "").split())[:TITLE_MAX] or "Church Management"
    body = " ".join(str(message.get("body") or "").split())[:BODY_MAX]
    url = str(message.get("url") or DEFAULT_URL)
    if not url.startswith("/") or url.startswith("//") or "\\" in url:
        url = DEFAULT_URL
    tag = str(message.get("tag") or "")[:60]
    return {"title": title, "body": body, "url": url, "tag": tag}


# ---------------------------------------------------------------- delivery
def _send_one(subscription, payload):
    """Push `payload` (a JSON string) to one device. Returns "sent", "gone" (404/410) or "failed".

    Never logs the endpoint, the keys or the payload.
    """
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        logger.error("push: the pywebpush package is not installed")
        return "failed"
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=payload,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},   # a new dict each call: the library edits it
            ttl=settings.PUSH_TTL_SECONDS,
            timeout=SEND_TIMEOUT,
        )
        return "sent"
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            return "gone"
        logger.warning("push: delivery failed (subscription %s, status %s)", subscription.pk, status)
        return "failed"
    except Exception as exc:  # timeouts, DNS, bad key format: all "failed", never raised
        logger.warning("push: delivery error (subscription %s, %s)", subscription.pk, type(exc).__name__)
        return "failed"


def send_to_users(user_ids, message):
    """Push `message` to every device of every id in `user_ids`, synchronously.

    Returns counts: {"devices", "sent", "removed", "failed"}. Dead devices (404/410) are deleted.
    Never raises. Used directly by jobs (Brick 13) and by the test button.
    """
    result = {"devices": 0, "sent": 0, "removed": 0, "failed": 0}
    ids = list(dict.fromkeys(user_ids))
    if not ids or not is_enabled():
        return result
    try:
        subscriptions = list(PushSubscription.objects.filter(user_id__in=ids))
        result["devices"] = len(subscriptions)
        payload = json.dumps(clean_message(message), separators=(",", ":"))
        sent_ids, gone_ids = [], []
        for subscription in subscriptions:
            outcome = _send_one(subscription, payload)
            if outcome == "sent":
                sent_ids.append(subscription.pk)
            elif outcome == "gone":
                gone_ids.append(subscription.pk)
            else:
                result["failed"] += 1
        if gone_ids:
            PushSubscription.objects.filter(pk__in=gone_ids).delete()
        if sent_ids:
            PushSubscription.objects.filter(pk__in=sent_ids).update(last_success_at=timezone.now())
        result["sent"], result["removed"] = len(sent_ids), len(gone_ids)
    except Exception as exc:
        logger.error("push: delivery run failed (%s)", type(exc).__name__)
    return result


def send_test(user):
    """Push a test message to all of one person's devices. Returns the `send_to_users` counts."""
    return send_to_users(
        [user.pk],
        {"title": "Test notification", "body": "Notifications are working on this device.", "url": "/", "tag": "push-test"},
    )


def _get_executor():
    """A small shared pool, so a request never waits for the push services."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="push")
    return _executor


def _worker(user_ids, message):
    """Runs in a pool thread. Threads get their own database connection, so close it when done."""
    try:
        send_to_users(user_ids, message)
    finally:
        connections.close_all()


def dispatch_after_commit(user_ids, message):
    """Queue a push for `user_ids` once the surrounding transaction commits (so a rollback sends nothing).

    With `PUSH_ASYNC` on (the default) delivery happens in a background thread; tests turn it off.
    Nothing here can raise into the caller: the bell item already exists and must stay.
    """
    if not is_enabled():
        return
    ids = list(dict.fromkeys(user_ids))
    if not ids:
        return
    try:
        payload = clean_message(message)
    except Exception as exc:
        logger.error("push: bad message (%s)", type(exc).__name__)
        return

    def run():
        try:
            if getattr(settings, "PUSH_ASYNC", True):
                _get_executor().submit(_worker, ids, payload)
            else:
                send_to_users(ids, payload)
        except Exception as exc:
            logger.error("push: could not queue delivery (%s)", type(exc).__name__)

    transaction.on_commit(run)
