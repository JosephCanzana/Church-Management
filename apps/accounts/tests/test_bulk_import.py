"""Tests for the bulk add parser and rules (apps.accounts.bulk_import). No database needed.

Run: docker compose exec web python manage.py test apps.accounts.tests.test_bulk_import
"""
from datetime import date
import unittest

from apps.accounts.bulk_import import parser, rules

TODAY = date(2026, 10, 26)
EXT_HEADER = "name,country,province,municipality,barangay,postal_code,street,building_number\n"
PPL_HEADER = "first_name,middle_name,last_name,suffix,gender,birth_date,extension,phone_number\n"
EXTENSIONS = {"cabanatuan": [(1, False)], "old branch": [(2, True)], "twin": [(3, False), (4, False)]}


def ext_rows(body, header=EXT_HEADER):
    return parser.parse(header + body, rules.EXTENSION_SPEC)


def ppl_results(body, header=PPL_HEADER, **kw):
    parsed = parser.parse(header + body, rules.PEOPLE_SPEC)
    assert not parsed.problems, parsed.problems
    return rules.validate_people(parsed.rows, kw.pop("extensions", EXTENSIONS), TODAY, **kw)


class ParserTests(unittest.TestCase):
    def test_xlsx_and_binary_are_refused(self):
        with self.assertRaises(parser.FileProblem):
            parser.decode(b"PK\x03\x04 zipped excel")
        with self.assertRaises(parser.FileProblem):
            parser.decode(b"")

    def test_bom_and_windows_1252(self):
        text, notes = parser.decode("\ufeffname\nPeña".encode("utf-8"))
        self.assertTrue(text.startswith("name"))
        text, notes = parser.decode("name\nPe\xf1a".encode("cp1252"))
        self.assertIn("Peña", text)
        self.assertTrue(notes)

    def test_semicolon_and_tab_delimiters(self):
        for sep in (";", "\t"):
            parsed = parser.parse(sep.join(["first_name", "last_name", "gender", "extension"]) + "\n"
                                  + sep.join(["Ana", "Reyes", "f", "Cabanatuan"]), rules.PEOPLE_SPEC)
            self.assertEqual(parsed.problems, [])
            self.assertEqual(parsed.rows[0].cells["last_name"], "Reyes")

    def test_header_aliases_and_ignored_columns(self):
        parsed = parser.parse("First Name,Surname,Sex,Branch,Favourite Food\nAna,Reyes,F,Cabanatuan,rice", rules.PEOPLE_SPEC)
        self.assertEqual(parsed.problems, [])
        self.assertEqual(parsed.rows[0].cells["gender"], "F")
        self.assertTrue(any("Favourite Food" in n for n in parsed.notes))

    def test_missing_and_duplicate_columns(self):
        parsed = parser.parse("first_name,last_name\nAna,Reyes", rules.PEOPLE_SPEC)
        self.assertTrue(any("Missing" in p for p in parsed.problems))
        parsed = parser.parse("first_name,first_name,last_name,gender,extension\na,b,c,d,e", rules.PEOPLE_SPEC)
        self.assertTrue(any("twice" in p for p in parsed.problems))

    def test_blank_lines_skipped_and_line_numbers_kept(self):
        parsed = parser.parse("first_name,last_name,gender,extension\n\nAna,Reyes,f,x\n,,,\nBo,Cruz,m,x\n", rules.PEOPLE_SPEC)
        self.assertEqual([r.line for r in parsed.rows], [3, 5])

    def test_ragged_rows_are_flagged(self):
        parsed = parser.parse("first_name,last_name,gender,extension\nAna,Reyes,f\nBo,Cruz,m,x,extra", rules.PEOPLE_SPEC)
        self.assertTrue(parsed.rows[0].shape_error)
        self.assertTrue(parsed.rows[1].shape_error)

    def test_unclosed_quote_and_row_cap(self):
        parsed = parser.parse('first_name,last_name,gender,extension\n"Ana,Reyes,f,x', rules.PEOPLE_SPEC)
        self.assertTrue(parsed.problems)
        body = "".join(f"A{i},B,f,x\n" for i in range(parser.MAX_ROWS + 1))
        parsed = parser.parse("first_name,last_name,gender,extension\n" + body, rules.PEOPLE_SPEC)
        self.assertTrue(parsed.problems and not parsed.rows)

    def test_invisible_characters_removed(self):
        self.assertEqual(parser.clean_cell("\u200bAna\u00a0  Reyes\u200b"), "Ana Reyes")


class ExtensionRuleTests(unittest.TestCase):
    GOOD = "Cabanatuan,Philippines,Nueva Ecija,Cabanatuan City,Sangitan,3100,Rizal St,12\n"

    def run_rules(self, body, existing=None, check=None):
        return rules.validate_extensions(ext_rows(body).rows, existing or {}, check)

    def test_good_row(self):
        self.assertEqual(self.run_rules(self.GOOD)[0].status, "ok")

    def test_required_fields(self):
        r = self.run_rules("Branch,Philippines,,,,,,\n")[0]
        self.assertEqual(r.status, "error")
        self.assertGreaterEqual(len(r.issues), 4)

    def test_duplicate_in_database_ignores_case_and_spacing(self):
        r = self.run_rules("  CABANATUAN  ,PH,a,b,c,3100,,\n", existing={"cabanatuan": False})[0]
        self.assertEqual(r.status, "duplicate")

    def test_archived_duplicate_says_restore(self):
        r = self.run_rules(self.GOOD, existing={"cabanatuan": True})[0]
        self.assertIn("restore", r.reasons[0])

    def test_duplicate_inside_file_points_to_first_line(self):
        a, b = self.run_rules(self.GOOD + self.GOOD)
        self.assertEqual((a.status, b.status), ("ok", "duplicate"))
        self.assertIn("line 2", b.reasons[0])

    def test_failed_row_does_not_block_its_corrected_twin(self):
        bad = "Cabanatuan,Philippines,,,,,,\n"
        a, b = self.run_rules(bad + self.GOOD)
        self.assertEqual((a.status, b.status), ("error", "ok"))

    def test_formula_injection_and_dropped_zero(self):
        r = self.run_rules("=cmd|'/c calc'!A1,PH,a,b,c,3100,,\n")[0]
        self.assertIn("formula", r.reasons[0])
        r = self.run_rules("X,Philippines,a,b,c,900,,\n")[0]
        self.assertIn("leading zero", " ".join(r.reasons))

    def test_external_check_errors_are_shown(self):
        r = self.run_rules(self.GOOD, check=lambda d: ["Postal code must be 4 digits."])[0]
        self.assertEqual(r.status, "error")


class PeopleRuleTests(unittest.TestCase):
    def test_good_row_and_data_shape(self):
        r = ppl_results("Ana,Lim,Reyes,,F,2001-03-04,Cabanatuan,0917 123 4567\n")[0]
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.data["gender"], "female")
        self.assertEqual(r.data["extension_id"], 1)
        self.assertEqual(r.data["phone_number"], "09171234567")
        self.assertEqual(r.data["birth_date"], date(2001, 3, 4))

    def test_names_digits_placeholders_formulas(self):
        r = ppl_results("J0hn,,N/A,,m,2000-01-01,Cabanatuan,\n")[0]
        self.assertEqual(r.status, "error")
        r = ppl_results("=SUM(A1),,Cruz,,m,2000-01-01,Cabanatuan,\n")[0]
        self.assertIn("formula", " ".join(r.reasons))
        r = ppl_results("Ana,N/A,Cruz,,f,2000-01-01,Cabanatuan,\n")[0]
        self.assertEqual((r.status, r.data["middle_name"]), ("warning", ""))

    def test_dates(self):
        for text, ok in [("2001-03-04", True), ("03/04/2001", True), ("25/12/2001", True), ("2001-02-30", False),
                         ("36526", False), ("03/04/01", False), ("2999-01-01", False), ("1800-01-01", False),
                         ("not a date", False), ("12 Mar 2001", True)]:
            d, _, err = rules.parse_date(text, TODAY)
            self.assertEqual(bool(d) and not err, ok, text)

    def test_ambiguous_date_warns_with_month_first(self):
        d, warn, err = rules.parse_date("03/04/2001", TODAY)
        self.assertEqual((d, bool(warn)), (date(2001, 3, 4), True))

    def test_blank_birth_date_is_a_warning(self):
        self.assertEqual(ppl_results("Ana,,Reyes,,f,,Cabanatuan,\n")[0].status, "warning")

    def test_gender_and_type_and_role(self):
        self.assertEqual(ppl_results("Ana,,Reyes,,x,2000-01-01,Cabanatuan,\n")[0].status, "error")
        header = "first_name,last_name,gender,extension,person_type,role\n"
        r = ppl_results("Ana,Reyes,f,Cabanatuan,Member (no account),\n", header=header)[0]
        self.assertEqual(r.data["person_type"], "member_record")
        r = ppl_results("Ana,Reyes,f,Cabanatuan,guest,coordinator\n", header=header)[0]
        self.assertIn("members only", " ".join(r.reasons))
        r = ppl_results("Ana,Reyes,f,Cabanatuan,visitor,\n", header=header)[0]
        self.assertEqual(r.status, "error")

    def test_extension_typo_archived_ambiguous_missing(self):
        r = ppl_results("Ana,,Reyes,,f,2000-01-01,Cabanatun,\n")[0]
        self.assertIn("Did you mean “cabanatuan”", " ".join(r.reasons))
        self.assertIn("archived", " ".join(ppl_results("Ana,,Reyes,,f,2000-01-01,Old Branch,\n")[0].reasons))
        self.assertIn("more than one", " ".join(ppl_results("Ana,,Reyes,,f,2000-01-01,Twin,\n")[0].reasons))
        self.assertEqual(ppl_results("Ana,,Reyes,,f,2000-01-01,,\n")[0].status, "error")

    def test_phone_anomalies(self):
        self.assertEqual(ppl_results("A,,B,,f,2000-01-01,Cabanatuan,+63 917 123 4567\n")[0].data["phone_number"], "09171234567")
        r = ppl_results("A,,B,,f,2000-01-01,Cabanatuan,9171234567\n")[0]
        self.assertEqual((r.status, r.data["phone_number"]), ("warning", "09171234567"))
        self.assertEqual(ppl_results("A,,B,,f,2000-01-01,Cabanatuan,9.17E+10\n")[0].status, "error")
        self.assertEqual(ppl_results("A,,B,,f,2000-01-01,Cabanatuan,call me\n")[0].status, "error")

    def test_in_file_duplicates_follow_the_birth_date_rule(self):
        same, other, blank = ppl_results(
            "Ana,,Reyes,,f,2000-01-01,Cabanatuan,\n"
            "ana,,REYES,,f,2000-01-01,Cabanatuan,\n"
            "Ana,,Reyes,,f,1999-05-05,Cabanatuan,\n"
        )
        self.assertEqual(same.status, "ok")
        self.assertEqual(other.status, "duplicate")  # same name and birth date
        self.assertEqual(blank.status, "ok")  # different birth date: a different person
        a, b = ppl_results("Ana,,Reyes,,f,,Cabanatuan,\nAna,,Reyes,,f,2000-01-01,Cabanatuan,\n")
        self.assertEqual(b.status, "duplicate")  # a missing birth date cannot rule a match out

    def test_database_duplicate_and_idempotent_second_run(self):
        r = ppl_results("Ana,,Reyes,,f,2000-01-01,Cabanatuan,\n", db_duplicates=lambda d: ["Already exists as Account ID 12026."])[0]
        self.assertEqual(r.status, "duplicate")

    def test_ragged_row_is_an_error(self):
        parsed = parser.parse("first_name,last_name,gender,extension\nAna,Reyes,f\n", rules.PEOPLE_SPEC)
        self.assertEqual(rules.validate_people(parsed.rows, EXTENSIONS, TODAY)[0].status, "error")

    def test_summary(self):
        res = ppl_results("Ana,,Reyes,,f,2000-01-01,Cabanatuan,\nAna,,Reyes,,f,2000-01-01,Cabanatuan,\nBad,,,,,,,\n")
        s = rules.summarize(res)
        self.assertEqual((s["ok"], s["duplicate"], s["error"], s["importable"]), (1, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
