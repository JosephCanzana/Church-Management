"""accounts.views.default_passwords: the signed-in super-admin's own default passwords.

One row per role beneath them. An empty field leaves that default alone; a
typed one replaces it; Remove deletes it. Each default is also kept encrypted,
so the page can show the current value (decrypted here, never logged).
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.core.services import ServiceError

from ..decorators import role_required
from ..forms import DefaultPasswordsForm
from ..models import DefaultPassword, Role
from ..permissions import default_password_roles
from ..services.default_passwords import save_default_passwords

super_admin_only = role_required(Role.SUPER_ADMIN)


@super_admin_only
@never_cache
@require_http_methods(["GET", "POST"])
def default_passwords(request):
    """Show (GET) and save (POST) the signed-in person's default passwords."""
    roles = default_password_roles(request.user)
    form = DefaultPasswordsForm(request.POST or None, roles=roles)

    if request.method == "POST" and form.is_valid():
        changes = form.changes
        if not changes:
            messages.info(request, "Nothing to save. Type a password or tick Remove first.")
            return redirect("superadmin:default_passwords")
        try:
            changed = save_default_passwords(request.user, request, changes)
        except (ServiceError, PermissionDenied) as exc:
            form.add_error(None, str(exc))
        else:
            if changed:
                names = ", ".join(Role(r).label for r in changed)
                messages.success(request, f"Default passwords updated: {names}.")
            else:
                messages.info(request, "Nothing changed.")
            return redirect("superadmin:default_passwords")

    stored = {row.applies_to_role: row for row in DefaultPassword.objects.filter(owner=request.user)}
    rows = []
    for role in roles:
        record = stored.get(role)
        rows.append({
            "label": Role(role).label,
            "record": record,
            "current": record.plain if record else None,   # decrypted; None for old rows
            "password": form[f"password_{role}"],
            "remove": form[f"clear_{role}"],
        })
    return render(request, "accounts/superadmin/default_passwords.html", {"form": form, "rows": rows})