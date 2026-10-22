"""Goals views: login, owner-only access, Today, JSON check-in, forms."""
import datetime as dt

from django.test import TestCase
from django.urls import reverse

from apps.faith import services
from apps.faith.models import Goal, GoalCheckin

from .goal_helpers import TODAY, at_date, goal_data, make_user

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class GoalViewTests(TestCase):
    """The screens, with the clock pinned to TODAY."""

    def setUp(self):
        self.user = make_user()
        self.other = make_user()
        with at_date(TODAY):
            self.goal = services.create_goal(self.user, goal_data(title="Visible goal"))
            self.theirs = services.create_goal(self.other, goal_data(title="Secret goal"))
        self.client.force_login(self.user)

    def test_login_required_everywhere(self):
        self.client.logout()
        for name, args in (
            ("faith:goals", []), ("faith:goal_all", []), ("faith:goal_new", []),
            ("faith:goal_edit", [self.goal.pk]), ("faith:goal_check", [self.goal.pk]),
            ("faith:goal_note", [self.goal.pk]), ("faith:goal_change_schedule", [self.goal.pk]),
            ("faith:goal_archive", [self.goal.pk]), ("faith:goal_restore", [self.goal.pk]),
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 302)

    def test_today_renders_own_goals_only(self):
        with at_date(TODAY):
            response = self.client.get(reverse("faith:goals"))
        self.assertContains(response, "Visible goal")
        self.assertNotContains(response, "Secret goal")

    def test_all_goals_renders(self):
        with at_date(TODAY):
            response = self.client.get(reverse("faith:goal_all"))
        self.assertContains(response, "Visible goal")
        self.assertNotContains(response, "Secret goal")

    def test_missing_goal_and_another_persons_goal_look_the_same(self):
        for name in ("faith:goal_edit", "faith:goal_change_schedule"):
            with self.subTest(name=name):
                mine_missing = self.client.get(reverse(name, args=[999999]))
                theirs = self.client.get(reverse(name, args=[self.theirs.pk]))
                self.assertEqual((mine_missing.status_code, theirs.status_code), (404, 404))
        for name in ("faith:goal_check", "faith:goal_note", "faith:goal_archive", "faith:goal_restore"):
            with self.subTest(name=name):
                statuses = {
                    self.client.post(reverse(name, args=[pk]), **AJAX).status_code
                    for pk in (999999, self.theirs.pk)
                }
                self.assertEqual(statuses, {404})
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.status, "active")

    def test_json_check_in_works_and_reports_counts(self):
        with at_date(TODAY):
            response = self.client.post(reverse("faith:goal_check", args=[self.goal.pk]), {"done": "1"}, **AJAX)
        data = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual((data["ok"], data["done"], data["done_count"], data["total"]), (True, True, 1, 1))
        self.assertEqual(data["today"], TODAY.isoformat())
        self.assertEqual(data["streak"], 1)
        self.assertTrue(GoalCheckin.objects.get(goal=self.goal).done)

    def test_json_uncheck(self):
        with at_date(TODAY):
            self.client.post(reverse("faith:goal_check", args=[self.goal.pk]), {"done": "1"}, **AJAX)
            data = self.client.post(reverse("faith:goal_check", args=[self.goal.pk]), {"done": "0"}, **AJAX).json()
        self.assertEqual((data["done"], data["done_count"]), (False, 0))

    def test_json_error_for_a_goal_not_scheduled_today(self):
        Goal.objects.filter(pk=self.goal.pk).update(repeat_type="weekly", weekdays=[0])
        with at_date(TODAY):
            response = self.client.post(reverse("faith:goal_check", args=[self.goal.pk]), {"done": "1"}, **AJAX)
        self.assertEqual((response.status_code, response.json()["ok"]), (400, False))

    def test_check_requires_post(self):
        self.assertEqual(self.client.get(reverse("faith:goal_check", args=[self.goal.pk])).status_code, 405)

    def test_note_is_saved_and_never_shown_to_the_other_person(self):
        with at_date(TODAY):
            self.client.post(reverse("faith:goal_note", args=[self.goal.pk]), {"note": "ZXQ private"}, **AJAX)
            self.assertContains(self.client.get(reverse("faith:goals")), "ZXQ private")
            self.client.force_login(self.other)
            self.assertNotContains(self.client.get(reverse("faith:goals")), "ZXQ private")

    def test_create_through_the_form(self):
        with at_date(TODAY):
            form = self.client.get(reverse("faith:goal_new"))
            self.assertContains(form, "Your buddy can see this goal")
            response = self.client.post(reverse("faith:goal_new"), {
                "title": "Fast on Fridays", "description": "", "start_date": TODAY.isoformat(),
                "all_day": "on", "repeat_type": "weekly", "weekdays": ["4"], "ends": "never",
                "token": form.context["form"].initial["token"],
            })
        self.assertEqual(response.status_code, 302)
        goal = Goal.objects.get(title="Fast on Fridays")
        self.assertEqual((goal.repeat_type, goal.weekdays, goal.end_date), ("weekly", [4], None))

    def test_form_errors_show_inline(self):
        with at_date(TODAY):
            response = self.client.post(reverse("faith:goal_new"), {
                "title": "", "start_date": TODAY.isoformat(), "all_day": "on",
                "repeat_type": "daily", "ends": "never",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Goal.objects.filter(user=self.user).count(), 1)

    def test_edit_shows_read_only_repeat_after_a_checkin(self):
        GoalCheckin.objects.create(goal=self.goal, checkin_date=TODAY)
        with at_date(TODAY):
            response = self.client.get(reverse("faith:goal_edit", args=[self.goal.pk]))
        self.assertContains(response, "Change schedule")
        self.assertNotIn("repeat_type", response.context["form"].fields)

    def test_archive_and_restore_round_trip(self):
        with at_date(TODAY):
            self.client.post(reverse("faith:goal_archive", args=[self.goal.pk]))
            self.goal.refresh_from_db()
            self.assertEqual(self.goal.status, "archived")
            self.client.post(reverse("faith:goal_restore", args=[self.goal.pk]))
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.status, "active")

    def test_change_schedule_through_the_form(self):
        GoalCheckin.objects.create(goal=self.goal, checkin_date=TODAY)
        with at_date(TODAY):
            response = self.client.post(
                reverse("faith:goal_change_schedule", args=[self.goal.pk]),
                {"repeat_type": "weekly", "weekdays": ["0", "2"]},
            )
        self.assertEqual(response.status_code, 302)
        new = Goal.objects.get(previous_goal=self.goal)
        self.assertEqual((new.start_date, new.weekdays), (TODAY + dt.timedelta(days=1), [0, 2]))
