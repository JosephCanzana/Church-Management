"""Tests for the managed donation accounts (brick 7): services, audit rows, files and screens."""
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.core.services import ServiceError
from apps.pages import manage_services
from apps.pages.models import DonationAccount
from apps.pages.services import get_landing_context

from .helpers import TempMediaMixin, make_user, png_upload

NOT_PUBLISHED = "Giving details have not been published yet"


def account_data(**extra):
    """Minimal valid form-like data for create_donation_account."""
    data = {
        "payment_app": "GCash",
        "account_name": "Jesus Is Lord Church",
        "account_number": "0917 000 0000",
        "swift_code": "",
        "instructions": "",
        "sort_order": 0,
    }
    data.update(extra)
    return data


def form_post(**extra):
    """Form fields as a browser would post them."""
    post = {
        "payment_app": "GCash",
        "account_name": "Jesus Is Lord Church",
        "account_number": "0917 000 0000",
        "swift_code": "",
        "instructions": "",
        "sort_order": "",
    }
    post.update(extra)
    return post


class DonationAccountServiceTests(TempMediaMixin, TestCase):
    """Create, update, toggle and delete, with guards, idempotence and audit rows."""

    def setUp(self):
        self.admin = make_user(Role.ADMIN)

    def audit(self, action):
        return AuditLog.objects.filter(action=action)

    def test_create_makes_a_public_account_and_audits_it(self):
        account = manage_services.create_donation_account(self.admin, data=account_data())
        self.assertTrue(account.is_active)
        self.assertEqual(account.updated_by, self.admin)
        entry = self.audit("pages.donation_account_created").get()
        self.assertEqual(entry.after["payment_app"], "GCash")
        self.assertFalse(entry.after["qr_image_set"])

    def test_create_requires_a_payment_method(self):
        with self.assertRaises(ServiceError):
            manage_services.create_donation_account(self.admin, data=account_data(payment_app=""))
        self.assertEqual(DonationAccount.objects.count(), 0)

    def test_update_logs_before_and_after_and_only_changed_fields(self):
        account = manage_services.create_donation_account(self.admin, data=account_data())
        manage_services.update_donation_account(
            self.admin, account.pk, data={"account_number": "0999 111 2222"})
        entry = self.audit("pages.donation_account_updated").get()
        self.assertEqual(entry.before["account_number"], "0917 000 0000")
        self.assertEqual(entry.after["account_number"], "0999 111 2222")
        self.assertEqual(entry.after["changed_fields"], ["account_number"])

    def test_update_with_no_change_returns_false_and_logs_nothing(self):
        account = manage_services.create_donation_account(self.admin, data=account_data())
        self.assertFalse(manage_services.update_donation_account(
            self.admin, account.pk, data={"payment_app": "GCash"}))
        self.assertEqual(self.audit("pages.donation_account_updated").count(), 0)

    def test_update_of_a_missing_account_is_refused(self):
        with self.assertRaises(ServiceError):
            manage_services.update_donation_account(self.admin, 999999, data={"payment_app": "X"})

    def test_toggle_hides_and_shows_and_is_idempotent(self):
        account = manage_services.create_donation_account(self.admin, data=account_data())
        self.assertTrue(manage_services.set_donation_account_active(self.admin, account.pk, False))
        self.assertFalse(manage_services.set_donation_account_active(self.admin, account.pk, False))
        self.assertEqual(self.audit("pages.donation_account_toggled").count(), 1)
        entry = self.audit("pages.donation_account_toggled").get()
        self.assertEqual((entry.before["is_active"], entry.after["is_active"]), (True, False))

    def test_toggle_refuses_a_non_boolean(self):
        account = manage_services.create_donation_account(self.admin, data=account_data())
        with self.assertRaises(ServiceError):
            manage_services.set_donation_account_active(self.admin, account.pk, "yes")

    def test_hidden_accounts_leave_the_landing_context(self):
        account = manage_services.create_donation_account(self.admin, data=account_data())
        self.assertEqual([a.pk for a in get_landing_context()["give_accounts"]], [account.pk])
        manage_services.set_donation_account_active(self.admin, account.pk, False)
        self.assertEqual(get_landing_context()["give_accounts"], [])

    def test_delete_removes_the_row_the_qr_file_and_audits_without_file_data(self):
        account = manage_services.create_donation_account(
            self.admin, data=account_data(qr_image=png_upload("my-secret-qr.png")))
        name, storage = account.qr_image.name, account.qr_image.storage
        self.assertTrue(storage.exists(name))
        with self.captureOnCommitCallbacks(execute=True):
            manage_services.delete_donation_account(self.admin, account.pk)
        self.assertFalse(DonationAccount.objects.filter(pk=account.pk).exists())
        self.assertFalse(storage.exists(name))
        entry = self.audit("pages.donation_account_deleted").get()
        self.assertTrue(entry.before["qr_image_set"])
        self.assertNotIn("my-secret-qr", str(entry.before))

    def test_replacing_the_qr_image_removes_the_old_file(self):
        account = manage_services.create_donation_account(
            self.admin, data=account_data(qr_image=png_upload("first.png")))
        old_name, storage = account.qr_image.name, account.qr_image.storage
        with self.captureOnCommitCallbacks(execute=True):
            manage_services.update_donation_account(
                self.admin, account.pk, data={"qr_image": png_upload("second.png")})
        account.refresh_from_db()
        self.assertNotEqual(account.qr_image.name, old_name)
        self.assertFalse(storage.exists(old_name))
        self.assertTrue(storage.exists(account.qr_image.name))

    def test_text_edit_still_works_when_the_qr_file_is_missing_from_disk(self):
        account = manage_services.create_donation_account(
            self.admin, data=account_data(qr_image=png_upload()))
        account.qr_image.storage.delete(account.qr_image.name)
        manage_services.update_donation_account(
            self.admin, account.pk, data={"account_name": "Renamed"})
        account.refresh_from_db()
        self.assertEqual(account.account_name, "Renamed")

    def test_non_managers_are_refused_by_every_service(self):
        member = make_user(Role.MEMBER)
        account = manage_services.create_donation_account(self.admin, data=account_data())
        for call in (
            lambda: manage_services.create_donation_account(member, data=account_data()),
            lambda: manage_services.update_donation_account(member, account.pk, data={"payment_app": "X"}),
            lambda: manage_services.set_donation_account_active(member, account.pk, False),
            lambda: manage_services.delete_donation_account(member, account.pk),
        ):
            with self.subTest():
                with self.assertRaises(PermissionDenied):
                    call()
        self.assertTrue(DonationAccount.objects.filter(pk=account.pk, is_active=True).exists())


class DonationAccountScreenTests(TempMediaMixin, TestCase):
    """The management screens and what the public landing page shows."""

    def setUp(self):
        self.admin = make_user(Role.ADMIN)
        self.list_url = reverse("pages:pages_manage:donation_accounts")
        self.create_url = reverse("pages:pages_manage:donation_account_create")

    def test_anonymous_and_members_cannot_open_the_screens(self):
        self.assertEqual(self.client.get(self.list_url).status_code, 302)
        self.client.force_login(make_user(Role.MEMBER))
        self.assertNotEqual(self.client.get(self.list_url).status_code, 200)
        self.assertNotEqual(self.client.get(self.create_url).status_code, 200)

    def test_empty_list_explains_what_the_public_sees(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.list_url)
        self.assertContains(response, "No donation accounts yet")

    def test_create_with_a_qr_image_through_the_form(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, form_post(qr_image=png_upload()))
        self.assertRedirects(response, self.list_url)
        account = DonationAccount.objects.get()
        self.assertTrue(account.qr_image)
        self.assertEqual(account.qr_image_original_filename, "qr.png")
        self.assertTrue(account.qr_image.storage.exists(account.qr_image.name))

    def test_wrong_file_type_shows_an_inline_error_and_saves_nothing(self):
        self.client.force_login(self.admin)
        bad = SimpleUploadedFile("qr.txt", b"not an image", content_type="text/plain")
        response = self.client.post(self.create_url, form_post(qr_image=bad))
        self.assertEqual(response.status_code, 200)
        self.assertIn("qr_image", response.context["form"].errors)
        self.assertEqual(DonationAccount.objects.count(), 0)

    def test_missing_payment_method_shows_an_inline_error(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, form_post(payment_app=""))
        self.assertEqual(response.status_code, 200)
        self.assertIn("payment_app", response.context["form"].errors)

    def test_edit_can_clear_the_qr_image(self):
        account = manage_services.create_donation_account(
            self.admin, data=account_data(qr_image=png_upload()))
        storage, name = account.qr_image.storage, account.qr_image.name
        self.client.force_login(self.admin)
        url = reverse("pages:pages_manage:donation_account_edit", args=[account.pk])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, form_post(**{"qr_image-clear": "on"}))
        self.assertRedirects(response, self.list_url)
        account.refresh_from_db()
        self.assertFalse(account.qr_image)
        self.assertFalse(storage.exists(name))

    def test_edit_without_touching_the_qr_keeps_it(self):
        account = manage_services.create_donation_account(
            self.admin, data=account_data(qr_image=png_upload()))
        name = account.qr_image.name
        self.client.force_login(self.admin)
        url = reverse("pages:pages_manage:donation_account_edit", args=[account.pk])
        self.client.post(url, form_post(account_name="New Name"))
        account.refresh_from_db()
        self.assertEqual(account.account_name, "New Name")
        self.assertEqual(account.qr_image.name, name)

    def test_toggle_and_delete_refuse_get_and_bad_values(self):
        account = manage_services.create_donation_account(self.admin, data=account_data())
        self.client.force_login(self.admin)
        toggle = reverse("pages:pages_manage:donation_account_toggle", args=[account.pk])
        delete = reverse("pages:pages_manage:donation_account_delete", args=[account.pk])
        self.assertEqual(self.client.get(toggle).status_code, 405)
        self.assertEqual(self.client.get(delete).status_code, 405)
        self.client.post(toggle, {"active": "maybe"})
        account.refresh_from_db()
        self.assertTrue(account.is_active)

    def test_toggle_through_the_screen_hides_the_account(self):
        account = manage_services.create_donation_account(self.admin, data=account_data())
        self.client.force_login(self.admin)
        toggle = reverse("pages:pages_manage:donation_account_toggle", args=[account.pk])
        self.assertRedirects(self.client.post(toggle, {"active": "0"}), self.list_url)
        account.refresh_from_db()
        self.assertFalse(account.is_active)

    def test_landing_page_is_public_and_follows_the_active_flag(self):
        account = manage_services.create_donation_account(self.admin, data=account_data())
        response = self.client.get("/")
        self.assertContains(response, "0917 000 0000")
        self.assertNotContains(response, NOT_PUBLISHED)
        manage_services.set_donation_account_active(self.admin, account.pk, False)
        response = self.client.get("/")
        self.assertNotContains(response, "0917 000 0000")
        self.assertContains(response, NOT_PUBLISHED)

    def test_landing_page_with_no_accounts_shows_the_not_published_note(self):
        self.assertContains(self.client.get("/"), NOT_PUBLISHED)
