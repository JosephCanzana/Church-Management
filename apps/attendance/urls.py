"""attendance.urls: the member-root routes, mounted at /attendance/ (namespace `attendance`).

Used by attendance special members. The sheet routes are shared with the role areas (see `area_urls.py`).
Brick 14 adds `mine/` here: it is deliberately NOT in `area_urls`, so it never appears under a role area.
"""
from .area_urls import urlpatterns as sheet_urlpatterns

app_name = "attendance"

urlpatterns = [*sheet_urlpatterns]
