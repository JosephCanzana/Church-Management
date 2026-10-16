from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Extension, Role, Status
from apps.core.services import archive
from apps.events import permissions as perm

from .helpers import archive_user, build_world, make_event, make_user


class VisibilityTests(TestCase):
    def setUp(self):
        self.w = build_world()
        self.global_event = make_event(None, title="All church")
        self.event_a = make_event(self.w.ext_a, title="Alpha only")

    def everyone(self):
        w = self.w
        return [w.super, w.admin, w.coord_a, w.coord_b, w.member_a, w.member_a2, w.member_b]

    def test_global_event_is_visible_to_every_role_and_extension(self):
        for person in self.everyone():
            self.assertTrue(perm.can_view(person, self.global_event), person.role)

    def test_extension_event_is_visible_to_its_people_and_admins_only(self):
        w = self.w
        for person in (w.super, w.admin, w.coord_a, w.member_a, w.member_a2):
            self.assertTrue(perm.can_view(person, self.event_a), person.role)
        for person in (w.coord_b, w.member_b):
            self.assertFalse(perm.can_view(person, self.event_a), person.role)

    def test_queryset_matches_can_view_for_everyone(self):
        for person in self.everyone():
            expected = {e.pk for e in (self.global_event, self.event_a) if perm.can_view(person, e)}
            self.assertEqual({e.pk for e in perm.visible_events(person)}, expected, person.role)

    def test_outsider_does_not_get_the_extension_event_from_the_queryset(self):
        self.assertFalse(perm.visible_events(self.w.member_b).filter(pk=self.event_a.pk).exists())

    def test_anonymous_suspended_and_archived_people_see_nothing(self):
        suspended = make_user(Role.MEMBER, self.w.ext_a, status=Status.SUSPENDED)
        archived = archive_user(make_user(Role.MEMBER, self.w.ext_a))
        for person in (AnonymousUser(), None, suspended, archived):
            self.assertFalse(perm.can_view(person, self.global_event))
            self.assertFalse(perm.visible_events(person).exists())

    def test_archived_event_is_hidden_from_everyone(self):
        archive(self.event_a)
        self.event_a.refresh_from_db()
        for person in self.everyone():
            self.assertFalse(perm.can_view(person, self.event_a))
            self.assertFalse(perm.visible_events(person).filter(pk=self.event_a.pk).exists())


class ManageTests(TestCase):
    def setUp(self):
        self.w = build_world()
        self.global_event = make_event(None)
        self.event_a = make_event(self.w.ext_a)

    def test_only_admins_create_global_events(self):
        w = self.w
        self.assertTrue(perm.can_create(w.super, None))
        self.assertTrue(perm.can_create(w.admin, None))
        for person in (w.coord_a, w.member_a, AnonymousUser()):
            self.assertFalse(perm.can_create(person, None), getattr(person, "role", "anon"))
        self.assertTrue(perm.can_create_global(w.admin))
        self.assertFalse(perm.can_create_global(w.coord_a))

    def test_admins_create_for_any_extension(self):
        for admin in (self.w.super, self.w.admin):
            self.assertTrue(perm.can_create(admin, self.w.ext_a.pk))
            self.assertTrue(perm.can_create(admin, self.w.ext_b.pk))

    def test_coordinator_creates_only_for_own_extension(self):
        self.assertTrue(perm.can_create(self.w.coord_a, self.w.ext_a.pk))
        self.assertFalse(perm.can_create(self.w.coord_a, self.w.ext_b.pk))

    def test_members_never_create(self):
        self.assertFalse(perm.can_create(self.w.member_a, self.w.ext_a.pk))

    def test_suspended_coordinator_cannot_create(self):
        coord = make_user(Role.COORDINATOR, self.w.ext_a, status=Status.SUSPENDED)
        self.assertFalse(perm.can_create(coord, self.w.ext_a.pk))

    def test_edit_and_cancel_on_a_global_event_are_admin_only(self):
        w = self.w
        for person in (w.super, w.admin):
            self.assertTrue(perm.can_edit(person, self.global_event))
            self.assertTrue(perm.can_cancel(person, self.global_event))
        for person in (w.coord_a, w.coord_b, w.member_a):
            self.assertFalse(perm.can_edit(person, self.global_event))
            self.assertFalse(perm.can_cancel(person, self.global_event))

    def test_edit_and_cancel_on_an_extension_event(self):
        w = self.w
        for person in (w.super, w.admin, w.coord_a):
            self.assertTrue(perm.can_edit(person, self.event_a), person.role)
            self.assertTrue(perm.can_cancel(person, self.event_a), person.role)
        for person in (w.coord_b, w.member_a, w.member_b):
            self.assertFalse(perm.can_edit(person, self.event_a), person.role)
            self.assertFalse(perm.can_cancel(person, self.event_a), person.role)

    def test_a_cancelled_event_cannot_be_edited_or_cancelled_again(self):
        self.event_a.cancelled_at = timezone.now()
        self.assertTrue(perm.can_manage(self.w.coord_a, self.event_a))  # role and scope only
        self.assertFalse(perm.can_edit(self.w.coord_a, self.event_a))
        self.assertFalse(perm.can_cancel(self.w.admin, self.event_a))

    def test_available_actions(self):
        w = self.w
        self.assertEqual(perm.available_actions(w.coord_a, self.event_a), {perm.VIEW, perm.EDIT, perm.CANCEL})
        self.assertEqual(perm.available_actions(w.member_a, self.event_a), {perm.VIEW})
        self.assertEqual(perm.available_actions(w.member_b, self.event_a), set())
        self.assertEqual(perm.available_actions(w.coord_a, self.global_event), {perm.VIEW})

    def test_page_level_checks(self):
        w = self.w
        self.assertTrue(perm.can_manage_events(w.admin))
        self.assertTrue(perm.can_manage_events(w.coord_a))
        self.assertFalse(perm.can_manage_events(w.member_a))
        self.assertFalse(perm.can_manage_events(AnonymousUser()))

    def test_creatable_extensions_match_can_create(self):
        w = self.w
        archived = Extension.objects.create(name="Old")
        archive(archived)
        for person in (w.super, w.admin, w.coord_a, w.member_a):
            offered = set(perm.creatable_extensions(person).values_list("pk", flat=True))
            allowed = {e.pk for e in Extension.objects.filter(archived_at__isnull=True)
                       if perm.can_create(person, e.pk)}
            self.assertEqual(offered, allowed, person.role)
            self.assertNotIn(archived.pk, offered)

    def test_require_raises_permission_denied(self):
        with self.assertRaises(PermissionDenied):
            perm.require(perm.can_manage, self.w.member_a, self.event_a)
        with self.assertRaises(PermissionDenied):
            perm.require(perm.can_create, self.w.coord_a, None)
        perm.require(perm.can_create, self.w.coord_a, self.w.ext_a.pk)  # does not raise
