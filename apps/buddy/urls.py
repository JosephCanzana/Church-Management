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
    # Ending (Brick 7). No ids: each acts on the signed-in person's own partnership.
    path("end/request/", views.end_request, name="end_request"),
    path("end/cancel/", views.end_cancel, name="end_cancel"),
    path("end/agree/", views.end_agree, name="end_agree"),
    path("end/inactive/", views.end_inactive, name="end_inactive"),
]
