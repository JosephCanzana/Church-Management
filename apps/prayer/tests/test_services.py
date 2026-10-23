"""Prayer services: send, anonymous flag, daily cap, who may send, reply, complete, tokens, privacy of logs."""
import json

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import PersonType
from apps.audit.models import AuditLog  # (verify) model name and module
from apps.core.services import ServiceError, new_submission_token
from apps.notifications.models import Notification
from apps.prayer import services
from apps.prayer.models import PrayerReply, PrayerRequest

from .helpers import make_extension, make_user

SECRET = "please-pray-for-my-secret-matter"
REPLY = "reply-text-that-must-stay-private"


def _everything_logged():
    """Audit before/after/entity_label and notification data as one string (same scope as the tithes `assert_no_leak`).

    `actor_label` is left out: `log_action` fills it in by itself for every action.
    """
    return json.dumps(
        [[row.before, row.after, row.entity_label] for row in AuditLog.objects.all()], default=str,
    ) + json.dumps([row.data for row in Notification.objects.all()], default=str)


class SendTests(TestCase):
    """send_prayer_request."""

    def setUp(self):
        self.ext = make_extension()
        self.member = make_user("member", self.ext, last_name="Zebediah")
        self.coordinator = make_user("coordinator", self.ext)

    def send(self, actor=None, body=SECRET, anonymous=False, token=None):
        """Send with a fresh token unless one is given."""
        return services.send_prayer_request(actor or self.member, body, anonymous, token or new_submission_token())

    def test_send_creates_request_for_senders_extension(self):
        result = self.send()
        row = result.request
        self.assertFalse(result.duplicate)
        self.assertEqual((row.user, row.extension, row.status), (self.member, self.ext, "open"))
        self.assertIsNone(row.coordinator)

    def test_anonymous_flag_is_stored(self):
        self.assertTrue(self.send(anonymous=True).request.is_anonymous)

    def test_empty_and_too_long_refused(self):
        with self.assertRaises(ServiceError):
            self.send(body="   ")
        with self.assertRaises(ServiceError):
            self.send(body="x" * (services.MAX_BODY + 1))
        self.assertEqual(PrayerRequest.objects.count(), 0)

    def test_daily_cap(self):
        for _ in range(services.MAX_PER_DAY):
            self.send()
        with self.assertRaises(ServiceError):
            self.send()
        self.assertEqual(PrayerRequest.objects.count(), services.MAX_PER_DAY)

    def test_cap_is_per_person(self):
        other = make_user("member", self.ext)
        for _ in range(services.MAX_PER_DAY):
            self.send()
        self.assertFalse(self.send(actor=other).duplicate)

    def test_refused_for_non_members_and_cannot_sign_in(self):
        from django.core.exceptions import PermissionDenied
        for actor in (self.coordinator, make_user("admin"), make_user("super_admin")):
            with self.assertRaises(PermissionDenied):
                self.send(actor=actor)
        # The database forbids a member without an extension, so check the rule on an unsaved copy.
        from apps.prayer import permissions
        self.member.extension = None
        self.assertFalse(permissions.can_send(self.member))
        self.member.extension = self.ext
        guest = make_user("member", self.ext, person_type=PersonType.GUEST)
        with self.assertRaises(PermissionDenied):
            self.send(actor=guest)
        self.assertEqual(PrayerRequest.objects.count(), 0)

    def test_same_token_twice_sends_once(self):
        token = new_submission_token()
        first = self.send(token=token)
        second = self.send(token=token)
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(PrayerRequest.objects.count(), 1)

    def test_coordinator_is_notified_without_text_or_name(self):
        self.send()
        note = Notification.objects.get(type=services.NOTIFY_REQUEST)
        self.assertEqual(note.user_id, self.coordinator.pk)

    def test_admins_are_notified_when_extension_has_no_coordinator(self):
        lonely = make_extension()
        member = make_user("member", lonely)
        admin = make_user("admin")
        services.send_prayer_request(member, SECRET, False, new_submission_token())
        self.assertTrue(Notification.objects.filter(user=admin, type=services.NOTIFY_REQUEST).exists())

    def test_audit_and_notifications_hold_no_text_or_name(self):
        self.send(anonymous=True)
        blob = _everything_logged()
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("Zebediah", blob)
        self.assertTrue(AuditLog.objects.filter(action="prayer.send").exists())


class HandleTests(TestCase):
    """reply_to_request and complete_request."""

    def setUp(self):
        self.ext = make_extension()
        self.member = make_user("member", self.ext, last_name="Zebediah")
        self.coordinator = make_user("coordinator", self.ext)
        self.request = services.send_prayer_request(self.member, SECRET, False, new_submission_token()).request

    def test_reply_moves_status_and_notifies_sender(self):
        services.reply_to_request(self.coordinator, self.request.pk, REPLY)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "replied")
        self.assertEqual(PrayerReply.objects.filter(prayer_request=self.request).count(), 1)
        self.assertTrue(Notification.objects.filter(user=self.member, type=services.NOTIFY_REPLIED).exists())

    def test_reply_after_completed_keeps_completed(self):
        services.complete_request(self.coordinator, self.request.pk)
        services.reply_to_request(self.coordinator, self.request.pk, REPLY)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "completed")

    def test_complete_sets_time_and_is_idempotent(self):
        self.assertTrue(services.complete_request(self.coordinator, self.request.pk))
        self.request.refresh_from_db()
        first = self.request.completed_at
        self.assertIsNotNone(first)
        self.assertFalse(services.complete_request(self.coordinator, self.request.pk))
        self.request.refresh_from_db()
        self.assertEqual(self.request.completed_at, first)

    def test_out_of_reach_actor_gets_not_found(self):
        other_coordinator = make_user("coordinator", make_extension())
        with self.assertRaises(ServiceError):
            services.reply_to_request(other_coordinator, self.request.pk, REPLY)
        with self.assertRaises(ServiceError):
            services.complete_request(self.member, self.request.pk)
        with self.assertRaises(ServiceError):
            services.complete_request(self.coordinator, 999999)

    def test_admin_cannot_handle_request_of_extension_with_coordinator(self):
        with self.assertRaises(ServiceError):
            services.reply_to_request(make_user("admin"), self.request.pk, REPLY)

    def test_audit_and_notifications_hold_no_text_or_name(self):
        services.reply_to_request(self.coordinator, self.request.pk, REPLY)
        services.complete_request(self.coordinator, self.request.pk)
        blob = _everything_logged()
        for text in (SECRET, REPLY, "Zebediah"):
            self.assertNotIn(text, blob)
        for action in ("prayer.reply", "prayer.complete"):
            self.assertTrue(AuditLog.objects.filter(action=action).exists())
