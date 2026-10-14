"""Tests for attendance.age: the boundary ages, birthdays, 29 February and None cases."""
from datetime import date

from django.test import SimpleTestCase

from apps.attendance.age import AgeNetwork, age_network_for, age_on

SHEET = date(2026, 6, 15)


def born(years_old, *, day_before_birthday=False):
    """A birth date that makes someone `years_old` on SHEET (or one day short of it)."""
    return date(SHEET.year - years_old, 6, 16 if day_before_birthday else 15)


class AgeOnTests(SimpleTestCase):
    def test_birthday_on_the_sheet_date_counts(self):
        self.assertEqual(age_on(born(25), SHEET), 25)

    def test_one_day_before_the_birthday_does_not(self):
        self.assertEqual(age_on(born(25, day_before_birthday=True), SHEET), 24)

    def test_leap_day_birthday_falls_on_1_march_in_a_common_year(self):
        birth = date(2000, 2, 29)
        self.assertEqual(age_on(birth, date(2025, 2, 28)), 24)
        self.assertEqual(age_on(birth, date(2025, 3, 1)), 25)

    def test_leap_day_birthday_in_a_leap_year(self):
        birth = date(2000, 2, 29)
        self.assertEqual(age_on(birth, date(2024, 2, 28)), 23)
        self.assertEqual(age_on(birth, date(2024, 2, 29)), 24)


class AgeNetworkTests(SimpleTestCase):
    def test_boundaries(self):
        cases = {
            0: AgeNetwork.CHILDREN, 12: AgeNetwork.CHILDREN,
            13: AgeNetwork.YOUTH, 24: AgeNetwork.YOUTH,
            25: AgeNetwork.YOUNG_ADULT, 35: AgeNetwork.YOUNG_ADULT,
            36: AgeNetwork.ADULT, 90: AgeNetwork.ADULT,
        }
        for age, expected in cases.items():
            with self.subTest(age=age):
                self.assertEqual(age_network_for(born(age), SHEET), expected)

    def test_day_before_a_birthday_stays_in_the_lower_network(self):
        self.assertEqual(age_network_for(born(36, day_before_birthday=True), SHEET), AgeNetwork.YOUNG_ADULT)
        self.assertEqual(age_network_for(born(13, day_before_birthday=True), SHEET), AgeNetwork.CHILDREN)

    def test_leap_day_birth_changes_network_on_1_march(self):
        birth = date(2000, 2, 29)
        self.assertEqual(age_network_for(birth, date(2025, 2, 28)), AgeNetwork.YOUTH)
        self.assertEqual(age_network_for(birth, date(2025, 3, 1)), AgeNetwork.YOUNG_ADULT)

    def test_born_on_the_sheet_date_is_a_child(self):
        self.assertEqual(age_network_for(SHEET, SHEET), AgeNetwork.CHILDREN)

    def test_no_birth_date_is_none(self):
        self.assertIsNone(age_network_for(None, SHEET))

    def test_birth_date_after_the_sheet_date_is_none(self):
        self.assertIsNone(age_network_for(date(2026, 6, 16), SHEET))
