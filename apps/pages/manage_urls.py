"""URLs for admin-managed landing-page text blocks."""
from django.urls import path

from . import manage_views

app_name = "pages_manage"

urlpatterns = [
    path("", manage_views.content_list, name="content_list"),
    path("images/", manage_views.image_list, name="image_list"),
    path("images/new/", manage_views.image_create, name="image_create"),
    path("images/<int:pk>/edit/", manage_views.image_edit, name="image_edit"),
    path("images/<int:pk>/active/", manage_views.image_toggle, name="image_toggle"),
    path("images/<int:pk>/move/<str:direction>/", manage_views.image_move, name="image_move"),
    path("images/<int:pk>/delete/", manage_views.image_delete, name="image_delete"),
    path("<str:key>/", manage_views.content_edit, name="content_edit"),
]