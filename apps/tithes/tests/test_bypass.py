"""Bypass requests: request, withdraw, decide, the open-request lock, notices and privacy."""
from django.core.exceptions import PermissionDenied

from apps.accounts.models import Role, Status
from apps.audit.models import AuditLog
from apps.core.models import SubmissionToken
from apps.core.services import ServiceError
from apps.notifications.models import Notification
from apps.tithes import services
from apps.tithes.models import TitheBypassRequest

from .scene import TithesTestCase, make_user, token

MESSAGE = "SECRET-MESSAGE the cash box was short"
NOTE = "SECRET-NOTE recount on Monday"


class RequestBypassTests(TithesTestCase):
    def setUp(self):
        super().setUp()
        self.short_report()

    def _ask(self, actor=None, message=MESSAGE, tok=None):
        return services.request_bypass(
            actor or self.coordinator, self.report.pk, message=message, token=tok or token(),
        )

    def test_coordinator_files_a_request_tied_to_the_difference(self):
        result = self._ask()
        row = result.bypass_request
        self.assertFalse(result.duplicate)
        self.assertEqual((row.status, row.requested_by, row.message), ("pending", self.coordinator, MESSAGE))
        self.assertEqual(row.difference, services.build_totals(self.fresh()).difference)

    def test_only_the_coordinator_of_that_extension_may_ask(self):
        for actor in (self.admin, self.super_admin, self.special, self.other_coordinator, self.plain_member):
            with self.assertRaises(PermissionDenied, msg=str(actor.role)):
                self._ask(actor)

    def test_message_is_required_and_at_most_500_characters(self):
        for bad in ("", "   ", "x" * 501):
            with self.assertRaises(ServiceError):
                self._ask(message=bad)
        self._ask(message="x" * 500)

    def test_a_balanced_report_has_nothing_to_bypass(self):
        services.set_breakdown(self.coordinator, self.report, {"100": 1, "50": 1})
        with self.assertRaises(ServiceError):
            self._ask()

    def test_only_one_open_request_per_report(self):
        self._ask()
        with self.assertRaises(ServiceError):
            self._ask()

    def test_a_finalized_report_cannot_get_a_request(self):
        self.finalize(self.admin, bypass_reason="counted twice")
        with self.assertRaises(ServiceError):
            self._ask()

    def test_replayed_token_files_one_request(self):
        once = token()
        first = self._ask(tok=once)
        again = self._ask(tok=once)
        self.assertTrue(again.duplicate)
        self.assertEqual(again.bypass_request, first.bypass_request)
        self.assertEqual(TitheBypassRequest.objects.count(), 1)

    def test_a_refusal_gives_the_token_back(self):
        once = token()
        with self.assertRaises(ServiceError):
            self._ask(message="", tok=once)
        self.assertFalse(SubmissionToken.objects.filter(token=once).exists())

    def test_admins_and_super_admins_who_can_sign_in_are_told_generically(self):
        suspended = make_user(role=Role.ADMIN, status=Status.SUSPENDED)
        row = self._ask().bypass_request
        told = set(Notification.objects.filter(type=services.NOTIFY_REQUESTED).values_list("user_id", flat=True))
        self.assertEqual(told, {self.admin.pk, self.super_admin.pk})
        self.assertNotIn(suspended.pk, told)
        self.assertNotIn(self.coordinator.pk, told)             # never the actor
        notice = Notification.objects.filter(user=self.admin).get()
        self.assertEqual((notice.entity_type, notice.entity_id), ("tithes_offering", self.report.pk))
        self.assertEqual(notice.data, {"bypass_request_id": row.pk})
        self.assertEqual(notice.dedupe_key, f"{services.NOTIFY_REQUESTED}:{row.pk}")

    def test_audit_and_notices_carry_no_message_and_no_amount(self):
        self._ask()
        row = AuditLog.objects.get(action=services.REQUEST_ACTION)
        self.assertEqual(set(row.after), {"bypass_request_id", "status"})
        self.assert_no_leak(MESSAGE, "SECRET-MESSAGE", "50.00", "-50")


class WithdrawTests(TithesTestCase):
    def setUp(self):
        super().setUp()
        self.short_report()
        self.row = self.file_request()

    def test_coordinator_withdraws_and_the_draft_unlocks(self):
        services.withdraw_bypass(self.coordinator, self.row.pk)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, TitheBypassRequest.Status.WITHDRAWN)
        services.set_offering(self.coordinator, self.report, "5")
        self.assertTrue(AuditLog.objects.filter(action=services.WITHDRAW_ACTION).exists())

    def test_an_approved_request_can_be_withdrawn_too(self):
        services.decide_bypass(self.admin, self.row.pk, approve=True, note="", token=token())
        services.withdraw_bypass(self.coordinator, self.row.pk)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, TitheBypassRequest.Status.WITHDRAWN)

    def test_only_the_coordinator_may_withdraw(self):
        for actor in (self.admin, self.special, self.other_coordinator):
            with self.assertRaises(PermissionDenied):
                services.withdraw_bypass(actor, self.row.pk)

    def test_withdrawing_twice_is_a_friendly_refusal(self):
        services.withdraw_bypass(self.coordinator, self.row.pk)
        with self.assertRaises(ServiceError):
            services.withdraw_bypass(self.coordinator, self.row.pk)

    def test_a_new_request_is_allowed_after_a_withdrawal(self):
        services.withdraw_bypass(self.coordinator, self.row.pk)
        self.assertNotEqual(self.file_request("again").pk, self.row.pk)


class DecideTests(TithesTestCase):
    def setUp(self):
        super().setUp()
        self.short_report()
        self.row = self.file_request(MESSAGE)

    def _decide(self, actor=None, approve=True, note=NOTE, tok=None):
        return services.decide_bypass(
            actor or self.admin, self.row.pk, approve=approve, note=note, token=tok or token(),
        )

    def test_admin_approves(self):
        result = self._decide()
        row = result.bypass_request
        self.assertEqual((row.status, row.decided_by, row.decision_note), ("approved", self.admin, NOTE))
        self.assertIsNotNone(row.decided_at)
        self.assertTrue(AuditLog.objects.filter(action=services.APPROVE_ACTION).exists())

    def test_super_admin_rejects(self):
        row = self._decide(self.super_admin, approve=False).bypass_request
        self.assertEqual(row.status, "rejected")
        self.assertTrue(AuditLog.objects.filter(action=services.REJECT_ACTION).exists())

    def test_only_admins_decide(self):
        for actor in (self.coordinator, self.special, self.other_coordinator, self.plain_member):
            with self.assertRaises(PermissionDenied):
                self._decide(actor)

    def test_only_a_pending_request_can_be_decided(self):
        self._decide()
        with self.assertRaises(ServiceError):
            self._decide()

    def test_replayed_token_decides_once(self):
        once = token()
        self._decide(tok=once)
        again = self._decide(tok=once, approve=False)
        self.assertTrue(again.duplicate)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, "approved")

    def test_a_changed_difference_voids_instead_of_deciding(self):
        # Simulate a request that went stale without the edit hook (e.g. a direct data fix).
        TitheBypassRequest.objects.filter(pk=self.row.pk).update(difference=-99)
        result = self._decide()
        self.row.refresh_from_db()
        self.assertTrue(result.voided)
        self.assertEqual(self.row.status, "voided")
        self.assertFalse(AuditLog.objects.filter(action=services.APPROVE_ACTION).exists())

    def test_the_requester_is_told_generically_and_the_actor_never(self):
        self._decide(approve=False)
        notice = Notification.objects.get(type=services.NOTIFY_REJECTED)
        self.assertEqual(notice.user, self.coordinator)
        self.assertEqual(notice.data, {"bypass_request_id": self.row.pk})
        self.assertFalse(Notification.objects.filter(user=self.admin, type=services.NOTIFY_REJECTED).exists())

    def test_note_is_limited_to_500_characters(self):
        with self.assertRaises(ServiceError):
            self._decide(note="n" * 501)

    def test_audit_and_notices_carry_no_message_note_or_amount(self):
        self._decide()
        self.assert_no_leak(MESSAGE, NOTE, "SECRET-MESSAGE", "SECRET-NOTE", "50.00", "-50")
        self.row.refresh_from_db()
        self.assertEqual((self.row.message, self.row.decision_note), (MESSAGE, NOTE))    # they live on the row

    def test_a_decision_on_a_finalized_report_is_refused(self):
        self.finalize(self.admin, bypass_reason="counted twice")
        with self.assertRaises(ServiceError):
            self._decide()
