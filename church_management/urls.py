"""
URL configuration for church_management project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("", include("apps.pages.urls")),
    path("", include("apps.dashboards.urls")),
    path("django-admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("superadmin/", include("apps.accounts.urls.superadmin")),
    path("superadmin/site-settings/", include("apps.bible.urls_superadmin")),
    path("bible/", include("apps.bible.urls")),
    path("audit/", include("apps.accounts.urls.audit_log")),
    path("notifications/", include("apps.notifications.urls")),
    path("resources/", include("apps.resources.urls")),
    path("admin/", include("apps.accounts.urls.admin_area")),
    path("coordinator/", include("apps.accounts.urls.coordinator_area")),
    # Member root: attendance special members (the role areas mount the same sheet routes themselves).
    path("attendance/", include("apps.attendance.urls")),
    path("settings/", include("apps.theming.urls")),
]

# Dev only: Django serves uploaded photos. In production Caddy serves /media/.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)