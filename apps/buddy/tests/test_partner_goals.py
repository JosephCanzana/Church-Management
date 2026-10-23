"""Tests for Brick 9: the buddy's read-only "Their goals" view.

Run: docker compose exec web python manage.py test apps.buddy.tests.test_partner_goals -v 2

(verify) `make_user` below is a guess. Replace its body with the helper your other buddy tests
use (apps/buddy/tests/test_requests.py) so extension and field names match the real code.
"""
import datetime as dt
import inspect
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.buddy.models import UserBuddy
from apps.buddy.services import (
    PARTNER_GOALS_INACTIVE, PARTNER_GOALS_LIVE, PARTNER_GOALS_NONE, partner_goals_for,
)
from apps.faith.models import Goal, GoalCheckin
from apps.faith.partner import DONE, MISSED, NOT_SCHEDULED, OPEN, partner_goal_overview
from apps.notifications.models import Notification 
from apps.accounts.models import User
from apps.faith.tests.helpers import make_user as faith_make_user   

NOTE = "SECRET-NOTE-Do-Not-Leak-123"



def make_user(name):
    """A member (with an extension) who can sign in; `name` is the first name."""
    return faith_make_user(first_name=name.title())


def pair(a, b):
    """A live partnership with the two ids sorted, as the service does it."""
    first, second = sorted([a.pk, b.pk])
    return UserBuddy.objects.create(user_a_id=first, user_b_id=second)


def cannot_sign_in(user):
    """Make exactly this person unable to sign in, whatever `can_sign_in` is built from."""
    return mock.patch.object(User, "can_sign_in", property(lambda self: self.pk != user.pk))


def make_goal(user, title, today, **kwargs):
    """An active daily goal that started 10 days ago, unless overridden."""
    values = {
        "start_date": today - dt.timedelta(days=10), "end_date": None,
        "repeat_type": Goal.Repeat.DAILY, "weekdays": None, "status": Goal.Status.ACTIVE,
    }
    values.update(kwargs)
    return Goal.objects.create(user=user, title=title, description=f"About {title}", **values)


def check(goal, day, done=True, note=""):
    """A check-in row for `goal` on `day`."""
    return GoalCheckin.objects.create(goal=goal, checkin_date=day, done=done, note=note)


class PartnerGoalsTests(TestCase):
    """Services and overview. Dates come from `localdate()`, the same clock the service uses."""

    def setUp(self):
        self.today = timezone.localdate()
        self.alice, self.bob = make_user("alice"), make_user("bob")
        self.carol, self.dave = make_user("carol"), make_user("dave")

    def titles(self, result):
        return [row["title"] for row in result["overview"]["goals"]]

    def test_no_partnership_shows_nothing(self):
        make_goal(self.bob, "Bob prays", self.today)
        result = partner_goals_for(self.alice)
        self.assertEqual(result["state"], PARTNER_GOALS_NONE)
        self.assertIsNone(result["overview"])

    def test_partner_comes_from_the_row_not_the_caller(self):
        pair(self.alice, self.bob)
        make_goal(self.bob, "Bob prays", self.today)
        make_goal(self.carol, "Carol reads", self.today)
        # The function takes only the viewer: there is no id a request could swap.
        self.assertEqual(list(inspect.signature(partner_goals_for).parameters), ["viewer"])
        result = partner_goals_for(self.alice)
        self.assertEqual(result["state"], PARTNER_GOALS_LIVE)
        self.assertEqual(self.titles(result), ["Bob prays"])
        # Same from the other side of the pair.
        self.assertEqual(partner_goals_for(self.bob)["overview"]["goals"], [])

    def test_stranger_and_unpaired_person_see_nothing(self):
        pair(self.alice, self.bob)
        make_goal(self.alice, "Alice goal", self.today)
        make_goal(self.bob, "Bob goal", self.today)
        for stranger in (self.carol, self.dave):
            result = partner_goals_for(stranger)
            self.assertEqual(result["state"], PARTNER_GOALS_NONE)
            self.assertIsNone(result["overview"])

    def test_inactive_partner_hides_goals(self):
        pair(self.alice, self.bob)
        make_goal(self.bob, "Bob prays", self.today)
        with cannot_sign_in(self.bob):
            result = partner_goals_for(self.alice)
        self.assertEqual(result["state"], PARTNER_GOALS_INACTIVE)
        self.assertIsNone(result["overview"])
        self.assertNotIn("Bob prays", repr(result))

    def test_archived_completed_future_and_ended_goals_are_excluded(self):
        t = self.today
        make_goal(self.bob, "Shown", t)
        make_goal(self.bob, "Archived", t, status=Goal.Status.ARCHIVED, archived_at=timezone.now())
        make_goal(self.bob, "Completed", t, status=Goal.Status.COMPLETED)
        make_goal(self.bob, "Not started", t, start_date=t + dt.timedelta(days=1))
        make_goal(self.bob, "Ended", t, end_date=t - dt.timedelta(days=1))  # still "active" until the nightly job
        make_goal(self.bob, "Ends today", t, end_date=t)
        names = [row["title"] for row in partner_goal_overview(self.bob, t)["goals"]]
        self.assertEqual(sorted(names), ["Ends today", "Shown"])

    def test_to_do_and_done_lists(self):
        t = self.today
        wrong_day = [(t.weekday() + 1) % 7]
        done = make_goal(self.bob, "Done", t)
        check(done, t)
        make_goal(self.bob, "Waiting", t)
        unchecked = make_goal(self.bob, "Unchecked", t)
        check(unchecked, t, done=False)           # box was ticked, then unticked
        off_day = make_goal(self.bob, "Other day", t, repeat_type=Goal.Repeat.WEEKLY, weekdays=wrong_day)
        view = partner_goal_overview(self.bob, t)
        self.assertEqual([r["title"] for r in view["to_do_today"]], ["Unchecked", "Waiting"])
        self.assertEqual([r["title"] for r in view["done_today"]], ["Done"])
        self.assertEqual((view["today_done"], view["today_total"]), (1, 3))
        by_title = {r["title"]: r for r in view["goals"]}
        self.assertIsNone(by_title["Other day"]["done_today"])
        self.assertEqual(off_day.title, "Other day")

    def test_seven_day_strip_matches_check_ins(self):
        t = self.today
        day = lambda back: t - dt.timedelta(days=back)  # noqa: E731
        goal = make_goal(self.bob, "Strip", t)
        check(goal, day(1))
        check(goal, day(2), done=False)           # an unticked row is not a done day
        strip = partner_goal_overview(self.bob, t)["goals"][0]["strip"]
        self.assertEqual([c["date"] for c in strip], [day(n) for n in range(6, -1, -1)])
        states = [c["state"] for c in strip]
        self.assertEqual(states, [MISSED, MISSED, MISSED, MISSED, MISSED, DONE, OPEN])

    def test_strip_marks_days_before_start_and_off_days(self):
        t = self.today
        late = make_goal(self.bob, "Late start", t, start_date=t - dt.timedelta(days=2))
        weekly = make_goal(self.bob, "Weekly", t, repeat_type=Goal.Repeat.WEEKLY,
                           weekdays=[(t - dt.timedelta(days=1)).weekday()])
        rows = {r["title"]: r for r in partner_goal_overview(self.bob, t)["goals"]}
        self.assertEqual([c["state"] for c in rows["Late start"]["strip"]][:4], [NOT_SCHEDULED] * 4)
        weekly_states = [c["state"] for c in rows["Weekly"]["strip"]]
        self.assertEqual(weekly_states.count(MISSED), 1)       # yesterday, never checked
        self.assertEqual(weekly_states.count(NOT_SCHEDULED), 6)
        self.assertEqual((late.title, weekly.title), ("Late start", "Weekly"))

    def test_goal_streaks_are_included(self):
        t = self.today
        goal = make_goal(self.bob, "Streaky", t)
        check(goal, t - dt.timedelta(days=1))
        check(goal, t)
        row = partner_goal_overview(self.bob, t)["goals"][0]
        self.assertEqual((row["current"], row["best"]), (2, 2))
        self.assertIn("open", partner_goal_overview(self.bob, t)["streak_snapshot"])

    def test_check_in_notes_never_appear(self):
        t = self.today
        goal = make_goal(self.bob, "Private", t)
        check(goal, t, note=NOTE)
        check(goal, t - dt.timedelta(days=1), note=NOTE)
        pair(self.alice, self.bob)
        result = partner_goals_for(self.alice)
        self.assertNotIn(NOTE, repr(result))
        for row in result["overview"]["goals"]:
            self.assertNotIn("note", row)

    def test_reading_is_not_audited_and_does_not_notify(self):
        pair(self.alice, self.bob)
        make_goal(self.bob, "Quiet", self.today)
        audits, bells = AuditLog.objects.count(), Notification.objects.count()
        partner_goals_for(self.alice)
        self.assertEqual(AuditLog.objects.count(), audits)
        self.assertEqual(Notification.objects.count(), bells)


class PartnerGoalsPageTests(TestCase):
    """The screen: login, route without an id, and the rendered page."""

    def setUp(self):
        self.today = timezone.localdate()
        self.alice, self.bob, self.carol = make_user("alice"), make_user("bob"), make_user("carol")
        # (verify) url name: use the name you give the route in buddy/urls.py.
        self.url = reverse("buddy:goals")

    def test_route_takes_no_id(self):
        self.assertEqual(self.url, "/buddy/goals/")

    def test_login_required(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

    def test_page_shows_goals_but_never_notes(self):
        pair(self.alice, self.bob)
        goal = make_goal(self.bob, "Morning prayer", self.today)
        check(goal, self.today, note=NOTE)
        make_goal(self.bob, "Read a chapter", self.today)
        self.client.force_login(self.alice)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Morning prayer", body)
        self.assertIn("Still to do today", body)
        self.assertNotIn(NOTE, body)
        self.assertNotIn(NOTE, repr(response.context))

    def test_unpaired_person_gets_friendly_empty_state(self):
        make_goal(self.bob, "Hidden", self.today)
        self.client.force_login(self.carol)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Hidden", response.content.decode())
        self.assertContains(response, "do not have a buddy yet")

    def test_inactive_partner_page_hides_goals(self):
        pair(self.alice, self.bob)
        make_goal(self.bob, "Hidden", self.today)
        self.client.force_login(self.alice)
        with cannot_sign_in(self.bob):
            response = self.client.get(self.url)
        self.assertNotIn("Hidden", response.content.decode())
        self.assertContains(response, "can no longer sign in")
