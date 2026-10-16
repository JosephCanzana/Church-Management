"""Public landing-page behavior for anonymous and authenticated visitors."""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.decorators import home_url_for
from apps.accounts.models import Extension, Role, Status, User


class LandingPageTests(TestCase):
    """Anonymous visitors see the landing page; signed-in users see their dashboard."""

    def test_anonymous_visitors_see_the_landing_page(self):
        response = self.client.get(reverse("pages:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/landing.html")

    def test_authenticated_visitors_go_to_their_role_dashboard(self):
        extension = Extension.objects.create(name="Test Extension")
        role_data = {
            Role.SUPER_ADMIN: {},
            Role.ADMIN: {},
            Role.COORDINATOR: {"extension": extension},
            Role.MEMBER: {"extension": extension},
        }

        for role, extra_fields in role_data.items():
            with self.subTest(role=role):
                user = User.objects.create_user(
                    password="test-password",
                    first_name="Test",
                    last_name=role,
                    role=role,
                    status=Status.ACTIVE,
                    must_change_password=False,
                    **extra_fields,
                )
                self.client.force_login(user)
                response = self.client.get(reverse("pages:landing"))
                self.assertRedirects(
                    response,
                    home_url_for(user),
                    fetch_redirect_response=False,
                )