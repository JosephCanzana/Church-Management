"""Routes for the Resources library and admin management screens."""
from django.urls import path

from . import views

app_name = "resources"

urlpatterns = [
    path("", views.library, name="list"),
    path("monthly-files/<int:pk>/download/", views.monthly_resource_download, name="monthly_resource_download"),
    path("weekly/<int:pk>/download/", views.weekly_powerpoint_download, name="weekly_download"),
    path("general/<int:pk>/download/", views.general_resource_download, name="general_download"),
    path("manage/themes/", views.theme_list, name="theme_list"),
    path("manage/themes/new/", views.theme_create, name="theme_create"),
    path("manage/themes/<int:pk>/", views.theme_detail, name="theme_detail"),
    path("manage/themes/<int:pk>/edit/", views.theme_edit, name="theme_edit"),
    path("manage/themes/<int:pk>/publish/", views.theme_publish, name="theme_publish"),
    path("manage/themes/<int:pk>/archive/", views.theme_archive, name="theme_archive"),
    path("manage/themes/<int:pk>/restore/", views.theme_restore, name="theme_restore"),
    path("manage/themes/<int:pk>/delete/", views.theme_delete, name="theme_delete"),
    path("manage/themes/<int:theme_pk>/files/new/", views.monthly_resource_create, name="monthly_resource_create"),
    path("manage/monthly-files/<int:pk>/edit/", views.monthly_resource_edit, name="monthly_resource_edit"),
    path("manage/monthly-files/<int:pk>/delete/", views.monthly_resource_delete, name="monthly_resource_delete"),
    path("manage/weekly/", views.weekly_list, name="weekly_list"),
    path("manage/weekly/new/", views.weekly_create, name="weekly_create"),
    path("manage/weekly/<int:pk>/edit/", views.weekly_edit, name="weekly_edit"),
    path("manage/weekly/<int:pk>/archive/", views.weekly_archive, name="weekly_archive"),
    path("manage/weekly/<int:pk>/restore/", views.weekly_restore, name="weekly_restore"),
    path("manage/weekly/<int:pk>/delete/", views.weekly_delete, name="weekly_delete"),
    path("manage/general/", views.general_list, name="general_list"),
    path("manage/general/new/", views.general_create, name="general_create"),
    path("manage/general/<int:pk>/edit/", views.general_edit, name="general_edit"),
    path("manage/general/<int:pk>/archive/", views.general_archive, name="general_archive"),
    path("manage/general/<int:pk>/restore/", views.general_restore, name="general_restore"),
    path("manage/general/<int:pk>/delete/", views.general_delete, name="general_delete"),
]