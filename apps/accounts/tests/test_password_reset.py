"""accounts.tests.test_password_reset: forgot password and reset by emailed link.

Run with:
    docker compose exec web python manage.py test apps.accounts.tests.test_password_reset -v 2

Emails go to Django's in-memory outbox (`mail.outbox`). Mail is sent after the
transaction commits, so tests wrap calls in `captureOnCommitCallbacks(execute=True)`.
"""
import re
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import EmailToken, Extension, Role, Status, User
from apps.accounts.services import password_reset as svc
from apps.audit.models import AuditLog
from apps.core.services import ServiceError

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "Brand-new-Pass-2026!"
RESET = EmailToken.Purpose.RESET_PASSWORD


def make_person(extension, **overrides):
    """An active member with a verified email. Created one by one so account_id is generated."""
    fields = dict(
        first_name="Juan", last_name="Dela Cruz", role=Role.MEMBER, extension=extension,
        status=Status.ACTIVE, must_change_password=False,
        email="juan@example.com", email_verified_at=timezone.now(),
    )
    fields.update(overrides)
    return User.objects.create_user(password=PASSWORD, **fields)


def token_from_outbox():
    """The raw token found in the first reset email."""
    body = mail.outbox[0].body
    return re.search(r"/reset-password/([^/\s]+)/", body).group(1)


@override_settings(RESET_RESEND_SECONDS=60, RESET_MAX_PER_HOUR=5, RESET_LINK_MINUTES=30)
class RequestResetServiceTests(TestCase):
    def setUp(self):
        self.ext = Extension.objects.create(name="Cabanatuan")
        self.person = make_person(self.ext)

    def request(self, identifier):
        with self.captureOnCommitCallbacks(execute=True):
            return svc.request_password_reset(None, identifier)

    def test_account_id_sends_one_email(self):
        self.assertTrue(self.request(self.person.account_id))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["juan@example.com"])

    def test_verified_email_sends_one_email_whatever_the_case(self):
        self.assertTrue(self.request("  JUAN@Example.com "))
        self.assertEqual(len(mail.outbox), 1)

    def test_unknown_and_badly_shaped_identifiers_send_nothing(self):
        for value in ("99999999999", "nobody@example.com", "not an id", ""):
            self.assertFalse(self.request(value), value)
        self.assertEqual(len(mail.outbox), 0)

    def test_people_who_cannot_reset_get_no_email(self):
        for status in (Status.NOT_ACTIVATED, Status.SUSPENDED):
            User.objects.filter(pk=self.person.pk).update(status=status)
            self.assertFalse(self.request(self.person.account_id), status)
        User.objects.filter(pk=self.person.pk).update(status=Status.ACTIVE)
        self.assertEqual(len(mail.outbox), 0)

    def test_no_verified_email_means_no_reset(self):
        other = make_person(self.ext, email=None, email_verified_at=None, first_name="Pia")
        other.pending_email = "pia@example.com"
        other.save(update_fields=["pending_email"])
        self.assertFalse(self.request(other.account_id))
        self.assertFalse(self.request("pia@example.com"))   # pending addresses never match
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_gap_and_hourly_cap_are_silent(self):
        self.assertTrue(self.request(self.person.account_id))
        self.assertFalse(self.request(self.person.account_id))          # too soon
        self.assertEqual(len(mail.outbox), 1)
        # Age the existing tokens, then hit the hourly cap.
        EmailToken.objects.filter(user=self.person).update(created_at=timezone.now() - timedelta(minutes=5))
        for _ in range(4):
            EmailToken.objects.filter(user=self.person).update(created_at=timezone.now() - timedelta(minutes=5))
            self.request(self.person.account_id)
        EmailToken.objects.filter(user=self.person).update(created_at=timezone.now() - timedelta(minutes=5))
        self.assertFalse(self.request(self.person.account_id))

    def test_a_new_request_voids_the_older_link(self):
        self.request(self.person.account_id)
        first = token_from_outbox()
        EmailToken.objects.filter(user=self.person).update(created_at=timezone.now() - timedelta(minutes=5))
        self.request(self.person.account_id)
        self.assertIsNone(svc.get_valid_reset_token(first))

    def test_only_the_hash_is_stored_and_the_audit_row_has_no_secrets(self):
        self.request(self.person.account_id)
        raw = token_from_outbox()
        self.assertFalse(EmailToken.objects.filter(token_hash=raw).exists())
        row = AuditLog.objects.get(action="account.password_reset_requested")
        blob = f"{row.before}{row.after}{row.entity_label}"
        self.assertNotIn(raw, blob)
        self.assertNotIn(self.person.email, blob)


@override_settings(RESET_RESEND_SECONDS=60, RESET_MAX_PER_HOUR=5, RESET_LINK_MINUTES=30)
class ResetServiceTests(TestCase):
    def setUp(self):
        self.ext = Extension.objects.create(name="Cabanatuan")
        self.person = make_person(self.ext, must_change_password=True)
        with self.captureOnCommitCallbacks(execute=True):
            svc.request_password_reset(None, self.person.account_id)
        self.raw = token_from_outbox()
        mail.outbox.clear()

    def reset(self, raw=None, password=NEW_PASSWORD):
        with self.captureOnCommitCallbacks(execute=True):
            return svc.reset_password(None, raw or self.raw, password)

    def test_success_changes_password_clears_flag_and_confirms_by_email(self):
        self.reset()
        self.person.refresh_from_db()
        self.assertTrue(self.person.check_password(NEW_PASSWORD))
        self.assertFalse(self.person.must_change_password)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Your password was changed")
        self.assertEqual(AuditLog.objects.filter(action="account.password_reset").count(), 1)

    def test_the_link_works_only_once(self):
        self.reset()
        with self.assertRaises(ServiceError):
            self.reset(password="Another-Pass-2026!")
        self.person.refresh_from_db()
        self.assertTrue(self.person.check_password(NEW_PASSWORD))

    def test_garbage_and_expired_tokens_fail(self):
        for raw in ("garbage", "", None):
            with self.assertRaises(ServiceError):
                svc.reset_password(None, raw, NEW_PASSWORD)
        EmailToken.objects.filter(user=self.person).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(ServiceError):
            self.reset()

    def test_get_valid_token_does_not_use_the_token_up(self):
        self.assertIsNotNone(svc.get_valid_reset_token(self.raw))
        self.assertIsNotNone(svc.get_valid_reset_token(self.raw))
        self.assertIsNone(EmailToken.objects.get(user=self.person).used_at)

    def test_link_dies_when_the_account_stops_being_resettable(self):
        for change in ({"status": Status.SUSPENDED}, {"status": Status.NOT_ACTIVATED}):
            User.objects.filter(pk=self.person.pk).update(**change)
            with self.assertRaises(ServiceError):
                self.reset()
        User.objects.filter(pk=self.person.pk).update(status=Status.ACTIVE)

    def test_link_dies_when_the_verified_email_changed(self):
        User.objects.filter(pk=self.person.pk).update(email="new@example.com")
        self.assertIsNone(svc.get_valid_reset_token(self.raw))

    def test_old_sessions_end(self):
        self.client.force_login(self.person)
        self.reset()
        self.assertEqual(self.client.get(reverse("accounts:profile")).status_code, 302)
    # Two parallel POSTs need real threads and commits (TransactionTestCase); the
    # single-use test above plus the row locks in reset_password() cover the rule.


@override_settings(RESET_RESEND_SECONDS=60, RESET_MAX_PER_HOUR=5, RESET_LINK_MINUTES=30)
class ResetViewTests(TestCase):
    def setUp(self):
        self.ext = Extension.objects.create(name="Cabanatuan")
        self.person = make_person(self.ext)

    def test_same_response_for_real_and_unknown_accounts(self):
        url = reverse("accounts:forgot_password")
        done = reverse("accounts:forgot_password_done")
        real = self.client.post(url, {"identifier": self.person.account_id})
        fake = self.client.post(url, {"identifier": "99999999999"})
        self.assertRedirects(real, done, fetch_redirect_response=False)
        self.assertRedirects(fake, done, fetch_redirect_response=False)
        self.assertEqual(self.client.get(done).content, self.client.get(done).content)

    def test_forgot_page_renders_for_anonymous_visitors(self):
        self.assertEqual(self.client.get(reverse("accounts:forgot_password")).status_code, 200)

    def test_reset_get_shows_form_and_keeps_the_token(self):
        with self.captureOnCommitCallbacks(execute=True):
            svc.request_password_reset(None, self.person.account_id)
        raw = token_from_outbox()
        url = reverse("accounts:reset_password", args=[raw])
        response = self.client.get(url)
        self.assertContains(response, "Choose a new password")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertIsNone(EmailToken.objects.get(user=self.person).used_at)

    def test_bad_link_shows_the_generic_invalid_page(self):
        response = self.client.get(reverse("accounts:reset_password", args=["nope"]))
        self.assertContains(response, "no longer valid")

    def test_post_resets_and_sends_to_login(self):
        with self.captureOnCommitCallbacks(execute=True):
            svc.request_password_reset(None, self.person.account_id)
        raw = token_from_outbox()
        response = self.client.post(
            reverse("accounts:reset_password", args=[raw]),
            {"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
        )
        self.assertRedirects(response, reverse("accounts:login"), fetch_redirect_response=False)
        self.person.refresh_from_db()
        self.assertTrue(self.person.check_password(NEW_PASSWORD))

    def test_mismatch_and_weak_passwords_show_inline_errors(self):
        with self.captureOnCommitCallbacks(execute=True):
            svc.request_password_reset(None, self.person.account_id)
        url = reverse("accounts:reset_password", args=[token_from_outbox()])
        mismatch = self.client.post(url, {"new_password": NEW_PASSWORD, "confirm_password": "different"})
        self.assertContains(mismatch, "do not match")
        weak = self.client.post(url, {"new_password": "123", "confirm_password": "123"})
        self.assertEqual(weak.status_code, 200)
        self.person.refresh_from_db()
        self.assertTrue(self.person.check_password(PASSWORD))   # unchanged
