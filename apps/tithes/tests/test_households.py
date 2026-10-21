"""Households: create, join, leave, the one-active-household rule, pre-filling and the entry snapshot."""
from datetime import date

from django.core.exceptions import PermissionDenied

from apps.accounts.models import PersonType, Status
from apps.audit.models import AuditLog
from apps.core.services import ServiceError
from apps.tithes import services
from apps.tithes.models import TitheHouseholdMember

from .scene import TithesTestCase, make_user


class HouseholdTests(TithesTestCase):
    def setUp(self):
        super().setUp()
        self.household = services.create_household(self.coordinator, self.ext, name="  The   Santos family ")
        self.anna = services.add_household_member(self.coordinator, self.household, self.alice)
        self.ben = services.add_household_member(self.coordinator, self.household, self.bob)

    def test_create_cleans_the_name_and_checks_who_may(self):
        self.assertEqual(self.household.name, "The Santos family")
        self.assertEqual(self.household.created_by, self.coordinator)
        for actor in (self.other_coordinator, self.special, self.plain_member):
            with self.assertRaises(PermissionDenied):
                services.create_household(actor, self.ext)
        services.create_household(self.admin, self.ext)

    def test_only_members_of_the_households_extension_may_join(self):
        guest = make_user(self.ext, person_type=PersonType.GUEST)
        away = make_user(self.other_ext)
        suspended = make_user(self.ext, status=Status.SUSPENDED)
        for person in (guest, away, suspended):
            with self.assertRaises(ServiceError):
                services.add_household_member(self.coordinator, self.household, person)
        record = make_user(self.ext, person_type=PersonType.MEMBER_RECORD)
        services.add_household_member(self.coordinator, self.household, record)

    def test_a_person_has_at_most_one_active_household(self):
        other = services.create_household(self.coordinator, self.ext)
        with self.assertRaises(ServiceError):
            services.add_household_member(self.coordinator, other, self.alice)
        with self.assertRaises(ServiceError):
            services.add_household_member(self.coordinator, self.household, self.alice)    # same one, twice
        services.end_household_member(self.coordinator, self.anna.pk)
        services.add_household_member(self.coordinator, other, self.alice)                 # free to move now
        self.assertEqual(services.household_for(self.alice), other)

    def test_other_extensions_leaders_cannot_manage_it(self):
        with self.assertRaises(PermissionDenied):
            services.add_household_member(self.other_coordinator, self.household, self.carol)
        with self.assertRaises(PermissionDenied):
            services.end_household_member(self.other_coordinator, self.anna.pk)

    def test_ending_a_membership(self):
        services.end_household_member(self.coordinator, self.anna.pk, ended_on=date(2099, 1, 1))
        self.anna.refresh_from_db()
        self.assertEqual(self.anna.ended_on, date(2099, 1, 1))
        with self.assertRaises(ServiceError):
            services.end_household_member(self.coordinator, self.anna.pk)               # already left
        with self.assertRaises(ServiceError):                                            # before it started
            services.end_household_member(self.coordinator, self.ben.pk, ended_on=date(2000, 1, 1))

    def test_household_for_ignores_ended_memberships(self):
        self.assertEqual(services.household_for(self.alice), self.household)
        self.assertIsNone(services.household_for(self.carol))
        services.end_household_member(self.coordinator, self.anna.pk)
        self.assertIsNone(services.household_for(self.alice))

    def test_prefill_lists_the_other_current_members_only(self):
        services.add_household_member(self.coordinator, self.household, self.carol)
        self.assertEqual({r["id"] for r in services.household_prefill(self.alice)}, {self.bob.pk, self.carol.pk})
        services.end_household_member(self.coordinator, self.ben.pk)
        self.assertEqual([r["id"] for r in services.household_prefill(self.alice)], [self.carol.pk])
        self.assertEqual(services.household_prefill(make_user(self.ext)), [])
        for row in services.household_prefill(self.alice):
            self.assertEqual(set(row), {"id", "name"})

    def test_an_entry_keeps_its_co_givers_when_the_household_changes(self):
        entry = services.add_entry(
            self.coordinator, self.report, self.alice, "100",
            co_givers=[row["id"] for row in services.household_prefill(self.alice)],
        )
        services.end_household_member(self.coordinator, self.ben.pk)
        TitheHouseholdMember.objects.filter(household=self.household).delete()
        self.assertEqual([g.user_id for g in entry.co_givers.all()], [self.bob.pk])        # history is a snapshot
        self.assertEqual(services.build_totals(self.fresh()).tithers, 1)                   # counted once

    def test_households_are_not_audited(self):
        self.assertFalse(AuditLog.objects.filter(action__startswith="tithes.").exists())
