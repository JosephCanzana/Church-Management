"""accounts.views.profile: the signed-in person's own profile page.

Open to every role (login_required, not role_required): people only ever act
on THEIR OWN account, and each service re-reads the row of request.user.
Views stay thin: validate with a form, call a service, turn ServiceError into
a message. Success redirects back to the profile (a toast reports the result);
a failure re-renders the page with the bound form so the typed values stay.
Every page is `never_cache`, because it shows account details.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.services import ServiceError

from ..forms import ChangePasswordForm, ProfileDetailsForm, ProfileEmailForm
from ..services import profile as svc


def _render(request, *, details_form=None, email_form=None, password_form=None):
    """Draw the whole profile page; a form that failed is passed back in bound."""
    user = request.user
    return render(request, "accounts/profile.html", {
        "details_form": details_form or ProfileDetailsForm(user=user),
        "email_form": email_form or ProfileEmailForm(),
        "password_form": password_form or ChangePasswordForm(user=user),
        "resend_wait": svc.resend_wait_seconds(user),
        "verify_minutes": svc.link_minutes(),
        "has_photo": bool(user.profile_image),
    })


@login_required
@never_cache
@require_http_methods(["GET"])
def profile_view(request):
    """Show the profile page."""
    return _render(request)


# ------------------------------------------------------------------ details
@login_required
@never_cache
@require_POST
def details_view(request):
    """Save birth date and/or a new photo."""
    form = ProfileDetailsForm(request.POST, request.FILES, user=request.user)
    if form.is_valid():
        try:
            changed = svc.update_details(
                request, request.user,
                birth_date=form.cleaned_data["birth_date"], photo=form.cleaned_data["photo"],
            )
        except ServiceError as exc:
            form.add_error(None, str(exc))
        else:
            if changed:
                messages.success(request, "Your details were saved.")
            else:
                messages.info(request, "No changes to save.")
            return redirect("accounts:profile")
    return _render(request, details_form=form)


@login_required
@never_cache
@require_POST
def photo_remove_view(request):
    """Delete the profile photo."""
    if svc.remove_photo(request, request.user):
        messages.success(request, "Your photo was removed.")
    else:
        messages.info(request, "You have no photo to remove.")
    return redirect("accounts:profile")


# -------------------------------------------------------------------- email
@login_required
@never_cache
@require_POST
def email_view(request):
    """Add or change the email: it becomes pending until the link is clicked."""
    form = ProfileEmailForm(request.POST)
    if form.is_valid():
        try:
            address = svc.request_email_verification(request, request.user, form.cleaned_data["email"])
        except ServiceError as exc:
            form.add_error("email", str(exc))
        else:
            messages.success(request, f"We sent a verification link to {address}.")
            return redirect("accounts:profile")
    return _render(request, email_form=form)


@login_required
@never_cache
@require_POST
def email_resend_view(request):
    """Send the link again to the address that is waiting."""
    try:
        address = svc.request_email_verification(request, request.user)
    except ServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"We sent a new verification link to {address}.")
    return redirect("accounts:profile")


@login_required
@never_cache
@require_POST
def email_cancel_view(request):
    """Cancel the email that is waiting for verification."""
    if svc.cancel_pending_email(request, request.user):
        messages.success(request, "The pending email was cancelled.")
    else:
        messages.info(request, "There was no pending email.")
    return redirect("accounts:profile")


@login_required
@never_cache
@require_POST
def email_remove_view(request):
    """Remove the email (verified and pending)."""
    if svc.remove_email(request, request.user):
        messages.success(request, "Your email was removed. Log in with your account ID.")
    else:
        messages.info(request, "You have no email to remove.")
    return redirect("accounts:profile")


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def verify_email_view(request, token):
    """The page the emailed link opens.

    GET only SHOWS what will be verified. The change happens on POST, because mail
    scanners and link previewers open links automatically and a GET that verified
    would confirm addresses nobody clicked. People who are not signed in are sent
    to login first (login_required) and come back here afterwards.
    """
    if request.method == "POST":
        try:
            result = svc.verify_email(request, request.user, token)
        except ServiceError as exc:
            messages.error(request, str(exc))
        else:
            if result.changed:
                messages.success(request, f"{result.email} is now your verified email.")
            else:
                messages.info(request, "That email was already verified.")
        return redirect("accounts:profile")

    found = svc.find_valid_token(request.user, token)
    return render(request, "accounts/verify_email.html", {
        "email": found.email if found else None,
        "token": token,
    })


# ----------------------------------------------------------------- password
@login_required
@never_cache
@require_POST
def password_view(request):
    """Change the password (current one required)."""
    form = ChangePasswordForm(request.POST, user=request.user)
    if form.is_valid():
        try:
            svc.change_password(
                request, request.user,
                form.cleaned_data["current_password"], form.cleaned_data["new_password"],
            )
        except ServiceError as exc:
            form.add_error("current_password", str(exc))
        else:
            messages.success(request, "Your password was changed. Other devices were signed out.")
            return redirect("accounts:profile")
    return _render(request, password_form=form)