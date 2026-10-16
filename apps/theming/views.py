"""theming.views: the signed-in person's own settings page.

Open to every role. The page edits request.user's row and nothing else: no user id is
read from the URL, the query string or the form (build plan, rule 9.1). Views stay thin:
parse, call the service, show a message, redirect.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.core.services import ServiceError

from .forms import UserSettingsForm, reminder_initial
from .services import get_user_settings, update_user_settings


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def settings_view(request):
    """Show the settings form (GET) or save it (POST), then redirect so a refresh never resubmits."""
    row = get_user_settings(request.user)
    if request.method == "POST":
        form = UserSettingsForm(request.POST)
        if form.is_valid():
            try:
                _, changed = update_user_settings(request.user, **form.cleaned_data)
            except ServiceError as exc:
                form.add_error(None, str(exc))
            else:
                if changed:
                    messages.success(request, "Your settings were saved.")
                else:
                    messages.info(request, "No changes to save.")
                return redirect("theming:settings")
    else:
        form = UserSettingsForm(initial={
            "color_mode": row.color_mode,
            "mobile_nav_style": row.mobile_nav_style,
            "event_reminder": reminder_initial(row),
        })
    return render(request, "theming/settings.html", {
        "form": form,
        "color_options": form.options("color_mode"),
        "nav_options": form.options("mobile_nav_style"),
        "reminder_options": form.options("event_reminder"),
    })
