"""Smoke tests for the attendance screens (brick 8): who reaches which page, scoping, the filters, the create form.

Every signed-in user is created ACTIVATED (`status="active"`, `must_change_password=False`), or the activation
middleware redirects every request. Nothing here calls an outside service.
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.decorators import home_url_for
from apps.accounts.models import Extension, Gender, Role, SpecialRole, Status, User, UserSpecialRole
from apps.accounts.navigation import build_nav
from apps.attendance import services
from apps.attendance.models import Attendance

DAY = date(2026, 6, 14)

# The URL namespace each role works in (see attendance.views.AREA_ROLES).
AREAS = {
    Role.SUPER_ADMIN: "superadmin",
    Role.ADMIN: "admin_area",
    Role.COORDINATOR: "coordinator_area",
    Role.MEMBER: "attendance",
}


def person(role, extension=None, first="x", complete=True, **extra):
    """An activated person; `complete` gives members a gender and a birth date."""
    if complete and role == Role.MEMBER:
        extra.setdefault("gender", Gender.MALE)
        extra.setdefault("birth_date", date(1990, 1, 1))
    return User.objects.create_user(
        password="pass-12345", first_name=first, last_name="tester", role=role, extension=extension,
        status=Status.ACTIVE, must_change_password=False, **extra,
    )


class ScreenTestCase(TestCase):
    """People, two extensions and a few sheets shared by the screen tests."""

    def setUp(self):
        self.a = Extension.objects.create(name="alpha")
        self.b = Extension.objects.create(name="beta")
        self.coord = person(Role.COORDINATOR, self.a, first="coord")
        self.a.coordinator = self.coord
        self.a.save()
        self.coord_b = person(Role.COORDINATOR, self.b, first="coordb")
        self.admin = person(Role.ADMIN, first="adm")
        self.superadmin = person(Role.SUPER_ADMIN, first="sup")
        seat, _ = SpecialRole.objects.get_or_create(code="attendance", defaults={"name": "Attendance"})
        self.special = person(Role.MEMBER, self.a, first="special")
        UserSpecialRole.objects.create(user=self.special, role=seat, extension=self.a)
        self.plain = person(Role.MEMBER, self.a, first="plain")

        self.draft_a = services.create_sheet(self.coord, self.a, DAY, "Sunday Service")
        self.draft_b = services.create_sheet(self.coord_b, self.b, DAY, "Sunday Service")

    def url(self, user, name, *args):
        """Reverse `name` in the area that belongs to `user`'s role."""
        return reverse(f"{AREAS[user.role]}:{name}", args=args)

    def get(self, user, name, *args, **params):
        """Sign `user` in and GET one of their area's pages."""
        self.client.force_login(user)
        return self.client.get(self.url(user, name, *args), params)

    def mark_uploaded(self, sheet):
        """Make a sheet look uploaded without going through the upload service (this file tests screens)."""
        Attendance.objects.filter(pk=sheet.pk).update(
            status=Attendance.Status.UPLOADED, members_present=2, first_timers_count=1, total_present=3,
            summary={"first_timers": {"female": {"adult": 1}}, "members": {"male": {"adult": 2}}},
        )
        sheet.refresh_from_db()
        return sheet


class AccessTests(ScreenTestCase):
    def test_each_role_with_access_opens_the_list_in_its_own_area(self):
        for user in (self.coord, self.admin, self.superadmin, self.special):
            response = self.get(user, "sheet_list")
            self.assertEqual(response.status_code, 200, user.role)
            self.assertTemplateUsed(response, "attendance/sheet_list.html")

    def test_a_member_without_the_seat_is_refused_not_just_hidden(self):
        response = self.get(self.plain, "sheet_list")
        self.assertRedirects(response, home_url_for(self.plain), fetch_redirect_response=False)
        self.client.force_login(self.plain)
        response = self.client.post(self.url(self.plain, "sheet_create"), {
            "extension": self.a.pk, "attendance_date": "2026-06-21", "label": "Sunday Service",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Attendance.objects.count(), 2)

    def test_the_wrong_area_sends_a_person_home(self):
        self.client.force_login(self.coord)
        for namespace in ("admin_area", "superadmin", "attendance"):
            response = self.client.get(reverse(f"{namespace}:sheet_list"))
            self.assertRedirects(response, home_url_for(self.coord), fetch_redirect_response=False)
        self.client.force_login(self.special)
        response = self.client.get(reverse("coordinator_area:sheet_list"))
        self.assertRedirects(response, home_url_for(self.special), fetch_redirect_response=False)

    def test_a_signed_out_visitor_is_sent_to_sign_in(self):
        response = self.client.get(reverse("coordinator_area:sheet_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_the_navigation_has_a_working_attendance_entry_per_role(self):
        for user in (self.coord, self.admin, self.superadmin, self.plain, self.special):
            nav = build_nav(user.role, "/")
            entries = [e for s in nav["sections"] for e in s["items"] if e["label"] == "Attendance"]
            self.assertEqual(len(entries), 1, user.role)
            self.assertFalse(entries[0]["disabled"], user.role)
            self.assertEqual(entries[0]["href"], reverse(f"{AREAS[user.role]}:sheet_list"))


class ListTests(ScreenTestCase):
    def sheets(self, response):
        return [s.pk for s in response.context["page"].object_list]

    def test_a_coordinator_sees_only_their_own_extension(self):
        response = self.get(self.coord, "sheet_list")
        self.assertEqual(self.sheets(response), [self.draft_a.pk])

    def test_a_special_member_sees_drafts_of_their_extension_only(self):
        uploaded = self.mark_uploaded(
            services.create_sheet(self.coord, self.a, date(2026, 6, 7), "Sunday Service"))
        response = self.get(self.special, "sheet_list")
        self.assertEqual(self.sheets(response), [self.draft_a.pk])
        self.assertNotIn(uploaded.pk, self.sheets(response))

    def test_an_admin_sees_everything_and_can_filter_by_extension(self):
        response = self.get(self.admin, "sheet_list")
        self.assertCountEqual(self.sheets(response), [self.draft_a.pk, self.draft_b.pk])
        response = self.get(self.admin, "sheet_list", extension=self.b.pk)
        self.assertEqual(self.sheets(response), [self.draft_b.pk])
        self.assertEqual(response.context["active_filters"], 1)

    def test_a_coordinator_has_no_extension_filter(self):
        response = self.get(self.coord, "sheet_list", extension=self.b.pk)
        self.assertNotIn("extension", response.context["form"].fields)
        self.assertEqual(self.sheets(response), [self.draft_a.pk])

    def test_status_and_date_filters(self):
        uploaded = self.mark_uploaded(
            services.create_sheet(self.coord, self.a, date(2026, 6, 7), "Sunday Service"))
        everything = self.sheets(self.get(self.coord, "sheet_list"))
        self.assertEqual(everything, [self.draft_a.pk, uploaded.pk])           # newest date first
        self.assertEqual(self.sheets(self.get(self.coord, "sheet_list", status="uploaded")), [uploaded.pk])
        self.assertEqual(self.sheets(self.get(self.coord, "sheet_list", status="draft")), [self.draft_a.pk])
        self.assertEqual(self.sheets(self.get(self.coord, "sheet_list", date_to="2026-06-10")), [uploaded.pk])
        self.assertEqual(self.sheets(self.get(self.coord, "sheet_list", date_from="2026-06-10")), [self.draft_a.pk])

    def test_a_backwards_date_range_ignores_the_end_date(self):
        response = self.get(self.coord, "sheet_list", date_from="2026-06-20", date_to="2026-06-01")
        self.assertIn("date_to", response.context["form"].errors)
        self.assertEqual(self.sheets(response), [])                            # from 20 June onward: nothing

    def test_rows_show_frozen_numbers_when_uploaded_and_live_numbers_for_a_draft(self):
        member = person(Role.MEMBER, self.a, first="one")
        services.mark_present(self.coord, self.draft_a, member)
        uploaded = self.mark_uploaded(
            services.create_sheet(self.coord, self.a, date(2026, 6, 7), "Sunday Service"))
        rows = {s.pk: s for s in self.get(self.coord, "sheet_list").context["page"].object_list}
        self.assertEqual((rows[self.draft_a.pk].members_shown, rows[self.draft_a.pk].total_shown), (1, 1))
        self.assertEqual((rows[uploaded.pk].members_shown, rows[uploaded.pk].total_shown), (2, 3))

    def test_new_sheet_button_follows_the_right_to_create(self):
        self.assertTrue(self.get(self.coord, "sheet_list").context["can_create"])
        self.assertTrue(self.get(self.special, "sheet_list").context["can_create"])


class CreateTests(ScreenTestCase):
    def post(self, user, **data):
        self.client.force_login(user)
        return self.client.post(self.url(user, "sheet_create"), data)

    def test_the_form_starts_with_today_in_manila_and_sunday_service(self):
        form = self.get(self.coord, "sheet_create").context["form"]
        self.assertEqual(form["attendance_date"].value(), timezone.localdate())
        self.assertEqual(form["label"].value(), "Sunday Service")

    def test_a_coordinator_creates_a_sheet_for_their_own_extension(self):
        response = self.post(self.coord, extension=self.a.pk, attendance_date="2026-06-21", label="Sunday  Service ")
        sheet = Attendance.objects.get(attendance_date=date(2026, 6, 21))
        self.assertRedirects(response, self.url(self.coord, "sheet_detail", sheet.pk))
        self.assertEqual((sheet.extension, sheet.label, sheet.status), (self.a, "Sunday Service", "draft"))
        self.assertEqual((sheet.created_by, sheet.taken_by), (self.coord, self.coord))

    def test_the_hidden_extension_cannot_be_changed_to_another_extension(self):
        response = self.post(self.coord, extension=self.b.pk, attendance_date="2026-06-21", label="Sunday Service")
        self.assertEqual(response.status_code, 200)
        self.assertIn("extension", response.context["form"].errors)
        self.assertEqual(Attendance.objects.count(), 2)

    def test_a_missing_hidden_extension_falls_back_to_the_only_allowed_one(self):
        self.post(self.coord, attendance_date="2026-06-21", label="Sunday Service")
        self.assertEqual(Attendance.objects.get(attendance_date=date(2026, 6, 21)).extension, self.a)

    def test_the_same_extension_date_and_label_is_refused_kindly(self):
        response = self.post(self.coord, extension=self.a.pk, attendance_date="2026-06-14", label="sunday service")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertEqual(Attendance.objects.filter(extension=self.a).count(), 1)

    def test_a_special_member_creates_for_their_extension(self):
        self.post(self.special, extension=self.a.pk, attendance_date="2026-06-21", label="Midweek")
        sheet = Attendance.objects.get(label="Midweek")
        self.assertEqual((sheet.extension, sheet.taken_by), (self.a, self.special))

    def test_an_admin_must_choose_an_extension_and_may_choose_any(self):
        response = self.post(self.admin, attendance_date="2026-06-21", label="Sunday Service")
        self.assertEqual(response.status_code, 200)
        self.assertIn("extension", response.context["form"].errors)
        self.post(self.admin, extension=self.b.pk, attendance_date="2026-06-21", label="Sunday Service")
        self.assertTrue(Attendance.objects.filter(extension=self.b, attendance_date=date(2026, 6, 21)).exists())

    def test_bad_input_shows_errors_and_saves_nothing(self):
        response = self.post(self.coord, extension=self.a.pk, attendance_date="not a date", label="   ")
        self.assertEqual(response.status_code, 200)
        errors = response.context["form"].errors
        self.assertIn("attendance_date", errors)
        self.assertIn("label", errors)
        self.assertEqual(Attendance.objects.count(), 2)


class DetailTests(ScreenTestCase):
    def test_a_coordinator_opens_their_own_sheet(self):
        response = self.get(self.coord, "sheet_detail", self.draft_a.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sheet"], self.draft_a)

    def test_a_sheet_of_another_extension_looks_like_it_does_not_exist(self):
        response = self.get(self.coord, "sheet_detail", self.draft_b.pk)
        self.assertRedirects(response, self.url(self.coord, "sheet_list"), fetch_redirect_response=False)
        response = self.get(self.coord, "sheet_detail", 999999)
        self.assertRedirects(response, self.url(self.coord, "sheet_list"), fetch_redirect_response=False)

    def test_a_special_member_reaches_drafts_only(self):
        self.assertEqual(self.get(self.special, "sheet_detail", self.draft_a.pk).status_code, 200)
        uploaded = self.mark_uploaded(
            services.create_sheet(self.coord, self.a, date(2026, 6, 7), "Sunday Service"))
        response = self.get(self.special, "sheet_detail", uploaded.pk)
        self.assertRedirects(response, self.url(self.special, "sheet_list"), fetch_redirect_response=False)

    def test_an_admin_opens_any_sheet_read_only_numbers_included(self):
        uploaded = self.mark_uploaded(
            services.create_sheet(self.coord_b, self.b, date(2026, 6, 7), "Sunday Service"))
        response = self.get(self.admin, "sheet_detail", uploaded.pk)
        self.assertRedirects(
            response, self.url(self.admin, "sheet_review", uploaded.pk), fetch_redirect_response=False,
        )
        review = self.get(self.admin, "sheet_review", uploaded.pk)
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.context["summary"].total_present, 3)
