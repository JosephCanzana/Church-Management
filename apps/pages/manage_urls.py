"""URLs for admin-managed landing-page text blocks."""
from django.urls import path

from . import manage_views

app_name = "pages_manage"

urlpatterns = [
    path("", manage_views.content_list, name="content_list"),
    path("<str:key>/", manage_views.content_edit, name="content_edit"),
]