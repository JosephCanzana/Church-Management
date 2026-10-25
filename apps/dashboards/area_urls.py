"""dashboards.area_urls: the A4 print report route, written ONCE and mounted in the three leader areas.

No ``app_name`` on purpose (same pattern as ``prayer.area_urls`` and ``tithes.area_urls``): the role url modules in
``accounts/urls/`` (``superadmin.py``, ``admin_area.py``, ``coordinator_area.py``) pull this in with the STRING include
``path("", include("apps.dashboards.area_urls"))``, so the name lands inside THEIR namespace
(``coordinator_area:report_print``...) and templates reach it with ``{% role_url 'report_print' %}``. ``accounts``
imports nothing from ``dashboards``. The path starts with ``report/`` so mounting at the area root cannot shadow the
role's own pages. The view is ``role_required`` for the three leader roles (hiding a link is not access control).
There is no report screen route: the overview (``dashboards:superadmin|admin|coordinator``) shows the period.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("report/print/", views.report_print, name="report_print"),
]
