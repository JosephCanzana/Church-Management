"""Tests for the stored age network: the fallback rule and when attendance writes it."""
from datetime import date

from django.test import RequestFactory, TestCase

from apps.accounts.models import AgeNetwork, Extension, Gender, Role, Status, User
from apps.accounts.services.presence import remember_age_network
from apps.attendance import services as attendance
from apps.attendance.age import effective_age_network

SHEET_DATE = date(2026, 10, 11)


def member(ext, first, **extra):
    return User.objects.create_user(
        first_name=first, last_name="reyes", role=Role.MEMBER, extension=ext,
        status=Status.ACTIVE, must_change_password=False, gender=Gender.FEMALE, **extra,
    )


class EffectiveAgeNetworkTests(TestCase):
    def setUp(self):
        self.ext = Extension.objects.create(name="north")

    def test_birth_date_wins_over_the_stored_value(self):
        person = member(self.ext, "ana", birth_date=date(1980, 1, 1), age_network=AgeNetwork.YOUTH)
        self.assertEqual(effective_age_network(person, SHEET_DATE), AgeNetwork.ADULT)

    def test_stored_value_is_used_without_a_birth_date(self):
        person = member(self.ext, "ben", age_network=AgeNetwork.YOUTH)
        self.assertEqual(effective_age_network(person, SHEET_DATE), AgeNetwork.YOUTH)

    def test_none_when_nothing_is_known(self):
        self.assertIsNone(effective_age_network(member(self.ext, "cy"), SHEET_DATE))


class RememberAgeNetworkTests(TestCase):
    def setUp(self):
        self.ext = Extension.objects.create(name="north")

    def test_saved_when_there_is_no_birth_date(self):
        person = member(self.ext, "ana")
        self.assertTrue(remember_age_network(person.pk, AgeNetwork.YOUTH))
        person.refresh_from_db()
        self.assertEqual(person.age_network, AgeNetwork.YOUTH)

    def test_never_touches_someone_with_a_birth_date(self):
        person = member(self.ext, "ben", birth_date=date(1980, 1, 1))
        self.assertFalse(remember_age_network(person.pk, AgeNetwork.YOUTH))
        person.refresh_from_db()
        self.assertEqual(person.age_network, "")

    def test_ignores_an_unknown_value(self):
        person = member(self.ext, "cy")
        self.assertFalse(remember_age_network(person.pk, "elder"))


class AttendanceKeepsAgeNetworkTests(TestCase):
    def setUp(self):
        self.ext = Extension.objects.create(name="north")
        self.coord = User.objects.create_user(
            first_name="cora", last_name="lead", role=Role.COORDINATOR, extension=self.ext,
            status=Status.ACTIVE, must_change_password=False, gender=Gender.FEMALE,
        )
        self.ext.coordinator = self.coord
        self.ext.save()
        self.sheet = attendance.create_sheet(self.coord, self.ext, SHEET_DATE, request=RequestFactory().post("/"))

    def test_a_picked_network_is_remembered_and_not_asked_again(self):
        person = member(self.ext, "ana")
        attendance.mark_present(self.coord, self.sheet, person, age_network=AgeNetwork.YOUTH)
        person.refresh_from_db()
        self.assertEqual(person.age_network, AgeNetwork.YOUTH)
        # Next sheet: no network posted, the stored one fills the row.
        later = attendance.create_sheet(self.coord, self.ext, date(2026, 10, 18), request=RequestFactory().post("/"))
        row, _ = attendance.mark_present(self.coord, later, person)
        self.assertEqual(row.age_network, AgeNetwork.YOUTH)

    def test_changing_the_category_on_the_sheet_updates_the_person(self):
        person = member(self.ext, "ben", age_network=AgeNetwork.YOUTH)
        row, _ = attendance.mark_present(self.coord, self.sheet, person)
        attendance.set_member_category(self.coord, self.sheet, row.pk, age_network=AgeNetwork.ADULT)
        person.refresh_from_db()
        self.assertEqual(person.age_network, AgeNetwork.ADULT)

    def test_a_person_with_a_birth_date_is_not_changed(self):
        person = member(self.ext, "cy", birth_date=date(1980, 1, 1))
        row, _ = attendance.mark_present(self.coord, self.sheet, person)
        attendance.set_member_category(self.coord, self.sheet, row.pk, age_network=AgeNetwork.YOUTH)
        person.refresh_from_db()
        self.assertEqual(person.age_network, "")
