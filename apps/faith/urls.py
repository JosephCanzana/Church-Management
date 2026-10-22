"""faith.urls: mounted at /goals/ (church_management/urls.py), namespace `faith`.

The Goals tab of the journey hub points at `faith:goals` (the Today screen).
"""
from django.urls import path

from . import views

app_name = "faith"

urlpatterns = [
    path("", views.today_view, name="goals"),
    path("new/", views.new_view, name="goal_new"),
    path("all/", views.all_view, name="goal_all"),
    path("<int:pk>/edit/", views.edit_view, name="goal_edit"),
    path("<int:pk>/check/", views.check_view, name="goal_check"),
    path("<int:pk>/note/", views.note_view, name="goal_note"),
    path("<int:pk>/change-schedule/", views.change_schedule_view, name="goal_change_schedule"),
    path("<int:pk>/archive/", views.archive_view, name="goal_archive"),
    path("<int:pk>/restore/", views.restore_view, name="goal_restore"),
]
