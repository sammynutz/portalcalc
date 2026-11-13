"""Phase 4 regression: per-gridline building loads checked through main.py's envelopes.

CombinationTests are Qt-free. They cover:
- deduplicated combination counts;
- names and SLS prefixes;
- per-frame roof gravity;
- strip layout on the actual nodes.

DesktopEnvelopeTests solve real frames:
- ``legacy_2d`` restricted to one interior gridline reproduces today's
  design envelope exactly;
- ``legacy_2d`` over every gridline only adds demands;
- the ``building`` basis keeps every gravity- and side-wind-governed action
  unchanged, and reports where each action governs.
"""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace

import numpy as np

from building_loads import building_loads
from building_model import building_model_from_preset
from frame_loading import (FrameLoadCombination, distinct_frames, frame_load_combinations, frame_roof_loads,
                           gridline_label, roof_strips_on_nodes)

ROOT = Path(__file__).parent
DEFAULTS = json.loads((ROOT / "presets/factory_defaults.json").read_text(encoding="utf-8"))
SKIP_KEYS = {"uls_count", "sls_count", "displacements"}
# Element/node ids: in a symmetric frame equal demands tie and float noise picks either member.
ID_KEYS = {"member", "node", "anchor", "start", "end", "tilt_reference", "coincident_moment_member",
           "length_mm", "height_mm", "x1", "y1", "x2", "y2"}
# Doubled-chord zones are chosen from the whole combination set and then scale
# the recorded TOP/BOTTOM demands, so those paths are not per-case quantities.
SET_DEPENDENT_PREFIXES = (".TOP.", ".BOTTOM.", ".doubled_chord_zones")


def per_case(paths):
    return {path: value for path, value in paths.items()
            if not path.startswith(SET_DEPENDENT_PREFIXES) and "doubled" not in path}


def variant(**changes):
    preset = copy.deepcopy(DEFAULTS)
    for path, value in changes.items():
        section, key = path.split("__")
        preset[section][key] = value
    return preset


VARIANTS = {
    "factory defaults": DEFAULTS,
    "15 deg gable, front open": variant(
        geometry__roof_pitch_deg="15",
        wind__wall_enclosure={"left": True, "right": True, "front": False, "back": True}),
    "5 deg skillion, right open": variant(
        geometry__roof_shape="Skillion", geometry__roof_pitch_deg="5",
        wind__wall_enclosure={"left": True, "right": False, "front": True, "back": True}),
    "fully open 20 deg gable": variant(
        geometry__roof_pitch_deg="20",
        wind__wall_enclosure={"left": False, "right": False, "front": False, "back": False}),
    "enclosed cyclonic region C, light roof": variant(
        wind__region="C", wind__importance_level="2", loads__g_kpa="0.05", loads__q_kpa="0.1"),
}
LEAN = dict(canopies__left_type="Lean-to", canopies__left_length_mm="6000",
            canopies__left_lean_enclosure={"left": True, "right": True, "front": True, "back": True})
ATTACHMENT_VARIANTS = {
    "enclosed left lean-to": variant(**LEAN),
    "lean-to open front, open right canopy": variant(
        **{**LEAN, "canopies__left_lean_enclosure": {"left": True, "right": True, "front": False, "back": True}},
        canopies__right_type="Canopy", canopies__right_length_mm="4000", canopies__right_pitch_deg="3"),
    "lean-to joined to the main chamber": variant(
        **{**LEAN, "canopies__left_lean_enclosure": {"left": True, "right": False, "front": True, "back": False}},
        wind__wall_enclosure={"left": False, "right": True, "front": True, "back": True}),
    "fully open with free-roof canopies": variant(
        canopies__right_type="Canopy", canopies__right_length_mm="4000", canopies__right_pitch_deg="3",
        canopies__left_type="Canopy", canopies__left_length_mm="3000", canopies__left_pitch_deg="3",
        geometry__roof_pitch_deg="15",
        wind__wall_enclosure={"left": False, "right": False, "front": False, "back": False}),
    "15 deg, 3-sided, lean-to": variant(**LEAN, geometry__roof_pitch_deg="15",
                                        wind__wall_enclosure={"left": True, "right": True, "front": False, "back": True}),
}


def flatten(value, path=""):
    """Numeric leaves of an envelope, keyed by path (labels and counts skipped)."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in SKIP_KEYS or key in ID_KEYS or "combo" in str(key):
                continue
            out.update(flatten(item, f"{path}.{key}"))
        return out
    if isinstance(value, (list, tuple)):
        out = {}
        for index, item in enumerate(value):
            out.update(flatten(item, f"{path}[{index}]"))
        return out
    if isinstance(value, (bool, str)) or value is None:
        return {}
    if isinstance(value, (int, float, np.floating, np.integer)):
        return {path: float(value)}
    return {}


def governing_values(actions):
    """{path: (value, combo)} for every enveloped action with a governing combo."""
    out = {}

    def walk(value, path):
        if isinstance(value, dict):
            if ("value" in value and value.get("combo") not in (None, "-")
                    and isinstance(value["value"], (int, float, np.floating))):
                out[path] = (float(value["value"]), value["combo"])
            for key, item in value.items():
                if key not in SKIP_KEYS:
                    walk(item, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(actions, "")
    return out


class CombinationTests(unittest.TestCase):
    def setUp(self):
        self.model = building_model_from_preset(DEFAULTS)
        self.frames = building_loads(self.model)
        self.legacy = building_loads(self.model, basis="legacy_2d")

    def test_deduplicated_counts(self):
        self.assertEqual(distinct_frames(self.legacy), [["GL1", "GL6"], ["GL2", "GL3", "GL4", "GL5"]])
        self.assertEqual(len(frame_load_combinations(self.legacy)), 2 * 57)
        self.assertEqual(len(distinct_frames(self.frames)), 6)
        combos = frame_load_combinations(self.frames)
        # Gravity (9) and side wind (32) shared by tributary class; end wind (32) per frame.
        self.assertEqual(len(combos), 2 * 9 + 2 * 32 + 6 * 32)
        self.assertEqual(len(frame_load_combinations(self.frames, gridlines=["GL3"])), 9 + 64)

    def test_long_building_shares_its_middle_frames(self):
        preset = variant(wind__building_length_m="96")
        frames = building_loads(building_model_from_preset(preset))
        groups = distinct_frames(frames)
        self.assertEqual(len(frames), 13)
        self.assertLess(len(groups), len(frames))
        middle = max(groups, key=len)
        self.assertGreaterEqual(len(middle), 3)  # beyond 3h + a bay from both ends the frames are identical

    def test_names_and_flags(self):
        combos = frame_load_combinations(self.frames)
        for combo in combos:
            self.assertIsInstance(combo, FrameLoadCombination)
            self.assertTrue(combo.name.startswith(combo.base_name + " @"))
            self.assertEqual(combo.is_serviceability, combo.base_name.startswith("SLS:"))
            self.assertIn(combo.frame_loads.gridline, combo.gridlines)
        gq = [c for c in combos if c.base_name == "SLS: G + Q"]
        self.assertEqual([c.name for c in gq], ["SLS: G + Q @GL1,GL6", "SLS: G + Q @GL2–GL5"])
        self.assertEqual(gridline_label(("GL2", "GL3", "GL4")), "GL2–GL4")
        self.assertEqual(gridline_label(("GL1", "GL6")), "GL1,GL6")

    def test_frame_roof_loads_match_the_2d_roof_loads(self):
        roof = frame_roof_loads(self.frames[2])
        self.assertEqual(roof.bay_size_m, 8.0)
        self.assertEqual((roof.g, roof.q, roof.solar), (-0.15, -0.25, -0.15))
        self.assertEqual(frame_roof_loads(self.frames[0]).bay_size_m, 4.0)

    def test_strips_follow_the_actual_ridge_and_path(self):
        nodes = [SimpleNamespace(x=x, y=y) for x, y in ((150, 6000), (5000, 6700), (10000, 7400), (15000, 6700), (20850, 6000))]
        total = sum(math.hypot((b.x - a.x) / 1000, (b.y - a.y) / 1000) for a, b in zip(nodes, nodes[1:]))
        ridge = sum(math.hypot((b.x - a.x) / 1000, (b.y - a.y) / 1000) for a, b in zip(nodes[:3], nodes[1:3]))
        slopes = SimpleNamespace(roof_layout="slopes", roof_strips=((0, 10.6, 1.0), (10.6, 21.2, 2.0)))
        self.assertEqual(roof_strips_on_nodes(slopes, nodes), [(0.0, ridge, 1.0), (ridge, total, 2.0)])
        uniform = SimpleNamespace(roof_layout="uniform", roof_strips=((0, 21.2, 3.0),))
        self.assertEqual(roof_strips_on_nodes(uniform, nodes), [(0.0, total, 3.0)])


class DesktopEnvelopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls._appdata = tempfile.TemporaryDirectory(prefix="portalcalc_frame_loading_")
        os.environ["APPDATA"] = cls._appdata.name
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def solve(self, preset, mode=None, gridlines=None):
        w = self.window
        w.apply_preset_data(copy.deepcopy(preset))
        w._suppress_auto_web_split_on_solve = True
        w._suppress_panel_topology_search = True
        w.building_load_mode = mode
        w._building_loads_cache = None
        if gridlines is not None:
            original = type(w).current_building_frame_loads

            def restricted(self):
                result = dict(original(self))
                result["frames"] = [f for f in result["frames"] if f.gridline in gridlines]
                return result
            w.current_building_frame_loads = restricted.__get__(w)
        try:
            start = time.perf_counter()
            w.generate_and_solve()
            elapsed = time.perf_counter() - start
        finally:
            w.__dict__.pop("current_building_frame_loads", None)
            w.building_load_mode = None
        self.assertIsNotNone(w.design_envelope_actions, w.results_box.toPlainText()[:500])
        ratio = w.auto_size_overall_ratio(w.active_auto_size_group_specs())
        return copy.deepcopy(w.design_envelope_actions), float(ratio[0]), elapsed

    def test_one_interior_gridline_reproduces_todays_envelope(self):
        for name, preset in {**VARIANTS, **ATTACHMENT_VARIANTS}.items():
            with self.subTest(building=name):
                today, today_ratio, _ = self.solve(preset)
                legacy, legacy_ratio, _ = self.solve(preset, "legacy_2d", gridlines={"GL3"})
                a, b = flatten(today), flatten(legacy)
                self.assertEqual(set(a), set(b))
                for path, value in a.items():
                    # 1e-6 absolute covers round-off on near-zero flybrace segment moments.
                    self.assertLessEqual(abs(value - b[path]), 1e-6 * max(1.0, abs(value)), path)
                self.assertAlmostEqual(today_ratio, legacy_ratio, places=9)

    def test_all_gridlines_only_add_demands(self):
        for name in ("factory defaults", "15 deg gable, front open"):
            with self.subTest(building=name):
                today = per_case(governing_values(self.solve(VARIANTS[name])[0]))
                every = governing_values(self.solve(VARIANTS[name], "legacy_2d")[0])
                for path, (value, _) in today.items():
                    self.assertGreaterEqual(every[path][0], value - 1e-7 * max(1.0, abs(value)), path)

    def test_building_basis_keeps_gravity_and_side_wind_actions(self):
        for name in ("factory defaults", "enclosed cyclonic region C, light roof"):
            with self.subTest(building=name):
                today_actions, today_ratio, today_time = self.solve(VARIANTS[name])
                new_actions, new_ratio, new_time = self.solve(VARIANTS[name], "building")
                today, new = per_case(governing_values(today_actions)), governing_values(new_actions)
                self.assertTrue(any(p.startswith(".single_chord_envelopes") for p in today))
                end_governed = 0
                for path, (value, combo) in today.items():
                    if "End" in combo:
                        end_governed += 1
                        continue
                    new_value, new_combo = new[path]
                    self.assertIn(" @", new_combo, path)
                    if not any(d in new_combo for d in ("Front", "Back")):
                        # Same governing case family on an interior gridline: unchanged value.
                        self.assertLessEqual(abs(new_value - value), 1e-7 * max(1.0, abs(value)),
                                             f"{path}: {combo} -> {new_combo}")
                    else:
                        self.assertGreaterEqual(new_value, value - 1e-7 * max(1.0, abs(value)), path)
                # Everything here is below 10 degrees, so the 3D end wind never needs more than today's envelope.
                self.assertLessEqual(new_ratio, today_ratio + 1e-9)
                self.assertLess(new_time, 8.0 * max(today_time, 0.05))


class DesktopToggleTests(DesktopEnvelopeTests):
    """The "Frame loads" selector: building-load mode from the desktop, saved in presets."""

    test_one_interior_gridline_reproduces_todays_envelope = None
    test_all_gridlines_only_add_demands = None
    test_building_basis_keeps_gravity_and_side_wind_actions = None

    def test_toggle_solves_with_building_loads_and_round_trips(self):
        w = self.window
        w.apply_preset_data(copy.deepcopy(DEFAULTS))
        w._suppress_auto_web_split_on_solve = True
        w._suppress_panel_topology_search = True
        self.assertEqual((w.frame_load_basis(), w.building_load_mode), ("single_frame", None))
        self.assertNotIn("frame_load_basis", w.collect_preset_data()["wind"])  # default presets unchanged
        w.generate_and_solve()
        self.assertNotIn("BUILDING LOADS", w.results_box.toPlainText())

        w.frame_load_basis_combo.setCurrentIndex(w.frame_load_basis_combo.findData("building"))
        self.assertEqual(w.building_load_mode, "building")
        w.generate_and_solve()
        text = w.results_box.toPlainText()
        self.assertIn("BUILDING LOADS", text)
        self.assertIn("Combinations     : 274 of 274", text)
        self.assertIn("@GL", text)
        saved = w.collect_preset_data()
        self.assertEqual(saved["wind"]["frame_load_basis"], "building")

        w.apply_preset_data(copy.deepcopy(DEFAULTS))       # an older preset: single frame again
        self.assertIsNone(w.building_load_mode)
        w.apply_preset_data(copy.deepcopy(saved))
        self.assertEqual(w.building_load_mode, "building")
        model = building_model_from_preset(saved)          # the setting travels with the building
        self.assertEqual(model.frame_preset("GL3")["wind"]["frame_load_basis"], "building")

        # A service/review building drives the mode; loading its frame preset must not reset it.
        w.building_model_override = model
        w.building_load_mode = "legacy_2d"
        w.apply_preset_data(copy.deepcopy(DEFAULTS))
        self.assertEqual(w.building_load_mode, "legacy_2d")
        w.building_model_override = None
        w.apply_preset_data(copy.deepcopy(DEFAULTS))
        self.assertIsNone(w.building_load_mode)


class ScreeningTests(DesktopEnvelopeTests):
    """Phase 5 active-set screening and the full-set acceptance gate (fast checks)."""

    test_one_interior_gridline_reproduces_todays_envelope = None
    test_all_gridlines_only_add_demands = None
    test_building_basis_keeps_gravity_and_side_wind_actions = None

    def setUp(self):
        w = self.window
        w.apply_preset_data(copy.deepcopy(DEFAULTS))
        w._suppress_auto_web_split_on_solve = True
        w._suppress_panel_topology_search = True
        w.building_load_mode = "building"
        w._building_loads_cache = None
        w._building_active_set = None

    def tearDown(self):
        w = self.window
        w.building_load_mode = None
        w._building_active_set = None
        w.__dict__.pop("run_physics_structure_optimisation", None)

    def test_screened_solve_checks_only_the_active_set(self):
        w = self.window
        w.generate_and_solve()
        full = per_case(governing_values(w.design_envelope_actions))
        total = w.building_check_counts[1]
        self.assertEqual(w.building_check_counts, (total, total))
        governing = set(w.building_full_governing_names)
        self.assertTrue(governing)
        self.assertTrue(all(" @" in name for name in governing))
        w._building_active_set = frozenset(governing)
        w.generate_and_solve()
        self.assertEqual(w.building_check_counts, (len(governing), total))
        screened = governing_values(w.design_envelope_actions)
        # The governing combinations reproduce every governing action exactly.
        for path, (value, combo) in full.items():
            self.assertAlmostEqual(screened[path][0], value, delta=1e-7 * max(1.0, abs(value)), msg=path)

    def test_gate_adds_missed_combinations_and_rechecks_the_full_set(self):
        w = self.window
        calls = []

        def stub_optimiser(**kwargs):
            # Stands in for the optimiser: record the active set and keep the design.
            calls.append(frozenset(w._building_active_set))
            w.generate_and_solve()
            return {"verified": True}
        w.run_physics_structure_optimisation = stub_optimiser
        w.generate_and_solve()
        gravity = frozenset(w.building_gravity_combination_names)
        # Start the first round from gravity only by hiding the governing wind cases.
        original = type(w)._auto_size_with_building_screening

        def gravity_only_start(self, **kwargs):
            real_generate = self.generate_and_solve
            first = {"done": False}

            def generate_then_forget_wind():
                real_generate()
                if not first["done"] and self._building_active_set is None:
                    first["done"] = True
                    self.building_full_governing_names = set()
            self.generate_and_solve = generate_then_forget_wind
            try:
                return original(self, **kwargs)
            finally:
                del self.generate_and_solve
        result = gravity_only_start(w, show_completion=False)
        self.assertEqual(calls[0], gravity)
        self.assertGreater(len(calls), 1)                 # the gate found missed wind cases
        self.assertGreater(len(calls[1]), len(calls[0]))  # and added them for the next round
        self.assertEqual(w.building_check_counts[0], w.building_check_counts[1])  # final check is the full set
        self.assertIn("FULL-SET VERIFICATION", result["report"])
        self.assertIn("Governing gridlines and cases", result["report"])
        # Factory sections fail on gravity: an unchanged design is never reported as verified.
        self.assertFalse(result["verified"])


@unittest.skipUnless(os.environ.get("PORTALCALC_SLOW_TESTS"), "set PORTALCALC_SLOW_TESTS=1 for optimiser runs (~2 min)")
class OptimiserComparisonTests(DesktopEnvelopeTests):
    """Full optimiser: building loads never size heavier, and screening matches the unscreened result."""

    test_one_interior_gridline_reproduces_todays_envelope = None
    test_all_gridlines_only_add_demands = None
    test_building_basis_keeps_gravity_and_side_wind_actions = None

    def optimise(self, mode, screening=True):
        w = self.window
        w.apply_preset_data(copy.deepcopy(DEFAULTS))
        w.building_load_mode = mode
        w.building_screening_disabled = not screening
        w._building_loads_cache = None
        try:
            start = time.perf_counter()
            w.run_structure_optimisation(show_completion=False)
            elapsed = time.perf_counter() - start
        finally:
            w.building_load_mode = None
            w.building_screening_disabled = False
        specs = w.active_auto_size_group_specs()
        sections = (w.left_column_profile_combo.currentText(), w.top_profile_combo.currentText(),
                    w.web_profile_combo.currentText(), w.depth_input.text())
        return (w.costing_summary or {}).get("total_cost"), w.auto_size_design_acceptable(
            specs, w.auto_size_overall_ratio(specs)[0]), sections, elapsed, w.auto_size_report_text

    def test_building_loads_size_no_heavier_and_screening_is_exact(self):
        today_cost, today_ok, _, today_time, _ = self.optimise(None)
        cost, ok, sections, elapsed, report = self.optimise("building")
        full_cost, full_ok, full_sections, _, _ = self.optimise("building", screening=False)
        self.assertTrue(today_ok and ok and full_ok)
        self.assertLessEqual(cost, today_cost + 1e-6)
        self.assertEqual((cost, sections), (full_cost, full_sections))
        self.assertIn("FULL-SET VERIFICATION: OK", report)
        self.assertLess(elapsed, 1.6 * today_time)


if __name__ == "__main__":
    unittest.main()
