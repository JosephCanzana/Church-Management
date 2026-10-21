"""Totals and the draft services: report, offering, breakdown, entries, finding givers, the open-request lock."""
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.accounts.models import PersonType, Status
from apps.attendance.models import AttendanceMember
from apps.audit.models import AuditLog
from apps.core.services import ServiceError
from apps.tithes import services
from apps.tithes.models import TitheEntry, TitheEntryGiver

from .scene import TithesTestCase, make_extension, make_sheet, make_user


class TotalsTests(TithesTestCase):
    def test_empty_report_is_zero_and_balanced(self):
        totals = services.build_totals(self.fresh())
        self.assertEqual((totals.tithers, totals.total, totals.difference), (0, Decimal("0"), Decimal("0")))
        self.assertTrue(totals.balanced)
        self.assertTrue(totals.is_empty)

    def test_total_is_offering_plus_tithes_and_an_entry_counts_once(self):
        services.set_offering(self.coordinator, self.report, "100.50")
        services.add_entry(self.coordinator, self.report, self.alice, "50", co_givers=[self.bob])
        services.add_entry(self.coordinator, self.report, self.carol, "25.25")
        totals = services.build_totals(self.fresh())
        self.assertEqual(totals.tithers, 2)          # one envelope = one, whatever the co-givers
        self.assertEqual(totals.tithes, Decimal("75.25"))
        self.assertEqual(totals.total, Decimal("175.75"))

    def test_breakdown_total_and_signed_difference(self):
        services.set_offering(self.coordinator, self.report, "1000")
        services.set_breakdown(self.coordinator, self.report, {"500": 1, "200": 2, "20": 3})   # 960
        totals = services.build_totals(self.fresh())
        self.assertEqual(totals.breakdown_total, Decimal("960.00"))
        self.assertEqual(totals.difference, Decimal("-40.00"))        # short is negative
        services.set_breakdown(self.coordinator, self.report, {"1000": 1, "5": 1})   # 1005
        self.assertEqual(services.build_totals(self.fresh()).difference, Decimal("5.00"))

    def test_frozen_totals_refuse_a_draft(self):
        with self.assertRaises(ServiceError):
            services.frozen_totals_for(self.fresh())


class CreateReportTests(TithesTestCase):
    def test_one_report_per_sheet(self):
        with self.assertRaises(ServiceError):
            services.create_report(self.coordinator, self.sheet)

    def test_archived_sheet_is_refused(self):
        archived = make_sheet(self.ext, status="archived")
        with self.assertRaises(ServiceError):
            services.create_report(self.coordinator, archived)

    def test_who_may_start_a_report(self):
        sheet = make_sheet(self.ext)
        with self.assertRaises(PermissionDenied):
            services.create_report(self.other_coordinator, sheet)      # another extension
        with self.assertRaises(PermissionDenied):
            services.create_report(self.plain_member, sheet)           # no seat
        report = services.create_report(self.special, sheet)           # seat in that extension
        self.assertEqual(report.created_by, self.special)

    def test_creating_is_not_audited(self):
        self.assertFalse(AuditLog.objects.filter(action__startswith="tithes.").exists())


class OfferingAndBreakdownTests(TithesTestCase):
    def test_offering_rules(self):
        for bad in ("-1", "abc", "1.234", "NaN", "99999999999"):
            with self.assertRaises(ServiceError, msg=bad):
                services.set_offering(self.coordinator, self.report, bad)
        self.assertEqual(services.set_offering(self.coordinator, self.report, "0").offering_amount, 0)
        self.assertEqual(services.set_offering(self.coordinator, self.report, 12.5).offering_amount, Decimal("12.50"))

    def test_breakdown_is_normalised_to_all_denominations(self):
        report = services.set_breakdown(self.coordinator, self.report, {"100": "2", 5: 3, "1": ""})
        self.assertEqual(report.denomination_breakdown["100"], 2)
        self.assertEqual(report.denomination_breakdown["5"], 3)
        self.assertEqual(report.denomination_breakdown["1"], 0)
        self.assertEqual(len(report.denomination_breakdown), 9)

    def test_breakdown_refuses_bad_input(self):
        for bad in ({"2": 1}, {"100": -1}, {"100": "1.5"}, {"100": 10_000_000}, {"100": True}, "x"):
            with self.assertRaises(ServiceError, msg=str(bad)):
                services.set_breakdown(self.coordinator, self.report, bad)

    def test_edits_are_not_audited(self):
        self.short_report()
        self.assertFalse(AuditLog.objects.filter(action__startswith="tithes.").exists())


class AddEntryTests(TithesTestCase):
    def test_entry_with_co_givers_snapshots_names(self):
        entry = services.add_entry(self.coordinator, self.report, self.alice, "100", co_givers=[self.bob])
        self.assertEqual(entry.giver_name, self.alice.full_name)
        self.assertEqual(entry.co_givers.get().name_snapshot, self.bob.full_name)
        snapshot = entry.co_givers.get().name_snapshot
        self.bob.first_name = "renamed"
        self.bob.save()
        self.assertEqual(entry.co_givers.get().name_snapshot, snapshot)   # history is not rewritten
        self.assertNotIn("renamed", snapshot.lower())

    def test_guest_cannot_give_but_a_no_account_member_can(self):
        guest = make_user(self.ext, person_type=PersonType.GUEST)
        record = make_user(self.ext, person_type=PersonType.MEMBER_RECORD)
        with self.assertRaises(ServiceError):
            services.add_entry(self.coordinator, self.report, guest, "10")
        with self.assertRaises(ServiceError):
            services.add_entry(self.coordinator, self.report, self.alice, "10", co_givers=[guest])
        services.add_entry(self.coordinator, self.report, record, "10")
        self.assertEqual(TitheEntry.objects.count(), 1)

    def test_suspended_person_cannot_give(self):
        suspended = make_user(self.ext, status=Status.SUSPENDED)
        with self.assertRaises(ServiceError):
            services.add_entry(self.coordinator, self.report, suspended, "10")

    def test_a_person_appears_once_per_report(self):
        services.add_entry(self.coordinator, self.report, self.alice, "10", co_givers=[self.bob])
        with self.assertRaises(ServiceError):       # as giver again
            services.add_entry(self.coordinator, self.report, self.alice, "10")
        with self.assertRaises(ServiceError):       # as giver after being a co-giver
            services.add_entry(self.coordinator, self.report, self.bob, "10")
        with self.assertRaises(ServiceError):       # as co-giver of another entry
            services.add_entry(self.coordinator, self.report, self.carol, "10", co_givers=[self.alice])
        with self.assertRaises(ServiceError):       # giver and co-giver of the same entry
            services.add_entry(self.coordinator, self.report, self.carol, "10", co_givers=[self.carol])
        self.assertEqual(TitheEntry.objects.count(), 1)
        self.assertEqual(TitheEntryGiver.objects.count(), 1)

    def test_a_giver_need_not_be_present_or_from_this_extension(self):
        visitor = make_user(make_extension())
        services.add_entry(self.coordinator, self.report, visitor, "10")
        self.assertEqual(TitheEntry.objects.get().user, visitor)

    def test_amount_must_be_positive(self):
        for bad in ("0", "-5", "x", "1.001"):
            with self.assertRaises(ServiceError, msg=bad):
                services.add_entry(self.coordinator, self.report, self.alice, bad)


class UpdateAndRemoveEntryTests(TithesTestCase):
    def setUp(self):
        super().setUp()
        self.entry = services.add_entry(self.coordinator, self.report, self.alice, "100", co_givers=[self.bob])

    def test_update_amount(self):
        services.update_entry(self.coordinator, self.report, self.entry.pk, amount="80")
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.amount, Decimal("80.00"))

    def test_update_needs_something_to_change(self):
        with self.assertRaises(ServiceError):
            services.update_entry(self.coordinator, self.report, self.entry.pk)

    def test_replacing_co_givers_keeps_the_ones_who_stay(self):
        kept = self.entry.co_givers.get()
        services.update_entry(self.coordinator, self.report, self.entry.pk, co_givers=[self.bob, self.carol])
        self.assertEqual(self.entry.co_givers.count(), 2)
        self.assertTrue(self.entry.co_givers.filter(pk=kept.pk).exists())
        services.update_entry(self.coordinator, self.report, self.entry.pk, co_givers=[])
        self.assertEqual(self.entry.co_givers.count(), 0)

    def test_changing_the_giver_follows_the_one_person_rule(self):
        other = services.add_entry(self.coordinator, self.report, self.carol, "10")
        with self.assertRaises(ServiceError):
            services.update_entry(self.coordinator, self.report, self.entry.pk, giver=self.carol)
        services.remove_entry(self.coordinator, self.report, other.pk)
        services.update_entry(self.coordinator, self.report, self.entry.pk, giver=self.carol)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.user, self.carol)

    def test_remove_entry_takes_its_co_givers_too(self):
        self.assertTrue(services.remove_entry(self.coordinator, self.report, self.entry.pk))
        self.assertFalse(services.remove_entry(self.coordinator, self.report, self.entry.pk))
        self.assertEqual(TitheEntryGiver.objects.count(), 0)


class WhoMayEditTests(TithesTestCase):
    def test_seat_holder_and_leaders_may_edit_a_draft(self):
        for actor in (self.special, self.coordinator, self.admin, self.super_admin):
            services.set_offering(actor, self.report, "5")

    def test_others_are_denied(self):
        for actor in (self.other_coordinator, self.plain_member):
            with self.assertRaises(PermissionDenied):
                services.set_offering(actor, self.report, "5")

    def test_a_finalized_report_is_locked_for_everyone(self):
        self.balanced_report()
        self.finalize()
        with self.assertRaises(ServiceError):
            services.set_offering(self.admin, self.report, "5")
        with self.assertRaises(ServiceError):
            services.add_entry(self.coordinator, self.report, self.bob, "5")

    def test_an_archived_sheet_is_read_only(self):
        self.sheet.status = "archived"
        self.sheet.archived_at = timezone.now()
        self.sheet.save()
        with self.assertRaises(ServiceError):
            services.set_offering(self.admin, self.report, "5")


class SearchGiversTests(TithesTestCase):
    def test_short_terms_return_nothing(self):
        self.assertEqual(services.search_givers(self.coordinator, self.report, "an"), [])
        self.assertEqual(services.search_givers(self.coordinator, self.report, "  "), [])

    def test_returns_only_safe_fields(self):
        rows = services.search_givers(self.coordinator, self.report, "santos")
        self.assertEqual({r["name"] for r in rows}, {self.alice.full_name, self.bob.full_name})
        for row in rows:
            self.assertEqual(set(row), {"id", "name", "home_extension", "age_network"})

    def test_every_word_must_match(self):
        rows = services.search_givers(self.coordinator, self.report, "anna santos")
        self.assertEqual([r["id"] for r in rows], [self.alice.pk])

    def test_finds_visitors_but_never_guests_or_inactive_people(self):
        visitor = make_user(self.other_ext, first_name="victor", last_name="alvarez")
        make_user(self.ext, first_name="gina", last_name="alvarez", person_type=PersonType.GUEST)
        make_user(self.ext, first_name="hugo", last_name="alvarez", status=Status.SUSPENDED)
        make_user(self.ext, first_name="ines", last_name="alvarez", person_type=PersonType.MEMBER_RECORD)
        names = {r["id"] for r in services.search_givers(self.coordinator, self.report, "alvarez")}
        self.assertIn(visitor.pk, names)
        self.assertEqual(len(names), 2)       # the visitor and the no-account member

    def test_at_most_twenty(self):
        for i in range(25):
            make_user(self.ext, first_name=f"zed{i}", last_name="crowd")
        self.assertEqual(len(services.search_givers(self.coordinator, self.report, "crowd")), 20)

    def test_needs_the_right_to_edit(self):
        with self.assertRaises(PermissionDenied):
            services.search_givers(self.other_coordinator, self.report, "santos")


class PresentCandidatesTests(TithesTestCase):
    def test_ticked_members_are_offered_and_guests_are_not(self):
        guest = make_user(self.ext, person_type=PersonType.GUEST)
        for person in (self.alice, self.carol, guest):
            AttendanceMember.objects.create(
                attendance=self.sheet, user=person, person_type=person.person_type, member_name=person.full_name,
            )
        ids = [r["id"] for r in services.present_candidates(self.fresh())]
        self.assertEqual(set(ids), {self.alice.pk, self.carol.pk})

    def test_people_already_on_the_report_are_not_offered_again(self):
        AttendanceMember.objects.create(
            attendance=self.sheet, user=self.alice, person_type="member_account", member_name="anna santos",
        )
        services.add_entry(self.coordinator, self.report, self.alice, "10")
        self.assertEqual(services.present_candidates(self.fresh()), [])


class RequestLockTests(TithesTestCase):
    """While a bypass request is open, only an admin or super-admin may edit; a difference change voids it."""

    def setUp(self):
        super().setUp()
        self.short_report()
        self.request_row = self.file_request()

    def test_coordinator_and_seat_holder_are_locked_out(self):
        for actor in (self.coordinator, self.special):
            with self.assertRaises(ServiceError):
                services.set_offering(actor, self.report, "5")

    def test_admin_edit_that_changes_the_difference_voids_the_request(self):
        services.set_offering(self.admin, self.report, "120")
        self.request_row.refresh_from_db()
        self.assertEqual(self.request_row.status, "voided")
        services.set_offering(self.coordinator, self.report, "130")    # unlocked again

    def test_admin_edit_that_keeps_the_difference_keeps_the_request(self):
        services.update_entry(self.admin, self.report, TitheEntry.objects.get().pk, co_givers=[self.bob])
        self.request_row.refresh_from_db()
        self.assertEqual(self.request_row.status, "pending")
