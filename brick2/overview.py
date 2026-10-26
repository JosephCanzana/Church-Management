"""faith.overview: the numbers behind the goal pictures (Brick 4).

Two entry points, both read-only and both taking ``today`` as a parameter (never ``now()``):

* ``goal_overview(goal, today)``: one goal's streaks, completion, 84-day heatmap, last 8 weeks, best
  weekday and a month grid;
* ``global_overview(user, today)``: every goal that is not archived (completed ones stay), as a
  heatmap, "perfect days", week and month rates, and the "opened the app" and "read the Bible" rows.

Each entry point is a thin database wrapper around a pure ``build_*`` function that takes plain
Python values (goals, sets of done dates). The pure functions never touch the database, so the
maths can be tested without any rows.

Rules shared by every number here:

* A day that is not scheduled never counts for or against anyone.
* TODAY IS STILL OPEN until midnight: a scheduled goal that is not ticked today is left out of the
  "expected" counts and never breaks a streak. Once ticked it counts as done. This is the same rule
  the Today screen uses for a goal's own streak.
* A streak is a run of scheduled days that were done, so a weekly goal's streak counts weeks' worth
  of occurrences, not calendar days.
* Check-in notes are never read. Only dates and the ``done`` flag leave the database.

Privacy: nothing in this module writes to ``audit_log`` or ``notification.data``. Goal titles are
returned to the caller (the page the owner is looking at) and nowhere else.
"""
import calendar
from collections import defaultdict
from datetime import timedelta
from fractions import Fraction

from apps.faith import schedule
from apps.faith.models import Goal, GoalCheckin, UserDailyActivity
from apps.faith.streaks import streak_snapshot

HEATMAP_WEEKS = 12          # 12 weeks x 7 days = the 84-day heatmap (Monday first, ends on this week's Sunday)
BAR_WEEKS = 8               # weeks shown in the per-goal bar chart
ACTIVITY_DAYS = 28          # days shown in the "opened the app" / "read the Bible" strips
MIN_BAR_PERCENT = 4         # a non-zero bar is never invisible (same rule as the dashboard charts)
MIN_WEEKDAY_SAMPLE = 2      # a weekday needs this many settled days before it can be called "best"
RING_CIRCUMFERENCE = 251.33  # 2 * pi * 40, for the r=40 progress ring drawn by faith/_ring.html

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Words used in the aria-label / title of a per-goal cell.
GOAL_STATE_TEXT = {
    "done": "done",
    "missed": "missed",
    "off": "not scheduled",
    "open": "still open today",
    "future": "upcoming",
}


# ------------------------------------------------------------------ small helpers
def _week_start(day):
    """Return the Monday of the week that holds ``day``."""
    return day - timedelta(days=day.weekday())


def _day_label(day):
    """Return a day as text for a label, for example ``Mon 3 Mar``."""
    return f"{day:%a} {day.day} {day:%b}"


def _short_date(day):
    """Return a short date such as ``Oct 12`` (same style as the dashboard charts)."""
    return f"{day:%b} {day.day}"


def _rate(done, expected):
    """Return a whole percent (half rounds up), or None when nothing was expected."""
    if not expected:
        return None
    return (200 * done + expected) // (2 * expected)


def _runs(hits):
    """Return ``(current, best)`` run lengths for booleans listed oldest first.

    ``current`` is the run that ends at the last item; ``best`` is the longest run anywhere.
    """
    run = best = 0
    for hit in hits:
        run = run + 1 if hit else 0
        best = max(best, run)
    return run, best


def _cell(day, state, text, **extra):
    """Return one picture cell: the date, a state name and the text for its title and aria-label."""
    return {"date": day, "state": state, "label": f"{_day_label(day)}: {text}", **extra}


def heatmap_days(today):
    """Return the 84 dates of the heatmap, Monday of the first week first, in week-by-week order.

    The last week is the current one, so days after ``today`` are in the list (shown as upcoming).
    """
    first = _week_start(today) - timedelta(weeks=HEATMAP_WEEKS - 1)
    return [first + timedelta(days=i) for i in range(HEATMAP_WEEKS * 7)]


def make_ring(pct):
    """Return what the progress ring template needs: percent, stroke dash length and the middle text.

    ``pct`` None means "nothing to measure yet": an empty ring with a dash in the middle.
    """
    shown = "-" if pct is None else f"{pct}%"
    pct = max(0, min(100, pct or 0))
    return {
        "pct": pct,
        "dash": f"{RING_CIRCUMFERENCE * pct / 100:.2f}",
        "circumference": f"{RING_CIRCUMFERENCE:.2f}",
        "text": shown,
    }


def time_label(value):
    """Return a clock time as ``6:30 AM``."""
    return f"{(value.hour % 12) or 12}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


def when_summary(goal):
    """Return the goal's time as text: ``All day``, ``6:00 AM`` or ``6:00 AM - 7:00 AM``."""
    if goal.start_time is None:
        return "All day"
    if goal.end_time is None:
        return time_label(goal.start_time)
    return f"{time_label(goal.start_time)} - {time_label(goal.end_time)}"


def repeat_summary(goal):
    """Return how the goal repeats as text, for example ``Every day`` or ``Weekly: Mon, Wed``."""
    if goal.repeat_type == "weekly":
        return "Weekly: " + ", ".join(WEEKDAY_SHORT[d] for d in sorted(goal.weekdays or []))
    if goal.end_date is not None and goal.end_date == goal.start_date:
        return "One time"
    return "Every day"


# ------------------------------------------------------------------ one goal
def _settled(goal, done_dates, today):
    """Return ``[(day, hit)]``, oldest first, for the goal's scheduled days up to today.

    Today is included only once it has been ticked (an unticked today is still open).
    """
    end = min(goal.end_date, today) if goal.end_date else today
    if end < goal.start_date:
        return []
    rows = []
    for day in schedule.scheduled_dates(goal, goal.start_date, end):
        hit = day in done_dates
        if hit or day != today:
            rows.append((day, hit))
    return rows


def _goal_cell(goal, day, done_dates, today, number=False):
    """Return the cell for one day of one goal: done, missed, off, open (today, not ticked) or future."""
    if day > today:
        state = "future"
    elif not schedule.is_scheduled(goal, day):
        state = "off"
    elif day in done_dates:
        state = "done"
    else:
        state = "open" if day == today else "missed"
    extra = {"number": day.day} if number else {}
    return _cell(day, state, GOAL_STATE_TEXT[state], **extra)


def _week_bars(settled, today):
    """Return the last ``BAR_WEEKS`` weeks as column-chart rows (label, display, short, pct), oldest first.

    The height is the share done that week; the number above the column is ``done/expected``.
    """
    totals = defaultdict(lambda: [0, 0])
    for day, hit in settled:
        week = totals[_week_start(day)]
        week[0] += hit
        week[1] += 1
    bars = []
    for back in range(BAR_WEEKS - 1, -1, -1):
        start = _week_start(today) - timedelta(weeks=back)
        done, expected = totals.get(start, (0, 0))
        pct = _rate(done, expected) or 0
        if done and pct < MIN_BAR_PERCENT:
            pct = MIN_BAR_PERCENT
        bars.append({
            "label": _short_date(start),
            "display": f"{done} of {expected} done" if expected else "nothing scheduled",
            "short": f"{done}/{expected}" if expected else "-",
            "pct": pct,
        })
    return bars


def _best_weekday(settled):
    """Return the weekday with the best done-rate, or None when there is not enough to say.

    A weekday needs ``MIN_WEEKDAY_SAMPLE`` settled days, and at least one of them done.
    A tie goes to the earlier weekday.
    """
    totals = defaultdict(lambda: [0, 0])
    for day, hit in settled:
        entry = totals[day.weekday()]
        entry[0] += hit
        entry[1] += 1
    best = None
    for weekday, (done, expected) in sorted(totals.items()):
        if expected < MIN_WEEKDAY_SAMPLE or not done:
            continue
        if best is None or Fraction(done, expected) > best[0]:
            best = (Fraction(done, expected), weekday, done, expected)
    if best is None:
        return None
    _share, weekday, done, expected = best
    return {"name": WEEKDAY_NAMES[weekday], "rate": _rate(done, expected), "done": done, "expected": expected}


def _month_grid(goal, done_dates, today):
    """Return this month as weeks of seven cells (Monday first). Days of other months are ``pad`` cells."""
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(today.year, today.month):
        row = []
        for day in week:
            if day.month != today.month:
                row.append({"date": day, "state": "pad", "label": "", "number": ""})
            else:
                row.append(_goal_cell(goal, day, done_dates, today, number=True))
        weeks.append(row)
    return {"label": f"{calendar.month_name[today.month]} {today.year}", "weeks": weeks}


def build_goal_overview(goal, done_dates, today):
    """Return everything the goal detail page draws, from plain values (no database).

    ``done_dates`` is the set of dates on which the goal was ticked (``done=True`` rows only).
    """
    settled = _settled(goal, done_dates, today)
    done = sum(1 for _day, hit in settled if hit)
    expected = len(settled)
    current, best = _runs(hit for _day, hit in settled)
    rate = _rate(done, expected)
    return {
        "streak": {"current": current, "best": best},
        "done": done,
        "expected": expected,
        "rate": rate,
        "ring": make_ring(rate),
        "has_checkins": bool(done_dates),
        "heatmap": [_goal_cell(goal, day, done_dates, today) for day in heatmap_days(today)],
        "weeks": _week_bars(settled, today),
        "best_weekday": _best_weekday(settled),
        "month": _month_grid(goal, done_dates, today),
    }


def goal_overview(goal, today):
    """Return the detail-page numbers for one goal (one query: the goal's done dates).

    The caller must already have checked that the viewer owns the goal.
    """
    done_dates = set(
        GoalCheckin.objects.filter(goal=goal, done=True).values_list("checkin_date", flat=True)
    )
    return build_goal_overview(goal, done_dates, today)


# ------------------------------------------------------------------ all goals together
def _global_cell(day, sched, done, today):
    """Return the cell for one day across all goals.

    States: ``off`` (nothing scheduled), ``future``, ``missed`` (nothing done, day closed), ``open``
    (nothing done yet today), ``partial`` (``level`` 1-3 by the share done) and ``perfect`` (all done).
    """
    if day > today:
        return _cell(day, "future", "upcoming")
    scheduled = sched.get(day, 0)
    if not scheduled:
        return _cell(day, "off", "no goals scheduled")
    ticked = done.get(day, 0)
    text = f"{ticked} of {scheduled} done"
    if ticked == scheduled:
        return _cell(day, "perfect", text)
    if not ticked:
        return _cell(day, "open" if day == today else "missed", text)
    if 3 * ticked <= scheduled:
        level = 1
    elif 3 * ticked <= 2 * scheduled:
        level = 2
    else:
        level = 3
    return _cell(day, "partial", text, level=level)


def _window_rate(sched, done, today, days):
    """Return ``{"done", "expected", "rate"}`` for the last ``days`` days ending today (today still open rule)."""
    open_today = sched.get(today, 0) - done.get(today, 0)
    ticked = expected = 0
    for back in range(days):
        day = today - timedelta(days=back)
        ticked += done.get(day, 0)
        expected += sched.get(day, 0) - (open_today if day == today else 0)
    return {"done": ticked, "expected": expected, "rate": _rate(ticked, expected)}


def _perfect_days(sched, done, today):
    """Return ``{"current", "best", "total"}`` for perfect days (every scheduled goal done).

    Only days with at least one scheduled goal take part; a day with nothing scheduled neither
    adds to nor breaks a run. Today is skipped until every goal scheduled today is done.
    """
    hits = []
    for day in sorted(sched):
        perfect = done.get(day, 0) == sched[day]
        if day == today and not perfect:
            continue
        hits.append(perfect)
    current, best = _runs(hits)
    return {"current": current, "best": best, "total": sum(hits)}


def build_global(goals, done_by_goal, today):
    """Return the history page numbers for these goals, from plain values (no database).

    ``goals`` are the goals to include (archived ones already left out) and ``done_by_goal`` maps a goal
    id to the set of dates it was ticked.
    """
    sched = defaultdict(int)
    done = defaultdict(int)
    for goal in goals:
        end = min(goal.end_date, today) if goal.end_date else today
        if end < goal.start_date:
            continue
        hits = done_by_goal.get(goal.id, ())
        for day in schedule.scheduled_dates(goal, goal.start_date, end):
            sched[day] += 1
            if day in hits:
                done[day] += 1
    return {
        "has_goals": bool(goals),
        "goal_count": len(goals),
        "heatmap": [_global_cell(day, sched, done, today) for day in heatmap_days(today)],
        "perfect": _perfect_days(sched, done, today),
        "week": _window_rate(sched, done, today, 7),
        "month": _window_rate(sched, done, today, 30),
    }


def _snapshot_numbers(snapshot):
    """Return ``{"open": {"current", "best"}, "read": {"current", "best"}}`` from ``streak_snapshot``.

    VERIFY: Brick 1 only promised "current and longest for both streaks". This is the one place that
    knows the real shape, so adapt the candidate names below if the lookup raises.
    """
    def pick(*names):
        for name in names:
            if isinstance(snapshot, dict) and name in snapshot:
                return snapshot[name]
            if hasattr(snapshot, name):
                return getattr(snapshot, name)
        raise AttributeError(f"streak_snapshot has none of {names}")

    return {
        "open": {
            "current": pick("current_open_streak", "open_current", "current_open"),
            "best": pick("longest_open_streak", "open_longest", "longest_open"),
        },
        "read": {
            "current": pick("current_read_streak", "read_current", "current_read"),
            "best": pick("longest_read_streak", "read_longest", "longest_read"),
        },
    }


def streak_cards(user, today):
    """Return the open and Bible-reading streaks (current and best) for a streak widget."""
    return _snapshot_numbers(streak_snapshot(user, today))


def build_activity(rows, streaks, today):
    """Return the two activity strips ("Opened the app", "Read the Bible") for the last ``ACTIVITY_DAYS`` days.

    ``rows`` are ``(activity_date, opened_app, chapters_read)`` tuples. A cell is ``done``, ``missed`` or,
    for today when nothing has happened yet, ``open``.
    """
    opened = {day for day, was_opened, _chapters in rows if was_opened}
    read = {day for day, _opened, chapters in rows if chapters}
    days = [today - timedelta(days=back) for back in range(ACTIVITY_DAYS - 1, -1, -1)]
    specs = (
        ("open", "Opened the app", "calendar-days", opened, "opened the app", "did not open the app"),
        ("read", "Read the Bible", "book-open", read, "read the Bible", "did not read"),
    )
    result = []
    for key, label, icon, hit_days, yes, no in specs:
        cells = []
        for day in days:
            if day in hit_days:
                cells.append(_cell(day, "done", yes))
            elif day == today:
                cells.append(_cell(day, "open", "not yet today"))
            else:
                cells.append(_cell(day, "missed", no))
        result.append({
            "key": key,
            "label": label,
            "icon": icon,
            "cells": cells,
            "days": sum(1 for day in days if day in hit_days),
            "window": ACTIVITY_DAYS,
            "current": streaks[key]["current"],
            "best": streaks[key]["best"],
        })
    return result


def global_overview(user, today):
    """Return the history page numbers for one person: goals plus the two activity rows.

    Archived goals are left out; completed goals are included. Two goal queries at most (the goals, then
    every done date of those goals in one query; the perfect-day streak needs the whole history).
    """
    goals = list(Goal.objects.filter(user=user).exclude(status=STATUS_ARCHIVED))
    done_by_goal = defaultdict(set)
    if goals:
        rows = GoalCheckin.objects.filter(goal_id__in=[g.id for g in goals], done=True)
        for goal_id, day in rows.values_list("goal_id", "checkin_date"):
            done_by_goal[goal_id].add(day)
    result = build_global(goals, done_by_goal, today)
    first = today - timedelta(days=ACTIVITY_DAYS - 1)
    activity_rows = list(
        UserDailyActivity.objects.filter(user=user, activity_date__range=(first, today))
        .values_list("activity_date", "opened_app", "chapters_read")
    )
    result["activity"] = build_activity(activity_rows, streak_cards(user, today), today)
    return result


# ------------------------------------------------------------------ dashboard widget
def build_today(goals_today, done_ids):
    """Return the "goals today" widget numbers from goals scheduled today and the ids already ticked.

    ``next_up`` lists up to three goals still to do: all-day goals first, then timed ones in time order.
    """
    total = len(goals_today)
    ticked = sum(1 for goal in goals_today if goal.id in done_ids)
    pending = sorted(
        (g for g in goals_today if g.id not in done_ids),
        key=lambda g: (g.start_time is not None, g.start_time or 0, g.title.lower()),
    )
    pct = _rate(ticked, total)
    return {
        "total": total,
        "done": ticked,
        "remaining": total - ticked,
        "all_done": bool(total) and ticked == total,
        "ring": make_ring(pct),
        "next_up": [
            {"id": g.id, "title": g.title, "when": when_summary(g) if g.start_time else ""}
            for g in pending[:3]
        ],
        "more": max(0, len(pending) - 3),
    }


def today_summary(user, today):
    """Return the "goals today" widget numbers for a person (two small queries)."""
    goals = list(Goal.objects.filter(user=user, status=STATUS_ACTIVE))
    scheduled = [g for g in goals if schedule.is_scheduled(g, today)]
    done_ids = set()
    if scheduled:
        done_ids = set(
            GoalCheckin.objects.filter(goal_id__in=[g.id for g in scheduled], checkin_date=today, done=True)
            .values_list("goal_id", flat=True)
        )
    return build_today(scheduled, done_ids)
