"""attendance.person_overview: the read-only attendance picture on a person's detail page.

Answers three questions about ONE person, inside a month range chosen on the page, from UPLOADED sheets
only (drafts and archived sheets never count, a reopened sheet leaves until it is uploaded again):

  * which services of their extension did they come to,
  * which did they miss,
  * which did they spend at another extension (a visit, not a miss).

A "service" is an uploaded sheet of an extension the person belonged to on the sheet's date
(`user_extension_history`), so someone who moved here last month is not shown as having missed
last year. A sheet the person was ticked on (or promoted from as a first timer) always counts as
attended, even when the history dates do not cover it.

`start` and `end` are dates (the people view builds them from the month range); None means no limit.

Read-only: no writes, no audit rows, no notifications. Only dates, labels and counts leave this
module: no birth date, phone number or address. The caller decides who may look at the person
(`User.objects.for_user(viewer)` in the people views); this module does no permission check, like
`faith.partner`.

`accounts` never imports `attendance`, so the people view loads `person_attendance_overview` by
its dotted path (see `accounts/views/users.py: _attendance_overview`).
"""
from datetime import date

from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import UserExtensionHistory

from .models import Attendance
from .services import member_attendance_history

PRESENT = "present"        # at a service of their own extension
ELSEWHERE = "elsewhere"    # not at their extension that day, but ticked at another one
MISSED = "missed"

STATE_LABELS = {
    PRESENT: "Attended",
    ELSEWHERE: "Visited another extension",
    MISSED: "Missed",
}


def classify_services(home_sheets, attended_ids, elsewhere_dates):
    """Pure logic, no database. Returns the overview dict.

    `home_sheets`: dicts with `pk`, `attendance_date`, `label` of the person's own extension, sorted
    oldest first. `attended_ids`: sheet ids the person was at. `elsewhere_dates`: dates they were ticked
    at ANOTHER extension. `months` lists only months that had a service, newest month first, with its
    services oldest first; `missed_list` is newest first.
    """
    rows = []
    for sheet in home_sheets:
        if sheet["pk"] in attended_ids:
            state = PRESENT
        elif sheet["attendance_date"] in elsewhere_dates:
            state = ELSEWHERE
        else:
            state = MISSED
        rows.append({
            "sheet_id": sheet["pk"], "date": sheet["attendance_date"], "label": sheet["label"],
            "state": state, "state_label": STATE_LABELS[state],
        })

    longest = run = 0
    for row in rows:
        run = 0 if row["state"] == MISSED else run + 1
        longest = max(longest, run)

    by_month = {}
    for row in rows:
        key = (row["date"].year, row["date"].month)
        by_month.setdefault(key, []).append(row)
    months = []
    for (year, month) in sorted(by_month, reverse=True):
        cells = by_month[(year, month)]
        months.append({
            "date": date(year, month, 1),
            "cells": cells,
            "total": len(cells),
            "attended": sum(1 for c in cells if c["state"] != MISSED),
        })

    home = sum(1 for r in rows if r["state"] == PRESENT)
    elsewhere = sum(1 for r in rows if r["state"] == ELSEWHERE)
    missed = sum(1 for r in rows if r["state"] == MISSED)
    total = len(rows)
    attended = home + elsewhere
    return {
        "total": total,
        "home": home,
        "elsewhere": elsewhere,
        "attended": attended,
        "missed": missed,
        "rate": round(100 * attended / total) if total else None,
        "longest_run": longest,
        "months": months,
        "missed_list": [r for r in reversed(rows) if r["state"] == MISSED],
    }


def _periods(person, today):
    """(extension_id, from, to) for every stretch the person belonged to an extension."""
    rows = UserExtensionHistory.objects.filter(
        user=person, extension__isnull=False,
    ).values_list("extension_id", "from_date", "to_date")
    periods = [(ext, start, end or today) for ext, start, end in rows]
    if not periods and person.extension_id:
        # No history row (old data): count from the day the record was created.
        created = timezone.localtime(person.created_at).date() if person.created_at else today
        periods = [(person.extension_id, created, today)]
    return periods


def person_attendance_overview(person, *, start=None, end=None, today=None):
    """The attendance picture of `person` between `start` and `end`, or None for someone with no extension."""
    if person.extension_id is None:
        return None
    today = today or timezone.localdate()          # Manila calendar date (TIME_ZONE)
    last_day = min(end, today) if end else today

    attended_ids = {e["sheet_id"] for e in member_attendance_history(person)}
    periods = _periods(person, today)
    extension_ids = {ext for ext, _, _ in periods}

    in_period = Q()
    for ext, period_start, period_end in periods:
        in_period |= Q(extension_id=ext, attendance_date__gte=period_start, attendance_date__lte=period_end)
    sheets = Attendance.objects.filter(status=Attendance.Status.UPLOADED, attendance_date__lte=last_day)
    if start:
        sheets = sheets.filter(attendance_date__gte=start)
    sheets = list(
        sheets.filter(in_period | Q(pk__in=attended_ids))
        .order_by("attendance_date", "pk")
        .values("pk", "attendance_date", "label", "extension_id")
    )

    home_sheets = [s for s in sheets if s["extension_id"] in extension_ids]
    elsewhere_dates = {
        s["attendance_date"] for s in sheets
        if s["pk"] in attended_ids and s["extension_id"] not in extension_ids
    }
    return classify_services(home_sheets, attended_ids, elsewhere_dates)
