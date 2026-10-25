"""Tests for the pure part of attendance.person_overview (no database needed)."""
from datetime import date, timedelta

from django.test import SimpleTestCase

from apps.attendance.person_overview import ELSEWHERE, MISSED, PRESENT, classify_services

START = date(2026, 8, 2)   # a Sunday


def sheets(n):
    return [
        {"pk": i + 1, "attendance_date": START + timedelta(days=7 * i), "label": "Sunday Service"}
        for i in range(n)
    ]


class ClassifyServicesTests(SimpleTestCase):
    def test_no_services_gives_empty_overview(self):
        ov = classify_services([], set(), set())
        self.assertEqual((ov["total"], ov["attended"], ov["missed"], ov["streak"]), (0, 0, 0, 0))
        self.assertIsNone(ov["rate"])
        self.assertEqual(ov["cells"], [])

    def test_attended_and_missed_are_counted(self):
        ov = classify_services(sheets(4), {1, 2, 4}, set())
        self.assertEqual([c["state"] for c in ov["cells"]], [PRESENT, PRESENT, MISSED, PRESENT])
        self.assertEqual((ov["home"], ov["missed"], ov["rate"]), (3, 1, 75))
        self.assertEqual([c["sheet_id"] for c in ov["missed_list"]], [3])

    def test_a_visit_elsewhere_on_the_same_date_is_not_a_miss(self):
        data = sheets(3)
        ov = classify_services(data, {1}, {data[1]["attendance_date"]})
        self.assertEqual([c["state"] for c in ov["cells"]], [PRESENT, ELSEWHERE, MISSED])
        self.assertEqual((ov["attended"], ov["elsewhere"], ov["missed"]), (2, 1, 1))

    def test_streak_counts_back_from_the_newest_service(self):
        ov = classify_services(sheets(5), {1, 2, 4, 5}, set())
        self.assertEqual(ov["streak"], 2)
        ov = classify_services(sheets(3), {1, 2}, set())      # newest missed
        self.assertEqual(ov["streak"], 0)

    def test_window_limits_cells_but_not_the_streak(self):
        ov = classify_services(sheets(20), set(range(1, 21)), set(), window=16)
        self.assertEqual(ov["total"], 16)
        self.assertEqual(ov["cells"][0]["sheet_id"], 5)       # oldest shown
        self.assertEqual(ov["streak"], 20)

    def test_missed_list_is_newest_first(self):
        ov = classify_services(sheets(4), {2}, set())
        self.assertEqual([c["sheet_id"] for c in ov["missed_list"]], [4, 3, 1])
