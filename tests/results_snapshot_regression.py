"""Core results guard: solve a set of representative buildings and compare the numbers.

Each scenario is the factory-default preset with a few inputs changed. After a solve
the design dossier is reduced to its numbers, pass/fail flags and selected sections
(report wording, labels and notes are dropped) and compared with the saved baseline
in tests/fixtures/results_snapshot.json.

A failure lists exactly which values moved. Rewording a report, tooltip or label can
never fail this test; a changed ratio, deflection, reaction, cost or section does.

    python tests/results_snapshot_regression.py            # check
    python tests/results_snapshot_regression.py --update   # accept the current results as the new baseline

Only use --update when the change in results is intended; review the fixture diff in Git.
"""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
import copy
import json
import math
import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"

from portalcalc.resources import FACTORY_DEFAULTS, data_path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "results_snapshot.json"
RELATIVE_TOLERANCE = 1e-6
ABSOLUTE_TOLERANCE = 1e-9

# name -> (changes to the factory preset as {section: {key: value}}, run the optimiser afterwards?)
SCENARIOS = {
    "factory_defaults": ({}, False),
    "skillion_roof": ({"geometry": {"roof_shape": "Skillion"}}, False),
    "rafter_frame": ({"geometry": {"frame_system": "Rafter"}}, False),
    "wide_span_36m": ({"geometry": {"span_mm": "36000"}}, False),
    "one_internal_column": ({"geometry": {"internal_column_count": "1 column"}}, False),
    "two_internal_columns_moment": ({"geometry": {"internal_column_count": "2 columns",
                                                  "internal_column_connection": "Moment"}}, False),
    "fixed_bases": ({"geometry": {"left_support": "Fixed", "right_support": "Fixed"}}, False),
    "left_canopy": ({"canopies": {"left_length_mm": "6000", "left_type": "Canopy", "left_depth_mm": "900"}}, False),
    "right_lean_to": ({"canopies": {"right_length_mm": "8000", "right_type": "Lean-to"}}, False),
    "region_c_open_front": ({"wind": {"region": "C", "wall_enclosure": {"back": True, "left": True,
                                                                         "right": True, "front": False}}}, False),
    "importance_3_terrain_3": ({"wind": {"importance_level": "3", "terrain_category": "3"}}, False),
    "snow_region_as": ({"loads": {"snow_region": "Region AS", "snow_ahd_height_m": "1200"}}, False),
    "crane_10t": ({"loads": {"crane_rating_t": "10"}}, False),
    "storage_wall": ({"storage_loads": {"enabled": True}}, False),
    "second_order": ({"display": {"second_order_analysis": True}}, False),
    "building_loads_per_gridline": ({"wind": {"frame_load_basis": "building"}}, False),
    "factory_defaults_optimised": ({}, True),
}

# Dossier entries that are text, an echo of the inputs, or change every run.
DROP_KEYS = {"generated_at", "engine_provenance", "inputs", "key_analysis_report_lines",
             "key_section_check_report_lines", "optimiser_report", "portalcalc_version", "schema"}
# Text values that are results (what was selected, what governs), not wording.
RESULT_TEXT_KEYS = {"section", "profile", "name", "section_name", "display_section", "edge_section",
                    "general_section", "common_profile", "reinforcement_label", "combination", "combo",
                    "moment_combo", "shear_combo", "compression_combo", "uplift_combo", "group", "key",
                    "limit_basis", "inward_bridging", "outward_bridging", "span_type", "type", "rate_bucket"}


def results_only(value, key=""):
    """Keep numbers, flags and result text; drop wording. Returns None for dropped values."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in DROP_KEYS:
                continue
            kept = results_only(v, k)
            if kept is not None or v is None:
                out[k] = kept
        return out
    if isinstance(value, (list, tuple)):
        return [results_only(v, key) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return str(number)
        return float(f"{number:.10g}") if isinstance(value, float) else value
    if isinstance(value, str):
        return value if key in RESULT_TEXT_KEYS else None
    return None


def merged(base, changes):
    data = copy.deepcopy(base)
    for section, values in changes.items():
        data.setdefault(section, {}).update(copy.deepcopy(values))
    return data


@contextmanager
def solved_engine(name, kind="window"):
    """The scenario applied, solved (and optimised if the scenario says so).

    ``kind`` is "window" (the offscreen desktop window) or "headless" (the engine
    with plain fields and no Qt). Yields the engine; ``engine.structure`` is None
    if the model could not be generated.
    """
    changes, optimise = SCENARIOS[name]
    preset = merged(json.loads(data_path(FACTORY_DEFAULTS).read_text(encoding="utf-8")), changes)
    if kind == "headless":
        from portalcalc.frame.engine.headless import HeadlessFrameEngine
        engine = HeadlessFrameEngine()
    else:
        from PySide6.QtWidgets import QApplication
        from portalcalc.desktop.main_window import MainWindow
        app = QApplication.instance() or QApplication([])  # noqa: F841 (kept alive for the window)
        engine = MainWindow()
        engine.confirm_on_close = False
    try:
        engine.apply_preset_data(preset)
        engine.generate_and_solve()
        if optimise and engine.structure is not None and engine.design_envelope_actions is not None:
            engine.auto_size_current_preset(show_completion=False)
        yield engine
    finally:
        if kind != "headless":
            engine.close()


def solve_scenario(name, kind="window"):
    from portalcalc.service.contract import json_safe

    with solved_engine(name, kind) as engine:
        if engine.structure is None or engine.design_envelope_actions is None:
            return {"solved": False}
        dossier = json_safe(engine.calculation_design_dossier())
        result = results_only(dossier)
        result["solved"] = True
        result["sections"] = dict((dossier.get("inputs") or {}).get("sections", {}))
        displacements = [abs(float(x)) for x in engine.displacements]
        result["displacement_max_abs"] = float(f"{max(displacements):.10g}")
        result["displacement_sum_abs"] = float(f"{sum(displacements):.10g}")
        return result


def differences(expected, actual, path=""):
    """(changed, added): values that differ or disappeared, and keys that are new."""
    changed, added = [], []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in expected:
            where = f"{path}.{key}" if path else key
            if key not in actual:
                changed.append(f"{where}: {short(expected[key])} -> (missing)")
            else:
                c, a = differences(expected[key], actual[key], where)
                changed += c
                added += a
        added += [f"{path}.{key}" if path else key for key in actual if key not in expected]
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            changed.append(f"{path}: {len(expected)} items -> {len(actual)} items")
        for index, (e, a) in enumerate(zip(expected, actual)):
            c, n = differences(e, a, f"{path}[{index}]")
            changed += c
            added += n
    elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)) \
            and not isinstance(expected, bool) and not isinstance(actual, bool):
        if not math.isclose(expected, actual, rel_tol=RELATIVE_TOLERANCE, abs_tol=ABSOLUTE_TOLERANCE):
            percent = f" ({(actual - expected) / expected * 100:+.3g}%)" if expected else ""
            changed.append(f"{path}: {expected:.8g} -> {actual:.8g}{percent}")
    elif expected != actual:
        changed.append(f"{path}: {short(expected)} -> {short(actual)}")
    return changed, added


def short(value):
    text = json.dumps(value, default=str)
    return text if len(text) <= 60 else text[:57] + "..."


class ResultsSnapshotTests(unittest.TestCase):
    """One test per scenario; see the module docstring."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def check(self, name):
        self.assertIn(name, self.baseline, f"No baseline for {name}; run with --update.")
        changed, added = differences(self.baseline[name], solve_scenario(name))
        if added:
            print(f"\n[{name}] {len(added)} new result fields (not a failure): {', '.join(added[:6])}")
        if changed:
            shown = "\n  ".join(changed[:25])
            more = f"\n  ... and {len(changed) - 25} more" if len(changed) > 25 else ""
            self.fail(f"{name}: {len(changed)} result values changed\n  {shown}{more}\n"
                      "If this change is intended, run: python tests/results_snapshot_regression.py --update")


def _make_test(name):
    def test(self):
        self.check(name)
    test.__doc__ = f"Results unchanged for scenario: {name}"
    return test


for _name in SCENARIOS:
    setattr(ResultsSnapshotTests, f"test_{_name}", _make_test(_name))


def update():
    snapshot = {}
    for name in SCENARIOS:
        snapshot[name] = solve_scenario(name)
        governing = (snapshot[name].get("overall_governing") or {}).get("ratio")
        print(f"{name:30s} solved={snapshot[name]['solved']} governing ratio={governing}", flush=True)
    FIXTURE.write_text(json.dumps(snapshot, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {FIXTURE} ({FIXTURE.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    if "--update" in sys.argv:
        update()
    else:
        unittest.main()
