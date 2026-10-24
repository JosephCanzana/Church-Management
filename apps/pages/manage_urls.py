"""URLs for admin-managed landing-page text blocks."""
from django.urls import path

from . import manage_views

app_name = "pages_manage"

urlpatterns = [
    path("", manage_views.content_list, name="content_list"),
    path("images/", manage_views.image_list, name="image_list"),
    path("images/new/", manage_views.image_create, name="image_create"),
    path("images/<int:pk>/edit/", manage_views.image_edit, name="image_edit"),
    path("images/<int:pk>/active/", manage_views.image_toggle, name="image_toggle"),
    path("images/<int:pk>/move/<str:direction>/", manage_views.image_move, name="image_move"),
    path("images/<int:pk>/delete/", manage_views.image_delete, name="image_delete"),
    path("donation-accounts/", manage_views.donation_account_list, name="donation_accounts"),
    path("donation-accounts/new/", manage_views.donation_account_create, name="donation_account_create"),
    path("donation-accounts/<int:pk>/edit/", manage_views.donation_account_edit, name="donation_account_edit"),
    path("donation-accounts/<int:pk>/active/", manage_views.donation_account_toggle, name="donation_account_toggle"),
    path("donation-accounts/<int:pk>/move/<str:direction>/", manage_views.donation_account_move, name="donation_account_move"),
    path("donation-accounts/<int:pk>/delete/", manage_views.donation_account_delete, name="donation_account_delete"),
    path("footer-links/", manage_views.footer_link_list, name="footer_links"),
    path("footer-links/new/", manage_views.footer_link_create, name="footer_link_create"),
    path("footer-links/reset/", manage_views.footer_links_reset, name="footer_links_reset"),
    path("footer-links/<str:link_id>/edit/", manage_views.footer_link_edit, name="footer_link_edit"),
    path("footer-links/<str:link_id>/move/<str:direction>/", manage_views.footer_link_move, name="footer_link_move"),
    path("footer-links/<str:link_id>/delete/", manage_views.footer_link_delete, name="footer_link_delete"),
    path("reset-all/", manage_views.content_reset_all, name="content_reset_all"),
    path("<str:key>/reset/", manage_views.content_reset, name="content_reset"),
    path("<str:key>/", manage_views.content_edit, name="content_edit"),
]