"""accounts.tests.test_guests: brick 2 (people screens, tags, the guests list, promote and grant account).

Run with:
    docker compose exec web python manage.py test apps.accounts.tests.test_guests -v 2
"""
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.accounts import permissions as perms
from apps.accounts.models import Extension, PersonType, Role, Status, User
from apps.accounts.services import users as svc
from apps.accounts.tests.test_superadmin_users import (
    PASSWORD, audit, fresh, make_person, make_super_admin,
)
from apps.core.services import ServiceError, new_submission_token


class BrickTwoBase(TestCase):
    def setUp(self):
        self.ext = Extension.objects.create(name="Cabanatuan")
        self.other_ext = Extension.objects.create(name="Gapan")
        self.super_admin = make_super_admin()
        self.admin = make_person(None, role=Role.ADMIN, first_name="Ada", last_name="Admin")
        self.coordinator = make_person(self.ext, role=Role.COORDINATOR, first_name="Cora", last_name="Coord")
        self.guest = make_person(self.ext, person_type=PersonType.GUEST, first_name="Gus", last_name="Guest")
        self.record = make_person(self.ext, person_type=PersonType.MEMBER_RECORD, first_name="Rita", last_name="Record")
        self.member = make_person(self.ext, first_name="Mia", last_name="Member")
        self.far_guest = make_person(
            self.other_ext, person_type=PersonType.GUEST, first_name="Far", last_name="Away",
        )


class TagTests(BrickTwoBase):
    def test_tags_for_each_kind_of_person(self):
        self.assertEqual(self.member.tag_codes, ["member_account"])
        self.assertEqual(self.guest.tag_codes, ["guest"])
        self.assertEqual(self.record.tag_codes, ["member_record"])

    def test_needs_password_change_comes_first_and_only_for_accounts(self):
        self.member.must_change_password = True
        self.assertEqual(self.member.tag_codes, ["needs_password_change", "member_account"])
        self.guest.must_change_password = True            # a guest has no password to change
        self.assertEqual(self.guest.tag_codes, ["guest"])

    def test_status_tags_come_last(self):
        self.guest.status = Status.SUSPENDED
        self.assertEqual(self.guest.tag_codes, ["guest", "suspended"])
        self.assertTrue(self.guest.is_blocked)
        self.record.status = Status.ARCHIVED
        self.assertEqual(self.record.tag_codes, ["member_record", "archived"])
        self.assertFalse(self.record.is_blocked)


class PermissionTests(BrickTwoBase):
    def test_coordinator_promotes_a_guest_in_their_own_extension(self):
        self.assertTrue(perms.can_act_on(self.coordinator, self.guest, perms.PROMOTE))
        self.assertFalse(perms.can_act_on(self.coordinator, self.far_guest, perms.PROMOTE))

    def test_coordinator_never_suspends(self):
        for person in (self.guest, self.record, self.member):
            self.assertFalse(perms.can_act_on(self.coordinator, person, perms.SUSPEND))
            self.assertFalse(perms.can_act_on(self.coordinator, person, perms.UNSUSPEND))
        with self.assertRaises(PermissionDenied):
            svc.suspend_user(self.coordinator, None, self.guest)

    def test_coordinator_archives_and_restores_in_their_extension_only(self):
        self.assertTrue(perms.can_act_on(self.coordinator, self.guest, perms.ARCHIVE))
        self.assertTrue(perms.can_act_on(self.coordinator, self.guest, perms.RESTORE))
        self.assertFalse(perms.can_act_on(self.coordinator, self.far_guest, perms.ARCHIVE))
        svc.archive_user(self.coordinator, None, self.guest)
        self.assertEqual(fresh(self.guest).status, Status.ARCHIVED)
        svc.restore_user(self.coordinator, None, self.guest)
        self.assertEqual(fresh(self.guest).status, Status.ACTIVE)

    def test_admin_and_super_admin_reach_every_extension(self):
        for actor in (self.admin, self.super_admin):
            self.assertTrue(perms.can_act_on(actor, self.far_guest, perms.PROMOTE))
            self.assertTrue(perms.can_act_on(actor, self.far_guest, perms.SUSPEND))

    def test_buttons_follow_the_type_and_status(self):
        offered = perms.available_actions(self.admin, self.guest)
        self.assertIn(perms.PROMOTE, offered)
        self.assertNotIn(perms.RESET_PASSWORD, offered)
        self.assertNotIn(perms.GRANT_ACCOUNT, offered)
        offered = perms.available_actions(self.admin, self.record)
        self.assertIn(perms.GRANT_ACCOUNT, offered)
        self.assertNotIn(perms.PROMOTE, offered)
        offered = perms.available_actions(self.admin, self.member)
        self.assertNotIn(perms.PROMOTE, offered)
        self.assertNotIn(perms.GRANT_ACCOUNT, offered)
        self.assertIn(perms.RESET_PASSWORD, offered)

    def test_a_suspended_guest_is_not_offered_promotion_but_can_be_unsuspended(self):
        self.guest.status = Status.SUSPENDED
        self.guest.save()
        offered = perms.available_actions(self.admin, self.guest)
        self.assertNotIn(perms.PROMOTE, offered)
        self.assertIn(perms.UNSUSPEND, offered)
        self.assertNotIn(perms.UNSUSPEND, perms.available_actions(self.coordinator, self.guest))


class PromoteTests(BrickTwoBase):
    def test_promote_changes_the_existing_row(self):
        pk, account_id = self.guest.pk, self.guest.account_id
        person, changed = svc.promote_guest(self.coordinator, None, self.guest)
        self.assertTrue(changed)
        person = fresh(person)
        self.assertEqual((person.pk, person.account_id), (pk, account_id))
        self.assertEqual(person.person_type, PersonType.MEMBER_RECORD)
        self.assertFalse(person.can_sign_in)
        self.assertEqual(User.objects.filter(last_name__iexact="guest").count(), 1)    # no second row
        row = audit("account.promote").get()
        self.assertEqual(row.entity_id, pk)

    def test_promote_twice_is_harmless(self):
        svc.promote_guest(self.admin, None, self.guest)
        _, changed = svc.promote_guest(self.admin, None, self.guest)
        self.assertFalse(changed)
        self.assertEqual(audit("account.promote").count(), 1)

    def test_promote_leaves_a_member_alone(self):
        _, changed = svc.promote_guest(self.admin, None, self.member)
        self.assertFalse(changed)
        self.assertEqual(fresh(self.member).person_type, PersonType.MEMBER_ACCOUNT)

    def test_suspended_and_archived_guests_are_refused(self):
        self.guest.status = Status.SUSPENDED
        self.guest.save()
        with self.assertRaises(ServiceError):
            svc.promote_guest(self.admin, None, self.guest)
        svc.unsuspend_user(self.admin, None, self.guest)
        svc.archive_user(self.admin, None, self.guest)
        with self.assertRaises(ServiceError):
            svc.promote_guest(self.admin, None, self.guest)

    def test_a_coordinator_cannot_promote_outside_their_extension(self):
        with self.assertRaises(PermissionDenied):
            svc.promote_guest(self.coordinator, None, self.far_guest)
        self.assertEqual(fresh(self.far_guest).person_type, PersonType.GUEST)


class GrantAccountTests(BrickTwoBase):
    def test_grant_account_signs_the_person_in_with_a_forced_password_change(self):
        result = svc.grant_account(
            self.admin, None, self.record, token=new_submission_token(), typed=PASSWORD,
        )
        person = fresh(result.user)
        self.assertEqual(person.person_type, PersonType.MEMBER_ACCOUNT)
        self.assertTrue(person.must_change_password)
        self.assertEqual(person.pk, self.record.pk)
        self.assertEqual(authenticate(identifier=person.account_id, password=PASSWORD), person)
        self.assertIn("needs_password_change", person.tag_codes)

    def test_the_audit_row_never_holds_the_password(self):
        svc.grant_account(self.admin, None, self.record, token=new_submission_token(), typed=PASSWORD)
        row = audit("account.grant_account").get()
        self.assertNotIn(PASSWORD, str(vars(row)))

    def test_a_replayed_token_changes_nothing(self):
        token = new_submission_token()
        svc.grant_account(self.admin, None, self.record, token=token, typed=PASSWORD)
        again = svc.grant_account(self.admin, None, self.record, token=token, typed=PASSWORD)
        self.assertTrue(again.duplicate)
        self.assertEqual(audit("account.grant_account").count(), 1)

    def test_refusals(self):
        with self.assertRaises(ServiceError):             # a guest is promoted first
            svc.grant_account(self.admin, None, self.guest, token=new_submission_token())
        with self.assertRaises(ServiceError):             # already has one
            svc.grant_account(self.admin, None, self.member, token=new_submission_token())
        self.record.status = Status.SUSPENDED
        self.record.save()
        with self.assertRaises(ServiceError):
            svc.grant_account(self.admin, None, self.record, token=new_submission_token())

    def test_a_coordinator_may_only_type_nothing(self):
        with self.assertRaises(PermissionDenied):          # typing a password is for admins
            svc.grant_account(self.coordinator, None, self.record, token=new_submission_token(), typed=PASSWORD)

    def test_a_failed_grant_rolls_its_token_back(self):
        token = new_submission_token()
        with self.assertRaises(ServiceError):
            svc.grant_account(self.admin, None, self.guest, token=token)
        svc.promote_guest(self.admin, None, self.guest)
        result = svc.grant_account(self.admin, None, self.guest, token=token, typed=PASSWORD)
        self.assertFalse(result.duplicate)


class GuestScreenTests(BrickTwoBase):
    def names(self, response):
        return {p.pk for p in response.context["page"].object_list}

    def test_each_role_sees_only_the_people_it_should(self):
        self.client.force_login(self.coordinator)
        response = self.client.get(reverse("coordinator_area:guest_list"))
        self.assertEqual(self.names(response), {self.guest.pk, self.record.pk})   # not the far guest, not a member

        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_area:guest_list"))
        self.assertEqual(self.names(response), {self.guest.pk, self.record.pk, self.far_guest.pk})

        self.client.force_login(self.super_admin)
        response = self.client.get(reverse("superadmin:guest_list"))
        self.assertEqual(self.names(response), {self.guest.pk, self.record.pk, self.far_guest.pk})

    def test_the_type_filter_and_search(self):
        self.client.force_login(self.admin)
        url = reverse("admin_area:guest_list")
        self.assertEqual(self.names(self.client.get(url, {"person_type": "guest"})), {self.guest.pk, self.far_guest.pk})
        self.assertEqual(self.names(self.client.get(url, {"person_type": "member_record"})), {self.record.pk})
        self.assertEqual(self.names(self.client.get(url, {"q": "rita"})), {self.record.pk})

    def test_archived_people_are_hidden_until_asked_for(self):
        svc.archive_user(self.admin, None, self.guest)
        self.client.force_login(self.admin)
        url = reverse("admin_area:guest_list")
        self.assertNotIn(self.guest.pk, self.names(self.client.get(url)))
        self.assertIn(self.guest.pk, self.names(self.client.get(url, {"status": "archived"})))

    def test_a_suspended_person_is_flagged_as_blocked(self):
        svc.suspend_user(self.admin, None, self.guest)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_area:guest_list"))
        # The badge, not the "Suspended" option of the status filter.
        self.assertContains(response, 'text-error">Suspended</span>')

    def test_a_coordinator_cannot_open_a_guest_from_another_extension(self):
        self.client.force_login(self.coordinator)
        response = self.client.get(reverse("coordinator_area:user_detail", args=[self.far_guest.pk]))
        self.assertEqual(response.status_code, 302)       # looks like "no longer exists"

    def test_anonymous_and_plain_members_are_kept_out(self):
        url = reverse("admin_area:guest_list")
        self.assertNotEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.member)
        self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_the_coordinator_promotes_through_the_page_and_cannot_suspend(self):
        self.client.force_login(self.coordinator)
        self.client.post(reverse("coordinator_area:user_promote", args=[self.guest.pk]))
        self.assertEqual(fresh(self.guest).person_type, PersonType.MEMBER_RECORD)
        with self.assertRaises(NoReverseMatch):
            # no such route for a coordinator
            reverse("coordinator_area:user_suspend", args=[self.guest.pk])

    def test_grant_account_page_shows_the_password_once(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("admin_area:user_grant_account", args=[self.record.pk]),
            {"submit_token": new_submission_token(), "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fresh(self.record).person_type, PersonType.MEMBER_ACCOUNT)
        self.assertContains(response, "secrets-data")


class PeopleScreenTypeFilterTests(BrickTwoBase):
    def names(self, response):
        return {p.pk for p in response.context["page"].object_list}

    def test_the_people_list_shows_members_by_default(self):
        self.client.force_login(self.admin)
        url = reverse("admin_area:user_list")
        shown = self.names(self.client.get(url))
        self.assertIn(self.member.pk, shown)
        self.assertNotIn(self.guest.pk, shown)
        self.assertNotIn(self.record.pk, shown)

    def test_the_type_filter_reaches_the_other_types(self):
        self.client.force_login(self.admin)
        url = reverse("admin_area:user_list")
        self.assertEqual(self.names(self.client.get(url, {"person_type": "guest"})), {self.guest.pk, self.far_guest.pk})
        self.assertIn(self.record.pk, self.names(self.client.get(url, {"person_type": "member_record"})))
        everyone = self.names(self.client.get(url, {"person_type": "all"}))
        self.assertTrue({self.member.pk, self.guest.pk, self.record.pk} <= everyone)

    def test_a_coordinator_type_filter_stays_inside_their_extension(self):
        self.client.force_login(self.coordinator)
        shown = self.names(self.client.get(reverse("coordinator_area:user_list"), {"person_type": "all"}))
        self.assertIn(self.guest.pk, shown)
        self.assertNotIn(self.far_guest.pk, shown)

    def test_the_detail_page_shows_the_type_tag(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_area:user_detail", args=[self.record.pk]))
        self.assertContains(response, "Member (no account)")
        self.assertContains(response, "Give an account")
        response = self.client.get(reverse("admin_area:user_detail", args=[self.guest.pk]))
        self.assertContains(response, "Promote to member")


class GuestBulkTests(BrickTwoBase):
    def post(self, actor, action, people, area="admin_area"):
        self.client.force_login(actor)
        return self.client.post(reverse(f"{area}:user_bulk"), {
            "action": action, "ids": [p.pk for p in people], "back": "guests",
            "submit_token": new_submission_token(),
        })

    def test_bulk_promote_only_changes_active_guests(self):
        response = self.post(self.admin, "promote", [self.guest, self.far_guest, self.record, self.member])
        self.assertRedirects(response, reverse("admin_area:guest_list"), fetch_redirect_response=False)
        self.assertEqual(fresh(self.guest).person_type, PersonType.MEMBER_RECORD)
        self.assertEqual(fresh(self.far_guest).person_type, PersonType.MEMBER_RECORD)
        self.assertEqual(fresh(self.member).person_type, PersonType.MEMBER_ACCOUNT)
        self.assertEqual(audit("account.promote").count(), 2)

    def test_a_coordinator_bulk_promotes_only_their_extension(self):
        self.post(self.coordinator, "promote", [self.guest, self.far_guest], area="coordinator_area")
        self.assertEqual(fresh(self.guest).person_type, PersonType.MEMBER_RECORD)
        self.assertEqual(fresh(self.far_guest).person_type, PersonType.GUEST)

    def test_a_coordinator_has_no_bulk_suspend(self):
        self.post(self.coordinator, "suspend", [self.guest], area="coordinator_area")
        self.assertEqual(fresh(self.guest).status, Status.ACTIVE)

    def test_bulk_suspend_and_archive_for_an_admin(self):
        self.post(self.admin, "suspend", [self.guest, self.record])
        self.assertEqual(fresh(self.guest).status, Status.SUSPENDED)
        self.post(self.admin, "archive", [self.guest, self.record])
        self.assertEqual(fresh(self.record).status, Status.ARCHIVED)

    def test_the_guests_page_offers_the_bar_by_role(self):
        self.client.force_login(self.coordinator)
        page = self.client.get(reverse("coordinator_area:guest_list"))
        labels = [b["label"] for b in page.context["bulk_buttons"]]
        self.assertIn("Promote", labels)
        self.assertNotIn("Suspend", labels)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("admin_area:guest_list"))
        self.assertIn("Suspend", [b["label"] for b in page.context["bulk_buttons"]])
        self.assertContains(page, 'id="guest-bulk"')
