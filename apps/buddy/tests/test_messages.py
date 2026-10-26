"""Brick 8 tests: messages, nudges, prayed taps, the thread and the push preview.

Only the services and the small JSON endpoints are tested here. Push sending is mocked (the tests
never call outside services).
"""
import json
import uuid
from datetime import timedelta
from itertools import count
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Extension, PersonType, Role
from apps.audit.models import AuditLog  # VERIFY: model name of the audit table
from apps.core.services import ServiceError
from apps.notifications.models import Notification
from apps.buddy import messaging
from apps.buddy.models import BuddyNote, UserBuddy

User = get_user_model()


# ---------------------------------------------------------------- helpers
_counter = count(1)


def make_extension():
    """An extension (Extension.name is unique, so each one gets a number)."""
    return Extension.objects.create(name=f"Buddy test extension {next(_counter)}")


def make_user(name):
    """A member who can sign in. Same recipe as the faith test helpers: no email, blank account_id
    (User.save() generates it), and an extension because a member must belong to one."""
    return User.objects.create_user(
        password="pass-12345-xyz", account_id="", first_name=name.title(), last_name="Test",
        role=Role.MEMBER, person_type=PersonType.MEMBER_ACCOUNT, must_change_password=False,
        extension=make_extension(),
    )


def pair(one, two, **extra):
    """An open partnership with the ids sorted (user_a < user_b)."""
    a, b = sorted([one, two], key=lambda u: u.pk)
    return UserBuddy.objects.create(user_a=a, user_b=b, **extra)


def end(row, by=None, final_note=None, partner_final_note=None):
    """End a partnership row directly (the end flow itself is Brick 7)."""
    row.ended_at = timezone.now()
    row.ended_by = by
    row.final_note = final_note
    row.partner_final_note = partner_final_note
    row.save()
    return row


def push_user_ids(mock_send):
    """The people every mocked push went to."""
    return [uid for call in mock_send.call_args_list for uid in call.args[0]]


class MessagingBase(TestCase):
    """Two people who are partners, plus one stranger."""

    def setUp(self):
        self.ann = make_user("ann")
        self.ben = make_user("ben")
        self.cy = make_user("cy")
        self.row = pair(self.ann, self.ben)

    def token(self):
        """A fresh one-time token for each call."""
        return uuid.uuid4().hex


# ---------------------------------------------------------------- sending
class SendMessageTests(MessagingBase):
    def test_send_creates_a_message(self):
        note = messaging.send_message(self.ann, "  Hello there  ", self.token())
        self.assertEqual((note.kind, note.body, note.sender), ("message", "Hello there", self.ann))
        self.assertIsNone(note.read_at)

    def test_empty_and_blank_bodies_refused(self):
        for body in ("", "   \n  ", None):
            with self.assertRaises(ServiceError):
                messaging.send_message(self.ann, body, self.token())
        self.assertEqual(BuddyNote.objects.count(), 0)

    def test_length_limit(self):
        messaging.send_message(self.ann, "x" * 1000, self.token())
        with self.assertRaises(ServiceError):
            messaging.send_message(self.ann, "x" * 1001, self.token())
        self.assertEqual(BuddyNote.objects.count(), 1)

    def test_nul_characters_are_dropped(self):
        note = messaging.send_message(self.ann, "hi\x00 there", self.token())
        self.assertEqual(note.body, "hi there")

    def test_replayed_token_does_nothing(self):
        token = self.token()
        self.assertIsNotNone(messaging.send_message(self.ann, "once", token))
        self.assertIsNone(messaging.send_message(self.ann, "once", token))
        self.assertEqual(BuddyNote.objects.count(), 1)

    def test_refused_message_does_not_burn_the_token(self):
        token = self.token()
        with self.assertRaises(ServiceError):
            messaging.send_message(self.ann, "", token)
        self.assertIsNotNone(messaging.send_message(self.ann, "now it works", token))

    def test_no_partner_refused(self):
        with self.assertRaises(ServiceError):
            messaging.send_message(self.cy, "hello?", self.token())

    def test_ended_partnership_refused(self):
        end(self.row, by=self.ann)
        with self.assertRaises(ServiceError):
            messaging.send_message(self.ann, "too late", self.token())

    def test_inactive_partner_makes_the_thread_read_only(self):
        from apps.accounts.models import Status
        self.ben.status = Status.SUSPENDED
        self.ben.save()
        for write in (
            lambda: messaging.send_message(self.ann, "hi", self.token()),
            lambda: messaging.send_nudge(self.ann),
            lambda: messaging.send_prayed(self.ann),
        ):
            with self.assertRaises(ServiceError):
                write()
        data = messaging.thread_for(self.ann)
        self.assertTrue(data["active"])
        self.assertTrue(data["read_only"])


class NudgeAndPrayedTests(MessagingBase):
    def test_nudge_has_no_body_and_is_limited_to_one_per_hour(self):
        note = messaging.send_nudge(self.ann)
        self.assertEqual((note.kind, note.body), ("nudge", None))
        with self.assertRaises(ServiceError):
            messaging.send_nudge(self.ann)
        BuddyNote.objects.filter(pk=note.pk).update(created_at=timezone.now() - timedelta(minutes=61))
        self.assertEqual(messaging.send_nudge(self.ann).kind, "nudge")

    def test_nudge_limit_is_per_person(self):
        messaging.send_nudge(self.ann)
        self.assertEqual(messaging.send_nudge(self.ben).kind, "nudge")

    def test_prayed_is_limited_to_one_per_day(self):
        note = messaging.send_prayed(self.ann)
        self.assertEqual((note.kind, note.body), ("prayed", None))
        with self.assertRaises(ServiceError):
            messaging.send_prayed(self.ann)
        BuddyNote.objects.filter(pk=note.pk).update(created_at=timezone.now() - timedelta(days=1, hours=1))
        self.assertEqual(messaging.send_prayed(self.ann).kind, "prayed")

    def test_limits_for_reports_the_state(self):
        self.assertEqual(messaging.limits_for(self.ann), {"nudge_wait": 0, "prayed_today": False})
        messaging.send_nudge(self.ann)
        messaging.send_prayed(self.ann)
        state = messaging.limits_for(self.ann)
        self.assertGreater(state["nudge_wait"], 3500)
        self.assertTrue(state["prayed_today"])


# ---------------------------------------------------------------- reading
class ReadTests(MessagingBase):
    def test_mark_read_only_touches_the_partners_notes(self):
        mine = messaging.send_message(self.ann, "from ann", self.token())
        theirs = messaging.send_message(self.ben, "from ben", self.token())
        self.assertEqual(messaging.unread_count(self.ann), 1)
        self.assertEqual(messaging.mark_read(self.ann), 1)
        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertIsNone(mine.read_at)
        self.assertIsNotNone(theirs.read_at)
        self.assertEqual(messaging.unread_count(self.ann), 0)
        self.assertEqual(messaging.mark_read(self.ann), 0)       # nothing left: quiet

    def test_seen_state_follows_the_partner_reading(self):
        note = messaging.send_message(self.ann, "hello", self.token())
        self.assertEqual(messaging.seen_state(self.ann), {"last_mine_id": note.pk, "last_mine_seen": False})
        messaging.mark_read(self.ben)
        self.assertEqual(messaging.seen_state(self.ann), {"last_mine_id": note.pk, "last_mine_seen": True})

    def test_a_stranger_cannot_mark_anything_read(self):
        messaging.send_message(self.ann, "private", self.token())
        self.assertEqual(messaging.mark_read(self.cy), 0)
        self.assertEqual(BuddyNote.objects.filter(read_at__isnull=False).count(), 0)


class ThreadTests(MessagingBase):
    def test_unpaired_person_sees_nothing(self):
        messaging.send_message(self.ann, "secret", self.token())
        data = messaging.thread_for(self.cy)
        self.assertFalse(data["active"])
        self.assertEqual(data["items"], [])

    def test_a_different_partner_never_shows_this_thread(self):
        messaging.send_message(self.ann, "for ben only", self.token())
        end(self.row, by=self.ann)
        pair(self.ann, self.cy)
        bodies = [i["body"] for i in messaging.thread_for(self.ann)["items"] if i["type"] == "note"]
        self.assertEqual(bodies, [])
        self.assertEqual(messaging.thread_for(self.cy)["items"], [])

    def test_items_are_oldest_first_and_flag_my_notes(self):
        messaging.send_message(self.ann, "one", self.token())
        messaging.send_message(self.ben, "two", self.token())
        items = messaging.thread_for(self.ann)["items"]
        self.assertEqual([(i["body"], i["mine"]) for i in items], [("one", True), ("two", False)])

    def test_paging_with_before(self):
        for n in range(7):
            messaging.send_message(self.ann, f"m{n}", self.token())
        page = messaging.thread_for(self.ann, limit=3)
        self.assertEqual([i["body"] for i in page["items"]], ["m4", "m5", "m6"])
        self.assertTrue(page["has_more"])
        older = messaging.thread_for(self.ann, before=page["oldest_id"], limit=3)
        self.assertEqual([i["body"] for i in older["items"]], ["m1", "m2", "m3"])
        oldest = messaging.thread_for(self.ann, before=older["oldest_id"], limit=3)
        self.assertEqual([i["body"] for i in oldest["items"]], ["m0"])
        self.assertFalse(oldest["has_more"])

    def test_before_must_belong_to_this_pair(self):
        other = pair(self.cy, make_user("dee"))
        foreign = BuddyNote.objects.create(buddy=other, sender=self.cy, kind="message", body="not yours")
        data = messaging.thread_for(self.ann, before=foreign.pk + 1)
        self.assertEqual(data["items"], [])
        data = messaging.thread_for(self.ann, before=foreign.pk)
        self.assertEqual(data["items"], [])

    def test_same_pair_merges_periods_with_a_divider_and_final_notes(self):
        messaging.send_message(self.ann, "first period", self.token())
        end(self.row, by=self.ann, final_note="Thanks for walking with me", partner_final_note="You too")
        second = pair(self.ann, self.ben)
        BuddyNote.objects.create(buddy=second, sender=self.ben, kind="message", body="second period")

        items = messaging.thread_for(self.ann)["items"]
        self.assertEqual([i["type"] for i in items], ["note", "divider", "note"])
        divider = items[1]
        self.assertEqual(
            [(n["name"], n["text"]) for n in divider["notes"]],
            [("You", "Thanks for walking with me"), ("Ben Test", "You too")],
        )
        # Ben sees the same thread with the names turned around.
        ben_divider = [i for i in messaging.thread_for(self.ben)["items"] if i["type"] == "divider"][0]
        self.assertEqual([n["name"] for n in ben_divider["notes"]], ["Ann Test", "You"])
        # A stranger sees none of it.
        self.assertEqual(messaging.thread_for(self.cy)["items"], [])

    def test_ended_period_with_no_new_notes_still_shows_its_divider(self):
        messaging.send_message(self.ann, "old", self.token())
        end(self.row, by=self.ben, final_note="bye")
        pair(self.ann, self.ben)
        types = [i["type"] for i in messaging.thread_for(self.ann)["items"]]
        self.assertEqual(types, ["note", "divider"])


class PollTests(MessagingBase):
    def test_poll_returns_only_newer_notes(self):
        first = messaging.send_message(self.ann, "one", self.token())
        second = messaging.send_message(self.ben, "two", self.token())
        data = messaging.poll_since(self.ann, first.pk)
        self.assertTrue(data["active"])
        self.assertEqual([n["id"] for n in data["notes"]], [second.pk])

    def test_poll_gives_a_stranger_nothing(self):
        messaging.send_message(self.ann, "one", self.token())
        data = messaging.poll_since(self.cy, 0)
        self.assertFalse(data["active"])
        self.assertEqual(data["notes"], [])

    def test_poll_goes_inactive_when_the_partnership_ends(self):
        end(self.row, by=self.ann)
        self.assertFalse(messaging.poll_since(self.ann, 0)["active"])


# ---------------------------------------------------------------- push, bell, audit
@override_settings(PUSH_ASYNC=False)
class PrivacyTests(MessagingBase):
    def send_with_push(self, action):
        """Run `action` with push switched on and the sender mocked; returns the mock."""
        with mock.patch("apps.notifications.push.is_enabled", return_value=True), \
                mock.patch("apps.notifications.push.send_to_users") as send, \
                self.captureOnCommitCallbacks(execute=True):
            action()
        return send

    def test_push_preview_is_cut_and_goes_to_the_partner_only(self):
        long_text = "Praying for your exam this week, please remember " + "x" * 200
        send = self.send_with_push(lambda: messaging.send_message(self.ann, long_text, self.token()))
        self.assertEqual(push_user_ids(send), [self.ben.pk])
        message = send.call_args.args[1]
        self.assertEqual(message["title"], "Ann Test")
        self.assertTrue(message["body"].startswith("Praying for your exam"))
        self.assertLessEqual(len(message["body"]), messaging.PREVIEW_CHARS + 1)
        self.assertEqual(message["url"], reverse("buddy:messages"))

    def test_nudge_and_prayed_use_fixed_phrases(self):
        send = self.send_with_push(lambda: messaging.send_nudge(self.ann))
        self.assertEqual(send.call_args.args[1]["body"], "👋 nudged you")
        messaging.mark_read(self.ben)          # a new unread burst starts after the thread is read
        send = self.send_with_push(lambda: messaging.send_prayed(self.ann))
        self.assertEqual(send.call_args.args[1]["body"], "🙏 prayed for you")

    def test_one_push_per_unread_burst_then_again_after_reading(self):
        send = self.send_with_push(lambda: (
            messaging.send_message(self.ann, "one", self.token()),
            messaging.send_message(self.ann, "two", self.token()),
            messaging.send_message(self.ann, "three", self.token()),
        ))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(Notification.objects.filter(user=self.ben, type=messaging.BUDDY_MESSAGE).count(), 1)

        messaging.mark_read(self.ben)
        send = self.send_with_push(lambda: messaging.send_message(self.ann, "four", self.token()))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(Notification.objects.filter(user=self.ben, type=messaging.BUDDY_MESSAGE).count(), 2)

    def test_notification_data_holds_ids_only(self):
        messaging.send_message(self.ann, "TOP SECRET WORDS", self.token())
        messaging.send_nudge(self.ann)
        for item in Notification.objects.filter(user=self.ben):
            blob = json.dumps(item.data)
            self.assertNotIn("TOP SECRET", blob)
            self.assertEqual(set(item.data), {"kind", "sender_id"})

    def test_nothing_is_written_to_the_audit_log(self):
        before = AuditLog.objects.count()
        messaging.send_message(self.ann, "no audit please", self.token())
        messaging.send_nudge(self.ann)
        messaging.send_prayed(self.ann)
        messaging.mark_read(self.ben)
        self.assertEqual(AuditLog.objects.count(), before)


# ---------------------------------------------------------------- the small endpoints
class EndpointTests(MessagingBase):
    def post(self, name, user, **data):
        self.client.force_login(user)
        return self.client.post(reverse(name), data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    def test_login_required(self):
        for name in ("buddy:messages", "buddy:message_poll"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
        self.assertEqual(self.client.post(reverse("buddy:message_send"), {}).status_code, 302)

    def test_the_buddy_page_draws_the_chat_for_a_partner_but_not_for_an_unpaired_person(self):
        self.client.force_login(self.ann)
        self.assertContains(self.client.get(reverse("buddy:home")), "buddy-chat-data")
        self.client.force_login(self.cy)
        self.assertNotContains(self.client.get(reverse("buddy:home")), "buddy-chat-data")

    def test_the_old_messages_address_redirects_to_the_buddy_page(self):
        self.client.force_login(self.ann)
        response = self.client.get(reverse("buddy:messages"))
        self.assertRedirects(response, reverse("buddy:home"), fetch_redirect_response=False)

    def test_get_never_writes(self):
        messaging.send_message(self.ben, "unread", self.token())
        self.client.force_login(self.ann)
        self.client.get(reverse("buddy:home"))
        self.client.get(reverse("buddy:message_poll"), {"after": 0})
        self.assertEqual(BuddyNote.objects.filter(read_at__isnull=False).count(), 0)

    def test_send_replies_with_the_note_and_a_new_token(self):
        reply = self.post("buddy:message_send", self.ann, body="hi ben", token=self.token())
        data = reply.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["note"]["body"], "hi ben")
        self.assertTrue(data["token"])

    def test_error_is_a_400_with_a_message(self):
        reply = self.post("buddy:message_send", self.cy, body="hello", token=self.token())
        self.assertEqual(reply.status_code, 400)
        self.assertFalse(reply.json()["ok"])

    def test_poll_and_read_only_work_on_my_own_partnership(self):
        messaging.send_message(self.ben, "from ben", self.token())
        self.client.force_login(self.cy)
        self.assertEqual(self.client.get(reverse("buddy:message_poll"), {"after": 0}).json()["notes"], [])
        self.assertEqual(self.client.post(reverse("buddy:message_read")).json()["marked"], 0)
        self.client.force_login(self.ann)
        self.assertEqual(len(self.client.get(reverse("buddy:message_poll"), {"after": 0}).json()["notes"]), 1)
        self.assertEqual(self.client.post(reverse("buddy:message_read")).json()["marked"], 1)

    def test_earlier_needs_a_position(self):
        self.client.force_login(self.ann)
        self.assertEqual(self.client.get(reverse("buddy:message_earlier")).status_code, 400)