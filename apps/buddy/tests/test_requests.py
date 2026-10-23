"""Brick 6: sending, accepting, declining and cancelling buddy requests."""
import json

from django.test import TestCase

from apps.accounts.models import PersonType, Status
from apps.audit.models import AuditLog
from apps.core.services import ServiceError
from apps.notifications.models import Notification

from apps.buddy import services
from apps.buddy.models import BuddyRequest, UserBuddy

from .helpers import make_user

PENDING = BuddyRequest.Status.PENDING


class SendRequestTests(TestCase):
    """send_request: who may ask whom, and the limits."""

    def setUp(self):
        self.me = make_user(first="Maria")
        self.other = make_user(first="Josefina")

    def test_send_creates_a_pending_request_and_one_notice(self):
        request = services.send_request(self.me, self.other.pk)
        self.assertEqual(request.status, PENDING)
        self.assertEqual((request.from_user_id, request.to_user_id), (self.me.pk, self.other.pk))
        notice = Notification.objects.get(user=self.other, type=services.BUDDY_REQUEST)
        self.assertEqual(notice.actor_id, self.me.pk)

    def test_cannot_ask_yourself(self):
        with self.assertRaises(ServiceError):
            services.send_request(self.me, self.me.pk)

    def test_guest_and_no_account_targets_are_refused(self):
        for person_type in (PersonType.GUEST, PersonType.MEMBER_RECORD):
            target = make_user(person_type=person_type)
            with self.assertRaises(ServiceError):
                services.send_request(self.me, target.pk)
        self.assertFalse(BuddyRequest.objects.exists())

    def test_suspended_and_archived_targets_are_refused(self):
        for status in (Status.SUSPENDED, Status.ARCHIVED):
            target = make_user(status=status)
            with self.assertRaises(ServiceError):
                services.send_request(self.me, target.pk)

    def test_a_guest_cannot_send(self):
        guest = make_user(person_type=PersonType.GUEST)
        with self.assertRaises(ServiceError):
            services.send_request(guest, self.other.pk)

    def test_a_missing_target_is_refused(self):
        with self.assertRaises(ServiceError):
            services.send_request(self.me, 999999)

    def test_duplicate_pending_in_either_direction_is_refused(self):
        services.send_request(self.me, self.other.pk)
        with self.assertRaises(ServiceError):
            services.send_request(self.me, self.other.pk)          # same direction
        with self.assertRaises(ServiceError):
            services.send_request(self.other, self.me.pk)          # reverse direction
        self.assertEqual(BuddyRequest.objects.filter(status=PENDING).count(), 1)

    def test_at_most_three_pending_requests(self):
        targets = [make_user() for _ in range(4)]
        for target in targets[:3]:
            services.send_request(self.me, target.pk)
        with self.assertRaises(ServiceError):
            services.send_request(self.me, targets[3].pk)
        self.assertEqual(BuddyRequest.objects.filter(from_user=self.me, status=PENDING).count(), 3)

    def test_a_person_with_a_live_buddy_cannot_ask_or_be_asked(self):
        partner = make_user()
        UserBuddy.objects.create(user_a_id=min(self.me.pk, partner.pk), user_b_id=max(self.me.pk, partner.pk))
        with self.assertRaises(ServiceError):
            services.send_request(self.me, self.other.pk)          # I have a buddy
        with self.assertRaises(ServiceError):
            services.send_request(self.other, self.me.pk)          # target has a buddy

    def test_a_declined_request_can_be_sent_again_at_once(self):
        request = services.send_request(self.me, self.other.pk)
        services.decline_request(self.other, request.pk)
        again = services.send_request(self.me, self.other.pk)      # no cooldown
        self.assertEqual(again.status, PENDING)

    def test_an_inactive_partnership_is_ended_when_you_send(self):
        old_partner = make_user(status=Status.SUSPENDED)
        row = UserBuddy.objects.create(
            user_a_id=min(self.me.pk, old_partner.pk), user_b_id=max(self.me.pk, old_partner.pk),
        )
        services.send_request(self.me, self.other.pk)
        row.refresh_from_db()
        self.assertIsNotNone(row.ended_at)
        self.assertIsNone(row.ended_by_id)

    def test_a_target_with_only_an_inactive_partnership_can_be_asked(self):
        gone = make_user(status=Status.SUSPENDED)
        UserBuddy.objects.create(
            user_a_id=min(self.other.pk, gone.pk), user_b_id=max(self.other.pk, gone.pk),
        )
        request = services.send_request(self.me, self.other.pk)
        self.assertEqual(request.status, PENDING)


class AcceptRequestTests(TestCase):
    """accept_request: the partnership, the clean-up of other requests, and the guards."""

    def setUp(self):
        self.sender = make_user(first="Sender")
        self.recipient = make_user(first="Recipient")
        self.request = services.send_request(self.sender, self.recipient.pk)

    def test_accept_creates_a_sorted_pair_whoever_sent_it(self):
        row = services.accept_request(self.recipient, self.request.pk)
        self.assertLess(row.user_a_id, row.user_b_id)
        self.assertEqual({row.user_a_id, row.user_b_id}, {self.sender.pk, self.recipient.pk})
        self.assertIsNone(row.ended_at)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, BuddyRequest.Status.ACCEPTED)
        self.assertIsNotNone(self.request.responded_at)

    def test_pair_is_sorted_when_the_sender_has_the_higher_id(self):
        high, low = sorted([make_user(), make_user()], key=lambda u: -u.pk)
        request = services.send_request(high, low.pk)
        row = services.accept_request(low, request.pk)
        self.assertEqual((row.user_a_id, row.user_b_id), (low.pk, high.pk))

    def test_accept_notifies_the_sender(self):
        services.accept_request(self.recipient, self.request.pk)
        notice = Notification.objects.get(user=self.sender, type=services.BUDDY_ACCEPTED)
        self.assertEqual(notice.actor_id, self.recipient.pk)

    def test_accept_cancels_every_other_pending_request_involving_either_person(self):
        x, y, z, w = (make_user() for _ in range(4))
        involving = [
            services.send_request(x, self.recipient.pk),           # to the recipient
            services.send_request(self.sender, y.pk),              # from the sender
            services.send_request(z, self.sender.pk),              # to the sender
            services.send_request(self.recipient, w.pk),           # from the recipient
        ]
        unrelated = services.send_request(x, y.pk)
        services.accept_request(self.recipient, self.request.pk)
        for request in involving:
            request.refresh_from_db()
            self.assertEqual(request.status, BuddyRequest.Status.CANCELLED)
        unrelated.refresh_from_db()
        self.assertEqual(unrelated.status, PENDING)

    def test_accepting_twice_is_quiet(self):
        first = services.accept_request(self.recipient, self.request.pk)
        audit_count = AuditLog.objects.filter(action="buddy.accept").count()
        second = services.accept_request(self.recipient, self.request.pk)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(UserBuddy.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="buddy.accept").count(), audit_count)

    def test_only_the_recipient_may_accept(self):
        stranger = make_user()
        for actor in (stranger, self.sender):
            with self.assertRaises(ServiceError):
                services.accept_request(actor, self.request.pk)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, PENDING)
        self.assertFalse(UserBuddy.objects.exists())

    def test_accepting_a_missing_request_is_refused(self):
        with self.assertRaises(ServiceError):
            services.accept_request(self.recipient, 999999)

    def test_accept_is_refused_when_someone_got_a_live_buddy_meanwhile(self):
        busy_partner = make_user()
        UserBuddy.objects.create(
            user_a_id=min(self.sender.pk, busy_partner.pk), user_b_id=max(self.sender.pk, busy_partner.pk),
        )
        with self.assertRaises(ServiceError):
            services.accept_request(self.recipient, self.request.pk)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, PENDING)
        self.assertEqual(UserBuddy.objects.count(), 1)

    def test_accept_is_refused_when_the_sender_can_no_longer_sign_in(self):
        self.sender.status = Status.SUSPENDED
        self.sender.save(update_fields=["status"])
        with self.assertRaises(ServiceError):
            services.accept_request(self.recipient, self.request.pk)
        self.assertFalse(UserBuddy.objects.exists())

    def test_a_cancelled_or_declined_request_cannot_be_accepted(self):
        services.cancel_request(self.sender, self.request.pk)
        with self.assertRaises(ServiceError):
            services.accept_request(self.recipient, self.request.pk)

    def test_an_inactive_partnership_is_ended_when_you_accept(self):
        gone = make_user(status=Status.SUSPENDED)
        old = UserBuddy.objects.create(
            user_a_id=min(self.recipient.pk, gone.pk), user_b_id=max(self.recipient.pk, gone.pk),
        )
        services.accept_request(self.recipient, self.request.pk)
        old.refresh_from_db()
        self.assertIsNotNone(old.ended_at)
        self.assertEqual(UserBuddy.objects.filter(ended_at__isnull=True).count(), 1)

    def test_a_person_with_an_inactive_partner_who_returns_is_live_again(self):
        gone = make_user(status=Status.SUSPENDED)
        row = UserBuddy.objects.create(
            user_a_id=min(self.recipient.pk, gone.pk), user_b_id=max(self.recipient.pk, gone.pk),
        )
        self.assertFalse(services.is_live(UserBuddy.objects.get(pk=row.pk)))
        gone.status = Status.ACTIVE
        gone.save(update_fields=["status"])
        self.assertTrue(services.is_live(UserBuddy.objects.get(pk=row.pk)))


class DeclineCancelTests(TestCase):
    """decline_request (recipient) and cancel_request (sender)."""

    def setUp(self):
        self.sender = make_user()
        self.recipient = make_user()
        self.request = services.send_request(self.sender, self.recipient.pk)

    def test_decline(self):
        services.decline_request(self.recipient, self.request.pk)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, BuddyRequest.Status.DECLINED)

    def test_cancel(self):
        services.cancel_request(self.sender, self.request.pk)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, BuddyRequest.Status.CANCELLED)

    def test_only_the_right_side_may_close_a_request(self):
        stranger = make_user()
        with self.assertRaises(ServiceError):
            services.decline_request(self.sender, self.request.pk)     # sender cannot decline
        with self.assertRaises(ServiceError):
            services.cancel_request(self.recipient, self.request.pk)   # recipient cannot cancel
        with self.assertRaises(ServiceError):
            services.decline_request(stranger, self.request.pk)
        with self.assertRaises(ServiceError):
            services.cancel_request(stranger, self.request.pk)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, PENDING)

    def test_repeating_is_quiet_and_a_closed_request_cannot_flip(self):
        services.decline_request(self.recipient, self.request.pk)
        services.decline_request(self.recipient, self.request.pk)      # quiet
        self.assertEqual(AuditLog.objects.filter(action="buddy.decline").count(), 1)
        with self.assertRaises(ServiceError):
            services.cancel_request(self.sender, self.request.pk)      # already declined


class PrivacyTests(TestCase):
    """Audit rows and notices hold ids only (AGENTS.md privacy rules)."""

    def test_audit_and_notification_rows_hold_no_names(self):
        sender = make_user(first="Zebedee", last="Quillfeather")
        recipient = make_user(first="Ophelia", last="Brambleton")
        request = services.send_request(sender, recipient.pk)
        services.accept_request(recipient, request.pk)

        actions = {"buddy.request", "buddy.accept"}
        rows = AuditLog.objects.filter(action__in=actions)
        self.assertEqual({row.action for row in rows}, actions)
        blob = json.dumps(
            [[row.before, row.after, row.entity_label] for row in rows]
            + [n.data for n in Notification.objects.all()]
        ).lower()
        for name in ("zebedee", "quillfeather", "ophelia", "brambleton"):
            self.assertNotIn(name, blob)

    def test_each_action_is_audited_with_the_documented_name(self):
        a, b, c, d = (make_user() for _ in range(4))
        services.cancel_request(a, services.send_request(a, b.pk).pk)
        services.decline_request(d, services.send_request(c, d.pk).pk)
        names = set(AuditLog.objects.values_list("action", flat=True))
        self.assertTrue({"buddy.request", "buddy.cancel", "buddy.decline"} <= names)
