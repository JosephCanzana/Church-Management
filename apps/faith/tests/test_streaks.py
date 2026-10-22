"""Tests for faith.streaks (open and read streak engine)."""
from datetime import date, timedelta

from django.test import TestCase

from apps.faith.models import UserDailyActivity, UserStreak
from apps.faith.streaks import record_open, record_read, streak_snapshot

from .helpers import make_user

D = date(2026, 3, 10)


def day(offset):
    """Return D shifted by ``offset`` days."""
    return D + timedelta(days=offset)


class OpenStreakTests(TestCase):
    """record_open and the open streak columns."""

    def setUp(self):
        self.user = make_user()

    def streak(self):
        return UserStreak.objects.get(pk=self.user.pk)

    def test_first_day(self):
        record_open(self.user, D)
        s = self.streak()
        self.assertEqual((s.current_open_streak, s.longest_open_streak, s.last_open_date), (1, 1, D))
        self.assertTrue(UserDailyActivity.objects.get(user=self.user, activity_date=D).opened_app)

    def test_consecutive_days_add_up(self):
        for i in range(3):
            record_open(self.user, day(i))
        s = self.streak()
        self.assertEqual((s.current_open_streak, s.longest_open_streak), (3, 3))

    def test_gap_resets_to_one(self):
        record_open(self.user, D)
        record_open(self.user, day(1))
        record_open(self.user, day(3))  # skipped day 2
        self.assertEqual(self.streak().current_open_streak, 1)

    def test_same_day_twice_changes_nothing(self):
        record_open(self.user, D)
        record_open(self.user, D)
        s = self.streak()
        self.assertEqual((s.current_open_streak, s.longest_open_streak), (1, 1))
        self.assertEqual(UserDailyActivity.objects.filter(user=self.user).count(), 1)

    def test_longest_kept_after_reset(self):
        for i in range(4):
            record_open(self.user, day(i))      # streak of 4
        record_open(self.user, day(10))         # gap -> reset
        s = self.streak()
        self.assertEqual((s.current_open_streak, s.longest_open_streak), (1, 4))

    def test_out_of_order_older_date_never_moves_a_streak(self):
        record_open(self.user, day(5))
        record_open(self.user, day(2))
        s = self.streak()
        self.assertEqual((s.current_open_streak, s.last_open_date), (1, day(5)))
        # ...but the older day is still recorded as activity
        self.assertTrue(UserDailyActivity.objects.get(user=self.user, activity_date=day(2)).opened_app)


class ReadStreakTests(TestCase):
    """record_read and independence from the open streak."""

    def setUp(self):
        self.user = make_user()

    def test_read_counts_chapters_and_streak(self):
        record_read(self.user, D)
        record_read(self.user, D)   # a second chapter the same day
        s = UserStreak.objects.get(pk=self.user.pk)
        self.assertEqual((s.current_read_streak, s.longest_read_streak, s.last_read_date), (1, 1, D))
        self.assertEqual(UserDailyActivity.objects.get(user=self.user, activity_date=D).chapters_read, 2)

    def test_read_consecutive_and_gap(self):
        record_read(self.user, D)
        record_read(self.user, day(1))
        record_read(self.user, day(5))
        s = UserStreak.objects.get(pk=self.user.pk)
        self.assertEqual((s.current_read_streak, s.longest_read_streak), (1, 2))

    def test_open_and_read_are_independent(self):
        record_open(self.user, D)
        record_open(self.user, day(1))
        record_read(self.user, day(1))
        s = UserStreak.objects.get(pk=self.user.pk)
        self.assertEqual((s.current_open_streak, s.current_read_streak), (2, 1))
        self.assertFalse(UserDailyActivity.objects.get(user=self.user, activity_date=day(1)).chapters_read == 0)

    def test_read_does_not_mark_opened_app(self):
        record_read(self.user, D)
        self.assertFalse(UserDailyActivity.objects.get(user=self.user, activity_date=D).opened_app)


class SnapshotTests(TestCase):
    """streak_snapshot: what the person sees on a given day."""

    def setUp(self):
        self.user = make_user()

    def test_no_row_gives_zeros_and_creates_nothing(self):
        snap = streak_snapshot(self.user, D)
        self.assertEqual(
            (snap.open_current, snap.open_longest, snap.read_current, snap.read_longest), (0, 0, 0, 0)
        )
        self.assertFalse(UserStreak.objects.filter(pk=self.user.pk).exists())

    def test_yesterday_keeps_streak_alive(self):
        record_open(self.user, day(-2))
        record_open(self.user, day(-1))
        self.assertEqual(streak_snapshot(self.user, D).open_current, 2)

    def test_today_counts(self):
        record_open(self.user, D)
        self.assertEqual(streak_snapshot(self.user, D).open_current, 1)

    def test_shows_zero_after_a_gap_but_keeps_longest(self):
        record_open(self.user, day(-5))
        record_open(self.user, day(-4))
        snap = streak_snapshot(self.user, D)   # last open was 4 days ago
        self.assertEqual((snap.open_current, snap.open_longest), (0, 2))

    def test_open_and_read_snapshot_independently(self):
        record_open(self.user, D)
        snap = streak_snapshot(self.user, D)
        self.assertEqual((snap.open_current, snap.read_current), (1, 0))
