"""accounts.urls: maps URLs to the accounts views.

Included from the project urls with:  path("accounts/", include("apps.accounts.urls"))
The `accounts` namespace lets templates use {% url 'accounts:login' %}.
(Save this file as apps/accounts/urls/__init__.py.)
"""
from django.urls import path

from .. import views
from ..views import password_reset, profile

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("activate/", views.activate_view, name="activate"),
    # Profile: the signed-in person's own details, email and password (every role).
    path("profile/", profile.profile_view, name="profile"),
    path("profile/details/", profile.details_view, name="profile_details"),
    path("profile/photo/remove/", profile.photo_remove_view, name="profile_photo_remove"),
    path("profile/email/", profile.email_view, name="profile_email"),
    path("profile/email/resend/", profile.email_resend_view, name="profile_email_resend"),
    path("profile/email/cancel/", profile.email_cancel_view, name="profile_email_cancel"),
    path("profile/email/remove/", profile.email_remove_view, name="profile_email_remove"),
    path("profile/password/", profile.password_view, name="profile_password"),
    # The link in the verification email.
    path("verify-email/<str:token>/", profile.verify_email_view, name="verify_email"),
    # Forgot / reset password (public pages).
    path("forgot-password/", password_reset.forgot_password_view, name="forgot_password"),
    path("forgot-password/sent/", password_reset.forgot_password_done_view, name="forgot_password_done"),
    # The link in the reset email.
    path("reset-password/<str:token>/", password_reset.reset_password_view, name="reset_password"),
]