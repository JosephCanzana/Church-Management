"""tithes.urls: the member-root routes, mounted at /tithes/ (namespace `tithes`).

Used by tithes special members. Every route is shared with the role areas (see `area_urls.py`).
"""
from .area_urls import urlpatterns as area_urlpatterns

app_name = "tithes"

urlpatterns = [*area_urlpatterns]
