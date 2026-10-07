from django.urls import path

from . import views

app_name = "dashboards"

urlpatterns = [
    path("superadmin/", views.superadmin_home, name="superadmin"),
    path("admin/", views.admin_home, name="admin"),
    path("coordinator/", views.coordinator_home, name="coordinator"),
    path("home/", views.member_home, name="member"),
]