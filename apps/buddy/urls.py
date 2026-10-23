"""buddy.urls: mounted at /buddy/ with the namespace `buddy` (one line in the project urls)."""
from django.urls import path

from . import views

app_name = "buddy"

urlpatterns = [
    path("", views.home, name="home"),
    path("requests/send/<int:user_id>/", views.request_send, name="request_send"),
    path("requests/<int:request_id>/accept/", views.request_accept, name="request_accept"),
    path("requests/<int:request_id>/decline/", views.request_decline, name="request_decline"),
    path("requests/<int:request_id>/cancel/", views.request_cancel, name="request_cancel"),
]
