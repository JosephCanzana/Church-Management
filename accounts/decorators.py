"""accounts.decorators: role-based home URLs and the role guard."""
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse

from .models import Role

# URL names, resolved at request time. When a real dashboard exists,
# change the name here and nothing else.
ROLE_HOME_NAMES = {
    Role.SUPER_ADMIN: "accounts:superadmin_home",
    Role.ADMIN: "accounts:admin_home",
    Role.COORDINATOR: "accounts:coordinator_home",
    Role.MEMBER: "accounts:member_home",
}


def home_url_for(user):
    """Return the landing URL for this user's role."""
    name = ROLE_HOME_NAMES.get(user.role)
    return reverse(name) if name else settings.LOGIN_REDIRECT_URL


def role_required(*roles):
    """Allow only these roles; anyone else goes to their own home page."""
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.user.role not in roles:
                return redirect(home_url_for(request.user))
            return view(request, *args, **kwargs)
        return wrapper
    return decorator