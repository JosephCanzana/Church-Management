"""Tests for accounts.services.presence.mark_attended: last_attended_at only moves forward."""
from datetime import date

from django.test import TestCase

from apps.accounts.models import Extension, Role, Status, User
from apps.accounts.services.presence import mark_attended


class MarkAttendedTests(TestCase):
    def setUp(self):
        self.ext = Extension.objects.create(name="alpha")

    def member(self, first, last_attended_at=None):
        """An activated member with the given last attendance."""
        return User.objects.create_user(
            password="pass-12345", first_name=first, last_name="tester", role=Role.MEMBER,
            extension=self.ext, status=Status.ACTIVE, must_change_password=False,
            last_attended_at=last_attended_at,
        )

    def test_sets_the_date_when_there_is_none(self):
        person = self.member("new")
        self.assertEqual(mark_attended([person.pk], date(2026, 6, 14)), 1)
        person.refresh_from_db()
        self.assertEqual(person.last_attended_at, date(2026, 6, 14))

    def test_moves_an_earlier_date_forward(self):
        person = self.member("old", date(2026, 1, 4))
        mark_attended([person.pk], date(2026, 6, 14))
        person.refresh_from_db()
        self.assertEqual(person.last_attended_at, date(2026, 6, 14))

    def test_never_moves_back(self):
        person = self.member("recent", date(2026, 9, 6))
        self.assertEqual(mark_attended([person.pk], date(2026, 6, 14)), 0)
        person.refresh_from_db()
        self.assertEqual(person.last_attended_at, date(2026, 9, 6))

    def test_the_same_date_changes_nothing(self):
        person = self.member("same", date(2026, 6, 14))
        self.assertEqual(mark_attended([person.pk], date(2026, 6, 14)), 0)

    def test_only_the_listed_people_change_and_an_empty_list_is_fine(self):
        listed, other = self.member("listed"), self.member("other")
        self.assertEqual(mark_attended([], date(2026, 6, 14)), 0)
        mark_attended([listed.pk], date(2026, 6, 14))
        other.refresh_from_db()
        self.assertIsNone(other.last_attended_at)