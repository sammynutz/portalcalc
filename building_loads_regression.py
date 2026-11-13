"""Building load engine regression (phases 2-3).

EngineTests are Qt-free:
- end-frame halving and load conservation;
- partial cladding and open bays;
- scope errors and JSON output;
- the building-basis side-wind strips agree with wind.py's own strips;
- an independent numerical check of the end-wind band integration;
- Cpi taken from where each opening actually is;
- the surface-count Kc option.

ExpectedDifferenceTests compare the ``building`` basis with ``legacy_2d``
(today's 2D envelope) and attribute each difference.

DesktopParityTests solve the real 2D frame for several buildings. They load
an interior frame's ``legacy_2d`` descriptors onto the same nodes the way
main.py does, and require every gravity and wind basic case (Left/Right/End,
Cpe and Cpi, ULS and SLS) to match main.py's own per-case load arrows.
"""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

import building_loads as bl
from building_loads import BuildingLoadsScopeError, building_loads, building_loads_to_dict
from building_model import BuildingModel, building_model_from_preset
from loads import apply_roof_vertical_loads_in_zones
from wind import (CPI_CASE_OPTIONS, SIDE_WALL_CPE_BANDS, area_reduction_factor, low_pitch_cpe_values,
                  opening_area_reduction_factor, roof_outward_normal)

ROOT = Path(__file__).parent
DEFAULTS = json.loads((ROOT / "presets/factory_defaults.json").read_text(encoding="utf-8"))
SIDE_CASES = ("Left +", "Left -", "Right +", "Right -")
END_CASES = ("Front +", "Front -", "Back +", "Back -")


def preset_variant(**changes):
    preset = copy.deepcopy(DEFAULTS)
    for path, value in changes.items():
        section, key = path.split("__")
        preset[section][key] = value
    return preset


VARIANTS = {
    "factory defaults (enclosed 7.5 deg gable)": DEFAULTS,
    "15 deg gable, front open": preset_variant(
        geometry__roof_pitch_deg="15",
        wind__wall_enclosure={"left": True, "right": True, "front": False, "back": True}),
    "5 deg skillion, right open, bearing north": preset_variant(
        geometry__roof_shape="Skillion", geometry__roof_pitch_deg="5", wind__orientation="27°",
        wind__wall_enclosure={"left": True, "right": False, "front": True, "back": True}),
    "fully open 20 deg gable (free roof)": preset_variant(
        geometry__roof_pitch_deg="20",
        wind__wall_enclosure={"left": False, "right": False, "front": False, "back": False}),
    "left and back open, region C": preset_variant(
        wind__region="C", wind__importance_level="2",
        wind__wall_enclosure={"left": False, "right": True, "front": True, "back": False}),
    "enclosed cyclonic region C": preset_variant(wind__region="C", wind__importance_level="2"),
}


class Collector:
    def add_load(self, dof, value):
        return None


# ---------------------------------------------------------------- applier

def roof_arrows(nodes, case, scale):
    """main.py's roof wind arrows (strip overlap, per-slope or uniform), N/m display intensity."""
    distances = [0.0]
    for n1, n2 in zip(nodes[:-1], nodes[1:]):
        distances.append(distances[-1] + math.hypot((n2.x - n1.x) / 1000.0, (n2.y - n1.y) / 1000.0))
    apex_x = max(nodes, key=lambda node: node.y).x
    arrows = []
    for i, (n1, n2) in enumerate(zip(nodes[:-1], nodes[1:])):
        s1, s2 = distances[i], distances[i + 1]
        panel_len = max(s2 - s1, 1e-9)
        if case.roof_layout == "uniform":
            intensity_n_m = case.roof_strips[0][2] * scale * 1000.0
        elif case.roof_layout == "slopes":
            q = case.roof_strips[0][2] if (n1.x + n2.x) / 2.0 <= apex_x else case.roof_strips[1][2]
            intensity_n_m = q * scale * 1000.0
        else:
            total_kn = sum(q * scale * max(0.0, min(s2, b) - max(s1, a)) for a, b, q in case.roof_strips)
            intensity_n_m = (total_kn / panel_len) * 1000.0
        nx, ny = roof_outward_normal(n1, n2)
        arrows.append(((n1.x + n2.x) / 2.0, (n1.y + n2.y) / 2.0 + 800.0, intensity_n_m * nx, intensity_n_m * ny))
    return arrows


def roof_cpi_arrows(nodes, line_kn_m):
    arrows = []
    for n1, n2 in zip(nodes[:-1], nodes[1:]):
        total_n = line_kn_m * math.hypot((n2.x - n1.x) / 1000, (n2.y - n1.y) / 1000) * 1000.0
        nx, ny = roof_outward_normal(n1, n2)
        arrows.append(((n1.x + n2.x) / 2, (n1.y + n2.y) / 2 + 800, total_n * nx, total_n * ny))
    return arrows


def column_arrows(base, top, signed_kn_m):
    total_n = signed_kn_m * abs(top.y - base.y) / 1000.0 * 1000.0
    return [(base.x + (top.x - base.x) * t, base.y + (top.y - base.y) * t, total_n / 5, 0.0)
            for t in ((i + 0.5) / 5 for i in range(5))]


def frame_case_arrows(frame, label, roof_nodes, main_nodes, bases):
    """Arrows main.py would draw for ``label`` from this frame's descriptors."""
    columns = {"left": (bases[0], main_nodes[0]), "right": (bases[1], main_nodes[-1])}
    if label in {g.label for g in frame.gravity}:
        load = next(g for g in frame.gravity if g.label == label)
        return apply_roof_vertical_loads_in_zones(Collector(), main_nodes, load.kpa * frame.tributary_m,
                                                  load.side, load.from_percent, load.to_percent)
    level, kind, rest = label.split(" ", 2)
    if kind == "Cpe":
        case = frame.wind_case(rest)
        scale = 1.0 if level == "Wu" else case.sls_scale
        nodes = roof_nodes if case.roof_basis == "cpe" else main_nodes
        arrows = roof_arrows(nodes, case, scale)
        order = ("right", "left") if case.wind_from == "right" else ("left", "right")
        for column in order:
            if column in case.wall_cpe_kn_m:
                arrows += column_arrows(*columns[column], case.wall_cpe_kn_m[column] * scale)
        return arrows
    cpi_sign, _, cpe_case = rest.partition(" with ")
    case = frame.wind_case(cpe_case)
    cpi_case = f"Cpi {cpi_sign}"
    scale = 1.0 if level == "Wu" else case.sls_scale
    arrows = []
    if cpi_case in case.roof_cpi_kn_m:
        arrows += roof_cpi_arrows(main_nodes, case.roof_cpi_kn_m[cpi_case] * scale)
    for column in ("left", "right"):
        line = case.wall_cpi_kn_m.get(cpi_case, {}).get(column)
        if line is not None:
            arrows += column_arrows(*columns[column], line * scale)
    return arrows


def wind_labels():
    labels = []
    for case in SIDE_CASES + ("End +", "End -"):
        labels += [f"Wu Cpe {case}", f"Ws Cpe {case}"]
        for cpi in CPI_CASE_OPTIONS:
            labels += [f"Wu {cpi} with {case}", f"Ws {cpi} with {case}"]
    return labels


def hat_weight(y, stations, index):
    """Simply supported bay reaction shape for gridline ``index`` at ``y``."""
    w = 0.0
    if index > 0 and stations[index - 1] <= y <= stations[index]:
        w = (y - stations[index - 1]) / (stations[index] - stations[index - 1])
    if index + 1 < len(stations) and stations[index] <= y <= stations[index + 1]:
        w = (stations[index + 1] - y) / (stations[index + 1] - stations[index])
    return w


def numeric_band_integral_mm(bands_mm, stations, index, steps=200000):
    """Midpoint-rule ∫ Cpe(y) × hat(y) dy, independent of frame_strip_integrals."""
    length = stations[-1]
    dy = length / steps
    total = 0.0
    for k in range(steps):
        y = (k + 0.5) * dy
        cpe = next((c for a, b, c in bands_mm if a <= y < b), 0.0)
        total += cpe * hat_weight(y, stations, index) * dy
    return total


# ------------------------------------------------------------------ tests

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.model = building_model_from_preset(DEFAULTS)
        self.frames = building_loads(self.model)
        self.legacy = building_loads(self.model, basis="legacy_2d")

    def test_cases_per_basis(self):
        self.assertEqual([c.cpe_case for c in self.frames[2].wind], list(SIDE_CASES + END_CASES))
        self.assertEqual([c.cpe_case for c in self.legacy[2].wind], list(SIDE_CASES) + ["End +", "End -"])
        self.assertEqual({c.legacy_cpe_case for c in self.frames[2].wind if c.wind_from in ("front", "back")},
                         {"End +", "End -"})

    def test_end_frames_carry_exactly_half(self):
        self.assertEqual([f.tributary_mm for f in self.frames], [4000.0, 8000.0, 8000.0, 8000.0, 8000.0, 4000.0])
        cases = [(self.legacy, None), (self.frames, SIDE_CASES)]
        for frames, names in cases:
            end, interior = frames[0], frames[2]
            self.assertEqual(end.gravity_kn_m("G") * 2, interior.gravity_kn_m("G"))
            for case_end, case_int in zip(end.wind, interior.wind):
                if names and case_end.cpe_case not in names:
                    continue
                with self.subTest(basis=end.basis, case=case_end.cpe_case):
                    self.assertEqual([q * 2 for _, _, q in case_end.roof_strips], [q for _, _, q in case_int.roof_strips])
                    self.assertEqual({k: v * 2 for k, v in case_end.wall_cpe_kn_m.items()}, case_int.wall_cpe_kn_m)
                    self.assertEqual({k: v * 2 for k, v in case_end.roof_cpi_kn_m.items()}, case_int.roof_cpi_kn_m)
                    self.assertEqual(case_end.cpi, case_int.cpi)

    def test_loads_are_conserved_along_the_building(self):
        length_m = self.model.length_mm / 1000.0
        interior = self.frames[2]
        per_metre = lambda value: value / interior.tributary_m
        self.assertAlmostEqual(sum(f.gravity_kn_m("G") for f in self.frames),
                               per_metre(interior.gravity_kn_m("G")) * length_m, places=9)
        for name in SIDE_CASES:
            case = interior.wind_case(name)
            for j, (_, _, q) in enumerate(case.roof_strips):
                total = sum(f.wind_case(name).roof_strips[j][2] for f in self.frames)
                self.assertAlmostEqual(total, per_metre(q) * length_m, places=9)
        # End wind: the building total is the band integral along the whole length.
        for name in END_CASES:
            case = interior.wind_case(name)
            result = bl.calculate_wind(bl.replace(bl.base_wind_inputs(self.model, 8.0), orientation=case.source_direction))
            bands = low_pitch_cpe_values(result.h_m / length_m)
            column = 3 if name.endswith("+") else 4
            integral = sum(row[column] * max(0.0, min(length_m, row[2] * result.h_m) - row[1] * result.h_m)
                           for row in bands)
            expected = -integral * case.wu_kn_m2 * 0.8
            self.assertAlmostEqual(sum(f.wind_case(name).roof_strips[0][2] for f in self.frames), expected, places=9)

    def test_end_wind_band_integration_matches_an_independent_integral(self):
        stations = [g.y_mm for g in self.model.gridlines]
        length_mm = self.model.length_mm
        for name in ("Front -", "Back -"):
            for index in range(len(stations)):
                case = self.frames[index].wind_case(name)
                result = bl.calculate_wind(bl.replace(bl.base_wind_inputs(self.model, 8.0),
                                                      orientation=case.source_direction))
                h_mm = result.h_m * 1000.0
                bands = [(a * h_mm, min(length_mm, b * h_mm), cmin)
                         for _, a, b, _, cmin in low_pitch_cpe_values(result.h_m / (length_mm / 1000.0))]
                if name.startswith("Back"):
                    bands = [(length_mm - b, length_mm - a, c) for a, b, c in bands]
                expected = -numeric_band_integral_mm(bands, stations, index) / 1000.0 * case.wu_kn_m2 * 0.8
                with self.subTest(case=name, gridline=index):
                    self.assertAlmostEqual(case.roof_strips[0][2], expected, delta=1e-4 * max(1.0, abs(expected)))
                # Side walls: same bands, Table 5.2(C), suction outward on both columns.
                side = [(a * h_mm, (length_mm if math.isinf(b) else min(length_mm, b * h_mm)), c)
                        for a, b, c in SIDE_WALL_CPE_BANDS]
                if name.startswith("Back"):
                    side = [(length_mm - b, length_mm - a, c) for a, b, c in side]
                inward = numeric_band_integral_mm(side, stations, index) / 1000.0 * case.wu_kn_m2 * 0.8
                self.assertAlmostEqual(case.wall_cpe_kn_m["left"], inward, delta=1e-4 * max(1.0, abs(inward)))
                self.assertAlmostEqual(case.wall_cpe_kn_m["right"], -inward, delta=1e-4 * max(1.0, abs(inward)))

    def test_building_side_wind_strips_match_wind_py(self):
        """The building basis rebuilds wind.py's strips per frame; with legacy Kc they agree."""
        for name, preset in VARIANTS.items():
            model = building_model_from_preset(preset)
            new = building_loads(model, kc_rule="legacy")[2]
            old = building_loads(model, basis="legacy_2d")[2]
            for case in SIDE_CASES:
                with self.subTest(building=name, case=case):
                    self.assertEqual(new.wind_case(case).roof_strips, old.wind_case(case).roof_strips)
                    self.assertEqual(new.wind_case(case).wall_cpe_kn_m, old.wind_case(case).wall_cpe_kn_m)

    def test_partly_clad_wall_loads_only_the_columns_it_spans_to(self):
        data = self.model.to_dict()
        data["walls"][0] = {"face": "left", "segments": [{"from_mm": 0, "to_mm": 16000, "clad": True},
                                                         {"from_mm": 16000, "to_mm": 40000, "clad": False}]}
        frames = building_loads(BuildingModel.from_dict(data))
        self.assertEqual([f.wall_tributary_mm["left"] for f in frames], [4000.0, 8000.0, 4000.0, 0.0, 0.0, 0.0])
        self.assertEqual([f.wall_tributary_mm["right"] for f in frames], [4000.0, 8000.0, 8000.0, 8000.0, 8000.0, 4000.0])
        left_plus = [f.wind_case("Left +") for f in frames]
        self.assertNotIn("left", left_plus[3].wall_cpe_kn_m)
        self.assertNotIn("left", left_plus[3].wall_cpi_kn_m.get("Cpi +", {}))
        self.assertAlmostEqual(left_plus[2].wall_cpe_kn_m["left"] * 2, left_plus[1].wall_cpe_kn_m["left"])
        # End wind on the left wall only from its clad part; the right wall is unchanged.
        self.assertNotIn("left", frames[4].wind_case("Front -").wall_cpe_kn_m)
        self.assertEqual(frames[4].wind_case("Front -").wall_cpe_kn_m["right"],
                         self.frames[4].wind_case("Front -").wall_cpe_kn_m["right"])

    def test_one_open_bay_is_a_dominant_opening(self):
        data = self.model.to_dict()
        data["walls"][0] = {"face": "left", "segments": [{"from_mm": 0, "to_mm": 8000, "clad": False},
                                                         {"from_mm": 8000, "to_mm": 40000, "clad": True}]}
        frame = building_loads(BuildingModel.from_dict(data))[2]
        area = 8.0 * 6.0
        windward = opening_area_reduction_factor("windward_wall", area) * 0.7
        self.assertAlmostEqual(frame.wind_case("Left +").cpi["Cpi +"], windward, places=12)
        self.assertAlmostEqual(frame.wind_case("Left +").cpi["Cpi -"], windward, places=12)
        # Front wind: the open bay is at the windward end of a side wall (0-8 m).
        h_m = bl.calculate_wind(bl.base_wind_inputs(self.model, 8.0)).h_m
        side = bl.side_wall_band_cpe(h_m, 0.0, 8.0)
        self.assertAlmostEqual(frame.wind_case("Front +").cpi["Cpi +"],
                               opening_area_reduction_factor("side_wall", area) * side, places=12)
        # Back wind: the same opening is now 32-40 m from the windward end.
        far = bl.side_wall_band_cpe(h_m, 32.0, 40.0)
        self.assertAlmostEqual(frame.wind_case("Back +").cpi["Cpi +"],
                               opening_area_reduction_factor("side_wall", area) * far, places=12)
        self.assertLess(abs(far), abs(side))
        self.assertEqual(self.frames[2].wind_case("Left +").cpi, {"Cpi +": 0.2, "Cpi -": -0.2})

    def test_fully_open_building_uses_free_roof_without_walls_or_cpi(self):
        data = self.model.to_dict()
        for wall in data["walls"]:
            wall["clad"] = False
        frame = building_loads(BuildingModel.from_dict(data))[2]
        for case in frame.wind:
            self.assertEqual(case.roof_basis, "free_roof_cpn")
            self.assertEqual(case.roof_layout, "slopes" if case.wind_from in ("left", "right") else "uniform")
            self.assertEqual((case.wall_cpe_kn_m, case.roof_cpi_kn_m, case.wall_cpi_kn_m), ({}, {}, {}))

    def test_each_direction_uses_its_own_source(self):
        frame = self.frames[2]
        self.assertEqual([frame.wind_case(f"{d} +").source_direction for d in ("Left", "Right", "Front", "Back")],
                         ["S", "N", "W", "E"])

    def test_surface_count_kc_rule(self):
        self.assertEqual(self.frames[2].kc_rule, "surface_count")  # the building-basis default
        self.assertEqual(self.legacy[2].wind_case("Left +").factors["kc_rule"], "legacy")
        enclosed = building_loads(self.model, kc_rule="surface_count")[2]
        for case in enclosed.wind:
            # Roof + two clad columns (+ no internal: |Cpi| 0.2 < 0.4) = 3 surfaces: Kc,e 0.8, as today.
            self.assertEqual((case.factors["effective_surfaces"], case.factors["kce"]), (3, 0.8))
        today = building_loads(self.model, kc_rule="legacy")[2]
        self.assertEqual(enclosed.wind_case("Left +").roof_strips, today.wind_case("Left +").roof_strips)
        self.assertEqual(enclosed.wind_case("Left +").roof_cpi_kn_m, today.wind_case("Left +").roof_cpi_kn_m)
        # Cyclonic enclosed, Cpi ±0.6: internal is effective, 4 surfaces, Kc,e = Kc,i = 0.8 (as today).
        cyclonic = building_model_from_preset(VARIANTS["enclosed cyclonic region C"])
        new = building_loads(cyclonic, kc_rule="surface_count")[2].wind_case("Front -")
        old = building_loads(cyclonic, kc_rule="legacy")[2].wind_case("Front -")
        self.assertEqual((new.factors["effective_surfaces"], new.factors["kce"]), (4, 0.8))
        self.assertEqual((new.roof_strips, new.roof_cpi_kn_m, new.wall_cpi_kn_m),
                         (old.roof_strips, old.roof_cpi_kn_m, old.wall_cpi_kn_m))
        # One open side wall: roof + one clad column + internal (|Cpi| >= 0.4) = 3, Kc,e 0.8.
        open_side = building_model_from_preset(VARIANTS["5 deg skillion, right open, bearing north"])
        case = building_loads(open_side, kc_rule="surface_count")[2].wind_case("Left +")
        self.assertGreaterEqual(min(abs(v) for v in case.cpi.values()), 0.4)
        self.assertEqual((case.factors["effective_surfaces"], case.factors["kce"]), (3, 0.8))
        # A frame whose columns carry no cladding sees only the roof: Kc,e 1.0, so Ka now acts.
        data = self.model.to_dict()
        data["walls"][0] = {"face": "left", "segments": [{"from_mm": 0, "to_mm": 16000, "clad": True},
                                                         {"from_mm": 16000, "to_mm": 40000, "clad": False}]}
        data["walls"][1] = data["walls"][0] | {"face": "right"}
        frame = building_loads(BuildingModel.from_dict(data), kc_rule="surface_count")[4]
        case = frame.wind_case("Left -")
        self.assertEqual((case.factors["effective_surfaces"], case.factors["kce"]), (1, 1.0))
        a, b, q = case.roof_strips[0]
        result = bl.calculate_wind(bl.replace(bl.base_wind_inputs(BuildingModel.from_dict(data), 8.0),
                                              orientation=case.source_direction))
        cpe_min = low_pitch_cpe_values(result.h_over_d)[0][4]
        zone_m = 0.5 * result.h_m / math.cos(math.radians(7.5))
        factor = max(area_reduction_factor(result.inputs, "roof", zone_m * 8.0) * 1.0, 0.8)
        self.assertAlmostEqual(q, -cpe_min * result.wu_kn_m2 * factor * 8.0, places=12)

    def test_unsupported_features_raise(self):
        data = building_model_from_preset(preset_variant(canopies__right_type="Canopy",
                                                         canopies__right_length_mm="3000")).to_dict()
        data["attachments"][0]["to_gridline"] = "GL4"
        data["attachments"][0]["end_offset_mm"] = 3000
        with self.assertRaisesRegex(BuildingLoadsScopeError, "eave beam"):
            building_loads(BuildingModel.from_dict(data))
        skillion = building_model_from_preset(preset_variant(
            geometry__roof_shape="Skillion", canopies__right_type="Canopy", canopies__right_length_mm="3000"))
        with self.assertRaisesRegex(BuildingLoadsScopeError, "skillion"):
            building_loads(skillion)
        data = self.model.to_dict()
        data["partitions"] = [{"id": "P", "direction": "transverse", "position_mm": 8000, "from_mm": 0, "to_mm": 21000}]
        with self.assertRaisesRegex(BuildingLoadsScopeError, "partitions"):
            building_loads(BuildingModel.from_dict(data))
        with self.assertRaisesRegex(ValueError, "basis must be"):
            building_loads(self.model, basis="2d")

    def test_canopy_and_lean_to_descriptors(self):
        preset = preset_variant(
            canopies__left_type="Lean-to", canopies__left_length_mm="6000",
            canopies__left_lean_enclosure={"left": True, "right": True, "front": False, "back": True},
            canopies__right_type="Canopy", canopies__right_length_mm="4000", canopies__right_pitch_deg="3")
        model = building_model_from_preset(preset)
        for basis in ("legacy_2d", "building"):
            frames = building_loads(model, basis=basis)
            end, interior = frames[0], frames[2]
            case = interior.wind_case("Left +")
            self.assertEqual(set(interior.attachment_gravity), {"left", "right"})
            self.assertIn("left_lean", case.wall_cpe_kn_m)       # lean-to outer column, not the main left column
            self.assertNotIn("left", case.wall_cpe_kn_m)
            self.assertEqual(set(case.canopy_cpi_kn_m["Cpi +"]), {"left", "right"})
            self.assertIn("left", case.wall_cpi_kn_m["Cpi +"])   # net internal pressure on the shared column
            # The continuous roof runs from the lean-to tip to the canopy tip.
            self.assertGreater(case.roof_length_m, interior.wind_case("Left +").roof_strips[-1][0])
            self.assertAlmostEqual(case.roof_length_m, building_loads(self.model)[2].wind_case("Left +").roof_length_m
                                   + 6.0 / math.cos(math.radians(5)) + 4.0 / math.cos(math.radians(3)), places=9)
            for name in ("Left +", "Right -"):
                e, i = end.wind_case(name), interior.wind_case(name)
                self.assertEqual({k: v * 2 for k, v in e.canopy_cpi_kn_m["Cpi -"].items()}, i.canopy_cpi_kn_m["Cpi -"])
                self.assertEqual({k: v * 2 for k, v in e.wall_cpe_kn_m.items()}, i.wall_cpe_kn_m)

    def test_lean_to_opening_takes_the_cpe_of_where_it_is(self):
        """An open lean-to front in left wind is a side wall starting at the windward edge (the lean-to tip)."""
        preset = preset_variant(
            canopies__left_type="Lean-to", canopies__left_length_mm="6000",
            canopies__left_lean_enclosure={"left": True, "right": True, "front": False, "back": True})
        model = building_model_from_preset(preset)
        engine = bl._Engine(model, "building", "surface_count")
        nominal = engine.result(model.wind_source_direction("left"), 8.0)
        main, lean = engine.chamber_cpis(nominal, "left", "Left +")
        area = 6.0 * 6.0
        expected = opening_area_reduction_factor("side_wall", area) * bl.side_wall_band_cpe(nominal.h_m, 0.0, 6.0)
        self.assertAlmostEqual(lean["left_lean_to"]["Cpi +"], expected, places=12)
        self.assertEqual(main, {"Cpi +": 0.2, "Cpi -": -0.2})  # the main chamber stays enclosed
        legacy = bl._Engine(model, "legacy_2d", "legacy")
        _, legacy_lean = legacy.chamber_cpis(legacy.result(model.wind_source_direction("left"), 8.0), "left", "Left +")
        # The windward-edge band is more severe than the 2D first-bay average.
        self.assertLess(lean["left_lean_to"]["Cpi +"], legacy_lean["left_lean_to"]["Cpi +"])

    def test_part_length_lean_to(self):
        """A lean-to on GL2-GL4: half shares at its end gridlines, external main wall beyond it."""
        preset = preset_variant(
            canopies__left_type="Lean-to", canopies__left_length_mm="6000",
            canopies__left_lean_enclosure={"left": True, "right": True, "front": True, "back": True})
        data = building_model_from_preset(preset).to_dict()
        data["attachments"][0].update(from_gridline="GL2", to_gridline="GL4")
        model = BuildingModel.from_dict(data)
        frames = building_loads(model)
        self.assertEqual([f.attachment_tributary_mm.get("left") for f in frames],
                         [None, 4000.0, 8000.0, 4000.0, None, None])
        self.assertEqual([f.wall_tributary_mm.get("left_lean") for f in frames],
                         [None, 4000.0, 8000.0, 4000.0, None, None])
        left_plus = [f.wind_case("Left +") for f in frames]
        full = left_plus[0].wall_cpe_kn_m["left"] * 2  # GL1 is a half-bay plain end frame
        # Main left column: external beyond the lean-to, internal alongside it.
        self.assertAlmostEqual(left_plus[1].wall_cpe_kn_m["left"], full / 2, places=12)
        self.assertNotIn("left", left_plus[2].wall_cpe_kn_m)
        self.assertAlmostEqual(left_plus[4].wall_cpe_kn_m["left"], full, places=12)
        self.assertAlmostEqual(left_plus[1].wall_cpe_kn_m["left_lean"], full / 2, places=12)
        self.assertEqual(left_plus[1].canopy_roof_scale, {"left": 0.5})
        self.assertEqual(left_plus[2].canopy_roof_scale, {})
        # Lean-to outer wall load is conserved over its 16 m.
        total = sum(c.wall_cpe_kn_m.get("left_lean", 0.0) for c in left_plus)
        self.assertAlmostEqual(total, left_plus[2].wall_cpe_kn_m["left_lean"] / 8.0 * 16.0, places=9)
        # End wind on the lean-to roof integrates the bands over the lean-to only.
        stations = [g.y_mm for g in model.gridlines]
        front = frames[1].wind_case("Front -")
        result = bl.calculate_wind(bl.replace(bl.base_wind_inputs(model, 8.0), orientation=front.source_direction))
        h_mm = result.h_m * 1000.0
        bands = [(a * h_mm, min(40000.0, b * h_mm), cmin)
                 for _, a, b, _, cmin in low_pitch_cpe_values(result.h_m / 40.0)]
        bands = [(max(a, 8000.0), min(b, 24000.0), c) for a, b, c in bands if min(b, 24000.0) > max(a, 8000.0)]
        expected = -numeric_band_integral_mm(bands, stations, 1) / 1000.0 * front.wu_kn_m2 * 0.8
        self.assertAlmostEqual(front.canopy_roof_kn_m["left"], expected, delta=1e-4 * max(1.0, abs(expected)))

    def test_fully_open_canopies_are_free_roofs(self):
        preset = preset_variant(
            canopies__right_type="Canopy", canopies__right_length_mm="4000", canopies__right_pitch_deg="3",
            wind__wall_enclosure={"left": False, "right": False, "front": False, "back": False})
        case = building_loads(building_model_from_preset(preset))[2].wind_case("Left +")
        self.assertEqual(case.roof_basis, "free_roof_cpn")
        self.assertIn("right", case.canopy_cpe_kn_m)
        self.assertEqual((case.canopy_cpi_kn_m, case.wall_cpe_kn_m), ({}, {}))

    def test_output_is_json(self):
        for frames in (self.frames, self.legacy):
            text = json.dumps(building_loads_to_dict(frames), allow_nan=False)
            self.assertEqual(json.loads(text)["schema"], bl.SCHEMA)


class ExpectedDifferenceTests(unittest.TestCase):
    """building v legacy_2d (today's 2D envelope), each difference attributed."""

    @staticmethod
    def max_outward(case):
        return max(q for _, _, q in case.roof_strips)

    def envelope(self, legacy_frames, names):
        interior = legacy_frames[len(legacy_frames) // 2]
        return max(self.max_outward(interior.wind_case(n)) for n in names)

    def test_low_pitch_end_wind_never_exceeds_the_2d_envelope(self):
        """<10 degrees, both ends clad: bands per frame ≤ worst window; Md per end ≤ the larger end's."""
        for name in ("factory defaults (enclosed 7.5 deg gable)", "enclosed cyclonic region C"):
            model = building_model_from_preset(VARIANTS[name])
            new, old = building_loads(model), building_loads(model, basis="legacy_2d")
            roof_env = self.envelope(old, ("End +", "End -"))
            interior_old = old[len(old) // 2].wind_case("End +")
            wall_env = max(abs(v) for v in interior_old.wall_cpe_kn_m.values())
            for frame in new:
                for case_name in END_CASES:
                    case = frame.wind_case(case_name)
                    with self.subTest(building=name, gridline=frame.gridline, case=case_name):
                        self.assertLessEqual(case.wu_kn_m2, interior_old.wu_kn_m2 + 1e-12)
                        self.assertLessEqual(self.max_outward(case), roof_env + 1e-9)
                        self.assertLessEqual(max(abs(v) for v in case.wall_cpe_kn_m.values()), wall_env + 1e-9)

    def test_steep_gable_end_wind_exceeds_the_2d_downwind_rafter_value_near_the_ends(self):
        """>=10 degrees: Table 5.3(A) bands at all pitches (D4) are more severe than the 2D value
        near the windward end and less severe further in. Today's 2D is unconservative there."""
        model = building_model_from_preset(preset_variant(geometry__roof_pitch_deg="15"))
        new, old = building_loads(model), building_loads(model, basis="legacy_2d")
        roof_env = self.envelope(old, ("End +", "End -"))
        near = max(self.max_outward(new[1].wind_case(n)) for n in END_CASES)
        middle = max(self.max_outward(new[3].wind_case(n)) for n in ("Front -", "Front +"))
        self.assertGreater(near, roof_env * 1.2)
        self.assertLess(middle, roof_env)

    def test_end_frames_are_well_inside_the_2d_envelope(self):
        model = building_model_from_preset(DEFAULTS)
        new, old = building_loads(model), building_loads(model, basis="legacy_2d")
        env = self.envelope(old, ("End +", "End -"))
        for frame in (new[0], new[-1]):
            self.assertLess(max(self.max_outward(frame.wind_case(n)) for n in END_CASES), 0.6 * env)

    def test_open_end_wall_cpi_uses_its_own_position_for_side_wind(self):
        """Finding 2: legacy averages the side-wall bands along the first bay of the *length*;
        the building basis averages them across the open end wall's own span."""
        model = building_model_from_preset(VARIANTS["15 deg gable, front open"])
        new, old = building_loads(model)[2], building_loads(model, basis="legacy_2d")[2]
        result = bl.calculate_wind(bl.base_wind_inputs(model, 8.0))
        across = bl.side_wall_band_cpe(result.h_m, 0.0, 21.0)
        area = 21.0 * 6.0
        ka = opening_area_reduction_factor("side_wall", area)
        self.assertAlmostEqual(new.wind_case("Left +").cpi["Cpi +"], ka * across, places=12)
        self.assertNotAlmostEqual(new.wind_case("Left +").cpi["Cpi +"], old.wind_case("Left +").cpi["Cpi +"], places=3)

    def test_unclad_end_runs_both_directions(self):
        """Legacy runs only wind from the front when an end is open; the building basis runs both ends."""
        model = building_model_from_preset(VARIANTS["15 deg gable, front open"])
        frame = building_loads(model)[2]
        self.assertNotEqual(frame.wind_case("Front +").cpi, frame.wind_case("Back +").cpi)


class DesktopParityTests(unittest.TestCase):
    """An interior frame's legacy_2d descriptors reproduce main.py's own basic load cases."""

    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls._appdata = tempfile.TemporaryDirectory(prefix="portalcalc_building_loads_")
        os.environ["APPDATA"] = cls._appdata.name
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def assert_arrows_equal(self, expected, actual, label):
        self.assertEqual(len(actual), len(expected), f"{label}: arrow count")
        for i, (e, a) in enumerate(zip(expected, actual)):
            for k, (ev, av) in enumerate(zip(e, a)):
                self.assertLessEqual(abs(ev - av), 1e-9 * max(1.0, abs(ev)), f"{label} arrow {i}[{k}]: {e} v {a}")

    def test_interior_frame_matches_the_2d_engine(self):
        w = self.window
        for name, preset in VARIANTS.items():
            with self.subTest(building=name):
                w.apply_preset_data(copy.deepcopy(preset))
                w._suppress_auto_web_split_on_solve = True
                w._suppress_panel_topology_search = True
                w.generate_and_solve()
                self.assertIsNotNone(w.structure, w.results_box.toPlainText())
                model = building_model_from_preset(w.collect_preset_data())
                self.assertEqual(model.engine_scope_issues(), [])
                frame = building_loads(model, basis="legacy_2d")[2]
                self.assertEqual(frame.tributary_mm, model.bay_lengths_mm[2])
                bases = sorted((n for n in w.structure.nodes if abs(n.y) < 1e-6), key=lambda n: n.x)
                bases = (bases[0], bases[-1])
                expected = w.single_load_arrows
                labels = [g.label for g in frame.gravity] + wind_labels()
                # Guard against vacuous passes: gravity and all 12 Cpe cases always load.
                compared = sum(1 for label in labels if expected.get(label))
                self.assertGreaterEqual(compared, len(frame.gravity) + 12)
                for label in labels:
                    actual = frame_case_arrows(frame, label, w.concept_roof_top_nodes, w.concept_main_top_nodes, bases)
                    self.assert_arrows_equal(expected.get(label, []), actual, f"{name}: {label}")


if __name__ == "__main__":
    unittest.main()
