"""Brick 13: the event reminder choice on the Settings page."""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Extension, Role, Status, User
from apps.audit.models import AuditLog
from apps.core.services import ServiceError

from ..forms import UserSettingsForm, reminder_initial
from ..models import UserSettings
from ..services import get_user_settings, update_user_settings


def make_person():
    extension = Extension.objects.create(name="Reminder test")
    return User.objects.create_user(
        password="test-pass-12345", role=Role.MEMBER, extension=extension, status=Status.ACTIVE,
        first_name="Rem", last_name="Tester", must_change_password=False,
    )


BASE = {"color_mode": "system", "mobile_nav_style": "bottom"}


class ReminderServiceTests(TestCase):
    def setUp(self):
        self.user = make_person()

    def test_defaults_are_on_and_one_day(self):
        row = get_user_settings(self.user)
        self.assertTrue(row.event_reminders_enabled)
        self.assertEqual(row.event_reminder_minutes, 1440)

    def test_saves_a_lead_time(self):
        row, changed = update_user_settings(
            self.user, **BASE, event_reminders_enabled=True, event_reminder_minutes=60,
        )
        self.assertTrue(changed)
        self.assertEqual(row.event_reminder_minutes, 60)

    def test_off_keeps_the_stored_lead_time(self):
        update_user_settings(self.user, **BASE, event_reminders_enabled=True, event_reminder_minutes=180)
        row, changed = update_user_settings(
            self.user, **BASE, event_reminders_enabled=False, event_reminder_minutes=None,
        )
        self.assertTrue(changed)
        self.assertFalse(row.event_reminders_enabled)
        self.assertEqual(row.event_reminder_minutes, 180)

    def test_same_values_are_not_a_change(self):
        _, changed = update_user_settings(
            self.user, **BASE, event_reminders_enabled=True, event_reminder_minutes=1440,
        )
        self.assertFalse(changed)

    def test_rejects_other_lead_times(self):
        with self.assertRaises(ServiceError):
            update_user_settings(self.user, **BASE, event_reminders_enabled=True, event_reminder_minutes=90)

    def test_old_call_without_reminder_values_still_works(self):
        row, changed = update_user_settings(self.user, color_mode="dark", mobile_nav_style="bottom")
        self.assertTrue(changed)
        self.assertEqual(row.event_reminder_minutes, 1440)

    def test_nothing_is_written_to_the_audit_log(self):
        before = AuditLog.objects.count()
        update_user_settings(self.user, **BASE, event_reminders_enabled=False, event_reminder_minutes=None)
        self.assertEqual(AuditLog.objects.count(), before)


class ReminderFormTests(TestCase):
    def test_off_maps_to_disabled_without_a_lead_time(self):
        form = UserSettingsForm({**BASE, "event_reminder": "off"})
        self.assertTrue(form.is_valid())
        self.assertIs(form.cleaned_data["event_reminders_enabled"], False)
        self.assertIsNone(form.cleaned_data["event_reminder_minutes"])
        self.assertNotIn("event_reminder", form.cleaned_data)

    def test_lead_time_maps_to_enabled_and_minutes(self):
        form = UserSettingsForm({**BASE, "event_reminder": "180"})
        self.assertTrue(form.is_valid())
        self.assertIs(form.cleaned_data["event_reminders_enabled"], True)
        self.assertEqual(form.cleaned_data["event_reminder_minutes"], 180)

    def test_invalid_and_missing_are_rejected(self):
        self.assertFalse(UserSettingsForm({**BASE, "event_reminder": "30"}).is_valid())
        self.assertFalse(UserSettingsForm(BASE).is_valid())

    def test_initial_choice_follows_the_row(self):
        row = UserSettings(event_reminders_enabled=True, event_reminder_minutes=1440)
        self.assertEqual(reminder_initial(row), "1440")
        row.event_reminder_minutes = 2000          # an old value: closest allowed option
        self.assertEqual(reminder_initial(row), "1440")
        row.event_reminders_enabled = False
        self.assertEqual(reminder_initial(row), "off")


class ReminderPageTests(TestCase):
    def setUp(self):
        self.user = make_person()
        self.client.force_login(self.user)
        self.url = reverse("theming:settings")

    def test_page_shows_the_four_choices_with_the_saved_one_checked(self):
        update_user_settings(self.user, **BASE, event_reminders_enabled=True, event_reminder_minutes=60)
        response = self.client.get(self.url)
        for value in ("1440", "180", "60", "off"):
            self.assertContains(response, f'id="id_event_reminder_{value}"')
        checked = [o["value"] for o in response.context["reminder_options"] if o["checked"]]
        self.assertEqual(checked, ["60"])

    def test_post_saves_and_redirects(self):
        response = self.client.post(self.url, {**BASE, "event_reminder": "180"})
        self.assertRedirects(response, self.url)
        row = UserSettings.objects.get(user=self.user)
        self.assertTrue(row.event_reminders_enabled)
        self.assertEqual(row.event_reminder_minutes, 180)

    def test_post_off_turns_reminders_off(self):
        self.client.post(self.url, {**BASE, "event_reminder": "off"})
        self.assertFalse(UserSettings.objects.get(user=self.user).event_reminders_enabled)

    def test_bad_value_shows_an_error_and_saves_nothing(self):
        response = self.client.post(self.url, {**BASE, "event_reminder": "30"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose 1 day, 3 hours, 1 hour or off.")
        self.assertEqual(UserSettings.objects.get(user=self.user).event_reminder_minutes, 1440)
