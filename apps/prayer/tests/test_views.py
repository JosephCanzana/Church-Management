"""Prayer screens: who sees what, anonymous masking, and that a missing and an out-of-reach request look the same."""
from django.test import TestCase
from django.urls import reverse

from apps.core.services import new_submission_token
from apps.prayer import services
from apps.prayer.models import PrayerRequest

from .helpers import make_extension, make_user

PASSWORD = "pass-12345-x"


def _send(member, body, anonymous=False):
    """Create a request the way the app does."""
    return services.send_prayer_request(member, body, anonymous, new_submission_token()).request


class MemberViewTests(TestCase):
    """/prayer/."""

    def setUp(self):
        self.ext = make_extension()
        self.me = make_user("member", self.ext)
        self.other = make_user("member", self.ext)
        _send(self.me, "my own request text")
        _send(self.other, "somebody else's request text")

    def test_login_required(self):
        response = self.client.get(reverse("prayer:list"))
        self.assertEqual(response.status_code, 302)

    def test_member_sees_only_their_own_requests(self):
        self.client.force_login(self.me)
        html = self.client.get(reverse("prayer:list")).content.decode()
        self.assertIn("my own request text", html)
        self.assertNotIn("somebody else's request text", html)

    def test_post_sends_and_repeated_token_sends_once(self):
        self.client.force_login(self.me)
        token = new_submission_token()
        data = {"body": "a fresh request", "submit_token": token}
        self.client.post(reverse("prayer:send"), data)
        self.client.post(reverse("prayer:send"), data)
        self.assertEqual(PrayerRequest.objects.filter(user=self.me, body="a fresh request").count(), 1)

    def test_leaders_cannot_use_the_member_screen(self):
        for role in ("coordinator", "admin", "super_admin"):
            self.client.force_login(make_user(role, self.ext if role == "coordinator" else None))
            self.assertEqual(self.client.get(reverse("prayer:list")).status_code, 302)
            self.assertEqual(
                self.client.post(reverse("prayer:send"), {"body": "x", "submit_token": new_submission_token()}).status_code, 302,
            )
        self.assertFalse(PrayerRequest.objects.filter(body="x").exists())


class InboxViewTests(TestCase):
    """The shared inbox in the three role areas."""

    def setUp(self):
        self.ext = make_extension()
        self.lonely = make_extension()
        self.member = make_user("member", self.ext, last_name="Zebediah")
        self.lonely_member = make_user("member", self.lonely, last_name="Lonelyname")
        self.coordinator = make_user("coordinator", self.ext)
        self.other_coordinator = make_user("coordinator", make_extension())
        self.admin = make_user("admin")
        self.superadmin = make_user("super_admin")
        self.req = _send(self.member, "request in extension with coordinator")
        self.anon = _send(self.member, "anonymous request text", anonymous=True)
        self.lonely_req = _send(self.lonely_member, "request in extension with no coordinator")

    def inbox(self, user, area):
        self.client.force_login(user)
        return self.client.get(reverse(f"{area}:prayer_inbox")).content.decode()

    def test_coordinator_sees_only_their_extension(self):
        html = self.inbox(self.coordinator, "coordinator_area")
        self.assertIn("request in extension with coordinator", html)
        self.assertNotIn("with no coordinator", html)
        self.assertNotIn("with no coordinator", self.inbox(self.other_coordinator, "coordinator_area"))
        self.assertNotIn("request in extension with coordinator", self.inbox(self.other_coordinator, "coordinator_area"))

    def test_admin_and_superadmin_see_only_requests_without_a_coordinator(self):
        for user, area in ((self.admin, "admin_area"), (self.superadmin, "superadmin")):
            html = self.inbox(user, area)
            self.assertIn("request in extension with no coordinator", html)
            self.assertNotIn("request in extension with coordinator", html)

    def test_missing_and_out_of_reach_requests_look_the_same(self):
        self.client.force_login(self.other_coordinator)
        out_of_reach = self.client.get(reverse("coordinator_area:prayer_detail", args=[self.req.pk]), follow=True)
        missing = self.client.get(reverse("coordinator_area:prayer_detail", args=[999999]), follow=True)
        self.assertEqual(out_of_reach.redirect_chain, missing.redirect_chain)
        self.assertContains(out_of_reach, "no longer exists")
        self.assertNotContains(out_of_reach, "request in extension with coordinator")

    def test_anonymous_request_hides_the_sender_from_everyone(self):
        self.client.force_login(self.coordinator)
        inbox = self.client.get(reverse("coordinator_area:prayer_inbox")).content.decode()
        detail = self.client.get(reverse("coordinator_area:prayer_detail", args=[self.anon.pk])).content.decode()
        self.assertIn("Anonymous", inbox)
        self.assertIn("Anonymous", detail)
        self.assertEqual(detail.count("Zebediah"), 0)
        # The same request that is NOT anonymous shows the name.
        shown = self.client.get(reverse("coordinator_area:prayer_detail", args=[self.req.pk])).content.decode()
        self.assertIn("Zebediah", shown)

    def test_sender_sees_you_anonymous_on_their_own_list(self):
        self.client.force_login(self.member)
        html = self.client.get(reverse("prayer:list")).content.decode()
        self.assertIn("You (anonymous)", html)

    def test_reply_and_done_through_the_screens(self):
        self.client.force_login(self.coordinator)
        self.client.post(reverse("coordinator_area:prayer_reply", args=[self.req.pk]), {"body": "praying for you"})
        self.client.post(reverse("coordinator_area:prayer_complete", args=[self.req.pk]))
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, "completed")
        self.assertEqual(self.req.replies.count(), 1)

    def test_members_and_other_areas_are_turned_away(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("coordinator_area:prayer_inbox")).status_code, 302)
        self.client.force_login(self.coordinator)
        self.assertEqual(self.client.get(reverse("admin_area:prayer_inbox")).status_code, 302)

    def test_status_tabs_filter(self):
        self.client.force_login(self.coordinator)
        services.complete_request(self.coordinator, self.req.pk)
        url = reverse("coordinator_area:prayer_inbox")
        self.assertNotIn("request in extension with coordinator", self.client.get(url).content.decode())
        self.assertIn("request in extension with coordinator", self.client.get(url, {"status": "completed"}).content.decode())
