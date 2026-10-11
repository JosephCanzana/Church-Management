"""Routes for notifications, mounted at /notifications/ (namespace ``notifications``)."""
from django.urls import path

from apps.notifications import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_list, name="list"),
    path("read-all/", views.notification_read_all, name="read_all"),
    path("<int:pk>/open/", views.notification_open, name="open"),
]
