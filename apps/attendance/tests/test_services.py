"""attendance.tests.test_services: brick 4, the draft services.

Run: docker compose exec web python manage.py test apps.attendance.tests.test_services
Users who act are created activated (status="active", must_change_password=False).
"""
from datetime import date

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Extension, PersonType, Role, SpecialRole, Status, User, UserSpecialRole
from apps.audit.models import AuditLog
from apps.core.services import ServiceError

from .. import services
from ..models import Attendance, AttendanceFirstTimer, AttendanceMember
from ..permissions import can_set_taken_by

SHEET_DATE = date(2026, 10, 11)


def make_user(first, last, role=Role.MEMBER, extension=None, **extra):
    """An activated person who can sign in."""
    return User.objects.create_user(
        first_name=first, last_name=last, role=role, extension=extension,
        status=Status.ACTIVE, must_change_password=False, **extra,
    )


class ServiceTestCase(TestCase):
    """An extension with a coordinator, an attendance special member and a plain member."""

    @classmethod
    def setUpTestData(cls):
        cls.ext = Extension.objects.create(name="santa rosa")
        cls.other_ext = Extension.objects.create(name="cabanatuan")
        cls.coordinator = make_user("carla", "cruz", Role.COORDINATOR, cls.ext)
        cls.ext.coordinator = cls.coordinator
        cls.ext.save()
        cls.other_coordinator = make_user("omar", "ocampo", Role.COORDINATOR, cls.other_ext)
        cls.admin = make_user("ana", "admin", Role.ADMIN)
        cls.taker = make_user("tina", "taker", Role.MEMBER, cls.ext)
        cls.plain = make_user("paolo", "plain", Role.MEMBER, cls.ext)
        role, _ = SpecialRole.objects.get_or_create(code="attendance", defaults={"name": "Attendance"})
        UserSpecialRole.objects.create(user=cls.taker, role=role, extension=cls.ext)

    def sheet(self, **kwargs):
        """A draft sheet for the main extension."""
        values = dict(extension=self.ext, attendance_date=SHEET_DATE, label="Sunday Service",
                      status=Attendance.Status.DRAFT)
        values.update(kwargs)
        return Attendance.objects.create(**values)

    def lock(self, sheet):
        """Pretend the upload happened."""
        Attendance.objects.filter(pk=sheet.pk).update(status=Attendance.Status.UPLOADED)
        sheet.refresh_from_db()


class CreateSheetTests(ServiceTestCase):
    def test_coordinator_creates_a_draft_and_it_is_logged(self):
        sheet = services.create_sheet(self.coordinator, self.ext, SHEET_DATE, "  Sunday   Service ")
        self.assertEqual(sheet.status, Attendance.Status.DRAFT)
        self.assertEqual(sheet.label, "Sunday Service")
        self.assertEqual((sheet.created_by, sheet.taken_by), (self.coordinator, self.coordinator))
        entry = AuditLog.objects.get(action="attendance.create")
        self.assertEqual(entry.entity_id, sheet.pk)

    def test_special_member_may_create_in_own_extension_only(self):
        services.create_sheet(self.taker, self.ext, SHEET_DATE)
        with self.assertRaises(PermissionDenied):
            services.create_sheet(self.taker, self.other_ext, SHEET_DATE)

    def test_other_extensions_coordinator_and_plain_member_refused(self):
        for actor in (self.other_coordinator, self.plain):
            with self.assertRaises(PermissionDenied):
                services.create_sheet(actor, self.ext, SHEET_DATE)

    def test_admin_may_create_in_any_extension(self):
        sheet = services.create_sheet(self.admin, self.other_ext, SHEET_DATE)
        self.assertEqual(sheet.extension, self.other_ext)

    def test_repeat_is_a_friendly_error_even_with_other_capitals(self):
        services.create_sheet(self.coordinator, self.ext, SHEET_DATE, "Sunday Service")
        with self.assertRaises(ServiceError):
            services.create_sheet(self.coordinator, self.ext, SHEET_DATE, "sunday service")
        self.assertEqual(Attendance.objects.count(), 1)

    def test_blank_label_gets_the_default_and_archived_extension_is_refused(self):
        self.assertEqual(services.create_sheet(self.coordinator, self.ext, SHEET_DATE, "").label, "Sunday Service")
        Extension.objects.filter(pk=self.other_ext.pk).update(archived_at=timezone.now())
        with self.assertRaises(ServiceError):
            services.create_sheet(self.admin, self.other_ext, SHEET_DATE)

    def test_audit_row_holds_no_person_data(self):
        services.create_sheet(self.coordinator, self.ext, SHEET_DATE)
        entry = AuditLog.objects.get(action="attendance.create")
        self.assertEqual(set(entry.after), {"attendance_date", "label"})


class TakenByTests(ServiceTestCase):
    def test_coordinator_picks_a_special_member(self):
        sheet = self.sheet(taken_by=self.coordinator)
        self.assertTrue(services.set_taken_by(self.coordinator, sheet, self.taker))
        sheet.refresh_from_db()
        self.assertEqual(sheet.taken_by, self.taker)
        self.assertFalse(services.set_taken_by(self.coordinator, sheet, self.taker))

    def test_a_plain_member_or_other_extension_person_cannot_be_chosen(self):
        sheet = self.sheet()
        for person in (self.plain, self.other_coordinator, self.admin):
            with self.assertRaises(ServiceError):
                services.set_taken_by(self.coordinator, sheet, person)

    def test_special_member_may_not_change_it(self):
        sheet = self.sheet()
        with self.assertRaises(PermissionDenied):
            services.set_taken_by(self.taker, sheet, self.coordinator)
        self.assertFalse(can_set_taken_by(self.taker, sheet))
        self.assertTrue(can_set_taken_by(self.coordinator, sheet))
        self.assertTrue(can_set_taken_by(self.admin, sheet))

    def test_locked_sheet_refused(self):
        sheet = self.sheet()
        self.lock(sheet)
        with self.assertRaises(ServiceError):
            services.set_taken_by(self.admin, sheet, self.taker)


class SearchPeopleTests(ServiceTestCase):
    def test_short_text_returns_nothing(self):
        self.assertEqual(services.search_people(self.coordinator, self.sheet(), "ca"), [])

    def test_finds_visitors_admins_and_hides_archived_and_suspended(self):
        visitor = make_user("victor", "visitor", Role.MEMBER, self.other_ext, person_type=PersonType.GUEST)
        archived = make_user("victoria", "gone", Role.MEMBER, self.other_ext)
        suspended = make_user("victoria", "held", Role.MEMBER, self.other_ext)
        # the database ties status archived to archived_at, so set both
        User.objects.filter(pk=archived.pk).update(status=Status.ARCHIVED, archived_at=timezone.now())
        User.objects.filter(pk=suspended.pk).update(status=Status.SUSPENDED)
        sheet = self.sheet()
        found = services.search_people(self.coordinator, sheet, "vict")
        self.assertEqual([row["id"] for row in found], [visitor.pk])
        self.assertEqual(found[0]["home_extension"], "Cabanatuan")
        admin_row = services.search_people(self.coordinator, sheet, "ana adm")[0]
        self.assertEqual(admin_row["home_extension"], services.NO_EXTENSION_LABEL)

    def test_returns_limited_fields_and_at_most_twenty(self):
        for number in range(25):
            make_user("sample", f"person{number}", Role.MEMBER, self.ext, birth_date=date(1990, 1, 1))
        found = services.search_people(self.coordinator, self.sheet(), "sample")
        self.assertEqual(len(found), 20)
        self.assertEqual(set(found[0]), {"id", "name", "person_type", "home_extension", "age_network"})

    def test_needs_the_right_to_edit_the_draft(self):
        sheet = self.sheet()
        with self.assertRaises(PermissionDenied):
            services.search_people(self.plain, sheet, "carla")
        with self.assertRaises(PermissionDenied):
            services.search_people(self.other_coordinator, sheet, "carla")
        self.lock(sheet)
        with self.assertRaises(ServiceError):
            services.search_people(self.admin, sheet, "carla")


class MarkPresentTests(ServiceTestCase):
    def test_visitor_can_be_marked(self):
        visitor = make_user("vera", "visitor", Role.MEMBER, self.other_ext)
        row, _ = services.mark_present(self.taker, self.sheet(), visitor)
        self.assertEqual(row.home_extension, self.other_ext)
        self.assertEqual(row.home_extension_name, "Cabanatuan")

    def test_admin_has_no_home_extension(self):
        row, _ = services.mark_present(self.coordinator, self.sheet(), self.admin)
        self.assertIsNone(row.home_extension)
        self.assertEqual(row.home_extension_name, "")

    def test_ticking_twice_keeps_one_row(self):
        sheet = self.sheet()
        services.mark_present(self.coordinator, sheet, self.plain)
        row, created = services.mark_present(self.coordinator, sheet, self.plain, gender="male")
        self.assertFalse(created)
        self.assertEqual(AttendanceMember.objects.filter(attendance=sheet).count(), 1)
        self.assertEqual(row.gender, "")  # the second tick changed nothing

    def test_missing_category_is_supplied_by_the_taker_and_stays_off_the_user(self):
        sheet = self.sheet()
        row, _ = services.mark_present(self.coordinator, sheet, self.plain, gender="male", age_network="youth")
        self.assertEqual((row.gender, row.age_network), ("male", "youth"))
        self.plain.refresh_from_db()
        self.assertEqual(self.plain.gender, "")
        self.assertIsNone(self.plain.birth_date)

    def test_missing_category_leaves_blanks_until_set(self):
        sheet = self.sheet()
        row, _ = services.mark_present(self.coordinator, sheet, self.plain)
        self.assertEqual((row.gender, row.age_network), ("", ""))
        row = services.set_member_category(self.coordinator, sheet, row.pk, gender="male", age_network="adult")
        self.assertEqual((row.gender, row.age_network), ("male", "adult"))

    def test_own_record_wins_over_what_the_taker_supplies(self):
        person = make_user("gina", "given", Role.MEMBER, self.ext, gender="female")
        row, _ = services.mark_present(self.coordinator, self.sheet(), person, gender="male")
        self.assertEqual(row.gender, "female")

    def test_invalid_choice_and_blocked_people_refused(self):
        sheet = self.sheet()
        with self.assertRaises(ServiceError):
            services.mark_present(self.coordinator, sheet, self.plain, gender="other")
        User.objects.filter(pk=self.plain.pk).update(status=Status.SUSPENDED)
        with self.assertRaises(ServiceError):
            services.mark_present(self.coordinator, sheet, self.plain)

    def test_set_member_category_needs_a_value_and_a_valid_one(self):
        sheet = self.sheet()
        row, _ = services.mark_present(self.coordinator, sheet, self.plain)
        with self.assertRaises(ServiceError):
            services.set_member_category(self.coordinator, sheet, row.pk)
        with self.assertRaises(ServiceError):
            services.set_member_category(self.coordinator, sheet, row.pk, age_network="elder")

    def test_unmark(self):
        sheet = self.sheet()
        row, _ = services.mark_present(self.coordinator, sheet, self.plain)
        self.assertTrue(services.unmark_present(self.coordinator, sheet, row.pk))
        self.assertFalse(services.unmark_present(self.coordinator, sheet, row.pk))

    def test_other_extensions_special_member_and_plain_member_refused(self):
        sheet = self.sheet()
        for actor in (self.plain, self.other_coordinator):
            with self.assertRaises(PermissionDenied):
                services.mark_present(actor, sheet, self.admin)


class FirstTimerTests(ServiceTestCase):
    def add(self, sheet, **kwargs):
        values = dict(first_name="Juan", last_name="Dela Cruz", gender="male", age_network="adult")
        values.update(kwargs)
        return services.add_first_timer(self.coordinator, sheet, **values)

    def test_saves_names_lowercase_and_shows_name_case(self):
        result = self.add(self.sheet(), first_name="  JUAN ", middle_name="Santos", suffix="Jr.")
        row = result.first_timer
        self.assertFalse(result.needs_confirmation)
        self.assertEqual((row.first_name, row.suffix), ("juan", "jr"))
        self.assertEqual(row.full_name, "Juan Santos Dela Cruz Jr.")
        self.assertEqual(row.guest.person_type, PersonType.GUEST)

    def test_birth_date_decides_the_age_network(self):
        row = self.add(self.sheet(), age_network="youth", birth_date=date(1990, 10, 12)).first_timer
        self.assertEqual(row.age_network, "young_adult")

    def test_birth_date_after_the_sheet_date_refused(self):
        with self.assertRaises(ServiceError):
            self.add(self.sheet(), birth_date=date(2027, 1, 1))

    def test_required_fields(self):
        sheet = self.sheet()
        for bad in (dict(first_name=" "), dict(last_name=""), dict(gender=None), dict(age_network=None)):
            with self.assertRaises(ServiceError):
                self.add(sheet, **bad)
        self.assertEqual(AttendanceFirstTimer.objects.count(), 0)

    def test_same_person_prompt_saves_nothing_until_confirmed(self):
        make_user("juan", "dela cruz", Role.MEMBER, self.other_ext, birth_date=date(1980, 1, 1))
        sheet = self.sheet()
        result = self.add(sheet)
        self.assertTrue(result.needs_confirmation)
        self.assertEqual(len(result.matches), 1)
        self.assertEqual(result.matches[0]["home_extension"], "Cabanatuan")
        self.assertEqual(AttendanceFirstTimer.objects.count(), 0)
        result = self.add(sheet, confirm_new=True)
        self.assertIsNotNone(result.first_timer)

    def test_different_birth_dates_do_not_match(self):
        make_user("juan", "dela cruz", Role.MEMBER, self.ext, birth_date=date(1980, 1, 1))
        result = self.add(self.sheet(), birth_date=date(1999, 5, 5))
        self.assertFalse(result.needs_confirmation)

    def test_matches_return_limited_fields(self):
        make_user("juan", "dela cruz", Role.MEMBER, self.ext, phone_number="09171234567")
        matches = services.find_possible_matches("Juan", "Dela Cruz")
        self.assertEqual(set(matches[0]), {"id", "name", "home_extension", "age_network", "archived"})

    def test_the_same_first_timer_twice_on_one_sheet_refused(self):
        sheet = self.sheet()
        self.add(sheet)
        with self.assertRaises(ServiceError):
            self.add(sheet, first_name="JUAN", confirm_new=True)

    def test_update_and_remove(self):
        sheet = self.sheet()
        row = self.add(sheet).first_timer
        row = services.update_first_timer(self.coordinator, sheet, row.pk, {"first_name": "Jose", "gender": "male"})
        self.assertEqual(row.first_name, "jose")
        with self.assertRaises(ServiceError):
            services.update_first_timer(self.coordinator, sheet, row.pk, {"last_name": ""})
        self.assertTrue(services.remove_first_timer(self.coordinator, sheet, row.pk))
        self.assertFalse(services.remove_first_timer(self.coordinator, sheet, row.pk))

    def test_special_member_may_add_but_a_plain_member_may_not(self):
        sheet = self.sheet()
        services.add_first_timer(self.taker, sheet, "Ana", "Reyes", "female", "youth")
        with self.assertRaises(PermissionDenied):
            services.add_first_timer(self.plain, sheet, "Ben", "Reyes", "male", "youth")


class LockedSheetTests(ServiceTestCase):
    def test_a_locked_sheet_refuses_every_action(self):
        sheet = self.sheet()
        row, _ = services.mark_present(self.coordinator, sheet, self.plain)
        first_timer = services.add_first_timer(
            self.coordinator, sheet, "Juan", "Reyes", "male", "adult").first_timer
        self.lock(sheet)
        actions = [
            lambda: services.set_taken_by(self.admin, sheet, self.taker),
            lambda: services.search_people(self.admin, sheet, "carla"),
            lambda: services.mark_present(self.admin, sheet, self.taker),
            lambda: services.unmark_present(self.admin, sheet, row.pk),
            lambda: services.set_member_category(self.admin, sheet, row.pk, gender="male"),
            lambda: services.add_first_timer(self.admin, sheet, "Ben", "Reyes", "male", "adult"),
            lambda: services.update_first_timer(self.admin, sheet, first_timer.pk, {"first_name": "x"}),
            lambda: services.remove_first_timer(self.admin, sheet, first_timer.pk),
        ]
        for action in actions:
            with self.assertRaises(ServiceError):
                action()
        self.assertEqual(AttendanceMember.objects.filter(attendance=sheet).count(), 1)
        self.assertEqual(AttendanceFirstTimer.objects.filter(attendance=sheet).count(), 1)

    def test_a_stale_copy_of_the_sheet_cannot_get_around_the_lock(self):
        stale = self.sheet()  # still says "draft" in memory
        Attendance.objects.filter(pk=stale.pk).update(status=Attendance.Status.UPLOADED)
        with self.assertRaises(ServiceError):
            services.mark_present(self.coordinator, stale, self.plain)
