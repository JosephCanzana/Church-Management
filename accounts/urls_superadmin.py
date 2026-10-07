"""accounts.urls_superadmin: URLs for the super-admin management screens.

Included from the project urls with:  path("superadmin/", include("accounts.urls_superadmin"))
The `superadmin` namespace lets templates use {% url 'superadmin:extension_list' %}.
The super-admin's own home page (/superadmin/) stays in dashboards.urls.
"""
from django.urls import path

from . import views_superadmin as views

app_name = "superadmin"

urlpatterns = [
    path("extensions/", views.extension_list, name="extension_list"),
    path("extensions/new/", views.extension_create, name="extension_create"),
    path("extensions/<int:pk>/", views.extension_detail, name="extension_detail"),
    path("extensions/<int:pk>/edit/", views.extension_edit, name="extension_edit"),
    path("extensions/<int:pk>/coordinator/", views.extension_assign_coordinator,
         name="extension_assign_coordinator"),
    path("extensions/<int:pk>/coordinator/remove/", views.extension_unassign_coordinator,
         name="extension_unassign_coordinator"),
    path("extensions/<int:pk>/archive/", views.extension_archive, name="extension_archive"),
    path("extensions/<int:pk>/restore/", views.extension_restore, name="extension_restore"),
    path("extensions/<int:pk>/delete/", views.extension_delete, name="extension_delete"),
]
