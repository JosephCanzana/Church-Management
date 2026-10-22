"""faith.stats: read-only numbers for goals (streaks, the Today screen, the All goals list).

Nothing here writes. Every function takes `today` as a parameter (the caller passes
`timezone.localdate()`), so tests never need to patch the clock here.
Brick 4 adds `goal_overview` and `global_overview` to this module.
"""
import datetime as dt
from collections import defaultdict, namedtuple

from .models import Goal, GoalCheckin, UserStreak
from .schedule import is_scheduled

WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
ONE_DAY = dt.timedelta(days=1)

GoalStreaks = namedtuple("GoalStreaks", ["current", "best"])


# ------------------------------------------------------------------ labels
def repeat_summary(goal):
    """Short text for how a goal repeats: "One time", "Every day" or "Mon, Wed, Fri"."""
    if goal.repeat_type == Goal.Repeat.DAILY:
        return "One time" if goal.end_date == goal.start_date else "Every day"
    days = sorted(set(goal.weekdays or []))
    if len(days) == 7:
        return "Every day"
    return ", ".join(WEEKDAY_SHORT[d] for d in days)


def _clock(value):
    """12-hour clock text without a leading zero ("9:05 AM")."""
    return value.strftime("%I:%M %p").lstrip("0")


def time_label(goal):
    """"All day", "9:00 AM" or "9:00 AM to 10:30 AM"."""
    if goal.start_time is None:
        return "All day"
    if goal.end_time is None:
        return _clock(goal.start_time)
    return f"{_clock(goal.start_time)} to {_clock(goal.end_time)}"


# ------------------------------------------------------------------ streaks
def done_dates_for(goal_ids):
    """One query: {goal id: set of dates with a DONE check-in} for many goals at once."""
    found = defaultdict(set)
    rows = GoalCheckin.objects.filter(goal_id__in=list(goal_ids), done=True)
    for goal_id, day in rows.values_list("goal_id", "checkin_date"):
        found[goal_id].add(day)
    return found


def goal_streaks(goal, today, done_dates=None):
    """Current and best streak of one goal, counted in SCHEDULED days only.

    Current counts back from the latest scheduled day on or before `today`. An unchecked
    `today` does not break it, because the day is still open until midnight. Best scans
    every scheduled day from the start. Only `done=True` rows count. `done_dates` can be
    passed in (a set of dates) to save a query when many goals are shown together.
    """
    last = min(goal.end_date, today) if goal.end_date else today
    if last < goal.start_date:
        return GoalStreaks(0, 0)
    if done_dates is None:
        done_dates = set(goal.checkins.filter(done=True).values_list("checkin_date", flat=True))

    scheduled = []
    day = goal.start_date
    while day <= last:
        if is_scheduled(goal, day):
            scheduled.append(day)
        day += ONE_DAY

    best = run = 0
    for day in scheduled:
        run = run + 1 if day in done_dates else 0
        best = max(best, run)

    counted = list(scheduled)
    if counted and counted[-1] == today and today not in done_dates:
        counted.pop()                      # today is still open, so it cannot break the streak yet
    current = 0
    for day in reversed(counted):
        if day not in done_dates:
            break
        current += 1
    return GoalStreaks(current, max(best, current))


def streak_chips(user, today):
    """The two person-level streaks for the Today chips: daily open and Bible reading.

    A current streak shows as 0 once its last date is older than yesterday (the same rule
    as the streak engine). Read from `UserStreak` directly; a person with no row gets zeros.
    """
    row = UserStreak.objects.filter(pk=user.pk).first()
    yesterday = today - ONE_DAY

    def pick(current, longest, last_date):
        alive = last_date is not None and last_date >= yesterday
        return {"current": current if alive else 0, "best": longest}

    if row is None:
        return {"open": {"current": 0, "best": 0}, "read": {"current": 0, "best": 0}}
    return {
        "open": pick(row.current_open_streak, row.longest_open_streak, row.last_open_date),
        "read": pick(row.current_read_streak, row.longest_read_streak, row.last_read_date),
    }


# ------------------------------------------------------------------ Today screen
def _next_hint(goal, today):
    """Why a goal is not on today's list: "Starts Mar 3", "Next: Wed" or "Ended"."""
    if goal.start_date > today:
        return f"Starts {goal.start_date:%b} {goal.start_date.day}"
    if goal.end_date is not None and goal.end_date < today:
        return "Ended"
    for ahead in range(1, 15):
        day = today + dt.timedelta(days=ahead)
        if is_scheduled(goal, day):
            return f"Next: {WEEKDAY_SHORT[day.weekday()]}"
    return "Nothing coming up"


def today_overview(user, today):
    """Everything the Today screen draws, from two small queries plus one for streaks.

    Returns a dict: `all_day` and `timed` (rows of goals scheduled today, timed ones in
    time order), `others` (active goals not scheduled today), `total`, `done`, `percent`.
    Each row holds the goal, `done`, the private `note` (the owner's own page only),
    `current`, `best`, `time_label` and `repeat_label`. Brick 4's dashboard widget reuses it.
    """
    goals = list(Goal.objects.filter(user=user, status=Goal.Status.ACTIVE).order_by("title", "pk"))
    ids = [goal.pk for goal in goals]
    done_dates = done_dates_for(ids)
    today_rows = {c.goal_id: c for c in GoalCheckin.objects.filter(goal_id__in=ids, checkin_date=today)}

    rows, others = [], []
    for goal in goals:
        if is_scheduled(goal, today):
            checkin = today_rows.get(goal.pk)
            streaks = goal_streaks(goal, today, done_dates[goal.pk])
            rows.append({
                "goal": goal,
                "done": bool(checkin and checkin.done),
                "note": checkin.note if checkin else "",
                "current": streaks.current,
                "best": streaks.best,
                "time_label": time_label(goal),
                "repeat_label": repeat_summary(goal),
            })
        else:
            others.append({
                "goal": goal,
                "time_label": time_label(goal),
                "repeat_label": repeat_summary(goal),
                "hint": _next_hint(goal, today),
            })

    all_day = sorted((r for r in rows if r["goal"].start_time is None), key=lambda r: r["goal"].title.lower())
    timed = sorted(
        (r for r in rows if r["goal"].start_time is not None),
        key=lambda r: (r["goal"].start_time, r["goal"].title.lower()),
    )
    total = len(rows)
    done = sum(1 for r in rows if r["done"])
    return {
        "all_day": all_day,
        "timed": timed,
        "others": others,
        "total": total,
        "done": done,
        "percent": round(done * 100 / total) if total else 0,
        "has_goals": bool(goals),
    }


def today_counts(user, today):
    """(done, total) for the goals scheduled today: the numbers a check-in reply carries."""
    goals = [g for g in Goal.objects.filter(user=user, status=Goal.Status.ACTIVE) if is_scheduled(g, today)]
    ids = [g.pk for g in goals]
    done = GoalCheckin.objects.filter(goal_id__in=ids, checkin_date=today, done=True).count()
    return done, len(goals)


# ------------------------------------------------------------------ All goals screen
def goal_list(user, today):
    """Every goal of a person grouped by status, each with its streaks, for the All goals page."""
    goals = list(Goal.objects.filter(user=user).order_by("title", "pk"))
    done_dates = done_dates_for([g.pk for g in goals])
    groups = {"active": [], "completed": [], "archived": []}
    for goal in goals:
        streaks = goal_streaks(goal, today, done_dates[goal.pk])
        groups[goal.status].append({
            "goal": goal,
            "current": streaks.current,
            "best": streaks.best,
            "time_label": time_label(goal),
            "repeat_label": repeat_summary(goal),
        })
    return groups
