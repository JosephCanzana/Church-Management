"""accounts.urls.superadmin: URLs for the super-admin management screens.

Included from the project urls with:  path("superadmin/", include("apps.accounts.urls.superadmin"))
The `superadmin` namespace lets templates use {% url 'superadmin:extension_list' %}.
The super-admin's own home page (/superadmin/) stays in dashboards.urls.
"""
from django.urls import path

from ..views import superadmin as views
from ..views import users as people

app_name = "superadmin"

urlpatterns = [
    path("extensions/", views.extension_list, name="extension_list"),
    path("extensions/new/", views.extension_create, name="extension_create"),
    path("extensions/bulk/", views.extension_bulk, name="extension_bulk"),
    path("extensions/<int:pk>/", views.extension_detail, name="extension_detail"),
    path("extensions/<int:pk>/edit/", views.extension_edit, name="extension_edit"),
    path("extensions/<int:pk>/coordinator/", views.extension_assign_coordinator,
         name="extension_assign_coordinator"),
    path("extensions/<int:pk>/coordinator/remove/", views.extension_unassign_coordinator,
         name="extension_unassign_coordinator"),
    path("extensions/<int:pk>/archive/", views.extension_archive, name="extension_archive"),
    path("extensions/<int:pk>/restore/", views.extension_restore, name="extension_restore"),
    path("extensions/<int:pk>/delete/", views.extension_delete, name="extension_delete"),

    # people
    path("users/", people.user_list, name="user_list"),
    path("users/new/", people.user_create, name="user_create"),
    path("users/bulk/", people.user_bulk, name="user_bulk"),
    path("users/<int:pk>/", people.user_detail, name="user_detail"),
    path("users/<int:pk>/edit/", people.user_edit, name="user_edit"),
    path("users/<int:pk>/archive/", people.user_archive, name="user_archive"),
    path("users/<int:pk>/restore/", people.user_restore, name="user_restore"),
    path("users/<int:pk>/suspend/", people.user_suspend, name="user_suspend"),
    path("users/<int:pk>/unsuspend/", people.user_unsuspend, name="user_unsuspend"),
    path("users/<int:pk>/deactivate/", people.user_deactivate, name="user_deactivate"),
    path("users/<int:pk>/reset-password/", people.user_reset_password, name="user_reset_password"),
    path("users/<int:pk>/delete/", people.user_delete, name="user_delete"),
]
