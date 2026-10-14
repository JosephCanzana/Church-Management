"""accounts.urls.coordinator_area: the Coordinator role's people screens, mounted at /coordinator/.

Included from the project urls with:  path("coordinator/", include("apps.accounts.urls.coordinator_area"))
A coordinator manages the members of their own extension only: create, edit details, reset a
password, archive and restore. No suspend, deactivate or delete routes exist for this role, and
the services refuse them anyway. The views are shared with the other roles (see admin_area).
There is no default-passwords screen either: the Default passwords button on the list POSTs to
`default_password_show`, which displays the global member password read-only.
"""
from django.urls import include, path

from ..views import special_roles as special
from ..views import users as people

app_name = "coordinator_area"

urlpatterns = [
    # A coordinator keeps no default password of their own: the button shows the global member one.
    path("default-password/", people.default_password_show, name="default_password_show"),
    path("users/", people.user_list, name="user_list"),
    path("users/new/", people.user_create, name="user_create"),
    path("users/bulk/", people.user_bulk, name="user_bulk"),
    path("users/<int:pk>/", people.user_detail, name="user_detail"),
    path("users/<int:pk>/edit/", people.user_edit, name="user_edit"),
    path("users/<int:pk>/archive/", people.user_archive, name="user_archive"),
    path("users/<int:pk>/restore/", people.user_restore, name="user_restore"),
    path("users/<int:pk>/reset-password/", people.user_reset_password, name="user_reset_password"),
    path("users/<int:pk>/special-roles/assign/", special.special_role_assign, name="special_role_assign"),
    path("users/<int:pk>/special-roles/remove/", special.special_role_remove, name="special_role_remove"),
    # Attendance sheets: the routes are written once in attendance/area_urls.py. A string include, so accounts
    # imports nothing from attendance (dependency rule); the names land in this namespace.
    path("attendance/", include("apps.attendance.area_urls")),
]
