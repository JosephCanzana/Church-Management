"""accounts.tests.test_activation: tests for /accounts/activate/.

Covers the page, the service rule, the audit row and the middleware that keeps
people who must activate on the activation page.

Run with:
    docker compose exec web python manage.py test apps.accounts.tests.test_activation -v 2
"""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Status, User
from apps.accounts.services.activation import activate_account, user_needs_activation
from apps.accounts.tests.test_accounts import PASSWORD, make_user
from apps.audit.models import AuditLog

NEW_PASSWORD = "N3w-passw0rd-Test"


class ActivationPageTests(TestCase):
    """GET and POST /accounts/activate/."""

    def setUp(self):
        self.url = reverse("accounts:activate")
        self.member_home = reverse("dashboards:member")

    def post(self, new_password=NEW_PASSWORD, confirm=None):
        """Submit the activation form (confirm defaults to a matching value)."""
        return self.client.post(
            self.url,
            {
                "new_password": new_password,
                "confirm_password": new_password if confirm is None else confirm,
            },
        )

    def login_not_activated(self, **overrides):
        """Sign in a person who still has a temporary password."""
        user = make_user(
            status=Status.NOT_ACTIVATED, must_change_password=True, **overrides
        )
        self.client.force_login(user)
        return user

    # --- access ---------------------------------------------------------
    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={self.url}",
            fetch_redirect_response=False,
        )

    def test_activated_user_is_sent_home(self):
        self.client.force_login(make_user())
        response = self.client.get(self.url)
        self.assertRedirects(response, self.member_home, fetch_redirect_response=False)

    def test_not_activated_user_sees_the_form(self):
        self.login_not_activated()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New password")
        self.assertContains(response, "Confirm password")

    # --- success --------------------------------------------------------
    def test_valid_submit_activates_the_account(self):
        user = self.login_not_activated()
        response = self.post()
        self.assertRedirects(response, self.member_home, fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertEqual(user.status, Status.ACTIVE)
        self.assertFalse(user.must_change_password)
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertFalse(user.check_password(PASSWORD))

    def test_person_stays_logged_in_after_the_password_changes(self):
        user = self.login_not_activated()
        self.post()
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertEqual(self.client.get(self.member_home).status_code, 200)

    def test_active_user_with_forced_change_keeps_status_and_clears_the_flag(self):
        # What a password reset leaves behind: active, but must change.
        user = make_user(status=Status.ACTIVE, must_change_password=True)
        self.client.force_login(user)
        self.post()
        user.refresh_from_db()
        self.assertEqual(user.status, Status.ACTIVE)
        self.assertFalse(user.must_change_password)
        self.assertTrue(user.check_password(NEW_PASSWORD))

    # --- validation -----------------------------------------------------
    def assert_unchanged(self, user):
        user.refresh_from_db()
        self.assertEqual(user.status, Status.NOT_ACTIVATED)
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password(PASSWORD))

    def test_mismatched_confirmation_is_rejected(self):
        user = self.login_not_activated()
        response = self.post(confirm="something-else-123")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "do not match")
        self.assert_unchanged(user)

    def test_reusing_the_temporary_password_is_rejected(self):
        user = self.login_not_activated()
        response = self.post(new_password=PASSWORD)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "different from your temporary")
        self.assert_unchanged(user)

    def test_short_password_is_rejected(self):
        user = self.login_not_activated()
        response = self.post(new_password="Ab1-xyz")
        self.assertEqual(response.status_code, 200)
        self.assert_unchanged(user)

    def test_all_numeric_password_is_rejected(self):
        user = self.login_not_activated()
        response = self.post(new_password="2468013579")
        self.assertEqual(response.status_code, 200)
        self.assert_unchanged(user)

    def test_password_is_never_echoed_back(self):
        self.login_not_activated()
        response = self.post(new_password="12345678")
        self.assertNotContains(response, "12345678")

    # --- repeat submits -------------------------------------------------
    def test_second_submit_changes_nothing(self):
        user = self.login_not_activated()
        self.post()
        response = self.post(new_password="An0ther-passw0rd-Test")
        self.assertRedirects(response, self.member_home, fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertEqual(AuditLog.objects.filter(action="account.activated").count(), 1)


class ActivationServiceTests(TestCase):
    """activate_account() and user_needs_activation()."""

    def test_needs_activation_rules(self):
        self.assertTrue(user_needs_activation(make_user(status=Status.NOT_ACTIVATED)))
        self.assertTrue(user_needs_activation(make_user(must_change_password=True)))
        self.assertFalse(user_needs_activation(make_user()))

    def test_suspended_account_is_not_activated(self):
        user = make_user(
            status=Status.SUSPENDED, must_change_password=True,
        )
        result = activate_account(None, user, NEW_PASSWORD)
        self.assertFalse(result.changed)
        user.refresh_from_db()
        self.assertEqual(user.status, Status.SUSPENDED)
        self.assertTrue(user.check_password(PASSWORD))

    def test_activation_writes_an_audit_row_without_secrets(self):
        user = make_user(status=Status.NOT_ACTIVATED, must_change_password=True)
        self.client.force_login(user)
        self.client.post(
            reverse("accounts:activate"),
            {"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
        )
        row = AuditLog.objects.get(action="account.activated")
        self.assertEqual(row.actor_id, user.pk)
        self.assertEqual(row.entity_id, user.pk)
        self.assertEqual(row.before, {"status": "not_activated", "needs_activation": True})
        self.assertEqual(row.after, {"status": "active", "needs_activation": False})
        self.assertNotIn(NEW_PASSWORD, repr(row.before) + repr(row.after))
        self.assertEqual(User.objects.get(pk=user.pk).status, Status.ACTIVE)


class ActivationMiddlewareTests(TestCase):
    """People who must activate cannot open other pages."""

    def test_other_pages_redirect_to_activation(self):
        user = make_user(status=Status.NOT_ACTIVATED, must_change_password=True)
        self.client.force_login(user)
        response = self.client.get(reverse("dashboards:member"))
        self.assertRedirects(
            response, reverse("accounts:activate"), fetch_redirect_response=False
        )

    def test_activation_page_and_logout_stay_reachable(self):
        user = make_user(status=Status.NOT_ACTIVATED, must_change_password=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("accounts:activate")).status_code, 200)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(
            response, reverse("accounts:login"), fetch_redirect_response=False
        )

    def test_activated_user_is_not_redirected(self):
        self.client.force_login(make_user())
        response = self.client.get(reverse("dashboards:member"))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_is_not_affected(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)