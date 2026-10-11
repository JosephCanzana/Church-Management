"""Routes for the audit log viewer, mounted at /audit/.

The namespace is ``audit`` and the list is named ``log`` so it matches the existing
NavItem("Audit log", "audit:log", ...). The views live in ``accounts`` (see views/audit_log.py).
"""
from django.urls import path

from apps.accounts.views import audit_log as views

app_name = "audit"

urlpatterns = [
    path("", views.log_list, name="log"),
    path("<int:pk>/", views.log_detail, name="detail"),
]
