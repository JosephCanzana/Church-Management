"""attendance.urls: the member-root routes, mounted at /attendance/ (namespace `attendance`).

Used by attendance special members. The sheet routes are shared with the role areas (see `area_urls.py`).
Brick 14 adds `mine/` here: it is deliberately NOT in `area_urls`, so it never appears under a role area.
"""
from django.urls import path

from . import views
from .area_urls import urlpatterns as sheet_urlpatterns

app_name = "attendance"

urlpatterns = [
    *sheet_urlpatterns,
    # Brick 14: the person's own history, identical for all four roles (not mounted under any role area).
    path("mine/", views.my_attendance, name="mine"),
    path("<int:pk>/first-timers/edit/", views.sheet_first_timer_edit, name="sheet_first_timer_edit"),
]
