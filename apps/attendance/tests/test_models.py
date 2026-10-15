"""Tests for the attendance models: is_locked, first timer names, required fields, constraints."""
from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import Extension, Gender, Role, Status, User
from apps.attendance.age import AgeNetwork
from apps.attendance.models import Attendance, AttendanceFirstTimer, AttendanceMember


class AttendanceModelTests(TestCase):
    def setUp(self):
        self.ext = Extension.objects.create(name="north")
        self.sheet = Attendance.objects.create(extension=self.ext, attendance_date=date(2026, 6, 14))

    def member(self, **extra):
        return User.objects.create_user(
            first_name="ana", last_name="reyes", role=Role.MEMBER, extension=self.ext,
            status=Status.ACTIVE, must_change_password=False, **extra,
        )

    def first_timer(self, **extra):
        data = dict(
            attendance=self.sheet, first_name="Maria", last_name="Santos",
            gender=Gender.FEMALE, age_network=AgeNetwork.YOUTH,
        )
        data.update(extra)
        return AttendanceFirstTimer.objects.create(**data)

    # --- Attendance -----------------------------------------------------
    def test_is_locked_only_when_uploaded(self):
        self.assertFalse(self.sheet.is_locked)
        self.sheet.status = Attendance.Status.UPLOADED
        self.assertTrue(self.sheet.is_locked)

    def test_new_fields_start_empty(self):
        self.assertIsNone(self.sheet.summary)
        self.assertIsNone(self.sheet.taken_by)
        self.assertIsNone(self.sheet.uploaded_by)

    def test_sheet_per_extension_date_and_label_stays_unique(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Attendance.objects.create(extension=self.ext, attendance_date=date(2026, 6, 14))

    def test_same_date_with_another_label_is_allowed(self):
        Attendance.objects.create(extension=self.ext, attendance_date=date(2026, 6, 14), label="Prayer Meeting")

    # --- AttendanceMember -----------------------------------------------
    def test_member_row_may_have_blank_gender_and_age_network(self):
        row = AttendanceMember.objects.create(attendance=self.sheet, user=self.member(), member_name="Ana Reyes")
        row.full_clean()
        self.assertEqual((row.gender, row.age_network, row.home_extension_name), ("", "", ""))

    def test_member_row_keeps_the_home_extension_text(self):
        # A separate extension nobody belongs to: the sheet's own extension is RESTRICTed.
        home = Extension.objects.create(name="south")
        row = AttendanceMember.objects.create(
            attendance=self.sheet, user=self.member(), member_name="Ana Reyes",
            home_extension=home, home_extension_name="south",
        )
        home.delete()
        row.refresh_from_db()
        self.assertIsNone(row.home_extension)
        self.assertEqual(row.home_extension_name, "south")

    def test_a_person_is_ticked_once_per_sheet(self):
        person = self.member()
        AttendanceMember.objects.create(attendance=self.sheet, user=person, member_name="Ana Reyes")
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceMember.objects.create(attendance=self.sheet, user=person, member_name="Ana Reyes")

    # --- AttendanceFirstTimer -------------------------------------------
    def test_first_timer_names_are_stored_lowercase_and_shown_in_name_case(self):
        row = self.first_timer(first_name="  MARIA  ", middle_name="dela  Cruz", last_name="o'Brien", suffix="Jr.")
        row.refresh_from_db()
        self.assertEqual((row.first_name, row.middle_name, row.last_name, row.suffix),
                         ("maria", "dela cruz", "o'brien", "jr"))
        self.assertEqual(row.full_name, "Maria Dela Cruz O'Brien Jr.")

    def test_first_timer_phone_is_stored_as_digits(self):
        self.assertEqual(self.first_timer(phone_number="+63 917 123 4567").phone_number, "09171234567")

    def test_first_timer_requires_gender_and_age_network(self):
        row = AttendanceFirstTimer(attendance=self.sheet, first_name="maria", last_name="santos")
        with self.assertRaises(ValidationError) as caught:
            row.full_clean()
        self.assertIn("gender", caught.exception.message_dict)
        self.assertIn("age_network", caught.exception.message_dict)

    def test_first_timer_optional_fields(self):
        row = self.first_timer()
        row.full_clean()
        self.assertEqual((row.middle_name, row.suffix, row.phone_number), ("", "", ""))
        self.assertIsNone(row.birth_date)