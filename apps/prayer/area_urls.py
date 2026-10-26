"""prayer.area_urls: the inbox routes, written ONCE and mounted in three places.

No `app_name` on purpose (same pattern as attendance): `accounts/urls/superadmin.py`, `admin_area.py` and
`coordinator_area.py` pull this in with the STRING include `include("apps.prayer.area_urls")` at `prayer/`, so
the names land inside THEIR namespace (`coordinator_area:prayer_inbox`, ...). `accounts` imports nothing
from `prayer`. There is no send route here: coordinators and admins never send requests.
"""
from django.urls import path

from . import inbox_views as views

urlpatterns = [
    path("", views.inbox, name="prayer_inbox"),
    path("<int:pk>/", views.inbox_detail, name="prayer_detail"),
    path("<int:pk>/reply/", views.inbox_reply, name="prayer_reply"),
]
