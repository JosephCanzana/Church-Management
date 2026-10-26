"""Brick 4: the period on the overview (quick links, custom dates, giving switch, tables) and the A4 print report.

Covers: access (member and anonymous turned away, each leader gets the overview and the print page), no Reports tab or
screen route (the overview IS the report on screen), period validation (``parse_period``: presets, custom dates, bad
values, more than 366 days), the quick links (they keep the extension and the giving switch), the extension filter (a
coordinator never leaves their extension, even with a forged ``?extension=``), the numbers equal between the overview
and the print page for the same scope and period, the print page is standalone (no Tailwind, no token classes) and has
tables before charts, privacy (counts only for prayer on paper, no birth date, phone number or address), the giving
switch, that every overview card leads to its page (tiles, chart titles, giving, tables), and that the print page
writes nothing.
"""
from datetime import date
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse

from apps.accounts import navigation
from apps.accounts.models import Role
from apps.attendance.age import AgeNetwork
from apps.attendance.models import Attendance
from apps.dashboards import services
from apps.tithes.models import TithesOffering

from .helpers import add_finalized_report, add_uploaded_sheet, make_extension, make_person, sundays

SCREEN = {   # the overview IS the report on screen
    Role.SUPER_ADMIN: "dashboards:superadmin",
    Role.ADMIN: "dashboards:admin",
    Role.COORDINATOR: "dashboards:coordinator",
}
PRINT = {
    Role.SUPER_ADMIN: "superadmin:report_print",
    Role.ADMIN: "admin_area:report_print",
    Role.COORDINATOR: "coordinator_area:report_print",
}
OCTOBER = "preset=custom&start=2026-10-01&end=2026-10-31"   # fixed dates, so the tests do not depend on today


class ParsePeriodTests(SimpleTestCase):
    """parse_period never raises: anything it cannot accept becomes "last 3 months" and says so."""

    TODAY = date(2026, 10, 25)

    def parse(self, preset=None, start=None, end=None):
        """Call parse_period with a fixed "today"."""
        return services.parse_period(preset, start, end, self.TODAY)

    def test_each_preset_has_the_expected_range(self):
        expected = {
            "this_month": (date(2026, 10, 1), date(2026, 10, 25)),
            "last_month": (date(2026, 9, 1), date(2026, 9, 30)),
            "last_3_months": (date(2026, 7, 26), date(2026, 10, 25)),
            "this_year": (date(2026, 1, 1), date(2026, 10, 25)),
        }
        for preset, (start, end) in expected.items():
            period = self.parse(preset)
            self.assertEqual((period["start"], period["end"]), (start, end), preset)
            self.assertEqual(period["preset"], preset)
            self.assertFalse(period["fell_back"], preset)

    def test_last_month_in_january_is_december_of_the_year_before(self):
        period = services.parse_period("last_month", None, None, date(2026, 1, 15))
        self.assertEqual((period["start"], period["end"]), (date(2025, 12, 1), date(2025, 12, 31)))

    def test_three_months_back_clamps_the_day(self):
        period = services.parse_period("last_3_months", None, None, date(2026, 5, 31))
        self.assertEqual(period["start"], date(2026, 3, 1))     # Feb 28 + 1 day: May 31 minus 3 months clamps to Feb 28

    def test_empty_preset_is_the_default_without_a_warning(self):
        for raw in (None, "", "   "):
            period = self.parse(raw)
            self.assertEqual(period["preset"], "last_3_months")
            self.assertFalse(period["fell_back"], repr(raw))

    def test_unknown_preset_falls_back_and_says_so(self):
        for raw in ("forever", "THIS_MONTH", "1 or 1=1", "x" * 500):
            period = self.parse(raw)
            self.assertEqual(period["preset"], "last_3_months", repr(raw))
            self.assertTrue(period["fell_back"], repr(raw))

    def test_custom_dates_are_used(self):
        period = self.parse("custom", "2026-10-01", "2026-10-31")
        self.assertEqual((period["start"], period["end"], period["days"]), (date(2026, 10, 1), date(2026, 10, 31), 31))
        self.assertEqual(period["preset"], "custom")
        self.assertFalse(period["fell_back"])
        self.assertEqual(period["label"], "Oct 1 to Oct 31, 2026")

    def test_one_day_and_366_days_are_allowed(self):
        self.assertFalse(self.parse("custom", "2026-10-01", "2026-10-01")["fell_back"])
        self.assertFalse(self.parse("custom", "2025-10-25", "2026-10-25")["fell_back"])   # 366 days, inclusive

    def test_bad_custom_periods_fall_back(self):
        bad = [
            (None, None), ("", "2026-10-31"), ("2026-10-01", ""), ("nope", "2026-10-31"), ("2026-13-01", "2026-10-31"),
            ("2026-02-30", "2026-03-01"), ("20261001", "20261031"), ("2026-10-01 ", "2026-10-3"),
            ("2026-10-31", "2026-10-01"),                      # the end is before the start
            ("2025-10-24", "2026-10-25"),                      # 367 days
            ("2000-01-01", "2026-10-25"),                      # many years
            ("٢٠٢٦-١٠-٠١", "2026-10-31"),                       # non-ASCII digits
        ]
        for start, end in bad:
            period = self.parse("custom", start, end)
            self.assertEqual(period["preset"], "last_3_months", (start, end))
            self.assertTrue(period["fell_back"], (start, end))
            self.assertEqual(period["start"], date(2026, 7, 26), (start, end))

    def test_dates_are_ignored_unless_the_preset_is_custom(self):
        period = self.parse("this_month", "2020-01-01", "2020-12-31")
        self.assertEqual(period["start"], date(2026, 10, 1))

    def test_label_names_both_years_when_they_differ(self):
        self.assertEqual(services._period_label(date(2025, 12, 28), date(2026, 1, 4)), "Dec 28, 2025 to Jan 4, 2026")


class FlagTests(SimpleTestCase):
    """The giving checkbox: a hidden 0 first, then 1 when ticked; the last value wins; missing means the default."""

    def test_values(self):
        self.assertTrue(services._flag("1", False))
        self.assertFalse(services._flag("0", True))
        self.assertTrue(services._flag(None, True))
        self.assertFalse(services._flag("", False))
        self.assertTrue(services._flag("maybe", True))
        self.assertFalse(services._flag("OFF", True))


class ReportBase(TestCase):
    """Two extensions with data inside October 2026 and one sheet outside it; one person of every role."""

    def setUp(self):
        self.alpha, self.beta = make_extension("alpha"), make_extension("betaville")
        self.people = {
            Role.SUPER_ADMIN: make_person(Role.SUPER_ADMIN),
            Role.ADMIN: make_person(Role.ADMIN),
            Role.COORDINATOR: make_person(Role.COORDINATOR, self.alpha),
            Role.MEMBER: make_person(Role.MEMBER, self.alpha),
        }
        self.day1, self.day2 = sundays(2)                    # Oct 11 and Oct 18, 2026
        sheet_a1 = add_uploaded_sheet(
            self.alpha, self.day1, 10, first_timers=1, cells={("members", "male", AgeNetwork.values[0]): 4}
        )
        add_uploaded_sheet(self.alpha, self.day2, 15, first_timers=2)
        sheet_b = add_uploaded_sheet(self.beta, self.day2, 7777, first_timers=5)
        add_finalized_report(sheet_a1, "150.00", "20.00")
        add_finalized_report(sheet_b, "4321.50", "10.00")
        # Outside October: it must never reach a report for October.
        june = add_uploaded_sheet(self.alpha, date(2026, 6, 7), 31337, first_timers=1)
        add_finalized_report(june, "99999.00", "0.00")

    def open(self, route, role, query=OCTOBER):
        """Open a route (the overview or the print page) as ``role`` with a querystring."""
        self.client.force_login(self.people[role])
        return self.client.get(reverse(route) + ("?" + query if query else ""))

    def context(self, role, **params):
        """Call the service directly (October unless told otherwise)."""
        data = {"preset": "custom", "start": "2026-10-01", "end": "2026-10-31", **params}
        return services.get_report_context(self.people[role], data, today=date(2026, 10, 25))


class AccessTests(ReportBase):
    """Only the three leader roles may open the print page; the overview keeps its own role checks."""

    def test_each_leader_role_opens_the_overview_and_the_print_page(self):
        for role in SCREEN:
            overview = self.open(SCREEN[role], role)
            self.assertEqual(overview.status_code, 200, role)
            self.assertContains(overview, "Print report", msg_prefix=str(role))
            self.assertEqual(self.open(PRINT[role], role).status_code, 200, role)

    def test_there_is_no_report_screen_route(self):
        for name in ("superadmin:report", "admin_area:report", "coordinator_area:report"):
            with self.assertRaises(NoReverseMatch):
                reverse(name)

    def test_member_is_turned_away(self):
        for route in (PRINT[Role.COORDINATOR], PRINT[Role.ADMIN], PRINT[Role.SUPER_ADMIN]):
            response = self.open(route, Role.MEMBER)
            self.assertIn(response.status_code, (302, 403), route)
            self.assertNotContains(response, "Church report", status_code=response.status_code)

    def test_anonymous_is_sent_to_sign_in(self):
        for route in (PRINT[Role.SUPER_ADMIN], PRINT[Role.ADMIN], PRINT[Role.COORDINATOR]):
            self.assertIn(self.client.get(reverse(route)).status_code, (302, 403), route)

    def test_the_service_refuses_a_member_on_its_own(self):
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            services.get_report_context(self.people[Role.MEMBER], {})


class NavigationTests(ReportBase):
    """There is no Reports tab: the dashboard hub is Overview | Personal, and members have no tabs."""

    def test_each_leader_sees_overview_and_personal_only(self):
        for role in SCREEN:
            person = self.people[role]
            tabs = navigation.build_tabs("dashboard", person.role, reverse(SCREEN[role]))
            self.assertEqual([t["label"] for t in tabs], ["Overview", "Personal"], role)
            self.assertEqual([t["label"] for t in tabs if t["active"]], ["Overview"], role)

    def test_member_has_no_dashboard_tabs(self):
        self.assertEqual(navigation.build_tabs("dashboard", self.people[Role.MEMBER].role, "/home/"), [])

    def test_the_print_button_resolves_and_carries_the_validated_query(self):
        self.client.force_login(self.people[Role.ADMIN])
        response = self.client.get(reverse("dashboards:admin") + f"?extension={self.beta.pk}")
        self.assertContains(
            response, f'{reverse(PRINT[Role.ADMIN])}?preset=last_3_months&amp;extension={self.beta.pk}&amp;giving=1"'
        )


class ScopeTests(ReportBase):
    """Coordinators stay in their extension; admin and super-admin may filter; a bad value means All."""

    def test_coordinator_never_leaves_their_extension_even_with_a_forged_filter(self):
        for route in (SCREEN[Role.COORDINATOR], PRINT[Role.COORDINATOR]):
            response = self.open(route, Role.COORDINATOR, f"{OCTOBER}&extension={self.beta.pk}")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Alpha")
            self.assertNotContains(response, "Betaville")
            self.assertNotRegex(response.content.decode(), r"(?<!\w)7777(?!\w)")
        context = self.context(Role.COORDINATOR, extension=str(self.beta.pk))
        self.assertEqual(context["attendance_stats"]["total_present"], 25)   # 10 + 15, the coordinator's own sheets
        self.assertNotIn("extension=", context["query"])                     # a coordinator never carries a filter
        self.assertIsNone(context["extension_stats"])
        self.assertIsNone(context["extension_table"])

    def test_admin_and_super_admin_can_filter_to_one_extension(self):
        for role in (Role.ADMIN, Role.SUPER_ADMIN):
            context = self.context(role, extension=str(self.beta.pk))
            self.assertEqual(context["attendance_stats"]["total_present"], 7777, role)
            self.assertEqual(context["filter"]["label"], "Betaville", role)
            self.assertIn(f"extension={self.beta.pk}", context["query"], role)
            self.assertIsNone(context["extension_table"], role)          # one extension picked: no comparison table
            response = self.open(SCREEN[role], role, f"{OCTOBER}&extension={self.beta.pk}")
            self.assertRegex(response.content.decode(), r"(?<!\w)7777(?!\w)")

    def test_admin_with_a_bad_filter_gets_all_extensions(self):
        for raw in ("abc", "-1", "999999", "1 or 1=1", ""):
            context = self.context(Role.ADMIN, extension=raw)
            self.assertEqual(context["attendance_stats"]["total_present"], 10 + 15 + 7777, raw)
            self.assertEqual(context["filter"]["label"], "All extensions", raw)
            self.assertNotIn("extension=", context["query"], raw)

    def test_all_extensions_has_the_extension_table(self):
        rows = {row["name"]: row for row in self.context(Role.ADMIN)["extension_table"]}
        self.assertEqual(rows["Alpha"]["present"], 25)
        self.assertEqual(rows["Alpha"]["gatherings"], 2)
        self.assertEqual(rows["Betaville"]["present"], 7777)


class NumbersTests(ReportBase):
    """The report adds nothing of its own: it equals the dashboard's frozen numbers for the same scope and period."""

    START, END = date(2026, 10, 1), date(2026, 10, 31)

    def test_period_keeps_other_months_out(self):
        context = self.context(Role.ADMIN)
        self.assertEqual(context["attendance_stats"]["count"], 2)             # Oct 11 and Oct 18; June is out
        self.assertEqual(context["attendance_stats"]["total_present"], 10 + 15 + 7777)
        self.assertEqual(str(context["giving_stats"]["tithes"]), "4471.50")   # June's 99,999.00 is out
        june = self.context(Role.ADMIN, start="2026-06-01", end="2026-06-30")
        self.assertEqual(june["attendance_stats"]["total_present"], 31337)

    def test_equals_the_dashboard_for_the_same_scope_and_period(self):
        for role in SCREEN:
            person = self.people[role]
            report = self.context(role)
            dashboard = services.get_dashboard_context(person, None, "/", start=self.START, end=self.END)
            for key in ("total_present", "average", "first_timers", "count", "split"):
                self.assertEqual(report["attendance_stats"][key], dashboard["attendance_stats"][key], (role, key))
            self.assertEqual(report["attendance_stats"]["points"], dashboard["attendance_stats"]["points"], role)
            for key in ("reports", "tithes", "offering", "total"):
                self.assertEqual(report["giving_stats"][key], dashboard["giving_stats"][key], (role, key))
            self.assertEqual(report["people_stats"], dashboard["people_stats"], role)

    def test_equals_the_overview_when_the_period_comes_from_the_request(self):
        """The overview and the print page read the same ?preset= / start / end and give the same numbers."""
        params = {"preset": "custom", "start": "2026-10-01", "end": "2026-10-31"}
        for role in SCREEN:
            person = self.people[role]
            overview = services.get_dashboard_context(person, None, "/", params=params)
            report = self.context(role)
            self.assertEqual(overview["attendance_stats"]["points"], report["attendance_stats"]["points"], role)
            self.assertEqual(overview["giving_stats"]["total"], report["giving_stats"]["total"], role)
            self.assertEqual(overview["people_stats"], report["people_stats"], role)
            self.assertEqual(overview["network_table"], report["network_table"], role)
            self.assertEqual(overview["attendance_gap"], report["attendance_gap"], role)

    def test_equals_the_dashboard_for_one_extension(self):
        report = self.context(Role.ADMIN, extension=str(self.beta.pk))
        dashboard = services.get_dashboard_context(
            self.people[Role.ADMIN], None, "/", extension=str(self.beta.pk), start=self.START, end=self.END
        )
        self.assertEqual(report["attendance_stats"]["total_present"], dashboard["attendance_stats"]["total_present"])
        self.assertEqual(report["giving_stats"]["total"], dashboard["giving_stats"]["total"])

    def test_network_table_adds_up(self):
        table = self.context(Role.ADMIN)["network_table"]
        self.assertEqual(table["total"], sum(table["column_totals"]))
        self.assertEqual(table["total"], sum(row["total"] for row in table["rows"]))
        self.assertEqual(sum(table["column_totals"]), 4)         # the one frozen cell that is not 0

    def test_people_without_a_network_are_named_in_a_note(self):
        context = self.context(Role.ADMIN)
        stats = context["attendance_stats"]
        self.assertEqual(context["attendance_gap"], stats["total_present"] - stats["split"]["men"] - stats["split"]["women"])
        self.assertGreater(context["attendance_gap"], 0)

    def test_drafts_and_reopened_sheets_are_not_in_a_report(self):
        Attendance.objects.filter(attendance_date=self.day1).update(status="draft")   # a reopened sheet is a draft again
        context = self.context(Role.ADMIN)
        self.assertEqual(context["attendance_stats"]["total_present"], 15 + 7777)
        self.assertEqual(str(context["giving_stats"]["tithes"]), "4321.50")

    def test_long_period_thins_the_line_labels(self):
        self.assertEqual(services._label_step(0), 1)
        self.assertEqual(services._label_step(13), 1)      # the default three months prints every date
        self.assertEqual(services._label_step(14), 2)
        self.assertEqual(services._label_step(52), 4)
        self.assertEqual(self.context(Role.ADMIN)["charts"]["attendance_line"]["label_step"], 1)

    def test_summary_is_three_plain_lines(self):
        lines = self.context(Role.ADMIN)["summary_lines"]
        self.assertEqual(len(lines), 3)
        self.assertIn("2 gatherings", lines[0])
        self.assertIn("7,802 people counted in total", lines[0])
        self.assertIn("tithes ₱4,471.50", lines[2])

    def test_empty_system_still_renders_both_pages(self):
        TithesOffering.objects.all().delete()
        Attendance.objects.all().delete()
        for route in (SCREEN[Role.ADMIN], PRINT[Role.ADMIN]):
            response = self.open(route, Role.ADMIN)
            self.assertEqual(response.status_code, 200, route)
            self.assertContains(response, "No uploaded attendance sheets in this period.")
            self.assertContains(response, "Print report" if route == SCREEN[Role.ADMIN] else "Church report")
        self.assertEqual(self.context(Role.ADMIN)["charts"]["attendance_line"]["label_step"], 1)


class OverviewPeriodTests(ReportBase):
    """What the overview does with the period quick links, the custom dates and the giving switch."""

    TODAY = date(2026, 10, 25)

    def overview(self, role=Role.ADMIN, extension=None, **params):
        """The overview context with a fixed "today" (so presets are deterministic)."""
        with mock.patch.object(services.timezone, "localdate", return_value=self.TODAY):
            return services.get_dashboard_context(
                self.people[role], None, "/", extension=extension, params=params
            )

    def test_default_view_is_the_last_8_gatherings(self):
        overview = self.overview()
        pf = overview["period_filter"]
        self.assertEqual(pf["preset"], "")
        self.assertEqual([link["label"] for link in pf["links"] if link["active"]], ["Last 8 gatherings"])
        self.assertEqual(overview["attendance_stats"]["total_present"], 31337 + 10 + 15 + 7777)   # June is in the last 8
        self.assertEqual(pf["print_query"], "preset=last_3_months&giving=1")    # 8 gatherings is not a date range

    def test_a_preset_replaces_the_default_view(self):
        overview = self.overview(preset="this_month")
        self.assertEqual(overview["period"], {"start": date(2026, 10, 1), "end": date(2026, 10, 25)})
        self.assertEqual(overview["attendance_stats"]["total_present"], 10 + 15 + 7777)   # June is out
        self.assertEqual(overview["period_filter"]["preset"], "this_month")
        self.assertEqual(overview["period_filter"]["print_query"], "preset=this_month&giving=1")
        self.assertEqual([l["label"] for l in overview["period_filter"]["links"] if l["active"]], ["This month"])

    def test_custom_dates_are_used_and_kept_in_the_print_query(self):
        overview = self.overview(preset="custom", start="2026-06-01", end="2026-06-30")
        self.assertEqual(overview["attendance_stats"]["total_present"], 31337)
        self.assertEqual(
            overview["period_filter"]["print_query"], "preset=custom&start=2026-06-01&end=2026-06-30&giving=1"
        )

    def test_a_refused_period_keeps_the_default_view_and_says_so(self):
        for params in (
            {"preset": "forever"}, {"preset": "custom", "start": "nope", "end": "2026-01-01"},
            {"preset": "custom", "start": "2025-01-01", "end": "2026-10-31"},
            {"preset": "custom", "start": "2026-10-31", "end": "2026-10-01"},
        ):
            overview = self.overview(**params)
            pf = overview["period_filter"]
            self.assertTrue(pf["fell_back"], params)
            self.assertEqual(pf["preset"], "", params)
            self.assertEqual(overview["attendance_stats"]["total_present"], 31337 + 10 + 15 + 7777, params)

    def test_a_blank_preset_is_the_default_without_a_warning(self):
        for raw in ("", "   "):
            pf = self.overview(preset=raw)["period_filter"]
            self.assertFalse(pf["fell_back"], repr(raw))
            self.assertEqual(pf["preset"], "", repr(raw))

    def test_quick_links_keep_the_extension_and_the_giving_switch(self):
        overview = self.overview(extension=str(self.beta.pk), giving="0")
        links = overview["period_filter"]["links"]
        self.assertEqual(len(links), 5)          # default view + 4 presets (custom is the form, not a chip)
        for link in links:
            self.assertIn(f"extension={self.beta.pk}", link["query"], link["label"])
            self.assertIn("giving=0", link["query"], link["label"])
        self.assertNotIn("preset=", links[0]["query"])          # the default view has no preset
        self.assertIn("preset=last_month", links[2]["query"])

    def test_a_coordinator_never_carries_an_extension(self):
        overview = self.overview(Role.COORDINATOR, extension=str(self.beta.pk), preset="this_month")
        for link in overview["period_filter"]["links"]:
            self.assertNotIn("extension=", link["query"])
        self.assertNotIn("extension=", overview["period_filter"]["print_query"])

    def test_quick_links_and_the_form_are_on_the_page(self):
        response = self.open(SCREEN[Role.ADMIN], Role.ADMIN, "")
        for text in ("Period quick links", "Last 8 gatherings", "This month", "Last month", "Last 3 months", "This year",
                     "Custom dates", 'id="filter-start"', 'id="filter-end"', "Giving: on", "Print report"):
            self.assertContains(response, text)
        self.assertContains(response, 'aria-current="true"', count=1)

    def test_a_refused_period_shows_a_notice_on_the_page(self):
        response = self.open(SCREEN[Role.ADMIN], Role.ADMIN, "preset=custom&start=nope&end=2026-01-01")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That period was not valid")
        self.assertContains(response, "last 8 gatherings")
        self.assertNotContains(self.open(SCREEN[Role.ADMIN], Role.ADMIN), "That period was not valid")

    def test_print_button_carries_the_validated_query_not_the_raw_one(self):
        response = self.open(SCREEN[Role.ADMIN], Role.ADMIN, "preset=custom&start=nope&end=%3Cscript%3E&giving=0&evil=1")
        html = response.content.decode()
        self.assertIn(f'{reverse(PRINT[Role.ADMIN])}?preset=last_3_months&amp;giving=0"', html)
        self.assertNotIn("evil", html)
        self.assertNotIn("<script>&", html)

    def test_the_giving_chip_says_its_state_and_flips_it_keeping_the_view(self):
        on = self.open(SCREEN[Role.ADMIN], Role.ADMIN, f"{OCTOBER}&giving=1").content.decode()
        off = self.open(SCREEN[Role.ADMIN], Role.ADMIN, f"{OCTOBER}&giving=0").content.decode()
        self.assertIn("Giving: on", on)
        self.assertIn("Giving: off", off)
        self.assertIn("?preset=custom&amp;start=2026-10-01&amp;end=2026-10-31&amp;giving=0", on)    # flips off, same dates
        self.assertIn("?preset=custom&amp;start=2026-10-01&amp;end=2026-10-31&amp;giving=1", off)   # flips back on

    def test_the_giving_chip_and_the_extension_form_keep_the_rest_of_the_view(self):
        pf = self.overview(extension=str(self.beta.pk), preset="this_month", giving="0")["period_filter"]
        self.assertEqual(pf["giving_query"], f"preset=this_month&giving=1&extension={self.beta.pk}")
        self.assertEqual(pf["hidden"], [("preset", "this_month"), ("giving", "0")])
        default = self.overview()["period_filter"]
        self.assertEqual(default["hidden"], [("giving", "1")])
        self.assertEqual(default["giving_query"], "giving=0")
        custom = self.overview(preset="custom", start="2026-06-01", end="2026-06-30")["period_filter"]
        self.assertEqual(custom["hidden"], [("preset", "custom"), ("start", "2026-06-01"), ("end", "2026-06-30"), ("giving", "1")])

    def test_the_extension_picker_is_a_form_that_carries_the_period(self):
        html = self.open(SCREEN[Role.ADMIN], Role.ADMIN, f"{OCTOBER}&giving=0").content.decode()
        for pair in ('name="preset" value="custom"', 'name="start" value="2026-10-01"', 'name="end" value="2026-10-31"', 'name="giving" value="0"'):
            self.assertIn(pair, html, pair)
        self.assertIn('id="filter-extension"', html)

    def test_a_coordinator_has_no_extension_picker_but_keeps_the_rest(self):
        html = self.open(SCREEN[Role.COORDINATOR], Role.COORDINATOR).content.decode()
        self.assertNotIn('id="filter-extension"', html)
        self.assertIn("Custom dates", html)

    def test_whose_numbers_and_which_dates_are_in_the_heading_not_in_the_bar(self):
        html = self.open(SCREEN[Role.ADMIN], Role.ADMIN, OCTOBER).content.decode()
        self.assertIn("All extensions, Oct 1 to Oct 31, 2026", html)
        self.assertNotIn("Showing", html)

    def test_giving_off_hides_the_giving_numbers_on_the_overview(self):
        self.assertContains(self.open(SCREEN[Role.ADMIN], Role.ADMIN, f"{OCTOBER}&giving=1"), "₱4,471.50")
        off = self.open(SCREEN[Role.ADMIN], Role.ADMIN, f"{OCTOBER}&giving=0")
        self.assertNotContains(off, "₱4,471.50")
        self.assertNotContains(off, "giving-heading")
        self.assertIsNone(self.overview(preset="this_month", giving="0")["giving_stats"])

    def test_the_tables_are_on_the_overview_for_every_leader(self):
        for role in SCREEN:
            response = self.open(SCREEN[role], role)
            for text in ("Attendance per gathering", "Men and women by age group"):
                self.assertContains(response, text, msg_prefix=str(role))
        self.assertNotContains(self.open(SCREEN[Role.COORDINATOR], Role.COORDINATOR), "Reports</a>")

    def test_a_member_never_gets_period_data(self):
        context = services.get_dashboard_context(self.people[Role.MEMBER], None, "/", params={"preset": "this_month"})
        for key in ("period_filter", "network_table", "attendance_gap", "attendance_stats", "giving_stats"):
            self.assertIsNone(context[key], key)


class CardLinkTests(ReportBase):
    """Each card of the overview leads to the page behind it, in the person's own area."""

    AREAS = {Role.SUPER_ADMIN: "superadmin", Role.ADMIN: "admin_area", Role.COORDINATOR: "coordinator_area"}

    def test_links_follow_the_persons_own_area(self):
        for role, area in self.AREAS.items():
            links = services.card_links(self.people[role])
            self.assertEqual(links["people"], reverse(f"{area}:user_list"), role)
            self.assertEqual(links["attendance"], reverse(f"{area}:sheet_list"), role)
            self.assertEqual(links["giving"], reverse(f"{area}:report_list"), role)
            self.assertEqual(links["prayer"], reverse(f"{area}:prayer_inbox"), role)

    def test_extensions_page_is_for_admin_and_super_admin_only(self):
        self.assertEqual(services.card_links(self.people[Role.COORDINATOR])["extensions"], "")
        for role in (Role.ADMIN, Role.SUPER_ADMIN):
            self.assertEqual(services.card_links(self.people[role])["extensions"], reverse("superadmin:extension_list"))

    def test_a_member_has_no_card_links(self):
        self.assertIsNone(services.card_links(self.people[Role.MEMBER]))

    def test_links_carry_no_id_and_no_querystring(self):
        for role in self.AREAS:
            for name, url in services.card_links(self.people[role]).items():
                self.assertNotIn("?", url, (role, name))

    def test_tiles_and_charts_carry_their_url(self):
        context = services.get_dashboard_context(self.people[Role.ADMIN], None, "/", params={"preset": "custom", "start": "2026-10-01", "end": "2026-10-31"})
        links = context["links"]
        urls = {tile["label"]: tile["url"] for tile in context["tiles"]}
        self.assertEqual(urls["People"], links["people"])
        self.assertEqual(urls["Last gathering"], links["attendance"])
        self.assertEqual(urls["Average attendance"], links["attendance"])
        self.assertEqual(urls["Extensions reporting"], links["extensions"])
        charts = context["charts"]
        self.assertEqual(charts["attendance_line"]["url"], links["attendance"])
        self.assertEqual(charts["attendance_columns"]["url"], links["attendance"])
        self.assertEqual(charts["donut_roles"]["url"], links["people"])
        self.assertEqual(charts["donut_status"]["url"], links["people"])
        self.assertEqual(charts["people_bars"]["url"], links["extensions"])
        self.assertEqual(charts["attendance_bars"]["url"], links["attendance"])

    def test_a_coordinators_tiles_lead_to_their_own_pages_and_never_to_extensions(self):
        context = services.get_dashboard_context(self.people[Role.COORDINATOR], None, "/")
        urls = {tile["label"]: tile["url"] for tile in context["tiles"]}
        self.assertEqual(urls["First-timers"], reverse("coordinator_area:sheet_list"))
        self.assertIsNone(context["charts"]["people_bars"])

    def test_without_links_nothing_is_linked(self):
        stats = services.attendance_stats(self.people[Role.ADMIN], None, date(2026, 10, 1), date(2026, 10, 31))
        people = services.people_stats(self.people[Role.ADMIN])
        self.assertTrue(all(tile["url"] == "" for tile in services.dashboard_tiles(stats, people, None)))
        self.assertEqual(services.leader_charts(stats, people, None, None)["attendance_line"]["url"], "")

    def test_the_page_draws_the_links(self):
        for role, area in self.AREAS.items():
            html = self.open(SCREEN[role], role).content.decode()
            for name in ("user_list", "sheet_list", "report_list"):
                self.assertIn(f'href="{reverse(f"{area}:{name}")}"', html, (role, name))
            self.assertIn("&rarr;", html, role)
        coordinator_html = self.open(SCREEN[Role.COORDINATOR], Role.COORDINATOR).content.decode()
        self.assertNotIn(reverse("superadmin:extension_list"), coordinator_html)
        admin_html = self.open(SCREEN[Role.ADMIN], Role.ADMIN).content.decode()
        self.assertIn(f'href="{reverse("superadmin:extension_list")}"', admin_html)

    def test_the_print_report_has_no_links_to_pages(self):
        html = self.open(PRINT[Role.ADMIN], Role.ADMIN).content.decode()
        self.assertNotIn(reverse("admin_area:user_list"), html)
        self.assertNotIn("&rarr;", html)


class PrintPageTests(ReportBase):
    """The A4 page is standalone, tables come first, and it prints only what the report allows."""

    def html(self, role=Role.ADMIN, query=OCTOBER):
        """The print page's HTML for ``role``."""
        return self.open(PRINT[role], role, query).content.decode()

    def test_page_is_standalone_and_black_and_white(self):
        html = self.html()
        self.assertIn("@page { size: A4", html)
        self.assertIn("black-logo.svg", html)
        for forbidden in ("tailwind", "stroke-accent", "fill-accent", "chart-fill", "chart-stroke", "chart-bg", 'class="card', "x-data", "includes/logo"):
            self.assertNotIn(forbidden, html, forbidden)
        for color in ("#000000", "#ffffff", "#bbbbbb", "#333333"):
            self.assertIn(color, html)       # the grayscale hex colors drawn inline in the two charts
        import re
        hexes = set(re.findall(r'(?:fill|stroke)="(#[0-9a-fA-F]{3,8})"', html))
        self.assertTrue(hexes <= {"#000000", "#ffffff", "#bbbbbb", "#333333"}, hexes)   # nothing but greys

    def test_header_names_the_scope_period_and_reader(self):
        html = self.html(Role.ADMIN, f"{OCTOBER}&extension={self.beta.pk}")
        for text in ("Betaville", "Oct 1 to Oct 31, 2026", "As of", "Prepared for", "Admin", "(Manila time)"):
            self.assertIn(text, html, text)
        self.assertIn("All extensions", self.html())

    def test_tables_come_before_charts_and_signatures_come_last(self):
        html = self.html()
        order = [html.index(text) for text in (
            "Attendance per gathering</h2>", "Men and women by age group</h2>", "People</h2>", "Extensions</h2>",
            "Events in this period", "Giving</h2>", "Prayer requests</h2>", "<svg", "Prepared by", "Noted by", "Approved by", "Printed",
        )]
        self.assertEqual(order, sorted(order))

    def test_it_is_not_a_print_of_the_screen_report(self):
        html = self.html()
        self.assertNotIn("Dashboard filter", html)
        self.assertNotIn("Include giving", html)
        self.assertNotIn("Period quick links", html)
        self.assertEqual(html.count('role="img"'), 2)        # exactly the two grayscale charts

    def test_every_block_avoids_a_page_break_and_tables_repeat_their_header(self):
        html = self.html()
        self.assertIn("page-break-inside: avoid", html)
        self.assertIn("thead { display: table-header-group; }", html)

    def test_giving_is_in_by_default_and_out_when_not_included(self):
        self.assertIn("4,471.50", self.html())
        self.assertIn("Included", self.html())
        off = self.html(Role.ADMIN, f"{OCTOBER}&giving=0")
        for text in ("4,471.50", "4,321.50", "150.00", "Tithes", "Offering"):
            self.assertNotIn(text, off, text)
        self.assertIn("Not included", off)
        self.assertIsNone(self.context(Role.ADMIN, giving="0")["giving_stats"])
        self.assertIsNone(self.context(Role.ADMIN, giving="0")["charts"]["giving_spark"])

    def test_no_birth_date_phone_address_or_name(self):
        text = self.html().lower()
        for forbidden in ("birth", "phone", "address", "password"):
            self.assertNotIn(forbidden, text, forbidden)
        for person in self.people.values():
            self.assertNotIn(f"{person.first_name} {person.last_name}".lower(), text)   # no person's name at all

    def test_prayer_is_counts_only(self):
        """Even if the prayer summary carries rows, the report forwards only open, completed and the scope note."""
        loaded = {
            "open": 4, "replied": 9, "scope_note": "",
            "latest": [{"id": 1, "label": "SECRET NAME", "status": "open", "status_label": "Open",
                        "created_at": None, "extension": "Alpha"}],
        }
        with mock.patch.object(services, "prayer_summary", return_value=loaded):
            response = self.open(PRINT[Role.ADMIN], Role.ADMIN)
            self.assertNotContains(response, "SECRET NAME")
            context = self.context(Role.ADMIN)
        self.assertEqual(context["prayer"], {"open": 4, "replied": 9, "scope_note": ""})
        self.assertNotIn("SECRET NAME", repr(context))

    def test_prayer_scope_note_is_printed(self):
        loaded = {"open": 0, "replied": 0, "latest": [], "scope_note": services.PRAYER_SCOPE_NOTE}
        with mock.patch.object(services, "prayer_summary", return_value=loaded):
            self.assertIn("coordinator handles its prayer requests", self.html())


class ReadOnlyTests(ReportBase):
    """The print page is a plain GET: it changes and logs nothing (the overview may write today's daily_verse row)."""

    def test_the_print_page_writes_nothing_to_the_database(self):
        self.client.force_login(self.people[Role.ADMIN])
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(reverse(PRINT[Role.ADMIN]) + "?" + OCTOBER).status_code, 200)
        writes = [
            q["sql"][:80] for q in queries.captured_queries
            if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) and "django_session" not in q["sql"]
        ]
        self.assertEqual(writes, [])

    def test_the_print_page_never_calls_get_dashboard_context(self):
        """get_dashboard_context writes today's daily_verse row; the print report must never call it."""
        with mock.patch.object(services, "get_dashboard_context", side_effect=AssertionError("called")):
            self.assertEqual(self.open(PRINT[Role.ADMIN], Role.ADMIN).status_code, 200)
