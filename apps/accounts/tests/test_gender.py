"""Tests for the optional gender field: the form, saving it, and keeping it out of audit_log."""
from unittest import mock

from django.test import RequestFactory, TestCase

from apps.accounts.forms import UserForm
from apps.accounts.models import Extension, Gender, Role, Status, User
from apps.accounts.services import users as user_services


def make_admin():
    """An activated super-admin who can edit anyone."""
    return User.objects.create_superuser(first_name="sam", last_name="root", password="x-Strong-pass-1")


class GenderFieldTests(TestCase):
    def setUp(self):
        self.ext = Extension.objects.create(name="north")
        self.actor = make_admin()
        self.request = RequestFactory().post("/")
        self.request.user = self.actor

    def person(self):
        """An activated member of the extension."""
        return User.objects.create_user(
            first_name="ana", last_name="reyes", role=Role.MEMBER, extension=self.ext,
            status=Status.ACTIVE, must_change_password=False,
        )

    def form(self, **extra):
        data = {"first_name": "Ana", "last_name": "Reyes", "role": Role.MEMBER, "extension": self.ext.pk, **extra}
        return UserForm(data, actor=self.actor)

    def test_gender_is_optional(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["gender"], "")

    def test_gender_accepts_male_and_female(self):
        for value in (Gender.MALE, Gender.FEMALE):
            with self.subTest(value=value):
                form = UserForm(
                    {"first_name": f"x{value}", "last_name": "Reyes", "role": Role.MEMBER,
                     "extension": self.ext.pk, "gender": value},
                    actor=self.actor,
                )
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["gender"], value)

    def test_gender_refuses_other_values(self):
        self.assertFalse(self.form(gender="other").is_valid())

    def test_update_user_saves_gender_and_logs_only_the_field_name(self):
        person = self.person()
        data = {
            "first_name": "ana", "last_name": "reyes", "role": Role.MEMBER,
            "extension": self.ext, "gender": Gender.FEMALE,
        }
        with mock.patch.object(user_services, "log_action") as log:
            _, changed = user_services.update_user(self.actor, self.request, person, data)
        self.assertTrue(changed)
        person.refresh_from_db()
        self.assertEqual(person.gender, Gender.FEMALE)
        update_calls = [c for c in log.call_args_list if c.args and c.args[0] == "account.update"]
        self.assertEqual(len(update_calls), 1)
        self.assertEqual(update_calls[0].kwargs["after"], {"contact_fields_changed": ["gender"]})
        self.assertNotIn("female", repr(update_calls[0].kwargs))

    def test_changing_only_gender_never_runs_the_duplicate_check(self):
        person = self.person()
        with mock.patch.object(user_services, "_refuse_duplicate") as refuse, \
                mock.patch.object(user_services, "log_action"):
            user_services.update_user(self.actor, self.request, person, {
                "first_name": "ana", "last_name": "reyes", "role": Role.MEMBER,
                "extension": self.ext, "gender": Gender.MALE,
            })
        refuse.assert_not_called()
