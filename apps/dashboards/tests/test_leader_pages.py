"""Brick 2: the three leader dashboards draw charts, tiles and widgets from the Brick 1 numbers.

Covers: every leader role renders, the extension picker (admin and super-admin only), a coordinator never leaving
their extension (even with a forged ``?extension=``), the prayer card hiding rows when it has a scope note, the
real events card replacing "Coming soon", the giving card, the chart markup (role, aria-label, text values) and the
small template tags the pages rely on.
"""
from types import SimpleNamespace
from unittest import mock

import re

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Extension, Role
from apps.accounts.templatetags import people_tags
from apps.attendance.age import AgeNetwork
from apps.dashboards import services
from apps.dashboards.templatetags.chart_tags import chart_slot

from .helpers import add_finalized_report, add_uploaded_sheet, make_extension, make_person, sundays

URLS = {
    Role.SUPER_ADMIN: "dashboards:superadmin",
    Role.ADMIN: "dashboards:admin",
    Role.COORDINATOR: "dashboards:coordinator",
}


class LeaderPageBase(TestCase):
    """Two extensions with data, one person of every role."""

    def setUp(self):
        self.alpha, self.beta = make_extension("alpha"), make_extension("betaville")
        self.people = {
            Role.SUPER_ADMIN: make_person(Role.SUPER_ADMIN),
            Role.ADMIN: make_person(Role.ADMIN),
            Role.COORDINATOR: make_person(Role.COORDINATOR, self.alpha),
            Role.MEMBER: make_person(Role.MEMBER, self.alpha),
        }
        self.day1, self.day2 = sundays(2)
        sheet_a1 = add_uploaded_sheet(self.alpha, self.day1, 10, first_timers=1, cells={("members", "male", AgeNetwork.values[0]): 4})
        add_uploaded_sheet(self.alpha, self.day2, 15, first_timers=2)
        sheet_b = add_uploaded_sheet(self.beta, self.day2, 7777, first_timers=5)
        add_finalized_report(sheet_a1, "150.00", "20.00")
        add_finalized_report(sheet_b, "4321.50", "10.00")

    def get(self, role, query=""):
        """Open a role's dashboard as that role."""
        self.client.force_login(self.people[role])
        return self.client.get(reverse(URLS[role]) + query)


class EveryLeaderRendersTests(LeaderPageBase):
    """200 for each role, with the same building blocks."""

    def test_each_leader_role_renders_the_new_blocks(self):
        for role in URLS:
            response = self.get(role)
            self.assertEqual(response.status_code, 200, role)
            for text in ("Overview", "Attendance", "People by role", "People by status", "Upcoming events", "Giving"):
                self.assertContains(response, text, msg_prefix=str(role))

    def test_tiles_show_the_headline_numbers_as_text(self):
        response = self.get(Role.ADMIN)
        for text in ("Last gathering", "Average attendance", "Extensions reporting"):
            self.assertContains(response, text)

    def test_member_page_has_no_leader_blocks_and_the_real_events_card(self):
        """Brick 3: the member home is the personal dashboard, so its placeholder became the real events card."""
        self.client.force_login(self.people[Role.MEMBER])
        response = self.client.get(reverse("dashboards:member"))
        self.assertNotContains(response, "Overview")
        self.assertNotContains(response, "People by role")
        self.assertNotContains(response, "coming-soon-heading")
        self.assertContains(response, "events-heading")

    def test_leaders_no_longer_see_the_coming_soon_placeholder(self):
        for role in URLS:
            # the heading id, not the words: template header comments are sent to the browser too
            self.assertNotContains(self.get(role), "coming-soon-heading", msg_prefix=str(role))

    def test_empty_system_still_renders(self):
        """No sheets, no reports: every chart says so instead of drawing nothing or failing."""
        from apps.attendance.models import Attendance
        from apps.tithes.models import TithesOffering
        TithesOffering.objects.all().delete()
        Attendance.objects.all().delete()
        response = self.get(Role.ADMIN)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No uploaded attendance sheets yet.")
        self.assertContains(response, "No finalized tithes reports")


class ChartMarkupTests(LeaderPageBase):
    """Charts are inline SVG with a text alternative and the values as text."""

    def test_line_chart_has_role_label_and_values(self):
        response = self.get(Role.COORDINATOR)
        self.assertContains(response, 'role="img"')
        label = f"Attendance: {services._day_label(self.day1)} 10, {services._day_label(self.day2)} 15"
        self.assertContains(response, f'aria-label="{label}"')
        self.assertContains(response, "<title>")

    def test_charts_use_token_classes_only(self):
        """No hex colors and no chart library: SVG colors come from classes (stroke-accent, chart-fill-1...)."""
        html = self.get(Role.ADMIN).content.decode()
        section = html[html.index("overview-heading"):html.index("giving-heading")]
        self.assertIsNone(re.search(r'(fill|stroke)="#', section))
        self.assertIsNone(re.search(r"(fill|stroke|color):\s*#", section))
        self.assertNotIn("chart.js", section.lower())

    def test_donut_legend_prints_counts(self):
        """The donut is never the only carrier of a number: label, count and percent are text next to it."""
        response = self.get(Role.COORDINATOR)
        self.assertContains(response, "Coordinator")
        self.assertContains(response, "Member")
        self.assertContains(response, "%")

    def test_giving_card_is_plain_text_with_a_hidden_trend_list(self):
        response = self.get(Role.ADMIN)
        self.assertContains(response, "₱4,471.50")      # tithes 150 + 4321.50
        self.assertContains(response, 'class="sr-only"')

    def test_chart_slot_wraps_around_the_five_colors(self):
        self.assertEqual([chart_slot(i) for i in range(7)], [1, 2, 3, 4, 5, 1, 2])
        self.assertEqual(chart_slot("x"), 1)

    def test_long_labels_are_shortened_with_the_full_name_kept(self):
        long_name = "A very long extension name that will not fit"
        self.assertLessEqual(len(services._ellipsize(long_name)), services.LABEL_LIMIT)
        chart = services._hbar_chart("T", "c", [{"name": long_name, "count": 3}])
        self.assertEqual(chart["rows"][0]["full"], long_name)
        self.assertTrue(chart["rows"][0]["label"].endswith("…"))


class ExtensionFilterOnThePageTests(LeaderPageBase):
    """The picker exists for admin and super-admin only."""

    def test_admin_and_super_admin_get_the_picker_with_live_extensions_only(self):
        Extension.objects.create(name="gone", archived_at=timezone.now())
        for role in (Role.ADMIN, Role.SUPER_ADMIN):
            response = self.get(role)
            self.assertContains(response, 'id="filter-extension"')
            self.assertContains(response, "All extensions")
            self.assertContains(response, ">Betaville</option>")
            self.assertNotContains(response, ">Gone</option>")

    def test_admin_filter_narrows_the_numbers(self):
        response = self.get(Role.ADMIN, f"?extension={self.alpha.pk}")
        dashboard = response.context["dashboard"]
        self.assertEqual(dashboard["filter"]["extension_id"], self.alpha.pk)
        self.assertEqual(dashboard["filter"]["label"], "Alpha")
        self.assertEqual([p["present"] for p in dashboard["attendance_stats"]["points"]], [10, 15])
        self.assertContains(response, 'value="%d" selected' % self.alpha.pk)

    def test_bad_extension_value_falls_back_to_all(self):
        for raw in ("abc", "-1", "999999", "1 or 1=1", ""):
            response = self.get(Role.ADMIN, f"?extension={raw}")
            self.assertEqual(response.status_code, 200)
            dashboard = response.context["dashboard"]
            self.assertIsNone(dashboard["filter"]["extension_id"], raw)
            self.assertEqual(dashboard["filter"]["label"], "All extensions")

    def test_comparison_ignores_the_filter(self):
        response = self.get(Role.ADMIN, f"?extension={self.alpha.pk}")
        self.assertContains(response, "Betaville")      # still in the comparison

    def test_coordinator_has_no_picker_and_no_comparison(self):
        response = self.get(Role.COORDINATOR)
        self.assertNotContains(response, 'id="filter-extension"')
        self.assertNotContains(response, "Needs a look")
        self.assertNotContains(response, "Attendance per extension")
        self.assertContains(response, "Numbers for Alpha")


class CoordinatorStaysInTheirExtensionTests(LeaderPageBase):
    """A forged ``?extension=`` never moves a coordinator out of their own extension."""

    def test_forged_extension_shows_nothing_of_another_extension(self):
        response = self.get(Role.COORDINATOR, f"?extension={self.beta.pk}")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("Betaville", html)
        self.assertIsNone(re.search(r"(?<!\w)7777(?!\w)", html))   # a whole number, not a piece of a token
        self.assertNotIn("4,321.50", html)
        self.assertEqual(response.context["dashboard"]["filter"]["label"], "Alpha")
        self.assertContains(response, "Numbers for Alpha")

    def test_forged_extension_gives_the_same_numbers_as_none(self):
        plain = self.get(Role.COORDINATOR).context["dashboard"]
        forged = self.get(Role.COORDINATOR, f"?extension={self.beta.pk}").context["dashboard"]
        self.assertEqual(plain["attendance_stats"]["values"], forged["attendance_stats"]["values"])
        self.assertEqual(plain["giving_stats"]["total"], forged["giving_stats"]["total"])

    def test_member_cannot_open_a_leader_page(self):
        self.client.force_login(self.people[Role.MEMBER])
        response = self.client.get(reverse("dashboards:admin") + f"?extension={self.alpha.pk}")
        self.assertRedirects(response, reverse("dashboards:member"), fetch_redirect_response=False)


class PrayerCardTests(LeaderPageBase):
    """The card shows counts and labels only, and rows disappear behind a scope note."""

    def summary(self, **extra):
        data = {
            "open": 2, "replied": 1, "scope_note": "",
            "latest": [
                {"id": 1, "label": "Row Label One", "status": "open", "status_label": "Open",
                 "created_at": timezone.now(), "extension": "Alpha"},
            ],
        }
        data.update(extra)
        return data

    def test_rows_show_when_there_is_no_scope_note(self):
        with mock.patch("apps.dashboards.services.prayer_summary", return_value=self.summary()):
            response = self.get(Role.ADMIN)
        self.assertContains(response, "Prayer requests")
        self.assertContains(response, "Row Label One")

    def test_rows_and_counts_are_hidden_when_scope_note_is_set(self):
        note = services.PRAYER_SCOPE_NOTE
        with mock.patch("apps.dashboards.services.prayer_summary", return_value=self.summary(scope_note=note)):
            response = self.get(Role.ADMIN, f"?extension={self.alpha.pk}")
        self.assertContains(response, "coordinator handles its prayer requests")
        self.assertNotContains(response, "Row Label One")

    def test_real_filtered_page_shows_the_note_for_an_extension_with_a_coordinator(self):
        response = self.get(Role.ADMIN, f"?extension={self.alpha.pk}")
        self.assertContains(response, "coordinator handles its prayer requests")

    def test_card_is_absent_when_the_person_does_not_handle_prayer(self):
        with mock.patch("apps.dashboards.services.prayer_summary", return_value=None):
            response = self.get(Role.ADMIN)
        self.assertNotContains(response, "Prayer requests")


class EventsCardTests(LeaderPageBase):
    """The real events card replaces the placeholder."""

    def test_events_show_with_a_global_badge_and_an_extension_badge(self):
        now = timezone.now()
        rows = [
            {"id": 1, "title": "Church Anniversary", "start_at": now, "end_at": None, "is_global": True, "extension": ""},
            {"id": 2, "title": "Alpha Cell Night", "start_at": now, "end_at": None, "is_global": False, "extension": "Alpha"},
        ]
        with mock.patch("apps.dashboards.services.upcoming_events", return_value=rows):
            response = self.get(Role.COORDINATOR)
        self.assertContains(response, "Church Anniversary")
        self.assertContains(response, "Global")
        self.assertContains(response, "Alpha Cell Night")

    def test_empty_state(self):
        with mock.patch("apps.dashboards.services.upcoming_events", return_value=[]):
            response = self.get(Role.ADMIN)
        self.assertContains(response, "No upcoming events.")


class ContextAndTagTests(LeaderPageBase):
    """The new context keys and the two template-tag changes."""

    def test_member_context_has_no_tiles_or_charts(self):
        context = services.get_dashboard_context(self.people[Role.MEMBER], None, "/")
        self.assertIsNone(context["tiles"])
        self.assertIsNone(context["charts"])

    def test_leader_context_has_tiles_and_charts(self):
        context = services.get_dashboard_context(self.people[Role.ADMIN], None, "/")
        self.assertEqual(len(context["tiles"]), 4)
        self.assertEqual(context["tiles"][3]["label"], "Extensions reporting")
        self.assertIsNotNone(context["charts"]["people_bars"])
        coordinator = services.get_dashboard_context(self.people[Role.COORDINATOR], None, "/")
        self.assertEqual(coordinator["tiles"][3]["label"], "First-timers")
        self.assertIsNone(coordinator["charts"]["people_bars"])

    def test_period_given_switches_the_columns_to_every_gathering(self):
        admin = self.people[Role.ADMIN]
        latest = services.get_dashboard_context(admin, None, "/")["charts"]["attendance_columns"]
        whole = services.get_dashboard_context(admin, None, "/", start=self.day1, end=self.day2)["charts"]["attendance_columns"]
        self.assertTrue(latest["caption"].startswith("Latest gathering"))
        self.assertEqual(whole["caption"], "Every gathering in the period")

    def test_role_url_falls_back_to_the_signed_in_persons_area(self):
        """On a dashboards page the namespace is 'dashboards': the tag must use the person's own area."""
        admin = self.people[Role.ADMIN]
        context = {"request": SimpleNamespace(resolver_match=SimpleNamespace(namespace="dashboards"), user=admin)}
        self.assertEqual(people_tags.role_url(context, "prayer_inbox"), reverse("admin_area:prayer_inbox"))

    def test_role_url_keeps_the_pages_own_area(self):
        context = {"request": SimpleNamespace(resolver_match=SimpleNamespace(namespace="superadmin"), user=self.people[Role.ADMIN])}
        self.assertEqual(people_tags.role_url(context, "prayer_inbox"), reverse("superadmin:prayer_inbox"))

    def test_role_url_or_empty_gives_empty_for_a_missing_route(self):
        context = {"request": SimpleNamespace(resolver_match=SimpleNamespace(namespace="dashboards"), user=self.people[Role.ADMIN])}
        self.assertEqual(people_tags.role_url_or_empty(context, "no_such_route_anywhere"), "")
