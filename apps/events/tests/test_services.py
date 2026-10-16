from datetime import datetime, timedelta

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Extension, Role, Status
from apps.audit.models import AuditLog
from apps.core.services import ServiceError, archive, new_submission_token
from apps.events import services
from apps.events.models import Event
from apps.notifications.models import Notification, NotificationPreference

from .helpers import archive_user, build_world, in_days, make_event, make_user


def notified(event, type_):
    """Set of user ids that got a notice of this type about this event."""
    return set(
        Notification.objects.filter(type=type_, entity_type="event", entity_id=event.pk)
        .values_list("user_id", flat=True)
    )


class CreateEventTests(TestCase):
    def setUp(self):
        self.w = build_world()

    def create(self, actor, extension_id, **kw):
        kw.setdefault("title", "Prayer night")
        kw.setdefault("start_at", in_days(3))
        return services.create_event(actor, extension_id=extension_id, **kw)

    def test_admin_creates_a_global_event(self):
        event = self.create(self.w.admin, None, location="Main hall")
        self.assertIsNone(event.extension_id)
        self.assertEqual(event.created_by_id, self.w.admin.pk)
        self.assertEqual(event.location, "Main hall")

    def test_admin_creates_an_event_for_any_extension(self):
        event = self.create(self.w.super, self.w.ext_b.pk)
        self.assertEqual(event.extension_id, self.w.ext_b.pk)

    def test_coordinator_creates_for_own_extension(self):
        event = self.create(self.w.coord_a, self.w.ext_a.pk)
        self.assertEqual(event.extension_id, self.w.ext_a.pk)

    def test_coordinator_cannot_create_global_or_for_another_extension(self):
        with self.assertRaises(PermissionDenied):
            self.create(self.w.coord_a, None)
        with self.assertRaises(PermissionDenied):
            self.create(self.w.coord_a, self.w.ext_b.pk)
        self.assertEqual(Event.objects.count(), 0)

    def test_member_cannot_create(self):
        with self.assertRaises(PermissionDenied):
            self.create(self.w.member_a, self.w.ext_a.pk)

    def test_suspended_coordinator_cannot_create(self):
        coord = make_user(Role.COORDINATOR, self.w.ext_a, status=Status.SUSPENDED)
        with self.assertRaises(PermissionDenied):
            self.create(coord, self.w.ext_a.pk)

    def test_archived_extension_is_refused(self):
        archive(self.w.ext_b)
        with self.assertRaises(ServiceError):
            self.create(self.w.admin, self.w.ext_b.pk)

    def test_missing_extension_is_refused(self):
        with self.assertRaises(ServiceError):
            self.create(self.w.admin, 999999)

    def test_title_is_required_and_trimmed(self):
        with self.assertRaises(ServiceError):
            self.create(self.w.admin, None, title="   ")
        self.assertEqual(self.create(self.w.admin, None, title="  Youth night  ").title, "Youth night")

    def test_end_must_be_after_start(self):
        start = in_days(3)
        with self.assertRaises(ServiceError):
            self.create(self.w.admin, None, start_at=start, end_at=start)
        with self.assertRaises(ServiceError):
            self.create(self.w.admin, None, start_at=start, end_at=start - timedelta(hours=1))
        self.assertEqual(Event.objects.count(), 0)

    def test_start_is_required(self):
        with self.assertRaises(ServiceError):
            self.create(self.w.admin, None, start_at=None)

    def test_naive_times_are_read_as_manila_time(self):
        event = self.create(self.w.admin, None, start_at=datetime(2031, 5, 4, 9, 0))
        local = timezone.localtime(event.start_at)
        self.assertEqual((local.hour, str(local.tzinfo)), (9, "Asia/Manila"))

    def test_the_same_form_token_cannot_create_twice(self):
        token = new_submission_token()
        self.create(self.w.admin, None, token=token)
        with self.assertRaises(ServiceError):
            self.create(self.w.admin, None, token=token)
        self.assertEqual(Event.objects.count(), 1)

    def test_creating_is_audited_and_notifies_nobody(self):
        event = self.create(self.w.coord_a, self.w.ext_a.pk)
        row = AuditLog.objects.get(action="event.create", entity_id=event.pk)
        self.assertEqual(row.actor_id, self.w.coord_a.pk)
        self.assertEqual(row.extension_id, self.w.ext_a.pk)
        self.assertEqual(Notification.objects.count(), 0)

    def test_the_event_belongs_to_the_extension_not_the_coordinator(self):
        event = self.create(self.w.coord_a, self.w.ext_a.pk)
        Extension.objects.filter(pk=self.w.ext_a.pk).update(coordinator=self.w.member_a)
        event.refresh_from_db()
        self.assertEqual(event.extension_id, self.w.ext_a.pk)


class UpdateEventTests(TestCase):
    def setUp(self):
        self.w = build_world()
        self.event = make_event(self.w.ext_a, title="Cell group", description="Bring a friend",
                                location="Room 1", start_at=in_days(3), end_at=in_days(3, 2))

    def update(self, actor, event=None, **changes):
        event = event or self.event
        event.refresh_from_db()
        values = dict(title=event.title, description=event.description, start_at=event.start_at,
                      end_at=event.end_at, location=event.location)
        values.update(changes)
        return services.update_event(actor, event, **values)

    def test_description_only_edit_saves_logs_and_notifies_nobody(self):
        self.update(self.w.coord_a, description="Now with snacks")
        self.event.refresh_from_db()
        self.assertEqual(self.event.description, "Now with snacks")
        row = AuditLog.objects.get(action="event.update", entity_id=self.event.pk)
        self.assertEqual(row.after, {"description_changed": True})
        self.assertEqual(Notification.objects.count(), 0)

    def test_title_only_edit_does_not_notify(self):
        self.update(self.w.coord_a, title="Cell group night")
        self.assertEqual(Notification.objects.count(), 0)

    def test_time_change_notifies_the_extension_and_logs(self):
        w = self.w
        self.update(w.admin, start_at=in_days(4), end_at=in_days(4, 2))
        self.assertEqual(notified(self.event, services.EVENT_CHANGED),
                         {w.coord_a.pk, w.member_a.pk, w.member_a2.pk})
        notice = Notification.objects.get(user=w.member_a, type=services.EVENT_CHANGED)
        self.assertEqual(notice.data["changes"], ["time"])
        self.assertEqual(notice.actor_id, w.admin.pk)
        self.assertEqual(notice.audit_log_id,
                         AuditLog.objects.get(action="event.update", entity_id=self.event.pk).pk)
        self.assertNotIn("description", notice.data)

    def test_place_change_notifies_and_both_changes_are_listed(self):
        self.update(self.w.admin, location="Fellowship hall")
        self.assertEqual(Notification.objects.get(user=self.w.member_a).data["changes"], ["place"])
        Notification.objects.all().delete()
        self.update(self.w.admin, location="Chapel", start_at=in_days(5), end_at=in_days(5, 2))
        self.assertEqual(Notification.objects.get(user=self.w.member_a).data["changes"], ["time", "place"])

    def test_the_person_who_made_the_change_is_not_notified(self):
        w = self.w
        self.update(w.coord_a, location="Chapel")
        self.assertEqual(notified(self.event, services.EVENT_CHANGED), {w.member_a.pk, w.member_a2.pk})

    def test_inactive_suspended_archived_and_muted_people_are_skipped(self):
        w = self.w
        suspended = make_user(Role.MEMBER, w.ext_a, status=Status.SUSPENDED)
        archived = archive_user(make_user(Role.MEMBER, w.ext_a))
        not_activated = make_user(Role.MEMBER, w.ext_a, status=Status.NOT_ACTIVATED)
        NotificationPreference.objects.create(user=w.member_a2, type=services.EVENT_CHANGED, in_app=False)
        self.update(w.admin, location="Chapel")
        got = notified(self.event, services.EVENT_CHANGED)
        self.assertEqual(got, {w.coord_a.pk, w.member_a.pk})
        for skipped in (suspended, archived, not_activated, w.member_a2, w.member_b, w.coord_b):
            self.assertNotIn(skipped.pk, got)

    def test_global_event_change_reaches_every_active_person_but_the_actor(self):
        w = self.w
        suspended = make_user(Role.MEMBER, w.ext_b, status=Status.SUSPENDED)
        event = make_event(None, title="Anniversary")
        self.update(w.admin, event, location="Main hall")
        expected = {w.super.pk, w.coord_a.pk, w.coord_b.pk, w.member_a.pk, w.member_a2.pk, w.member_b.pk}
        self.assertEqual(notified(event, services.EVENT_CHANGED), expected)
        self.assertNotIn(suspended.pk, expected)

    def test_a_started_event_cannot_be_moved_but_can_be_described(self):
        started = make_event(self.w.ext_a, start_at=timezone.now() - timedelta(hours=1))
        with self.assertRaises(ServiceError):
            self.update(self.w.admin, started, start_at=in_days(1))
        self.update(self.w.admin, started, description="Recorded, link to follow")
        started.refresh_from_db()
        self.assertEqual(started.description, "Recorded, link to follow")

    def test_nothing_changed_saves_and_logs_nothing(self):
        self.update(self.w.admin)
        self.assertFalse(AuditLog.objects.filter(action="event.update").exists())
        self.assertEqual(Notification.objects.count(), 0)

    def test_end_before_start_is_refused(self):
        with self.assertRaises(ServiceError):
            self.update(self.w.admin, end_at=self.event.start_at - timedelta(minutes=5))

    def test_a_cancelled_event_cannot_be_changed(self):
        services.cancel_event(self.w.admin, self.event)
        with self.assertRaises(ServiceError):
            self.update(self.w.admin, description="Too late")

    def test_who_may_edit(self):
        w = self.w
        for person in (w.coord_b, w.member_a, w.member_b):
            with self.assertRaises(PermissionDenied):
                self.update(person, title="Hijacked")
        global_event = make_event(None)
        with self.assertRaises(PermissionDenied):
            self.update(w.coord_a, global_event, title="Hijacked")
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Cell group")

    def test_a_deleted_event_gives_a_friendly_error(self):
        event_id = self.event.pk
        self.event.delete()
        with self.assertRaises(ServiceError):
            services.update_event(self.w.admin, event_id, title="x", start_at=in_days(1))


class CancelEventTests(TestCase):
    def setUp(self):
        self.w = build_world()
        self.event = make_event(self.w.ext_a, start_at=in_days(2))

    def test_cancel_marks_the_event_and_keeps_the_row(self):
        w = self.w
        services.cancel_event(w.coord_a, self.event)
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_cancelled)
        self.assertEqual(self.event.cancelled_by_id, w.coord_a.pk)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        self.assertIsNone(self.event.archived_at)

    def test_cancel_is_audited(self):
        services.cancel_event(self.w.admin, self.event)
        row = AuditLog.objects.get(action="event.cancel", entity_id=self.event.pk)
        self.assertEqual(row.actor_id, self.w.admin.pk)
        self.assertEqual(row.extension_id, self.w.ext_a.pk)

    def test_cancel_notifies_recipients_as_important(self):
        w = self.w
        services.cancel_event(w.coord_a, self.event)
        self.assertEqual(notified(self.event, services.EVENT_CANCELLED), {w.member_a.pk, w.member_a2.pk})
        notice = Notification.objects.get(user=w.member_a)
        self.assertEqual(notice.importance, Notification.Importance.IMPORTANT)
        self.assertEqual(notice.data, {"title": self.event.title})

    def test_cancel_respects_event_alerts_off(self):
        NotificationPreference.objects.create(user=self.w.member_a, type=services.EVENT_CANCELLED, in_app=False)
        services.cancel_event(self.w.admin, self.event)
        self.assertNotIn(self.w.member_a.pk, notified(self.event, services.EVENT_CANCELLED))
        self.assertIn(self.w.member_a2.pk, notified(self.event, services.EVENT_CANCELLED))

    def test_cancelling_a_global_event_reaches_everyone_active(self):
        w = self.w
        event = make_event(None, start_at=in_days(2))
        services.cancel_event(w.admin, event)
        self.assertEqual(
            notified(event, services.EVENT_CANCELLED),
            {w.super.pk, w.coord_a.pk, w.coord_b.pk, w.member_a.pk, w.member_a2.pk, w.member_b.pk},
        )

    def test_cancel_twice_is_refused(self):
        services.cancel_event(self.w.admin, self.event)
        with self.assertRaises(ServiceError):
            services.cancel_event(self.w.admin, self.event)
        self.assertEqual(Notification.objects.filter(type=services.EVENT_CANCELLED).count(), 3)  # one notice each for coord_a, member_a, member_a2, none from the refused second cancel

    def test_an_event_that_already_ended_cannot_be_cancelled(self):
        past = make_event(self.w.ext_a, start_at=timezone.now() - timedelta(days=2),
                          end_at=timezone.now() - timedelta(days=2) + timedelta(hours=2))
        with self.assertRaises(ServiceError):
            services.cancel_event(self.w.admin, past)

    def test_an_event_still_running_can_be_cancelled(self):
        running = make_event(self.w.ext_a, start_at=timezone.now() - timedelta(hours=1),
                             end_at=timezone.now() + timedelta(hours=1))
        services.cancel_event(self.w.admin, running)
        running.refresh_from_db()
        self.assertTrue(running.is_cancelled)

    def test_who_may_cancel(self):
        w = self.w
        for person in (w.coord_b, w.member_a, w.member_b):
            with self.assertRaises(PermissionDenied):
                services.cancel_event(person, self.event)
        with self.assertRaises(PermissionDenied):
            services.cancel_event(w.coord_a, make_event(None))
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_cancelled)
        self.assertEqual(Notification.objects.count(), 0)
