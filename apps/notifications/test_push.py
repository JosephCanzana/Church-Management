"""notifications.test_push: Web Push (Brick 12).

Kept as a flat module (like `apps.core.test_text`) because `notifications/tests.py` already exists:
a `tests/` package beside it would hide it. Run with `python manage.py test apps.notifications.test_push`.

Nothing here calls a real push service: `push._send_one` is replaced, and PUSH_ASYNC is off so
delivery happens inside `captureOnCommitCallbacks`.
Users come from `make_user()` (the shared builder in apps/bible/tests/helpers.py, which makes an ACTIVATED
person); change that one import if your builder lives elsewhere.
"""
import base64
import json
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.bible.tests.helpers import make_user
from apps.core.services import ServiceError

from . import push, services
from .models import Notification, NotificationPreference, PushSubscription

VAPID = dict(
    VAPID_PUBLIC_KEY="BPublicKeyForTests", VAPID_PRIVATE_KEY="privateKeyForTests",
    VAPID_SUBJECT="mailto:test@example.com", PUSH_ASYNC=False,
)
ENDPOINT = "https://fcm.googleapis.com/fcm/send/device-one"
ENDPOINT_2 = "https://updates.push.services.mozilla.com/wpush/v2/device-two"
KEYS = {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA", "auth": "tBHItJI5svbpez7KI4CCXg"}


def register(user, endpoint=ENDPOINT, agent="Mozilla/5.0 (Linux; Android 14) Chrome/126.0 Mobile Safari/537.36"):
    """Register a device through the service, the way the view does."""
    return services.register_device(user, endpoint=endpoint, user_agent=agent, **KEYS)


class HelperTests(SimpleTestCase):
    """Pure helpers in push.py."""

    def test_device_labels(self):
        cases = {
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit Chrome/126.0 Mobile Safari/537.36": "Chrome on Android",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit Version/17.5 Mobile Safari/604.1": "Safari on iPhone",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit Chrome/126.0 Safari/537.36 Edg/126.0": "Edge on Windows",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0": "Firefox on macOS",
            "": "Browser on unknown system",
        }
        for agent, label in cases.items():
            self.assertEqual(push.device_label(agent), label)

    @override_settings(PUSH_ENDPOINT_HOSTS=("fcm.googleapis.com", ".push.apple.com"))
    def test_endpoint_allow_list(self):
        self.assertTrue(push.endpoint_allowed("https://fcm.googleapis.com/fcm/send/abc"))
        self.assertTrue(push.endpoint_allowed("https://web.push.apple.com/QWxs"))
        for bad in (
            "http://fcm.googleapis.com/fcm/send/abc",           # not https
            "https://localhost/x", "https://127.0.0.1/x", "https://10.0.0.5/x",
            "https://evil.example.com/fcm.googleapis.com",       # host is evil.example.com
            "https://fcm.googleapis.com.evil.example/x",
            "https://user:pw@fcm.googleapis.com/x",
            "https://fcm.googleapis.com:8443/x",
            "not a url", "",
        ):
            self.assertFalse(push.endpoint_allowed(bad), bad)

    def test_clean_message_keeps_links_on_this_site(self):
        self.assertEqual(push.clean_message({"title": "Hi", "url": "/events/3/"})["url"], "/events/3/")
        for bad in ("https://evil.example/x", "//evil.example/x", "javascript:alert(1)", "/\\evil.example", ""):
            self.assertEqual(push.clean_message({"title": "Hi", "url": bad})["url"], push.DEFAULT_URL)

    def test_clean_message_shortens_and_flattens(self):
        msg = push.clean_message({"title": "T" * 500, "body": "line one\n\nline   two " + "x" * 500})
        self.assertLessEqual(len(msg["title"]), push.TITLE_MAX)
        self.assertLessEqual(len(msg["body"]), push.BODY_MAX)
        self.assertNotIn("\n", msg["body"])
        self.assertEqual(push.clean_message({})["title"], "Church Management")

    def test_hash_is_stable_sha256(self):
        self.assertEqual(len(push.hash_endpoint(ENDPOINT)), 64)
        self.assertEqual(push.hash_endpoint(ENDPOINT), push.hash_endpoint(ENDPOINT))


class VapidCommandTests(SimpleTestCase):
    """generate_vapid_keys prints keys in the sizes browsers and pywebpush expect."""

    def test_output(self):
        out = StringIO()
        call_command("generate_vapid_keys", stdout=out, stderr=StringIO())
        lines = dict(line.split("=", 1) for line in out.getvalue().splitlines() if "=" in line)
        pad = lambda s: s + "=" * (-len(s) % 4)
        self.assertEqual(len(base64.urlsafe_b64decode(pad(lines["VAPID_PUBLIC_KEY"]))), 65)
        self.assertEqual(len(base64.urlsafe_b64decode(pad(lines["VAPID_PRIVATE_KEY"]))), 32)


@override_settings(**VAPID)
class DeviceServiceTests(TestCase):
    """register / unregister / remove: private to the owner, limited, audited without secrets."""

    def setUp(self):
        self.ana = make_user()
        self.ben = make_user()

    def test_register_creates_a_labelled_device(self):
        sub = register(self.ana)
        self.assertEqual(sub.user, self.ana)
        self.assertEqual(sub.label, "Chrome on Android")
        self.assertEqual(sub.endpoint_hash, push.hash_endpoint(ENDPOINT))

    def test_registering_again_updates_instead_of_duplicating(self):
        register(self.ana)
        register(self.ana)
        self.assertEqual(PushSubscription.objects.filter(user=self.ana).count(), 1)

    def test_same_browser_signed_in_as_someone_else_moves_the_device(self):
        register(self.ana)
        register(self.ben)
        self.assertEqual(PushSubscription.objects.get().user, self.ben)

    def test_unknown_push_service_is_refused(self):
        with self.assertRaises(ServiceError):
            register(self.ana, endpoint="https://169.254.169.254/latest/meta-data")
        self.assertFalse(PushSubscription.objects.exists())

    def test_bad_keys_are_refused(self):
        with self.assertRaises(ServiceError):
            services.register_device(self.ana, endpoint=ENDPOINT, p256dh="x", auth="y")

    def test_limit_per_person(self):
        for i in range(services.MAX_DEVICES):
            register(self.ana, endpoint=f"https://fcm.googleapis.com/fcm/send/dev-{i}")
        with self.assertRaises(ServiceError):
            register(self.ana, endpoint="https://fcm.googleapis.com/fcm/send/one-too-many")

    @override_settings(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY="", VAPID_SUBJECT="")
    def test_refused_when_push_is_not_set_up(self):
        with self.assertRaises(ServiceError):
            register(self.ana)

    def test_unregister_removes_only_own_device(self):
        register(self.ana)
        self.assertFalse(services.unregister_device(self.ben, ENDPOINT))
        self.assertTrue(PushSubscription.objects.exists())
        self.assertTrue(services.unregister_device(self.ana, ENDPOINT))
        self.assertFalse(PushSubscription.objects.exists())

    def test_remove_device_refuses_someone_elses(self):
        sub = register(self.ana)
        with self.assertRaises(ServiceError):
            services.remove_device(self.ben, sub.pk)
        self.assertTrue(PushSubscription.objects.filter(pk=sub.pk).exists())
        services.remove_device(self.ana, sub.pk)
        self.assertFalse(PushSubscription.objects.filter(pk=sub.pk).exists())

    def test_is_registered_is_per_person(self):
        register(self.ana)
        self.assertTrue(services.is_registered(self.ana, ENDPOINT))
        self.assertFalse(services.is_registered(self.ben, ENDPOINT))

    def test_audit_never_gets_the_endpoint_or_keys(self):
        """log_action is replaced so we can see exactly what would be written."""
        with mock.patch.object(services, "log_action") as log:
            register(self.ana)
        sent = repr(log.call_args)
        for secret in (ENDPOINT, KEYS["p256dh"], KEYS["auth"]):
            self.assertNotIn(secret, sent)


@override_settings(**VAPID)
class DeliveryTests(TestCase):
    """send_to_users: counts, dead devices removed, failures kept, nothing raised."""

    def setUp(self):
        self.ana = make_user()
        self.msg = {"title": "Hello", "body": "World", "url": "/events/1/"}

    def test_sent_updates_last_success(self):
        sub = register(self.ana)
        with mock.patch.object(push, "_send_one", return_value="sent"):
            result = push.send_to_users([self.ana.pk], self.msg)
        self.assertEqual((result["devices"], result["sent"]), (1, 1))
        sub.refresh_from_db()
        self.assertIsNotNone(sub.last_success_at)

    def test_404_or_410_removes_the_device(self):
        register(self.ana)
        with mock.patch.object(push, "_send_one", return_value="gone"):
            result = push.send_to_users([self.ana.pk], self.msg)
        self.assertEqual(result["removed"], 1)
        self.assertFalse(PushSubscription.objects.exists())

    def test_other_failures_keep_the_device(self):
        register(self.ana)
        with mock.patch.object(push, "_send_one", return_value="failed"):
            result = push.send_to_users([self.ana.pk], self.msg)
        self.assertEqual((result["failed"], result["removed"]), (1, 0))
        self.assertTrue(PushSubscription.objects.exists())

    def test_payload_has_only_title_body_url_tag(self):
        register(self.ana)
        with mock.patch.object(push, "_send_one", return_value="sent") as send:
            push.send_to_users([self.ana.pk], {**self.msg, "secret": "never", "amount": "500"})
        payload = json.loads(send.call_args.args[1])
        self.assertEqual(set(payload), {"title", "body", "url", "tag"})

    @override_settings(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY="", VAPID_SUBJECT="")
    def test_nothing_happens_without_keys(self):
        with mock.patch.object(push, "_send_one") as send:
            result = push.send_to_users([self.ana.pk], self.msg)
        send.assert_not_called()
        self.assertEqual(result["devices"], 0)

    def test_send_one_treats_410_as_gone_and_hides_the_endpoint_in_logs(self):
        sub = register(self.ana)

        class FakeError(Exception):
            response = mock.Mock(status_code=410)

        fake_module = mock.Mock(WebPushException=FakeError, webpush=mock.Mock(side_effect=FakeError("boom")))
        with mock.patch.dict("sys.modules", {"pywebpush": fake_module}):
            self.assertEqual(push._send_one(sub, "{}"), "gone")
            FakeError.response = mock.Mock(status_code=500)
            with self.assertLogs("apps.notifications.push", level="WARNING") as logs:
                self.assertEqual(push._send_one(sub, "{}"), "failed")
        self.assertNotIn(ENDPOINT, "\n".join(logs.output))


@override_settings(**VAPID)
class NotifyPushTests(TestCase):
    """notify() and notify_many() with `push=`: bell first, push after commit, never blocking."""

    def setUp(self):
        self.ana = make_user()
        self.ben = make_user()
        self.msg = {"title": "Event moved", "body": "New time", "url": "/events/9/"}

    def test_push_goes_after_commit_to_the_recipient(self):
        with mock.patch.object(push, "send_to_users") as send:
            with self.captureOnCommitCallbacks(execute=True):
                row = services.notify(self.ana, "event_changed", push=self.msg)
        self.assertIsNotNone(row)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], [self.ana.pk])

    def test_no_push_argument_means_no_push(self):
        with mock.patch.object(push, "send_to_users") as send:
            with self.captureOnCommitCallbacks(execute=True):
                services.notify(self.ana, "event_changed")
        send.assert_not_called()

    def test_muted_type_gets_no_bell_and_no_push(self):
        NotificationPreference.objects.create(user=self.ana, type="event_changed", in_app=False)
        with mock.patch.object(push, "send_to_users") as send:
            with self.captureOnCommitCallbacks(execute=True):
                row = services.notify(self.ana, "event_changed", push=self.msg)
        self.assertIsNone(row)
        send.assert_not_called()

    def test_duplicate_notice_does_not_push_twice(self):
        with mock.patch.object(push, "send_to_users") as send:
            with self.captureOnCommitCallbacks(execute=True):
                services.notify(self.ana, "event_changed", dedupe_key="k1", push=self.msg)
                services.notify(self.ana, "event_changed", dedupe_key="k1", push=self.msg)
        self.assertEqual(send.call_count, 1)

    def test_a_push_failure_never_undoes_the_bell_item(self):
        with mock.patch.object(push, "send_to_users", side_effect=RuntimeError("push service down")):
            with self.captureOnCommitCallbacks(execute=True):
                services.notify(self.ana, "event_changed", push=self.msg)
        self.assertTrue(Notification.objects.filter(user=self.ana, type="event_changed").exists())

    def test_notify_many_pushes_only_to_people_who_got_a_bell_item(self):
        NotificationPreference.objects.create(user=self.ben, type="event_changed", in_app=False)
        with mock.patch.object(push, "send_to_users") as send:
            with self.captureOnCommitCallbacks(execute=True):
                created = services.notify_many([self.ana.pk, self.ben.pk], "event_changed", push=self.msg)
        self.assertEqual(created, 1)
        self.assertEqual(send.call_args.args[0], [self.ana.pk])

    @override_settings(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY="", VAPID_SUBJECT="")
    def test_without_keys_the_bell_still_works(self):
        with mock.patch.object(push, "send_to_users") as send:
            with self.captureOnCommitCallbacks(execute=True):
                row = services.notify(self.ana, "event_changed", push=self.msg)
        self.assertIsNotNone(row)
        send.assert_not_called()


@override_settings(**VAPID)
class ViewTests(TestCase):
    """The JSON endpoints, the remove form and /sw.js."""

    def setUp(self):
        self.ana = make_user()
        self.ben = make_user()
        self.client.force_login(self.ana)

    def post_json(self, name, body, **kwargs):
        return self.client.post(reverse(name, **kwargs), data=json.dumps(body), content_type="application/json")

    def test_subscribe_then_status_then_unsubscribe(self):
        res = self.post_json("push:subscribe", {"endpoint": ENDPOINT, "keys": KEYS})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.assertTrue(self.post_json("push:status", {"endpoint": ENDPOINT}).json()["registered"])
        self.assertTrue(self.post_json("push:unsubscribe", {"endpoint": ENDPOINT}).json()["ok"])
        self.assertFalse(self.post_json("push:status", {"endpoint": ENDPOINT}).json()["registered"])

    def test_bad_json_and_bad_endpoint_are_plain_400s(self):
        res = self.client.post(reverse("push:subscribe"), data="not json", content_type="application/json")
        self.assertEqual(res.status_code, 400)
        res = self.post_json("push:subscribe", {"endpoint": "https://evil.example/x", "keys": KEYS})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["ok"])

    def test_signed_out_people_are_sent_to_login(self):
        self.client.logout()
        res = self.post_json("push:subscribe", {"endpoint": ENDPOINT, "keys": KEYS})
        self.assertEqual(res.status_code, 302)
        self.assertFalse(PushSubscription.objects.exists())

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("push:subscribe")).status_code, 405)

    def test_test_button_reports_how_many_were_reached(self):
        register(self.ana)
        register(self.ana, endpoint=ENDPOINT_2)
        with mock.patch.object(push, "_send_one", side_effect=["sent", "failed"]):
            res = self.post_json("push:test", {})
        body = res.json()
        self.assertEqual((body["reached"], body["total"]), (1, 2))
        self.assertIn("1 of 2", body["message"])

    def test_test_button_with_no_device_says_so(self):
        res = self.post_json("push:test", {})
        self.assertEqual(res.status_code, 400)

    def test_test_button_has_a_cooldown(self):
        register(self.ana)
        with mock.patch.object(push, "_send_one", return_value="sent"):
            self.assertEqual(self.post_json("push:test", {}).status_code, 200)
            self.assertEqual(self.post_json("push:test", {}).status_code, 429)

    def test_remove_form_removes_own_device_and_only_own(self):
        mine = register(self.ana)
        theirs = register(self.ben, endpoint=ENDPOINT_2)
        self.client.post(reverse("push:remove_device", args=[theirs.pk]))
        self.assertTrue(PushSubscription.objects.filter(pk=theirs.pk).exists())
        res = self.client.post(reverse("push:remove_device", args=[mine.pk]), {"next": "https://evil.example/"})
        self.assertFalse(PushSubscription.objects.filter(pk=mine.pk).exists())
        self.assertNotIn("evil.example", res["Location"])

    def test_service_worker_is_served_from_the_root_with_site_scope(self):
        self.client.logout()
        res = self.client.get("/sw.js")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Service-Worker-Allowed"], "/")
        self.assertIn("javascript", res["Content-Type"])
        self.assertIn("no-cache", res["Cache-Control"])
