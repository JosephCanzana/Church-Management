"""Tests for the public /give/ page and the landing #give invitation."""
from django.test import TestCase
from django.urls import reverse

from apps.pages.models import ChurchContent, DonationAccount


def _account(**kwargs):
    """Create a donation account with sensible defaults."""
    data = {"payment_app": "GCash", "account_name": "JIL Church", "account_number": "0917 000 0000"}
    data.update(kwargs)
    return DonationAccount.objects.create(**data)


def _instructions(body):
    """Save English donation instructions."""
    return ChurchContent.objects.create(
        key=ChurchContent.Key.DONATION_INSTRUCTIONS, language="en", body=body
    )


class GivePageTests(TestCase):
    def setUp(self):
        self.url = reverse("pages:give")

    def test_public_without_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_url_is_give(self):
        self.assertEqual(self.url, "/give/")

    def test_no_accounts_shows_not_published_note(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Giving details have not been published yet")

    def test_no_saved_instructions_invents_nothing(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context["donation_instructions"], "")

    def test_shows_active_accounts_only(self):
        _account(payment_app="GCash")
        _account(payment_app="Hidden Bank", is_active=False)
        response = self.client.get(self.url)
        self.assertContains(response, "GCash")
        self.assertNotContains(response, "Hidden Bank")
        self.assertNotContains(response, "have not been published yet")

    def test_instructions_are_escaped_and_keep_line_breaks(self):
        _instructions("<script>x</script>\nSecond line")
        response = self.client.get(self.url)
        self.assertNotContains(response, "<script>x</script>")
        self.assertContains(response, "&lt;script&gt;x&lt;/script&gt;<br>Second line")

    def test_instructions_show_without_accounts(self):
        _instructions("Thank you for giving.")
        response = self.client.get(self.url)
        self.assertContains(response, "Thank you for giving.")
        self.assertContains(response, "have not been published yet")

    def test_read_only_no_form_and_no_write(self):
        _account()
        before = DonationAccount.objects.count()
        response = self.client.get(self.url)
        self.assertNotContains(response, "<form")
        self.assertNotContains(response, 'name="amount"')
        self.assertEqual(self.client.post(self.url, {"amount": "100"}).status_code, 405)
        self.assertEqual(DonationAccount.objects.count(), before)


class LandingGiveLinkTests(TestCase):
    def test_landing_give_block_links_to_give_page(self):
        response = self.client.get(reverse("pages:landing"))
        self.assertContains(response, 'id="give"')
        self.assertContains(response, 'href="%s"' % reverse("pages:give"))

    def test_landing_no_longer_holds_account_details(self):
        _account(account_number="1234-UNIQUE-5678")
        _instructions("UNIQUE-INSTRUCTION-TEXT")
        response = self.client.get(reverse("pages:landing"))
        self.assertNotContains(response, "1234-UNIQUE-5678")
        self.assertNotContains(response, "UNIQUE-INSTRUCTION-TEXT")

    def test_landing_anchors_still_exist(self):
        response = self.client.get(reverse("pages:landing"))
        self.assertContains(response, 'id="about"')
        self.assertContains(response, 'id="give"')
