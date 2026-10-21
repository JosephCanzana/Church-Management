"""accounts.urls.admin_area: the Admin role's people screens, mounted at /admin/.

Included from the project urls with:  path("admin/", include("apps.accounts.urls.admin_area"))
The namespace is `admin_area` because `admin` already belongs to Django's built-in admin
(mounted at /django-admin/). The views are the same ones the super-admin uses; they read the
current namespace, so every link and redirect stays inside this area. There is no delete route:
only the super-admin force deletes.
"""
from django.urls import include, path

from ..views import default_passwords as defaults
from ..views import special_roles as special
from ..views import users as people
from ..views import registration

app_name = "admin_area"

urlpatterns = [
    path("registration-approvals/", registration.approval_list, name="registration_approvals"),
    path("registration-approvals/<int:pk>/", registration.approval_detail, name="registration_detail"),
    path("registration-approvals/<int:pk>/approve/", registration.approve, name="registration_approve"),
    path("registration-approvals/<int:pk>/reject/", registration.reject, name="registration_reject"),
    path("default-passwords/", defaults.default_passwords, name="default_passwords"),
    path("users/", people.user_list, name="user_list"),
    path("users/new/", people.user_create, name="user_create"),
    path("users/bulk/", people.user_bulk, name="user_bulk"),
    path("users/<int:pk>/", people.user_detail, name="user_detail"),
    path("users/<int:pk>/edit/", people.user_edit, name="user_edit"),
    path("guests/", people.guest_list, name="guest_list"),
    path("users/<int:pk>/promote/", people.user_promote, name="user_promote"),
    path("users/<int:pk>/grant-account/", people.user_grant_account, name="user_grant_account"),
    path("users/<int:pk>/archive/", people.user_archive, name="user_archive"),
    path("users/<int:pk>/restore/", people.user_restore, name="user_restore"),
    path("users/<int:pk>/suspend/", people.user_suspend, name="user_suspend"),
    path("users/<int:pk>/unsuspend/", people.user_unsuspend, name="user_unsuspend"),
    path("users/<int:pk>/reset-password/", people.user_reset_password, name="user_reset_password"),
    path("users/<int:pk>/special-roles/assign/", special.special_role_assign, name="special_role_assign"),
    path("users/<int:pk>/special-roles/remove/", special.special_role_remove, name="special_role_remove"),
    path("users/<int:pk>/delete/", people.user_delete, name="user_delete"),
    # How many people each extension may have in each special role.
    path("special-roles/", special.special_role_limits, name="special_role_limits"),
    # Attendance sheets: written once in attendance/area_urls.py (string include: accounts imports nothing from attendance).
    path("attendance/", include("apps.attendance.area_urls")),
    path("tithes/", include("apps.tithes.area_urls")),
]
