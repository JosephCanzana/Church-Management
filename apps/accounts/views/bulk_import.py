"""Screens for Site settings > Bulk add (super-admin only).

Flow: the section form POSTs to `bulk_preview` (nothing is saved), the preview page POSTs to `bulk_commit`.
The preview and result pages carry personal data and may show passwords, so every view is `never_cache` and
renders in the POST response (no redirect, nothing kept in the session).
"""
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import role_required
from apps.accounts.models import Role
from apps.accounts.services import bulk_import as service
from apps.core.services import ServiceError, new_submission_token  # VERIFY import path

SITE_SETTINGS_URL_NAME = "bible_superadmin:site_settings"  # must match the real Site settings route name
TITLES = {"extensions": "Add extensions", "people": "Add people"}


def _kind(request) -> str:
    kind = request.POST.get("kind") or request.GET.get("kind", "")
    if kind not in service.KINDS:
        raise Http404
    return kind


def _render(request, report, *, csv_text="", token="", mode="preview"):
    secrets = None
    if report.secrets:
        secrets = {
            "title": "Temporary passwords",
            "message": "Shown once. Copy them now: they are not stored and cannot be shown again.",
            "headers": ["Account ID", "Name", "Password"],
            "rows": report.secrets,
        }
    return render(request, "accounts/superadmin/bulk_import_result.html", {
        "report": report, "kind": report.kind, "title": TITLES[report.kind], "csv_text": csv_text, "token": token,
        "mode": mode, "secrets": secrets, "settings_url": reverse(SITE_SETTINGS_URL_NAME),
    })


@role_required(Role.SUPER_ADMIN)
@never_cache
@require_POST
def bulk_preview(request):
    """Check the file and show what would happen to every row. Saves nothing."""
    kind = _kind(request)
    upload = request.FILES.get("file")
    try:
        text, report = service.validate(
            request.user, kind, upload.read() if upload else None, request.POST.get("pasted", "")
        )
    except ServiceError as exc:
        messages.error(request, str(exc))
        return redirect(reverse(SITE_SETTINGS_URL_NAME))
    token = new_submission_token() if report.summary["importable"] else ""
    return _render(request, report, csv_text=text, token=token)


@role_required(Role.SUPER_ADMIN)
@never_cache
@require_POST
def bulk_commit(request):
    """Add the valid rows. Re-checks the file first."""
    kind = _kind(request)
    try:
        report = service.commit(request.user, request, kind, request.POST.get("csv_text", ""), request.POST.get("token", ""))
    except ServiceError as exc:
        messages.error(request, str(exc))
        return redirect(reverse(SITE_SETTINGS_URL_NAME))
    return _render(request, report, mode="result")


@role_required(Role.SUPER_ADMIN)
@never_cache
@require_POST
def bulk_problems(request):
    """Download the rows that need attention as a CSV to fix and upload again."""
    kind = _kind(request)
    try:
        _, report = service.validate(request.user, kind, pasted=request.POST.get("csv_text", ""))
    except ServiceError as exc:
        messages.error(request, str(exc))
        return redirect(reverse(SITE_SETTINGS_URL_NAME))
    response = HttpResponse(service.problems_csv(report), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{kind}-problems.csv"'
    response["Cache-Control"] = "no-store"
    return response


@role_required(Role.SUPER_ADMIN)
@require_GET
def bulk_template(request):
    """A header-only CSV to start from."""
    kind = _kind(request)
    response = HttpResponse("\ufeff" + service.template_csv(kind), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{kind}-template.csv"'
    return response
