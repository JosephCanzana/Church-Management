"""Tests for the faith listener on bible.signals.chapter_read (Brick 2): the Bible streak follows reads."""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Extension, Role, User
from apps.bible.services import record_chapter_read
from apps.faith.models import UserDailyActivity, UserStreak


class ChapterReadListenerTests(TestCase):
    """A NEW read row moves the read streak once; a repeat the same day changes nothing."""

    def setUp(self):
        extension = Extension.objects.create(name="alpha")
        self.user = User.objects.create_user(
            password="pass-12345", first_name="test", last_name="reader", role=Role.MEMBER,
            extension=extension, must_change_password=False, person_type="member_account",
        )

    def test_first_read_starts_streak_and_counts_chapter(self):
        record_chapter_read(self.user, "KJV", "Gen", 1)
        streak = UserStreak.objects.get(user=self.user)
        self.assertEqual(streak.current_read_streak, 1)
        self.assertEqual(streak.last_read_date, timezone.localdate())
        activity = UserDailyActivity.objects.get(user=self.user, activity_date=timezone.localdate())
        self.assertEqual(activity.chapters_read, 1)

    def test_same_chapter_twice_in_a_day_changes_nothing(self):
        record_chapter_read(self.user, "KJV", "Gen", 1)
        record_chapter_read(self.user, "KJV", "Gen", 1)
        self.assertEqual(UserDailyActivity.objects.get(user=self.user).chapters_read, 1)
        self.assertEqual(UserStreak.objects.get(user=self.user).current_read_streak, 1)

    def test_second_chapter_same_day_adds_a_chapter_not_a_streak_day(self):
        record_chapter_read(self.user, "KJV", "Gen", 1)
        record_chapter_read(self.user, "KJV", "Gen", 2)
        self.assertEqual(UserDailyActivity.objects.get(user=self.user).chapters_read, 2)
        self.assertEqual(UserStreak.objects.get(user=self.user).current_read_streak, 1)

    def test_next_day_extends_the_streak(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        with mock.patch("django.utils.timezone.localdate", return_value=yesterday):
            record_chapter_read(self.user, "KJV", "Gen", 1)
        record_chapter_read(self.user, "KJV", "Gen", 2)
        streak = UserStreak.objects.get(user=self.user)
        self.assertEqual(streak.current_read_streak, 2)
        self.assertEqual(streak.longest_read_streak, 2)

    def test_read_does_not_touch_the_open_streak(self):
        record_chapter_read(self.user, "KJV", "Gen", 1)
        streak = UserStreak.objects.get(user=self.user)
        self.assertEqual(streak.current_open_streak, 0)

    def test_pressing_next_moves_the_streak_end_to_end(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("bible:finish", args=["Gen", 1]), {"after": "next"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(UserStreak.objects.get(user=self.user).current_read_streak, 1)
