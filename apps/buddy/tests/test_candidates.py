"""Brick 6: the candidate list (default list, search, states, fields, paging)."""
from django.test import TestCase

from apps.accounts.models import PersonType, Role, Status, User
from apps.core.services import ServiceError

from apps.buddy import services
from apps.buddy.models import UserBuddy
from apps.buddy.permissions import can_use_buddy, signable_q

from .helpers import make_extension, make_user


def names(result):
    """The display names of one candidates() result, in order."""
    return [row["name"] for row in result["rows"]]


class DefaultListTests(TestCase):
    """What the list shows before anyone types a search."""

    def setUp(self):
        self.ext_a, self.ext_b = make_extension(), make_extension()
        self.me = make_user(first="Me", last="Self", extension=self.ext_a)
        self.same = make_user(first="Sam", last="Same", extension=self.ext_a)
        self.far = make_user(first="Fara", last="Faraway", extension=self.ext_b)

    def test_default_list_is_your_own_extension_without_you(self):
        result = services.candidates(self.me, "")
        self.assertEqual(names(result), [self.same.full_name])
        self.assertEqual(result["scope"], "extension")

    def test_guests_and_suspended_people_are_not_listed(self):
        make_user(first="Gus", extension=self.ext_a, person_type=PersonType.GUEST)
        make_user(first="Sue", extension=self.ext_a, status=Status.SUSPENDED)
        make_user(first="Ari", extension=self.ext_a, status=Status.ARCHIVED)
        self.assertEqual(names(services.candidates(self.me, "")), [self.same.full_name])

    def test_admins_and_super_admins_see_everyone(self):
        for role in (Role.ADMIN, Role.SUPER_ADMIN):
            leader = make_user(first="Boss", role=role)
            result = services.candidates(leader, "")
            self.assertEqual(result["scope"], "everyone")
            self.assertTrue({self.me.full_name, self.same.full_name, self.far.full_name} <= set(names(result)))

    def test_one_or_two_characters_keep_the_default_list_and_ask_for_more(self):
        result = services.candidates(self.me, "fa")
        self.assertTrue(result["too_short"])
        self.assertEqual(result["scope"], "extension")
        self.assertEqual(names(result), [self.same.full_name])

    def test_a_guest_or_suspended_actor_is_refused(self):
        for actor in (
            make_user(person_type=PersonType.GUEST),
            make_user(status=Status.SUSPENDED),
        ):
            with self.assertRaises(ServiceError):
                services.candidates(actor, "")


class SearchTests(TestCase):
    """Searching with 3 or more characters."""

    def setUp(self):
        self.me = make_user(first="Me", last="Self")
        self.far = make_user(first="Juan", middle="Dela", last="Cruz")
        self.other = make_user(first="Juana", last="Santos")

    def test_three_characters_search_every_extension(self):
        result = services.candidates(self.me, "jua")
        self.assertEqual(result["scope"], "search")
        self.assertEqual(set(names(result)), {self.far.full_name, self.other.full_name})

    def test_every_word_must_match_first_middle_or_last_name(self):
        self.assertEqual(names(services.candidates(self.me, "juan cruz")), [self.far.full_name])
        self.assertEqual(names(services.candidates(self.me, "dela cruz")), [self.far.full_name])
        # "juan" is inside "juana" and "santos" is her last name, so only she matches both words.
        self.assertEqual(names(services.candidates(self.me, "juan santos")), [self.other.full_name])
        self.assertEqual(names(services.candidates(self.me, "cruz santos")), [])

    def test_search_never_returns_the_actor_or_people_who_cannot_sign_in(self):
        make_user(first="Juno", status=Status.SUSPENDED)
        make_user(first="Junia", person_type=PersonType.MEMBER_RECORD)
        self.assertEqual(names(services.candidates(self.me, "jun")), [])
        self.assertEqual(names(services.candidates(self.me, "self")), [])

    def test_search_is_case_insensitive(self):
        self.assertEqual(names(services.candidates(self.me, "CRUZ")), [self.far.full_name])


class StateAndFieldTests(TestCase):
    """The four states, and the few fields that leave the service."""

    def setUp(self):
        self.ext = make_extension()
        self.me = make_user(first="Me", extension=self.ext)

    def state_of(self, person):
        rows = services.candidates(self.me, "")["rows"]
        return next(row["state"] for row in rows if row["id"] == person.pk)

    def test_states(self):
        free = make_user(first="Free", extension=self.ext)
        sent = make_user(first="Sent", extension=self.ext)
        received = make_user(first="Recv", extension=self.ext)
        busy = make_user(first="Busy", extension=self.ext)
        busy_partner = make_user(extension=self.ext)
        UserBuddy.objects.create(
            user_a_id=min(busy.pk, busy_partner.pk), user_b_id=max(busy.pk, busy_partner.pk),
        )
        services.send_request(self.me, sent.pk)
        services.send_request(received, self.me.pk)

        self.assertEqual(self.state_of(free), services.CandidateState.AVAILABLE)
        self.assertEqual(self.state_of(sent), services.CandidateState.PENDING_SENT)
        self.assertEqual(self.state_of(received), services.CandidateState.PENDING_RECEIVED)
        self.assertEqual(self.state_of(busy), services.CandidateState.HAS_BUDDY)

    def test_a_person_whose_partner_cannot_sign_in_counts_as_available(self):
        person = make_user(first="Lonely", extension=self.ext)
        gone = make_user(status=Status.SUSPENDED, extension=self.ext)
        UserBuddy.objects.create(user_a_id=min(person.pk, gone.pk), user_b_id=max(person.pk, gone.pk))
        self.assertEqual(self.state_of(person), services.CandidateState.AVAILABLE)

    def test_only_the_allowed_fields_are_returned(self):
        make_user(first="Field", extension=self.ext)
        result = services.candidates(self.me, "")
        self.assertTrue(result["rows"])
        for row in result["rows"]:
            self.assertEqual(set(row), {"id", "name", "extension", "state"})
        self.assertEqual(
            set(result), {"rows", "page", "has_next", "has_previous", "query", "scope", "too_short"},
        )

    def test_results_are_capped_at_twenty_a_page(self):
        for _ in range(25):
            make_user(extension=self.ext)
        first = services.candidates(self.me, "")
        self.assertEqual(len(first["rows"]), 20)
        self.assertTrue(first["has_next"])
        self.assertFalse(first["has_previous"])
        second = services.candidates(self.me, "", page=2)
        self.assertEqual(len(second["rows"]), 5)
        self.assertFalse(second["has_next"])
        self.assertTrue(second["has_previous"])
        self.assertFalse({r["id"] for r in first["rows"]} & {r["id"] for r in second["rows"]})

    def test_a_bad_page_number_falls_back_to_the_first_page(self):
        make_user(extension=self.ext)
        for bad in ("abc", "-4", "0", None, ""):
            self.assertEqual(services.candidates(self.me, "", page=bad)["page"], 1)


class SignableParityTests(TestCase):
    """`signable_q` (database) must agree with `User.can_sign_in` (Python) for every case."""

    def test_signable_q_matches_can_sign_in_and_can_use_buddy(self):
        created = []
        for person_type in PersonType.values:
            for status in Status.values:
                created.append(make_user(person_type=person_type, status=status))
        allowed_in_db = set(User.objects.filter(signable_q()).values_list("pk", flat=True))
        for user in created:
            self.assertEqual(user.pk in allowed_in_db, user.can_sign_in, (user.person_type, user.status))
            self.assertEqual(can_use_buddy(user), user.can_sign_in)
