"""Read-side helpers for the audit log viewer.

Scoping, filtering, labels and change lists live here so the screen in ``accounts`` stays thin.
``audit`` sits below ``accounts`` in the dependency order, so nothing here imports ``accounts``:
the caller says whether the person may see every row.
"""
import json
from datetime import date, datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from apps.audit.models import AuditLog

# Admins see church activity, not system configuration (site settings, themes, retention).
# Edit this tuple to widen or narrow what an admin can see.
ADMIN_VISIBLE_PREFIXES = (
    "account.",
    "extension.",
    "attendance.",
    "tithes.",
    "event.",
    "resource.",
    "prayer.",
)

# Straight from the model, so the filters can never drift from the stored values.
STATUS_CHOICES = AuditLog.Result.choices
SOURCE_CHOICES = AuditLog.Source.choices


def scope_for(queryset, *, full_access):
    """Return only the rows this person may see: everything for a super-admin, a safe subset for an admin."""
    if full_access:
        return queryset
    allowed = Q()
    for prefix in ADMIN_VISIBLE_PREFIXES:
        allowed |= Q(action__startswith=prefix)
    return queryset.filter(allowed)


def _parse_date(value):
    """Turn 'YYYY-MM-DD' into a date, or None when it is empty or malformed."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _start_of_day(day):
    """Midnight of a calendar day in the project time zone (Asia/Manila)."""
    return timezone.make_aware(datetime.combine(day, time.min))


def apply_filters(queryset, params):
    """Narrow a queryset by the GET filters: q, action, status, source, date_from, date_to."""
    search = params.get("q", "").strip()
    if search:
        match = (
            Q(actor_label__icontains=search)
            | Q(action__icontains=search)
            | Q(entity_type__icontains=search)
            | Q(entity_label__icontains=search)
        )
        # entity_id is a number: match it exactly when the search is one (and fits a bigint).
        if search.isdigit() and len(search) <= 18:
            match |= Q(entity_id=int(search))
        queryset = queryset.filter(match)
    for field in ("action", "status", "source"):
        value = params.get(field, "").strip()
        if value:
            queryset = queryset.filter(**{field: value})
    start = _parse_date(params.get("date_from", ""))
    if start:
        queryset = queryset.filter(created_at__gte=_start_of_day(start))
    end = _parse_date(params.get("date_to", ""))
    if end:
        # The end date is inclusive, so stop at the start of the next day.
        queryset = queryset.filter(created_at__lt=_start_of_day(end + timedelta(days=1)))
    return queryset


def humanize_code(code):
    """'bible_version_toggled' -> 'Bible version toggled'."""
    return (code or "").replace("_", " ").replace(".", " ").strip().capitalize()


def action_parts(action):
    """Split 'account.login_failed' into ('Account', 'Login failed'); no dot means no group."""
    group, _, rest = (action or "").partition(".")
    if not rest:
        group, rest = "", group
    return humanize_code(group), humanize_code(rest)


def action_label(action):
    """One-line label for a filter dropdown: 'Account: Login failed'."""
    group, label = action_parts(action)
    return f"{group}: {label}" if group else label


def distinct_values(queryset, field):
    """Every non-empty value of one column, sorted. Used to fill the filter dropdowns."""
    blank = Q(**{field: ""}) | Q(**{f"{field}__isnull": True})
    return list(
        queryset.exclude(blank).order_by().values_list(field, flat=True).distinct().order_by(field)
    )


def _format_value(value):
    """Show one before/after value as plain text."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def build_changes(before, after):
    """Pair the before and after snapshots field by field for the detail panel.

    Either side may be empty (a create has no before, a delete has no after). log_action() has
    already removed sensitive keys, so everything here is safe to show.
    """
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    keys = list(dict.fromkeys([*before, *after]))
    return [
        {
            "field": humanize_code(key),
            "before": _format_value(before.get(key)),
            "after": _format_value(after.get(key)),
        }
        for key in keys
    ]
