"""tithes.area_urls: the tithes routes, written ONCE and mounted in four places.

No `app_name` here on purpose (same pattern as `attendance/area_urls.py`): the role url modules
(`accounts/urls/superadmin.py`, `admin_area.py`, `coordinator_area.py`) pull this in with the STRING include
`include("apps.tithes.area_urls")`, so the names land inside THEIR namespace (`coordinator_area:report_list`, ...).
`apps/tithes/urls.py` mounts the same list again at the member root as the `tithes` namespace. Because it is a
string include, `accounts` never imports `tithes` (AGENTS.md dependency rule). A new route goes HERE, once.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.report_list, name="report_list"),
    path("requests/", views.pending_requests, name="pending_requests"),
    # Households (coordinator of the extension, admin, super-admin).
    path("households/", views.household_list, name="household_list"),
    path("households/create/", views.household_create, name="household_create"),
    path("households/<int:pk>/members/add/", views.household_member_add, name="household_member_add"),
    path("households/members/<int:pk>/end/", views.household_member_end, name="household_member_end"),
    # Start a report for an attendance sheet (POST).
    path("sheets/<int:sheet_pk>/start/", views.report_create, name="report_create"),
    # One report: the workspace while it is an editable draft, the review otherwise.
    path("<int:pk>/", views.report_detail, name="report_detail"),
    path("<int:pk>/review/", views.report_review, name="report_review"),
    # Standalone A4 page for the browser's print dialog (GET, changes and logs nothing).
    path("<int:pk>/print/", views.report_print, name="report_print"),
    # JSON helpers for the giver picker (GET, change nothing).
    path("<int:pk>/givers/", views.giver_search, name="giver_search"),
    path("<int:pk>/givers/prefill/", views.giver_prefill, name="giver_prefill"),
    # POST-only actions, each runs ONE service and redirects back to the report.
    path("<int:pk>/offering/", views.report_offering, name="report_offering"),
    path("<int:pk>/breakdown/", views.report_breakdown, name="report_breakdown"),
    path("<int:pk>/entries/add/", views.entry_add, name="entry_add"),
    path("<int:pk>/entries/update/", views.entry_update, name="entry_update"),
    path("<int:pk>/entries/remove/", views.entry_remove, name="entry_remove"),
    path("<int:pk>/bypass/request/", views.bypass_request, name="bypass_request"),
    path("<int:pk>/bypass/withdraw/", views.bypass_withdraw, name="bypass_withdraw"),
    path("<int:pk>/bypass/decide/", views.bypass_decide, name="bypass_decide"),
    path("<int:pk>/finalize/", views.report_finalize, name="report_finalize"),
    path("<int:pk>/reopen/", views.report_reopen, name="report_reopen"),
    path("households/<int:pk>/disband/", views.household_disband, name="household_disband"),
]
