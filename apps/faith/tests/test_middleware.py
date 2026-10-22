"""Tests for faith.middleware.DailyOpenMiddleware (it must be listed in settings.MIDDLEWARE)."""
from datetime import date
from unittest import mock

from django.test import TestCase

from apps.faith.models import UserDailyActivity

from .helpers import make_user

HTML = "text/html,application/xhtml+xml,*/*;q=0.8"
URL = "/"   # any page that answers a signed-in GET (a redirect is fine: the middleware runs first)


class DailyOpenMiddlewareTests(TestCase):
    """Counts one page GET per session-day; ignores everything else."""

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        patcher = mock.patch("apps.faith.middleware.record_open")
        self.record_open = patcher.start()
        self.addCleanup(patcher.stop)

    def test_counts_page_get_once_per_session_day(self):
        self.client.get(URL, HTTP_ACCEPT=HTML)
        self.client.get(URL, HTTP_ACCEPT=HTML)
        self.assertEqual(self.record_open.call_count, 1)

    def test_counts_again_on_the_next_day(self):
        with mock.patch("apps.faith.middleware.timezone.localdate", return_value=date(2026, 3, 10)):
            self.client.get(URL, HTTP_ACCEPT=HTML)
        with mock.patch("apps.faith.middleware.timezone.localdate", return_value=date(2026, 3, 11)):
            self.client.get(URL, HTTP_ACCEPT=HTML)
        self.assertEqual(self.record_open.call_count, 2)

    def test_passes_the_user_and_manila_date(self):
        with mock.patch("apps.faith.middleware.timezone.localdate", return_value=date(2026, 3, 10)):
            self.client.get(URL, HTTP_ACCEPT=HTML)
        self.record_open.assert_called_once_with(self.user, date(2026, 3, 10))

    def test_skips_json_requests(self):
        self.client.get(URL, HTTP_ACCEPT="application/json")
        self.assertEqual(self.record_open.call_count, 0)

    def test_skips_ajax_requests(self):
        self.client.get(URL, HTTP_ACCEPT=HTML, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(self.record_open.call_count, 0)

    def test_skips_requests_without_an_accept_header(self):
        self.client.get(URL)
        self.assertEqual(self.record_open.call_count, 0)

    def test_skips_post(self):
        self.client.post(URL, HTTP_ACCEPT=HTML)
        self.assertEqual(self.record_open.call_count, 0)

    def test_skips_guests(self):
        self.client.logout()
        self.client.get(URL, HTTP_ACCEPT=HTML)
        self.assertEqual(self.record_open.call_count, 0)

    def test_skips_people_who_cannot_sign_in(self):
        user_model = type(self.user)
        with mock.patch.object(user_model, "can_sign_in", new_callable=mock.PropertyMock, return_value=False):
            self.client.get(URL, HTTP_ACCEPT=HTML)
        self.assertEqual(self.record_open.call_count, 0)

    def test_a_failure_never_breaks_the_page_and_is_retried(self):
        self.record_open.side_effect = RuntimeError("boom")
        response = self.client.get(URL, HTTP_ACCEPT=HTML)
        self.assertLess(response.status_code, 500)
        self.record_open.side_effect = None
        self.client.get(URL, HTTP_ACCEPT=HTML)
        self.assertEqual(self.record_open.call_count, 2)   # the session day was not marked after the failure


class DailyOpenIntegrationTests(TestCase):
    """One real (unmocked) pass through the middleware, using the project's MIDDLEWARE setting."""

    def test_page_visit_writes_the_activity_row(self):
        user = make_user()
        self.client.force_login(user)
        self.client.get(URL, HTTP_ACCEPT=HTML)
        self.assertTrue(UserDailyActivity.objects.filter(user=user, opened_app=True).exists())
