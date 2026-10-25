"""Tests for the role dashboards (apps/dashboards)."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Extension, Role, Status, User
from apps.attendance.models import Attendance
from apps.bible.models import BibleBookmark, BibleChapterRead, BibleVerse, DailyVerse
from apps.dashboards import services
from apps.tithes.models import TithesOffering


def make_person(role, extension=None, status=Status.ACTIVE, **extra):
    """Create an activated person (the activation middleware redirects everyone else)."""
    return User.objects.create_user(
        password="pass-12345", first_name="test", last_name=role.replace("_", ""), role=role,
        extension=extension, status=status, must_change_password=False, **extra,
    )


def add_sheet(extension, day, present, first_timers, amount=None, status="uploaded"):
    """Create an attendance sheet and, if ``amount`` is given, its tithes record with the same status."""
    sheet = Attendance.objects.create(
        extension=extension, attendance_date=day, status=status,
        total_present=present if status == "uploaded" else None,
        first_timers_count=first_timers if status == "uploaded" else None,
    )
    if amount is not None:
        TithesOffering.objects.create(
            attendance=sheet,                 # keep whatever your helper passes here
            status="finalized",
            offering_amount=Decimal("0"),
            tithes_total=Decimal(amount),     # was total_amount; the bar now reads tithes_total
            total_amount=Decimal(amount),
            breakdown_total=Decimal(amount),
            difference=Decimal("0"),
            tithers_count=0,
            finalized_at=timezone.now(),
        )
    return sheet


class AccessTests(TestCase):
    """Each role gets its own dashboard; other roles are sent home."""

    def setUp(self):
        self.alpha = Extension.objects.create(name="alpha")
        self.people = {
            Role.SUPER_ADMIN: make_person(Role.SUPER_ADMIN),
            Role.ADMIN: make_person(Role.ADMIN),
            Role.COORDINATOR: make_person(Role.COORDINATOR, self.alpha),
            Role.MEMBER: make_person(Role.MEMBER, self.alpha),
        }
        self.urls = {
            Role.SUPER_ADMIN: reverse("dashboards:superadmin"),
            Role.ADMIN: reverse("dashboards:admin"),
            Role.COORDINATOR: reverse("dashboards:coordinator"),
            Role.MEMBER: reverse("dashboards:member"),
        }

    def test_anonymous_is_sent_to_login(self):
        """No session, no dashboard."""
        for url in self.urls.values():
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_each_role_sees_its_own_dashboard(self):
        """Own page: 200. Members see the personal sections on their home; leaders see them on the Personal page."""
        for role, person in self.people.items():
            self.client.force_login(person)
            response = self.client.get(self.urls[role])
            self.assertEqual(response.status_code, 200, role)
            if role == Role.MEMBER:
                self.assertContains(response, "Verse of the day")
                self.assertContains(response, "Continue reading")
            else:
                # Brick 3 moved the personal cards off the leader overview (they are on the Personal tab).
                self.assertNotContains(response, "verse-heading", msg_prefix=str(role))
                personal = self.client.get(reverse("dashboards:personal"))
                self.assertContains(personal, "Verse of the day")
                self.assertContains(personal, "Continue reading")

    def test_other_roles_are_redirected_to_their_own_home(self):
        """A member opening the super-admin page lands on the member page."""
        self.client.force_login(self.people[Role.MEMBER])
        response = self.client.get(self.urls[Role.SUPER_ADMIN])
        self.assertRedirects(response, self.urls[Role.MEMBER], fetch_redirect_response=False)

    def test_only_leaders_get_the_overview(self):
        """Members never see counts or charts."""
        self.client.force_login(self.people[Role.MEMBER])
        self.assertNotContains(self.client.get(self.urls[Role.MEMBER]), "Overview")
        self.client.force_login(self.people[Role.ADMIN])
        self.assertContains(self.client.get(self.urls[Role.ADMIN]), "Overview")


class VerseOnDashboardTests(TestCase):
    """The verse follows the reader's version and never swaps silently."""

    def setUp(self):
        self.alpha = Extension.objects.create(name="alpha")
        self.member = make_person(Role.MEMBER, self.alpha)
        self.client.force_login(self.member)
        DailyVerse.objects.create(
            verse_date=timezone.localdate(), translation="KJV", book="John", chapter=3, verse_start=16,
        )

    def test_default_version_text_is_shown(self):
        """No cookie: the default (KJV) text and its heading."""
        BibleVerse.objects.create(translation="KJV", book="John", chapter=3, verse=16, text="kjv sixteen")
        response = self.client.get(reverse("dashboards:member"))
        self.assertContains(response, "kjv sixteen")
        self.assertContains(response, "John 3:16 (KJV)")

    def test_cookie_version_is_used(self):
        """The reader's cookie picks the version."""
        BibleVerse.objects.create(translation="KJV", book="John", chapter=3, verse=16, text="kjv sixteen")
        BibleVerse.objects.create(translation="ADB", book="John", chapter=3, verse=16, text="adb labing-anim")
        self.client.cookies["bible_version"] = "ADB"
        response = self.client.get(reverse("dashboards:member"))
        self.assertContains(response, "adb labing-anim")
        self.assertNotContains(response, "kjv sixteen")

    def test_version_without_text_shows_a_notice(self):
        """A version that is not loaded gives a notice and the reference, not a swap."""
        BibleVerse.objects.create(translation="KJV", book="John", chapter=3, verse=16, text="kjv sixteen")
        self.client.cookies["bible_version"] = "ADB"
        response = self.client.get(reverse("dashboards:member"))
        self.assertContains(response, "has not been loaded yet")
        self.assertNotContains(response, "kjv sixteen")

    def test_esv_shows_notice_and_link_and_is_not_stored(self):
        """ESV text carries (ESV), the copyright notice and the esv.org link, and is never saved."""
        self.client.cookies["bible_version"] = "ESV"
        with patch("apps.bible.esv.get_verses", return_value=[(16, "esv placeholder text")]):
            response = self.client.get(reverse("dashboards:member"))
        self.assertContains(response, "esv placeholder text")
        self.assertContains(response, "John 3:16 (ESV)")
        self.assertContains(response, "Crossway")
        self.assertContains(response, "www.esv.org")
        self.assertEqual(DailyVerse.objects.get(verse_date=timezone.localdate()).verse_text, "")


class ContinueReadingTests(TestCase):
    """The 'continue reading' card."""

    def setUp(self):
        self.alpha = Extension.objects.create(name="alpha")
        self.member = make_person(Role.MEMBER, self.alpha)

    def test_nothing_read_yet(self):
        """No reads: the card is empty (the template invites them to start)."""
        context = services.get_dashboard_context(self.member, None, "/")
        self.assertIsNone(context["continue_reading"])

    def test_bookmark_is_the_card(self):
        """Brick 2: the card shows the bookmark, not the newest read (a read alone changes nothing)."""
        today = timezone.localdate()
        BibleChapterRead.objects.create(user=self.member, translation="KJV", book="Gen", chapter=50, read_date=today)
        self.assertIsNone(services.get_dashboard_context(self.member, None, "/")["continue_reading"])
        BibleBookmark.objects.create(user=self.member, book="Exod", chapter=1)
        card = services.get_dashboard_context(self.member, None, "/")["continue_reading"]
        self.assertEqual(card["label"], "Exodus 1")


class LeaderNumbersTests(TestCase):
    """Counts and trends: scoped, frozen, and only from uploaded sheets."""

    def setUp(self):
        self.alpha = Extension.objects.create(name="alpha")
        self.beta = Extension.objects.create(name="beta")
        self.super_admin = make_person(Role.SUPER_ADMIN)
        self.admin = make_person(Role.ADMIN)
        self.coordinator = make_person(Role.COORDINATOR, self.alpha)
        make_person(Role.MEMBER, self.alpha)
        make_person(Role.MEMBER, self.beta)
        make_person(Role.MEMBER, self.beta, status=Status.ARCHIVED, archived_at=timezone.now())
        self.day1, self.day2 = date(2026, 10, 4), date(2026, 10, 11)
        add_sheet(self.alpha, self.day1, present=10, first_timers=2, amount="150.00")
        add_sheet(self.beta, self.day2, present=15, first_timers=1, amount="1500.50")
        add_sheet(self.alpha, date(2026, 10, 18), present=99, first_timers=9, amount=None, status="draft")

    def overview(self, person):
        """Return the leader part of the context for a person."""
        return services.get_dashboard_context(person, None, "/")["leader"]

    def test_members_get_no_overview(self):
        """Members have nothing under 'leader'."""
        member = User.objects.filter(role=Role.MEMBER, extension=self.alpha).first()
        self.assertIsNone(services.get_dashboard_context(member, None, "/")["leader"])

    def test_coordinator_sees_only_their_extension(self):
        """Attendance and tithes are limited to the coordinator's extension."""
        leader = self.overview(self.coordinator)
        self.assertEqual([bar.display for bar in leader["attendance_bars"]], ["10"])
        self.assertEqual([bar.display for bar in leader["tithe_bars"]], ["₱150.00"])
        self.assertFalse(leader["show_extensions"])
        self.assertEqual(leader["scope_label"], "Alpha")

    def test_coordinator_people_counts_stay_in_their_extension(self):
        """Only the coordinator and the one member of 'alpha' are counted."""
        leader = self.overview(self.coordinator)
        roles = {bar.label: bar.display for bar in leader["role_bars"]}
        self.assertEqual(roles, {"Coordinator": "1", "Member": "1"})

    def test_admin_sees_every_extension_oldest_first(self):
        """Admins get all uploaded sheets, ordered by date, drafts excluded."""
        leader = self.overview(self.admin)
        self.assertEqual([bar.label for bar in leader["attendance_bars"]], ["Oct 4", "Oct 11"])
        self.assertEqual([bar.display for bar in leader["attendance_bars"]], ["10", "15"])
        self.assertEqual([bar.display for bar in leader["first_timer_bars"]], ["2", "1"])
        self.assertEqual([bar.display for bar in leader["tithe_bars"]], ["₱150.00", "₱1,500.50"])

    def test_archived_people_are_not_in_the_roster_but_are_in_status(self):
        """Role bars skip archived people; the status chart still shows them."""
        leader = self.overview(self.admin)
        members = {bar.label: bar.display for bar in leader["role_bars"]}["Member"]
        self.assertEqual(members, "2")
        statuses = {bar.label: bar.display for bar in leader["status_bars"]}
        self.assertEqual(statuses["Archived"], "1")

    def test_only_the_super_admin_sees_super_admin_rows(self):
        """The admin's roster has no super-admin bar; the super-admin's does."""
        self.assertNotIn("Super admin", [bar.label for bar in self.overview(self.admin)["role_bars"]])
        self.assertIn("Super admin", [bar.label for bar in self.overview(self.super_admin)["role_bars"]])

    def test_extension_chart_counts_people_who_are_not_archived(self):
        """beta has one active member and one archived: it shows 1."""
        bars = {bar.label: bar.display for bar in self.overview(self.admin)["extension_bars"]}
        self.assertEqual(bars["Beta"], "1")
        self.assertEqual(bars["Alpha"], "2")

    def test_bars_are_scaled_and_never_invisible(self):
        """The biggest value is 100 and a tiny non-zero value still draws."""
        bars = services._bars([("big", 1000), ("tiny", 1), ("none", 0)])
        self.assertEqual([bar.pct for bar in bars], [100, services.MIN_BAR_PERCENT, 0])

    def test_compact_numbers(self):
        """Narrow charts use short numbers."""
        self.assertEqual(services._compact(Decimal("950")), "950")
        self.assertEqual(services._compact(Decimal("1500")), "1.5k")
        self.assertEqual(services._compact(Decimal("2000000")), "2m")

    def test_dashboard_renders_with_numbers(self):
        """The admin page renders the charts with real data."""
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboards:admin"))
        self.assertContains(response, "Attendance")
        self.assertContains(response, "₱1,500.50")
