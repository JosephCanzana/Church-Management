"""tithes.urls: the member-root routes, mounted at /tithes/ (namespace `tithes`).

Used by tithes special members. Every route is shared with the role areas (see `area_urls.py`).

`mine/` ("My tithes") lives HERE ONLY, never in `area_urls.py`: it is the signed-in person's own giving
history, open to every role, so it must not be mounted under a role area next to the report screens
(same rule as `attendance:mine`). It is listed before the shared routes; it cannot clash with
`<int:pk>/` because `mine` is not a number.
"""
from django.urls import path

from . import views
from .area_urls import urlpatterns as area_urlpatterns

app_name = "tithes"

urlpatterns = [
    path("mine/", views.my_tithes, name="mine"),
    *area_urlpatterns,
]
