"""Tests for the A4 print page (brick 9): who may open it, what it shows, and what it never shows."""
from datetime import date, datetime, timezone

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Extension, Role, User, UserSpecialRole

from apps.attendance.models import Attendance, AttendanceFirstTimer, AttendanceMember
from apps.attendance.summary import build_summary

SHEET_DATE = date(2026, 9, 27)


def make_user(role, extension, first, last="tester", **extra):
    """An ACTIVATED person (status active, no forced password change) so a test can sign them in."""
    return User.objects.create_user(
        first_name=first, last_name=last, role=role, extension=extension,
        status="active", must_change_password=False, password="pw-for-tests-1", **extra,
    )


class PrintPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.soledad = Extension.objects.create(name="soledad")
        cls.other = Extension.objects.create(name="poblacion")
        cls.coordinator = make_user(Role.COORDINATOR, cls.soledad, "carla")
        cls.soledad.coordinator = cls.coordinator
        cls.soledad.save()
        cls.other_coordinator = make_user(Role.COORDINATOR, cls.other, "olga")
        cls.admin = make_user(Role.ADMIN, None, "adam")
        cls.super_admin = make_user(Role.SUPER_ADMIN, None, "sue")
        cls.plain_member = make_user(Role.MEMBER, cls.soledad, "mario")
        cls.seat_member = make_user(Role.MEMBER, cls.soledad, "sam")
        UserSpecialRole.objects.create(user=cls.seat_member, role_id="attendance", extension=cls.soledad)

        cls.draft = cls._sheet("Sunday Service")
        cls.uploaded = cls._sheet("Evening Service", uploaded=True)

    @classmethod
    def _sheet(cls, label, uploaded=False):
        """A sheet with two members (one a visitor) and a first timer; uploaded ones are frozen."""
        sheet = Attendance.objects.create(
            extension=cls.soledad, attendance_date=SHEET_DATE, label=label,
            created_by=cls.coordinator, taken_by=cls.seat_member,
        )
        AttendanceMember.objects.create(
            attendance=sheet, user=cls.plain_member, member_name="Mario Tester",
            gender="male", age_network="adult",
            home_extension=cls.soledad, home_extension_name="soledad",
        )
        AttendanceMember.objects.create(
            attendance=sheet, user=cls.other_coordinator, member_name="Olga Tester",
            gender="female", age_network="youth",
            home_extension=cls.other, home_extension_name="poblacion",
        )
        AttendanceFirstTimer.objects.create(
            attendance=sheet, first_name="fina", last_name="newcomer", gender="female",
            age_network="children", phone_number="09171234567", birth_date=date(2018, 3, 14),
        )
        if uploaded:
            numbers = build_summary(sheet)
            sheet.members_present = numbers.members_present
            sheet.first_timers_count = numbers.first_timers_count
            sheet.total_present = numbers.total_present
            sheet.summary = numbers.summary
            sheet.uploaded_by = cls.coordinator
            sheet.uploaded_at = datetime(2026, 9, 27, 5, 0, tzinfo=timezone.utc)
            sheet.status = Attendance.Status.UPLOADED
            sheet.save()
        return sheet

    def url(self, namespace, sheet):
        return reverse(f"{namespace}:sheet_print", kwargs={"pk": sheet.pk})

    def get(self, user, namespace, sheet):
        self.client.force_login(user)
        return self.client.get(self.url(namespace, sheet))

    # ---------------------------------------------------------------- renders
    def test_draft_renders_with_the_draft_line_and_no_uploader(self):
        response = self.get(self.coordinator, "coordinator_area", self.draft)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "attendance/print.html")
        self.assertContains(response, "DRAFT, not uploaded")
        self.assertNotContains(response, "Uploaded by:")

    def test_uploaded_sheet_shows_the_taker_and_the_uploader(self):
        response = self.get(self.coordinator, "coordinator_area", self.uploaded)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Taken by:")
        self.assertContains(response, "Sam Tester")        # taken_by
        self.assertContains(response, "Uploaded by:")
        self.assertContains(response, "Carla Tester")      # uploaded_by
        self.assertNotContains(response, "DRAFT, not uploaded")

    def test_admin_and_super_admin_can_print_any_sheet(self):
        for user, namespace in ((self.admin, "admin_area"), (self.super_admin, "superadmin")):
            with self.subTest(role=user.role):
                self.assertEqual(self.get(user, namespace, self.uploaded).status_code, 200)

    # ------------------------------------------------------------------ rows
    def test_ticks_land_in_the_right_columns_and_first_timers_come_last(self):
        response = self.get(self.coordinator, "coordinator_area", self.uploaded)
        rows = response.context["rows"]
        self.assertEqual([row["name"] for row in rows], ["Mario Tester", "Olga Tester", "Fina Newcomer"])
        self.assertEqual([row["number"] for row in rows], [1, 2, 3])
        # cells: men adult, ya, youth, children, then women adult, ya, youth, children
        self.assertEqual(rows[0]["cells"].index(True), 0)   # male adult
        self.assertEqual(rows[1]["cells"].index(True), 6)   # female youth
        self.assertEqual(rows[2]["cells"].index(True), 7)   # female children
        self.assertEqual([row["first_timer"] for row in rows], [False, False, True])

    def test_belongs_to_is_shown_for_a_visitor_only(self):
        rows = self.get(self.coordinator, "coordinator_area", self.uploaded).context["rows"]
        self.assertEqual(rows[0]["belongs"], "")            # from this extension
        self.assertEqual(rows[1]["belongs"], "poblacion")   # a visitor
        self.assertEqual(rows[2]["belongs"], "")

    def test_a_long_list_prints_every_name_with_one_repeating_header(self):
        sheet = Attendance.objects.create(extension=self.soledad, attendance_date=SHEET_DATE, label="Long")
        AttendanceMember.objects.bulk_create([
            AttendanceMember(
                attendance=sheet, member_name=f"Person {number:03d}", gender="male", age_network="adult",
                home_extension=self.soledad, home_extension_name="soledad",
            )
            for number in range(1, 71)
        ])
        response = self.get(self.coordinator, "coordinator_area", sheet)
        self.assertEqual(len(response.context["rows"]), 70)
        self.assertContains(response, "Person 070")
        self.assertContains(response, "<thead>", count=2)   # the grid's and the summary's, once each
        self.assertContains(response, "display: table-header-group")

    def test_an_uploaded_sheet_prints_its_frozen_numbers(self):
        response = self.get(self.coordinator, "coordinator_area", self.uploaded)
        self.assertEqual(response.context["summary"].total_present, 3)
        self.assertEqual(response.context["table"]["total"], 3)

    # --------------------------------------------------------------- privacy
    def test_no_birth_date_phone_or_address_reaches_the_page(self):
        html = self.get(self.coordinator, "coordinator_area", self.uploaded).content.decode()
        for private in ("09171234567", "2018-03-14", "14 Mar 2018", "March 14, 2018", "birth"):
            with self.subTest(private=private):
                self.assertNotIn(private, html.lower() if private == "birth" else html)

    # ---------------------------------------------------------------- access
    def test_a_special_member_prints_drafts_only(self):
        self.assertEqual(self.get(self.seat_member, "attendance", self.draft).status_code, 200)
        response = self.get(self.seat_member, "attendance", self.uploaded)
        self.assertEqual(response.status_code, 302)
        self.assertTemplateNotUsed(response, "attendance/print.html")

    def test_a_member_without_the_seat_is_refused(self):
        response = self.get(self.plain_member, "attendance", self.draft)
        self.assertEqual(response.status_code, 302)

    def test_a_coordinator_of_another_extension_gets_nothing(self):
        response = self.get(self.other_coordinator, "coordinator_area", self.uploaded)
        self.assertEqual(response.status_code, 302)
        self.assertTemplateNotUsed(response, "attendance/print.html")

    def test_the_wrong_area_for_the_role_is_refused(self):
        response = self.get(self.coordinator, "admin_area", self.uploaded)
        self.assertEqual(response.status_code, 302)

    def test_anonymous_visitor_is_sent_to_sign_in(self):
        response = self.client.get(self.url("coordinator_area", self.uploaded))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_print_is_get_only_and_changes_nothing(self):
        self.client.force_login(self.coordinator)
        response = self.client.post(self.url("coordinator_area", self.draft))
        self.assertEqual(response.status_code, 405)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, Attendance.Status.DRAFT)

    # ------------------------------------------------------ review integration
    def test_the_review_page_offers_the_print_button(self):
        self.client.force_login(self.coordinator)
        review = reverse("coordinator_area:sheet_review", kwargs={"pk": self.uploaded.pk})
        self.assertContains(self.client.get(review), self.url("coordinator_area", self.uploaded))
