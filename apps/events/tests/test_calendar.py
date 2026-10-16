"""Tests for events.calendar: the month grid. No database needed (events are not saved)."""
import datetime as dt
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from apps.events.calendar import MAX_CHIPS, build_month, parse_month, shift_month
from apps.events.models import Event

MANILA = ZoneInfo("Asia/Manila")
TODAY = dt.date(2026, 11, 5)


def ev(title, start, end=None, extension_id=None):
    """An unsaved event (times are Manila wall-clock tuples)."""
    return Event(
        title=title, extension_id=extension_id,
        start_at=dt.datetime(*start, tzinfo=MANILA),
        end_at=dt.datetime(*end, tzinfo=MANILA) if end else None,
    )


def cell(weeks, day):
    return next(d for week in weeks for d in week if d.date == day)


class ParseMonthTests(SimpleTestCase):
    def test_valid_value(self):
        self.assertEqual(parse_month("2026-12", TODAY), (2026, 12))

    def test_bad_values_mean_today(self):
        for bad in (None, "", "garbage", "2026-13", "2026-00", "1800-01", "2026-1-1", "9999-01"):
            self.assertEqual(parse_month(bad, TODAY), (2026, 11), bad)

    def test_shift_month_crosses_years(self):
        self.assertEqual(shift_month(2026, 12, 1), (2027, 1))
        self.assertEqual(shift_month(2026, 1, -1), (2025, 12))


class BuildMonthTests(SimpleTestCase):
    def test_grid_is_whole_weeks_starting_sunday(self):
        weeks = build_month(2026, 11, [], TODAY)      # 1 Nov 2026 is a Sunday
        self.assertEqual(weeks[0][0].date, dt.date(2026, 11, 1))
        self.assertTrue(all(len(week) == 7 for week in weeks))
        self.assertTrue(all(week[0].date.weekday() == 6 for week in weeks))   # Sunday

    def test_spill_over_days_are_not_in_month(self):
        weeks = build_month(2026, 10, [], TODAY)
        self.assertFalse(weeks[0][0].in_month)
        self.assertTrue(cell(weeks, dt.date(2026, 10, 15)).in_month)

    def test_today_is_marked(self):
        weeks = build_month(2026, 11, [], TODAY)
        self.assertTrue(cell(weeks, TODAY).is_today)
        self.assertFalse(cell(weeks, dt.date(2026, 11, 6)).is_today)

    def test_event_sits_on_its_manila_day_not_the_utc_day(self):
        # 01:00 on 10 Nov in Manila is 17:00 on 9 Nov in UTC.
        weeks = build_month(2026, 11, [ev("Early", (2026, 11, 10, 1, 0))], TODAY)
        self.assertEqual(len(cell(weeks, dt.date(2026, 11, 10)).events), 1)
        self.assertEqual(len(cell(weeks, dt.date(2026, 11, 9)).events), 0)

    def test_multi_day_event_shows_on_each_day(self):
        event = ev("Camp", (2026, 11, 10, 18, 0), (2026, 11, 12, 10, 0))
        weeks = build_month(2026, 11, [event], TODAY)
        for day in (10, 11, 12):
            self.assertEqual(len(cell(weeks, dt.date(2026, 11, day)).events), 1, day)
        self.assertEqual(len(cell(weeks, dt.date(2026, 11, 13)).events), 0)

    def test_event_ending_at_midnight_does_not_spill_over(self):
        event = ev("Vigil", (2026, 11, 10, 20, 0), (2026, 11, 11, 0, 0))
        weeks = build_month(2026, 11, [event], TODAY)
        self.assertEqual(len(cell(weeks, dt.date(2026, 11, 10)).events), 1)
        self.assertEqual(len(cell(weeks, dt.date(2026, 11, 11)).events), 0)

    def test_event_outside_the_grid_is_ignored(self):
        weeks = build_month(2026, 11, [ev("Far", (2027, 3, 1, 9, 0))], TODAY)
        self.assertTrue(all(not d.events for week in weeks for d in week))

    def test_busy_day_shows_chips_then_more(self):
        events = [ev(f"E{i}", (2026, 11, 8, 8 + i, 0)) for i in range(MAX_CHIPS + 2)]
        day = cell(build_month(2026, 11, events, TODAY), dt.date(2026, 11, 8))
        self.assertEqual(len(day.shown), MAX_CHIPS)
        self.assertEqual(day.more, 2)

    def test_events_in_a_day_are_sorted_by_start_time(self):
        late, early = ev("Late", (2026, 11, 8, 19, 0)), ev("Early", (2026, 11, 8, 6, 0))
        day = cell(build_month(2026, 11, [late, early], TODAY), dt.date(2026, 11, 8))
        self.assertEqual([e.title for e in day.events], ["Early", "Late"])
