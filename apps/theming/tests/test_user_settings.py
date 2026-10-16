"""Service tests for personal settings (brick 9): rows exist, saving works, bad values are refused."""
from django.test import TestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog  # adjust if the model lives elsewhere
from apps.core.services import ServiceError
from apps.theming.models import UserSettings
from apps.theming.services import get_user_settings, update_user_settings


def make_user(**extra):
    """An activated user. Replace with your shared test factory if the accounts tests have one."""
    fields = dict(first_name="Ana", last_name="Cruz", role="member", status="active", must_change_password=False)
    fields.update(extra)
    return User.objects.create(**fields)


class UserSettingsServiceTests(TestCase):
    def test_new_user_gets_a_row_with_the_defaults(self):
        row = UserSettings.objects.get(user=make_user())
        self.assertEqual(row.color_mode, "system")
        self.assertEqual(row.mobile_nav_style, "bottom")

    def test_get_user_settings_creates_a_missing_row(self):
        user = make_user()
        UserSettings.objects.filter(user=user).delete()
        self.assertEqual(get_user_settings(user).color_mode, "system")

    def test_update_saves_both_values_and_reports_a_change(self):
        user = make_user()
        row, changed = update_user_settings(user, color_mode="dark", mobile_nav_style="hamburger")
        self.assertTrue(changed)
        row.refresh_from_db()
        self.assertEqual((row.color_mode, row.mobile_nav_style), ("dark", "hamburger"))

    def test_saving_the_same_values_reports_no_change(self):
        user = make_user()
        _, changed = update_user_settings(user, color_mode="system", mobile_nav_style="bottom")
        self.assertFalse(changed)

    def test_invalid_values_are_refused_and_nothing_is_saved(self):
        user = make_user()
        with self.assertRaises(ServiceError):
            update_user_settings(user, color_mode="purple", mobile_nav_style="bottom")
        with self.assertRaises(ServiceError):
            update_user_settings(user, color_mode="dark", mobile_nav_style="side")
        row = UserSettings.objects.get(user=user)
        self.assertEqual((row.color_mode, row.mobile_nav_style), ("system", "bottom"))

    def test_update_touches_only_that_user(self):
        mine, other = make_user(), make_user(first_name="Ben")
        update_user_settings(mine, color_mode="dark", mobile_nav_style="hamburger")
        self.assertEqual(UserSettings.objects.get(user=other).color_mode, "system")

    def test_nothing_is_written_to_the_audit_log(self):
        user = make_user()
        before = AuditLog.objects.count()
        update_user_settings(user, color_mode="dark", mobile_nav_style="hamburger")
        self.assertEqual(AuditLog.objects.count(), before)
