"""attendance.area_urls: the sheet routes, written ONCE and mounted in four places.

No `app_name` here on purpose: the role url modules (`accounts/urls/superadmin.py`, `admin_area.py`,
`coordinator_area.py`) pull this in with the string `include("apps.attendance.area_urls")`, so the names
land inside THEIR namespace (`coordinator_area:sheet_list`, ...). `apps/attendance/urls.py` mounts the same
list again at the member root as the `attendance` namespace. Because it is a string include, `accounts` does
not import `attendance` (AGENTS.md dependency rule).
"""
from django.urls import path

from . import views

urlpatterns = [
    path("sheets/", views.sheet_list, name="sheet_list"),
    path("sheets/new/", views.sheet_create, name="sheet_create"),
    path("sheets/<int:pk>/", views.sheet_detail, name="sheet_detail"),
    path("sheets/<int:pk>/review/", views.sheet_review, name="sheet_review"),
    # POST-only actions on a draft (each redirects back to the workspace).
    path("sheets/<int:pk>/mark/", views.sheet_mark, name="sheet_mark"),
    path("sheets/<int:pk>/unmark/", views.sheet_unmark, name="sheet_unmark"),
    path("sheets/<int:pk>/category/", views.sheet_member_category, name="sheet_member_category"),
    path("sheets/<int:pk>/first-timers/add/", views.sheet_first_timer_add, name="sheet_first_timer_add"),
    path("sheets/<int:pk>/first-timers/remove/", views.sheet_first_timer_remove, name="sheet_first_timer_remove"),
    path("sheets/<int:pk>/upload/", views.sheet_upload, name="sheet_upload"),
    path("sheets/<int:pk>/reopen/", views.sheet_reopen, name="sheet_reopen"),
]
