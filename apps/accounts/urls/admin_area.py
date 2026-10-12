"""accounts.urls.admin_area: the Admin role's people screens, mounted at /admin/.

Included from the project urls with:  path("admin/", include("apps.accounts.urls.admin_area"))
The namespace is `admin_area` because `admin` already belongs to Django's built-in admin
(mounted at /django-admin/). The views are the same ones the super-admin uses; they read the
current namespace, so every link and redirect stays inside this area. There is no delete route:
only the super-admin force deletes.
"""
from django.urls import path

from ..views import default_passwords as defaults
from ..views import users as people

app_name = "admin_area"

urlpatterns = [
    path("default-passwords/", defaults.default_passwords, name="default_passwords"),
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
]
