"""bible.urls_superadmin: the super-admin's Bible switches.

Included from the project urls with:
    path("superadmin/site-settings/", include("apps.bible.urls_superadmin"))
Namespace ``bible_superadmin``: {% url 'bible_superadmin:site_settings' %}.
"""
from django.urls import path

from . import views_superadmin as views

app_name = "bible_superadmin"

urlpatterns = [
    path("", views.site_settings, name="site_settings"),
    path("versions/<str:code>/", views.version_toggle, name="version_toggle"),
    path("online/", views.online_toggle, name="online_toggle"),
]
