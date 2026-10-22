"""Tests for the Goal time fields and the goal_time_range constraint."""
from datetime import date, time

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.faith.models import Goal

from .helpers import make_user


class GoalTimeTests(TestCase):
    """All-day, start only, and start + end are valid; everything else is refused."""

    def setUp(self):
        self.user = make_user()

    def make(self, **kw):
        data = dict(user=self.user, title="Pray", start_date=date(2026, 3, 2), repeat_type="daily")
        data.update(kw)
        return Goal(**data)

    def test_all_day_start_only_and_range_are_saved(self):
        for kw in ({}, {"start_time": time(6, 0)}, {"start_time": time(6, 0), "end_time": time(7, 0)}):
            self.make(**kw).save()

    def test_end_without_start_is_refused_by_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make(end_time=time(7, 0)).save()

    def test_end_not_after_start_is_refused_by_the_database(self):
        for end in (time(6, 0), time(5, 0)):
            with self.assertRaises(IntegrityError), transaction.atomic():
                self.make(start_time=time(6, 0), end_time=end).save()

    def test_clean_gives_friendly_errors(self):
        with self.assertRaises(ValidationError):
            self.make(end_time=time(7, 0)).clean()
        with self.assertRaises(ValidationError):
            self.make(start_time=time(8, 0), end_time=time(7, 0)).clean()
        self.make(start_time=time(6, 0), end_time=time(7, 0)).clean()   # valid: no error
