"""Tests for the Events screens (calendar, list, detail, create, edit, cancel).

ADAPT THE THREE BUILDERS at the top to apps/events/tests/helpers.py (the brick 10 builders):
make_extension, make_user and make_event. Everything below only uses those three.
"""
import datetime as dt

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.events.models import Event

from . import helpers


# ---- adapters: change only these three to match tests/helpers.py -------------
def make_extension(name):
    return helpers.make_extension(name)


def make_user(role, extension=None):
    return helpers.make_user(role=role, extension=extension)


def make_event(title="Service", extension=None, days=3, **extra):
    """An event `days` from now (negative = in the past)."""
    start = timezone.now() + dt.timedelta(days=days)
    return Event.objects.create(title=title, extension=extension, start_at=start, **extra)
# ------------------------------------------------------------------------------


def form_data(**override):
    """A valid create / edit POST body (Manila time, minute precision)."""
    start = (timezone.localtime() + dt.timedelta(days=10)).replace(second=0, microsecond=0)
    data = {
        "title": "Youth night", "location": "Main hall", "description": "",
        "start_at": start.strftime("%Y-%m-%dT%H:%M"),
        "end_at": (start + dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
    }
    data.update(override)
    return data


class EventsTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ext_a = make_extension("Alpha")
        cls.ext_b = make_extension("Bravo")
        cls.admin = make_user(Role.ADMIN)
        cls.coord_a = make_user(Role.COORDINATOR, cls.ext_a)
        cls.member_a = make_user(Role.MEMBER, cls.ext_a)
        cls.member_b = make_user(Role.MEMBER, cls.ext_b)
        cls.global_event = make_event("Global rally")
        cls.event_a = make_event("Alpha cell", cls.ext_a)
        cls.event_b = make_event("Bravo cell", cls.ext_b)


class AccessTests(EventsTestBase):
    def test_anonymous_is_sent_away(self):
        for name in ("events:index", "events:calendar", "events:list", "events:create"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertNotIn("events", response["Location"].split("?")[0].replace("/accounts/", ""), name)

    def test_member_list_shows_global_and_own_extension_only(self):
        self.client.force_login(self.member_a)
        html = self.client.get(reverse("events:list")).content.decode()
        self.assertIn("Global rally", html)
        self.assertIn("Alpha cell", html)
        self.assertNotIn("Bravo cell", html)

    def test_other_extensions_event_is_404(self):
        self.client.force_login(self.member_a)
        self.assertEqual(self.client.get(reverse("events:detail", args=[self.event_b.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("events:cancel", args=[self.event_b.pk])).status_code, 404)

    def test_admin_sees_everything(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("events:list")).content.decode()
        for title in ("Global rally", "Alpha cell", "Bravo cell"):
            self.assertIn(title, html)


class ManageControlsTests(EventsTestBase):
    def test_member_sees_no_manage_controls_and_cannot_open_forms(self):
        self.client.force_login(self.member_a)
        html = self.client.get(reverse("events:list")).content.decode()
        self.assertNotIn("New event", html)
        self.assertNotIn(reverse("events:edit", args=[self.event_a.pk]), html)
        self.assertEqual(self.client.get(reverse("events:create")).status_code, 302)
        self.assertEqual(self.client.get(reverse("events:edit", args=[self.event_a.pk])).status_code, 302)

    def test_coordinator_sees_edit_only_on_own_extension(self):
        self.client.force_login(self.coord_a)
        html = self.client.get(reverse("events:list")).content.decode()
        self.assertIn(reverse("events:edit", args=[self.event_a.pk]), html)
        self.assertNotIn(reverse("events:edit", args=[self.global_event.pk]), html)


class CreateEditCancelTests(EventsTestBase):
    def test_coordinator_creates_for_own_extension(self):
        self.client.force_login(self.coord_a)
        response = self.client.post(reverse("events:create"), form_data(extension=self.ext_a.pk))
        event = Event.objects.get(title="Youth night")
        self.assertRedirects(response, reverse("events:detail", args=[event.pk]))
        self.assertEqual(event.extension_id, self.ext_a.pk)

    def test_coordinator_cannot_pick_another_extension(self):
        self.client.force_login(self.coord_a)
        response = self.client.post(reverse("events:create"), form_data(extension=self.ext_b.pk))
        self.assertEqual(response.status_code, 200)            # form shown again with an error
        self.assertFalse(Event.objects.filter(title="Youth night").exists())

    def test_coordinator_cannot_create_a_global_event(self):
        self.client.force_login(self.coord_a)
        self.client.post(reverse("events:create"), form_data(extension=""))
        self.assertFalse(Event.objects.filter(title="Youth night").exists())

    def test_admin_creates_a_global_event(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("events:create"), form_data(extension=""))
        self.assertIsNone(Event.objects.get(title="Youth night").extension_id)

    def test_end_before_start_is_an_inline_error(self):
        self.client.force_login(self.admin)
        data = form_data(extension="")
        data["end_at"] = data["start_at"]
        response = self.client.post(reverse("events:create"), data)
        self.assertContains(response, "The end must be after the start.")
        self.assertFalse(Event.objects.filter(title="Youth night").exists())

    def test_clicked_day_prefills_the_start(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("events:create") + "?date=2026-12-25")
        self.assertContains(response, "2026-12-25T09:00")

    def test_bad_date_parameter_is_ignored(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("events:create") + "?date=nope").status_code, 200)

    def test_edit_saves_changes(self):
        self.client.force_login(self.coord_a)
        data = form_data(title="Alpha cell (renamed)")
        self.client.post(reverse("events:edit", args=[self.event_a.pk]), data)
        self.event_a.refresh_from_db()
        self.assertEqual(self.event_a.title, "Alpha cell (renamed)")

    def test_cancel_marks_the_event_and_keeps_it(self):
        self.client.force_login(self.coord_a)
        self.client.post(reverse("events:cancel", args=[self.event_a.pk]))
        self.event_a.refresh_from_db()
        self.assertTrue(self.event_a.is_cancelled)

    def test_member_cannot_cancel(self):
        self.client.force_login(self.member_a)
        self.client.post(reverse("events:cancel", args=[self.event_a.pk]))
        self.event_a.refresh_from_db()
        self.assertFalse(self.event_a.is_cancelled)

    def test_cancel_get_is_not_allowed(self):
        self.client.force_login(self.coord_a)
        self.assertEqual(self.client.get(reverse("events:cancel", args=[self.event_a.pk])).status_code, 405)

    def test_cancelled_event_cannot_be_edited(self):
        self.client.force_login(self.coord_a)
        self.client.post(reverse("events:cancel", args=[self.event_a.pk]))
        response = self.client.get(reverse("events:edit", args=[self.event_a.pk]))
        self.assertRedirects(response, reverse("events:detail", args=[self.event_a.pk]))

    def test_next_parameter_must_stay_on_this_site(self):
        self.client.force_login(self.coord_a)
        response = self.client.post(
            reverse("events:cancel", args=[self.event_a.pk]), {"next": "https://evil.example/"},
        )
        self.assertRedirects(response, reverse("events:detail", args=[self.event_a.pk]))


class ListTests(EventsTestBase):
    def test_past_tab_holds_only_finished_events(self):
        make_event("Last month", days=-30)
        self.client.force_login(self.admin)
        upcoming = self.client.get(reverse("events:list")).content.decode()
        past = self.client.get(reverse("events:list") + "?when=past").content.decode()
        self.assertNotIn("Last month", upcoming)
        self.assertIn("Last month", past)
        self.assertNotIn("Global rally", past)

    def test_cancelled_event_is_marked_in_the_list(self):
        make_event("Called off", cancelled_at=timezone.now())
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("events:list")), "Cancelled")

    def test_global_filter(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("events:list") + "?scope=global").content.decode()
        self.assertIn("Global rally", html)
        self.assertNotIn("Alpha cell", html)

    def test_my_extension_filter(self):
        self.client.force_login(self.member_a)
        html = self.client.get(reverse("events:list") + "?scope=mine").content.decode()
        self.assertIn("Alpha cell", html)
        self.assertNotIn("Global rally", html)

    def test_admin_extension_filter(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("events:list") + f"?extension={self.ext_b.pk}").content.decode()
        self.assertIn("Bravo cell", html)
        self.assertNotIn("Alpha cell", html)

    def test_extension_filter_is_ignored_for_non_admins(self):
        self.client.force_login(self.member_a)
        html = self.client.get(reverse("events:list") + f"?extension={self.ext_b.pk}").content.decode()
        self.assertNotIn("Bravo cell", html)
        self.assertIn("Global rally", html)

    def test_junk_filters_do_not_crash(self):
        self.client.force_login(self.admin)
        for query in ("?scope=zzz", "?extension=abc", "?extension=99999999999999", "?page=banana", "?when=zzz"):
            self.assertEqual(self.client.get(reverse("events:list") + query).status_code, 200, query)


class CalendarTests(EventsTestBase):
    def test_calendar_shows_chips_for_visible_events(self):
        self.client.force_login(self.member_a)
        html = self.client.get(reverse("events:calendar")).content.decode()
        self.assertNotIn("Bravo cell", html)

    def test_junk_month_falls_back_to_today(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("events:calendar") + "?month=junk").status_code, 200)

    def test_last_view_is_remembered_per_device(self):
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse("events:index")), reverse("events:calendar"), fetch_redirect_response=False)
        self.client.get(reverse("events:list"))                       # sets the cookie
        self.assertRedirects(self.client.get(reverse("events:index")), reverse("events:list"), fetch_redirect_response=False)
        self.client.get(reverse("events:calendar"))
        self.assertRedirects(self.client.get(reverse("events:index")), reverse("events:calendar"), fetch_redirect_response=False)
