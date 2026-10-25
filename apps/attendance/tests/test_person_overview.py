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


def states(ov):
    return [c["state"] for m in reversed(ov["months"]) for c in m["cells"]]


class ClassifyServicesTests(SimpleTestCase):
    def test_no_services_gives_empty_overview(self):
        ov = classify_services([], set(), set())
        self.assertEqual((ov["total"], ov["attended"], ov["missed"], ov["longest_run"]), (0, 0, 0, 0))
        self.assertIsNone(ov["rate"])
        self.assertEqual((ov["months"], ov["missed_list"]), ([], []))

    def test_attended_and_missed_are_counted(self):
        ov = classify_services(sheets(4), {1, 2, 4}, set())
        self.assertEqual(states(ov), [PRESENT, PRESENT, MISSED, PRESENT])
        self.assertEqual((ov["home"], ov["missed"], ov["rate"]), (3, 1, 75))
        self.assertEqual([c["sheet_id"] for c in ov["missed_list"]], [3])

    def test_a_visit_elsewhere_on_the_same_date_is_not_a_miss(self):
        data = sheets(3)
        ov = classify_services(data, {1}, {data[1]["attendance_date"]})
        self.assertEqual(states(ov), [PRESENT, ELSEWHERE, MISSED])
        self.assertEqual((ov["attended"], ov["elsewhere"], ov["missed"]), (2, 1, 1))

    def test_longest_run_is_the_best_unbroken_stretch(self):
        ov = classify_services(sheets(7), {1, 2, 3, 5, 6, 7}, set())     # 3 then a miss then 3
        self.assertEqual(ov["longest_run"], 3)
        ov = classify_services(sheets(4), {1, 2, 3, 4}, set())
        self.assertEqual(ov["longest_run"], 4)
        ov = classify_services(sheets(2), set(), set())
        self.assertEqual(ov["longest_run"], 0)

    def test_a_visit_keeps_the_run_going(self):
        data = sheets(3)
        ov = classify_services(data, {1, 3}, {data[1]["attendance_date"]})
        self.assertEqual(ov["longest_run"], 3)

    def test_services_are_grouped_by_month_newest_month_first(self):
        # Aug 2, 9, 16, 23, 30, then Sep 6, 13
        ov = classify_services(sheets(7), {1, 2, 3, 6}, set())
        self.assertEqual([m["date"] for m in ov["months"]], [date(2026, 9, 1), date(2026, 8, 1)])
        sept, aug = ov["months"]
        self.assertEqual((aug["total"], aug["attended"]), (5, 3))
        self.assertEqual((sept["total"], sept["attended"]), (2, 1))
        self.assertEqual([c["sheet_id"] for c in aug["cells"]], [1, 2, 3, 4, 5])    # oldest first inside a month

    def test_a_month_without_services_is_left_out(self):
        data = [
            {"pk": 1, "attendance_date": date(2026, 6, 7), "label": "x"},
            {"pk": 2, "attendance_date": date(2026, 9, 6), "label": "x"},
        ]
        ov = classify_services(data, {1}, set())
        self.assertEqual([m["date"] for m in ov["months"]], [date(2026, 9, 1), date(2026, 6, 1)])

    def test_missed_list_is_newest_first(self):
        ov = classify_services(sheets(4), {2}, set())
        self.assertEqual([c["sheet_id"] for c in ov["missed_list"]], [4, 3, 1])
