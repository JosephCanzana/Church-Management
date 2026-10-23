"""buddy.urls: mounted at /buddy/ with the namespace `buddy` (one line in the project urls)."""
from django.urls import path

from . import views
from . import message_views

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
    path("messages/", message_views.thread, name="messages"),
    path("messages/send/", message_views.send, name="message_send"),
    path("messages/nudge/", message_views.nudge, name="message_nudge"),
    path("messages/prayed/", message_views.prayed, name="message_prayed"),
    path("messages/poll/", message_views.poll, name="message_poll"),
    path("messages/earlier/", message_views.earlier, name="message_earlier"),
    path("messages/read/", message_views.read, name="message_read"),
    path("goals/", views.partner_goals, name="goals"),
]
