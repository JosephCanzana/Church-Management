"""bible urls (namespace ``bible``), mounted at /bible/."""
from django.urls import path

from . import views

app_name = "bible"

urlpatterns = [
    path("", views.reader_view, name="reader"),
    path("version/", views.set_version_view, name="set_version"),
    path("<str:book>/", views.book_view, name="book"),
    path("<str:book>/<int:chapter>/", views.chapter_view, name="chapter"),
        path("<str:book>/<int:chapter>/finish/", views.finish_chapter_view, name="finish"),
    path("<str:book>/<int:chapter>/bookmark/", views.bookmark_view, name="bookmark"),
]
