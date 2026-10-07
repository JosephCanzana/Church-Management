from django.shortcuts import render

from accounts.decorators import role_required
from accounts.models import Role


def _placeholder(request, label):
    return render(request, "dashboards/placeholder.html", {"label": label})


@role_required(Role.SUPER_ADMIN)
def superadmin_home(request):
    return _placeholder(request, "Super-admin")


@role_required(Role.ADMIN)
def admin_home(request):
    return _placeholder(request, "Admin")


@role_required(Role.COORDINATOR)
def coordinator_home(request):
    return _placeholder(request, "Coordinator")


@role_required(Role.MEMBER)
def member_home(request):
    return _placeholder(request, "Member")