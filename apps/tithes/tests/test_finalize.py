"""Finalize and reopen: balanced, short, over, approved, voided, direct bypass, replay, empty, archived sheet."""
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.core.models import SubmissionToken
from apps.core.services import ServiceError
from apps.notifications.models import NotificationRule
from apps.tithes import services
from apps.tithes.models import TitheBypassRequest, TithesOffering

from .scene import TithesTestCase, token


class FinalizeTests(TithesTestCase):
    def test_balanced_report_is_frozen(self):
        self.balanced_report()
        result = self.finalize()
        report = self.fresh()
        self.assertFalse(result.duplicate)
        self.assertEqual(report.status, TithesOffering.Status.FINALIZED)
        self.assertEqual(
            (report.tithers_count, report.tithes_total, report.total_amount, report.breakdown_total, report.difference),
            (1, Decimal("50"), Decimal("150"), Decimal("150"), Decimal("0")),
        )
        self.assertEqual(report.finalized_by, self.coordinator)
        self.assertIsNotNone(report.finalized_at)
        self.assertEqual((report.bypass_reason, report.bypassed_by), ("", None))
        self.assertEqual(services.frozen_totals_for(report).total, Decimal("150"))

    def test_admin_may_finalize_a_balanced_report_too(self):
        self.balanced_report()
        self.assertEqual(self.finalize(self.admin).report.finalized_by, self.admin)

    def test_short_report_is_refused_and_says_by_how_much(self):
        self.short_report()
        with self.assertRaises(ServiceError) as caught:
            self.finalize()
        self.assertIn("50.00", str(caught.exception))
        self.assertIn("short", str(caught.exception))
        self.assertEqual(self.fresh().status, TithesOffering.Status.DRAFT)

    def test_over_report_is_refused_and_says_by_how_much(self):
        self.balanced_report()
        services.set_breakdown(self.coordinator, self.report, {"100": 1, "50": 1, "20": 1})
        with self.assertRaises(ServiceError) as caught:
            self.finalize()
        self.assertIn("20.00", str(caught.exception))
        self.assertIn("over", str(caught.exception))

    def test_empty_report_is_refused(self):
        with self.assertRaises(ServiceError):
            self.finalize()
        with self.assertRaises(ServiceError):
            self.finalize(self.admin, bypass_reason="nothing to count")

    def test_special_member_and_other_extension_cannot_finalize(self):
        self.balanced_report()
        for actor in (self.special, self.other_coordinator, self.plain_member):
            with self.assertRaises(PermissionDenied):
                self.finalize(actor)

    def test_archived_sheet_is_refused(self):
        self.balanced_report()
        self.sheet.status = "archived"
        self.sheet.archived_at = timezone.now()
        self.sheet.save()
        with self.assertRaises(ServiceError):
            self.finalize()

    def test_finalizing_twice_with_new_tokens_is_refused(self):
        self.balanced_report()
        self.finalize()
        with self.assertRaises(ServiceError):
            self.finalize()

    def test_replayed_token_does_nothing_the_second_time(self):
        self.balanced_report()
        once = token()
        first = services.finalize_report(self.coordinator, self.report.pk, token=once)
        again = services.finalize_report(self.coordinator, self.report.pk, token=once)
        self.assertFalse(first.duplicate)
        self.assertTrue(again.duplicate)
        self.assertEqual(AuditLog.objects.filter(action=services.FINALIZE_ACTION).count(), 1)

    def test_a_refusal_gives_the_token_back(self):
        self.short_report()
        once = token()
        with self.assertRaises(ServiceError):
            services.finalize_report(self.coordinator, self.report.pk, token=once)
        self.assertFalse(SubmissionToken.objects.filter(token=once).exists())

    def test_a_bad_token_is_refused(self):
        self.balanced_report()
        with self.assertRaises(ServiceError):
            services.finalize_report(self.coordinator, self.report.pk, token="short")

    def test_audit_has_counts_and_a_balanced_flag_only(self):
        services.set_offering(self.coordinator, self.report, "777.77")
        services.add_entry(self.coordinator, self.report, self.alice, "123.45")
        services.set_breakdown(self.coordinator, self.report, {"500": 2})      # 1000, over
        self.finalize(self.admin, bypass_reason="SECRET-REASON counted by two people")
        row = AuditLog.objects.get(action=services.FINALIZE_ACTION)
        self.assertEqual(row.after, {"status": "finalized", "tithers_count": 1, "balanced": False})
        self.assertEqual(row.entity_id, self.report.pk)
        self.assert_no_leak("777.77", "123.45", "901.22", "SECRET-REASON")


class BypassFinalizeTests(TithesTestCase):
    def setUp(self):
        super().setUp()
        self.short_report()

    def _approved_request(self):
        row = self.file_request()
        services.decide_bypass(self.admin, row.pk, approve=True, note="ok", token=token())
        row.refresh_from_db()
        return row

    def test_approved_request_lets_the_coordinator_finalize_and_is_used_once(self):
        row = self._approved_request()
        result = self.finalize()
        row.refresh_from_db()
        self.assertEqual(result.report.status, TithesOffering.Status.FINALIZED)
        self.assertEqual(row.status, TitheBypassRequest.Status.USED)
        self.assertEqual(result.report.difference, Decimal("-50.00"))
        self.assertEqual(result.report.bypass_reason, "")        # a request keeps its text on the request row

    def test_an_approval_does_not_survive_a_reopen(self):
        self._approved_request()
        self.finalize()
        services.reopen_report(self.admin, self.report.pk, token=token())
        with self.assertRaises(ServiceError):                    # the old approval was used up
            self.finalize()

    def test_pending_request_does_not_unlock_finalize(self):
        self.file_request()
        with self.assertRaises(ServiceError) as caught:
            self.finalize()
        self.assertIn("waiting", str(caught.exception))

    def test_rejected_request_does_not_unlock_finalize(self):
        row = self.file_request()
        services.decide_bypass(self.admin, row.pk, approve=False, note="recount", token=token())
        with self.assertRaises(ServiceError):
            self.finalize()

    def test_request_voided_by_an_edit_no_longer_unlocks_finalize(self):
        row = self._approved_request()
        services.set_offering(self.admin, self.report, "120")     # difference is now -70
        row.refresh_from_db()
        self.assertEqual(row.status, TitheBypassRequest.Status.VOIDED)
        with self.assertRaises(ServiceError):
            self.finalize()

    def test_admin_can_bypass_directly_with_a_reason(self):
        result = self.finalize(self.admin, bypass_reason="  Counted by two people.  ")
        report = result.report
        self.assertEqual(report.status, TithesOffering.Status.FINALIZED)
        self.assertEqual((report.bypass_reason, report.bypassed_by), ("Counted by two people.", self.admin))

    def test_direct_bypass_needs_a_reason(self):
        for reason in ("", "   "):
            with self.assertRaises(ServiceError):
                self.finalize(self.admin, bypass_reason=reason)

    def test_a_coordinator_cannot_bypass_directly(self):
        with self.assertRaises(ServiceError):
            self.finalize(self.coordinator, bypass_reason="please")

    def test_a_reason_on_a_balanced_report_is_ignored(self):
        services.set_breakdown(self.coordinator, self.report, {"100": 1, "50": 1})
        report = self.finalize(self.admin, bypass_reason="not needed").report
        self.assertEqual((report.bypass_reason, report.bypassed_by), ("", None))


class ReopenTests(TithesTestCase):
    def setUp(self):
        super().setUp()
        self.balanced_report()
        self.finalize()

    def test_admin_reopens_and_the_frozen_fields_clear(self):
        result = services.reopen_report(self.super_admin, self.report.pk, token=token())
        report = self.fresh()
        self.assertFalse(result.duplicate)
        self.assertEqual(report.status, TithesOffering.Status.DRAFT)
        for name in ("tithers_count", "tithes_total", "total_amount", "breakdown_total", "difference",
                     "finalized_by", "finalized_at", "bypassed_by"):
            self.assertIsNone(getattr(report, name), name)
        self.assertEqual(report.entries.count(), 1)                # the work itself is kept
        services.set_offering(self.coordinator, self.report, "200")   # editable again

    def test_totals_are_recomputed_on_the_next_finalize(self):
        services.reopen_report(self.admin, self.report.pk, token=token())
        services.set_offering(self.admin, self.report, "150")
        services.set_breakdown(self.admin, self.report, {"100": 2})
        report = self.finalize(self.admin).report
        self.assertEqual(report.total_amount, Decimal("200"))

    def test_only_admins_may_reopen(self):
        for actor in (self.coordinator, self.special, self.other_coordinator):
            with self.assertRaises(PermissionDenied):
                services.reopen_report(actor, self.report.pk, token=token())

    def test_a_draft_cannot_be_reopened(self):
        services.reopen_report(self.admin, self.report.pk, token=token())
        with self.assertRaises(ServiceError):
            services.reopen_report(self.admin, self.report.pk, token=token())

    def test_replayed_token_does_nothing_the_second_time(self):
        once = token()
        services.reopen_report(self.admin, self.report.pk, token=once)
        again = services.reopen_report(self.admin, self.report.pk, token=once)
        self.assertTrue(again.duplicate)
        self.assertEqual(AuditLog.objects.filter(action=services.REOPEN_ACTION).count(), 1)

    def test_audit_has_the_old_tither_count_and_no_amounts(self):
        services.reopen_report(self.admin, self.report.pk, token=token())
        row = AuditLog.objects.get(action=services.REOPEN_ACTION)
        self.assertEqual(row.before, {"status": "finalized", "tithers_count": 1})
        self.assertEqual(row.after, {"status": "draft"})
        self.assert_no_leak("150.00", "100.00", "50.00")

    def test_reopen_action_has_an_alert_rule(self):
        """The reopen audit action must equal the action of the existing "reopen tithes" notification rule."""
        self.assertTrue(
            NotificationRule.objects.filter(audit_action=services.REOPEN_ACTION).exists(),
            f"No notification_rule for {services.REOPEN_ACTION!r}. Set REOPEN_ACTION in tithes/services.py "
            "to the action name core migration 0002 seeds for reopening tithes (add no new rule).",
        )
