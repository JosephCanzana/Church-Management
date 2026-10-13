"""bible.urls_superadmin: Site settings (super-admin) and the Bible switches.

Included from the project urls with:
    path("superadmin/site-settings/", include("apps.bible.urls_superadmin"))
Namespace ``bible_superadmin``: {% url 'bible_superadmin:site_settings' %}. The name and the path
are unchanged so the sidebar entry keeps working; the page itself now comes from `accounts`
(the section menu) and the Bible is one of its sections.
"""
from django.urls import path

from apps.accounts.views.site_settings import site_settings

from . import site_section  # noqa: F401  (importing registers the Bible section)
from . import views_superadmin as views

app_name = "bible_superadmin"

urlpatterns = [
    path("", site_settings, name="site_settings"),
    path("versions/<str:code>/", views.version_toggle, name="version_toggle"),
    path("online/", views.online_toggle, name="online_toggle"),
]
