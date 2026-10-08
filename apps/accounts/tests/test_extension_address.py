"""accounts.tests.test_extension_address: address, cleaning and bulk-action tests.

The first class needs no database. The others use the throwaway test database like
core.test_schema does. Run with:
    docker compose exec web python manage.py test apps.accounts.tests.test_extension_address
"""
from django.test import RequestFactory, SimpleTestCase, TestCase

from apps.core.services import ServiceError

from .. import address
from ..forms import ExtensionForm
from ..models import Extension, Role, Status, User
from ..services import (
    archive_extension,
    bulk_archive_extensions,
    bulk_delete_extensions,
    clean_extension_data,
    create_extension,
    update_extension,
)

GOOD = {
    "name": "Santa Rosa", "country": "Philippines", "province": "Nueva Ecija",
    "municipality": "Santa Rosa", "barangay": "Cojuangco (Pob.)",
    "postal_code": "3101", "street": "Main Street", "building_number": "",
}


class AddressRuleTests(SimpleTestCase):
    """accounts.address.check_address, with no database."""

    def values(self, **changes):
        return {k: v.lower() for k, v in {**GOOD, **changes}.items()}

    def test_listed_address_passes(self):
        self.assertEqual(address.check_address(self.values()), {})

    def test_comparison_ignores_case(self):
        self.assertEqual(address.check_address({**GOOD, "province": "NUEVA ECIJA"}), {})

    def test_municipality_must_belong_to_province(self):
        errors = address.check_address(self.values(municipality="baliuag"))
        self.assertIn("municipality", errors)

    def test_barangay_must_belong_to_municipality(self):
        errors = address.check_address(self.values(barangay="nowhere"))
        self.assertIn("barangay", errors)

    def test_philippine_postal_code_is_four_digits(self):
        self.assertIn("postal_code", address.check_address(self.values(postal_code="31")))

    def test_other_countries_are_free_text(self):
        data = self.values(country="Japan", province="anything", postal_code="100-0001")
        self.assertEqual(address.check_address(data), {})

    def test_old_unlisted_value_is_kept_until_changed(self):
        data = self.values(barangay="old place")
        self.assertIn("barangay", address.check_address(data))
        self.assertEqual(address.check_address(data, existing={"barangay": "Old Place"}), {})


class CleanExtensionDataTests(SimpleTestCase):
    def test_lowercases_and_keeps_postal_code(self):
        clean = clean_extension_data({**GOOD, "name": "  JIL   Sta. Rosa "})
        self.assertEqual(clean["name"], "jil sta. rosa")
        self.assertEqual(clean["country"], "philippines")
        self.assertEqual(clean["postal_code"], "3101")

    def test_building_number_is_the_only_optional_field(self):
        clean_extension_data({**GOOD, "building_number": ""})
        for field in ("name", "country", "province", "municipality", "barangay", "postal_code", "street"):
            with self.assertRaises(ServiceError, msg=field):
                clean_extension_data({**GOOD, field: ""})


class ExtensionServiceTests(TestCase):
    """Services against the test database."""

    def setUp(self):
        self.actor = User.objects.create_superuser(first_name="Super", last_name="Admin", password="x-test-pass")
        self.request = RequestFactory().post("/")
        self.request.user = self.actor

    def make(self, name, **extra):
        return create_extension(self.actor, self.request, {**GOOD, "name": name, **extra})

    def test_create_stores_lowercase(self):
        ext = self.make("JIL Cabanatuan")
        self.assertEqual(ext.name, "jil cabanatuan")
        self.assertEqual(ext.province, "nueva ecija")

    def test_duplicate_name_ignores_case(self):
        self.make("Cabanatuan")
        with self.assertRaises(ServiceError):
            self.make("CABANATUAN")

    def test_form_rejects_blank_required_fields(self):
        form = ExtensionForm({**GOOD, "street": "", "barangay": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("street", form.errors)
        self.assertIn("barangay", form.errors)

    def test_form_accepts_a_complete_listed_address(self):
        form = ExtensionForm(GOOD)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["name"], "santa rosa")

    def test_old_mixed_case_row_is_not_rewritten_without_a_change(self):
        ext = Extension.objects.create(**{**GOOD, "name": "Old Name", "street": "Old St"})
        _, changed = update_extension(self.actor, self.request, ext, dict(GOOD, name="old name", street="old st"))
        self.assertFalse(changed)
        ext.refresh_from_db()
        self.assertEqual(ext.name, "Old Name")

    def test_bulk_archive_skips_extensions_with_people(self):
        empty = self.make("Empty One")
        busy = self.make("Busy One")
        User.objects.create_user(
            first_name="Mem", last_name="Ber", role=Role.MEMBER, extension=busy,
            status=Status.ACTIVE, password="x-test-pass",
        )
        result = bulk_archive_extensions(self.actor, self.request, [empty.pk, busy.pk])
        self.assertEqual(result.done, ["Empty One"])
        self.assertEqual(len(result.skipped), 1)
        self.assertIn("Busy One", result.skipped[0])
        empty.refresh_from_db(); busy.refresh_from_db()
        self.assertTrue(empty.is_archived)
        self.assertFalse(busy.is_archived)

    def test_bulk_delete_only_removes_archived_extensions(self):
        archived = self.make("Gone One")
        archive_extension(self.actor, self.request, archived)
        active = self.make("Stays One")
        result = bulk_delete_extensions(self.actor, self.request, [archived.pk, active.pk])
        self.assertEqual(result.done, ["Gone One"])
        self.assertEqual(len(result.skipped), 1)
        self.assertTrue(Extension.objects.filter(pk=active.pk).exists())
        self.assertFalse(Extension.objects.filter(pk=archived.pk).exists())
