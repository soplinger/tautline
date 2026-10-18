# Tautline

**Offline guitar string tension calculator.** Describe a string set in one plain-text file and get each string's frequency, its tension in pounds-force and newtons, the total load on the neck, and the gauge you'd need for any target note, all from a single stdlib Python file.

Tautline uses the standard vibrating-string formula `T = 4 · L² · μ · f²`, where `L` is scale length, `μ` is mass per unit length and `f` is frequency. Everything is computed locally. There are no accounts, no network calls and no dependencies beyond the Python 3 standard library.

## Features

- **Plain-text set files.** Scale, name, default material and one line per string, with `#` comments, tabs, CRLF line endings and a UTF-8 BOM all accepted.
- **Per-string tension table** in pounds-force and newtons, with frequency, gauge and material for each string.
- **Neck load summary:** total, average, lowest and highest string, and the high/low spread ratio.
- **Scale lengths in inches, millimetres, centimetres or metres**, or a bare number read as inches.
- **Material densities** for plain steel, nickel-wound, phosphor bronze and nylon, with a `density=` override for any string.
- **Tune-down what-if** with `--drop N`, which lowers every string by N semitones and recalculates tension.
- **Custom concert pitch** with `--a4` (for example 432 Hz).
- **Scale override** with `--scale`, to test a longer or shorter neck without editing the file.
- **Limit gates for CI** with `--max-total`, `--max-string` and `--max-spread`. A breach exits with code 1 and prints each failed limit.
- **JSON output** with `--json` for dashboards and scripts.
- **Gauge solver** (`gauge`): give a target note and tension and get the required diameter in inches and millimetres.
- **Set comparison** (`compare`): compare two sets string by string, for example before and after a change of gauge.
- **Linter** (`lint`): every problem is reported as `file:line: error:` or `file:line: warning:`. Warnings cover duplicate notes, strings not ordered low to high, and gauges that get thicker as pitch rises.
- **Exit codes** that separate outcomes: 0 for OK, 1 for a gate failure, 2 for an input error.

## Quick start

Requires Python 3.8 or newer.

Save a set file, or use the included `examples/light-set.txt`:

```text
# Example light set, listed low to high. Gauges are illustrative.
name: Example light set
scale: 25.5in
material: nickel-wound

E2 0.046        # wound
A2 0.036        # wound
D3 0.026        # wound
G3 0.017 plain-steel
B3 0.011 plain-steel
E4 0.009 plain-steel
```

Then run:

```sh
python3 tautline.py check examples/light-set.txt
python3 tautline.py check examples/light-set.txt --drop 2 --max-total 120
python3 tautline.py gauge examples/light-set.txt --note A3 --tension 15lbf
```

## How to run

```sh
python3 tautline.py check FILE [--scale 24.75in] [--a4 440] [--drop N]
                               [--max-total LBF] [--max-string LBF] [--max-spread RATIO] [--json]
python3 tautline.py lint FILE
python3 tautline.py gauge FILE --note A3 --tension 15lbf [--material nylon] [--scale 25.5in]
python3 tautline.py compare BEFORE AFTER
```

Set file grammar:

| Line | Meaning |
| --- | --- |
| `name: TEXT` | Label shown in the report |
| `scale: 25.5in` | Scale length. Units `in`, `mm`, `cm`, `m`; a bare number means inches. Required. |
| `material: NAME` | Default material for strings that don't name one |
| `NOTE GAUGE [MATERIAL] [density=N]` | One string, lowest to highest. `NOTE` such as `E2`, `A#3` or `Bb4`. `GAUGE` in inches (`0.046`) or millimetres (`1.17mm`). |

## How to test

The suite uses only `unittest`, so it runs with or without pytest:

```sh
python3 -m unittest discover -s tests -t . -v
```

With pytest installed, `python3 -m pytest tests` works as well.

The tests cover note and unit parsing, the tension and gauge formulas (including the round trip and the drop scaling), parser errors with line numbers, lint warnings, scale overrides, gate messages, and the CLI exit codes for check, lint, gauge and compare.

## Notes

- Densities are approximate effective values for the material. Wound strings differ by manufacturer, so use `density=` when you have a measured figure.
- Scale lengths must be between 0.2 m and 1.5 m, and gauges between 0.1 mm and 10 mm. Anything outside these ranges is reported as an error.
- The tension figures are theoretical and ignore bending stiffness, which matters most for thick bass strings.
