"""Tests for bricks 10 and 14: first timer to member conversion, the "visited before" hint and My attendance."""
from datetime import date
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Extension, Gender, Role, SpecialRole, Status, User, UserSpecialRole
from apps.attendance import services
from apps.attendance.age import AgeNetwork
from apps.attendance.models import AttendanceFirstTimer, AttendanceMember
from apps.core.services import ServiceError, new_submission_token

EARLIER = date(2026, 6, 7)
DAY = date(2026, 6, 14)


def person(role, extension=None, first="x", last="tester", complete=True, **extra):
    """An activated person; `complete` gives members a gender and a birth date so their tick needs nothing more."""
    if complete and role == Role.MEMBER:
        extra.setdefault("gender", Gender.MALE)
        extra.setdefault("birth_date", date(1990, 1, 1))
    return User.objects.create_user(
        password="pass-12345", first_name=first, last_name=last, role=role, extension=extension,
        status=Status.ACTIVE, must_change_password=False, **extra,
    )


class ConvertTests(TestCase):
    def setUp(self):
        self.a = Extension.objects.create(name="alpha")
        self.b = Extension.objects.create(name="beta")
        self.coord = person(Role.COORDINATOR, self.a, first="coord")
        self.a.coordinator = self.coord
        self.a.save()
        self.coord_b = person(Role.COORDINATOR, self.b, first="coordb")
        self.admin = person(Role.ADMIN, first="adm")
        seat, _ = SpecialRole.objects.get_or_create(code="attendance", defaults={"name": "Attendance"})
        self.special = person(Role.MEMBER, self.a, first="special")
        UserSpecialRole.objects.create(user=self.special, role=seat, extension=self.a)
        self.regular = person(Role.MEMBER, self.a, first="regular")
        self.sheet = services.create_sheet(self.coord, self.a, DAY, "Sunday Service")
        self.ft = services.add_first_timer(
            self.coord, self.sheet, "Ana", "Lopez", Gender.FEMALE, birth_date=date(2008, 3, 4),
            phone_number="0917 123 4567",
        ).first_timer

    def convert(self, actor=None, ft=None, token=None):
        """Convert with a fresh token unless one is given."""
        return services.convert_first_timer(actor or self.coord, (ft or self.ft).pk, token=token or new_submission_token())

    def upload(self, sheet=None):
        """Tick a regular member (so the sheet is not empty) and upload."""
        sheet = sheet or self.sheet
        services.mark_present(self.coord, sheet, self.regular)
        services.upload_sheet(self.coord, sheet.pk, token=new_submission_token())

    # --- the conversion ---------------------------------------------------
    def test_the_account_gets_the_data_of_the_row(self):
        result = self.convert()
        user = result.user
        self.assertEqual((user.first_name, user.last_name), ("ana", "lopez"))
        self.assertEqual((user.role, user.extension_id), (Role.MEMBER, self.a.pk))
        self.assertEqual((user.gender, user.birth_date), (Gender.FEMALE, date(2008, 3, 4)))
        self.assertEqual(user.phone_number, self.ft.phone_number)
        self.assertTrue(user.must_change_password)
        self.assertTrue(result.password.display)
        self.ft.refresh_from_db()
        self.assertEqual(self.ft.converted_user_id, user.pk)

    def test_no_member_row_is_added_so_nobody_is_counted_twice(self):
        user = self.convert().user
        self.assertFalse(AttendanceMember.objects.filter(attendance=self.sheet, user=user).exists())
        with self.assertRaises(ServiceError):
            services.mark_present(self.coord, self.sheet, user)
        self.upload()
        self.sheet.refresh_from_db()
        self.assertEqual((self.sheet.members_present, self.sheet.first_timers_count, self.sheet.total_present), (1, 1, 2))

    def test_an_admin_may_convert_and_it_works_on_an_uploaded_sheet(self):
        self.upload()
        result = self.convert(actor=self.admin)
        self.assertEqual(result.user.extension_id, self.a.pk)

    def test_a_second_conversion_is_refused(self):
        self.convert()
        with self.assertRaises(ServiceError):
            self.convert()
        self.assertEqual(User.objects.filter(first_name="ana", last_name="lopez").count(), 1)

    def test_a_double_submit_converts_once(self):
        token = new_submission_token()
        first = self.convert(token=token)
        second = self.convert(token=token)
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(User.objects.filter(first_name="ana", last_name="lopez").count(), 1)

    def test_only_the_coordinator_of_that_extension_or_an_admin_may(self):
        for actor in (self.special, self.regular, self.coord_b):
            with self.assertRaises(PermissionDenied, msg=actor):
                self.convert(actor=actor)
        self.ft.refresh_from_db()
        self.assertIsNone(self.ft.converted_user_id)

    def test_someone_with_the_same_name_is_refused_and_nothing_is_created(self):
        person(
            Role.MEMBER, self.b, first="ana", last="lopez",
            birth_date=date(2008, 3, 4),
        )
        before = User.objects.count()
        with self.assertRaises(ServiceError):
            self.convert()
        self.assertEqual(User.objects.count(), before)
        self.ft.refresh_from_db()
        self.assertIsNone(self.ft.converted_user_id)

    def test_the_audit_row_has_ids_only(self):
        with mock.patch.object(services, "log_action") as log:
            user = self.convert().user
        call = log.call_args
        self.assertEqual(call.args[0], "attendance.first_timer_convert")
        self.assertEqual(call.kwargs["after"], {"attendance_id": self.sheet.pk, "user_id": user.pk})
        self.assertNotIn("Ana", repr(call.kwargs))
        self.assertNotIn("Lopez", repr(call.kwargs))

    def test_a_converted_first_timer_cannot_be_removed(self):
        self.convert()
        with self.assertRaises(ServiceError):
            services.remove_first_timer(self.coord, self.sheet, self.ft.pk)
        self.assertTrue(AttendanceFirstTimer.objects.filter(pk=self.ft.pk).exists())

    def test_conversion_response_renders_the_uncached_welcome_slip(self):
        self.client.force_login(self.coord)
        response = self.client.post(
            reverse("coordinator_area:sheet_first_timer_convert", args=[self.sheet.pk]),
            {"first_timer_id": self.ft.pk, "submit_token": new_submission_token()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["welcome_slip"]["account_id"], response.context["member"].account_id)
        self.assertContains(response, "Print 58 mm welcome slip")
        self.assertContains(response, "@page { size: 58mm auto")
        self.assertEqual(response["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")

    # --- visited before ---------------------------------------------------
    def test_a_second_visit_prompts_instead_of_saving(self):
        earlier = services.create_sheet(self.coord, self.a, EARLIER, "Sunday Service")
        old = services.add_first_timer(self.coord, earlier, "Ben", "Cruz", Gender.MALE, AgeNetwork.ADULT).first_timer
        result = services.add_first_timer(self.coord, self.sheet, "Ben", "Cruz", Gender.MALE, AgeNetwork.ADULT)
        self.assertTrue(result.needs_confirmation)
        self.assertEqual(
            result.previous_visits, [{"first_timer_id": old.pk, "sheet_id": earlier.pk, "date": EARLIER}])
        self.assertFalse(AttendanceFirstTimer.objects.filter(attendance=self.sheet, first_name="ben").exists())
        again = services.add_first_timer(
            self.coord, self.sheet, "Ben", "Cruz", Gender.MALE, AgeNetwork.ADULT, confirm_new=True)
        self.assertIsNotNone(again.first_timer)

    def test_the_hint_stays_inside_the_extension_and_skips_converted_people(self):
        other = services.create_sheet(self.coord_b, self.b, EARLIER, "Sunday Service")
        services.add_first_timer(self.coord_b, other, "Cy", "Dela", Gender.MALE, AgeNetwork.ADULT)
        self.assertEqual(services.previous_first_timer_visits(self.a, "Cy", "Dela"), [])
        self.convert()
        self.assertEqual(services.previous_first_timer_visits(self.a, "Ana", "Lopez"), [])

    # --- history ----------------------------------------------------------
    def test_the_first_visit_shows_in_the_new_members_history_once_uploaded(self):
        user = self.convert().user
        self.assertEqual(services.member_attendance_history(user), [])       # a draft is not history
        self.upload()
        history = services.member_attendance_history(user)
        self.assertEqual([(h["date"], h["kind"]) for h in history], [(DAY, "first_visit")])

    def test_history_combines_visits_newest_first_and_drops_reopened_sheets(self):
        user = self.convert().user
        self.upload()
        later = services.create_sheet(self.coord, self.a, date(2026, 6, 21), "Sunday Service")
        services.mark_present(self.coord, later, user, gender=Gender.FEMALE)
        services.upload_sheet(self.coord, later.pk, token=new_submission_token())
        kinds = [(h["date"], h["kind"]) for h in services.member_attendance_history(user)]
        self.assertEqual(kinds, [(date(2026, 6, 21), "present"), (DAY, "first_visit")])
        services.reopen_sheet(self.admin, later.pk, token=new_submission_token())
        kinds = [h["kind"] for h in services.member_attendance_history(user)]
        self.assertEqual(kinds, ["first_visit"])


class MyAttendanceViewTests(TestCase):
    def setUp(self):
        self.a = Extension.objects.create(name="alpha")
        self.coord = person(Role.COORDINATOR, self.a, first="coord")
        self.a.coordinator = self.coord
        self.a.save()
        self.admin = person(Role.ADMIN, first="adm")
        self.me = person(Role.MEMBER, self.a, first="mine")
        self.other = person(Role.MEMBER, self.a, first="zzother")
        self.sheet = services.create_sheet(self.coord, self.a, DAY, "Morning Prayer")
        for who in (self.me, self.other):
            services.mark_present(self.coord, self.sheet, who)
        services.mark_present(self.coord, self.sheet, self.admin, gender=Gender.MALE, age_network=AgeNetwork.ADULT)
        services.upload_sheet(self.coord, self.sheet.pk, token=new_submission_token())
        self.url = reverse("attendance:mine")

    def test_a_member_sees_only_their_own_rows(self):
        self.client.force_login(self.me)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Morning Prayer")
        self.assertNotContains(response, "zzother")

    def test_no_parameter_selects_another_person(self):
        self.client.force_login(self.me)
        response = self.client.get(self.url, {"user": self.other.pk, "user_id": self.other.pk, "id": self.other.pk})
        self.assertEqual(response.context["total"], 1)
        self.assertNotContains(response, "zzother")

    def test_every_role_has_the_page_and_an_admin_marked_present_sees_the_visit(self):
        for who in (self.coord, self.admin):
            self.client.force_login(who)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200, who.role)
        self.assertEqual(response.context["total"], 1)       # the admin's own tick

    def test_a_draft_does_not_appear(self):
        draft = services.create_sheet(self.coord, self.a, date(2026, 6, 21), "Draft Night")
        services.mark_present(self.coord, draft, self.me)
        self.client.force_login(self.me)
        response = self.client.get(self.url)
        self.assertNotContains(response, "Draft Night")

    def test_an_anonymous_visitor_is_sent_to_sign_in(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
