"""Database rules of the tithes tables: constraints, unique rules, NULL while draft, on_delete."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import RestrictedError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Role
from apps.tithes.models import (
    TitheBypassRequest, TitheEntry, TitheEntryGiver, TitheHousehold, TitheHouseholdMember, TithesOffering,
)

from .helpers import finalized, make_extension, make_report, make_request, make_sheet, make_user

Status = TithesOffering.Status
RequestStatus = TitheBypassRequest.Status


class DbRefusesMixin:
    """`assertRefused()` runs a block that the database must refuse, without breaking the test's transaction."""

    @contextmanager
    def assertRefused(self):
        """The block must raise IntegrityError; it runs in its own savepoint so PostgreSQL stays usable."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                yield


class ReportDraftTests(DbRefusesMixin, TestCase):
    """A draft keeps every frozen value empty; a finalized report has them all."""

    @classmethod
    def setUpTestData(cls):
        cls.extension = make_extension()
        cls.admin = make_user(role=Role.ADMIN)

    def test_new_report_is_a_draft_with_nothing_frozen(self):
        report = make_report(make_sheet(self.extension))
        self.assertEqual(report.status, Status.DRAFT)
        self.assertEqual(report.offering_amount, 0)
        self.assertEqual(report.denomination_breakdown, {})
        for name in ("tithers_count", "tithes_total", "total_amount", "breakdown_total", "difference",
                     "finalized_by", "finalized_at", "bypassed_by"):
            with self.subTest(field=name):
                self.assertIsNone(getattr(report, name))
        self.assertEqual(report.bypass_reason, "")

    def test_draft_refuses_any_frozen_value(self):
        samples = {
            "tithers_count": 1, "tithes_total": Decimal("1.00"), "total_amount": Decimal("1.00"),
            "breakdown_total": Decimal("1.00"), "difference": Decimal("0.00"),
            "finalized_at": timezone.now(), "finalized_by": self.admin, "bypassed_by": self.admin,
            "bypass_reason": "because",
        }
        for name, value in samples.items():
            with self.subTest(field=name), self.assertRefused():
                make_report(make_sheet(self.extension), **{name: value})

    def test_finalized_needs_every_number(self):
        for name in ("tithers_count", "tithes_total", "total_amount", "breakdown_total", "difference",
                     "finalized_at"):
            with self.subTest(field=name), self.assertRefused():
                make_report(make_sheet(self.extension), **finalized(**{name: None}))

    def test_balanced_finalized_report_saves(self):
        report = make_report(make_sheet(self.extension), **finalized(finalized_by=self.admin))
        report.refresh_from_db()
        self.assertEqual(report.total_amount, Decimal("500.00"))
        self.assertEqual(report.difference, Decimal("0.00"))

    def test_unbalanced_finalized_report_keeps_a_signed_difference(self):
        short = make_report(make_sheet(self.extension), **finalized(
            breakdown_total=Decimal("450.00"), difference=Decimal("-50.00")))
        over = make_report(make_sheet(self.extension), **finalized(
            breakdown_total=Decimal("520.00"), difference=Decimal("20.00")))
        self.assertEqual(short.difference, Decimal("-50.00"))
        self.assertEqual(over.difference, Decimal("20.00"))

    def test_difference_must_be_breakdown_minus_total(self):
        with self.assertRefused():
            make_report(make_sheet(self.extension), **finalized(
                breakdown_total=Decimal("450.00"), difference=Decimal("50.00")))

    def test_total_must_be_offering_plus_tithes(self):
        with self.assertRefused():
            make_report(make_sheet(self.extension), **finalized(total_amount=Decimal("501.00"),
                                                                breakdown_total=Decimal("501.00")))

    def test_frozen_amounts_cannot_be_negative(self):
        with self.assertRefused():
            make_report(make_sheet(self.extension), **finalized(
                offering_amount=Decimal("600.00"), tithes_total=Decimal("-100.00")))

    def test_status_is_only_draft_or_finalized(self):
        for status in ("uploaded", "archived", "done"):
            with self.subTest(status=status), self.assertRefused():
                make_report(make_sheet(self.extension), status=status)

    def test_offering_cannot_be_negative(self):
        with self.assertRefused():
            make_report(make_sheet(self.extension), offering_amount=Decimal("-0.01"))

    def test_one_report_per_sheet(self):
        sheet = make_sheet(self.extension)
        make_report(sheet)
        with self.assertRefused():
            make_report(sheet)

    def test_two_sheets_two_reports(self):
        make_report(make_sheet(self.extension))
        make_report(make_sheet(self.extension))
        self.assertEqual(TithesOffering.objects.count(), 2)

    def test_direct_bypass_needs_a_reason(self):
        with self.assertRefused():
            make_report(make_sheet(self.extension), **finalized(
                breakdown_total=Decimal("450.00"), difference=Decimal("-50.00"), bypassed_by=self.admin))
        report = make_report(make_sheet(self.extension), **finalized(
            breakdown_total=Decimal("450.00"), difference=Decimal("-50.00"),
            bypassed_by=self.admin, bypass_reason="torn envelope"))
        self.assertEqual(report.bypassed_by, self.admin)

    def test_deleting_a_person_keeps_the_report_and_the_reason(self):
        admin = make_user(role=Role.ADMIN)
        report = make_report(make_sheet(self.extension), **finalized(
            breakdown_total=Decimal("450.00"), difference=Decimal("-50.00"), finalized_by=admin,
            bypassed_by=admin, bypass_reason="torn envelope"))
        admin.delete()
        report.refresh_from_db()
        self.assertIsNone(report.finalized_by)
        self.assertIsNone(report.bypassed_by)
        self.assertEqual(report.bypass_reason, "torn envelope")

    def test_money_is_decimal_12_2(self):
        for name in ("offering_amount", "tithes_total", "total_amount", "breakdown_total", "difference"):
            with self.subTest(field=name):
                field = TithesOffering._meta.get_field(name)
                self.assertEqual((field.max_digits, field.decimal_places), (12, 2))


class EntryTests(DbRefusesMixin, TestCase):
    """One envelope per person per report, a positive amount, names kept when a person is purged."""

    @classmethod
    def setUpTestData(cls):
        cls.extension = make_extension()
        cls.report = make_report(make_sheet(cls.extension))
        cls.person = make_user(extension=cls.extension)

    def entry(self, user, amount="100.00", report=None):
        """An entry on the test report (or `report`) for `user`."""
        return TitheEntry.objects.create(
            tithes_offering=report or self.report, user=user,
            giver_name=user.full_name if user else "gone", amount=Decimal(amount),
        )

    def test_amount_must_be_positive(self):
        for amount in ("0.00", "-5.00"):
            with self.subTest(amount=amount), self.assertRefused():
                self.entry(self.person, amount)

    def test_a_person_appears_once_per_report(self):
        self.entry(self.person)
        with self.assertRefused():
            self.entry(self.person)

    def test_same_person_may_give_on_two_reports(self):
        self.entry(self.person)
        self.entry(self.person, report=make_report(make_sheet(self.extension)))
        self.assertEqual(TitheEntry.objects.filter(user=self.person).count(), 2)

    def test_several_purged_givers_may_share_a_report(self):
        self.entry(None)
        self.entry(None)
        self.assertEqual(self.report.entries.count(), 2)

    def test_deleting_the_person_keeps_the_entry_and_name(self):
        person = make_user(extension=self.extension)
        entry = self.entry(person)
        name = entry.giver_name
        person.delete()
        entry.refresh_from_db()
        self.assertIsNone(entry.user)
        self.assertEqual(entry.giver_name, name)

    def test_entries_go_with_their_report(self):
        report = make_report(make_sheet(self.extension))
        self.entry(self.person, report=report)
        report.delete()
        self.assertFalse(TitheEntry.objects.filter(user=self.person).exists())

    def test_co_giver_once_per_entry(self):
        entry = self.entry(self.person)
        spouse = make_user(extension=self.extension)
        TitheEntryGiver.objects.create(entry=entry, user=spouse, name_snapshot="spouse")
        with self.assertRefused():
            TitheEntryGiver.objects.create(entry=entry, user=spouse, name_snapshot="spouse")

    def test_purged_co_givers_may_repeat_and_keep_their_names(self):
        entry = self.entry(self.person)
        spouse = make_user(extension=self.extension)
        child = make_user(extension=self.extension)
        TitheEntryGiver.objects.create(entry=entry, user=spouse, name_snapshot="spouse")
        TitheEntryGiver.objects.create(entry=entry, user=child, name_snapshot="child")
        spouse.delete()
        child.delete()
        self.assertEqual(entry.co_givers.filter(user__isnull=True).count(), 2)
        self.assertEqual(set(entry.co_givers.values_list("name_snapshot", flat=True)), {"spouse", "child"})

    def test_co_givers_go_with_their_entry(self):
        entry = self.entry(self.person)
        TitheEntryGiver.objects.create(entry=entry, user=None, name_snapshot="x")
        entry.delete()
        self.assertEqual(TitheEntryGiver.objects.count(), 0)


class HouseholdTests(DbRefusesMixin, TestCase):
    """A dated, standing link: one active household per person, history kept."""

    @classmethod
    def setUpTestData(cls):
        cls.extension = make_extension()
        cls.person = make_user(extension=cls.extension)

    def household(self):
        """A household in the test extension."""
        return TitheHousehold.objects.create(extension=self.extension, name="")

    def join(self, household, user=None, started=date(2026, 1, 1), ended=None):
        """A membership row."""
        return TitheHouseholdMember.objects.create(
            household=household, user=user or self.person, started_on=started, ended_on=ended)

    def test_one_active_household_per_person(self):
        self.join(self.household())
        with self.assertRefused():
            self.join(self.household())

    def test_person_may_move_after_ending_a_membership(self):
        self.join(self.household(), ended=date(2026, 3, 1))
        self.join(self.household(), started=date(2026, 3, 2))
        self.assertEqual(TitheHouseholdMember.objects.filter(user=self.person).count(), 2)

    def test_many_ended_memberships_are_fine(self):
        self.join(self.household(), started=date(2025, 1, 1), ended=date(2025, 6, 1))
        self.join(self.household(), started=date(2025, 7, 1), ended=date(2025, 12, 1))
        self.assertEqual(TitheHouseholdMember.objects.filter(user=self.person).count(), 2)

    def test_two_people_share_a_household(self):
        household = self.household()
        self.join(household)
        self.join(household, user=make_user(extension=self.extension))
        self.assertEqual(household.members.count(), 2)

    def test_membership_cannot_end_before_it_starts(self):
        with self.assertRefused():
            self.join(self.household(), started=date(2026, 5, 1), ended=date(2026, 4, 30))

    def test_same_day_end_is_allowed(self):
        self.join(self.household(), started=date(2026, 5, 1), ended=date(2026, 5, 1))

    def test_deleting_the_person_removes_their_membership(self):
        person = make_user(extension=self.extension)
        household = self.household()
        self.join(household, user=person)
        person.delete()
        self.assertEqual(household.members.count(), 0)

    def test_deleting_the_household_removes_its_memberships(self):
        household = self.household()
        self.join(household)
        household.delete()
        self.assertEqual(TitheHouseholdMember.objects.count(), 0)

    def test_an_extension_with_households_cannot_be_deleted(self):
        extension = make_extension()
        TitheHousehold.objects.create(extension=extension)
        with self.assertRaises(RestrictedError):
            extension.delete()

    def test_creator_is_set_null(self):
        creator = make_user(role=Role.ADMIN)
        household = TitheHousehold.objects.create(extension=self.extension, created_by=creator)
        creator.delete()
        household.refresh_from_db()
        self.assertIsNone(household.created_by)


class BypassRequestTests(DbRefusesMixin, TestCase):
    """At most one open request per report; a request needs a message and a real difference."""

    @classmethod
    def setUpTestData(cls):
        cls.extension = make_extension()
        cls.coordinator = make_user(extension=cls.extension, role=Role.COORDINATOR)

    def setUp(self):
        self.report = make_report(make_sheet(self.extension))

    def test_one_open_request_per_report(self):
        make_request(self.report)
        for status in (RequestStatus.PENDING, RequestStatus.APPROVED):
            with self.subTest(status=status), self.assertRefused():
                make_request(self.report, status=status)

    def test_an_approved_request_is_still_open(self):
        make_request(self.report, status=RequestStatus.APPROVED)
        with self.assertRefused():
            make_request(self.report)

    def test_closed_requests_do_not_block_a_new_one(self):
        for status in (RequestStatus.REJECTED, RequestStatus.WITHDRAWN, RequestStatus.VOIDED, RequestStatus.USED):
            make_request(self.report, status=status)
        make_request(self.report)
        self.assertEqual(self.report.bypass_requests.count(), 5)

    def test_other_reports_are_independent(self):
        make_request(self.report)
        make_request(make_report(make_sheet(self.extension)))

    def test_message_is_required(self):
        with self.assertRefused():
            make_request(self.report, message="")

    def test_difference_cannot_be_zero(self):
        with self.assertRefused():
            make_request(self.report, difference=Decimal("0.00"))

    def test_difference_is_signed(self):
        make_request(self.report, status=RequestStatus.REJECTED, difference=Decimal("-50.00"))
        make_request(self.report, difference=Decimal("20.00"))

    def test_status_must_be_known(self):
        with self.assertRefused():
            make_request(self.report, status="maybe")

    def test_decided_at_follows_the_status(self):
        with self.assertRefused():
            make_request(self.report, status=RequestStatus.APPROVED, decided_at=None)
        with self.assertRefused():
            make_request(self.report, status=RequestStatus.PENDING, decided_at=timezone.now())

    def test_deleting_the_coordinator_keeps_the_request(self):
        request = make_request(self.report, requested_by=self.coordinator)
        self.coordinator.delete()
        request.refresh_from_db()
        self.assertIsNone(request.requested_by)
        self.assertEqual(request.message, "Short by 50, the envelope was torn")

    def test_requests_go_with_their_report(self):
        make_request(self.report)
        self.report.delete()
        self.assertEqual(TitheBypassRequest.objects.count(), 0)
