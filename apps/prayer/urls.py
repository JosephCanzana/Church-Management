"""prayer.urls: the member screens, mounted at /prayer/ (namespace `prayer`).

Project urls: path("prayer/", include("apps.prayer.urls")). The inbox routes are NOT here: they are written
once in `area_urls.py` and mounted in the three role areas.
"""
from django.urls import path

from . import views

app_name = "prayer"

urlpatterns = [
    path("", views.prayer_list, name="list"),
    path("send/", views.prayer_send, name="send"),
]
