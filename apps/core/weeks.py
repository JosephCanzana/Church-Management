"""Helpers for the personal history pages ("My attendance", "My tithes").

A week starts on Sunday and ends on Friday. Saturday has no service, but a row dated on a Saturday is never
dropped: it belongs to the Sunday-start week it falls in, and the month boxes add a Saturday column only when
the person has a Saturday row in that month. Pure functions, no database access.

`month_view` is the one entry point the views call: it applies the month filter and returns everything the
templates need (the weekly list, the month boxes and the filter choices).
"""
import calendar
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.utils import timezone

WEEK_LENGTH_DAYS = 5  # Sunday + 5 = Friday, the last day shown in a week's label
DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def as_date(value):
    """Return a plain `date` for a date or a datetime (an aware datetime is read in Manila time)."""
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.date()
    return value


def week_start(day):
    """The Sunday that starts the week containing `day` (Monday=0 ... Sunday=6 in Python)."""
    return day - timedelta(days=(day.weekday() + 1) % 7)


def parse_month(value):
    """Turn "2026-10" into date(2026, 10, 1). Anything else (blank, junk, month 13) gives None."""
    match = _MONTH_RE.match(value or "")
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), 1)
    except ValueError:
        return None


def in_month(entries, first):
    """The entries whose date falls in the month that starts on `first`."""
    kept = []
    for entry in entries:
        day = as_date(entry["date"])
        if day.year == first.year and day.month == first.month:
            kept.append(entry)
    return kept


def month_choices(entries, today, selected=None):
    """Filter options, newest first: every month that has an entry, plus this month and the selected one."""
    months = {today.replace(day=1)}
    if selected:
        months.add(selected)
    for entry in entries:
        months.add(as_date(entry["date"]).replace(day=1))
    return [{"value": m.strftime("%Y-%m"), "label": m} for m in sorted(months, reverse=True)]


def group_by_week(entries, amount_key=None):
    """Group history entries (dicts with a "date") into weeks, newest week first.

    Entries keep their incoming order inside a week. Each week is
    {"start", "end" (the Friday), "entries", "count", "amount"}; "amount" adds `entry[amount_key]`
    when a key is given (tithes) and stays 0 otherwise (attendance).
    """
    weeks = {}
    for entry in entries:
        start = week_start(as_date(entry["date"]))
        week = weeks.setdefault(start, {
            "start": start,
            "end": start + timedelta(days=WEEK_LENGTH_DAYS),
            "entries": [],
            "count": 0,
            "amount": Decimal("0"),
        })
        week["entries"].append(entry)
        week["count"] += 1
        if amount_key:
            week["amount"] += entry[amount_key]
    return sorted(weeks.values(), key=lambda w: w["start"], reverse=True)


def month_boxes(entries, first, amount_key=None):
    """Calendar boxes for the month that starts on `first`: one row per week, one box per day.

    Columns are Sun to Fri (plus Sat only when this month has a Saturday row). A day outside the month is
    None so the columns stay lined up. Each box is {"date", "count", "amount", "first_visit"}; an empty day
    has count 0. Also returns the month's totals ("count", "amount") and the column headers.
    """
    days = {}
    for entry in in_month(entries, first):
        day = as_date(entry["date"])
        box = days.setdefault(day, {"count": 0, "amount": Decimal("0"), "first_visit": False})
        box["count"] += 1
        if amount_key:
            box["amount"] += entry[amount_key]
        if entry.get("kind") == "first_visit":
            box["first_visit"] = True

    columns = 7 if any(day.weekday() == 5 for day in days) else 6
    last = date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])
    rows = []
    start = week_start(first)
    while start <= last:
        row = []
        for offset in range(columns):
            day = start + timedelta(days=offset)
            if (day.year, day.month) != (first.year, first.month):
                row.append(None)
                continue
            data = days.get(day)
            row.append({
                "date": day,
                "count": data["count"] if data else 0,
                "amount": data["amount"] if data else Decimal("0"),
                "first_visit": data["first_visit"] if data else False,
            })
        if any(row):  # skip a week that has no day in this month (month starting on a Saturday)
            rows.append(row)
        start += timedelta(days=7)

    return {
        "month": first,
        "headers": DAY_NAMES[:columns],
        "columns": columns,
        "rows": rows,
        "count": sum(box["count"] for box in days.values()),
        "amount": sum((box["amount"] for box in days.values()), Decimal("0")),
    }


def month_view(entries, raw_month, today, amount_key=None):
    """Everything a history page needs, from the full history and the raw `?month=` value.

    With no valid month the list shows ALL history and the boxes show the current month. With a month, both
    the list and the boxes show that month. Keys: has_any, month, selected, months, boxes, weeks, total,
    querystring (for the pagination links).
    """
    month = parse_month(raw_month)
    shown = in_month(entries, month) if month else list(entries)
    return {
        "has_any": bool(entries),
        "month": month,
        "selected": month.strftime("%Y-%m") if month else "",
        "months": month_choices(entries, today, month),
        "boxes": month_boxes(entries, month or today.replace(day=1), amount_key),
        "weeks": group_by_week(shown, amount_key),
        "total": len(shown),
        "querystring": f"month={month:%Y-%m}" if month else "",
    }