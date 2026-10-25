from django.urls import include, path
from . import views

app_name = "pages"

urlpatterns = [
    path("", views.root_view, name="root"),
    path("welcome/", views.landing_page, name="landing"),  
    path("give/", views.give_view, name="give"),
    path("about/", views.about_view, name="about"),
    path(
        "manage/landing/",
        include(("apps.pages.manage_urls", "pages_manage"), namespace="pages_manage"),
    ),
]
