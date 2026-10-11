"""Tests for apps/bible/esv.py: switches, locks, call budget and the 500-verse cache."""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.bible import esv
from apps.bible.errors import VersionLocked
from apps.bible.esv_client import EsvAuthError, EsvError, EsvThrottled
from apps.bible.models import EsvChapterCache
from apps.core.models import SiteSetting

FETCH = "apps.bible.esv.esv_client.fetch_chapter"


def verses(count, prefix="v"):
    """A fake chapter with ``count`` verses."""
    return [(n, f"{prefix}{n}") for n in range(1, count + 1)]


@override_settings(ESV_API_KEY="test-key")
class LockReasonTests(TestCase):
    """Why ESV is (not) available."""

    def test_available_by_default_with_a_key(self):
        self.assertEqual(esv.lock_reason(), "")

    @override_settings(ESV_API_KEY="")
    def test_locked_without_a_key(self):
        self.assertEqual(esv.lock_reason(), esv.MSG_NOT_SET_UP)

    def test_master_and_esv_switches(self):
        SiteSetting.objects.create(key=esv.KEY_ESV, value=False)
        self.assertEqual(esv.lock_reason(), esv.MSG_ESV_OFF)
        SiteSetting.objects.create(key=esv.KEY_MASTER, value=False)
        self.assertEqual(esv.lock_reason(), esv.MSG_ALL_OFF)  # master wins


@override_settings(ESV_API_KEY="test-key")
class GetVersesTests(TestCase):
    """Fetching, caching and what happens when the service misbehaves."""

    def test_fetches_once_then_serves_the_cache(self):
        with mock.patch(FETCH, return_value=verses(3)) as fetch:
            first = esv.get_verses("Gen", 1)
            second = esv.get_verses("Gen", 1)
        self.assertEqual(first, second)
        self.assertEqual(fetch.call_count, 1)

    def test_cached_chapter_still_served_when_switched_off_or_locked(self):
        with mock.patch(FETCH, return_value=verses(3)):
            esv.get_verses("Gen", 1)
        SiteSetting.objects.create(key=esv.KEY_MASTER, value=False)
        with mock.patch(FETCH) as fetch:
            self.assertEqual(len(esv.get_verses("Gen", 1)), 3)  # cached
            with self.assertRaises(VersionLocked):
                esv.get_verses("Gen", 2)                          # not cached
        fetch.assert_not_called()

    def test_expired_cache_is_ignored_and_refetched(self):
        with mock.patch(FETCH, return_value=verses(2)):
            esv.get_verses("Gen", 1)
        EsvChapterCache.objects.update(fetched_at=timezone.now() - esv.ESV_CACHE_MAX_AGE - timedelta(minutes=1))
        with mock.patch(FETCH, return_value=verses(2, "new")) as fetch:
            self.assertEqual(esv.get_verses("Gen", 1)[0][1], "new1")
        self.assertEqual(fetch.call_count, 1)

    def test_throttled_locks_until_retry_after_then_unlocks(self):
        with mock.patch(FETCH, side_effect=EsvThrottled(120)):
            with self.assertRaises(VersionLocked) as caught:
                esv.get_verses("Gen", 1)
        self.assertEqual(caught.exception.reason, esv.MSG_THROTTLED)
        self.assertEqual(esv.lock_reason(), esv.MSG_THROTTLED)
        with mock.patch(FETCH) as fetch:
            with self.assertRaises(VersionLocked):
                esv.get_verses("Gen", 2)
        fetch.assert_not_called()  # no call while locked
        later = timezone.now() + timedelta(seconds=121)
        with mock.patch("apps.bible.esv.timezone.now", return_value=later):
            self.assertEqual(esv.lock_reason(), "")

    def test_throttled_without_retry_after_uses_the_fallback_wait(self):
        with mock.patch(FETCH, side_effect=EsvThrottled(None)), self.assertRaises(VersionLocked):
            esv.get_verses("Gen", 1)
        almost = timezone.now() + timedelta(seconds=esv.FALLBACK_THROTTLE_SECONDS - 5)
        with mock.patch("apps.bible.esv.timezone.now", return_value=almost):
            self.assertEqual(esv.lock_reason(), esv.MSG_THROTTLED)

    def test_other_problems_lock_briefly_and_hide_the_cause(self):
        for error in (EsvError("boom"), EsvAuthError("bad key")):
            SiteSetting.objects.filter(key=esv.KEY_STATE).delete()
            with self.subTest(error=error), mock.patch(FETCH, side_effect=error):
                with self.assertRaises(VersionLocked) as caught:
                    esv.get_verses("Gen", 1)
                self.assertEqual(caught.exception.reason, esv.MSG_PROBLEM)
                self.assertNotIn("key", caught.exception.reason.lower())

    def test_a_failed_call_is_not_cached(self):
        with mock.patch(FETCH, side_effect=EsvError("boom")), self.assertRaises(VersionLocked):
            esv.get_verses("Gen", 1)
        self.assertEqual(EsvChapterCache.objects.count(), 0)


@override_settings(ESV_API_KEY="test-key")
class BudgetTests(TestCase):
    """Our own call budget stays under Crossway's limits."""

    def test_limits_are_below_crossways(self):
        self.assertLess(esv.BUDGET_PER_MINUTE, 60)
        self.assertLess(esv.BUDGET_PER_HOUR, 1000)
        self.assertLess(esv.BUDGET_PER_DAY, 5000)

    def test_minute_budget_locks_until_the_next_minute(self):
        with mock.patch.object(esv, "_WINDOWS", ((("minute", "%Y%m%d%H%M", 2, esv.MSG_MINUTE),) + esv._WINDOWS[1:])):
            with mock.patch(FETCH, return_value=verses(1)) as fetch:
                esv.get_verses("Gen", 1)
                esv.get_verses("Gen", 2)
                with self.assertRaises(VersionLocked) as caught:
                    esv.get_verses("Gen", 3)
            self.assertEqual(caught.exception.reason, esv.MSG_MINUTE)
            self.assertEqual(fetch.call_count, 2)
            later = timezone.now() + timedelta(minutes=2)
            with mock.patch("apps.bible.esv.timezone.now", return_value=later):
                self.assertEqual(esv.lock_reason(), "")

    def test_day_budget_locks_until_tomorrow(self):
        day_only = (esv._WINDOWS[0], esv._WINDOWS[1], ("day", "%Y%m%d", 1, esv.MSG_DAY))
        with mock.patch.object(esv, "_WINDOWS", day_only), mock.patch(FETCH, return_value=verses(1)):
            esv.get_verses("Gen", 1)
            with self.assertRaises(VersionLocked) as caught:
                esv.get_verses("Gen", 2)
        self.assertEqual(caught.exception.reason, esv.MSG_DAY)
        state = SiteSetting.objects.get(key=esv.KEY_STATE).value
        self.assertGreater(esv._parse_time(state["locked_until"]), timezone.now() + timedelta(minutes=1))

    def test_counters_start_over_in_a_new_window(self):
        with mock.patch(FETCH, return_value=verses(1)):
            esv.get_verses("Gen", 1)
            later = timezone.now() + timedelta(days=1, minutes=5)
            with mock.patch("apps.bible.esv.timezone.now", return_value=later):
                esv.get_verses("Gen", 2)
        state = SiteSetting.objects.get(key=esv.KEY_STATE).value
        self.assertEqual(state["day"]["count"], 1)


@override_settings(ESV_API_KEY="test-key")
class CacheCapTests(TestCase):
    """Crossway: never store more than 500 verses. We keep the total at 450 or less."""

    def total(self):
        return sum(EsvChapterCache.objects.values_list("verse_count", flat=True))

    def test_cap_is_under_crossways_limit(self):
        self.assertLessEqual(esv.ESV_CACHE_MAX_VERSES, 450)

    def test_least_recently_used_chapters_are_evicted(self):
        with mock.patch(FETCH, side_effect=lambda key, book, chapter: verses(200, f"c{chapter}-")):
            esv.get_verses("Gen", 1)
            esv.get_verses("Gen", 2)          # 400 verses cached
            esv.get_verses("Gen", 1)          # touch chapter 1: now chapter 2 is the oldest used
            esv.get_verses("Gen", 3)          # needs room: chapter 2 must go
        cached = set(EsvChapterCache.objects.values_list("chapter", flat=True))
        self.assertEqual(cached, {1, 3})
        self.assertLessEqual(self.total(), esv.ESV_CACHE_MAX_VERSES)

    def test_total_never_exceeds_the_cap_over_many_chapters(self):
        with mock.patch(FETCH, side_effect=lambda key, book, chapter: verses(31)):
            for chapter in range(1, 41):
                esv.get_verses("Ps", chapter)
                self.assertLessEqual(self.total(), esv.ESV_CACHE_MAX_VERSES)

    def test_a_chapter_over_the_cap_is_shown_but_not_cached(self):
        with mock.patch(FETCH, return_value=verses(esv.ESV_CACHE_MAX_VERSES + 1)):
            self.assertEqual(len(esv.get_verses("Ps", 119)), esv.ESV_CACHE_MAX_VERSES + 1)
        self.assertEqual(EsvChapterCache.objects.count(), 0)

    def test_database_refuses_an_oversized_row(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            EsvChapterCache.objects.create(
                book="Gen", chapter=1, verses=[], verse_count=501,
                fetched_at=timezone.now(), last_used_at=timezone.now(),
            )
