"""Tests for the main church tag: one holder at most, moved by the services, never archived."""
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase

from apps.accounts.forms import ExtensionForm
from apps.accounts.models import Extension, User
from apps.accounts.services import (
    archive_extension, create_extension, update_extension,
)
from apps.core.services import ServiceError

ADDRESS = {
    "country": "japan", "province": "kanto", "municipality": "tokyo", "barangay": "shibuya",
    "postal_code": "1500001",
}


def make_extension(name, **extra):
    """An extension with a complete address, so `update_extension` accepts it."""
    return Extension.objects.create(name=name, **{**ADDRESS, **extra})


class MainChurchTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser(first_name="sam", last_name="root", password="x-Strong-pass-1")
        self.request = RequestFactory().post("/")
        self.request.user = self.actor
        self.a = make_extension("alpha")
        self.b = make_extension("bravo")

    def holders(self):
        return set(Extension.objects.filter(is_main_church=True).values_list("name", flat=True))

    def test_database_allows_only_one_main_church(self):
        self.a.is_main_church = True
        self.a.save()
        self.b.is_main_church = True
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.b.save()

    def test_giving_the_tag_moves_it(self):
        update_extension(self.actor, self.request, self.a, {"is_main_church": True})
        self.assertEqual(self.holders(), {"alpha"})
        _, changed = update_extension(self.actor, self.request, self.b, {"is_main_church": True})
        self.assertTrue(changed)
        self.assertEqual(self.holders(), {"bravo"})

    def test_a_form_without_the_key_leaves_the_tag_alone(self):
        update_extension(self.actor, self.request, self.a, {"is_main_church": True})
        _, changed = update_extension(self.actor, self.request, self.a, {})
        self.assertFalse(changed)
        self.assertEqual(self.holders(), {"alpha"})

    def test_unticking_removes_the_tag(self):
        update_extension(self.actor, self.request, self.a, {"is_main_church": True})
        update_extension(self.actor, self.request, self.a, {"is_main_church": False})
        self.assertEqual(self.holders(), set())

    def test_create_extension_can_take_the_tag(self):
        update_extension(self.actor, self.request, self.a, {"is_main_church": True})
        new = create_extension(self.actor, self.request, {
            "name": "Charlie", **ADDRESS, "is_main_church": True,
        })
        self.assertEqual(self.holders(), {new.name})

    def test_the_main_church_cannot_be_archived(self):
        update_extension(self.actor, self.request, self.a, {"is_main_church": True})
        with self.assertRaises(ServiceError):
            archive_extension(self.actor, self.request, self.a)
        self.assertEqual(self.holders(), {"alpha"})

    def test_the_form_does_not_refuse_a_second_main_church(self):
        update_extension(self.actor, self.request, self.a, {"is_main_church": True})
        form = ExtensionForm({
            "name": "bravo", "country": "Japan", "province": "kanto", "municipality": "tokyo",
            "barangay": "shibuya", "postal_code": "1500001", "is_main_church": "on",
        }, instance=self.b)
        self.assertNotIn("is_main_church", form.errors)
        self.assertEqual(form.current_main_church, "Alpha")
