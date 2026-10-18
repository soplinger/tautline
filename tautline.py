#!/usr/bin/env python3
"""Tautline: offline guitar string tension calculator.

Reads a plain-text string-set file and works out, for every string, its
frequency and its tension in pounds-force and newtons, then the load on the
neck. It can also solve for the gauge needed for a target note and tension,
gate a set against limits, and compare two sets.

Physics: a string of length L (m), mass per unit length mu (kg/m) and
frequency f (Hz) has tension T = 4 * L^2 * mu * f^2 (newtons).
"""
import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

EXIT_OK = 0
EXIT_GATE = 1
EXIT_ERROR = 2

LBF_PER_N = 0.2248089431

# Approximate effective densities in kg/m^3. Wound strings vary by maker, so
# override with density=NNNN on a string line when you know the real value.
MATERIALS = {
    "plain-steel": 7850.0,
    "nickel-wound": 8200.0,
    "phosphor-bronze": 8800.0,
    "nylon": 1140.0,
}
DEFAULT_MATERIAL = "plain-steel"

UNIT_METRES = {"in": 0.0254, "mm": 0.001, "cm": 0.01, "m": 1.0}
LENGTH_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(in|mm|cm|m)?$")
FORCE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(lbf|lb|n)?$")
NOTE_RE = re.compile(r"^([A-G])(#|b)?(-?\d+)$")
SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

SCALE_MIN_M, SCALE_MAX_M = 0.2, 1.5
DIAMETER_MIN_M, DIAMETER_MAX_M = 0.0001, 0.01


def parse_length(text: str) -> float:
    """Return metres. A bare number is read as inches."""
    match = LENGTH_RE.match(text.strip().lower())
    if not match:
        raise ValueError(f"not a length: {text!r} (try 25.5in, 648mm or 65cm)")
    value, unit = match.groups()
    return float(value) * UNIT_METRES[unit or "in"]


def parse_force(text: str) -> float:
    """Return newtons. A bare number is read as pounds-force."""
    match = FORCE_RE.match(text.strip().lower())
    if not match:
        raise ValueError(f"not a force: {text!r} (try 15lbf or 70N)")
    value, unit = match.groups()
    newtons = float(value)
    if unit != "n":
        newtons /= LBF_PER_N
    return newtons


def parse_note(text: str) -> int:
    """Return the MIDI number for a note such as E2, A#3 or Bb4."""
    cleaned = text.strip()
    match = NOTE_RE.match(cleaned[:1].upper() + cleaned[1:])
    if not match:
        raise ValueError(f"not a note: {text!r} (try E2, A#3 or Bb4)")
    letter, accidental, octave = match.groups()
    shift = {"#": 1, "b": -1}.get(accidental or "", 0)
    return (int(octave) + 1) * 12 + SEMITONE[letter] + shift


def note_name(midi: int) -> str:
    return f"{SHARP_NAMES[midi % 12]}{midi // 12 - 1}"


def frequency(midi: int, a4: float = 440.0) -> float:
    return a4 * 2 ** ((midi - 69) / 12)


def tension_n(scale_m: float, diameter_m: float, density: float, freq_hz: float) -> float:
    mu = density * math.pi * (diameter_m / 2) ** 2
    return 4 * scale_m ** 2 * mu * freq_hz ** 2


def diameter_for(scale_m: float, density: float, freq_hz: float, tension: float) -> float:
    mu = tension / (4 * scale_m ** 2 * freq_hz ** 2)
    return math.sqrt(4 * mu / (math.pi * density))


@dataclass
class StringSpec:
    line: int
    note: str
    midi: int
    diameter_m: float
    material: str
    density: float


@dataclass
class StringSet:
    path: str
    name: str = "Unnamed set"
    scale_m: Optional[float] = None
    default_material: str = DEFAULT_MATERIAL
    strings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass(frozen=True)
class Row:
    number: int
    note: str
    frequency_hz: float
    diameter_m: float
    material: str
    tension_n: float

    @property
    def tension_lbf(self) -> float:
        return self.tension_n * LBF_PER_N


def _problem(result: StringSet, lineno, level: str, message: str) -> None:
    where = f"{result.path}:{lineno}" if lineno else result.path
    target = result.errors if level == "error" else result.warnings
    target.append(f"{where}: {level}: {message}")


def _parse_setting(result: StringSet, lineno: int, line: str) -> None:
    key, _, value = line.partition(":")
    key = key.strip().lower()
    value = value.strip()
    if key == "name":
        result.name = value or result.name
    elif key == "scale":
        try:
            result.scale_m = parse_length(value)
        except ValueError as exc:
            _problem(result, lineno, "error", str(exc))
    elif key == "material":
        if value in MATERIALS:
            result.default_material = value
        else:
            _problem(result, lineno, "error",
                     f"unknown material {value!r}; known: {', '.join(MATERIALS)}")
    else:
        _problem(result, lineno, "error", f"unknown setting {key!r}")


def _parse_string(result: StringSet, lineno: int, line: str, pending: list) -> None:
    tokens = line.split()
    if len(tokens) < 2:
        _problem(result, lineno, "error", "expected NOTE GAUGE [MATERIAL] [density=N]")
        return
    try:
        midi = parse_note(tokens[0])
    except ValueError as exc:
        _problem(result, lineno, "error", str(exc))
        return
    try:
        diameter = parse_length(tokens[1])
    except ValueError:
        _problem(result, lineno, "error",
                 f"bad gauge {tokens[1]!r}; use inches (0.046) or mm (1.17mm)")
        return
    if not DIAMETER_MIN_M <= diameter <= DIAMETER_MAX_M:
        _problem(result, lineno, "error",
                 f"gauge {tokens[1]!r} is outside 0.1 mm to 10 mm")
        return
    material = None
    density = None
    valid = True
    for token in tokens[2:]:
        if token.lower().startswith("density="):
            try:
                density = float(token.split("=", 1)[1])
            except ValueError:
                density = -1.0
            if density <= 0:
                _problem(result, lineno, "error", f"bad density {token!r}")
                valid = False
        elif token in MATERIALS:
            material = token
        else:
            _problem(result, lineno, "error", f"unknown token {token!r}")
            valid = False
    if valid:
        pending.append((lineno, tokens[0], midi, diameter, material, density))


def _lint_order(result: StringSet) -> None:
    for previous, current in zip(result.strings, result.strings[1:]):
        if current.midi == previous.midi:
            _problem(result, current.line, "warning",
                     f"duplicate note {current.note} (also on line {previous.line})")
        elif current.midi < previous.midi:
            _problem(result, current.line, "warning",
                     f"{current.note} is lower than the string before it; "
                     "list strings from lowest to highest pitch")
        elif current.diameter_m > previous.diameter_m:
            _problem(result, current.line, "warning",
                     f"gauge is thicker than the lower string on line {previous.line}")


def parse_set(text: str, path: str = "<input>", scale_m: Optional[float] = None) -> StringSet:
    result = StringSet(path=path)
    pending = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" in line:
            _parse_setting(result, lineno, line)
        else:
            _parse_string(result, lineno, line, pending)
    if scale_m is not None:
        result.scale_m = scale_m
    if result.scale_m is None:
        _problem(result, None, "error", "missing 'scale:' setting")
    elif not SCALE_MIN_M <= result.scale_m <= SCALE_MAX_M:
        _problem(result, None, "error",
                 f"scale {result.scale_m:.3f} m is outside 0.2 to 1.5 m")
    if not pending:
        _problem(result, None, "error", "no strings defined")
    for lineno, note, midi, diameter, material, density in pending:
        material = material or result.default_material
        result.strings.append(StringSpec(
            line=lineno, note=note, midi=midi, diameter_m=diameter, material=material,
            density=density or MATERIALS[material]))
    _lint_order(result)
    return result


def analyse(sset: StringSet, a4: float = 440.0, drop: int = 0) -> list:
    rows = []
    for number, string in enumerate(sset.strings, start=1):
        midi = string.midi - drop
        freq = frequency(midi, a4)
        tension = tension_n(sset.scale_m, string.diameter_m, string.density, freq)
        rows.append(Row(number, note_name(midi), freq, string.diameter_m,
                        string.material, tension))
    return rows


def summarise(rows: list) -> dict:
    total = sum(row.tension_n for row in rows)
    low = min(rows, key=lambda row: row.tension_n)
    high = max(rows, key=lambda row: row.tension_n)
    return {
        "total_n": total,
        "total_lbf": total * LBF_PER_N,
        "average_lbf": total * LBF_PER_N / len(rows),
        "lowest_note": low.note,
        "lowest_lbf": low.tension_lbf,
        "highest_note": high.note,
        "highest_lbf": high.tension_lbf,
        "spread": high.tension_n / low.tension_n,
    }


def gate_failures(rows: list, summary: dict, max_total=None, max_string=None,
                  max_spread=None) -> list:
    failures = []
    if max_total is not None and summary["total_lbf"] > max_total:
        failures.append(f"total {summary['total_lbf']:.2f} lbf is over the "
                        f"{max_total:g} lbf limit")
    if max_string is not None:
        for row in rows:
            if row.tension_lbf > max_string:
                failures.append(f"string {row.number} ({row.note}) at "
                                f"{row.tension_lbf:.2f} lbf is over {max_string:g} lbf")
    if max_spread is not None and summary["spread"] > max_spread:
        failures.append(f"spread {summary['spread']:.2f}x is over {max_spread:g}x")
    return failures


def render_table(rows: list) -> str:
    lines = [f"{'#':>2}  {'Note':<5} {'Freq Hz':>8} {'Gauge in':>9}  "
             f"{'Material':<15} {'lbf':>7} {'N':>7}"]
    for row in rows:
        lines.append(f"{row.number:>2}  {row.note:<5} {row.frequency_hz:>8.2f} "
                     f"{row.diameter_m / UNIT_METRES['in']:>9.4f}  "
                     f"{row.material:<15} {row.tension_lbf:>7.2f} {row.tension_n:>7.2f}")
    return "\n".join(lines)


def load(path: str, scale: Optional[str] = None) -> StringSet:
    with open(path, encoding="utf-8-sig") as handle:
        text = handle.read()
    scale_m = parse_length(scale) if scale else None
    return parse_set(text, path, scale_m)


def _report(sset: StringSet) -> bool:
    for line in sset.warnings:
        print(line, file=sys.stderr)
    for line in sset.errors:
        print(line, file=sys.stderr)
    return not sset.errors


def cmd_check(args) -> int:
    sset = load(args.file, args.scale)
    if not _report(sset):
        return EXIT_ERROR
    rows = analyse(sset, args.a4, args.drop)
    summary = summarise(rows)
    failures = gate_failures(rows, summary, args.max_total, args.max_string, args.max_spread)
    if args.json:
        payload = {
            "name": sset.name,
            "scale_m": round(sset.scale_m, 6),
            "a4_hz": args.a4,
            "drop_semitones": args.drop,
            "strings": [{
                "number": row.number,
                "note": row.note,
                "frequency_hz": round(row.frequency_hz, 4),
                "gauge_in": round(row.diameter_m / UNIT_METRES["in"], 5),
                "material": row.material,
                "tension_lbf": round(row.tension_lbf, 4),
                "tension_n": round(row.tension_n, 4),
            } for row in rows],
            "summary": {key: round(value, 4) if isinstance(value, float) else value
                        for key, value in summary.items()},
            "gate_failures": failures,
        }
        print(json.dumps(payload, indent=2))
    else:
        scale_in = sset.scale_m / UNIT_METRES["in"]
        print(sset.name)
        print(f"Scale {scale_in:.2f} in ({sset.scale_m:.4f} m) | A4 = {args.a4:g} Hz | "
              f"tuned down {args.drop} semitone(s)")
        print()
        print(render_table(rows))
        print()
        print(f"Total    {summary['total_lbf']:.2f} lbf ({summary['total_n']:.1f} N), "
              f"average {summary['average_lbf']:.2f} lbf")
        print(f"Lowest   {summary['lowest_note']} at {summary['lowest_lbf']:.2f} lbf")
        print(f"Highest  {summary['highest_note']} at {summary['highest_lbf']:.2f} lbf")
        print(f"Spread   {summary['spread']:.2f}x")
        for failure in failures:
            print(f"GATE FAILED: {failure}")
    return EXIT_GATE if failures else EXIT_OK


def cmd_lint(args) -> int:
    sset = load(args.file)
    for line in sset.errors + sset.warnings:
        print(line)
    if sset.errors:
        print(f"{len(sset.errors)} error(s), {len(sset.warnings)} warning(s)")
        return EXIT_ERROR
    print(f"ok: {len(sset.strings)} strings, {len(sset.warnings)} warning(s)")
    return EXIT_OK


def cmd_gauge(args) -> int:
    sset = load(args.file, args.scale)
    if not _report(sset):
        return EXIT_ERROR
    midi = parse_note(args.note)
    freq = frequency(midi, args.a4)
    tension = parse_force(args.tension)
    material = args.material or sset.default_material
    diameter = diameter_for(sset.scale_m, MATERIALS[material], freq, tension)
    scale_in = sset.scale_m / UNIT_METRES["in"]
    print(f"{note_name(midi)} ({freq:.2f} Hz) at {tension * LBF_PER_N:.2f} lbf on a "
          f"{scale_in:.2f} in {material} scale needs "
          f"{diameter / UNIT_METRES['in']:.4f} in ({diameter * 1000:.3f} mm)")
    if not DIAMETER_MIN_M <= diameter <= DIAMETER_MAX_M:
        print("warning: that gauge is outside 0.1 mm to 10 mm; check the target")
    return EXIT_OK


def cmd_compare(args) -> int:
    left = load(args.before)
    right = load(args.after)
    left_ok = _report(left)
    right_ok = _report(right)
    if not (left_ok and right_ok):
        return EXIT_ERROR
    if len(left.strings) != len(right.strings):
        print(f"tautline: error: sets have {len(left.strings)} and "
              f"{len(right.strings)} strings; they must match", file=sys.stderr)
        return EXIT_ERROR
    before = analyse(left)
    after = analyse(right)
    print(f"{'#':>2}  {'Before':<5} {'lbf':>7}    {'After':<5} {'lbf':>7}  {'Change':>8}")
    for old, new in zip(before, after):
        change = new.tension_lbf - old.tension_lbf
        print(f"{old.number:>2}  {old.note:<5} {old.tension_lbf:>7.2f}    "
              f"{new.note:<5} {new.tension_lbf:>7.2f}  {change:>+8.2f}")
    total_change = sum(new.tension_lbf for new in after) - sum(old.tension_lbf for old in before)
    print(f"Total change {total_change:+.2f} lbf")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tautline", description="Offline guitar string tension calculator.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="tension table, totals and limit gates")
    check.add_argument("file")
    check.add_argument("--scale", help="override the scale length, e.g. 24.75in")
    check.add_argument("--a4", type=float, default=440.0, help="pitch of A4 in Hz")
    check.add_argument("--drop", type=int, default=0, metavar="N",
                       help="tune every string down N semitones")
    check.add_argument("--max-total", type=float, metavar="LBF",
                       help="fail if total neck load exceeds this")
    check.add_argument("--max-string", type=float, metavar="LBF",
                       help="fail if any single string exceeds this")
    check.add_argument("--max-spread", type=float, metavar="RATIO",
                       help="fail if highest/lowest tension exceeds this")
    check.add_argument("--json", action="store_true", help="print machine-readable JSON")
    check.set_defaults(func=cmd_check)

    lint = sub.add_parser("lint", help="check a set file for errors and warnings")
    lint.add_argument("file")
    lint.set_defaults(func=cmd_lint)

    gauge = sub.add_parser("gauge", help="solve the gauge for a note and tension")
    gauge.add_argument("file", help="set file supplying scale and default material")
    gauge.add_argument("--note", required=True, help="target note, e.g. A3")
    gauge.add_argument("--tension", required=True, help="target tension, e.g. 15lbf or 70N")
    gauge.add_argument("--scale", help="override the scale length")
    gauge.add_argument("--material", choices=list(MATERIALS), help="override the material")
    gauge.add_argument("--a4", type=float, default=440.0, help="pitch of A4 in Hz")
    gauge.set_defaults(func=cmd_gauge)

    compare = sub.add_parser("compare", help="compare two sets string by string")
    compare.add_argument("before")
    compare.add_argument("after")
    compare.set_defaults(func=cmd_compare)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as exc:
        print(f"tautline: error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
