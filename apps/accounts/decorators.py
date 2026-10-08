"""accounts.decorators: role-based home URLs and the role guard."""
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse

from .models import Role

# URL names, resolved at request time. These are the real dashboard routes
# (see dashboards/urls.py). When a real dashboard replaces a placeholder,
# change the name here and nothing else. accounts/navigation.py reads this
# dict for the "Home" link, so the sidebar and the redirects always agree.
ROLE_HOME_NAMES = {
    Role.SUPER_ADMIN: "dashboards:superadmin",
    Role.ADMIN: "dashboards:admin",
    Role.COORDINATOR: "dashboards:coordinator",
    Role.MEMBER: "dashboards:member",
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
