"""Tests for the draft workspace and the review screen (brick 8, round 2).

Builds on `ScreenTestCase` from test_views.py (people, two extensions, a draft sheet each). Every person who
signs in is created activated. Nothing here calls an outside service. The business rules themselves are tested
in the service tests; this file checks the SCREENS: who reaches what, that each tap reaches the right service,
that a refusal comes back as a message (never a stack trace), and that nothing private is printed on the page.
"""
from datetime import date

from django.contrib.messages import get_messages
from django.urls import reverse

from apps.accounts.decorators import home_url_for
from apps.accounts.models import Gender, Role
from apps.attendance import services
from apps.attendance.age import AgeNetwork
from apps.attendance.models import Attendance, AttendanceFirstTimer, AttendanceMember
from apps.core.services import new_submission_token

from .test_views import ScreenTestCase, person

BIRTH = date(1987, 3, 9)


class WorkspaceTestCase(ScreenTestCase):
    """Adds POST and message helpers, and a few extra people."""

    def setUp(self):
        super().setUp()
        # A visitor: belongs to the other extension, complete record, a birth date that must never be printed.
        self.visitor = person(Role.MEMBER, self.b, first="visitor", birth_date=BIRTH)
        # A member with no gender and no birth date: ticking them needs an inline category.
        self.nocat = person(Role.MEMBER, self.a, first="nocat", complete=False)

    def post(self, user, name, sheet, data=None):
        """Sign `user` in and POST one of the sheet actions in their own area."""
        self.client.force_login(user)
        return self.client.post(self.url(user, name, sheet.pk), data or {})

    def messages_of(self, response):
        """The toast texts queued by the view that produced `response`."""
        return [str(m) for m in get_messages(response.wsgi_request)]

    def tick(self, sheet, *people):
        """Mark people present as the coordinator (the service, not the screen)."""
        for who in people:
            services.mark_present(self.coord, sheet, who)

    def first_timer_post(self, **extra):
        """A valid first-timer form with a name nobody has."""
        data = {"first_name": "newbie", "last_name": "person", "gender": Gender.FEMALE, "age_network": AgeNetwork.YOUTH}
        data.update(extra)
        return data


class WorkspaceAccessTests(WorkspaceTestCase):
    def test_a_draft_opens_the_workspace_for_every_role_that_may_edit_it(self):
        for user in (self.coord, self.admin, self.superadmin, self.special):
            response = self.get(user, "sheet_detail", self.draft_a.pk)
            self.assertEqual(response.status_code, 200, user.role)
            self.assertTemplateUsed(response, "attendance/sheet_workspace.html")

    def test_an_uploaded_sheet_opens_on_the_review(self):
        self.mark_uploaded(self.draft_a)
        response = self.get(self.coord, "sheet_detail", self.draft_a.pk)
        self.assertRedirects(
            response, self.url(self.coord, "sheet_review", self.draft_a.pk), fetch_redirect_response=False)

    def test_a_special_member_cannot_open_an_uploaded_sheet(self):
        self.mark_uploaded(self.draft_a)
        for name in ("sheet_detail", "sheet_review"):
            response = self.get(self.special, name, self.draft_a.pk)
            self.assertRedirects(response, self.url(self.special, "sheet_list"), fetch_redirect_response=False)

    def test_another_extensions_coordinator_sees_a_sheet_that_looks_deleted(self):
        for name in ("sheet_detail", "sheet_review"):
            response = self.get(self.coord_b, name, self.draft_a.pk)
            self.assertRedirects(response, self.url(self.coord_b, "sheet_list"), fetch_redirect_response=False)

    def test_the_action_urls_accept_post_only(self):
        for name in ("sheet_mark", "sheet_unmark", "sheet_member_category", "sheet_first_timer_add",
                     "sheet_first_timer_remove", "sheet_upload"):
            self.client.force_login(self.coord)
            response = self.client.get(self.url(self.coord, name, self.draft_a.pk))
            self.assertEqual(response.status_code, 405, name)

    def test_a_member_without_the_seat_cannot_tick(self):
        response = self.post(self.plain, "sheet_mark", self.draft_a, {"user_id": self.visitor.pk})
        self.assertRedirects(response, home_url_for(self.plain), fetch_redirect_response=False)
        self.assertFalse(AttendanceMember.objects.filter(attendance=self.draft_a).exists())

    def test_another_extensions_coordinator_cannot_tick(self):
        self.client.force_login(self.coord_b)
        response = self.client.post(
            reverse("coordinator_area:sheet_mark", args=[self.draft_a.pk]), {"user_id": self.plain.pk})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AttendanceMember.objects.filter(attendance=self.draft_a).exists())

    def test_a_signed_out_visitor_is_sent_to_sign_in(self):
        response = self.client.post(reverse("coordinator_area:sheet_mark", args=[self.draft_a.pk]), {})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)


class SearchAndTickTests(WorkspaceTestCase):
    def test_search_finds_a_visitor_and_shows_nothing_private(self):
        response = self.get(self.coord, "sheet_detail", self.draft_a.pk, q="visitor")
        results = response.context["results"]
        self.assertEqual([r["id"] for r in results], [self.visitor.pk])
        self.assertTrue(results[0]["visitor"])
        for text in ("1987-03-09", "9 March 1987", "March 9, 1987", "Mar. 9, 1987"):
            self.assertNotContains(response, text)

    def test_a_short_search_asks_for_more_letters(self):
        response = self.get(self.coord, "sheet_detail", self.draft_a.pk, q="vi")
        self.assertEqual(response.context["results"], [])
        self.assertIn("at least 3", response.context["search_hint"])

    def test_a_ticked_person_shows_as_marked_in_the_results(self):
        self.tick(self.draft_a, self.visitor)
        response = self.get(self.coord, "sheet_detail", self.draft_a.pk, q="visitor")
        self.assertTrue(response.context["results"][0]["marked"])

    def test_one_tap_marks_a_visitor_and_goes_back_to_the_search(self):
        response = self.post(self.coord, "sheet_mark", self.draft_a, {"user_id": self.visitor.pk})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("#find"))
        row = AttendanceMember.objects.get(attendance=self.draft_a)
        self.assertEqual(row.user, self.visitor)
        self.assertEqual(row.home_extension, self.b)
        self.assertEqual(row.age_network, AgeNetwork.ADULT)  # born 1987: 39 on the sheet date

    def test_ticking_twice_keeps_one_row_and_says_so(self):
        self.post(self.coord, "sheet_mark", self.draft_a, {"user_id": self.visitor.pk})
        response = self.post(self.coord, "sheet_mark", self.draft_a, {"user_id": self.visitor.pk})
        self.assertEqual(AttendanceMember.objects.filter(attendance=self.draft_a).count(), 1)
        self.assertTrue(any("already" in text for text in self.messages_of(response)))

    def test_a_special_member_can_tick_on_a_draft(self):
        response = self.post(self.special, "sheet_mark", self.draft_a, {"user_id": self.visitor.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceMember.objects.filter(attendance=self.draft_a).count(), 1)

    def test_an_unknown_person_id_is_a_message_not_an_error(self):
        for value in ("", "abc", "999999"):
            response = self.post(self.coord, "sheet_mark", self.draft_a, {"user_id": value})
            self.assertEqual(response.status_code, 302, value)
        self.assertFalse(AttendanceMember.objects.filter(attendance=self.draft_a).exists())

    def test_a_locked_sheet_refuses_the_tap_with_a_message(self):
        self.tick(self.draft_a, self.visitor)
        self.mark_uploaded(self.draft_a)
        response = self.post(self.coord, "sheet_mark", self.draft_a, {"user_id": self.plain.pk})
        self.assertEqual(AttendanceMember.objects.filter(attendance=self.draft_a).count(), 1)
        self.assertTrue(any("locked" in text for text in self.messages_of(response)))

    def test_unmark_takes_a_person_off(self):
        self.tick(self.draft_a, self.visitor)
        row = AttendanceMember.objects.get(attendance=self.draft_a)
        self.post(self.coord, "sheet_unmark", self.draft_a, {"member_id": row.pk})
        self.assertFalse(AttendanceMember.objects.filter(attendance=self.draft_a).exists())


class CategoryTests(WorkspaceTestCase):
    def test_a_member_without_a_category_is_flagged_and_listed_first(self):
        self.tick(self.draft_a, self.plain, self.nocat)
        response = self.get(self.coord, "sheet_detail", self.draft_a.pk)
        members = response.context["members"]
        self.assertTrue(members[0].needs_category)
        self.assertEqual(members[0].user, self.nocat)
        self.assertEqual(response.context["summary"].incomplete, 1)
        self.assertTrue(response.context["upload_blocker"])

    def test_the_inline_pick_saves_the_row_only(self):
        self.tick(self.draft_a, self.nocat)
        row = AttendanceMember.objects.get(attendance=self.draft_a)
        self.post(self.coord, "sheet_member_category", self.draft_a, {
            "member_id": row.pk, "gender": Gender.MALE, "age_network": AgeNetwork.YOUTH,
        })
        row.refresh_from_db()
        self.assertEqual((row.gender, row.age_network), (Gender.MALE, AgeNetwork.YOUTH))
        self.nocat.refresh_from_db()
        self.assertEqual(self.nocat.gender, "")  # never written to the person
        response = self.get(self.coord, "sheet_detail", self.draft_a.pk)
        self.assertEqual(response.context["summary"].incomplete, 0)

    def test_an_empty_pick_is_refused_with_a_message(self):
        self.tick(self.draft_a, self.nocat)
        row = AttendanceMember.objects.get(attendance=self.draft_a)
        response = self.post(self.coord, "sheet_member_category", self.draft_a, {"member_id": row.pk})
        self.assertTrue(self.messages_of(response))
        row.refresh_from_db()
        self.assertEqual(row.gender, "")


class FirstTimerTests(WorkspaceTestCase):
    def test_authorized_leaders_can_create_a_member_from_the_workspace_and_review(self):
        services.add_first_timer(
            self.coord, self.draft_a, "newbie", "person", Gender.FEMALE, AgeNetwork.YOUTH,
        )

        workspace = self.get(self.coord, "sheet_detail", self.draft_a.pk)
        self.assertContains(workspace, "Make a member")

        self.mark_uploaded(self.draft_a)
        review = self.get(self.coord, "sheet_review", self.draft_a.pk)
        self.assertContains(review, "Make a member")

    def test_a_first_timer_is_added_in_one_post(self):
        response = self.post(self.coord, "sheet_first_timer_add", self.draft_a, self.first_timer_post())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("#first-timers"))
        row = AttendanceFirstTimer.objects.get(attendance=self.draft_a)
        self.assertEqual((row.gender, row.age_network), (Gender.FEMALE, AgeNetwork.YOUTH))

    def test_a_missing_gender_comes_back_with_the_error_and_saves_nothing(self):
        data = self.first_timer_post()
        del data["gender"]
        response = self.post(self.coord, "sheet_first_timer_add", self.draft_a, data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("gender", response.context["form"].errors)
        self.assertFalse(AttendanceFirstTimer.objects.exists())

    def test_the_age_network_is_needed_unless_a_birth_date_is_given(self):
        data = self.first_timer_post()
        del data["age_network"]
        response = self.post(self.coord, "sheet_first_timer_add", self.draft_a, data)
        self.assertIn("age_network", response.context["form"].errors)
        data["birth_date"] = "2005-01-01"
        response = self.post(self.coord, "sheet_first_timer_add", self.draft_a, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceFirstTimer.objects.get().age_network, AgeNetwork.YOUTH)

    def test_the_same_person_prompt_saves_nothing_and_lists_the_match(self):
        data = self.first_timer_post(first_name="plain", last_name="tester")
        response = self.post(self.coord, "sheet_first_timer_add", self.draft_a, data)
        self.assertEqual(response.status_code, 200)
        pending = response.context["pending"]
        self.assertIsNotNone(pending)
        self.assertIn(self.plain.pk, [m["id"] for m in pending["matches"]])
        self.assertFalse(AttendanceFirstTimer.objects.exists())
        self.assertContains(response, "No, add as a new first timer")

    def test_a_new_person_answer_saves_the_first_timer(self):
        data = self.first_timer_post(first_name="plain", last_name="tester", confirm_new="on")
        response = self.post(self.coord, "sheet_first_timer_add", self.draft_a, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceFirstTimer.objects.filter(attendance=self.draft_a).count(), 1)

    def test_a_same_person_answer_marks_the_member_present_instead(self):
        self.post(self.coord, "sheet_mark", self.draft_a, {
            "user_id": self.nocat.pk, "gender": Gender.MALE, "age_network": AgeNetwork.ADULT,
        })
        row = AttendanceMember.objects.get(attendance=self.draft_a)
        self.assertEqual((row.gender, row.age_network), (Gender.MALE, AgeNetwork.ADULT))
        self.assertFalse(AttendanceFirstTimer.objects.exists())

    def test_a_first_timer_is_removed(self):
        self.post(self.coord, "sheet_first_timer_add", self.draft_a, self.first_timer_post())
        row = AttendanceFirstTimer.objects.get()
        self.post(self.coord, "sheet_first_timer_remove", self.draft_a, {"first_timer_id": row.pk})
        self.assertFalse(AttendanceFirstTimer.objects.exists())

    def test_a_locked_sheet_refuses_a_first_timer(self):
        self.tick(self.draft_a, self.visitor)
        self.mark_uploaded(self.draft_a)
        response = self.post(self.coord, "sheet_first_timer_add", self.draft_a, self.first_timer_post())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AttendanceFirstTimer.objects.exists())
        self.assertTrue(any("locked" in text for text in self.messages_of(response)))

    def test_the_phone_and_birth_date_are_not_shown_back_on_the_list(self):
        self.post(self.coord, "sheet_first_timer_add", self.draft_a, self.first_timer_post(
            phone_number="09171234567", birth_date="2006-05-04"))
        self.assertTrue(AttendanceFirstTimer.objects.exists())  # saved, so the checks below mean something
        response = self.get(self.coord, "sheet_detail", self.draft_a.pk)
        self.assertContains(response, "Newbie Person")
        self.assertNotContains(response, "09171234567")
        self.assertNotContains(response, "2006-05-04")


class UploadTests(WorkspaceTestCase):
    def upload(self, user, sheet):
        return self.post(user, "sheet_upload", sheet, {"submit_token": new_submission_token()})

    def test_the_coordinator_uploads_and_lands_on_the_review(self):
        self.tick(self.draft_a, self.plain, self.visitor)
        self.post(self.coord, "sheet_first_timer_add", self.draft_a, self.first_timer_post())
        response = self.upload(self.coord, self.draft_a)
        self.assertEqual(response.status_code, 302)
        sheet = Attendance.objects.get(pk=self.draft_a.pk)
        self.assertEqual(sheet.status, Attendance.Status.UPLOADED)
        self.assertEqual((sheet.members_present, sheet.first_timers_count, sheet.total_present), (2, 1, 3))
        self.assertEqual(sheet.uploaded_by, self.coord)
        review = self.get(self.coord, "sheet_review", sheet.pk)
        self.assertContains(review, self.coord.full_name)

    def test_a_double_tap_uploads_once(self):
        self.tick(self.draft_a, self.plain)
        token = new_submission_token()
        self.post(self.coord, "sheet_upload", self.draft_a, {"submit_token": token})
        response = self.post(self.coord, "sheet_upload", self.draft_a, {"submit_token": token})
        self.assertEqual(Attendance.objects.get(pk=self.draft_a.pk).status, Attendance.Status.UPLOADED)
        self.assertEqual(response.status_code, 302)

    def test_a_sheet_with_a_missing_category_is_refused(self):
        self.tick(self.draft_a, self.plain, self.nocat)
        response = self.upload(self.coord, self.draft_a)
        self.assertEqual(Attendance.objects.get(pk=self.draft_a.pk).status, Attendance.Status.DRAFT)
        self.assertTrue(any("need" in text for text in self.messages_of(response)))

    def test_an_empty_sheet_is_refused(self):
        self.upload(self.coord, self.draft_a)
        self.assertEqual(Attendance.objects.get(pk=self.draft_a.pk).status, Attendance.Status.DRAFT)

    def test_a_special_member_and_an_admin_cannot_upload(self):
        self.tick(self.draft_a, self.plain)
        for user in (self.special, self.admin):  # the extension has a coordinator, so an admin may not either
            self.upload(user, self.draft_a)
            self.assertEqual(Attendance.objects.get(pk=self.draft_a.pk).status, Attendance.Status.DRAFT, user.role)

    def test_only_the_coordinator_is_offered_the_upload_button(self):
        self.tick(self.draft_a, self.plain)
        self.assertTrue(self.get(self.coord, "sheet_detail", self.draft_a.pk).context["can_upload"])
        self.assertFalse(self.get(self.special, "sheet_detail", self.draft_a.pk).context["can_upload"])
        self.assertFalse(self.get(self.admin, "sheet_detail", self.draft_a.pk).context["can_upload"])


class ReviewTests(WorkspaceTestCase):
    def test_the_review_renders_for_a_draft_and_shows_no_birth_date(self):
        self.tick(self.draft_a, self.visitor)
        response = self.get(self.coord, "sheet_review", self.draft_a.pk)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "attendance/sheet_review.html")
        self.assertContains(response, "Visitor Tester")
        self.assertContains(response, "Draft, not uploaded")
        for text in ("1987-03-09", "9 March 1987", "March 9, 1987"):
            self.assertNotContains(response, text)

    def test_a_special_member_reviews_a_draft(self):
        response = self.get(self.special, "sheet_review", self.draft_a.pk)
        self.assertEqual(response.status_code, 200)

    def test_an_admin_reviews_an_uploaded_sheet_read_only(self):
        self.tick(self.draft_a, self.plain)
        self.post(self.coord, "sheet_upload", self.draft_a, {"submit_token": new_submission_token()})
        response = self.get(self.admin, "sheet_review", self.draft_a.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_edit"])
        self.assertFalse(response.context["can_upload"])
        self.assertEqual(response.context["summary"].total_present, 1)


class ReopenTests(WorkspaceTestCase):
    def uploaded(self):
        """A real uploaded sheet with one member."""
        self.tick(self.draft_a, self.plain)
        self.post(self.coord, "sheet_upload", self.draft_a, {"submit_token": new_submission_token()})
        return Attendance.objects.get(pk=self.draft_a.pk)

    def reopen(self, user, sheet, token=None):
        return self.post(user, "sheet_reopen", sheet, {"submit_token": token or new_submission_token()})

    def test_an_admin_reopens_and_lands_on_the_workspace(self):
        sheet = self.uploaded()
        response = self.reopen(self.admin, sheet)
        self.assertRedirects(response, self.url(self.admin, "sheet_detail", sheet.pk), fetch_redirect_response=False)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, Attendance.Status.DRAFT)
        self.assertIsNone(sheet.total_present)
        self.assertIsNone(sheet.uploaded_by)
        self.assertEqual(self.get(self.admin, "sheet_detail", sheet.pk).status_code, 200)

    def test_the_superadmin_may_reopen_too(self):
        sheet = self.uploaded()
        self.reopen(self.superadmin, sheet)
        sheet.refresh_from_db()
        self.assertEqual(sheet.status, Attendance.Status.DRAFT)

    def test_a_coordinator_and_a_special_member_cannot_reopen(self):
        sheet = self.uploaded()
        self.reopen(self.coord, sheet)
        self.assertEqual(Attendance.objects.get(pk=sheet.pk).status, Attendance.Status.UPLOADED)
        self.reopen(self.special, sheet)  # an uploaded sheet is out of a special member's reach
        self.assertEqual(Attendance.objects.get(pk=sheet.pk).status, Attendance.Status.UPLOADED)

    def test_a_draft_cannot_be_reopened(self):
        response = self.reopen(self.admin, self.draft_a)
        self.assertTrue(any("draft" in text for text in self.messages_of(response)))

    def test_a_double_tap_reopens_once(self):
        sheet = self.uploaded()
        token = new_submission_token()
        self.reopen(self.admin, sheet, token)
        # The coordinator uploads again; a replay of the old reopen form must not undo that.
        self.post(self.coord, "sheet_upload", sheet, {"submit_token": new_submission_token()})
        self.reopen(self.admin, sheet, token)
        self.assertEqual(Attendance.objects.get(pk=sheet.pk).status, Attendance.Status.UPLOADED)

    def test_only_an_admin_is_offered_the_button_on_the_review(self):
        sheet = self.uploaded()
        self.assertTrue(self.get(self.admin, "sheet_review", sheet.pk).context["can_reopen"])
        self.assertContains(self.get(self.admin, "sheet_review", sheet.pk), "Reopen to correct")
        self.assertFalse(self.get(self.coord, "sheet_review", sheet.pk).context["can_reopen"])
        self.assertNotContains(self.get(self.coord, "sheet_review", sheet.pk), "Reopen to correct")
