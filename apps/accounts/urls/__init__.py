"""accounts.urls: maps URLs to the accounts views.

Included from the project urls with:  path("accounts/", include("apps.accounts.urls"))
The `accounts` namespace lets templates use {% url 'accounts:login' %}.
"""
from django.urls import path

from .. import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("activate/", views.activate_view, name="activate"),  # placeholder for now
]