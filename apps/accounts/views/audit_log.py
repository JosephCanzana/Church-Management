"""Audit log viewer: a read-only, filterable list plus a detail partial for the side panel.

The screen lives in ``accounts`` (not ``audit``) because ``audit`` sits below ``accounts`` and
cannot import ``role_required``; the Bible super-admin screen follows the same pattern.
"""
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from apps.accounts.decorators import role_required
from apps.accounts.models import Role
from apps.audit import queries
from apps.audit.models import AuditLog

PAGE_SIZE = 25
FILTER_LABELS = {
    "action": "Action",
    "status": "Result",
    "source": "Source",
    "date_from": "From",
    "date_to": "Until",
}


def _base_queryset(user):
    """Rows this person may see: super-admins everything, admins only the allowed actions."""
    return queries.scope_for(AuditLog.objects.all(), full_access=user.role == Role.SUPER_ADMIN)


def _chips(params, choice_labels):
    """One removable chip per active filter (the search box shows its own text)."""
    chips = []
    for key, name in FILTER_LABELS.items():
        value = params.get(key, "").strip()
        if not value:
            continue
        remaining = params.copy()
        remaining.pop(key, None)
        remaining.pop("page", None)
        shown = choice_labels.get(key, {}).get(value, value)
        chips.append({"label": f"{name}: {shown}", "remove": remaining.urlencode()})
    return chips


@role_required(Role.SUPER_ADMIN, Role.ADMIN)
@require_GET
def log_list(request):
    """List audit rows, newest first, with search, filters and pagination."""
    scoped = _base_queryset(request.user)
    filtered = queries.apply_filters(scoped, request.GET).order_by("-created_at", "-pk")
    page = Paginator(filtered, PAGE_SIZE).get_page(request.GET.get("page"))

    rows = list(page.object_list)
    for row in rows:
        row.action_group, row.action_label = queries.action_parts(row.action)
        row.actor_role_label = queries.humanize_code(row.actor_role)

    action_choices = [(a, queries.action_label(a)) for a in queries.distinct_values(scoped, "action")]
    choice_labels = {
        "action": dict(action_choices),
        "status": dict(queries.STATUS_CHOICES),
        "source": dict(queries.SOURCE_CHOICES),
    }

    chips = _chips(request.GET, choice_labels)
    without_page = request.GET.copy()
    without_page.pop("page", None)
    filters = {key: request.GET.get(key, "").strip() for key in ("q", *FILTER_LABELS)}

    return render(request, "accounts/audit/log_list.html", {
        "rows": rows,
        "page": page,
        "querystring": without_page.urlencode(),
        "chips": chips,
        "filters": filters,
        "has_filters": bool(chips or filters["q"]),
        # Keeps the modal filters when the search box is submitted on its own.
        "hidden_filters": [(k, v) for k, v in filters.items() if k != "q" and v],
        "action_choices": action_choices,
        "source_choices": queries.SOURCE_CHOICES,
        "status_choices": queries.STATUS_CHOICES,
        "limited": request.user.role != Role.SUPER_ADMIN,
    })


@role_required(Role.SUPER_ADMIN, Role.ADMIN)
@require_GET
def log_detail(request, pk):
    """Return the HTML fragment for one row, loaded into the side panel.

    A row outside the person's scope is a 404, the same as a row that does not exist.
    """
    row = get_object_or_404(_base_queryset(request.user), pk=pk)
    row.action_group, row.action_label = queries.action_parts(row.action)
    return render(request, "accounts/audit/_log_detail.html", {
        "row": row,
        "changes": queries.build_changes(row.before, row.after),
        "source_label": row.get_source_display(),
        "actor_role_label": queries.humanize_code(row.actor_role),
    })
