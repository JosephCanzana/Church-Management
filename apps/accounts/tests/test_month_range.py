"""Tests for accounts.month_range: the month choice on the people detail page (no database)."""
from datetime import date

from django.test import SimpleTestCase

from apps.accounts.month_range import month_options, parse_month_range

TODAY = date(2026, 10, 25)


class ParseMonthRangeTests(SimpleTestCase):
    def test_nothing_given_is_the_last_twelve_months(self):
        r = parse_month_range(None, None, TODAY)
        self.assertEqual((r.start, r.end), (date(2025, 11, 1), date(2026, 10, 31)))
        self.assertEqual((r.from_value, r.to_value, r.months), ("2025-11", "2026-10", 12))
        self.assertTrue(r.is_default)

    def test_a_chosen_range(self):
        r = parse_month_range("2026-02", "2026-04", TODAY)
        self.assertEqual((r.start, r.end, r.months), (date(2026, 2, 1), date(2026, 4, 30), 3))
        self.assertFalse(r.is_default)

    def test_leap_february_ends_on_the_29th(self):
        self.assertEqual(parse_month_range("2024-02", "2024-02", date(2026, 10, 1)).end, date(2024, 2, 29))

    def test_from_after_to_is_swapped(self):
        r = parse_month_range("2026-06", "2026-03", TODAY)
        self.assertEqual((r.from_value, r.to_value), ("2026-03", "2026-06"))

    def test_the_future_is_clamped_to_this_month(self):
        r = parse_month_range("2026-08", "2027-05", TODAY)
        self.assertEqual((r.from_value, r.to_value), ("2026-08", "2026-10"))
        r = parse_month_range("2030-01", None, TODAY)
        self.assertEqual((r.from_value, r.to_value), ("2026-10", "2026-10"))

    def test_only_to_gives_the_twelve_months_before_it(self):
        r = parse_month_range(None, "2026-03", TODAY)
        self.assertEqual((r.from_value, r.to_value, r.months), ("2025-04", "2026-03", 12))

    def test_only_from_runs_to_this_month(self):
        r = parse_month_range("2026-07", None, TODAY)
        self.assertEqual((r.from_value, r.to_value), ("2026-07", "2026-10"))

    def test_garbage_is_ignored(self):
        for bad in ("", "abc", "2026", "2026-13", "2026-00", "1999-05", "2026-1-1", "<script>"):
            r = parse_month_range(bad, bad, TODAY)
            self.assertTrue(r.is_default, bad)

    def test_a_huge_span_is_cut_to_120_months(self):
        r = parse_month_range("2005-01", "2026-10", TODAY)
        self.assertEqual(r.months, 120)
        self.assertEqual(r.to_value, "2026-10")


class MonthOptionsTests(SimpleTestCase):
    def test_always_covers_the_default_range(self):
        options = month_options(date(2026, 9, 3), TODAY)
        self.assertEqual(options[0]["value"], "2026-10")
        self.assertEqual(options[-1]["value"], "2025-11")
        self.assertEqual(len(options), 12)

    def test_goes_back_to_the_first_month_seen(self):
        options = month_options(date(2023, 5, 20), TODAY)
        self.assertEqual(options[-1]["value"], "2023-05")

    def test_is_capped(self):
        self.assertEqual(len(month_options(date(2001, 1, 1), TODAY)), 120)

    def test_no_first_date_gives_twelve(self):
        self.assertEqual(len(month_options(None, TODAY)), 12)
