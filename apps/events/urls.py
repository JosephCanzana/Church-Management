"""events.urls: mounted at /events/ (church_management/urls.py), namespace `events`.

`index` redirects to the view used last on this device. The sidebar points at `index`.
"""
from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.index_view, name="index"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("list/", views.list_view, name="list"),
    path("new/", views.create_view, name="create"),
    path("<int:pk>/", views.detail_view, name="detail"),
    path("<int:pk>/edit/", views.edit_view, name="edit"),
    path("<int:pk>/cancel/", views.cancel_view, name="cancel"),
]
