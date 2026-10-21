"""Dashboard tithes scoping and trend: finalized reports on uploaded sheets, frozen values, per-extension."""
from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import Role
from apps.attendance import services as attendance_services
from apps.attendance.age import AgeNetwork
from apps.core.services import new_submission_token
from apps.dashboards import services as dash
from apps.tithes import services as tithes_services
from apps.tithes.tests.support import finalize, make_extension, make_report, make_sheet, make_user

BALANCED = {"500": 1, "100": 1}


class TithesScopeTests(TestCase):
    """`_scope_tithes` and `_tithe_totals` through `for_user`; the dashboard writes nothing."""

    def setUp(self):
        self.ext_a, self.ext_b = make_extension(), make_extension()
        self.coord_a = make_user(Role.COORDINATOR, self.ext_a)
        self.coord_b = make_user(Role.COORDINATOR, self.ext_b)
        self.admin = make_user(Role.ADMIN)
        self.giver_a = make_user(Role.MEMBER, self.ext_a)
        self.giver_b = make_user(Role.MEMBER, self.ext_b)

    def _uploaded_sheet(self, coordinator, extension, giver, day=None):
        sheet = make_sheet(extension, coordinator, day=day)
        attendance_services.mark_present(coordinator, sheet, giver, gender="male", age_network=AgeNetwork.ADULT.value)
        attendance_services.upload_sheet(coordinator, sheet.pk, token=new_submission_token())
        sheet.refresh_from_db()
        return sheet

    def _finalized(self, coordinator, extension, giver, day=None, offering="100", tithe="500", breakdown=BALANCED):
        sheet = self._uploaded_sheet(coordinator, extension, giver, day)
        report = make_report(coordinator, sheet, offering=offering, entries=[(giver, tithe)], breakdown=breakdown)
        return finalize(coordinator, report)

    def test_coordinator_sees_only_their_own_extension(self):
        mine = self._finalized(self.coord_a, self.ext_a, self.giver_a)
        self._finalized(self.coord_b, self.ext_b, self.giver_b)
        self.assertEqual([r.pk for r in dash._scope_tithes(self.coord_a)], [mine.pk])

    def test_admin_sees_every_extension(self):
        self._finalized(self.coord_a, self.ext_a, self.giver_a)
        self._finalized(self.coord_b, self.ext_b, self.giver_b)
        self.assertEqual(dash._scope_tithes(self.admin).count(), 2)

    def test_draft_report_is_not_counted(self):
        sheet = self._uploaded_sheet(self.coord_a, self.ext_a, self.giver_a)
        make_report(self.coord_a, sheet, entries=[(self.giver_a, "500")], breakdown=BALANCED)
        self.assertEqual(dash._scope_tithes(self.coord_a).count(), 0)

    def test_reopened_report_drops_out(self):
        report = self._finalized(self.coord_a, self.ext_a, self.giver_a)
        tithes_services.reopen_report(self.admin, report.pk, token=new_submission_token())
        self.assertEqual(dash._scope_tithes(self.admin).count(), 0)

    def test_trend_uses_frozen_values_and_adds_same_date_reports(self):
        first = self._finalized(self.coord_a, self.ext_a, self.giver_a)
        day = first.attendance.attendance_date
        self._finalized(self.coord_b, self.ext_b, self.giver_b, day=day)
        totals = dash._tithe_totals(self.admin, [day])
        self.assertEqual(totals[day]["tithes"], Decimal("1000.00"))
        self.assertEqual(totals[day]["offering"], Decimal("200.00"))
        # The coordinator only gets their own extension's share of the same date.
        self.assertEqual(dash._tithe_totals(self.coord_a, [day])[day]["tithes"], Decimal("500.00"))

    def test_no_dates_means_no_query_result(self):
        self.assertEqual(dash._tithe_totals(self.admin, []), {})
