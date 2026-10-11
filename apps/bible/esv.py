"""bible.esv: reading ESV chapters safely (switches, limits, locks and a tiny cache).

``get_verses()`` is the one entry point. In order it:

1. returns the chapter from the cache when it is there (no API call, so cached
   chapters keep working even while ESV is locked or switched off);
2. refuses with ``VersionLocked`` when ESV is not usable (no key, switched off,
   locked after a problem or after a limit was reached);
3. counts the call against our own budget (kept below Crossway's limits);
4. fetches the chapter, caches it, and returns it. A failure locks ESV for a
   while and raises ``VersionLocked`` so the page can say why.

State that must be shared by every web worker lives in ``site_setting`` rows:

* ``bible_api_enabled``   master switch for ALL external Bible APIs (default on)
* ``bible_esv_enabled``   switch for ESV only (default on)
* ``bible_esv_state``     call counters and the current lock
* ``bible_esv_cache_lock`` row used as a mutex while the cache is changed
"""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import SiteSetting

from . import esv_client
from .errors import VersionLocked
from .models import EsvChapterCache

KEY_MASTER = "bible_api_enabled"
KEY_ESV = "bible_esv_enabled"
KEY_STATE = "bible_esv_state"
KEY_CACHE_LOCK = "bible_esv_cache_lock"

# Crossway allows 60/minute, 1,000/hour, 5,000/day. We stay clearly below.
BUDGET_PER_MINUTE = 50
BUDGET_PER_HOUR = 900
BUDGET_PER_DAY = 4500

# Crossway allows at most 500 cached verses in total; keep a margin.
ESV_CACHE_MAX_VERSES = 450
ESV_CACHE_MAX_AGE = timedelta(days=7)

# How long ESV stays locked after each kind of problem.
FALLBACK_THROTTLE_SECONDS = 15 * 60
AUTH_PROBLEM_SECONDS = 60 * 60
OTHER_PROBLEM_SECONDS = 60

# What people see (never mention keys or limits' exact numbers).
MSG_NOT_SET_UP = "ESV is not set up yet."
MSG_ALL_OFF = "The online Bible services are turned off by an administrator."
MSG_ESV_OFF = "ESV is turned off by an administrator."
MSG_THROTTLED = "ESV asked us to slow down. It will unlock by itself."
MSG_PROBLEM = "ESV is not responding right now. It will unlock by itself."
MSG_DAY = "ESV's daily limit has been reached. It will unlock by itself."
MSG_HOUR = "ESV's hourly limit has been reached. It will unlock by itself."
MSG_MINUTE = "ESV is busy right now. Try again in a minute."

_WINDOWS = (
    ("minute", "%Y%m%d%H%M", BUDGET_PER_MINUTE, MSG_MINUTE),
    ("hour", "%Y%m%d%H", BUDGET_PER_HOUR, MSG_HOUR),
    ("day", "%Y%m%d", BUDGET_PER_DAY, MSG_DAY),
)


def _api_key():
    """The ESV key from settings (empty string when not configured)."""
    return getattr(settings, "ESV_API_KEY", "") or ""


def _parse_time(value):
    """Read a stored ISO time, or None."""
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _read_settings():
    """Fetch the switches and the state in one query: (master, esv, state)."""
    rows = {r.key: r.value for r in SiteSetting.objects.filter(key__in=[KEY_MASTER, KEY_ESV, KEY_STATE])}
    return bool(rows.get(KEY_MASTER, True)), bool(rows.get(KEY_ESV, True)), rows.get(KEY_STATE) or {}


def lock_reason():
    """Why ESV cannot be used right now, or "" when it can."""
    if not _api_key():
        return MSG_NOT_SET_UP
    master, esv_on, state = _read_settings()
    if not master:
        return MSG_ALL_OFF
    if not esv_on:
        return MSG_ESV_OFF
    until = _parse_time(state.get("locked_until"))
    if until and until > timezone.now():
        return state.get("reason") or MSG_PROBLEM
    return ""


def _locked_state_row():
    """The state row, locked for update (created on first use)."""
    row, _ = SiteSetting.objects.select_for_update().get_or_create(key=KEY_STATE, defaults={"value": {}})
    row.value = row.value or {}
    return row


def _end_of_window(name, now):
    """When the current minute, hour or UTC day ends."""
    if name == "minute":
        return now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    if name == "hour":
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return datetime(now.year, now.month, now.day, tzinfo=dt_timezone.utc) + timedelta(days=1)


def _lock(seconds, reason):
    """Lock ESV for a number of seconds with a reason people can read."""
    with transaction.atomic():
        row = _locked_state_row()
        row.value["locked_until"] = (timezone.now() + timedelta(seconds=seconds)).isoformat()
        row.value["reason"] = reason
        row.save()


def _reserve_call():
    """Count one API call. Returns "" if allowed, else the reason it is refused.

    When a window is full, ESV is locked until that window ends.
    """
    now = timezone.now()
    with transaction.atomic():
        row = _locked_state_row()
        state = row.value
        for name, fmt, _limit, _msg in _WINDOWS:
            key = now.strftime(fmt)
            if state.get(name, {}).get("key") != key:
                state[name] = {"key": key, "count": 0}
        for name, _fmt, limit, message in reversed(_WINDOWS):  # day first, then hour, then minute
            if state[name]["count"] >= limit:
                state["locked_until"] = _end_of_window(name, now).isoformat()
                state["reason"] = message
                row.save()
                return message
        for name, _fmt, _limit, _msg in _WINDOWS:
            state[name]["count"] += 1
        row.save()
    return ""


def _cache_get(osis_id, chapter):
    """A fresh cached chapter as [(number, text)], or None."""
    now = timezone.now()
    row = EsvChapterCache.objects.filter(
        book=osis_id, chapter=chapter, fetched_at__gte=now - ESV_CACHE_MAX_AGE,
    ).first()
    if row is None:
        return None
    EsvChapterCache.objects.filter(pk=row.pk).update(last_used_at=now)
    return [(int(number), text) for number, text in row.verses]


def _cache_put(osis_id, chapter, verses):
    """Store a chapter, first making room so the total stays under the cap."""
    count = len(verses)
    if not 1 <= count <= ESV_CACHE_MAX_VERSES:
        return
    now = timezone.now()
    with transaction.atomic():
        SiteSetting.objects.select_for_update().get_or_create(key=KEY_CACHE_LOCK, defaults={"value": True})
        EsvChapterCache.objects.filter(fetched_at__lt=now - ESV_CACHE_MAX_AGE).delete()
        EsvChapterCache.objects.filter(book=osis_id, chapter=chapter).delete()
        total = EsvChapterCache.objects.aggregate(total=Sum("verse_count"))["total"] or 0
        if total + count > ESV_CACHE_MAX_VERSES:
            for old in EsvChapterCache.objects.order_by("last_used_at", "pk"):
                if total + count <= ESV_CACHE_MAX_VERSES:
                    break
                total -= old.verse_count
                old.delete()
        EsvChapterCache.objects.create(
            book=osis_id, chapter=chapter, verses=[[n, t] for n, t in verses],
            verse_count=count, fetched_at=now, last_used_at=now,
        )


def get_verses(osis_id, chapter):
    """Return [(verse number, text)] for an ESV chapter or raise VersionLocked."""
    cached = _cache_get(osis_id, chapter)
    if cached is not None:
        return cached
    reason = lock_reason()
    if reason:
        raise VersionLocked(reason)
    refused = _reserve_call()
    if refused:
        raise VersionLocked(refused)
    try:
        verses = esv_client.fetch_chapter(_api_key(), osis_id, chapter)
    except esv_client.EsvThrottled as error:
        _lock(error.retry_after or FALLBACK_THROTTLE_SECONDS, MSG_THROTTLED)
        raise VersionLocked(MSG_THROTTLED) from error
    except esv_client.EsvAuthError as error:
        _lock(AUTH_PROBLEM_SECONDS, MSG_PROBLEM)
        raise VersionLocked(MSG_PROBLEM) from error
    except esv_client.EsvError as error:
        _lock(OTHER_PROBLEM_SECONDS, MSG_PROBLEM)
        raise VersionLocked(MSG_PROBLEM) from error
    _cache_put(osis_id, chapter, verses)
    return verses
