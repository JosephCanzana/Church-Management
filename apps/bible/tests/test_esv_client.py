"""Tests for the ESV HTTP client: query, parsing and error mapping (no network)."""
import io
import json
import urllib.error
from unittest import mock

from django.test import SimpleTestCase

from apps.bible import esv_client
from apps.bible.esv_client import EsvAuthError, EsvError, EsvThrottled, build_query, fetch_chapter, parse_passage_text

SAMPLE = "  [1] In the beginning, God created\n  the heavens and the earth.\n\n  [2] The earth was without form\n      and void. (ESV)"


class FakeResponse:
    """Minimal urlopen() result."""

    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, headers=None):
    """An HTTPError like urllib raises."""
    return urllib.error.HTTPError("https://api.esv.org/", code, "x", headers or {}, io.BytesIO(b""))


class BuildQueryTests(SimpleTestCase):
    """Chapters are asked for by name; single-chapter books by book name only."""

    def test_queries(self):
        self.assertEqual(build_query("John", 3), "John 3")
        self.assertEqual(build_query("1Sam", 17), "1 Samuel 17")
        self.assertEqual(build_query("Obad", 1), "Obadiah")
        self.assertEqual(build_query("Jude", 1), "Jude")


class ParseTests(SimpleTestCase):
    """Plain ESV text becomes numbered verses."""

    def test_parses_verses_and_drops_the_trailing_tag(self):
        self.assertEqual(parse_passage_text(SAMPLE), [
            (1, "In the beginning, God created the heavens and the earth."),
            (2, "The earth was without form and void."),
        ])

    def test_text_before_the_first_number_is_dropped(self):
        verses = parse_passage_text("To the choirmaster.\n [1] O LORD, how many are my foes! [2] Many are rising.")
        self.assertEqual([n for n, _ in verses], [1, 2])
        self.assertNotIn("choirmaster", verses[0][1])

    def test_skipped_verse_numbers_are_fine(self):
        self.assertEqual([n for n, _ in parse_passage_text("[20] a [22] b")], [20, 22])

    def test_bad_text_is_refused(self):
        for bad in ("", "no numbers here", "[2] a [1] b", "[1]   ", "[1] a [1] b"):
            with self.subTest(bad=bad), self.assertRaises(EsvError):
                parse_passage_text(bad)


class FetchTests(SimpleTestCase):
    """fetch_chapter sends the key and maps every failure to a clear error."""

    def _fetch(self, **kwargs):
        with mock.patch("apps.bible.esv_client.urllib.request.urlopen", **kwargs) as opened:
            try:
                return fetch_chapter("secret", "John", 3), opened
            finally:
                self.opened = opened

    def test_success_sends_token_and_options(self):
        verses, opened = self._fetch(return_value=FakeResponse({"passages": [SAMPLE]}))
        self.assertEqual(verses[0][0], 1)
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Token secret")
        self.assertIn("q=John+3", request.full_url)
        self.assertIn("include-headings=false", request.full_url)
        self.assertIn("include-footnotes=false", request.full_url)

    def test_throttled_carries_retry_after(self):
        with self.assertRaises(EsvThrottled) as caught:
            self._fetch(side_effect=http_error(429, {"Retry-After": "120"}))
        self.assertEqual(caught.exception.retry_after, 120)

    def test_throttled_without_header(self):
        with self.assertRaises(EsvThrottled) as caught:
            self._fetch(side_effect=http_error(429))
        self.assertIsNone(caught.exception.retry_after)

    def test_bad_key(self):
        for code in (401, 403):
            with self.subTest(code=code), self.assertRaises(EsvAuthError):
                self._fetch(side_effect=http_error(code))

    def test_other_failures(self):
        for failure in (http_error(500), urllib.error.URLError("down"), TimeoutError("slow")):
            with self.subTest(failure=failure), self.assertRaises(EsvError):
                self._fetch(side_effect=failure)

    def test_empty_or_odd_replies(self):
        for payload in ({"passages": []}, {"passages": [123]}, {}, []):
            with self.subTest(payload=payload), self.assertRaises(EsvError):
                self._fetch(return_value=FakeResponse(payload))

    def test_module_exports(self):
        self.assertTrue(issubclass(EsvThrottled, EsvError) and issubclass(EsvAuthError, EsvError))
        self.assertTrue(esv_client.ENDPOINT.startswith("https://api.esv.org/"))
