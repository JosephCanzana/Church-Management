"""The extension filter: admin and super-admin may pick one, a bad value means "All", a coordinator never can."""
from django.utils import timezone

from django.test import TestCase

from apps.accounts.models import Role
from apps.dashboards import services

from .helpers import add_draft_sheet, add_uploaded_sheet, make_extension, make_person, sundays


class ResolveExtensionTests(TestCase):
    """resolve_extension: validate against real, non-archived extensions."""

    def setUp(self):
        self.alpha, self.beta = make_extension("alpha"), make_extension("beta")
        self.admin = make_person(Role.ADMIN)
        self.super_admin = make_person(Role.SUPER_ADMIN)
        self.coordinator = make_person(Role.COORDINATOR, self.alpha)
        self.member = make_person(Role.MEMBER, self.alpha)

    def test_admin_and_super_admin_get_the_extension_they_pick(self):
        for person in (self.admin, self.super_admin):
            self.assertEqual(services.resolve_extension(person, str(self.beta.pk)), self.beta)
            self.assertEqual(services.resolve_extension(person, f" {self.beta.pk} "), self.beta)

    def test_bad_values_mean_all(self):
        bad = [None, "", "   ", "abc", "-1", "1.5", "0", "999999", "1 or 1=1", "²", "٣", "9" * 30, str(self.beta.pk) + "x"]
        for value in bad:
            self.assertIsNone(services.resolve_extension(self.admin, value), repr(value))

    def test_archived_extension_is_not_valid(self):
        self.beta.archived_at = timezone.now()
        self.beta.save(update_fields=["archived_at"])
        self.assertIsNone(services.resolve_extension(self.admin, str(self.beta.pk)))

    def test_coordinator_always_gets_their_own_extension(self):
        for raw in (None, "", str(self.beta.pk), "abc"):
            self.assertEqual(services.resolve_extension(self.coordinator, raw), self.alpha)

    def test_member_gets_nothing(self):
        self.assertIsNone(services.resolve_extension(self.member, str(self.alpha.pk)))


class ScopeFilterTests(TestCase):
    """The three `_scope_*` functions take the validated id; a coordinator's own extension still wins."""

    def setUp(self):
        self.alpha, self.beta = make_extension("alpha"), make_extension("beta")
        self.admin = make_person(Role.ADMIN)
        self.coordinator = make_person(Role.COORDINATOR, self.alpha)
        make_person(Role.MEMBER, self.alpha)
        make_person(Role.MEMBER, self.beta)
        self.day = sundays(1)[0]
        add_uploaded_sheet(self.alpha, self.day, 10)
        add_uploaded_sheet(self.beta, self.day, 20)

    def test_attendance_filter_for_admin(self):
        self.assertEqual(services._scope_attendance(self.admin).count(), 2)
        picked = services._scope_attendance(self.admin, self.beta.pk)
        self.assertEqual([s.extension_id for s in picked], [self.beta.pk])

    def test_coordinator_ignores_the_id(self):
        sheets = services._scope_attendance(self.coordinator, self.beta.pk)
        self.assertEqual([s.extension_id for s in sheets], [self.alpha.pk])

    def test_people_filter(self):
        from apps.accounts.models import User

        everyone = User.objects.all()
        beta_only = services._scope_people(everyone, self.admin, self.beta.pk)
        self.assertEqual({p.extension_id for p in beta_only}, {self.beta.pk})
        own = services._scope_people(everyone, self.coordinator, self.beta.pk)
        self.assertEqual({p.extension_id for p in own}, {self.alpha.pk})

    def test_period_narrows_sheets(self):
        add_uploaded_sheet(self.alpha, self.day.replace(day=1), 5, label="Early")
        sheets = services._scope_attendance(self.admin, None, self.day, self.day)
        self.assertEqual(sheets.count(), 2)
        self.assertEqual(services._scope_attendance(self.admin, None, None, self.day.replace(day=2)).count(), 1)


class ContextFilterTests(TestCase):
    """get_dashboard_context carries the filter and the numbers follow it."""

    def setUp(self):
        self.alpha, self.beta = make_extension("alpha"), make_extension("beta")
        self.admin = make_person(Role.ADMIN)
        self.coordinator = make_person(Role.COORDINATOR, self.alpha)
        self.member = make_person(Role.MEMBER, self.alpha)
        day = sundays(1)[0]
        add_uploaded_sheet(self.alpha, day, 10)
        add_uploaded_sheet(self.beta, day, 20)
        add_draft_sheet(self.beta, day, label="Evening")

    def context(self, person, extension=None):
        return services.get_dashboard_context(person, None, "/", extension=extension)

    def test_admin_filter_changes_the_numbers(self):
        everyone = self.context(self.admin)
        picked = self.context(self.admin, str(self.beta.pk))
        self.assertEqual(everyone["attendance_stats"]["latest"]["present"], 30)
        self.assertEqual(picked["attendance_stats"]["latest"]["present"], 20)
        self.assertEqual(picked["filter"]["extension_id"], self.beta.pk)
        self.assertEqual(picked["filter"]["label"], "Beta")
        self.assertTrue(picked["filter"]["enabled"])
        self.assertEqual([o["name"] for o in picked["filter"]["options"]], ["Alpha", "Beta"])

    def test_bad_value_falls_back_to_all(self):
        context = self.context(self.admin, "not-a-number")
        self.assertIsNone(context["filter"]["extension_id"])
        self.assertEqual(context["filter"]["label"], "All extensions")
        self.assertEqual(context["attendance_stats"]["latest"]["present"], 30)

    def test_coordinator_forged_extension_is_ignored(self):
        context = self.context(self.coordinator, str(self.beta.pk))
        self.assertEqual(context["attendance_stats"]["latest"]["present"], 10)
        self.assertEqual(context["filter"]["extension_id"], self.alpha.pk)
        self.assertFalse(context["filter"]["enabled"])
        self.assertEqual(context["filter"]["options"], [])
        self.assertEqual(context["filter"]["label"], "Alpha")

    def test_member_has_the_keys_but_no_leader_data(self):
        context = self.context(self.member, str(self.beta.pk))
        for key in ("attendance_stats", "people_stats", "extension_stats", "giving_stats", "prayer_summary"):
            self.assertIsNone(context[key], key)
        self.assertEqual(context["upcoming_events"], [])
        self.assertIsNone(context["filter"]["extension_id"])
        self.assertIsNone(context["leader"])
