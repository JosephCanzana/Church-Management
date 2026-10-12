"""accounts.urls.coordinator_area: the Coordinator role's people screens, mounted at /coordinator/.

Included from the project urls with:  path("coordinator/", include("apps.accounts.urls.coordinator_area"))
A coordinator manages the members of their own extension only: create, edit details, reset a
password, archive and restore. No suspend, deactivate or delete routes exist for this role, and
the services refuse them anyway. The views are shared with the other roles (see admin_area).
"""
from django.urls import path

from ..views import default_passwords as defaults
from ..views import users as people

app_name = "coordinator_area"

urlpatterns = [
    path("default-passwords/", defaults.default_passwords, name="default_passwords"),
    path("users/", people.user_list, name="user_list"),
    path("users/new/", people.user_create, name="user_create"),
    path("users/bulk/", people.user_bulk, name="user_bulk"),
    path("users/<int:pk>/", people.user_detail, name="user_detail"),
    path("users/<int:pk>/edit/", people.user_edit, name="user_edit"),
    path("users/<int:pk>/archive/", people.user_archive, name="user_archive"),
    path("users/<int:pk>/restore/", people.user_restore, name="user_restore"),
    path("users/<int:pk>/reset-password/", people.user_reset_password, name="user_reset_password"),
]
