"""core.test_text: tests for the lowercase-store / title-case-show helpers."""
from django.test import SimpleTestCase

from apps.core.text import clean_text, title_case


class CleanTextTests(SimpleTestCase):
    def test_trims_collapses_and_lowercases(self):
        self.assertEqual(clean_text("  Santa   ROSA "), "santa rosa")

    def test_none_becomes_empty(self):
        self.assertEqual(clean_text(None), "")


class TitleCaseTests(SimpleTestCase):
    def test_basic_names(self):
        self.assertEqual(title_case("sta. rosa"), "Sta. Rosa")
        self.assertEqual(title_case("  nueva   ecija "), "Nueva Ecija")

    def test_small_words_stay_lowercase_except_first(self):
        self.assertEqual(title_case("city of manila"), "City of Manila")
        self.assertEqual(title_case("of mice"), "Of Mice")

    def test_apostrophes_and_accents(self):
        self.assertEqual(title_case("bataan's end"), "Bataan's End")
        self.assertEqual(title_case("dasmariñas"), "Dasmariñas")
        self.assertEqual(title_case("santo niño"), "Santo Niño")

    def test_roman_numerals_and_acronyms(self):
        self.assertEqual(title_case("poblacion ii"), "Poblacion II")
        self.assertEqual(title_case("jil sta. rosa"), "JIL Sta. Rosa")

    def test_digits(self):
        self.assertEqual(title_case("12-a"), "12-A")
        self.assertEqual(title_case("3rd street"), "3rd Street")

    def test_none_becomes_empty(self):
        self.assertEqual(title_case(None), "")
