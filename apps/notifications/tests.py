"""Tests for the admin-alert receiver in notifications.listeners (it never imports a feature app)."""
from django.test import TestCase

from apps.accounts.models import Extension, Role, Status, User
from apps.audit.services import log_action
from apps.notifications.models import Notification, NotificationPreference, NotificationRule


def person(role, extension=None, status=Status.ACTIVE, name="x"):
    """An activated person; archived people also need `archived_at` (a database rule)."""
    from django.utils import timezone
    extra = {"archived_at": timezone.now()} if status == Status.ARCHIVED else {}
    return User.objects.create_user(
        password="pass-12345", first_name=name, last_name=role.replace("_", ""), role=role,
        extension=extension, status=status, must_change_password=False, **extra,
    )


class AdminAlertTests(TestCase):
    """The seeded rule `attendance.reopen` notifies admin and super_admin."""

    def setUp(self):
        self.ext = Extension.objects.create(name="alpha")
        self.actor = person(Role.ADMIN, name="actor")
        self.admin = person(Role.ADMIN, name="adm")
        self.super_admin = person(Role.SUPER_ADMIN, name="sup")
        self.coord = person(Role.COORDINATOR, self.ext, name="coord")

    def fire(self, action="attendance.reopen"):
        """Write an audit row the way a service would."""
        return log_action(action, actor=self.actor, entity_type="attendance", entity_id=7, extension_id=self.ext.pk)

    def recipients(self):
        return set(Notification.objects.filter(type="admin_alert").values_list("user_id", flat=True))

    def test_roles_of_the_rule_are_notified_but_not_the_actor(self):
        entry = self.fire()
        self.assertEqual(self.recipients(), {self.admin.pk, self.super_admin.pk})
        item = Notification.objects.get(user=self.admin)
        self.assertEqual(item.audit_log_id, entry.pk)
        self.assertEqual(item.actor_id, self.actor.pk)

    def test_no_rule_no_alert(self):
        self.fire("attendance.create")          # audited, but no rule row for it
        self.assertEqual(self.recipients(), set())

    def test_disabled_rule_no_alert(self):
        NotificationRule.objects.filter(audit_action="attendance.reopen").update(is_enabled=False)
        self.fire()
        self.assertEqual(self.recipients(), set())

    def test_rule_roles_are_respected(self):
        NotificationRule.objects.filter(audit_action="attendance.reopen").update(
            notify_roles=["coordinator", "super_admin"])
        self.fire()
        self.assertEqual(self.recipients(), {self.coord.pk, self.super_admin.pk})

    def test_suspended_and_archived_people_are_skipped(self):
        person(Role.ADMIN, status=Status.SUSPENDED, name="susp")
        person(Role.ADMIN, status=Status.ARCHIVED, name="arch")
        self.fire()
        self.assertEqual(self.recipients(), {self.admin.pk, self.super_admin.pk})

    def test_a_person_who_turned_in_app_alerts_off_is_skipped(self):
        NotificationPreference.objects.create(user=self.admin, type="admin_alert", in_app=False)
        self.fire()
        self.assertEqual(self.recipients(), {self.super_admin.pk})

    def test_an_alert_without_an_entity_is_allowed(self):
        log_action("attendance.reopen", actor=self.actor)      # no entity: the pair constraint must hold
        item = Notification.objects.get(user=self.admin)
        self.assertIsNone(item.entity_type)
        self.assertIsNone(item.entity_id)
