"""theming.urls: mount with path("settings/", include("apps.theming.urls")) in church_management/urls.py."""
from django.urls import path

from . import views

app_name = "theming"

urlpatterns = [
    path("", views.settings_view, name="settings"),
]
