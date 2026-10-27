"""accounts.urls.superadmin: URLs for the super-admin management screens.

Included from the project urls with:  path("superadmin/", include("apps.accounts.urls.superadmin"))
The `superadmin` namespace lets templates use {% url 'superadmin:extension_list' %}.
The super-admin's own home page (/superadmin/) stays in dashboards.urls.
"""
from django.urls import include, path

from ..views import default_passwords as defaults
from ..views import special_roles as special
from ..views import superadmin as views
from ..views import users as people
from ..views import registration
from ..views.site_settings import registration_auto_approve_toggle
import apps.accounts.site_section_bulk  # noqa: F401

app_name = "superadmin"

urlpatterns = [
    path("site-settings/registration-auto-approve/", registration_auto_approve_toggle, name="registration_auto_approve"),
    path("registration-approvals/", registration.approval_list, name="registration_approvals"),
    path("registration-approvals/<int:pk>/", registration.approval_detail, name="registration_detail"),
    path("registration-approvals/<int:pk>/approve/", registration.approve, name="registration_approve"),
    path("registration-approvals/<int:pk>/reject/", registration.reject, name="registration_reject"),
    path("extensions/", views.extension_list, name="extension_list"),
    path("extensions/new/", views.extension_create, name="extension_create"),
    path("extensions/bulk/", views.extension_bulk, name="extension_bulk"),
    path("extensions/<int:pk>/", views.extension_detail, name="extension_detail"),
    path("extensions/<int:pk>/edit/", views.extension_edit, name="extension_edit"),
    path("extensions/<int:pk>/coordinator/", views.extension_assign_coordinator,
         name="extension_assign_coordinator"),
    path("extensions/<int:pk>/coordinator/remove/", views.extension_unassign_coordinator,
         name="extension_unassign_coordinator"),
    path("extensions/<int:pk>/people/add/", views.extension_person_add, name="extension_person_add"),
    path("extensions/<int:pk>/archive/", views.extension_archive, name="extension_archive"),
    path("extensions/<int:pk>/restore/", views.extension_restore, name="extension_restore"),
    path("extensions/<int:pk>/delete/", views.extension_delete, name="extension_delete"),

    # people
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
    path("users/<int:pk>/delete/", people.user_delete, name="user_delete"),
    # special member roles: give or take one, and the per-extension limits
    path("users/<int:pk>/special-roles/assign/", special.special_role_assign, name="special_role_assign"),
    path("users/<int:pk>/special-roles/remove/", special.special_role_remove, name="special_role_remove"),
    path("special-roles/", special.special_role_limits, name="special_role_limits"),

    # Attendance sheets: written once in attendance/area_urls.py (string include: accounts imports nothing from attendance).
    path("attendance/", include("apps.attendance.area_urls")),
    path("tithes/", include("apps.tithes.area_urls")),
    path("prayer/", include("apps.prayer.area_urls")),
     path("", include("apps.dashboards.area_urls")),
    path("site-settings/", include("apps.accounts.urls.site_jobs")),
      path("site-settings/", include("apps.accounts.urls.site_bulk")),
]
