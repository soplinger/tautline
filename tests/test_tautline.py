import contextlib
import io
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tautline  # noqa: E402

EXAMPLE = ROOT / "examples" / "light-set.txt"


def _write(directory: str, name: str, text: str) -> str:
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


class NoteAndUnitTests(unittest.TestCase):
    def test_note_numbers(self):
        self.assertEqual(tautline.parse_note("E2"), 40)
        self.assertEqual(tautline.parse_note("A4"), 69)
        self.assertEqual(tautline.parse_note("Bb3"), tautline.parse_note("A#3"))
        self.assertEqual(tautline.parse_note("e2"), 40)

    def test_bad_note_raises(self):
        with self.assertRaises(ValueError):
            tautline.parse_note("H4")

    def test_note_name_round_trip(self):
        self.assertEqual(tautline.note_name(40), "E2")
        self.assertEqual(tautline.note_name(70), "A#4")

    def test_frequency_respects_reference_pitch(self):
        self.assertAlmostEqual(tautline.frequency(69), 440.0, places=9)
        self.assertAlmostEqual(tautline.frequency(69, 432.0), 432.0, places=9)
        self.assertAlmostEqual(tautline.frequency(81), 880.0, places=9)

    def test_length_units(self):
        self.assertAlmostEqual(tautline.parse_length("25.5in"), 25.5 * 0.0254)
        self.assertAlmostEqual(tautline.parse_length("648mm"), 0.648)
        self.assertAlmostEqual(tautline.parse_length("65cm"), 0.65)
        self.assertAlmostEqual(tautline.parse_length("0.046"), 0.046 * 0.0254)
        with self.assertRaises(ValueError):
            tautline.parse_length("long")

    def test_force_units(self):
        self.assertAlmostEqual(tautline.parse_force("70N"), 70.0)
        self.assertAlmostEqual(tautline.parse_force("15lbf") * tautline.LBF_PER_N, 15.0)
        self.assertAlmostEqual(tautline.parse_force("15") * tautline.LBF_PER_N, 15.0)
        with self.assertRaises(ValueError):
            tautline.parse_force("heavy")


class PhysicsTests(unittest.TestCase):
    def test_known_low_e_tension(self):
        # 25.5 in scale, 0.046 in steel-ish string tuned to E2 (about 82.41 Hz)
        newtons = tautline.tension_n(25.5 * 0.0254, 0.046 * 0.0254,
                                     tautline.MATERIALS["plain-steel"], 82.4069)
        self.assertAlmostEqual(newtons, 95.9, delta=0.5)

    def test_tension_grows_with_frequency_squared(self):
        base = tautline.tension_n(0.65, 0.001, 7850.0, 100.0)
        doubled = tautline.tension_n(0.65, 0.001, 7850.0, 200.0)
        self.assertAlmostEqual(doubled / base, 4.0, places=9)

    def test_diameter_solves_back_to_tension(self):
        scale = 0.6477
        freq = tautline.frequency(tautline.parse_note("A3"))
        target = tautline.parse_force("15lbf")
        diameter = tautline.diameter_for(scale, 7850.0, freq, target)
        back = tautline.tension_n(scale, diameter, 7850.0, freq)
        self.assertAlmostEqual(back, target, places=6)

    def test_drop_tuning_scales_tension_by_power_of_two(self):
        sset = tautline.parse_set(EXAMPLE.read_text(encoding="utf-8"), str(EXAMPLE))
        standard = tautline.analyse(sset)
        dropped = tautline.analyse(sset, drop=2)
        for before, after in zip(standard, dropped):
            ratio = after.tension_n / before.tension_n
            self.assertAlmostEqual(ratio, 2 ** (-2 / 6), places=9)
        self.assertEqual(dropped[0].note, "D2")


class ParserTests(unittest.TestCase):
    def test_example_file_parses_cleanly(self):
        sset = tautline.parse_set(EXAMPLE.read_text(encoding="utf-8"), str(EXAMPLE))
        self.assertEqual(sset.errors, [])
        self.assertEqual(sset.warnings, [])
        self.assertEqual(len(sset.strings), 6)
        self.assertEqual(sset.name, "Example light set")
        self.assertAlmostEqual(sset.scale_m, 25.5 * 0.0254)
        self.assertEqual(sset.strings[3].material, "plain-steel")
        self.assertEqual(sset.strings[0].material, "nickel-wound")

    def test_crlf_tabs_and_comments_are_tolerated(self):
        text = "scale:\t25.5in  # long\r\nE2\t0.046\tnickel-wound # wound\r\n\r\nA2 0.036\r\n"
        sset = tautline.parse_set(text, "t.txt")
        self.assertEqual(sset.errors, [])
        self.assertEqual([s.note for s in sset.strings], ["E2", "A2"])

    def test_missing_scale_is_an_error(self):
        sset = tautline.parse_set("E2 0.046\n", "t.txt")
        self.assertTrue(any("missing 'scale:' setting" in e for e in sset.errors))

    def test_bad_lines_carry_line_numbers(self):
        text = "scale: 25.5in\nE2 0.046\nH9 0.030\n"
        sset = tautline.parse_set(text, "set.txt")
        self.assertEqual(len(sset.errors), 1)
        self.assertTrue(sset.errors[0].startswith("set.txt:3: error:"))
        self.assertIn("not a note", sset.errors[0])

    def test_unknown_material_token_and_gauge_range(self):
        text = ("scale: 25.5in\n"
                "material: mystery\n"
                "E2 0.046 rubber\n"
                "A2 50mm\n"
                "D3 0.026 density=-4\n")
        sset = tautline.parse_set(text, "t.txt")
        messages = "\n".join(sset.errors)
        self.assertIn("t.txt:2: error: unknown material 'mystery'", messages)
        self.assertIn("t.txt:3: error: unknown token 'rubber'", messages)
        self.assertIn("t.txt:4: error: gauge '50mm' is outside", messages)
        self.assertIn("t.txt:5: error: bad density", messages)

    def test_string_order_and_duplicate_warnings(self):
        text = "scale: 25.5in\nE4 0.010\nE2 0.046\nE2 0.046\n"
        sset = tautline.parse_set(text, "t.txt")
        self.assertEqual(sset.errors, [])
        self.assertTrue(any("lower than the string before it" in w for w in sset.warnings))
        self.assertTrue(any("duplicate note E2" in w for w in sset.warnings))

    def test_scale_override_replaces_file_value(self):
        sset = tautline.parse_set("E2 0.046\n", "t.txt", scale_m=tautline.parse_length("24.75in"))
        self.assertEqual(sset.errors, [])
        self.assertAlmostEqual(sset.scale_m, 24.75 * 0.0254)


class GateAndSummaryTests(unittest.TestCase):
    def setUp(self):
        sset = tautline.parse_set(EXAMPLE.read_text(encoding="utf-8"), str(EXAMPLE))
        self.rows = tautline.analyse(sset)
        self.summary = tautline.summarise(self.rows)

    def test_summary_spread_and_extremes(self):
        self.assertAlmostEqual(self.summary["spread"],
                               self.summary["highest_lbf"] / self.summary["lowest_lbf"])
        self.assertEqual(self.summary["lowest_note"], "B3")
        self.assertEqual(self.summary["highest_note"], "A2")
        total = sum(row.tension_lbf for row in self.rows)
        self.assertTrue(math.isclose(self.summary["total_lbf"], total))

    def test_gates_report_each_limit(self):
        failures = tautline.gate_failures(self.rows, self.summary,
                                          max_total=1.0, max_string=1.0, max_spread=1.0)
        self.assertTrue(any("over the 1 lbf limit" in f for f in failures))
        self.assertEqual(sum("string " in f for f in failures), len(self.rows))
        self.assertTrue(any("spread" in f for f in failures))
        self.assertEqual(tautline.gate_failures(self.rows, self.summary), [])


class CommandTests(unittest.TestCase):
    def _run(self, *argv):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tautline.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_check_json_and_gate_exit_codes(self):
        code, out, _ = self._run("check", str(EXAMPLE), "--json")
        self.assertEqual(code, tautline.EXIT_OK)
        self.assertIn('"note": "E2"', out)
        code, out, _ = self._run("check", str(EXAMPLE), "--max-total", "1")
        self.assertEqual(code, tautline.EXIT_GATE)
        self.assertIn("GATE FAILED", out)

    def test_lint_reports_errors_with_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "bad.txt", "scale: 25.5in\nE2 0.046 rubber\n")
            code, out, _ = self._run("lint", path)
        self.assertEqual(code, tautline.EXIT_ERROR)
        self.assertIn("bad.txt:2: error: unknown token 'rubber'", out)

    def test_gauge_command_prints_diameter(self):
        code, out, _ = self._run("gauge", str(EXAMPLE), "--note", "A3", "--tension", "15lbf")
        self.assertEqual(code, tautline.EXIT_OK)
        self.assertIn("A3 (220.00 Hz)", out)
        self.assertIn(" in (", out)

    def test_compare_mismatched_sets_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            short = _write(tmp, "short.txt", "scale: 25.5in\nE2 0.046\n")
            code, _, err = self._run("compare", str(EXAMPLE), short)
        self.assertEqual(code, tautline.EXIT_ERROR)
        self.assertIn("must match", err)

    def test_compare_same_set_shows_zero_change(self):
        code, out, _ = self._run("compare", str(EXAMPLE), str(EXAMPLE))
        self.assertEqual(code, tautline.EXIT_OK)
        self.assertIn("Total change +0.00 lbf", out)


if __name__ == "__main__":
    unittest.main()
