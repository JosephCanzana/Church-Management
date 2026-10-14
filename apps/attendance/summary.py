"""attendance.summary: the numbers of one sheet, worked out in ONE place.

`build_summary` turns a sheet's stored rows into the breakdown by gender and age network, and the
three totals. The live preview on a draft, the printed page and the value frozen at upload (brick 6)
all come from it, so they cannot disagree.

Rules:
- Only the STORED rows are counted (the snapshot taken at tick time). Nobody's current birth date
  or gender is read, so a birthday after the sheet date changes nothing.
- `total_present` is `members_present` plus `first_timers_count`.
- A member row still missing a gender or an age network counts in `members_present` but in no cell
  of the breakdown; `incomplete_rows` lists those rows and the upload refuses while any exist, so a
  FROZEN breakdown always adds up to its totals.
- Nothing here checks permissions or writes anything: callers (views, the upload service) do.
"""
from dataclasses import dataclass

from django.db.models import Count, Q

from apps.accounts.models import Gender

from .age import AgeNetwork
from .models import AttendanceFirstTimer, AttendanceMember

#: How the two genders are named on the summary table.
GENDER_ROW_LABELS = {Gender.MALE.value: "Men", Gender.FEMALE.value: "Women"}
#: The two groups of the summary, in display order: (key in the summary, title shown).
SUMMARY_GROUPS = (("first_timers", "First timers"), ("members", "Members"))


@dataclass(frozen=True)
class SheetSummary:
    """The numbers of a sheet.

    `summary` is the JSON breakdown stored on the sheet at upload (see `build_summary`). `incomplete`
    is how many member rows still miss a category: always 0 in a frozen summary.
    """

    summary: dict
    members_present: int
    first_timers_count: int
    total_present: int
    incomplete: int = 0


def _empty_breakdown():
    """{gender: {age network: 0}} for every gender and every age network."""
    return {gender: {network: 0 for network in AgeNetwork.values} for gender in Gender.values}


def _count(rows):
    """Count a queryset of rows by (gender, age network): returns (breakdown, rows counted, rows incomplete).

    One grouped query, however many rows there are. A row whose gender or age network is blank
    (or not one of the known choices) is counted in the total but in no cell.
    """
    breakdown = _empty_breakdown()
    total = incomplete = 0
    for gender, network, number in rows.order_by().values_list("gender", "age_network").annotate(n=Count("pk")):
        total += number
        if gender in breakdown and network in breakdown[gender]:
            breakdown[gender][network] += number
        else:
            incomplete += number
    return breakdown, total, incomplete


def build_summary(sheet):
    """The numbers of `sheet`, counted from its stored rows. Returns a `SheetSummary`.

    `.summary` is the structure stored in `Attendance.summary` (two queries in all):
        {"first_timers": {"male": {"adult": 0, "young_adult": 0, "youth": 0, "children": 0},
                          "female": {...}},
         "members": {"male": {...}, "female": {...}}}
    """
    members, members_present, incomplete = _count(AttendanceMember.objects.filter(attendance=sheet))
    first_timers, first_timers_count, _ = _count(AttendanceFirstTimer.objects.filter(attendance=sheet))
    return SheetSummary(
        summary={"first_timers": first_timers, "members": members},
        members_present=members_present,
        first_timers_count=first_timers_count,
        total_present=members_present + first_timers_count,
        incomplete=incomplete,
    )


def summary_for(sheet):
    """The numbers to SHOW for `sheet`: the frozen ones once uploaded, otherwise counted live.

    Screens and the print page call this, so an uploaded sheet always reads exactly what was frozen.
    A sheet without frozen numbers (a draft, or a reopened sheet) is counted from its rows.
    """
    frozen = (sheet.members_present, sheet.first_timers_count, sheet.total_present)
    if sheet.is_locked and sheet.summary is not None and None not in frozen:
        return SheetSummary(
            summary=sheet.summary, members_present=frozen[0], first_timers_count=frozen[1],
            total_present=frozen[2],
        )
    return build_summary(sheet)


def incomplete_rows(sheet):
    """Member rows still missing a gender or an age network, by name.

    The draft screen flags these so the taker can pick one inline (`set_member_category`), and the
    upload refuses while the list is not empty. First timers are never listed: the service requires
    both for them.
    """
    return list(
        AttendanceMember.objects.filter(attendance=sheet)
        .exclude(Q(gender__in=Gender.values) & Q(age_network__in=AgeNetwork.values))
        .order_by("member_name", "pk")
    )


def summary_table(summary):
    """Display rows for the screen and the print page, from a `summary` breakdown.

    Returns {"columns": [...], "groups": [...], "total": n}:
      columns  one per age network, in order: {"code", "label"}
      groups   first timers, then members; each {"key", "title", "rows", "column_totals", "total"}
               where a row is {"gender", "label" ("Men" or "Women"), "cells", "total"} and `cells`
               (and `column_totals`) are plain numbers in the same order as `columns`
      total    everyone counted in the table (first timers plus members)
    Missing keys count as 0, so a summary of any age renders.
    """
    columns = [{"code": code, "label": AgeNetwork(code).label} for code in AgeNetwork.values]
    groups = []
    grand_total = 0
    for key, title in SUMMARY_GROUPS:
        block = (summary or {}).get(key) or {}
        rows = []
        column_totals = [0] * len(columns)
        for gender in Gender.values:
            counts = block.get(gender) or {}
            cells = [int(counts.get(code) or 0) for code in AgeNetwork.values]
            column_totals = [a + b for a, b in zip(column_totals, cells)]
            rows.append({
                "gender": gender, "label": GENDER_ROW_LABELS[gender], "cells": cells, "total": sum(cells),
            })
        group_total = sum(column_totals)
        grand_total += group_total
        groups.append({
            "key": key, "title": title, "rows": rows, "column_totals": column_totals, "total": group_total,
        })
    return {"columns": columns, "groups": groups, "total": grand_total}