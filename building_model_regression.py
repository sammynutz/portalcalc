"""Building model (portalcalc.building.v1) regression.

ModelTests are Qt-free: schema round trip, validation messages, the preset
adapter round trip, tributaries, sizing groups, scope issues and wind source
directions. DesktopTests load ``frame_preset`` output into an offscreen
MainWindow and check it reproduces the original inputs, bracing geometry and
wind directions exactly.
"""
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import building_model as bm
from building_model import BuildingModel, BuildingModelError, building_model_from_preset

ROOT = Path(__file__).parent
DEFAULTS = json.loads((ROOT / "presets/factory_defaults.json").read_text(encoding="utf-8"))


def normalised(value):
    """Compare presets the way the desktop reads them: numeric text as numbers."""
    if isinstance(value, dict):
        return {key: normalised(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalised(item) for item in value]
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return value
        return number if math.isfinite(number) else value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def assert_contains(test, original, produced, path="preset"):
    """Every value in ``original`` appears unchanged in ``produced``."""
    if isinstance(original, dict):
        test.assertIsInstance(produced, dict, path)
        for key, item in original.items():
            test.assertIn(key, produced, f"{path}.{key} missing")
            assert_contains(test, item, produced[key], f"{path}.{key}")
    else:
        test.assertEqual(normalised(original), normalised(produced), path)


def variant_preset():
    """Left lean-to, right open canopy, mixed enclosure, bearing north, short last bay."""
    preset = copy.deepcopy(DEFAULTS)
    preset["wind"].update({"orientation": "27°", "building_length_m": "43.5",
                           "wall_enclosure": {"left": True, "right": False, "front": True, "back": False}})
    preset["canopies"].update({
        "left_type": "Lean-to", "left_length_mm": "6000", "left_eave_height_mm": "3500",
        "left_lean_enclosure": {"left": True, "right": True, "front": False, "back": True},
        "left_lean_outer_wall_clad": "Yes",  # the desktop derives this from the enclosure
        "right_type": "Canopy", "right_length_mm": "4500", "right_pitch_deg": "3",
    })
    preset["sections"].update({"left_lean_to_column": "150UC23.4", "right_canopy_top_chord": "75x75x3 SHS"})
    return preset


def rich_building():
    """A building using the features the current engine cannot yet analyse."""
    data = building_model_from_preset(DEFAULTS).to_dict()
    data["frame_types"].append({
        "id": "LIGHT_END", "kind": "propped_end", "geometry": {"frame_system": "Rafter"},
        "sections": {"top_chord": "200UB25.4"}, "prop_x_mm": [7000, 14000]})
    data["gridlines"][0]["frame_type"] = "LIGHT_END"
    data["attachments"] = [{
        "id": "office_lean", "side": "left", "kind": "lean_to",
        "from_gridline": "GL2", "to_gridline": "GL4", "end_offset_mm": 3000, "width_mm": 5000,
        "properties": {"eave_height_mm": "3200", "pitch_deg": "5"}, "sections": {"lean_to_column": "100x100x3 SHS"}}]
    data["walls"] = [
        {"face": "left", "segments": [{"from_mm": 0, "to_mm": 16000, "clad": True},
                                      {"from_mm": 16000, "to_mm": 24000, "clad": True},
                                      {"from_mm": 24000, "to_mm": 40000, "clad": False}]},
        {"face": "right", "clad": True},
        {"face": "front", "clad": True},
        {"face": "back", "segments": [{"from_mm": 0, "to_mm": 10500, "clad": False},
                                      {"from_mm": 10500, "to_mm": 21000, "clad": True}]},
        {"face": "office_lean/outer", "clad": True},
        {"face": "office_lean/front", "clad": True},
        {"face": "office_lean/back", "clad": False},
    ]
    data["openings"] = [
        {"id": "RD1", "face": "right", "kind": "roller_door", "from_mm": 9000, "to_mm": 13000, "head_mm": 4500},
        {"id": "PA1", "face": "front", "kind": "door", "from_mm": 1000, "to_mm": 1900, "head_mm": 2100},
    ]
    data["partitions"] = [
        {"id": "P1", "direction": "transverse", "position_mm": 8000, "from_mm": 0, "to_mm": 21000},
        {"id": "P2", "direction": "longitudinal", "position_mm": 10500, "from_mm": 8000, "to_mm": 24000},
    ]
    return data


class ModelTests(unittest.TestCase):
    def test_factory_defaults_become_a_uniform_building(self):
        model = building_model_from_preset(DEFAULTS)
        self.assertEqual(model.gridline_ids, ("GL1", "GL2", "GL3", "GL4", "GL5", "GL6"))
        self.assertEqual(model.bay_lengths_mm, (8000.0,) * 5)
        self.assertEqual(model.tributary_widths_mm, (4000.0, 8000.0, 8000.0, 8000.0, 8000.0, 4000.0))
        self.assertAlmostEqual(sum(model.tributary_widths_mm), model.length_mm)
        self.assertEqual(model.engine_scope_issues(), [])
        self.assertEqual([g.gridlines for g in model.resolved_sizing_groups()], [model.gridline_ids])
        self.assertEqual(model.attachments, ())
        self.assertTrue(all(wall.fully_clad for wall in model.walls))

    def test_json_round_trip_is_exact(self):
        for model in (building_model_from_preset(DEFAULTS), building_model_from_preset(variant_preset()),
                      BuildingModel.from_dict(rich_building())):
            text = json.dumps(model.to_dict(), allow_nan=False)
            self.assertEqual(BuildingModel.from_dict(json.loads(text)), model)
            self.assertEqual(BuildingModel.from_dict(json.loads(text)).to_dict(), model.to_dict())

    def test_frame_preset_reproduces_the_original_preset_on_every_gridline(self):
        legacy = copy.deepcopy(DEFAULTS)
        legacy["wind"].pop("wall_enclosure")
        legacy["wind"]["frame_type"] = "2 Sided"
        for preset in (DEFAULTS, variant_preset(), legacy):
            model = building_model_from_preset(preset)
            for gridline_id in model.gridline_ids:
                produced = model.frame_preset(gridline_id)
                expected = copy.deepcopy(preset)
                expected.setdefault("wind", {})["wall_enclosure"] = produced["wind"]["wall_enclosure"]
                assert_contains(self, expected, produced, f"{gridline_id}")
        legacy_walls = building_model_from_preset(legacy).frame_preset("GL3")["wind"]["wall_enclosure"]
        self.assertEqual(legacy_walls, {"left": True, "right": False, "front": False, "back": True})

    def test_variant_preset_maps_attachments_walls_and_short_bay(self):
        model = building_model_from_preset(variant_preset())
        self.assertEqual(model.bay_lengths_mm, (8000.0,) * 5 + (3500.0,))
        self.assertEqual(model.frame_preset("GL3")["loads"]["bay_size_mm"], "8000")
        lean, canopy = model.attachments
        self.assertEqual((lean.id, lean.kind, lean.width_mm), ("left_lean_to", "lean_to", 6000.0))
        self.assertEqual((canopy.id, canopy.kind, canopy.width_mm), ("right_canopy", "canopy", 4500.0))
        self.assertEqual(lean.sections["lean_to_column"], "150UC23.4")
        self.assertIn("canopy_top_chord", lean.sections)  # a lean-to rafter uses the canopy chords
        self.assertFalse(any(key.startswith("right_") for key in lean.sections))
        self.assertNotIn("lean_outer_wall_clad", lean.properties)
        self.assertEqual(model.wall("left_lean_to/outer").segments, ((0.0, 43500.0, True),))
        self.assertEqual(model.face_extent_mm("left_lean_to/front"), (-6000.0, 0.0))
        self.assertEqual(model.wall("back").segments, ((0.0, 21000.0, False),))
        self.assertEqual(model.engine_scope_issues(), [])  # a short last bay is today's layout rule
        data = model.to_dict()
        data["gridlines"][2]["y_mm"] = 15000.0
        self.assertIn("unequal bays", " ".join(BuildingModel.from_dict(data).engine_scope_issues()))

    def test_lean_to_inner_wall_syncs_main_side_wall(self):
        preset = variant_preset()
        preset["wind"]["wall_enclosure"]["left"] = False
        model = building_model_from_preset(preset)
        self.assertTrue(model.wall("left").fully_clad)

    def test_rich_building_validates_and_reports_scope(self):
        model = BuildingModel.from_dict(rich_building())
        self.assertEqual(model.attachment_extent_mm(model.attachments[0]), (8000.0, 27000.0))
        self.assertEqual(model.attachments_at("GL1"), {})
        self.assertEqual(set(model.attachments_at("GL3")), {"left"})
        self.assertEqual(model.attachments_at("GL5"), {})
        # Left wall: clad to 24 m, so GL4 (girts in bay 3) is clad and GL5 onward are not.
        clad = [model.side_wall_clad_at("left", g) for g in model.gridline_ids]
        self.assertEqual(clad, [True, True, True, True, False, False])
        self.assertEqual(model.wall("left").segments, ((0.0, 24000.0, True), (24000.0, 40000.0, False)))
        groups = [g.gridlines for g in model.default_sizing_groups()]
        self.assertEqual(groups, [("GL1",), ("GL2", "GL3", "GL4"), ("GL5", "GL6")])
        issues = " | ".join(model.engine_scope_issues())
        for text in ("more than one frame type", "propped end", "part of the building length",
                     "extends past a gridline", "wall left is partly clad", "partitions"):
            self.assertIn(text, issues)
        preset = model.frame_preset("GL3")
        self.assertEqual(preset["canopies"]["left_type"], "Lean-to")
        self.assertEqual(preset["canopies"]["left_lean_enclosure"],
                         {"left": True, "right": True, "front": True, "back": False})
        self.assertEqual(preset["sections"]["left_lean_to_column"], "100x100x3 SHS")
        self.assertEqual(model.frame_preset("GL1")["geometry"]["frame_system"], "Rafter")
        self.assertEqual(model.frame_preset("GL6")["wind"]["wall_enclosure"]["left"], False)
        with self.assertRaisesRegex(ValueError, "part of the length"):
            data = rich_building()
            data["attachments"][0]["kind"] = "canopy"
            data["walls"] = [w for w in data["walls"] if not w["face"].startswith("office_lean/")]
            BuildingModel.from_dict(data).layout_geometry()

    def test_explicit_sizing_groups(self):
        data = building_model_from_preset(DEFAULTS).to_dict()
        data["sizing_groups"] = [{"id": "ends", "gridlines": ["GL1", "GL6"]},
                                 {"id": "interior", "gridlines": ["GL2", "GL3", "GL4", "GL5"]}]
        model = BuildingModel.from_dict(data)
        self.assertEqual([g.id for g in model.resolved_sizing_groups()], ["ends", "interior"])

    def test_sizing_groups_share_one_frame_and_carry_frame_inputs(self):
        data = building_model_from_preset(variant_preset()).to_dict()
        data["attachments"][0].update(from_gridline="GL2", to_gridline="GL4")  # part-length lean-to
        model = BuildingModel.from_dict(data)
        self.assertEqual([g.gridlines for g in model.default_sizing_groups()],
                         [("GL1", "GL5", "GL6", "GL7"), ("GL2", "GL3", "GL4")])
        mixed = copy.deepcopy(data)
        mixed["sizing_groups"] = [{"id": "a", "gridlines": ["GL1", "GL2"]},
                                  {"id": "b", "gridlines": ["GL3", "GL4", "GL5", "GL6", "GL7"]}]
        with self.assertRaisesRegex(BuildingModelError, "share one 2D frame"):
            BuildingModel.from_dict(mixed)
        sized = copy.deepcopy(data)
        sized["sizing_groups"] = [
            {"id": "plain", "gridlines": ["GL1", "GL5", "GL6", "GL7"],
             "frame_inputs": {"sections": {"left_column": "250 UB 31.4 (31.5 kg/m)"},
                              "geometry": {"truss_depth_mm": "850"}}},
            {"id": "lean", "gridlines": ["GL2", "GL3", "GL4"],
             "frame_inputs": {"sections": {"left_lean_to_column": "200 UB 22.3"}, "canopies": {"left_depth_mm": "450"}}}]
        model = BuildingModel.from_dict(sized)
        self.assertEqual(BuildingModel.from_dict(model.to_dict()), model)
        plain, lean = model.frame_preset("GL5"), model.frame_preset("GL3")
        self.assertEqual((plain["sections"]["left_column"], plain["geometry"]["truss_depth_mm"]),
                         ("250 UB 31.4 (31.5 kg/m)", "850"))
        self.assertEqual(plain["canopies"]["left_type"], "None")
        self.assertEqual((lean["sections"]["left_lean_to_column"], lean["canopies"]["left_depth_mm"]),
                         ("200 UB 22.3", "450"))
        self.assertNotEqual(lean["geometry"]["truss_depth_mm"], "850")
        bad = copy.deepcopy(sized)
        bad["sizing_groups"][0]["frame_inputs"] = {"wind": {}}
        with self.assertRaisesRegex(BuildingModelError, "may only hold"):
            BuildingModel.from_dict(bad)
        bad["sizing_groups"][0]["frame_inputs"] = {"geometry": {"span_mm": "1"}}
        with self.assertRaisesRegex(BuildingModelError, "must not set span_mm"):
            BuildingModel.from_dict(bad)

    def test_wind_source_directions(self):
        model = building_model_from_preset(DEFAULTS)  # north = E
        self.assertEqual([model.wind_source_direction(f) for f in bm.WIND_FROM], ["S", "N", "W", "E"])
        model = building_model_from_preset(variant_preset())  # north = 27 degrees
        bearings = [model.wind_source_direction(f) for f in bm.WIND_FROM]
        for got, expected in zip(bearings, (243.0, 63.0, 333.0, 153.0)):
            self.assertAlmostEqual(got, expected)
        data = model.to_dict()
        data["site"]["north"] = "Any"
        self.assertEqual(BuildingModel.from_dict(data).wind_source_direction("left"), "Any")

    def test_validation_messages(self):
        base = building_model_from_preset(DEFAULTS).to_dict()

        def mutate(change):
            data = copy.deepcopy(base)
            change(data)
            return data

        def set_item(container, key, value):
            container[key] = value

        cases = [
            (lambda d: set_item(d, "schema", "portalcalc.building.v0"), "unsupported building schema"),
            (lambda d: set_item(d["site"], "region", "Z9"), "not a supported wind region"),
            (lambda d: set_item(d["site"], "north", "sideways"), "site.north"),
            (lambda d: set_item(d["site"], "terrain_category", 5), "terrain_category"),
            (lambda d: set_item(d["main"], "span_mm", -1), "main.span_mm must be greater than zero"),
            (lambda d: set_item(d["gridlines"][0], "y_mm", 100), "gridlines[0].y_mm must be 0"),
            (lambda d: set_item(d["gridlines"][2], "y_mm", 8000), "gridlines[2].y_mm must be greater"),
            (lambda d: set_item(d["gridlines"][1], "id", "GL1"), "used more than once"),
            (lambda d: set_item(d["gridlines"][1], "frame_type", "NOPE"), "not a defined frame type"),
            (lambda d: set_item(d["frame_types"][0]["geometry"], "span_mm", "9000"), "use main"),
            (lambda d: (d["frame_types"].append({"id": "P", "kind": "propped_end", "prop_x_mm": [5000]}),
                        set_item(d["gridlines"][2], "frame_type", "P")), "only allowed on an end gridline"),
            (lambda d: d["frame_types"].append({"id": "P", "kind": "propped_end"}), "needs at least one mullion"),
            (lambda d: d["frame_types"].append({"id": "P", "kind": "propped_end", "prop_x_mm": [0]}),
             "strictly inside the span"),
            (lambda d: d["walls"].pop(), "walls is missing back"),
            (lambda d: set_item(d["walls"][0], "segments", [{"from_mm": 0, "to_mm": 9000, "clad": True},
                                                            {"from_mm": 10000, "to_mm": 40000, "clad": True}]),
             "give either clad or segments"),
            (lambda d: (d["walls"][0].pop("clad"),
                        set_item(d["walls"][0], "segments", [{"from_mm": 0, "to_mm": 9000, "clad": True},
                                                             {"from_mm": 10000, "to_mm": 40000, "clad": True}])),
             "must start at 9000"),
            (lambda d: d["walls"].append({"face": "left_lean_to/outer", "clad": True}), "is not a wall"),
            (lambda d: d["openings"].append({"id": "D", "face": "left", "from_mm": 1000, "to_mm": 2000,
                                             "head_mm": 7000}), "above the wall"),
            (lambda d: (set_item(d["walls"][2], "clad", False),
                        d["openings"].append({"id": "D", "face": "front", "from_mm": 1000, "to_mm": 2000,
                                              "head_mm": 2000})), "within clad parts"),
            (lambda d: d["partitions"].append({"id": "P", "direction": "transverse", "position_mm": 40000,
                                               "from_mm": 0, "to_mm": 21000}), "strictly inside the building"),
            (lambda d: d["attachments"].append({"id": "A", "side": "left", "kind": "canopy", "width_mm": 3000,
                                                "from_gridline": "GL3", "to_gridline": "GL2"}),
             "must come before"),
            (lambda d: d["attachments"].extend([
                {"id": "A", "side": "left", "kind": "canopy", "width_mm": 3000, "from_gridline": "GL1", "to_gridline": "GL3"},
                {"id": "B", "side": "left", "kind": "canopy", "width_mm": 3000, "from_gridline": "GL3", "to_gridline": "GL5"}]),
             "overlap on the left side"),
            (lambda d: d["attachments"].append({"id": "A", "side": "left", "kind": "canopy", "width_mm": 3000,
                                                "from_gridline": "GL1", "to_gridline": "GL3", "start_offset_mm": 500}),
             "past the front"),
            (lambda d: d["attachments"].append({"id": "A", "side": "left", "kind": "canopy", "width_mm": 3000,
                                                "from_gridline": "GL1", "to_gridline": "GL3", "end_offset_mm": 8000}),
             "less than the adjacent bay"),
            (lambda d: set_item(d, "sizing_groups", [{"id": "g", "gridlines": ["GL1", "GL2"]}]),
             "missing GL3"),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                with self.assertRaises(BuildingModelError) as raised:
                    BuildingModel.from_dict(mutate(change))
                self.assertIn(message, str(raised.exception))

    def test_module_is_qt_free(self):
        code = ("import sys, building_model; "
                "bad = [m for m in sys.modules if m.startswith(('PySide6', 'main'))]; "
                "assert not bad, bad")
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


class DesktopTests(unittest.TestCase):
    """frame_preset loaded into the real desktop reproduces today's inputs."""

    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls._appdata = tempfile.TemporaryDirectory(prefix="portalcalc_building_")
        os.environ["APPDATA"] = cls._appdata.name
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def collected(self, preset):
        self.window.apply_preset_data(copy.deepcopy(preset))
        return self.window.collect_preset_data()

    def check_round_trip(self, preset):
        before = self.collected(preset)
        model = building_model_from_preset(before)
        layout_before = self.window.current_bracing_layout_geometry()
        directions = {case: self.window.wind_source_direction_for_case(self.window.wind_orientation_selector.currentText(), case)
                      for case in ("Left +", "Right +", "End +")}
        for gridline_id in (model.gridline_ids[0], model.gridline_ids[2]):
            with self.subTest(gridline=gridline_id):
                after = self.collected(model.frame_preset(gridline_id))
                # Text inputs keep their typed form ("40.0" v "40"); values must agree.
                self.assertEqual(normalised(after), normalised(before))
        self.assertEqual(model.layout_geometry(), layout_before)
        self.assertEqual(directions["Left +"], model.wind_source_direction("left"))
        self.assertEqual(directions["Right +"], model.wind_source_direction("right"))
        if not (model.wall("front").fully_clad and model.wall("back").fully_clad):
            self.assertEqual(directions["End +"], model.wind_source_direction("front"))
        return model

    def test_factory_defaults(self):
        self.check_round_trip(DEFAULTS)

    def test_lean_to_canopy_and_open_walls(self):
        model = self.check_round_trip(variant_preset())
        self.assertEqual({a.kind for a in model.attachments}, {"lean_to", "canopy"})


if __name__ == "__main__":
    unittest.main()
