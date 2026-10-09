"""Tests for the profile: email verification, removal, password change and the pages.

Run:  docker compose exec web python manage.py test apps.accounts.tests.test_profile -v 2
Mail goes to Django's in-memory outbox during tests, so nothing is really sent.
"""
import re
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import EmailToken, Role, Status, User
from apps.accounts.services import profile as svc
from apps.core.services import ServiceError

PASSWORD = "Str0ng-Test-Pass!"


def make_user(first, last="tester"):
    """An active admin (admins need no extension), already activated."""
    return User.objects.create_user(
        first_name=first, last_name=last, role=Role.ADMIN, status=Status.ACTIVE,
        must_change_password=False, password=PASSWORD,
    )


def last_link_token():
    """The raw token from the newest verification email."""
    return re.search(r"/accounts/verify-email/([^/\s]+)/", mail.outbox[-1].body).group(1)


@override_settings(VERIFY_RESEND_SECONDS=0)
class EmailVerificationTests(TestCase):
    def test_request_sets_pending_and_sends_one_mail(self):
        user = make_user("ana")
        svc.request_email_verification(None, user, "Ana@Example.com ")
        user.refresh_from_db()
        self.assertEqual(user.pending_email, "ana@example.com")
        self.assertIsNone(user.email)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ana@example.com"])
        self.assertEqual(EmailToken.objects.filter(user=user).count(), 1)

    def test_token_is_stored_hashed_not_raw(self):
        user = make_user("ana")
        svc.request_email_verification(None, user, "ana@example.com")
        self.assertFalse(EmailToken.objects.filter(token_hash=last_link_token()).exists())

    def test_verified_email_of_someone_else_is_refused(self):
        owner = make_user("owner")
        svc.request_email_verification(None, owner, "shared@example.com")
        svc.verify_email(None, owner, last_link_token())
        other = make_user("other")
        with self.assertRaises(ServiceError):
            svc.request_email_verification(None, other, "SHARED@example.com")

    def test_pending_email_of_someone_else_still_gets_a_link(self):
        first = make_user("first")
        second = make_user("second")
        svc.request_email_verification(None, first, "shared@example.com")
        svc.request_email_verification(None, second, "shared@example.com")
        self.assertEqual(len(mail.outbox), 2)

    def test_verifying_moves_pending_and_removes_it_from_the_other_person(self):
        first = make_user("first")
        second = make_user("second")
        svc.request_email_verification(None, first, "shared@example.com")
        svc.request_email_verification(None, second, "shared@example.com")
        result = svc.verify_email(None, second, last_link_token())
        self.assertTrue(result.changed)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(second.email, "shared@example.com")
        self.assertIsNotNone(second.email_verified_at)
        self.assertIsNone(second.pending_email)
        self.assertIsNone(first.pending_email)       # taken away from the other person
        self.assertIsNone(first.email)

    def test_repeating_a_used_link_is_calm(self):
        user = make_user("ana")
        svc.request_email_verification(None, user, "ana@example.com")
        token = last_link_token()
        svc.verify_email(None, user, token)
        self.assertFalse(svc.verify_email(None, user, token).changed)

    def test_expired_link_is_refused(self):
        user = make_user("ana")
        svc.request_email_verification(None, user, "ana@example.com")
        EmailToken.objects.filter(user=user).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(ServiceError):
            svc.verify_email(None, user, last_link_token())

    def test_link_of_another_account_is_refused(self):
        owner = make_user("owner")
        stranger = make_user("stranger")
        svc.request_email_verification(None, owner, "owner@example.com")
        with self.assertRaises(ServiceError):
            svc.verify_email(None, stranger, last_link_token())

    def test_a_new_link_replaces_the_old_one(self):
        user = make_user("ana")
        svc.request_email_verification(None, user, "ana@example.com")
        old = last_link_token()
        svc.request_email_verification(None, user)          # resend
        with self.assertRaises(ServiceError):
            svc.verify_email(None, user, old)
        self.assertTrue(svc.verify_email(None, user, last_link_token()).changed)

    def test_cancel_and_remove(self):
        user = make_user("ana")
        svc.request_email_verification(None, user, "ana@example.com")
        self.assertTrue(svc.cancel_pending_email(None, user))
        self.assertFalse(svc.cancel_pending_email(None, user))      # already done
        svc.request_email_verification(None, user, "ana@example.com")
        svc.verify_email(None, user, last_link_token())
        self.assertTrue(svc.remove_email(None, user))
        user.refresh_from_db()
        self.assertIsNone(user.email)
        self.assertIsNone(user.email_verified_at)
        self.assertFalse(svc.remove_email(None, user))


class ResendLimitTests(TestCase):
    def test_second_request_inside_the_cooldown_is_refused(self):
        user = make_user("ana")
        svc.request_email_verification(None, user, "ana@example.com")
        with self.assertRaises(ServiceError):
            svc.request_email_verification(None, user)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(VERIFY_RESEND_SECONDS=0, VERIFY_MAX_PER_HOUR=2)
    def test_hourly_limit(self):
        user = make_user("ana")
        svc.request_email_verification(None, user, "ana@example.com")
        svc.request_email_verification(None, user)
        with self.assertRaises(ServiceError):
            svc.request_email_verification(None, user)


@override_settings(VERIFY_RESEND_SECONDS=0)
class ProfilePageTests(TestCase):
    def test_profile_page_needs_login(self):
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_profile_page_renders(self):
        user = make_user("ana")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)

    def test_opening_the_link_does_not_verify_but_confirming_does(self):
        user = make_user("ana")
        svc.request_email_verification(None, user, "ana@example.com")
        url = reverse("accounts:verify_email", args=[last_link_token()])
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 200)
        user.refresh_from_db()
        self.assertIsNone(user.email)                       # a GET changes nothing
        self.client.post(url)
        user.refresh_from_db()
        self.assertEqual(user.email, "ana@example.com")

    def test_link_sends_logged_out_people_to_login_first(self):
        user = make_user("ana")
        svc.request_email_verification(None, user, "ana@example.com")
        response = self.client.get(reverse("accounts:verify_email", args=[last_link_token()]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_wrong_current_password_changes_nothing(self):
        user = make_user("ana")
        self.client.force_login(user)
        response = self.client.post(reverse("accounts:profile_password"), {
            "current_password": "wrong", "new_password": "Another-Str0ng-Pass!",
            "confirm_password": "Another-Str0ng-Pass!",
        })
        self.assertEqual(response.status_code, 200)         # page shown again with the error
        user.refresh_from_db()
        self.assertTrue(user.check_password(PASSWORD))

    def test_password_change_keeps_this_session_signed_in(self):
        user = make_user("ana")
        self.client.force_login(user)
        self.client.post(reverse("accounts:profile_password"), {
            "current_password": PASSWORD, "new_password": "Another-Str0ng-Pass!",
            "confirm_password": "Another-Str0ng-Pass!",
        })
        user.refresh_from_db()
        self.assertTrue(user.check_password("Another-Str0ng-Pass!"))
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 200)