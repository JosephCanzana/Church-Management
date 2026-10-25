"""Brick 3: the personal dashboard (``personal/``), its pieces and the Overview | Personal hub tabs.

Covers: every role opens the page; a person sees only their OWN goals and events (a coordinator and an admin too);
the Buddy card empty, live and inactive states and the partner's goals never leaking; a person who cannot use
goals gets no goals, streak or check-in card; the 7-day check-in numbers; the goal ring geometry; the hub tabs
(leaders get Overview and Personal, members none, and Overview is not lit on other pages of the same area).
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, Status
from apps.accounts.navigation import build_tabs
from apps.buddy import services as buddy_services
from apps.dashboards import services
from apps.events.models import Event
from apps.faith.models import Goal

from .helpers import make_extension, make_person

ALL_ROLES = (Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER)


class PersonalBase(TestCase):
    """Two extensions and one person of every role (the coordinator and the member are in alpha)."""

    def setUp(self):
        self.alpha, self.beta = make_extension("alpha"), make_extension("betaville")
        self.people = {
            Role.SUPER_ADMIN: make_person(Role.SUPER_ADMIN),
            Role.ADMIN: make_person(Role.ADMIN),
            Role.COORDINATOR: make_person(Role.COORDINATOR, self.alpha),
            Role.MEMBER: make_person(Role.MEMBER, self.alpha),
        }
        self.today = timezone.localdate()

    def goal(self, person, title):
        """A daily goal that started yesterday, so it is scheduled today."""
        return Goal.objects.create(
            user=person, title=title, start_date=self.today - timedelta(days=1), repeat_type="daily"
        )

    def open_personal(self, person):
        """Open the personal page as ``person``."""
        self.client.force_login(person)
        return self.client.get(reverse("dashboards:personal"))


class PersonalAccessTests(PersonalBase):
    """Open to all four roles, closed to anonymous visitors."""

    def test_anonymous_is_sent_to_login(self):
        self.assertEqual(self.client.get(reverse("dashboards:personal")).status_code, 302)

    def test_every_role_opens_the_personal_page(self):
        for role in ALL_ROLES:
            response = self.open_personal(self.people[role])
            self.assertEqual(response.status_code, 200, role)
            for text in ("Verse of the day", "Continue reading", "Buddy", "My journey", "Upcoming events"):
                self.assertContains(response, text, msg_prefix=str(role))

    def test_member_home_shows_the_same_cards(self):
        self.client.force_login(self.people[Role.MEMBER])
        response = self.client.get(reverse("dashboards:member"))
        self.assertEqual(response.status_code, 200)
        for text in ("Goals today", "Buddy", "My journey"):
            self.assertContains(response, text)

    def test_personal_page_has_no_leader_blocks_and_ignores_the_extension_filter(self):
        """No counts, no charts, no picker, and a forged ?extension= changes nothing."""
        self.client.force_login(self.people[Role.ADMIN])
        response = self.client.get(reverse("dashboards:personal") + f"?extension={self.beta.pk}")
        self.assertNotContains(response, "People by role")
        self.assertNotContains(response, "Extensions reporting")

    def test_context_has_the_personal_keys_and_no_leader_keys(self):
        context = services.get_personal_context(self.people[Role.COORDINATOR], None, "/")
        for key in ("verse", "continue_reading", "streaks", "goals_today", "checkin", "buddy", "journey", "upcoming_events"):
            self.assertIn(key, context)
        for key in ("charts", "tiles", "giving_stats", "extension_stats"):
            self.assertNotIn(key, context)


class PersonalDataIsOwnTests(PersonalBase):
    """A person sees only their own goals and the events they may see."""

    def test_leaders_see_only_their_own_goals(self):
        self.goal(self.people[Role.COORDINATOR], "COORD-OWN-GOAL")
        self.goal(self.people[Role.ADMIN], "ADMIN-OWN-GOAL")
        self.goal(self.people[Role.MEMBER], "MEMBER-OTHER-GOAL")
        coordinator_page = self.open_personal(self.people[Role.COORDINATOR])
        self.assertContains(coordinator_page, "COORD-OWN-GOAL")
        self.assertNotContains(coordinator_page, "ADMIN-OWN-GOAL")
        self.assertNotContains(coordinator_page, "MEMBER-OTHER-GOAL")
        admin_page = self.open_personal(self.people[Role.ADMIN])
        self.assertContains(admin_page, "ADMIN-OWN-GOAL")
        self.assertNotContains(admin_page, "COORD-OWN-GOAL")

    def test_goals_card_shows_the_ring_as_text(self):
        self.goal(self.people[Role.MEMBER], "Pray")
        response = self.open_personal(self.people[Role.MEMBER])
        self.assertContains(response, "0 of 1 goals done today (0 percent).")
        self.assertContains(response, "chart-stroke-1", count=0)   # nothing done yet: no arc, only the track

    def test_events_follow_the_same_scope_as_the_events_pages(self):
        soon = timezone.now() + timedelta(days=1)
        Event.objects.create(title="GLOBAL-EVENT", start_at=soon)
        Event.objects.create(title="ALPHA-EVENT", start_at=soon, extension=self.alpha)
        Event.objects.create(title="BETA-EVENT", start_at=soon, extension=self.beta)
        response = self.open_personal(self.people[Role.COORDINATOR])
        self.assertContains(response, "GLOBAL-EVENT")
        self.assertContains(response, "ALPHA-EVENT")
        self.assertNotContains(response, "BETA-EVENT")


class GoalsAreOptionalTests(PersonalBase):
    """A person who cannot use goals sees no goals, streak or check-in card (and no hole in the grid)."""

    def test_no_goals_cards(self):
        with mock.patch("apps.dashboards.services.can_use_goals", return_value=False):
            response = self.open_personal(self.people[Role.MEMBER])
        self.assertEqual(response.status_code, 200)
        for text in ("goals-today-heading", "streaks-heading", "checkin-heading"):
            self.assertNotContains(response, text)
        for text in ("Verse of the day", "Buddy", "My journey"):
            self.assertContains(response, text)
        self.assertContains(response, "Goals are not available for your account.")

    def test_context_is_none_for_those_people(self):
        with mock.patch("apps.dashboards.services.can_use_goals", return_value=False):
            context = services.get_personal_context(self.people[Role.MEMBER], None, "/")
        self.assertIsNone(context["goals_today"])
        self.assertIsNone(context["streaks"])
        self.assertIsNone(context["checkin"])
        self.assertIsNone(context["journey"]["goals"])


class BuddyCardTests(PersonalBase):
    """Empty, live and inactive states; nothing private of the partner is ever shown."""

    def pair(self, first, second):
        """Make ``first`` and ``second`` live buddies through the real services."""
        request = buddy_services.send_request(first, second.pk)
        buddy_services.accept_request(second, request.pk)

    def test_empty_state_when_there_is_no_buddy(self):
        response = self.open_personal(self.people[Role.MEMBER])
        self.assertContains(response, "No buddy yet.")
        self.assertEqual(response.context["dashboard"]["buddy"]["state"], "none")

    def test_live_buddy_shows_the_name_and_never_their_goals(self):
        me, partner = self.people[Role.MEMBER], self.people[Role.COORDINATOR]
        partner.first_name = "Zebedee"
        partner.save()
        self.goal(partner, "PARTNER-SECRET-GOAL")
        self.pair(me, partner)
        response = self.open_personal(me)
        self.assertContains(response, "Zebedee")
        self.assertNotContains(response, "PARTNER-SECRET-GOAL")
        self.assertNotContains(response, "No buddy yet.")
        self.assertEqual(response.context["dashboard"]["buddy"]["state"], "live")

    def test_partner_who_cannot_sign_in_is_hidden(self):
        me, partner = self.people[Role.MEMBER], self.people[Role.COORDINATOR]
        partner.first_name = "Zebedee"
        partner.save()
        self.pair(me, partner)
        partner.status = Status.ARCHIVED      # an archived person cannot sign in, so the partnership is not live
        partner.archived_at = timezone.now()
        partner.save()
        response = self.open_personal(me)
        self.assertContains(response, "cannot sign in right now")
        self.assertNotContains(response, "Zebedee")


class CheckinWeekTests(TestCase):
    """The 7-day strip, from a hand-built overview (no database)."""

    @staticmethod
    def overview(opened_days, read_days, total=28):
        """Build the ``activity`` part of ``global_overview``: ``total`` cells ending today, with the given days done."""
        today = timezone.localdate()
        days = [today - timedelta(days=back) for back in range(total - 1, -1, -1)]

        def cells(done):
            return [{"date": d, "state": "done" if d in done else "missed", "label": ""} for d in days]

        done_open = {today - timedelta(days=n) for n in opened_days}
        done_read = {today - timedelta(days=n) for n in read_days}
        return {"activity": [{"key": "open", "cells": cells(done_open)}, {"key": "read", "cells": cells(done_read)}]}

    def test_none_without_an_overview(self):
        self.assertIsNone(services.checkin_week(None))

    def test_counts_only_the_last_seven_days(self):
        # opened today, yesterday and 20 days ago (outside the strip); read once, 3 days ago
        strip = services.checkin_week(self.overview(opened_days=[0, 1, 20], read_days=[3]))
        self.assertEqual((strip["days"], strip["opened"], strip["read"]), (7, 2, 1))
        self.assertEqual(len(strip["rows"]), 7)
        self.assertIn("opened the app on 2 days", strip["aria"])

    def test_every_bar_has_a_series_label_and_the_strip_has_a_view_box(self):
        strip = services.checkin_week(self.overview([0], [0]))
        names = {bar["series_label"] for group in strip["chart"]["groups"] for bar in group["bars"]}
        self.assertEqual(names, {"Opened the app", "Read the Bible"})
        self.assertEqual(len(strip["chart"]["view_box"].split()), 4)

    def test_nothing_done_is_not_a_failure(self):
        strip = services.checkin_week(self.overview([], []))
        self.assertEqual((strip["opened"], strip["read"]), (0, 0))
        self.assertTrue(strip["chart"]["empty"])


class GoalRingTests(TestCase):
    """The ring is a two-segment donut from charts.donut; zero goals never divide by zero."""

    def summary(self, done, total):
        return {"done": done, "total": total, "remaining": total - done}

    def test_partly_done(self):
        ring = services._goal_ring(self.summary(3, 5))
        self.assertEqual(ring["pct"], 60)
        self.assertEqual(ring["aria"], "3 of 5 goals done today (60 percent).")
        self.assertEqual([arc["label"] for arc in ring["donut"]["arcs"]], ["Done", "To do"])

    def test_all_done_draws_one_arc(self):
        ring = services._goal_ring(self.summary(4, 4))
        self.assertEqual(ring["pct"], 100)
        self.assertEqual(len(ring["donut"]["arcs"]), 1)

    def test_nothing_scheduled_is_an_empty_track(self):
        ring = services._goal_ring(self.summary(0, 0))
        self.assertEqual(ring["pct"], 0)
        self.assertTrue(ring["donut"]["empty"])
        self.assertEqual(ring["aria"], "No goals scheduled today.")


class JourneyCardTests(PersonalBase):
    """Empty states are clean, and the goals numbers come from the person's own goals."""

    def test_empty_states(self):
        response = self.open_personal(self.people[Role.MEMBER])
        self.assertContains(response, "Services you attend will show here.")
        self.assertContains(response, "You have no goals yet.")

    def test_recent_services_are_listed_when_the_source_returns_rows(self):
        rows = [{"date": self.today, "label": "Sunday Service"}]
        with mock.patch("apps.dashboards.services._my_attended_services", return_value=rows):
            response = self.open_personal(self.people[Role.MEMBER])
        self.assertContains(response, "Sunday Service")
        self.assertNotContains(response, "Services you attend will show here.")

    def test_goals_this_week_numbers_are_the_persons_own(self):
        self.goal(self.people[Role.MEMBER], "Walk")
        journey = services.journey_summary(
            self.people[Role.MEMBER], {"has_goals": True, "week": {"done": 2, "expected": 5}, "perfect": {"current": 1, "best": 3}}
        )
        self.assertEqual(journey["goals"], {"has_goals": True, "done": 2, "expected": 5, "streak": 1, "best_streak": 3})


class DashboardHubTabsTests(PersonalBase):
    """Overview | Personal for leaders; none for members; Overview never lit on other pages of the area."""

    def labels(self, role, path="/"):
        return [tab["label"] for tab in build_tabs("dashboard", role, path)]

    def test_leaders_get_overview_and_personal(self):
        for role in (Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR):
            self.assertEqual(self.labels(role), ["Overview", "Personal"], role)

    def test_members_have_no_tabs(self):
        self.assertEqual(self.labels(Role.MEMBER), [])

    def test_each_tab_is_lit_only_on_its_own_page(self):
        tabs = build_tabs("dashboard", Role.ADMIN, reverse("dashboards:admin"))
        self.assertEqual([t["active"] for t in tabs], [True, False])
        tabs = build_tabs("dashboard", Role.ADMIN, reverse("dashboards:personal"))
        self.assertEqual([t["active"] for t in tabs], [False, True])

    def test_overview_is_not_lit_on_other_pages_below_the_same_path(self):
        """/admin/ is a prefix of every page of the admin area: an exact tab must not match them."""
        below = reverse("dashboards:admin") + "users/"
        tabs = build_tabs("dashboard", Role.ADMIN, below)
        self.assertEqual([t["active"] for t in tabs], [False, False])

    def test_leaders_see_the_tabs_on_both_pages(self):
        self.client.force_login(self.people[Role.COORDINATOR])
        for name in ("dashboards:coordinator", "dashboards:personal"):
            response = self.client.get(reverse(name))
            self.assertContains(response, 'aria-label="Section"')
            self.assertContains(response, ">Personal<")
            self.assertContains(response, ">Overview<")

    def test_member_sees_no_tab_row(self):
        self.client.force_login(self.people[Role.MEMBER])
        self.assertNotContains(self.client.get(reverse("dashboards:member")), 'aria-label="Section"')
