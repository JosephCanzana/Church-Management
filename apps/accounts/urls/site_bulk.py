"""URLs for Site settings > Bulk add. Included from the super-admin url module like `site_jobs`:

    path("site-settings/", include("apps.accounts.urls.site_bulk")),
"""
from django.urls import path

from apps.accounts.views import bulk_import as views

urlpatterns = [
    path("bulk-add/preview/", views.bulk_preview, name="bulk_preview"),
    path("bulk-add/commit/", views.bulk_commit, name="bulk_commit"),
    path("bulk-add/problems.csv", views.bulk_problems, name="bulk_problems"),
    path("bulk-add/template.csv", views.bulk_template, name="bulk_template"),
]
