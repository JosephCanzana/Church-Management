"""accounts.test_superadmin_users: user management (super-admin).

Run with:
    docker compose exec web python manage.py test accounts.test_superadmin_users -v 2

Covers the one-time token, create / update, every status change, bulk actions,
the pages, and one real two-thread race. The last class is a TransactionTestCase
(it needs real commits to prove the token works across connections); Django runs
those after the ordinary tests, and `serialized_rollback` puts the seed data back.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services_users as svc
from apps.accounts.models import (
    ArchiveReason, DefaultPassword, Extension, Role, Status, User, UserExtensionHistory,
)
from apps.audit.models import AuditLog
from apps.core.services import ServiceError, consume_submission_token, new_submission_token

PASSWORD = "correct-horse-battery"


def make_super_admin(**overrides):
    fields = dict(first_name="Sam", last_name="Super")
    fields.update(overrides)
    return User.objects.create_superuser(password=PASSWORD, **fields)


def make_person(extension, role=Role.MEMBER, **overrides):
    fields = dict(
        first_name="Juan", last_name="Dela Cruz", role=role, extension=extension,
        status=Status.ACTIVE, must_change_password=False,
    )
    fields.update(overrides)
    return User.objects.create_user(password=PASSWORD, **fields)


def create_data(extension, **overrides):
    """What UserCreateForm.cleaned_data looks like."""
    data = dict(
        first_name="Ana", middle_name="", last_name="Reyes", birth_date=None,
        role=Role.MEMBER, extension=extension, password="", activated=False,
    )
    data.update(overrides)
    return data


def audit(action):
    return AuditLog.objects.filter(action=action)


def fresh(person):
    person.refresh_from_db()
    return person


# ------------------------------------------------------------------ token
class SubmissionTokenTests(TestCase):
    def test_first_use_is_true_and_a_replay_is_false(self):
        token = new_submission_token()
        self.assertTrue(consume_submission_token(token))
        self.assertFalse(consume_submission_token(token))

    def test_missing_or_malformed_tokens_are_refused(self):
        for bad in ("", None, "short", "has spaces in it, so no!!"):
            with self.assertRaises(ServiceError, msg=repr(bad)):
                consume_submission_token(bad)


# ----------------------------------------------------------------- create
class CreateUserTests(TestCase):
    def setUp(self):
        self.admin = make_super_admin()
        self.ext = Extension.objects.create(name="Cabanatuan")

    def create(self, **overrides):
        extension = overrides.pop("extension", self.ext)
        return svc.create_user(self.admin, None, create_data(extension, **overrides), token=new_submission_token())

    def test_a_generated_password_is_returned_once_and_works(self):
        result = self.create()
        self.assertEqual(result.password.kind, "generated")
        person = fresh(result.user)
        self.assertTrue(person.check_password(result.password.value))
        self.assertEqual(person.status, Status.NOT_ACTIVATED)
        self.assertTrue(person.must_change_password)
        self.assertRegex(person.account_id, r"^[1-9][0-9]{7,}$")
        self.assertEqual(person.created_by_id, self.admin.pk)

    def test_the_password_never_reaches_the_audit_log(self):
        result = self.create()
        logged = json.dumps(list(AuditLog.objects.values("before", "after", "entity_label")), default=str)
        self.assertNotIn(result.password.value, logged)

    def test_a_typed_password_is_used(self):
        result = self.create(password="my-test-pass")
        self.assertEqual(result.password.kind, "typed")
        self.assertTrue(fresh(result.user).check_password("my-test-pass"))

    def test_a_too_short_typed_password_is_refused(self):
        with self.assertRaises(ServiceError):
            self.create(password="short")
        self.assertFalse(User.objects.filter(first_name="Ana").exists())

    def test_the_default_password_is_used_when_one_exists(self):
        DefaultPassword.objects.create(
            extension=None, applies_to_role="member", password_hash=make_password("default-pass-1"),
        )
        result = self.create()
        self.assertEqual(result.password.kind, "default")
        self.assertTrue(fresh(result.user).check_password("default-pass-1"))

    def test_skip_activation_makes_a_usable_test_account(self):
        person = fresh(self.create(activated=True).user)
        self.assertEqual(person.status, Status.ACTIVE)
        self.assertFalse(person.must_change_password)

    def test_a_member_needs_an_extension(self):
        with self.assertRaises(ServiceError):
            self.create(extension=None)

    def test_an_admin_never_gets_an_extension(self):
        person = fresh(self.create(role=Role.ADMIN).user)
        self.assertIsNone(person.extension_id)
        self.assertEqual(UserExtensionHistory.objects.filter(user=person).count(), 0)

    def test_an_extension_history_row_is_started(self):
        person = self.create().user
        row = UserExtensionHistory.objects.get(user=person)
        self.assertEqual((row.extension_id, row.reason, row.to_date), (self.ext.pk, "initial", None))

    def test_a_new_coordinator_takes_the_seat_and_a_second_is_refused(self):
        first = self.create(role=Role.COORDINATOR).user
        self.assertEqual(fresh(self.ext).coordinator_id, first.pk)
        with self.assertRaises(ServiceError):
            self.create(role=Role.COORDINATOR, first_name="Second")

    def test_an_archived_extension_is_refused(self):
        self.ext.archived_at = timezone.now()
        self.ext.save(update_fields=["archived_at"])
        with self.assertRaises(ServiceError):
            self.create()

    def test_a_replayed_token_creates_nobody_twice(self):
        token = new_submission_token()
        data = create_data(self.ext)
        first = svc.create_user(self.admin, None, data, token=token)
        second = svc.create_user(self.admin, None, data, token=token)
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(User.objects.filter(first_name="Ana").count(), 1)

    def test_a_failed_create_does_not_use_up_the_token(self):
        token = new_submission_token()
        with self.assertRaises(ServiceError):
            svc.create_user(self.admin, None, create_data(self.ext, password="short"), token=token)
        retry = svc.create_user(self.admin, None, create_data(self.ext), token=token)
        self.assertFalse(retry.duplicate)

    def test_only_the_super_admin_may_create(self):
        member = make_person(self.ext)
        with self.assertRaises(PermissionDenied):
            svc.create_user(member, None, create_data(self.ext), token=new_submission_token())


# ----------------------------------------------------------------- update
class UpdateUserTests(TestCase):
    def setUp(self):
        self.admin = make_super_admin()
        self.ext = Extension.objects.create(name="Cabanatuan")
        self.other = Extension.objects.create(name="Gapan")
        self.person = make_person(self.ext)

    def update(self, person=None, **overrides):
        person = person or self.person
        person.refresh_from_db()          # build the form data from the current row
        data = dict(
            first_name=person.first_name, middle_name=person.middle_name, last_name=person.last_name,
            birth_date=person.birth_date, role=person.role, extension=person.extension,
        )
        data.update(overrides)
        return svc.update_user(self.admin, None, person, data)

    def test_only_changed_details_are_written_and_logged(self):
        self.update(last_name="Santos")
        row = audit("account.update").get()
        self.assertEqual(row.before, {"last_name": "Dela Cruz"})
        self.assertEqual(row.after, {"last_name": "Santos"})

    def test_saving_without_changes_does_nothing(self):
        _, changed = self.update()
        self.assertFalse(changed)
        self.assertEqual(audit("account.update").count(), 0)

    def test_promoting_to_coordinator_takes_the_seat_and_demoting_frees_it(self):
        self.update(role=Role.COORDINATOR)
        self.assertEqual(fresh(self.ext).coordinator_id, self.person.pk)
        self.update(role=Role.MEMBER)
        self.assertIsNone(fresh(self.ext).coordinator_id)
        self.assertEqual(audit("account.role_change").count(), 2)

    def test_a_taken_seat_blocks_a_second_coordinator(self):
        self.update(role=Role.COORDINATOR)
        second = make_person(self.ext, first_name="Second")
        with self.assertRaises(ServiceError):
            self.update(person=second, role=Role.COORDINATOR)

    def test_moving_extension_closes_and_opens_history_and_logs_it(self):
        svc.create_user(self.admin, None, create_data(self.ext, first_name="Hist"), token=new_submission_token())
        person = User.objects.get(first_name="Hist")
        self.update(person=person, extension=self.other)
        rows = list(UserExtensionHistory.objects.filter(user=person).order_by("pk"))
        self.assertEqual([r.extension_id for r in rows], [self.ext.pk, self.other.pk])
        self.assertIsNotNone(rows[0].to_date)
        self.assertIsNone(rows[1].to_date)
        self.assertEqual(rows[1].reason, "manual")
        self.assertEqual(audit("account.extension_change").count(), 1)

    def test_a_coordinator_who_moves_leaves_the_old_seat(self):
        self.update(role=Role.COORDINATOR)
        self.update(extension=self.other)
        self.assertIsNone(fresh(self.ext).coordinator_id)
        self.assertEqual(fresh(self.other).coordinator_id, self.person.pk)

    def test_becoming_an_admin_clears_the_extension(self):
        self.update(role=Role.ADMIN, extension=None)
        self.assertIsNone(fresh(self.person).extension_id)

    def test_you_cannot_change_your_own_role(self):
        data = dict(first_name="Sam", middle_name="", last_name="Super", birth_date=None,
                    role=Role.ADMIN, extension=None)
        with self.assertRaises(ServiceError):
            svc.update_user(self.admin, None, self.admin, data)

    def test_the_last_active_super_admin_is_protected(self):
        # The acting super-admin is always one of the active ones, so only a race can
        # reach this guard through a service call. Check the guard itself.
        with self.assertRaises(ServiceError):
            svc._refuse_last_super_admin(svc._lock_person(self.admin.pk), "change the role of")
        make_super_admin(first_name="Two")
        svc._refuse_last_super_admin(svc._lock_person(self.admin.pk), "change the role of")  # allowed now

    def test_an_archived_person_cannot_be_edited(self):
        svc.archive_user(self.admin, None, self.person)
        with self.assertRaises(ServiceError):
            self.update(last_name="Nope")


# --------------------------------------------------------- status changes
class StatusChangeTests(TestCase):
    def setUp(self):
        self.admin = make_super_admin()
        self.ext = Extension.objects.create(name="Cabanatuan")
        self.person = make_person(self.ext)

    # ----- suspend / unsuspend
    def test_a_suspended_person_cannot_log_in_and_can_after_unsuspend(self):
        svc.suspend_user(self.admin, None, self.person)
        self.assertIsNone(authenticate(identifier=self.person.account_id, password=PASSWORD))
        svc.unsuspend_user(self.admin, None, self.person)
        self.assertEqual(fresh(self.person).status, Status.ACTIVE)
        self.assertIsNotNone(authenticate(identifier=self.person.account_id, password=PASSWORD))

    def test_suspending_ends_an_open_session_and_is_logged_as_a_login_reason(self):
        self.client.force_login(self.person)
        self.assertEqual(self.client.get(reverse("dashboards:member")).status_code, 200)
        svc.suspend_user(self.admin, None, self.person)
        self.assertEqual(self.client.get(reverse("dashboards:member")).status_code, 302)
        self.client.post(reverse("accounts:login"), {"identifier": self.person.account_id, "password": PASSWORD})
        self.assertEqual(audit("account.login_failed").latest("pk").after, {"reason": "suspended"})

    def test_unsuspending_someone_who_never_activated_returns_them_to_not_activated(self):
        newbie = make_person(self.ext, first_name="New", status=Status.NOT_ACTIVATED, must_change_password=True)
        svc.suspend_user(self.admin, None, newbie)
        svc.unsuspend_user(self.admin, None, newbie)
        self.assertEqual(fresh(newbie).status, Status.NOT_ACTIVATED)

    def test_suspending_twice_logs_once(self):
        svc.suspend_user(self.admin, None, self.person)
        _, changed = svc.suspend_user(self.admin, None, self.person)
        self.assertFalse(changed)
        self.assertEqual(audit("account.suspend").count(), 1)

    def test_you_cannot_suspend_yourself(self):
        with self.assertRaises(ServiceError):
            svc.suspend_user(self.admin, None, self.admin)

    def test_a_suspended_super_admin_can_no_longer_manage_anyone(self):
        other = make_super_admin(first_name="Other")
        svc.suspend_user(self.admin, None, other)
        other.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            svc.suspend_user(other, None, self.person)

    # ----- deactivate
    def test_deactivate_sends_an_active_person_back_to_activation(self):
        svc.deactivate_user(self.admin, None, self.person)
        person = fresh(self.person)
        self.assertEqual(person.status, Status.NOT_ACTIVATED)
        self.assertTrue(person.must_change_password)

    def test_deactivate_is_only_for_active_accounts(self):
        svc.suspend_user(self.admin, None, self.person)
        with self.assertRaises(ServiceError):
            svc.deactivate_user(self.admin, None, self.person)
        newbie = make_person(self.ext, first_name="New", status=Status.NOT_ACTIVATED, must_change_password=True)
        self.assertFalse(svc.deactivate_user(self.admin, None, newbie)[1])

    # ----- archive / restore / delete
    def test_archive_sets_the_purge_date_and_restore_clears_it(self):
        svc.archive_user(self.admin, None, self.person)
        person = fresh(self.person)
        self.assertEqual((person.status, person.archive_reason), (Status.ARCHIVED, ArchiveReason.MANUAL))
        self.assertIsNotNone(person.archived_at)
        self.assertIsNotNone(person.purge_at)                 # set by the database trigger
        svc.restore_user(self.admin, None, person)
        person = fresh(person)
        self.assertEqual(person.status, Status.ACTIVE)
        self.assertIsNone(person.archived_at)
        self.assertIsNone(person.purge_at)
        self.assertIsNone(person.archive_reason)

    def test_archiving_twice_logs_once(self):
        svc.archive_user(self.admin, None, self.person)
        self.assertFalse(svc.archive_user(self.admin, None, self.person)[1])
        self.assertEqual(audit("account.archive").count(), 1)

    def test_archiving_a_coordinator_frees_the_seat_and_makes_them_a_member(self):
        coordinator = make_person(self.ext, role=Role.COORDINATOR, first_name="Coord")
        self.ext.coordinator = coordinator
        self.ext.save(update_fields=["coordinator"])
        svc.archive_user(self.admin, None, coordinator)
        self.assertIsNone(fresh(self.ext).coordinator_id)
        self.assertEqual(fresh(coordinator).role, Role.MEMBER)

    def test_you_cannot_archive_yourself(self):
        with self.assertRaises(ServiceError):
            svc.archive_user(self.admin, None, self.admin)

    def test_restoring_into_an_archived_extension_is_refused(self):
        svc.archive_user(self.admin, None, self.person)
        self.ext.archived_at = timezone.now()
        self.ext.save(update_fields=["archived_at"])
        with self.assertRaises(ServiceError):
            svc.restore_user(self.admin, None, self.person)

    def test_restoring_someone_who_is_not_archived_does_nothing(self):
        self.assertFalse(svc.restore_user(self.admin, None, self.person)[1])

    def test_delete_needs_an_archived_person_and_never_yourself(self):
        with self.assertRaises(ServiceError):
            svc.force_delete_user(self.admin, None, self.person)
        svc.archive_user(self.admin, None, self.person)
        pk = self.person.pk
        svc.force_delete_user(self.admin, None, self.person)
        self.assertFalse(User.objects.filter(pk=pk).exists())
        self.assertEqual(audit("account.delete").filter(entity_id=pk).count(), 1)

    # ----- reset password
    def test_reset_gives_a_new_working_password_and_ends_the_old_one(self):
        result = svc.reset_password(self.admin, None, self.person, token=new_submission_token())
        person = fresh(self.person)
        self.assertTrue(person.check_password(result.password.value))
        self.assertFalse(person.check_password(PASSWORD))
        self.assertTrue(person.must_change_password)
        logged = json.dumps(list(AuditLog.objects.values("before", "after")), default=str)
        self.assertNotIn(result.password.value, logged)

    def test_reset_with_a_replayed_token_does_nothing(self):
        token = new_submission_token()
        svc.reset_password(self.admin, None, self.person, token=token)
        hash_after_first = fresh(self.person).password
        again = svc.reset_password(self.admin, None, self.person, token=token)
        self.assertTrue(again.duplicate)
        self.assertEqual(fresh(self.person).password, hash_after_first)

    def test_reset_is_refused_for_yourself_and_for_archived_people(self):
        with self.assertRaises(ServiceError):
            svc.reset_password(self.admin, None, self.admin, token=new_submission_token())
        svc.archive_user(self.admin, None, self.person)
        with self.assertRaises(ServiceError):
            svc.reset_password(self.admin, None, self.person, token=new_submission_token())


# ------------------------------------------------------------------- bulk
class BulkTests(TestCase):
    def setUp(self):
        self.admin = make_super_admin()
        self.ext = Extension.objects.create(name="Cabanatuan")
        self.people = [make_person(self.ext, first_name=f"P{n}") for n in range(3)]

    def run_bulk(self, action, people, **kw):
        ids = [p.pk for p in people]
        return svc.run_bulk(self.admin, None, action, ids, token=kw.pop("token", new_submission_token()))

    def test_it_works_on_everyone_it_can_and_logs_one_row_per_person(self):
        result = self.run_bulk("archive", self.people)
        self.assertEqual(len(result.done), 3)
        self.assertEqual(audit("account.archive").count(), 3)
        self.assertEqual(audit("account.bulk_archive").count(), 1)
        self.assertEqual(audit("account.bulk_archive").get().after, {"count": 3})

    def test_people_it_cannot_apply_to_are_skipped_with_a_reason(self):
        svc.archive_user(self.admin, None, self.people[0])
        result = self.run_bulk("archive", [*self.people, self.admin])
        self.assertEqual(len(result.done), 2)
        reasons = dict(result.skipped)
        self.assertEqual(reasons["P0 Dela Cruz"], "already archived")
        self.assertIn("your own account", reasons[self.admin.full_name])

    def test_a_missing_person_is_skipped_not_fatal(self):
        result = svc.run_bulk(self.admin, None, "suspend", [self.people[0].pk, 999999], token=new_submission_token())
        self.assertEqual(len(result.done), 1)
        self.assertEqual(result.skipped, [("#999999", "no longer exists")])

    def test_the_same_selection_submitted_twice_is_done_once(self):
        token = new_submission_token()
        first = self.run_bulk("suspend", self.people, token=token)
        second = self.run_bulk("suspend", self.people, token=token)
        self.assertEqual(len(first.done), 3)
        self.assertTrue(second.duplicate)
        self.assertEqual(audit("account.suspend").count(), 3)

    def test_reset_password_returns_one_row_per_person(self):
        result = self.run_bulk("reset_password", self.people)
        self.assertEqual(len(result.passwords), 3)
        for (name, account_id, password), person in zip(result.passwords, self.people):
            self.assertTrue(fresh(person).check_password(password))

    def test_limits_and_bad_input_are_refused(self):
        with self.assertRaises(ServiceError):
            svc.run_bulk(self.admin, None, "archive", [], token=new_submission_token())
        with self.assertRaises(ServiceError):
            svc.run_bulk(self.admin, None, "archive", range(1, svc.MAX_BULK + 2), token=new_submission_token())
        with self.assertRaises(ServiceError):
            svc.run_bulk(self.admin, None, "launch_rockets", [1], token=new_submission_token())

    def test_delete_only_removes_archived_people(self):
        svc.archive_user(self.admin, None, self.people[0])
        result = self.run_bulk("delete", self.people)
        self.assertEqual(result.done, ["P0 Dela Cruz"])
        self.assertEqual(len(result.skipped), 2)
        self.assertEqual(User.objects.filter(first_name__startswith="P").count(), 2)


# ------------------------------------------------------------------ pages
class UserViewTests(TestCase):
    def setUp(self):
        self.admin = make_super_admin()
        self.client.force_login(self.admin)
        self.ext = Extension.objects.create(name="Cabanatuan")
        self.other = Extension.objects.create(name="Gapan")
        self.ana = make_person(self.ext, first_name="Ana", last_name="Reyes")
        self.ben = make_person(self.other, first_name="Ben", last_name="Santos", role=Role.COORDINATOR)
        self.url = reverse("superadmin:user_list")

    def names(self, response):
        return [p.first_name for p in response.context["page"].object_list]

    def test_other_roles_are_sent_home(self):
        self.client.force_login(self.ana)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse("dashboards:member"), fetch_redirect_response=False)

    def test_list_defaults_hide_archived_people(self):
        svc.archive_user(self.admin, None, self.ben)
        self.assertNotIn("Ben", self.names(self.client.get(self.url)))
        self.assertEqual(self.names(self.client.get(self.url, {"status": "archived"})), ["Ben"])
        self.assertIn("Ben", self.names(self.client.get(self.url, {"status": "all"})))

    def test_search_matches_several_words_across_name_fields_and_ids(self):
        self.assertEqual(self.names(self.client.get(self.url, {"q": "ana reyes"})), ["Ana"])
        self.assertEqual(self.names(self.client.get(self.url, {"q": self.ben.account_id})), ["Ben"])

    def test_filters_by_role_extension_and_status(self):
        self.assertEqual(self.names(self.client.get(self.url, {"role": "coordinator"})), ["Ben"])
        self.assertEqual(self.names(self.client.get(self.url, {"extension": self.ext.pk})), ["Ana"])
        svc.suspend_user(self.admin, None, self.ana)
        self.assertEqual(self.names(self.client.get(self.url, {"status": "suspended"})), ["Ana"])

    def test_sorting_by_first_name_and_account_id_both_ways(self):
        got = lambda sort: self.names(self.client.get(self.url, {"sort": sort, "role": "", "q": "", "status": "current"}))
        self.assertEqual([n for n in got("first_name") if n in ("Ana", "Ben")], ["Ana", "Ben"])
        self.assertEqual([n for n in got("-first_name") if n in ("Ana", "Ben")], ["Ben", "Ana"])
        low, high = sorted([self.ana, self.ben], key=lambda p: p.account_id)
        self.assertEqual([n for n in got("account_id") if n in (low.first_name, high.first_name)],
                         [low.first_name, high.first_name])
        self.assertEqual([n for n in got("-account_id") if n in (low.first_name, high.first_name)],
                         [high.first_name, low.first_name])

    def test_a_bad_sort_value_is_ignored(self):
        self.assertEqual(self.client.get(self.url, {"sort": "password"}).status_code, 200)

    def test_you_cannot_tick_your_own_row_and_the_right_bulk_buttons_show(self):
        response = self.client.get(self.url)
        self.assertNotContains(response, f'name="ids" value="{self.admin.pk}"')
        self.assertContains(response, f'name="ids" value="{self.ana.pk}"')
        self.assertContains(response, "Reset password")
        self.assertNotContains(response, 'value="delete"')
        svc.archive_user(self.admin, None, self.ben)
        self.assertContains(self.client.get(self.url, {"status": "archived"}), 'value="delete"')

    def test_creating_a_person_shows_the_password_once(self):
        response = self.client.post(reverse("superadmin:user_create"), {
            "first_name": "Cara", "last_name": "Lim", "role": "member", "extension": self.ext.pk,
            "submit_token": new_submission_token(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="secrets-data"')
        self.assertTrue(User.objects.filter(first_name="Cara").exists())
        self.assertIn("no-store", response["Cache-Control"])

    def test_posting_the_same_create_form_twice_makes_one_person(self):
        payload = {"first_name": "Dina", "last_name": "Cruz", "role": "member",
                   "extension": self.ext.pk, "submit_token": new_submission_token()}
        self.client.post(reverse("superadmin:user_create"), payload)
        second = self.client.post(reverse("superadmin:user_create"), payload)
        self.assertRedirects(second, self.url, fetch_redirect_response=False)
        self.assertEqual(User.objects.filter(first_name="Dina").count(), 1)

    def test_an_invalid_create_form_shows_errors_and_creates_nobody(self):
        response = self.client.post(reverse("superadmin:user_create"), {
            "first_name": "Eli", "last_name": "Go", "role": "member", "extension": "",
            "submit_token": new_submission_token(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose an extension for this role.")
        self.assertFalse(User.objects.filter(first_name="Eli").exists())

    def test_edit_saves_changes(self):
        response = self.client.post(reverse("superadmin:user_edit", args=[self.ana.pk]), {
            "first_name": "Anna", "last_name": "Reyes", "role": "member", "extension": self.ext.pk,
        })
        self.assertRedirects(response, reverse("superadmin:user_detail", args=[self.ana.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(fresh(self.ana).first_name, "Anna")

    def test_the_detail_page_and_unknown_people(self):
        self.assertContains(self.client.get(reverse("superadmin:user_detail", args=[self.ana.pk])),
                            self.ana.account_id)
        gone = self.client.get(reverse("superadmin:user_detail", args=[999999]))
        self.assertRedirects(gone, self.url, fetch_redirect_response=False)

    def test_actions_reject_get_requests(self):
        for name in ("archive", "restore", "suspend", "unsuspend", "deactivate", "reset_password", "delete"):
            url = reverse(f"superadmin:user_{name}", args=[self.ana.pk])
            self.assertEqual(self.client.get(url).status_code, 405, name)
        self.assertEqual(self.client.get(reverse("superadmin:user_bulk")).status_code, 405)

    def test_a_single_action_works_and_a_repeat_is_calm(self):
        url = reverse("superadmin:user_suspend", args=[self.ana.pk])
        self.client.post(url)
        again = self.client.post(url, follow=True)
        self.assertEqual(fresh(self.ana).status, Status.SUSPENDED)
        self.assertContains(again, "already suspended")

    def test_deleting_a_person_who_is_already_gone_is_calm(self):
        response = self.client.post(reverse("superadmin:user_delete", args=[999999]))
        self.assertRedirects(response, self.url, fetch_redirect_response=False)

    def test_a_single_reset_shows_the_password_once(self):
        response = self.client.post(
            reverse("superadmin:user_reset_password", args=[self.ana.pk]),
            {"submit_token": new_submission_token()},
        )
        self.assertContains(response, 'id="secrets-data"')

    def test_a_bulk_action_returns_to_the_same_filtered_list(self):
        response = self.client.post(reverse("superadmin:user_bulk"), {
            "action": "archive", "ids": [self.ana.pk, self.ben.pk],
            "submit_token": new_submission_token(), "qs": "status=all&sort=first_name",
        })
        self.assertEqual(response["Location"], f"{self.url}?status=all&sort=first_name")
        self.assertEqual(fresh(self.ana).status, Status.ARCHIVED)
        self.assertEqual(fresh(self.ben).status, Status.ARCHIVED)

    def test_a_bulk_reset_shows_all_passwords_in_one_dialog(self):
        response = self.client.post(reverse("superadmin:user_bulk"), {
            "action": "reset_password", "ids": [self.ana.pk, self.ben.pk],
            "submit_token": new_submission_token(), "qs": "",
        })
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode().split('id="secrets-data" type="application/json">')[1].split("</script>")[0])
        self.assertEqual(len(payload["rows"]), 2)

    def test_a_bulk_post_with_nothing_ticked_says_so(self):
        response = self.client.post(reverse("superadmin:user_bulk"), {
            "action": "archive", "submit_token": new_submission_token(), "qs": "",
        }, follow=True)
        self.assertContains(response, "Select at least one person.")


# ------------------------------------------------------------ concurrency
class CreateUserRaceTests(TransactionTestCase):
    """Two identical submissions at the same instant create exactly one person."""

    serialized_rollback = True   # restores the migration seed data after the flush

    def test_two_simultaneous_submissions_create_one_person(self):
        admin = make_super_admin()
        ext = Extension.objects.create(name="Race")
        token = new_submission_token()
        barrier = threading.Barrier(2)

        def submit(_):
            try:
                barrier.wait(timeout=10)
                return svc.create_user(admin, None, create_data(ext, first_name="Racer"), token=token)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))

        self.assertEqual(sorted(r.duplicate for r in results), [False, True])
        self.assertEqual(User.objects.filter(first_name="Racer").count(), 1)
