"""attendance.tests.test_summary: brick 5, the summary calculation.

Run: docker compose exec web python manage.py test apps.attendance.tests.test_summary
Rows are created directly (no services) so each test controls the exact numbers.
"""
from datetime import date

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Extension, PersonType, Role, Status, User

from .. import summary
from ..age import AgeNetwork
from ..models import Attendance, AttendanceFirstTimer, AttendanceMember

GENDERS = ("male", "female")
NETWORKS = ("adult", "young_adult", "youth", "children")


class SummaryTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ext = Extension.objects.create(name="santa rosa")
        cls.other_ext = Extension.objects.create(name="cabanatuan")

    def sheet(self, **kwargs):
        values = dict(extension=self.ext, attendance_date=date(2026, 10, 11), label="Sunday Service")
        values.update(kwargs)
        return Attendance.objects.create(**values)

    def member(self, sheet, gender="male", network="adult", **kwargs):
        return AttendanceMember.objects.create(
            attendance=sheet, member_name="someone", gender=gender, age_network=network, **kwargs,
        )

    def first_timer(self, sheet, gender="male", network="adult", name="juan"):
        return AttendanceFirstTimer.objects.create(
            attendance=sheet, first_name=name, last_name="reyes", gender=gender, age_network=network,
        )

    def fill_every_cell(self, sheet):
        """Put a different number in every cell: first timers 1..8, members 11..18."""
        expected = {"first_timers": {}, "guests": {}, "members": {}}
        first, second = 0, 10
        for gender in GENDERS:
            expected["first_timers"][gender] = {}
            expected["guests"][gender] = {network: 0 for network in NETWORKS}
            expected["members"][gender] = {}
            for network in NETWORKS:
                first += 1
                second += 1
                expected["first_timers"][gender][network] = first
                expected["members"][gender][network] = second
                for _ in range(first):
                    self.first_timer(sheet, gender, network, name=f"ft{first}x{_}")
                for _ in range(second):
                    self.member(sheet, gender, network)
        return expected


class BuildSummaryTests(SummaryTestCase):
    def test_every_cell_counts_and_the_totals_add_up(self):
        sheet = self.sheet()
        expected = self.fill_every_cell(sheet)
        result = summary.build_summary(sheet)
        self.assertEqual(result.summary, expected)
        self.assertEqual(result.first_timers_count, sum(range(1, 9)))      # 36
        self.assertEqual(result.members_present, sum(range(11, 19)))        # 116
        self.assertEqual(result.total_present, 36 + 116)
        self.assertEqual(result.incomplete, 0)
        cells = sum(n for part in result.summary.values() for g in part.values() for n in g.values())
        self.assertEqual(cells, result.total_present)

    def test_an_empty_sheet_is_all_zeros_with_every_key_present(self):
        result = summary.build_summary(self.sheet())
        self.assertEqual((result.members_present, result.first_timers_count, result.total_present), (0, 0, 0))
        for part in ("first_timers", "guests", "members"):
            for gender in GENDERS:
                self.assertEqual(result.summary[part][gender], {network: 0 for network in NETWORKS})

    def test_a_visitor_is_counted(self):
        sheet = self.sheet()
        self.member(
            sheet, "female", "youth", person_type=PersonType.GUEST,
            home_extension=self.other_ext, home_extension_name="Cabanatuan",
        )
        result = summary.build_summary(sheet)
        self.assertEqual(result.members_present, 1)
        self.assertEqual(result.summary["guests"]["female"]["youth"], 1)

    def test_only_the_stored_rows_count_never_the_current_birth_date(self):
        person = User.objects.create_user(
            first_name="old", last_name="timer", role=Role.MEMBER, extension=self.ext,
            status=Status.ACTIVE, must_change_password=False, gender="female", birth_date=date(1950, 1, 1),
        )
        sheet = self.sheet()
        self.member(sheet, "male", "children", user=person)  # the snapshot says male, children
        result = summary.build_summary(sheet)
        self.assertEqual(result.summary["members"]["male"]["children"], 1)
        self.assertEqual(result.summary["members"]["female"]["adult"], 0)

    def test_a_row_without_a_category_counts_in_the_total_but_in_no_cell(self):
        sheet = self.sheet()
        self.member(sheet, "male", "adult")
        self.member(sheet, "", "")
        self.member(sheet, "female", "")
        result = summary.build_summary(sheet)
        self.assertEqual((result.members_present, result.incomplete), (3, 2))
        self.assertEqual(result.summary["members"]["male"]["adult"], 1)
        self.assertEqual(sum(n for g in result.summary["members"].values() for n in g.values()), 1)

    def test_only_this_sheets_rows_count(self):
        sheet = self.sheet()
        other = self.sheet(label="Midweek")
        self.member(other)
        self.first_timer(other)
        self.assertEqual(summary.build_summary(sheet).total_present, 0)

    def test_it_uses_three_queries_however_many_rows(self):
        sheet = self.sheet()
        self.fill_every_cell(sheet)
        with self.assertNumQueries(3):
            summary.build_summary(sheet)

    def test_the_summary_is_plain_json(self):
        import json
        sheet = self.sheet()
        self.member(sheet)
        result = summary.build_summary(sheet)
        self.assertEqual(json.loads(json.dumps(result.summary)), result.summary)


class IncompleteRowsTests(SummaryTestCase):
    def test_lists_rows_missing_a_gender_or_an_age_network_by_name(self):
        sheet = self.sheet()
        self.member(sheet, "male", "adult")
        no_gender = AttendanceMember.objects.create(attendance=sheet, member_name="zed", gender="", age_network="youth")
        no_network = AttendanceMember.objects.create(attendance=sheet, member_name="amy", gender="female", age_network="")
        neither = AttendanceMember.objects.create(attendance=sheet, member_name="mia", gender="", age_network="")
        self.assertEqual(summary.incomplete_rows(sheet), [no_network, neither, no_gender])

    def test_complete_sheet_has_none_and_first_timers_are_never_listed(self):
        sheet = self.sheet()
        self.member(sheet)
        self.first_timer(sheet)
        self.assertEqual(summary.incomplete_rows(sheet), [])

    def test_count_matches_build_summary(self):
        sheet = self.sheet()
        self.member(sheet, "", "")
        self.member(sheet, "male", "")
        self.assertEqual(len(summary.incomplete_rows(sheet)), summary.build_summary(sheet).incomplete)


class SummaryForTests(SummaryTestCase):
    def test_a_draft_is_counted_live(self):
        sheet = self.sheet()
        self.member(sheet)
        self.assertEqual(summary.summary_for(sheet).total_present, 1)
        self.member(sheet, "female", "youth")
        self.assertEqual(summary.summary_for(sheet).total_present, 2)

    def test_the_preview_equals_what_gets_frozen(self):
        sheet = self.sheet()
        self.fill_every_cell(sheet)
        preview = summary.build_summary(sheet)
        # what the upload (brick 6) will write
        sheet.summary = preview.summary
        sheet.members_present = preview.members_present
        sheet.first_timers_count = preview.first_timers_count
        sheet.total_present = preview.total_present
        sheet.status = Attendance.Status.UPLOADED
        sheet.uploaded_at = timezone.now()
        sheet.save()
        sheet.refresh_from_db()
        self.assertEqual(summary.summary_for(sheet).summary, preview.summary)
        self.assertEqual(summary.summary_for(sheet).total_present, preview.total_present)

    def test_an_uploaded_sheet_reads_the_frozen_numbers_not_the_rows(self):
        sheet = self.sheet()
        self.member(sheet)
        frozen = summary.build_summary(sheet)
        Attendance.objects.filter(pk=sheet.pk).update(
            status=Attendance.Status.UPLOADED, summary=frozen.summary, members_present=1,
            first_timers_count=0, total_present=1,
        )
        self.member(sheet, "female", "youth")  # a row that appeared after the freeze
        sheet.refresh_from_db()
        with self.assertNumQueries(0):
            shown = summary.summary_for(sheet)
        self.assertEqual(shown.total_present, 1)

    def test_a_reopened_sheet_is_counted_live_again(self):
        sheet = self.sheet(status=Attendance.Status.DRAFT)  # reopen clears the frozen fields
        self.member(sheet)
        self.assertEqual(summary.summary_for(sheet).total_present, 1)


class SummaryTableTests(SummaryTestCase):
    def test_rows_columns_and_totals(self):
        sheet = self.sheet()
        expected = self.fill_every_cell(sheet)
        table = summary.summary_table(expected)
        self.assertEqual([c["code"] for c in table["columns"]], list(NETWORKS))
        self.assertEqual([c["label"] for c in table["columns"]], ["Adult", "Young adult", "Youth", "Children"])
        first_timers, guests, members = table["groups"]
        self.assertEqual((guests["title"], guests["total"]), ("Guests", 0))
        self.assertEqual((first_timers["title"], members["title"]), ("First timers", "Members"))
        self.assertEqual([r["label"] for r in first_timers["rows"]], ["Men", "Women"])
        self.assertEqual(first_timers["rows"][0]["cells"], [1, 2, 3, 4])
        self.assertEqual(first_timers["rows"][1]["cells"], [5, 6, 7, 8])
        self.assertEqual(first_timers["rows"][0]["total"], 10)
        self.assertEqual(first_timers["column_totals"], [6, 8, 10, 12])
        self.assertEqual(members["rows"][0]["cells"], [11, 12, 13, 14])
        self.assertEqual((first_timers["total"], members["total"], table["total"]), (36, 116, 152))

    def test_table_total_equals_total_present(self):
        sheet = self.sheet()
        self.fill_every_cell(sheet)
        result = summary.build_summary(sheet)
        self.assertEqual(summary.summary_table(result.summary)["total"], result.total_present)

    def test_missing_keys_count_as_zero(self):
        table = summary.summary_table({"members": {"male": {"adult": 2}}})
        self.assertEqual(table["total"], 2)
        self.assertEqual(table["groups"][0]["total"], 0)
        self.assertEqual(table["groups"][1]["total"], 0)
        self.assertEqual(table["groups"][2]["total"], 2)
        self.assertEqual(summary.summary_table(None)["total"], 0)

    def test_network_labels_cover_every_age_network(self):
        self.assertEqual(set(NETWORKS), set(AgeNetwork.values))
