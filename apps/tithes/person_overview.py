"""tithes.person_overview: one person's giving history in a chosen month range, for the people detail page.

ADMIN AND SUPER-ADMIN ONLY (`permissions.can_view_person_giving`). A coordinator, a special member or
anyone else gets None. The check lives HERE as well as in the view, so hiding the card is never the
only guard.

Built on `services.member_tithe_history`, so the rules are the same as "My tithes": FINALIZED reports
only (a draft, a reopened report and a report on an archived sheet are left out), the person counts as
giver or co-giver, and a shared envelope shows its FULL amount, never split. A person who shares an
envelope therefore sees that amount in their own total; totals here are "envelopes they gave or shared
in", not a split of the money.

Numbers only: totals and a list, no chart. `start` and `end` are dates (the people view builds them from
the month range); None means no limit. Tithes only: the offering is recorded per report, not per person,
so it cannot be shown here.

Read-only: nothing is written, nothing is audited, and no amount ever goes to `audit_log` or a
notification. `accounts` never imports `tithes`; the people view loads `person_tithes_overview` by its
dotted path (see `accounts/views/users.py: _tithes_overview`).
"""
from decimal import Decimal

from apps.accounts.models import PersonType

from .permissions import can_view_person_giving
from .services import member_tithe_history

#: Shown before every amount. Keep in step with `CURRENCY` in the dashboards services.
CURRENCY = "\u20b1"


def summarize_tithes(history, start=None, end=None):
    """Pure logic, no database. `history` is `member_tithe_history` output (newest first)."""
    rows = [
        e for e in history
        if (start is None or e["date"] >= start) and (end is None or e["date"] <= end)
    ]
    total = sum((e["amount"] for e in rows), Decimal("0"))
    average = (total / len(rows)).quantize(Decimal("0.01")) if rows else None
    return {
        "currency": CURRENCY,
        "window_total": total,
        "window_count": len(rows),
        "average": average,
        "rows": rows,                                        # newest first, every envelope in the range
        "all_count": len(history),
        "outside": len(history) - len(rows),                 # envelopes before or after the range
        "last_date": history[0]["date"] if history else None,    # their newest ever, whatever the range
    }


def person_tithes_overview(viewer, person, *, start=None, end=None):
    """The giving picture of `person` as `viewer` may see it, or None (no card)."""
    if not can_view_person_giving(viewer):
        return None
    if person.person_type == PersonType.GUEST:      # a guest has no envelopes: promote them first
        return None
    return summarize_tithes(member_tithe_history(person), start, end)
