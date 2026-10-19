"""accounts.tests.test_password_change: tests for /accounts/change-password/.

Covers the page, the service rule, the audit row and the middleware that keeps
people who must change their password on the change-password page.

Run with:
    docker compose exec web python manage.py test apps.accounts.tests.test_password_change -v 2
"""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Status, User
from apps.accounts.models import PersonType
from apps.accounts.services.password_change import complete_password_change, user_needs_password_change
from apps.accounts.tests.test_accounts import PASSWORD, make_user
from apps.audit.models import AuditLog

NEW_PASSWORD = "N3w@passw0rdTest"


class ChangePasswordPageTests(TestCase):
    """GET and POST /accounts/change-password/."""

    def setUp(self):
        self.url = reverse("accounts:change_password")
        self.member_home = reverse("dashboards:member")

    def post(self, new_password=NEW_PASSWORD, confirm=None):
        """Submit the change-password form (confirm defaults to a matching value)."""
        return self.client.post(
            self.url,
            {
                "new_password": new_password,
                "confirm_password": new_password if confirm is None else confirm,
            },
        )

    def login_must_change(self, **overrides):
        """Sign in a person who still has a temporary password."""
        user = make_user(must_change_password=True, **overrides)
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

    def test_user_with_nothing_to_change_is_sent_home(self):
        self.client.force_login(make_user())
        response = self.client.get(self.url)
        self.assertRedirects(response, self.member_home, fetch_redirect_response=False)

    def test_user_who_must_change_sees_the_form(self):
        self.login_must_change()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "New password")
        self.assertContains(response, "Confirm password")

    # --- success --------------------------------------------------------
    def test_valid_submit_changes_the_password(self):
        user = self.login_must_change()
        response = self.post()
        self.assertRedirects(response, self.member_home, fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertEqual(user.status, Status.ACTIVE)
        self.assertFalse(user.must_change_password)
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertFalse(user.check_password(PASSWORD))

    def test_person_stays_logged_in_after_the_password_changes(self):
        user = self.login_must_change()
        self.post()
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertEqual(self.client.get(self.member_home).status_code, 200)

    def test_the_status_stays_active_and_the_flag_clears(self):
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
        self.assertEqual(user.status, Status.ACTIVE)
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password(PASSWORD))

    def test_mismatched_confirmation_is_rejected(self):
        user = self.login_must_change()
        response = self.post(confirm="something-else-123")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "do not match")
        self.assert_unchanged(user)

    def test_reusing_the_temporary_password_is_rejected(self):
        user = self.login_must_change()
        response = self.post(new_password=PASSWORD)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "different from your temporary")
        self.assert_unchanged(user)

    def test_short_password_is_rejected(self):
        user = self.login_must_change()
        response = self.post(new_password="Ab1-xyz")
        self.assertEqual(response.status_code, 200)
        self.assert_unchanged(user)

    def test_symbols_outside_the_password_pattern_are_rejected(self):
        user = self.login_must_change()
        for password in ("N3w-passw0rdTest", "N3w_passw0rdTest"):
            response = self.post(new_password=password)
            self.assertEqual(response.status_code, 200)
            self.assert_unchanged(user)

    def test_all_numeric_password_is_rejected(self):
        user = self.login_must_change()
        response = self.post(new_password="2468013579")
        self.assertEqual(response.status_code, 200)
        self.assert_unchanged(user)

    def test_password_is_never_echoed_back(self):
        self.login_must_change()
        response = self.post(new_password="12345678")
        self.assertNotContains(response, "12345678")

    # --- repeat submits -------------------------------------------------
    def test_second_submit_changes_nothing(self):
        user = self.login_must_change()
        self.post()
        response = self.post(new_password="An0ther@passw0rdTest")
        self.assertRedirects(response, self.member_home, fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.check_password(NEW_PASSWORD))
        self.assertEqual(AuditLog.objects.filter(action="account.password_set").count(), 1)


class PasswordChangeServiceTests(TestCase):
    """complete_password_change() and user_needs_password_change()."""

    def test_needs_password_change_rules(self):
        self.assertTrue(user_needs_password_change(make_user(must_change_password=True)))
        self.assertFalse(user_needs_password_change(make_user()))

    def test_a_person_without_an_account_is_left_alone(self):
        record = make_user(person_type=PersonType.MEMBER_RECORD, must_change_password=True)
        result = complete_password_change(None, record, NEW_PASSWORD)
        self.assertFalse(result.changed)
        record.refresh_from_db()
        self.assertTrue(record.must_change_password)
        self.assertFalse(record.has_usable_password() and record.check_password(NEW_PASSWORD))

    def test_suspended_account_is_not_changed(self):
        user = make_user(
            status=Status.SUSPENDED, must_change_password=True,
        )
        result = complete_password_change(None, user, NEW_PASSWORD)
        self.assertFalse(result.changed)
        user.refresh_from_db()
        self.assertEqual(user.status, Status.SUSPENDED)
        self.assertTrue(user.check_password(PASSWORD))

    def test_the_change_writes_an_audit_row_without_secrets(self):
        user = make_user(must_change_password=True)
        self.client.force_login(user)
        self.client.post(
            reverse("accounts:change_password"),
            {"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
        )
        row = AuditLog.objects.get(action="account.password_set")
        self.assertEqual(row.actor_id, user.pk)
        self.assertEqual(row.entity_id, user.pk)
        # "needs_change", not "needs_password_change": log_action drops any key containing "password".
        self.assertEqual(row.before, {"needs_change": True})
        self.assertEqual(row.after, {"needs_change": False})
        self.assertNotIn(NEW_PASSWORD, repr(row.before) + repr(row.after))
        self.assertEqual(User.objects.get(pk=user.pk).status, Status.ACTIVE)


class PasswordChangeMiddlewareTests(TestCase):
    """People who must change their password cannot open other pages."""

    def test_other_pages_redirect_to_change_password(self):
        user = make_user(must_change_password=True)
        self.client.force_login(user)
        response = self.client.get(reverse("dashboards:member"))
        self.assertRedirects(
            response, reverse("accounts:change_password"), fetch_redirect_response=False
        )

    def test_change_password_page_and_logout_stay_reachable(self):
        user = make_user(must_change_password=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("accounts:change_password")).status_code, 200)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(
            response, reverse("accounts:login"), fetch_redirect_response=False
        )

    def test_user_with_nothing_to_change_is_not_redirected(self):
        self.client.force_login(make_user())
        response = self.client.get(reverse("dashboards:member"))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_is_not_affected(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)