"""accounts.tests.test_person_type: people types, the trimmed statuses and the sign-in rules.

Run with:
    docker compose exec web python manage.py test apps.accounts.tests.test_person_type -v 2
"""
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from apps.accounts import permissions as perms
from apps.accounts.models import Extension, PersonType, Role, Status, User
from apps.accounts.services import special_roles
from apps.accounts.services import users as svc
from apps.accounts.tests.test_superadmin_users import (
    PASSWORD, create_data, fresh, make_person, make_super_admin,
)
from apps.core.services import ServiceError, new_submission_token


class ChoicesTests(SimpleTestCase):
    def test_status_has_no_not_activated(self):
        self.assertEqual(set(Status.values), {"active", "suspended", "archived"})
        self.assertFalse(hasattr(Status, "NOT_ACTIVATED"))

    def test_the_three_person_types(self):
        self.assertEqual(set(PersonType.values), {"guest", "member_record", "member_account"})
        self.assertEqual(PersonType.MEMBER_RECORD.label, "Member (no account)")
        self.assertEqual(PersonType.MEMBER_ACCOUNT.label, "Member")


class SignInRuleTests(TestCase):
    def setUp(self):
        self.ext = Extension.objects.create(name="Cabanatuan")

    def test_only_a_member_account_can_sign_in(self):
        for person_type, expected in (
            (PersonType.MEMBER_ACCOUNT, True), (PersonType.MEMBER_RECORD, False), (PersonType.GUEST, False),
        ):
            person = make_person(self.ext, person_type=person_type)
            self.assertEqual(person.can_sign_in, expected, person_type)
            self.assertEqual(person.is_active, expected, person_type)

    def test_guests_and_no_account_members_get_an_unusable_password_and_an_account_id(self):
        for person_type in (PersonType.GUEST, PersonType.MEMBER_RECORD):
            person = make_person(self.ext, person_type=person_type, first_name=person_type)
            self.assertFalse(person.has_usable_password(), person_type)
            self.assertRegex(person.account_id, r"^[1-9][0-9]{7,}$")
            self.assertIsNone(authenticate(identifier=person.account_id, password=PASSWORD))

    def test_a_suspended_member_cannot_sign_in(self):
        person = make_person(self.ext)
        person.status = Status.SUSPENDED
        self.assertFalse(person.can_sign_in)

    def test_the_database_only_lets_a_member_be_a_guest(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_person(self.ext, role=Role.COORDINATOR, person_type=PersonType.GUEST)

    def test_create_superuser_is_a_member_account(self):
        self.assertEqual(make_super_admin().person_type, PersonType.MEMBER_ACCOUNT)


class StatusChangeTests(TestCase):
    def setUp(self):
        self.admin = make_super_admin()
        self.ext = Extension.objects.create(name="Cabanatuan")

    def test_restore_always_goes_back_to_active(self):
        person = make_person(self.ext, must_change_password=True)
        svc.archive_user(self.admin, None, person)
        svc.restore_user(self.admin, None, person)
        person = fresh(person)
        self.assertEqual(person.status, Status.ACTIVE)
        self.assertTrue(person.must_change_password)        # shown as a tag, never a status

    def test_a_reset_leaves_the_status_alone(self):
        person = make_person(self.ext)
        svc.suspend_user(self.admin, None, person)
        svc.reset_password(self.admin, None, person, token=new_submission_token())
        person = fresh(person)
        self.assertEqual(person.status, Status.SUSPENDED)
        self.assertTrue(person.must_change_password)

    def test_the_reset_audit_row_uses_the_new_flag_name(self):
        from apps.audit.models import AuditLog
        person = make_person(self.ext)
        svc.reset_password(self.admin, None, person, token=new_submission_token())
        row = AuditLog.objects.get(action="account.password_reset")
        self.assertEqual(row.after.get("needs_change"), True)
        self.assertNotIn("needs_activation", row.after)

    def test_a_person_without_an_account_has_no_password_to_reset(self):
        record = make_person(self.ext, person_type=PersonType.MEMBER_RECORD)
        with self.assertRaises(ServiceError):
            svc.reset_password(self.admin, None, record, token=new_submission_token())

    def test_there_is_no_deactivate(self):
        self.assertFalse(hasattr(svc, "deactivate_user"))
        self.assertFalse(hasattr(perms, "DEACTIVATE"))
        self.assertNotIn("deactivate", svc.BULK_ACTION_NAMES)
        self.assertNotIn("deactivate", perms.allowed_bulk_actions(self.admin))
        self.assertNotIn("deactivate", perms.available_actions(self.admin, make_person(self.ext)))

    def test_reset_is_not_offered_for_someone_without_an_account(self):
        record = make_person(self.ext, person_type=PersonType.MEMBER_RECORD)
        self.assertNotIn(perms.RESET_PASSWORD, perms.available_actions(self.admin, record))


class CreateWithTypeTests(TestCase):
    def setUp(self):
        self.admin = make_super_admin()
        self.ext = Extension.objects.create(name="Cabanatuan")

    def create(self, **overrides):
        data = create_data(self.ext, gender="male", **overrides)
        return svc.create_user(self.admin, None, data, token=new_submission_token())

    def test_the_default_is_a_member_account_who_must_change_their_password(self):
        person = fresh(self.create().user)
        self.assertEqual(person.person_type, PersonType.MEMBER_ACCOUNT)
        self.assertEqual(person.status, Status.ACTIVE)
        self.assertTrue(person.must_change_password)

    def test_a_no_account_member_gets_no_password_and_no_flag(self):
        result = self.create(person_type=PersonType.MEMBER_RECORD)
        person = fresh(result.user)
        self.assertEqual(result.password.kind, "none")
        self.assertFalse(person.has_usable_password())
        self.assertFalse(person.must_change_password)

    def test_only_a_member_can_be_a_guest(self):
        with self.assertRaises(ServiceError):
            self.create(role=Role.COORDINATOR, person_type=PersonType.GUEST)


class SpecialRoleTypeTests(TestCase):
    def setUp(self):
        self.admin = make_super_admin()
        self.ext = Extension.objects.create(name="Cabanatuan")

    def test_a_no_account_member_cannot_hold_a_special_role(self):
        record = make_person(self.ext, person_type=PersonType.MEMBER_RECORD)
        with self.assertRaises(ServiceError):
            special_roles.assign_special_role(self.admin, None, record, "attendance")

    def test_only_members_with_an_account_count_toward_a_limit(self):
        make_person(self.ext, first_name="A")
        make_person(self.ext, first_name="B", person_type=PersonType.GUEST)
        make_person(self.ext, first_name="C", person_type=PersonType.MEMBER_RECORD)
        self.assertEqual(special_roles.member_counts([self.ext.pk]).get(self.ext.pk), 1)

    def test_a_guest_is_never_an_actor(self):
        guest = make_person(self.ext, person_type=PersonType.GUEST)
        self.assertFalse(perms.can_manage_users(guest))
        with self.assertRaises(PermissionDenied):
            perms.require_user_manager(guest)
