"""attendance.person_overview: the read-only attendance picture on a person's detail page.

Answers three questions about ONE person, from UPLOADED sheets only (drafts and archived sheets
never count, a reopened sheet leaves until it is uploaded again):

  * which services of their extension did they come to,
  * which did they miss,
  * which did they spend at another extension (a visit, not a miss).

A "service" is an uploaded sheet of an extension the person belonged to on the sheet's date
(`user_extension_history`), so someone who moved here last month is not shown as having missed
last year. A sheet the person was ticked on (or promoted from as a first timer) always counts as
attended, even when the history dates do not cover it.

Read-only: no writes, no audit rows, no notifications. Only dates, labels and counts leave this
module: no birth date, phone number or address. The caller decides who may look at the person
(`User.objects.for_user(viewer)` in the people views); this module does no permission check, like
`faith.partner`.

`accounts` never imports `attendance`, so the people view loads `person_attendance_overview` by
its dotted path (see `accounts/views/users.py: _attendance_overview`).
"""
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import UserExtensionHistory

from .models import Attendance
from .services import member_attendance_history

#: How many of the newest services the strip and the totals cover.
OVERVIEW_WINDOW = 16

PRESENT = "present"        # at a service of their own extension
ELSEWHERE = "elsewhere"    # not at their extension that day, but ticked at another one
MISSED = "missed"

STATE_LABELS = {
    PRESENT: "Attended",
    ELSEWHERE: "Visited another extension",
    MISSED: "Missed",
}


def classify_services(home_sheets, attended_ids, elsewhere_dates, window=OVERVIEW_WINDOW):
    """Pure logic, no database. Returns the overview dict.

    `home_sheets`: dicts with `pk`, `attendance_date`, `label` of the person's own extension,
    sorted oldest first. `attended_ids`: sheet ids the person was at. `elsewhere_dates`: dates they
    were ticked at ANOTHER extension. The streak is measured over every service, the strip and the
    totals over the newest `window`.
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

    streak = 0
    for row in reversed(rows):
        if row["state"] == MISSED:
            break
        streak += 1

    cells = rows[-window:] if window else rows
    home = sum(1 for c in cells if c["state"] == PRESENT)
    elsewhere = sum(1 for c in cells if c["state"] == ELSEWHERE)
    missed = sum(1 for c in cells if c["state"] == MISSED)
    total = len(cells)
    attended = home + elsewhere
    return {
        "window": window,
        "total": total,
        "home": home,
        "elsewhere": elsewhere,
        "attended": attended,
        "missed": missed,
        "rate": round(100 * attended / total) if total else None,
        "streak": streak,
        "cells": cells,                                              # oldest first
        "missed_list": [c for c in reversed(cells) if c["state"] == MISSED],   # newest first
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


def person_attendance_overview(person, *, window=OVERVIEW_WINDOW, today=None):
    """The attendance picture of `person`, or None for someone with no extension (admins)."""
    if person.extension_id is None:
        return None
    today = today or timezone.localdate()          # Manila calendar date (TIME_ZONE)

    attended_ids = {e["sheet_id"] for e in member_attendance_history(person)}
    periods = _periods(person, today)
    extension_ids = {ext for ext, _, _ in periods}

    in_period = Q()
    for ext, start, end in periods:
        in_period |= Q(extension_id=ext, attendance_date__gte=start, attendance_date__lte=end)
    sheets = list(
        Attendance.objects.filter(status=Attendance.Status.UPLOADED, attendance_date__lte=today)
        .filter(in_period | Q(pk__in=attended_ids))
        .order_by("attendance_date", "pk")
        .values("pk", "attendance_date", "label", "extension_id")
    )

    home_sheets = [s for s in sheets if s["extension_id"] in extension_ids]
    elsewhere_dates = {
        s["attendance_date"] for s in sheets
        if s["pk"] in attended_ids and s["extension_id"] not in extension_ids
    }
    return classify_services(home_sheets, attended_ids, elsewhere_dates, window)
