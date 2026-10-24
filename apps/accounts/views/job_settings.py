"""accounts.views.job_settings: POST views and section contexts for Site settings > Jobs and Retention.

Thin views: super-admin only (`role_required`), one service call each, then back to the section.
The Site settings page itself only DISPLAYS these sections; every change comes through here.
"""
from functools import wraps

from django.apps import apps as django_apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require_site_settings_manager
from apps.accounts.services import job_settings as services
from apps.core import job_settings
from apps.core.models import RetentionPolicy
from apps.core.services import ServiceError

# verify: the url name of the Site settings page in the superadmin namespace.
SITE_SETTINGS_URL_NAME = "bible_superadmin:site_settings"

# Friendly names for the retention screen; unknown rows fall back to a title-cased table name.
RETENTION_LABELS = {
    "app_user": "People (archived accounts)",
    "extension": "Extensions",
    "goal": "Goals",
    "prayer_request": "Prayer requests",
    "event": "Events",
    "monthly_theme": "Monthly themes",
    "weekly_powerpoint": "Weekly PowerPoints",
    "general_resource": "General resources",
    "attendance": "Attendance",
    "tithes_offering": "Tithes & offering",
    "notification": "Notifications",
    "audit_log": "Audit log",
}


def super_admin_only(view):
    """Sign-in required, then `require_site_settings_manager` decides (it is the real permission rule).

    The service layer checks again on every change; this only keeps strangers off the view.
    """
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        """Refuse with 403 unless the person may manage site settings."""
        try:
            require_site_settings_manager(request.user)
        except ServiceError as exc:
            raise PermissionDenied from exc
        return view(request, *args, **kwargs)
    return wrapper


def _back(section):
    """Redirect to the Site settings page with `section` open."""
    return redirect(f"{reverse(SITE_SETTINGS_URL_NAME)}?section={section}")


def _archivable_tables():
    """Table names whose models are archived and purged (same test as `purge_archived`)."""
    names = set()
    for model in django_apps.get_models():
        fields = {f.name for f in model._meta.concrete_fields}
        if {"archived_at", "purge_at"} <= fields:
            names.add(model._meta.db_table)
    return names


def jobs_context(request):
    """Context for the Jobs section: the reminder switch and the end-request wait."""
    return {
        "reminders_on": job_settings.goal_reminders_enabled(),
        "end_request_days": job_settings.end_request_days(),
        "end_days_min": job_settings.MIN_END_REQUEST_DAYS,
        "end_days_max": job_settings.MAX_END_REQUEST_DAYS,
    }


def retention_context(request):
    """Context for the Retention section: one row per policy, flagged archivable or not."""
    archivable = _archivable_tables()
    rows = [
        {
            "entity_type": p.entity_type,
            "label": RETENTION_LABELS.get(p.entity_type, p.entity_type.replace("_", " ").title()),
            "days": p.purge_after_days,
            "archivable": p.entity_type in archivable,
        }
        for p in RetentionPolicy.objects.order_by("entity_type")
    ]
    rows.sort(key=lambda r: r["label"])
    return {"retention_rows": rows, "retention_max": services.MAX_RETENTION_DAYS}


def _report(request, changed, done_text, same_text):
    """Add a success message for a change, or an info message when nothing changed."""
    if changed:
        messages.success(request, done_text)
    else:
        messages.info(request, same_text)


@never_cache
@require_POST
@super_admin_only
def goal_reminders(request):
    """Turn goal reminders on or off; the form posts the wanted state ("on" or "off")."""
    wanted = request.POST.get("enabled") == "on"
    try:
        changed = services.set_goal_reminders_enabled(request.user, request, wanted)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        _report(request, changed, "Goal reminders are now %s." % ("on" if wanted else "off"),
                "Goal reminders were already %s." % ("on" if wanted else "off"))
    return _back("jobs")


@never_cache
@require_POST
@super_admin_only
def end_request_days(request):
    """Save how many days a buddy end request waits before it closes by itself."""
    try:
        changed = services.set_end_request_days(request.user, request, request.POST.get("days", ""))
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        _report(request, changed, "Saved.", "No change.")
    return _back("jobs")


@never_cache
@require_POST
@super_admin_only
def retention_update(request):
    """Save one auto-delete period. An empty number with 'never' ticked means never delete."""
    entity = request.POST.get("entity_type", "")
    days = None if request.POST.get("never") == "on" else request.POST.get("days", "")
    try:
        changed = services.set_retention_days(request.user, request, entity, days)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        _report(request, changed, "Retention period saved. It applies to records archived from now on.",
                "No change.")
    return _back("retention")
