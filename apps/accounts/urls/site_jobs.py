"""accounts.urls.site_jobs: POST routes for the Jobs and Retention sections of Site settings.

Include this from the super-admin url module (see docs/BRICK10_AGENTS_PATCH.md). Names resolve in
the `superadmin` namespace, for example `superadmin:site_goal_reminders`.
"""
from django.urls import path

from apps.accounts.views import job_settings as views

urlpatterns = [
    path("jobs/reminders/", views.goal_reminders, name="site_goal_reminders"),
    path("jobs/end-request-days/", views.end_request_days, name="site_end_request_days"),
    path("retention/", views.retention_update, name="site_retention_update"),
]
