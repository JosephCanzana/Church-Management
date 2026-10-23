"""Brick 7: ending a partnership (two steps, inactive partner, stale-request job)."""
import json
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Status, User
from apps.audit.models import AuditLog
from apps.core.services import ServiceError
from apps.notifications.models import Notification

from apps.buddy import services
from apps.buddy.models import UserBuddy

from .helpers import make_user

NOTE_A = "Thank you for walking with me, A-SECRET-NOTE"
NOTE_B = "Take care too, B-SECRET-NOTE"


def pair(first, second):
    """Make two people partners through the real services and return the UserBuddy row."""
    request = services.send_request(first, second.pk)
    return services.accept_request(second, request.pk)


class EndFlowTests(TestCase):
    """request_end, cancel_end_request and agree_end."""

    def setUp(self):
        self.a = make_user(first="Maria")
        self.b = make_user(first="Josefina")
        self.row = pair(self.a, self.b)

    def reload(self):
        return UserBuddy.objects.get(pk=self.row.pk)

    def test_full_two_step_flow_stores_both_notes(self):
        services.request_end(self.a, NOTE_A)
        pending = self.reload()
        self.assertIsNone(pending.ended_at)
        self.assertEqual(pending.end_requested_by_id, self.a.pk)
        self.assertTrue(Notification.objects.filter(user=self.b, type=services.BUDDY_END_REQUESTED).exists())

        services.agree_end(self.b, NOTE_B)
        done = self.reload()
        self.assertIsNotNone(done.ended_at)
        self.assertEqual(done.ended_by_id, self.a.pk)
        self.assertEqual(done.final_note, NOTE_A)
        self.assertEqual(done.partner_final_note, NOTE_B)
        self.assertTrue(Notification.objects.filter(user=self.a, type=services.BUDDY_ENDED).exists())

    def test_notes_are_optional_and_trimmed(self):
        services.request_end(self.a, "   ")
        services.agree_end(self.b, None)
        done = self.reload()
        self.assertIsNone(done.final_note)
        self.assertIsNone(done.partner_final_note)

    def test_a_note_over_500_characters_is_refused(self):
        with self.assertRaises(ServiceError):
            services.request_end(self.a, "x" * 501)
        self.assertIsNone(self.reload().end_requested_at)
        services.request_end(self.a, "x" * 500)

    def test_a_repeat_request_is_quiet(self):
        services.request_end(self.a, NOTE_A)
        services.request_end(self.a, "a different note")
        services.request_end(self.b, "the partner also taps it")
        row = self.reload()
        self.assertEqual(row.end_requested_by_id, self.a.pk)
        self.assertEqual(row.end_request_note, NOTE_A)
        self.assertEqual(Notification.objects.filter(type=services.BUDDY_END_REQUESTED).count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="buddy.end_request").count(), 1)

    def test_the_requester_can_cancel_and_ask_again(self):
        services.request_end(self.a, NOTE_A)
        services.cancel_end_request(self.a)
        row = self.reload()
        self.assertIsNone(row.end_requested_by_id)
        self.assertIsNone(row.end_requested_at)
        self.assertIsNone(row.end_request_note)
        services.cancel_end_request(self.a)          # nothing pending: quiet
        services.request_end(self.a, None)
        self.assertEqual(Notification.objects.filter(user=self.b, type=services.BUDDY_END_REQUESTED).count(), 2)

    def test_only_the_requester_can_cancel(self):
        services.request_end(self.a, None)
        with self.assertRaises(ServiceError):
            services.cancel_end_request(self.b)
        self.assertIsNotNone(self.reload().end_requested_at)

    def test_the_requester_cannot_agree_and_agree_needs_a_request(self):
        with self.assertRaises(ServiceError):
            services.agree_end(self.b, None)         # nobody asked yet
        services.request_end(self.a, None)
        with self.assertRaises(ServiceError):
            services.agree_end(self.a, None)         # you cannot agree to your own request
        self.assertIsNone(self.reload().ended_at)

    def test_an_outsider_is_refused_everywhere(self):
        outsider = make_user(first="Carla")
        services.request_end(self.a, None)
        for action in (services.request_end, services.agree_end, services.end_without_approval):
            with self.assertRaises(ServiceError):
                action(outsider, None)
        with self.assertRaises(ServiceError):
            services.cancel_end_request(outsider)
        self.assertIsNone(self.reload().ended_at)

    def test_a_live_partner_cannot_be_force_ended(self):
        with self.assertRaises(ServiceError):
            services.end_without_approval(self.a, "let me out")
        self.assertIsNone(self.reload().ended_at)

    def test_after_ending_both_can_pair_again_with_anyone_including_each_other(self):
        services.request_end(self.a, None)
        services.agree_end(self.b, None)
        # each person can ask someone new...
        self.assertEqual(services.send_request(self.a, make_user(first="Dora").pk).status, "pending")
        self.assertEqual(services.send_request(self.b, make_user(first="Elena").pk).status, "pending")
        # ...and the same two people can pair again, as a new partnership row
        again = pair(self.b, self.a)
        self.assertNotEqual(again.pk, self.row.pk)

    def test_no_note_text_reaches_audit_or_notifications(self):
        services.request_end(self.a, NOTE_A)
        services.agree_end(self.b, NOTE_B)
        audit = json.dumps(list(AuditLog.objects.values_list("before", "after")), default=str)
        notices = json.dumps(list(Notification.objects.values_list("data", flat=True)), default=str)
        for secret in ("A-SECRET-NOTE", "B-SECRET-NOTE"):
            self.assertNotIn(secret, audit)
            self.assertNotIn(secret, notices)

    def test_state_modes(self):
        self.assertEqual(services.partnership_state(self.a)["mode"], "live")
        services.request_end(self.a, NOTE_A)
        self.assertEqual(services.partnership_state(self.a)["mode"], "waiting")
        asked = services.partnership_state(self.b)
        self.assertEqual(asked["mode"], "asked")
        self.assertEqual(asked["request_note"], NOTE_A)
        self.assertIsNone(services.partnership_state(self.a)["request_note"])
        self.assertIsNone(services.partnership_state(make_user(first="Nobody")))


class InactivePartnerTests(TestCase):
    """A partner who can no longer sign in: end without approval, or come back."""

    def setUp(self):
        self.a = make_user(first="Maria")
        self.b = make_user(first="Josefina")
        self.row = pair(self.a, self.b)

    def suspend_b(self):
        User.objects.filter(pk=self.b.pk).update(status=Status.SUSPENDED)

    def test_the_other_person_can_end_it_without_approval(self):
        self.suspend_b()
        self.assertEqual(services.partnership_state(self.a)["mode"], "inactive")
        services.end_without_approval(self.a, NOTE_A)
        row = UserBuddy.objects.get(pk=self.row.pk)
        self.assertIsNotNone(row.ended_at)
        self.assertEqual(row.ended_by_id, self.a.pk)
        self.assertEqual(row.final_note, NOTE_A)
        self.assertIsNone(row.partner_final_note)
        self.assertFalse(Notification.objects.filter(type=services.BUDDY_ENDED).exists())

    def test_asking_to_end_needs_a_live_partner(self):
        self.suspend_b()
        with self.assertRaises(ServiceError):
            services.request_end(self.a, None)

    def test_a_partner_who_comes_back_makes_it_live_again(self):
        self.suspend_b()
        User.objects.filter(pk=self.b.pk).update(status=Status.ACTIVE)
        self.assertEqual(services.partnership_state(self.a)["mode"], "live")
        with self.assertRaises(ServiceError):
            services.end_without_approval(self.a, None)

    def test_the_requester_can_still_cancel_after_the_partner_goes_inactive(self):
        services.request_end(self.a, None)
        self.suspend_b()
        services.cancel_end_request(self.a)
        self.assertIsNone(UserBuddy.objects.get(pk=self.row.pk).end_requested_at)


class StaleRequestJobTests(TestCase):
    """close_stale_end_requests: the 7-day auto-close."""

    def setUp(self):
        self.a = make_user(first="Maria")
        self.b = make_user(first="Josefina")
        self.row = pair(self.a, self.b)
        services.request_end(self.a, NOTE_A)

    def age(self, days):
        UserBuddy.objects.filter(pk=self.row.pk).update(end_requested_at=timezone.now() - timedelta(days=days))

    def test_an_old_request_closes_in_the_requesters_favour(self):
        self.age(8)
        self.assertEqual(services.close_stale_end_requests(days=7), 1)
        row = UserBuddy.objects.get(pk=self.row.pk)
        self.assertIsNotNone(row.ended_at)
        self.assertEqual(row.ended_by_id, self.a.pk)
        self.assertEqual(row.final_note, NOTE_A)
        self.assertIsNone(row.partner_final_note)

    def test_a_fresh_request_stays(self):
        self.age(6)
        self.assertEqual(services.close_stale_end_requests(days=7), 0)
        self.assertIsNone(UserBuddy.objects.get(pk=self.row.pk).ended_at)

    def test_the_setting_decides_how_long_to_wait(self):
        self.age(4)
        with mock.patch.object(services, "end_request_days", return_value=3):
            self.assertEqual(services.close_stale_end_requests(), 1)

    def test_running_twice_is_safe(self):
        self.age(9)
        self.assertEqual(services.close_stale_end_requests(days=7), 1)
        self.assertEqual(services.close_stale_end_requests(days=7), 0)
        self.assertEqual(AuditLog.objects.filter(action="buddy.end").count(), 1)

    def test_it_logs_as_a_system_job_without_note_text(self):
        self.age(9)
        services.close_stale_end_requests(days=7)
        entry = AuditLog.objects.get(action="buddy.end")
        self.assertEqual(entry.source, AuditLog.Source.SYSTEM_JOB)
        self.assertIsNone(entry.actor_id)
        self.assertNotIn("A-SECRET-NOTE", json.dumps([entry.before, entry.after], default=str))

    def test_the_command_runs_and_dry_run_changes_nothing(self):
        self.age(9)
        out = StringIO()
        with mock.patch.object(services, "end_request_days", return_value=7):
            call_command("close_stale_end_requests", "--dry-run", stdout=out)
            self.assertIsNone(UserBuddy.objects.get(pk=self.row.pk).ended_at)
            call_command("close_stale_end_requests", stdout=out)
        self.assertIsNotNone(UserBuddy.objects.get(pk=self.row.pk).ended_at)

    def test_both_people_can_pair_again_after_an_auto_close(self):
        self.age(9)
        services.close_stale_end_requests(days=7)
        pair(self.b, self.a)
