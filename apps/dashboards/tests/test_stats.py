"""Dashboard stats: frozen sums, only uploaded sheets, prayer scope note and events scope."""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Role, Status
from apps.attendance.models import Attendance
from apps.dashboards import services
from apps.events.models import Event
from apps.prayer.models import PrayerRequest

from .helpers import (
    add_draft_sheet, add_finalized_report, add_uploaded_sheet, make_extension, make_person, summary_json, sundays,
)


class AttendanceStatsTests(TestCase):
    """attendance_stats reads frozen values from uploaded sheets only."""

    def setUp(self):
        self.alpha, self.beta = make_extension("alpha"), make_extension("beta")
        self.admin = make_person(Role.ADMIN)
        self.coordinator = make_person(Role.COORDINATOR, self.alpha)
        self.d1, self.d2, self.d3 = sundays(3)

    def test_no_sheets_gives_empty_but_complete_stats(self):
        stats = services.attendance_stats(self.admin)
        self.assertEqual((stats["points"], stats["count"], stats["average"]), ([], 0, 0))
        self.assertIsNone(stats["highest"])
        self.assertIsNone(stats["lowest"])
        self.assertIsNone(stats["latest"])
        self.assertIsNone(stats["period"])
        self.assertEqual(stats["split"], {"men": 0, "women": 0})
        self.assertEqual(stats["network"]["total"], 0)

    def test_sheets_on_the_same_date_are_added_and_values_are_frozen(self):
        add_uploaded_sheet(self.alpha, self.d1, 10, first_timers=2)
        add_uploaded_sheet(self.beta, self.d1, 15, first_timers=1)
        add_uploaded_sheet(self.alpha, self.d2, 12, first_timers=0)
        stats = services.attendance_stats(self.admin)
        self.assertEqual(stats["values"], [25, 12])
        self.assertEqual(stats["labels"], [services._day_label(self.d1), services._day_label(self.d2)])
        self.assertEqual(stats["first_timers"], 3)
        self.assertEqual(stats["total_present"], 37)
        self.assertEqual(stats["average"], 18.5)
        self.assertEqual((stats["highest"]["present"], stats["lowest"]["present"]), (25, 12))
        self.assertEqual(stats["latest"]["date"], self.d2)
        self.assertEqual(stats["period"], {"start": self.d1, "end": self.d2})

    def test_drafts_and_reopened_sheets_are_excluded(self):
        add_uploaded_sheet(self.alpha, self.d1, 10)
        add_draft_sheet(self.alpha, self.d2)
        reopened = add_uploaded_sheet(self.alpha, self.d3, 99)
        Attendance.objects.filter(pk=reopened.pk).update(status=Attendance.Status.DRAFT)   # what a reopen does
        stats = services.attendance_stats(self.admin)
        self.assertEqual(stats["values"], [10])

    def test_default_is_the_last_eight_gathering_dates(self):
        days = sundays(10)
        for number, day in enumerate(days, start=1):
            add_uploaded_sheet(self.alpha, day, number)
        stats = services.attendance_stats(self.admin)
        self.assertEqual(stats["count"], services.TREND_SHEETS)
        self.assertEqual(stats["values"], list(range(3, 11)))       # the two oldest are left out, oldest first

    def test_period_replaces_the_last_eight_rule(self):
        days = sundays(10)
        for number, day in enumerate(days, start=1):
            add_uploaded_sheet(self.alpha, day, number)
        stats = services.attendance_stats(self.admin, start=days[0], end=days[-1])
        self.assertEqual(stats["count"], 10)
        narrow = services.attendance_stats(self.admin, start=days[4], end=days[5])
        self.assertEqual(narrow["values"], [5, 6])

    def test_highest_and_lowest_ties_go_to_the_most_recent(self):
        add_uploaded_sheet(self.alpha, self.d1, 10)
        add_uploaded_sheet(self.alpha, self.d2, 10)
        stats = services.attendance_stats(self.admin)
        self.assertEqual(stats["highest"]["date"], self.d2)
        self.assertEqual(stats["lowest"]["date"], self.d2)

    def test_gender_by_age_network_comes_from_the_frozen_summaries(self):
        add_uploaded_sheet(self.alpha, self.d1, 6, first_timers=1, cells={
            ("members", "male", "adult"): 2, ("members", "female", "adult"): 1,
            ("guests", "female", "youth"): 2, ("first_timers", "male", "children"): 1,
        })
        add_uploaded_sheet(self.beta, self.d1, 3, cells={("members", "female", "adult"): 3})
        add_uploaded_sheet(self.alpha, self.d2, 2, cells={("members", "male", "youth"): 2})
        stats = services.attendance_stats(self.admin)
        columns = [c["code"] for c in stats["network"]["columns"]]
        rows = {r["gender"]: dict(zip(columns, r["values"])) for r in stats["network"]["rows"]}
        self.assertEqual(rows["male"], {"adult": 2, "young_adult": 0, "youth": 2, "children": 1})
        self.assertEqual(rows["female"], {"adult": 4, "young_adult": 0, "youth": 2, "children": 0})
        self.assertEqual(stats["split"], {"men": 5, "women": 6})
        self.assertEqual([(p["men"], p["women"]) for p in stats["points"]], [(3, 6), (2, 0)])
        # The newest date alone:
        latest = {r["gender"]: sum(r["values"]) for r in stats["network_latest"]["rows"]}
        self.assertEqual(latest, {"male": 2, "female": 0})

    def test_sheet_without_a_summary_still_counts_in_the_totals(self):
        add_uploaded_sheet(self.alpha, self.d1, 7, summary=False)
        stats = services.attendance_stats(self.admin)
        self.assertEqual(stats["values"], [7])
        self.assertEqual(stats["split"], {"men": 0, "women": 0})

    def test_coordinator_gets_only_their_extension(self):
        add_uploaded_sheet(self.alpha, self.d1, 10)
        add_uploaded_sheet(self.beta, self.d1, 15)
        self.assertEqual(services.attendance_stats(self.coordinator)["values"], [10])
        self.assertEqual(services.attendance_stats(self.coordinator, self.beta.pk)["values"], [10])

    def test_member_gets_nothing(self):
        self.assertIsNone(services.attendance_stats(make_person(Role.MEMBER, self.alpha)))


class PeopleStatsTests(TestCase):
    """people_stats: scoped counts and new people in a period."""

    def setUp(self):
        self.alpha, self.beta = make_extension("alpha"), make_extension("beta")
        self.super_admin = make_person(Role.SUPER_ADMIN)
        self.admin = make_person(Role.ADMIN)
        self.coordinator = make_person(Role.COORDINATOR, self.alpha)
        self.member_a = make_person(Role.MEMBER, self.alpha)
        make_person(Role.MEMBER, self.beta)
        make_person(Role.MEMBER, self.beta, status=Status.ARCHIVED, archived_at=timezone.now())
        make_person(Role.MEMBER, self.beta, status=Status.SUSPENDED)

    def counts(self, rows):
        return {row["label"]: row["count"] for row in rows}

    def test_admin_counts_and_hidden_super_admin(self):
        stats = services.people_stats(self.admin)
        self.assertEqual(self.counts(stats["roles"]), {"Admin": 1, "Coordinator": 1, "Member": 3})
        self.assertEqual(self.counts(stats["statuses"])["Archived"], 1)
        self.assertEqual(stats["total"], 5)
        self.assertEqual(services.people_stats(self.super_admin)["total"], 6)
        self.assertIn("Super admin", self.counts(services.people_stats(self.super_admin)["roles"]))

    def test_extension_filter_and_coordinator_scope(self):
        beta = services.people_stats(self.admin, self.beta.pk)
        self.assertEqual(self.counts(beta["roles"]), {"Member": 2})
        own = services.people_stats(self.coordinator, self.beta.pk)
        self.assertEqual(self.counts(own["roles"]), {"Coordinator": 1, "Member": 1})

    def test_new_in_period(self):
        self.assertIsNone(services.people_stats(self.admin)["new_in_period"])
        today = timezone.localdate()
        self.assertEqual(services.people_stats(self.admin, None, today, today)["new_in_period"], 5)
        tomorrow = today + timedelta(days=1)
        self.assertEqual(services.people_stats(self.admin, None, tomorrow, tomorrow + timedelta(days=3))["new_in_period"], 0)

    def test_member_gets_nothing(self):
        self.assertIsNone(services.people_stats(self.member_a))


class ExtensionStatsTests(TestCase):
    """extension_stats: admin and super-admin only, never narrowed by the filter."""

    def setUp(self):
        self.alpha, self.beta, self.gamma = make_extension("alpha"), make_extension("beta"), make_extension("gamma")
        self.admin = make_person(Role.ADMIN)
        self.coordinator = make_person(Role.COORDINATOR, self.alpha)
        make_person(Role.COORDINATOR, self.beta, status=Status.SUSPENDED)   # cannot sign in: does not count
        make_person(Role.MEMBER, self.alpha)
        make_person(Role.MEMBER, self.alpha)
        make_person(Role.MEMBER, self.gamma)
        self.today = timezone.localdate()

    def test_coordinator_and_member_get_nothing(self):
        self.assertIsNone(services.extension_stats(self.coordinator))
        self.assertIsNone(services.extension_stats(make_person(Role.MEMBER, self.alpha)))

    def test_coordinator_counts_ignore_suspended_coordinators(self):
        stats = services.extension_stats(self.admin)
        self.assertEqual((stats["total"], stats["with_coordinator"], stats["without_coordinator"]), (3, 1, 2))

    def test_extensions_with_no_recent_uploaded_sheet(self):
        add_uploaded_sheet(self.alpha, self.today - timedelta(days=2), 10)
        add_uploaded_sheet(self.beta, self.today - timedelta(days=30), 10)       # too old
        add_draft_sheet(self.gamma, self.today - timedelta(days=1))              # a draft does not count
        stats = services.extension_stats(self.admin)
        self.assertEqual([e["name"] for e in stats["silent"]], ["Beta", "Gamma"])
        self.assertEqual(stats["silent_days"], 7)

    def test_attendance_per_extension_is_sorted_and_limited(self):
        day = sundays(1)[0]
        add_uploaded_sheet(self.alpha, day, 10)
        add_uploaded_sheet(self.beta, day, 30)
        stats = services.extension_stats(self.admin)
        self.assertEqual(stats["attendance_per_extension"], [{"name": "Beta", "count": 30}, {"name": "Alpha", "count": 10}])
        for number in range(services.EXTENSION_BAR_LIMIT + 2):
            add_uploaded_sheet(make_extension(f"extra {number}"), day, 1)
        capped = services.extension_stats(self.admin)
        self.assertEqual(len(capped["attendance_per_extension"]), services.EXTENSION_BAR_LIMIT)
        self.assertEqual(len(capped["people_per_extension"]), services.EXTENSION_BAR_LIMIT)
        self.assertEqual(capped["people_more"], capped["total"] - services.EXTENSION_BAR_LIMIT)

    def test_people_per_extension_skips_archived_people(self):
        make_person(Role.MEMBER, self.gamma, status=Status.ARCHIVED, archived_at=timezone.now())
        counts = {row["name"]: row["count"] for row in services.extension_stats(self.admin)["people_per_extension"]}
        self.assertEqual(counts["Gamma"], 1)
        self.assertEqual(counts["Alpha"], 3)


class GivingStatsTests(TestCase):
    """giving_stats: finalized reports on uploaded sheets, frozen Decimal amounts."""

    def setUp(self):
        self.alpha, self.beta = make_extension("alpha"), make_extension("beta")
        self.admin = make_person(Role.ADMIN)
        self.coordinator = make_person(Role.COORDINATOR, self.alpha)
        self.d1, self.d2 = sundays(2)

    def test_totals_trend_and_decimals(self):
        add_finalized_report(add_uploaded_sheet(self.alpha, self.d1, 5), "150.25", "20.00")
        add_finalized_report(add_uploaded_sheet(self.beta, self.d1, 5), "100.00", "5.50")
        add_finalized_report(add_uploaded_sheet(self.alpha, self.d2, 5), "300.00", "0")
        stats = services.giving_stats(self.admin)
        self.assertEqual(stats["reports"], 3)
        self.assertEqual(stats["tithes"], Decimal("550.25"))
        self.assertEqual(stats["offering"], Decimal("25.50"))
        self.assertEqual(stats["total"], Decimal("575.75"))
        self.assertIsInstance(stats["total"], Decimal)
        self.assertEqual(stats["total_display"], "₱575.75")
        self.assertEqual(stats["totals"], [Decimal("275.75"), Decimal("300.00")])
        self.assertEqual([t["date"] for t in stats["trend"]], [self.d1, self.d2])

    def test_draft_reports_and_reopened_sheets_are_excluded(self):
        sheet = add_uploaded_sheet(self.alpha, self.d1, 5)
        add_finalized_report(sheet, "100.00", "0")
        from apps.tithes.models import TithesOffering

        draft_sheet = add_uploaded_sheet(self.alpha, self.d2, 5)
        TithesOffering.objects.create(attendance=draft_sheet, offering_amount=Decimal("50"))        # a draft report
        stats = services.giving_stats(self.admin)
        self.assertEqual((stats["reports"], stats["total"]), (1, Decimal("100.00")))
        Attendance.objects.filter(pk=sheet.pk).update(status=Attendance.Status.DRAFT)
        self.assertEqual(services.giving_stats(self.admin)["reports"], 0)

    def test_date_without_a_report_is_zero_not_missing(self):
        add_finalized_report(add_uploaded_sheet(self.alpha, self.d1, 5), "100.00", "0")
        add_uploaded_sheet(self.alpha, self.d2, 5)
        self.assertEqual(services.giving_stats(self.admin)["totals"], [Decimal("100.00"), Decimal("0")])

    def test_filter_and_coordinator_scope(self):
        add_finalized_report(add_uploaded_sheet(self.alpha, self.d1, 5), "100.00", "0")
        add_finalized_report(add_uploaded_sheet(self.beta, self.d1, 5), "200.00", "0")
        self.assertEqual(services.giving_stats(self.admin, self.beta.pk)["total"], Decimal("200.00"))
        self.assertEqual(services.giving_stats(self.coordinator, self.beta.pk)["total"], Decimal("100.00"))

    def test_empty_and_member(self):
        stats = services.giving_stats(self.admin)
        self.assertEqual((stats["reports"], stats["total"], stats["trend"]), (0, Decimal("0"), []))
        self.assertIsNone(services.giving_stats(make_person(Role.MEMBER, self.alpha)))


class PrayerSummaryTests(TestCase):
    """prayer_summary: counts and latest rows from inbox_for; never request text or names for anonymous."""

    def setUp(self):
        self.alpha, self.beta = make_extension("alpha"), make_extension("beta")   # alpha has a coordinator
        self.admin = make_person(Role.ADMIN)
        self.coordinator = make_person(Role.COORDINATOR, self.alpha)
        self.sender_a = make_person(Role.MEMBER, self.alpha)
        self.sender_b = make_person(Role.MEMBER, self.beta)

    def request(self, sender, extension, status="open", anonymous=False, body="private words"):
        return PrayerRequest.objects.create(
            user=sender, extension=extension, body=body, status=status, is_anonymous=anonymous,
            completed_at=timezone.now() if status == "completed" else None,
        )

    def test_member_gets_nothing(self):
        self.assertIsNone(services.prayer_summary(self.sender_a))

    def test_coordinator_sees_their_own_extension_only(self):
        mine = self.request(self.sender_a, self.alpha)
        self.request(self.sender_b, self.beta)
        summary = services.prayer_summary(self.coordinator)
        self.assertEqual([row["id"] for row in summary["latest"]], [mine.pk])
        self.assertEqual(summary["scope_note"], "")

    def test_admin_sees_only_extensions_without_a_coordinator(self):
        self.request(self.sender_a, self.alpha)                # alpha has a coordinator: not in the admin inbox
        waiting = self.request(self.sender_b, self.beta)
        summary = services.prayer_summary(self.admin)
        self.assertEqual([row["id"] for row in summary["latest"]], [waiting.pk])
        self.assertEqual(summary["open"], 1)

    def test_open_includes_replied_and_completed_and_archived_are_handled(self):
        self.request(self.sender_a, self.alpha, "open")
        self.request(self.sender_a, self.alpha, "replied")
        self.request(self.sender_a, self.alpha, "completed")
        archived = self.request(self.sender_a, self.alpha, "open")
        PrayerRequest.objects.filter(pk=archived.pk).update(status="archived", archived_at=timezone.now())
        summary = services.prayer_summary(self.coordinator)
        self.assertEqual((summary["open"], summary["completed"], len(summary["latest"])), (2, 1, 2))

    def test_latest_is_limited_to_five_newest_first(self):
        for _ in range(7):
            self.request(self.sender_a, self.alpha)
        ids = [row["id"] for row in services.prayer_summary(self.coordinator)["latest"]]
        self.assertEqual(len(ids), services.PRAYER_LATEST)
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_anonymous_stays_anonymous_and_text_never_appears(self):
        self.request(self.sender_b, self.beta, anonymous=True, body="secret body text")
        summary = services.prayer_summary(self.admin)
        row = summary["latest"][0]
        self.assertEqual(row["label"], "Anonymous")
        flat = repr(summary)
        self.assertNotIn("secret body text", flat)
        self.assertNotIn(self.sender_b.last_name, flat)
        self.assertNotIn("user_id", flat)

    def test_filtering_to_an_extension_with_a_coordinator_gives_a_note_and_no_rows(self):
        self.request(self.sender_a, self.alpha)
        summary = services.prayer_summary(self.admin, self.alpha.pk)
        self.assertEqual(summary["scope_note"], services.PRAYER_SCOPE_NOTE)
        self.assertEqual((summary["latest"], summary["open"], summary["completed"]), ([], 0, 0))

    def test_filtering_to_an_extension_without_a_coordinator_keeps_rows(self):
        waiting = self.request(self.sender_b, self.beta)
        summary = services.prayer_summary(self.admin, self.beta.pk)
        self.assertEqual(summary["scope_note"], "")
        self.assertEqual([row["id"] for row in summary["latest"]], [waiting.pk])

    def test_suspended_coordinator_does_not_hide_the_requests(self):
        self.coordinator.status = Status.SUSPENDED
        self.coordinator.save(update_fields=["status"])
        waiting = self.request(self.sender_a, self.alpha)
        summary = services.prayer_summary(self.admin, self.alpha.pk)
        self.assertEqual(summary["scope_note"], "")
        self.assertEqual([row["id"] for row in summary["latest"]], [waiting.pk])


class UpcomingEventsTests(TestCase):
    """upcoming_events: `visible_events` scoping, upcoming means Coalesce(end_at, start_at) >= now.

    VERIFY: `Event` field names (title, start_at, end_at, extension) were not in the files for this brick.
    """

    def setUp(self):
        self.alpha, self.beta = make_extension("alpha"), make_extension("beta")
        self.admin = make_person(Role.ADMIN)
        self.coordinator = make_person(Role.COORDINATOR, self.alpha)
        self.member_b = make_person(Role.MEMBER, self.beta)
        self.now = timezone.now()

    def event(self, title, start, end=None, extension=None):
        return Event.objects.create(title=title, start_at=start, end_at=end, extension=extension)

    def titles(self, person, limit=5):
        return [e["title"] for e in services.upcoming_events(person, limit)]

    def test_scope_global_for_everyone_and_extension_events_for_their_people(self):
        soon = self.now + timedelta(days=1)
        self.event("Global", soon)
        self.event("Alpha only", soon + timedelta(hours=1), extension=self.alpha)
        self.event("Beta only", soon + timedelta(hours=2), extension=self.beta)
        self.assertEqual(self.titles(self.admin), ["Global", "Alpha only", "Beta only"])
        self.assertEqual(self.titles(self.coordinator), ["Global", "Alpha only"])
        self.assertEqual(self.titles(self.member_b), ["Global", "Beta only"])

    def test_past_events_are_out_and_a_running_event_is_in(self):
        self.event("Past", self.now - timedelta(days=2), self.now - timedelta(days=1))
        self.event("Running", self.now - timedelta(hours=1), self.now + timedelta(hours=1))
        self.event("Past without end", self.now - timedelta(hours=1))
        self.assertEqual(self.titles(self.admin), ["Running"])

    def test_limit_and_order(self):
        for number in range(8):
            self.event(f"E{number}", self.now + timedelta(days=8 - number))
        self.assertEqual(self.titles(self.admin, 3), ["E7", "E6", "E5"])

    def test_rows_say_global_or_the_extension(self):
        self.event("Global", self.now + timedelta(days=1))
        self.event("Alpha only", self.now + timedelta(days=2), extension=self.alpha)
        rows = services.upcoming_events(self.admin)
        self.assertEqual([(r["is_global"], r["extension"]) for r in rows], [(True, ""), (False, "Alpha")])
