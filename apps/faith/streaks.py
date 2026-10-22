"""faith.streaks: the daily-open and Bible-reading streak engine.

Callers pass the Manila calendar date (``timezone.localdate()``); this module never reads the
clock, so a streak can be tested for any day. Open and read streaks are independent: each has
its own current / longest / last-date columns on ``UserStreak``.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import F

from .models import UserDailyActivity, UserStreak

OPEN = "open"
READ = "read"


@dataclass(frozen=True)
class StreakSnapshot:
    """Current and longest streak for both kinds, ready for a template (``snap.open_current``)."""

    open_current: int = 0
    open_longest: int = 0
    read_current: int = 0
    read_longest: int = 0


def record_open(user, on_date):
    """Record that ``user`` opened the app on ``on_date`` and advance the open streak.

    Safe to call many times a day: the day's row is created once and the streak moves once.
    """
    with transaction.atomic():
        activity, _ = UserDailyActivity.objects.get_or_create(user=user, activity_date=on_date)
        if not activity.opened_app:
            UserDailyActivity.objects.filter(pk=activity.pk).update(opened_app=True)
        _advance(user, OPEN, on_date)


def record_read(user, on_date):
    """Record one chapter read by ``user`` on ``on_date`` and advance the read streak.

    ``chapters_read`` grows by one per call. The caller (the bible signal listener) only calls
    this for a NEW read row, so each chapter counts once a day.
    """
    with transaction.atomic():
        activity, _ = UserDailyActivity.objects.get_or_create(user=user, activity_date=on_date)
        UserDailyActivity.objects.filter(pk=activity.pk).update(chapters_read=F("chapters_read") + 1)
        _advance(user, READ, on_date)


def _advance(user, kind, on_date):
    """Move one streak (``kind`` is OPEN or READ) forward for ``on_date``, under a row lock.

    Rules: a date later than the last one counts; yesterday means +1, any gap resets to 1.
    The same day, or an older date, changes nothing. Longest is the maximum ever reached.
    """
    UserStreak.objects.get_or_create(user=user)
    # Primary key of UserStreak is the user, so pk=user.pk finds the row; lock it before reading.
    streak = UserStreak.objects.select_for_update().get(pk=user.pk)

    last = getattr(streak, f"last_{kind}_date")
    if last is not None and on_date <= last:
        return  # same day twice, or an out-of-order older date: never moves a streak

    current = getattr(streak, f"current_{kind}_streak")
    if last is not None and on_date - last == timedelta(days=1):
        current += 1
    else:
        current = 1  # first ever day, or a gap

    longest = max(getattr(streak, f"longest_{kind}_streak"), current)
    setattr(streak, f"current_{kind}_streak", current)
    setattr(streak, f"longest_{kind}_streak", longest)
    setattr(streak, f"last_{kind}_date", on_date)
    streak.save(update_fields=[
        f"current_{kind}_streak", f"longest_{kind}_streak", f"last_{kind}_date",
    ])


def streak_snapshot(user, today):
    """Return a ``StreakSnapshot`` for ``user`` as seen on ``today``. Read-only, never creates a row.

    A current streak shows 0 once its last date is older than yesterday, so yesterday's streak
    stays alive all day today until the person acts.
    """
    streak = UserStreak.objects.filter(pk=user.pk).first()
    if streak is None:
        return StreakSnapshot()
    return StreakSnapshot(
        open_current=_alive(streak.current_open_streak, streak.last_open_date, today),
        open_longest=streak.longest_open_streak,
        read_current=_alive(streak.current_read_streak, streak.last_read_date, today),
        read_longest=streak.longest_read_streak,
    )


def _alive(current, last_date, today):
    """Return ``current`` while ``last_date`` is today or yesterday (or later), else 0."""
    if last_date is None or last_date < today - timedelta(days=1):
        return 0
    return current
