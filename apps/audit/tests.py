"""audit.tests: tests for audit.services.log_action.

Run with:
    docker compose exec web python manage.py test audit -v 2

The actor is a plain stand-in object, not a real User, so these tests do not
import the accounts app (audit must not depend on feature apps).
"""
from types import SimpleNamespace

from django.test import RequestFactory, TestCase

from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.audit.signals import action_logged


class FakeActor(SimpleNamespace):
    """Minimal stand-in for a User: pk, role, extension_id and a readable str."""

    def __str__(self):
        return self.label


def make_actor():
    return FakeActor(pk=7, role="coordinator", extension_id=3, label="Maria Santos (10002026)")


class LogActionTests(TestCase):
    """What gets stored by log_action()."""

    def test_writes_a_row_with_the_basic_fields(self):
        entry = log_action("account.archive", entity_type="app_user", entity_id=5)
        row = AuditLog.objects.get(pk=entry.pk)
        self.assertEqual(row.action, "account.archive")
        self.assertEqual(row.entity_type, "app_user")
        self.assertEqual(row.entity_id, 5)
        self.assertEqual(row.status, AuditLog.Result.SUCCESS)
        self.assertEqual(row.source, AuditLog.Source.WEB)

    def test_actor_details_are_copied_onto_the_row(self):
        entry = log_action("attendance.upload", actor=make_actor())
        self.assertEqual(entry.actor_id, 7)
        self.assertEqual(entry.actor_label, "Maria Santos (10002026)")
        self.assertEqual(entry.actor_role, "coordinator")

    def test_extension_defaults_to_the_actors_extension(self):
        self.assertEqual(log_action("x.y", actor=make_actor()).extension_id, 3)

    def test_explicit_extension_wins_over_the_actors(self):
        entry = log_action("x.y", actor=make_actor(), extension_id=9)
        self.assertEqual(entry.extension_id, 9)

    def test_no_actor_for_system_jobs(self):
        entry = log_action("job.purge", source=AuditLog.Source.SYSTEM_JOB)
        self.assertIsNone(entry.actor_id)
        self.assertEqual(entry.source, AuditLog.Source.SYSTEM_JOB)

    def test_ip_and_user_agent_come_from_the_request(self):
        request = RequestFactory().get("/", REMOTE_ADDR="203.0.113.9", HTTP_USER_AGENT="TestBrowser/1.0")
        entry = log_action("x.y", request=request)
        self.assertEqual(entry.ip_address, "203.0.113.9")
        self.assertEqual(entry.user_agent, "TestBrowser/1.0")

    def test_failed_status_is_stored(self):
        entry = log_action("account.login_failed", status=AuditLog.Result.FAILED)
        self.assertEqual(entry.status, AuditLog.Result.FAILED)


class PrivacyTests(TestCase):
    """Sensitive values never reach the table."""

    def test_sensitive_keys_are_removed_at_every_depth(self):
        entry = log_action(
            "account.update",
            before={"first_name": "A", "password_hash": "abc", "nested": {"new_token": "t", "ok": 1}},
            after={"first_name": "B", "goal_text": "private", "amount": "100.00", "notes": "n"},
        )
        row = AuditLog.objects.get(pk=entry.pk)
        self.assertEqual(row.before, {"first_name": "A", "nested": {"ok": 1}})
        self.assertEqual(row.after, {"first_name": "B"})

    def test_non_json_values_are_converted_not_crashed(self):
        import datetime

        entry = log_action("x.y", after={"birth_date": datetime.date(2000, 1, 2)})
        self.assertEqual(AuditLog.objects.get(pk=entry.pk).after, {"birth_date": "2000-01-02"})


class SignalTests(TestCase):
    """Other apps can react through `action_logged`."""

    def test_signal_is_sent_with_the_saved_entry(self):
        received = []

        def receiver(sender, entry, **kwargs):
            received.append(entry)

        action_logged.connect(receiver)
        self.addCleanup(action_logged.disconnect, receiver)

        entry = log_action("x.y")
        self.assertEqual(received, [entry])