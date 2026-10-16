"""notifications.push_urls: Web Push endpoints, mounted at /notifications/push/ (namespace `push`)."""
from django.urls import path

from . import push_views

app_name = "push"

urlpatterns = [
    path("subscribe/", push_views.subscribe, name="subscribe"),
    path("unsubscribe/", push_views.unsubscribe, name="unsubscribe"),
    path("status/", push_views.status, name="status"),
    path("test/", push_views.test, name="test"),
    path("devices/<int:pk>/remove/", push_views.remove_device, name="remove_device"),
]
