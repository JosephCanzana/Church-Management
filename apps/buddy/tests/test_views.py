"""Brick 6: the Buddy tab and its POST routes."""
from importlib.util import find_spec

from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, Status

from apps.buddy import services
from apps.buddy.models import BuddyRequest, UserBuddy

from .helpers import make_extension, make_user

HOME = "buddy:home"


def pair(a, b):
    """Create a live partnership row with the ids sorted."""
    return UserBuddy.objects.create(user_a_id=min(a.pk, b.pk), user_b_id=max(a.pk, b.pk))


class AccessTests(TestCase):
    """Sign-in is required and only people who can sign in may use Buddy."""

    def test_anonymous_is_sent_to_sign_in(self):
        response = self.client.get(reverse(HOME))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("/buddy/", response["Location"].split("?")[0])

    def test_post_routes_need_sign_in_too(self):
        response = self.client.post(reverse("buddy:request_send", args=[1]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(BuddyRequest.objects.exists())

    def test_a_suspended_person_does_not_get_in(self):
        user = make_user(status=Status.SUSPENDED)
        self.client.force_login(user)
        self.assertNotEqual(self.client.get(reverse(HOME)).status_code, 200)

    def test_post_only_routes_refuse_get(self):
        self.client.force_login(make_user())
        for name, args in (
            ("buddy:request_send", [1]), ("buddy:request_accept", [1]),
            ("buddy:request_decline", [1]), ("buddy:request_cancel", [1]),
        ):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 405, name)

    def test_every_role_that_can_sign_in_gets_the_tab(self):
        for role in (Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR, Role.MEMBER):
            self.client.force_login(make_user(role=role))
            self.assertEqual(self.client.get(reverse(HOME)).status_code, 200, role)

    def test_pages_are_not_cached(self):
        self.client.force_login(make_user())
        self.assertIn("no-store", self.client.get(reverse(HOME))["Cache-Control"])


class NoPartnerStateTests(TestCase):
    """The tab for someone without a live buddy."""

    def setUp(self):
        self.ext = make_extension()
        self.me = make_user(first="Me", extension=self.ext)
        self.mate = make_user(first="Clara", last="Mateo", extension=self.ext)
        self.client.force_login(self.me)

    def test_search_box_list_and_request_sections_render(self):
        response = self.client.get(reverse(HOME))
        self.assertContains(response, 'type="search"')
        self.assertContains(response, self.mate.full_name)
        self.assertContains(response, "Requests for you")
        self.assertContains(response, "Requests you sent")
        self.assertContains(response, reverse("buddy:request_send", args=[self.mate.pk]))
        self.assertNotContains(response, "Your buddy")

    def test_search_finds_someone_from_another_extension(self):
        far = make_user(first="Zoe", last="Zamora")
        response = self.client.get(reverse(HOME), {"q": "zamora"})
        self.assertContains(response, far.full_name)

    def test_incoming_and_outgoing_requests_are_listed_with_their_buttons(self):
        asker = make_user(first="Asker", last="Ann", extension=self.ext)
        incoming = services.send_request(asker, self.me.pk)
        outgoing = services.send_request(self.me, self.mate.pk)
        response = self.client.get(reverse(HOME))
        self.assertContains(response, reverse("buddy:request_accept", args=[incoming.pk]))
        self.assertContains(response, reverse("buddy:request_decline", args=[incoming.pk]))
        self.assertContains(response, reverse("buddy:request_cancel", args=[outgoing.pk]))
        self.assertContains(response, "Request sent")      # the reason is text, not just a greyed button

    def test_at_the_cap_the_request_buttons_become_text(self):
        targets = [make_user(extension=self.ext) for _ in range(3)]
        for target in targets:
            services.send_request(self.me, target.pk)
        free = make_user(first="Freda", extension=self.ext)
        response = self.client.get(reverse(HOME))
        self.assertNotContains(response, reverse("buddy:request_send", args=[free.pk]))
        self.assertContains(response, "Request limit reached")

    def test_there_is_no_tab_row_and_no_chat_without_a_buddy(self):
        response = self.client.get(reverse(HOME))
        self.assertNotContains(response, 'href="/buddy/messages/"')
        self.assertNotContains(response, 'href="/buddy/goals/"')
        self.assertNotContains(response, "buddy-chat-data")

    def test_the_old_messages_and_goals_addresses_redirect_to_the_page(self):
        for name in ("buddy:messages", "buddy:goals"):
            response = self.client.get(reverse(name))
            self.assertRedirects(response, reverse(HOME), fetch_redirect_response=False)


class PartnerStateTests(TestCase):
    """The tab for someone with a live buddy: the partner card, not the list."""

    def setUp(self):
        self.ext = make_extension("Grace Extension")
        self.me = make_user(first="Me", extension=self.ext)
        self.partner = make_user(first="Paula", last="Partner", extension=self.ext)
        self.row = pair(self.me, self.partner)
        self.bystander = make_user(first="Bystander", last="Nearby", extension=self.ext)
        self.client.force_login(self.me)

    def test_partner_card_replaces_the_list(self):
        response = self.client.get(reverse(HOME))
        self.assertContains(response, "Your buddy")
        self.assertContains(response, self.partner.full_name)
        self.assertContains(response, "Grace Extension")
        self.assertContains(response, "Partners since")
        self.assertNotContains(response, 'type="search"')
        self.assertNotContains(response, self.bystander.full_name)

    def test_the_page_holds_the_card_the_goals_and_the_chat(self):
        response = self.client.get(reverse(HOME))
        self.assertContains(response, "Their goals")
        self.assertContains(response, "buddy-chat-data")
        self.assertContains(response, f"Conversation with {self.partner.full_name}")
        self.assertEqual(response.context["goals_view"]["state"], "live")
        self.assertTrue(response.context["chat"]["active"])

    def test_an_inactive_partner_keeps_a_read_only_chat_next_to_the_list(self):
        self.partner.status = Status.SUSPENDED
        self.partner.save(update_fields=["status"])
        response = self.client.get(reverse(HOME))
        self.assertTrue(response.context["chat"]["read_only"])
        self.assertContains(response, "buddy-chat-data")
        self.assertContains(response, "can no longer sign in")

    def test_an_inactive_partner_shows_the_normal_list_again(self):
        self.partner.status = Status.SUSPENDED
        self.partner.save(update_fields=["status"])
        response = self.client.get(reverse(HOME))
        self.assertNotContains(response, "Your buddy")
        self.assertContains(response, 'type="search"')

    def test_partner_streaks_come_from_the_faith_engine(self):
        if find_spec("apps.faith.streaks") is None:
            self.skipTest("faith.streaks (Brick 1) is not installed yet")
        from apps.faith.models import UserStreak

        today = timezone.localdate()
        UserStreak.objects.create(
            user=self.partner,
            current_open_streak=7, longest_open_streak=11, last_open_date=today,
            current_read_streak=4, longest_read_streak=9, last_read_date=today,
        )
        card = services.partner_card(self.me)
        self.assertEqual(
            card["streaks"],
            {"open_current": 7, "open_best": 11, "read_current": 4, "read_best": 9},
        )
        response = self.client.get(reverse(HOME))
        self.assertContains(response, ">7<")
        self.assertContains(response, ">4<")


class PostRouteTests(TestCase):
    """The POST routes: they work for the right person and for nobody else."""

    def setUp(self):
        self.ext = make_extension()
        self.sender = make_user(first="Sender", extension=self.ext)
        self.recipient = make_user(first="Recipient", extension=self.ext)
        self.stranger = make_user(first="Stranger", extension=self.ext)

    def last_message(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_send_creates_a_request_and_goes_back_to_the_tab(self):
        self.client.force_login(self.sender)
        response = self.client.post(reverse("buddy:request_send", args=[self.recipient.pk]))
        self.assertRedirects(response, reverse(HOME), fetch_redirect_response=False)
        self.assertTrue(BuddyRequest.objects.filter(from_user=self.sender, to_user=self.recipient).exists())

    def test_send_keeps_the_search_text_and_page_in_the_redirect(self):
        self.client.force_login(self.sender)
        response = self.client.post(
            reverse("buddy:request_send", args=[self.recipient.pk]), {"q": "reci", "page": "2"},
        )
        self.assertEqual(response["Location"], f"{reverse(HOME)}?q=reci&page=2")

    def test_the_redirect_cannot_be_pointed_at_another_site(self):
        self.client.force_login(self.sender)
        response = self.client.post(
            reverse("buddy:request_send", args=[self.recipient.pk]),
            {"q": "x", "next": "https://evil.example/"},
        )
        self.assertTrue(response["Location"].startswith(reverse(HOME)))

    def test_a_refused_send_shows_a_message_and_changes_nothing(self):
        self.client.force_login(self.sender)
        response = self.client.post(reverse("buddy:request_send", args=[self.sender.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.last_message(response))
        self.assertFalse(BuddyRequest.objects.exists())

    def test_the_recipient_can_accept(self):
        request = services.send_request(self.sender, self.recipient.pk)
        self.client.force_login(self.recipient)
        response = self.client.post(reverse("buddy:request_accept", args=[request.pk]))
        self.assertRedirects(response, reverse(HOME), fetch_redirect_response=False)
        self.assertEqual(UserBuddy.objects.filter(ended_at__isnull=True).count(), 1)
        self.assertIn("now buddies", self.last_message(response)[0])

    def test_a_stranger_cannot_accept_decline_or_cancel_someone_elses_request(self):
        request = services.send_request(self.sender, self.recipient.pk)
        self.client.force_login(self.stranger)
        for name in ("buddy:request_accept", "buddy:request_decline", "buddy:request_cancel"):
            response = self.client.post(reverse(name, args=[request.pk]))
            self.assertEqual(response.status_code, 302, name)
        request.refresh_from_db()
        self.assertEqual(request.status, BuddyRequest.Status.PENDING)
        self.assertFalse(UserBuddy.objects.exists())

    def test_a_missing_request_and_someone_elses_request_give_the_same_message(self):
        request = services.send_request(self.sender, self.recipient.pk)
        # Two clients, so the first reply's unread message cannot leak into the second one.
        first, second = Client(), Client()
        first.force_login(self.stranger)
        second.force_login(self.stranger)
        theirs = first.post(reverse("buddy:request_accept", args=[request.pk]))
        missing = second.post(reverse("buddy:request_accept", args=[999999]))
        self.assertEqual(self.last_message(theirs), ["That request was not found."])
        self.assertEqual(self.last_message(theirs), self.last_message(missing))

    def test_the_sender_can_cancel_and_the_recipient_can_decline(self):
        first = services.send_request(self.sender, self.recipient.pk)
        self.client.force_login(self.sender)
        self.client.post(reverse("buddy:request_cancel", args=[first.pk]))
        first.refresh_from_db()
        self.assertEqual(first.status, BuddyRequest.Status.CANCELLED)

        second = services.send_request(self.sender, self.recipient.pk)
        self.client.force_login(self.recipient)
        self.client.post(reverse("buddy:request_decline", args=[second.pk]))
        second.refresh_from_db()
        self.assertEqual(second.status, BuddyRequest.Status.DECLINED)
