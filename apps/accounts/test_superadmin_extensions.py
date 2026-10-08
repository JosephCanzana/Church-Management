"""accounts.test_superadmin_extensions: extension management (super-admin).

Run with:
    docker compose exec web python manage.py test accounts.test_superadmin_extensions -v 2

The test database is built from the real migrations, so the purge trigger and
the seeded retention policy (extension: 365 days) are present.
"""
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import ArchiveReason, Extension, Role, Status, User
from apps.audit.models import AuditLog
from apps.core.services import ServiceError

PASSWORD = "correct-horse-battery"


def make_super_admin():
    """A working super-admin (no extension, as the database rules allow)."""
    return User.objects.create_superuser(
        password=PASSWORD, first_name="Sam", last_name="Super",
    )


def make_person(extension, role=Role.MEMBER, **overrides):
    """An active person in `extension`. Created one by one so account_id is generated."""
    fields = dict(
        first_name="Juan", last_name="Dela Cruz", role=role, extension=extension,
        status=Status.ACTIVE, must_change_password=False,
    )
    fields.update(overrides)
    return User.objects.create_user(password=PASSWORD, **fields)


def archive_person(person):
    """Archive a person the way the model requires (status and archived_at together)."""
    person.status = Status.ARCHIVED
    person.archived_at = timezone.now()
    person.archive_reason = ArchiveReason.MANUAL
    person.save(update_fields=["status", "archived_at", "archive_reason"])


def audit(action):
    """Audit rows for one action name."""
    return AuditLog.objects.filter(action=action)


class ExtensionServiceTests(TestCase):
    """Business rules in accounts.services."""

    def setUp(self):
        self.admin = make_super_admin()
        self.ext = Extension.objects.create(
            name="Cabanatuan", street="Main Street", barangay="Sumacab Norte",
            municipality="Cabanatuan City", province="Nueva Ecija",
            country="Philippines", postal_code="3100",
        )

    # ----- permissions and create / update
    def test_only_the_super_admin_may_manage_extensions(self):
        member = make_person(self.ext)
        with self.assertRaises(PermissionDenied):
            services.create_extension(member, None, {"name": "Nope"})
        self.assertFalse(Extension.objects.filter(name="Nope").exists())

    def test_create_writes_one_audit_row(self):
        ext = services.create_extension(self.admin, None, {
            "name": "Gapan", "street": "Main Street", "barangay": "San Lorenzo (Pob.)",
            "municipality": "City of Gapan", "province": "Nueva Ecija",
            "country": "Philippines", "postal_code": "3150",
        })
        row = audit("extension.create").get(entity_id=ext.pk)
        self.assertEqual(row.extension_id, ext.pk)
        self.assertEqual(row.actor_id, self.admin.pk)
        self.assertEqual(row.after["barangay"], "san lorenzo (pob.)")

    def test_duplicate_name_is_a_service_error(self):
        with self.assertRaises(ServiceError):
            services.create_extension(self.admin, None, {"name": "Cabanatuan"})

    def test_update_logs_only_the_changed_fields(self):
        services.update_extension(self.admin, None, self.ext, {"barangay": "Sumacab South"})
        row = audit("extension.update").get()
        self.assertEqual(row.before, {"barangay": "Sumacab Norte"})
        self.assertEqual(row.after, {"barangay": "sumacab south"})

    def test_update_with_no_changes_does_nothing(self):
        _, changed = services.update_extension(self.admin, None, self.ext, {"name": "Cabanatuan"})
        self.assertFalse(changed)
        self.assertEqual(audit("extension.update").count(), 0)

    def test_archived_extension_cannot_be_edited(self):
        services.archive_extension(self.admin, None, self.ext)
        with self.assertRaises(ServiceError):
            services.update_extension(self.admin, None, self.ext, {"street": "Main"})

    # ----- coordinator
    def test_assign_promotes_a_member_and_sets_the_link(self):
        person = make_person(self.ext)
        services.assign_coordinator(self.admin, None, self.ext, person)
        person.refresh_from_db()
        self.ext.refresh_from_db()
        self.assertEqual(person.role, Role.COORDINATOR)
        self.assertEqual(self.ext.coordinator_id, person.pk)
        self.assertEqual(audit("account.role_change").count(), 1)
        self.assertEqual(audit("extension.coordinator_change").count(), 1)

    def test_replacing_the_coordinator_demotes_the_old_one(self):
        old = make_person(self.ext, first_name="Old")
        new = make_person(self.ext, first_name="New")
        services.assign_coordinator(self.admin, None, self.ext, old)
        services.assign_coordinator(self.admin, None, self.ext, new)
        old.refresh_from_db()
        new.refresh_from_db()
        self.ext.refresh_from_db()
        self.assertEqual(old.role, Role.MEMBER)
        self.assertEqual(new.role, Role.COORDINATOR)
        self.assertEqual(self.ext.coordinator_id, new.pk)

    def test_choosing_the_same_coordinator_twice_changes_nothing(self):
        person = make_person(self.ext)
        services.assign_coordinator(self.admin, None, self.ext, person)
        _, changed = services.assign_coordinator(self.admin, None, self.ext, person)
        self.assertFalse(changed)
        self.assertEqual(audit("extension.coordinator_change").count(), 1)

    def test_a_person_from_another_extension_cannot_be_chosen(self):
        other = Extension.objects.create(name="Gapan")
        outsider = make_person(other)
        with self.assertRaises(ServiceError):
            services.assign_coordinator(self.admin, None, self.ext, outsider)

    def test_an_archived_person_cannot_be_chosen(self):
        person = make_person(self.ext)
        archive_person(person)
        with self.assertRaises(ServiceError):
            services.assign_coordinator(self.admin, None, self.ext, person)

    def test_unassign_demotes_and_clears_the_link(self):
        person = make_person(self.ext)
        services.assign_coordinator(self.admin, None, self.ext, person)
        services.unassign_coordinator(self.admin, None, self.ext)
        person.refresh_from_db()
        self.ext.refresh_from_db()
        self.assertEqual(person.role, Role.MEMBER)
        self.assertIsNone(self.ext.coordinator_id)
        _, changed = services.unassign_coordinator(self.admin, None, self.ext)
        self.assertFalse(changed)

    # ----- archive / restore / delete
    def test_archive_is_blocked_while_people_remain(self):
        make_person(self.ext)
        with self.assertRaises(ServiceError):
            services.archive_extension(self.admin, None, self.ext)
        self.ext.refresh_from_db()
        self.assertIsNone(self.ext.archived_at)

    def test_archive_sets_purge_date_and_restore_clears_it(self):
        services.archive_extension(self.admin, None, self.ext)
        self.ext.refresh_from_db()
        self.assertIsNotNone(self.ext.archived_at)
        self.assertIsNotNone(self.ext.purge_at)  # filled by the database trigger
        services.restore_extension(self.admin, None, self.ext)
        self.ext.refresh_from_db()
        self.assertIsNone(self.ext.archived_at)
        self.assertIsNone(self.ext.purge_at)

    def test_archiving_twice_is_harmless_and_logs_once(self):
        services.archive_extension(self.admin, None, self.ext)
        _, changed = services.archive_extension(self.admin, None, self.ext)
        self.assertFalse(changed)
        self.assertEqual(audit("extension.archive").count(), 1)

    def test_archive_clears_a_leftover_archived_coordinator(self):
        person = make_person(self.ext)
        services.assign_coordinator(self.admin, None, self.ext, person)
        archive_person(person)
        services.archive_extension(self.admin, None, self.ext)
        self.ext.refresh_from_db()
        self.assertIsNone(self.ext.coordinator_id)

    def test_delete_requires_an_archived_extension(self):
        with self.assertRaises(ServiceError):
            services.force_delete_extension(self.admin, None, self.ext)
        self.assertTrue(Extension.objects.filter(pk=self.ext.pk).exists())

    def test_delete_is_blocked_while_archived_people_still_reference_it(self):
        person = make_person(self.ext)
        archive_person(person)
        services.archive_extension(self.admin, None, self.ext)
        with self.assertRaises(ServiceError):
            services.force_delete_extension(self.admin, None, self.ext)
        self.assertTrue(Extension.objects.filter(pk=self.ext.pk).exists())
        self.assertEqual(audit("extension.delete").count(), 0)  # rolled back with the failed delete

    def test_delete_logs_then_removes_the_extension(self):
        pk = self.ext.pk
        services.archive_extension(self.admin, None, self.ext)
        services.force_delete_extension(self.admin, None, self.ext)
        self.assertFalse(Extension.objects.filter(pk=pk).exists())
        self.assertEqual(audit("extension.delete").filter(entity_id=pk).count(), 1)


class ExtensionViewTests(TestCase):
    """Pages, guards and messages."""

    def setUp(self):
        self.admin = make_super_admin()
        self.client.force_login(self.admin)
        self.ext = Extension.objects.create(name="Cabanatuan", barangay="Sumacab")

    def test_anonymous_visitors_are_sent_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("superadmin:extension_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_other_roles_are_sent_to_their_own_home(self):
        self.client.force_login(make_person(self.ext))
        response = self.client.get(reverse("superadmin:extension_list"))
        self.assertRedirects(response, reverse("dashboards:member"), fetch_redirect_response=False)

    def test_list_search_and_status_filter(self):
        Extension.objects.create(name="Gapan")
        archived = Extension.objects.create(name="Old Branch")
        services.archive_extension(self.admin, None, archived)
        url = reverse("superadmin:extension_list")

        names = lambda r: [e.name for e in r.context["page"].object_list]
        self.assertEqual(names(self.client.get(url)), ["Cabanatuan", "Gapan"])
        self.assertEqual(names(self.client.get(url, {"q": "sumacab"})), ["Cabanatuan"])
        self.assertEqual(names(self.client.get(url, {"status": "archived"})), ["Old Branch"])
        self.assertEqual(len(names(self.client.get(url, {"status": "all"}))), 3)
        self.assertEqual(names(self.client.get(url, {"sort": "-name"})), ["Gapan", "Cabanatuan"])

    def test_a_bad_sort_value_falls_back_to_the_default(self):
        response = self.client.get(reverse("superadmin:extension_list"), {"sort": "password"})
        self.assertEqual(response.status_code, 200)

    def test_list_counts_only_non_archived_people(self):
        make_person(self.ext)
        gone = make_person(self.ext, first_name="Gone")
        archive_person(gone)
        response = self.client.get(reverse("superadmin:extension_list"))
        self.assertEqual(response.context["page"].object_list[0].member_count, 1)

    def test_create_redirects_to_the_new_extension(self):
        response = self.client.post(reverse("superadmin:extension_create"), {
            "name": "  Gapan   Church ", "street": "Main Street",
            "barangay": "San Lorenzo (Pob.)", "municipality": "City of Gapan",
            "province": "Nueva Ecija", "country": "Philippines", "postal_code": "3105",
        })
        ext = Extension.objects.get(name="gapan church")
        self.assertRedirects(
            response, reverse("superadmin:extension_detail", args=[ext.pk]),
            fetch_redirect_response=False,
        )

    def test_create_rejects_a_name_that_differs_only_by_case(self):
        response = self.client.post(reverse("superadmin:extension_create"), {
            "name": "cabanatuan", "street": "Main Street", "barangay": "Sumacab Norte",
            "municipality": "Cabanatuan City", "province": "Nueva Ecija",
            "country": "Philippines", "postal_code": "3100",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(Extension.objects.count(), 1)

    def test_edit_saves_changes(self):
        url = reverse("superadmin:extension_edit", args=[self.ext.pk])
        response = self.client.post(url, {
            "name": "Cabanatuan", "street": "Main Street", "barangay": "Dalampang",
            "municipality": "Cabanatuan City", "province": "Nueva Ecija",
            "country": "Philippines", "postal_code": "3100",
        })
        self.assertRedirects(
            response, reverse("superadmin:extension_detail", args=[self.ext.pk]),
            fetch_redirect_response=False,
        )
        self.ext.refresh_from_db()
        self.assertEqual(self.ext.barangay, "dalampang")

    def test_detail_lists_people_and_the_coordinator_picker(self):
        person = make_person(self.ext)
        response = self.client.get(reverse("superadmin:extension_detail", args=[self.ext.pk]))
        self.assertContains(response, person.account_id)
        self.assertContains(response, "Set coordinator")

    def test_actions_reject_get_requests(self):
        for name in ("archive", "restore", "delete"):
            url = reverse(f"superadmin:extension_{name}", args=[self.ext.pk])
            self.assertEqual(self.client.get(url).status_code, 405, name)

    def test_archive_view_shows_the_rule_when_people_remain(self):
        make_person(self.ext)
        response = self.client.post(
            reverse("superadmin:extension_archive", args=[self.ext.pk]), follow=True
        )
        self.assertContains(response, "Move or archive them first")

    def test_acting_on_a_deleted_extension_is_calm_not_a_404(self):
        url = reverse("superadmin:extension_delete", args=[999999])
        response = self.client.post(url)
        self.assertRedirects(
            response, reverse("superadmin:extension_list"), fetch_redirect_response=False
        )

    def test_assign_view_sets_the_coordinator(self):
        person = make_person(self.ext)
        self.client.post(
            reverse("superadmin:extension_assign_coordinator", args=[self.ext.pk]),
            {"user": person.pk},
        )
        self.ext.refresh_from_db()
        self.assertEqual(self.ext.coordinator_id, person.pk)
