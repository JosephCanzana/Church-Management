"""Public registration and the role scoped approval inbox."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.services import new_submission_token, ServiceError
from ..decorators import role_required
from ..forms import RegistrationForm
from ..models import RegistrationRequest, Role
from ..services.registration import (
    approve_registration, registration_requests_for, reject_registration,
    send_approval_email, submit_registration,
)


def _ip(request):
    # Do not trust forwarded headers unless a trusted proxy has been configured.
    return request.META.get("REMOTE_ADDR", "")[:64]


@never_cache
@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("accounts:login")
    form = RegistrationForm(request.POST or None, initial={"submission_token": new_submission_token()})
    throttled = False
    if request.method == "POST":
        try:
            from ..services.registration import throttle_registration_ip
            throttle_registration_ip(_ip(request))
        except ServiceError as exc:
            form.add_error(None, str(exc))
            throttled = True
    if request.method == "POST" and not throttled and form.is_valid():
        # Honeypot: report the ordinary success page without retaining or notifying on bot submissions.
        if form.cleaned_data.get("website"):
            return render(request, "accounts/register_done.html")
        try:
            result = submit_registration(form.cleaned_data, token=form.cleaned_data["submission_token"])
            if result.duplicate:
                form.add_error(None, "You already have submitted your request.")
            else:
                return render(request, "accounts/register_done.html")
        except ServiceError as exc:
            form.add_error(None, str(exc))
    if request.method == "POST":
        form.data = form.data.copy()
        form.data["submission_token"] = new_submission_token()
    if not form.is_bound:
        form.initial["submission_token"] = new_submission_token()
    return render(request, "accounts/register.html", {"form": form})


@login_required
@role_required(Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR)
@never_cache
def approval_list(request):
    requests = registration_requests_for(request.user).filter(status=RegistrationRequest.Status.PENDING).select_related("extension").order_by("created_at")
    return render(request, "accounts/registrations/list.html", {"requests": requests})


def _approvals_url(request):
    return f"{request.resolver_match.namespace}:registration_approvals"


@login_required
@role_required(Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR)
@never_cache
@require_GET
def approval_detail(request, pk):
    req = get_object_or_404(registration_requests_for(request.user), pk=pk)
    if req.status != RegistrationRequest.Status.PENDING:
        messages.info(request, "That request was already processed.")   # <-- replaces the raise
        return redirect(_approvals_url(request))                        # <-- replaces the raise
    return render(request, "accounts/registrations/detail.html", {
        "registration": req, "token": new_submission_token(),
    })


@login_required
@role_required(Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR)
@require_POST
@never_cache
def approve(request, pk):
    try:
        outcome = approve_registration(
            request.user, request, pk, token=request.POST.get("submission_token"),
            email_override=request.POST.get("email"),
        )
        if outcome is None:
            messages.info(request, "That request was already processed.")
            return redirect(_approvals_url(request))
        req, user, password = outcome
        try:
            send_approval_email(user, password)
        except Exception:
            return render(request, "accounts/registrations/email_failed.html", {
                "person": user, "password": password, "sign_in_url": request.build_absolute_uri("/accounts/login/"),
            })
        messages.success(request, "The account was approved and an email was sent.")
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect(_approvals_url(request))


@login_required
@role_required(Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR)
@require_POST
@never_cache
def reject(request, pk):
    try:
        changed = reject_registration(request.user, request, pk, token=request.POST.get("submission_token"))
        if changed:
            messages.success(request, "The request was rejected.")
        else:
            messages.info(request, "That request was already processed.")
    except ServiceError as exc:
        messages.error(request, str(exc))
    return redirect(_approvals_url(request))
