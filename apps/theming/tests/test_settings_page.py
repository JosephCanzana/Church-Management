"""Page tests for /settings/ (brick 9): own settings only, one confirmed Save, no flash on load."""
from django.test import TestCase
from django.urls import reverse

from apps.theming.models import UserSettings
from apps.theming.tests.test_user_settings import make_user


class SettingsPageTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.url = reverse("theming:settings")

    def test_visitor_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_every_role_can_open_it(self):
        for role in ("super_admin", "admin", "coordinator", "member"):
            with self.subTest(role=role):
                self.client.force_login(make_user(role=role))
                self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_page_shows_the_defaults_and_asks_before_saving(self):
        self.client.force_login(self.user)
        html = self.client.get(self.url).content.decode()
        self.assertIn("data-confirm=", html)
        self.assertRegex(html, r'value="system"[^>]*checked')
        self.assertRegex(html, r'value="bottom"[^>]*checked')

    def test_post_saves_and_redirects(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {"color_mode": "dark", "mobile_nav_style": "hamburger"})
        self.assertRedirects(response, self.url)
        row = UserSettings.objects.get(user=self.user)
        self.assertEqual((row.color_mode, row.mobile_nav_style), ("dark", "hamburger"))

    def test_invalid_post_shows_errors_and_saves_nothing(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url, {"color_mode": "purple", "mobile_nav_style": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        row = UserSettings.objects.get(user=self.user)
        self.assertEqual((row.color_mode, row.mobile_nav_style), ("system", "bottom"))

    def test_a_user_id_in_the_request_is_ignored(self):
        other = make_user(first_name="Ben")
        self.client.force_login(self.user)
        self.client.post(
            f"{self.url}?user={other.pk}",
            {"color_mode": "dark", "mobile_nav_style": "hamburger", "user": other.pk},
        )
        self.assertEqual(UserSettings.objects.get(user=other).color_mode, "system")
        self.assertEqual(UserSettings.objects.get(user=self.user).color_mode, "dark")

    def test_saved_dark_is_in_the_first_html_so_there_is_no_flash(self):
        UserSettings.objects.filter(user=self.user).update(color_mode="dark")
        self.client.force_login(self.user)
        html = self.client.get(self.url).content.decode()
        self.assertIn('data-pref="dark"', html)
        self.assertIn('class="dark"', html)

    def test_saved_nav_style_reaches_the_next_page(self):
        UserSettings.objects.filter(user=self.user).update(mobile_nav_style="hamburger")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).context["mobile_nav_style"], "hamburger")

    def test_public_navbar_toggle_is_for_visitors_only(self):
        visitor_html = self.client.get(reverse("pages:give")).content.decode()
        self.assertIn("toggleMode", visitor_html)
        self.client.force_login(self.user)
        member_html = self.client.get(reverse("pages:give")).content.decode()
        self.assertNotIn("toggleMode()", member_html)
