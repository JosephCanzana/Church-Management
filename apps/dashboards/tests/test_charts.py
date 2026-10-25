"""charts.py is pure geometry: shapes, empty input, a single value and all zeros (no database needed)."""
from decimal import Decimal

from django.test import SimpleTestCase

from apps.dashboards import charts


class LineChartTests(SimpleTestCase):
    """line_chart: points, polyline, area and ticks."""

    def test_empty_has_no_points_and_a_usable_axis(self):
        chart = charts.line_chart([], [])
        self.assertTrue(chart["empty"])
        self.assertEqual((chart["points"], chart["polyline"], chart["area"]), ([], "", ""))
        self.assertEqual([t["value"] for t in chart["y_ticks"]], [0, 1])

    def test_single_value_sits_in_the_middle_with_no_area(self):
        chart = charts.line_chart([5], ["Oct 4"])
        plot = chart["plot"]
        self.assertEqual(len(chart["points"]), 1)
        self.assertAlmostEqual(chart["points"][0]["x"], plot["left"] + plot["width"] / 2, places=1)
        self.assertEqual(chart["area"], "")

    def test_all_zeros_sit_on_the_baseline(self):
        chart = charts.line_chart([0, 0, 0], ["a", "b", "c"])
        self.assertTrue(all(p["y"] == chart["baseline_y"] for p in chart["points"]))

    def test_bigger_value_has_a_smaller_y_and_stays_inside_the_plot(self):
        chart = charts.line_chart([10, 40, 20], ["a", "b", "c"])
        low, high, mid = chart["points"]
        self.assertLess(high["y"], mid["y"])
        self.assertLess(mid["y"], low["y"])
        plot = chart["plot"]
        for point in chart["points"]:
            self.assertTrue(plot["left"] <= point["x"] <= plot["right"])
            self.assertTrue(plot["top"] <= point["y"] <= plot["bottom"])

    def test_first_and_last_points_touch_the_plot_edges(self):
        chart = charts.line_chart([1, 2, 3], ["a", "b", "c"])
        self.assertEqual(chart["points"][0]["x"], chart["plot"]["left"])
        self.assertEqual(chart["points"][-1]["x"], chart["plot"]["right"])

    def test_area_is_closed_and_polyline_matches_the_points(self):
        chart = charts.line_chart([1, 2], ["a", "b"])
        self.assertTrue(chart["area"].startswith("M") and chart["area"].endswith("Z"))
        self.assertEqual(len(chart["polyline"].split()), 2)

    def test_ticks_are_round_and_cover_the_top_value(self):
        chart = charts.line_chart([37], ["a"])
        self.assertEqual([t["label"] for t in chart["y_ticks"]], ["0", "10", "20", "30", "40"])
        self.assertGreaterEqual(chart["ceiling"], 37)

    def test_missing_labels_are_numbered_and_bad_values_become_zero(self):
        chart = charts.line_chart([None, -3, float("nan"), 4], ["only"])
        self.assertEqual([p["label"] for p in chart["points"]], ["only", "2", "3", "4"])
        self.assertEqual([p["y"] == chart["baseline_y"] for p in chart["points"]], [True, True, True, False])

    def test_decimals_are_accepted(self):
        chart = charts.line_chart([Decimal("1.5"), Decimal("3")], ["a", "b"])
        self.assertEqual(len(chart["points"]), 2)


class ColumnGroupsTests(SimpleTestCase):
    """column_groups: grouped bars with zero bars kept."""

    def test_empty_and_all_zero_are_empty(self):
        self.assertTrue(charts.column_groups([])["empty"])
        zeros = charts.column_groups([("Adult", [0, 0]), ("Youth", [0, 0])])
        self.assertTrue(zeros["empty"])
        self.assertEqual(len(zeros["groups"][0]["bars"]), 2)   # the bars still exist, with height 0
        self.assertTrue(all(b["height"] == 0 for g in zeros["groups"] for b in g["bars"]))

    def test_bars_are_proportional_and_inside_their_group(self):
        chart = charts.column_groups([("Adult", [10, 20]), ("Youth", [5, 0])])
        men, women = chart["groups"][0]["bars"]
        self.assertAlmostEqual(women["height"], men["height"] * 2, places=1)
        self.assertEqual((men["series"], women["series"]), (0, 1))
        self.assertLess(men["x"] + men["width"], women["x"] + 0.02)      # side by side, not overlapping
        self.assertEqual(chart["series_count"], 2)

    def test_dict_groups_and_short_groups_are_accepted(self):
        chart = charts.column_groups([{"label": "A", "values": [1, 2]}, ("B", [3])])
        self.assertEqual(len(chart["groups"][1]["bars"]), 2)
        self.assertEqual(chart["groups"][1]["bars"][1]["value"], 0)


class DonutTests(SimpleTestCase):
    """donut: arcs that add up to one full circle."""

    def test_arcs_fill_the_circle_and_start_where_the_last_ended(self):
        ring = charts.donut([("Men", 1), ("Women", 3)], radius=50, stroke=10)
        first, second = ring["arcs"]
        self.assertEqual((first["pct"], second["pct"]), (25.0, 75.0))
        lengths = [float(a["dasharray"].split()[0]) for a in ring["arcs"]]
        self.assertAlmostEqual(sum(lengths), ring["circumference"], delta=0.05)
        self.assertEqual(first["dashoffset"], 0)
        self.assertAlmostEqual(second["dashoffset"], -lengths[0], places=1)

    def test_zero_total_has_no_arcs(self):
        for segments in ([], [("a", 0), ("b", 0)]):
            ring = charts.donut(segments)
            self.assertTrue(ring["empty"])
            self.assertEqual(ring["arcs"], [])
            self.assertEqual(len(ring["items"]), len(segments))

    def test_single_segment_is_a_full_ring(self):
        ring = charts.donut([("Everyone", 7)])
        self.assertEqual(ring["arcs"][0]["pct"], 100.0)
        self.assertEqual(ring["arcs"][0]["dasharray"].split()[1], "0.0")

    def test_zero_segment_is_listed_but_not_drawn(self):
        ring = charts.donut([("a", 0), ("b", 5)])
        self.assertEqual([i["label"] for i in ring["items"]], ["a", "b"])
        self.assertEqual([a["label"] for a in ring["arcs"]], ["b"])

    def test_size_and_center_follow_radius_and_stroke(self):
        ring = charts.donut([("a", 1)], radius=40, stroke=10)
        self.assertEqual((ring["size"], ring["cx"], ring["cy"]), (90, 45.0, 45.0))


class HbarTests(SimpleTestCase):
    """hbar: widths scaled to the biggest value."""

    def test_widths_are_scaled_and_a_tiny_value_still_shows(self):
        chart = charts.hbar([("big", 1000), ("tiny", 1), ("none", 0)], max_width=200)
        widths = [row["width"] for row in chart["rows"]]
        self.assertEqual(widths[0], 200)
        self.assertEqual(widths[1], 200 * charts.MIN_BAR_PERCENT / 100)
        self.assertEqual(widths[2], 0)

    def test_empty_and_all_zero(self):
        self.assertTrue(charts.hbar([])["empty"])
        self.assertEqual(charts.hbar([])["height"], 0)
        zeros = charts.hbar([("a", 0), ("b", 0)])
        self.assertTrue(zeros["empty"])
        self.assertEqual([r["width"] for r in zeros["rows"]], [0, 0])

    def test_rows_stack_top_to_bottom(self):
        chart = charts.hbar([("a", 2), ("b", 1)], row_height=20, gap=5)
        self.assertEqual([r["y"] for r in chart["rows"]], [0, 25])
        self.assertEqual(chart["height"], 45)


class SparklineTests(SimpleTestCase):
    """sparkline: a tiny line that never divides by zero."""

    def test_empty(self):
        line = charts.sparkline([])
        self.assertTrue(line["empty"])
        self.assertIsNone(line["last"])

    def test_single_value_and_flat_series_are_drawn_in_the_middle(self):
        single = charts.sparkline([9], width=100, height=30)
        self.assertEqual(single["points"][0]["y"], 15.0)
        flat = charts.sparkline([0, 0, 0], width=100, height=30)
        self.assertEqual({p["y"] for p in flat["points"]}, {15.0})

    def test_rising_series_rises_and_ends_on_the_last_point(self):
        line = charts.sparkline([1, 5, 9])
        ys = [p["y"] for p in line["points"]]
        self.assertGreater(ys[0], ys[1])
        self.assertGreater(ys[1], ys[2])
        self.assertEqual(line["last"], line["points"][-1])

    def test_decimal_money_is_only_used_for_position(self):
        line = charts.sparkline([Decimal("100.50"), Decimal("250.25")])
        self.assertEqual(line["points"][1]["value"], Decimal("250.25"))   # the Decimal itself is returned


class YScaleTests(SimpleTestCase):
    """y_scale: round ticks for any top value."""

    def test_zero_and_small_tops(self):
        self.assertEqual(charts.y_scale(0), ([0.0, 1.0], 1.0))
        ticks, ceiling = charts.y_scale(3)
        self.assertEqual(ticks, [0.0, 1.0, 2.0, 3.0])
        self.assertEqual(ceiling, 3.0)

    def test_ceiling_always_covers_the_top(self):
        for top in (1, 7, 12, 99, 101, 5000, 123456):
            self.assertGreaterEqual(charts.y_scale(top)[1], top)
