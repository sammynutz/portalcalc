"""Phase 6: calculation service v2, a 3D building checked with per-gridline loads.

ContractTests are Qt-free. They cover:
- v2 request preparation and its digest;
- rejections;
- the v1 path and digest staying unchanged;
- ``building_with_frame_inputs`` and ``reviewed_building``;
- the result envelope.

HeadlessTests run the real offscreen backend:
- v2 checkSpecified covers every combination and returns per-gridline
  evidence;
- the returned building resubmits to the same result;
- legacy_2d is consistent with v1.

ReviewTests open a v2 request in Configurator Review and return it.

AutoSizeTests (``PORTALCALC_SLOW_TESTS=1``): v2 autoSize returns a verified,
resubmittable building.
"""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
from portalcalc.resources import data_path
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PORTALCALC_LICENSE_ENFORCED", "0")

from portalcalc.building.model import building_model_from_preset
from calculation_service_regression import BASELINE_INPUT_DIGEST
from portalcalc.service.configurator_review import reviewed_building
from portalcalc.service.contract import (REQUEST_SCHEMA_V2, RESULT_SCHEMA_V2, InvalidCalculationRequest,
                                PortalCalcCalculationService, UnsupportedCalculationInput,
                                building_with_frame_inputs, prepare_request)

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = json.loads(data_path("presets/factory_defaults.json").read_text(encoding="utf-8"))


def factory_building():
    return building_model_from_preset(DEFAULTS).to_dict()


def part_length_lean_to_building():
    preset = copy.deepcopy(DEFAULTS)
    preset["canopies"].update(left_type="Lean-to", left_length_mm="6000",
                              left_lean_enclosure={"left": True, "right": True, "front": True, "back": True})
    building = building_model_from_preset(preset).to_dict()
    building["attachments"][0].update(from_gridline="GL2", to_gridline="GL4")
    return building


def propped_end_building():
    """Lightweight rafter end frames at GL1 and GL6, propped by mullions at 7 m and 14 m."""
    building = factory_building()
    light = copy.deepcopy(building["frame_types"][0])
    light.update(id="LIGHT_END", kind="propped_end", prop_x_mm=[7000, 14000])
    light["geometry"]["frame_system"] = "Rafter"
    building["frame_types"].append(light)
    building["gridlines"][0]["frame_type"] = building["gridlines"][-1]["frame_type"] = "LIGHT_END"
    return building


def v2_request(building=None, mode="checkSpecified", **options):
    request = {"schema": REQUEST_SCHEMA_V2, "request_id": f"v2-{mode}", "mode": mode,
               "building": building or factory_building()}
    if options:
        request["options"] = options
    return request


class ContractTests(unittest.TestCase):
    def test_v1_is_unchanged(self):
        prepared = prepare_request({"request_id": "v1", "mode": "checkSpecified", "inputs": DEFAULTS})
        self.assertEqual(prepared.input_digest, BASELINE_INPUT_DIGEST)
        self.assertIsNone(prepared.building)

    def test_v2_prepares_frame_inputs_from_an_interior_gridline(self):
        building = factory_building()
        prepared = prepare_request(v2_request(building))
        self.assertEqual(prepared.schema, REQUEST_SCHEMA_V2)
        self.assertEqual((prepared.load_basis, prepared.representative_gridline), ("building", "GL3"))
        model = building_model_from_preset(DEFAULTS)
        expected = prepare_request({"request_id": "x", "mode": "checkSpecified", "inputs": model.frame_preset("GL3")})
        self.assertEqual(prepared.inputs, expected.inputs)
        self.assertEqual(prepared.building, json.loads(json.dumps(building)))
        # The digest freezes the building and options, not the derived frame.
        legacy = prepare_request(v2_request(building, load_basis="legacy_2d"))
        self.assertNotEqual(prepared.input_digest, legacy.input_digest)
        self.assertEqual(prepared.input_digest, prepare_request(v2_request(copy.deepcopy(building))).input_digest)

    def test_v2_rejections(self):
        cases = []
        with_inputs = v2_request()
        with_inputs["inputs"] = DEFAULTS
        cases.append((with_inputs, InvalidCalculationRequest, "not 'inputs'"))
        bad = factory_building()
        bad["gridlines"][1]["y_mm"] = 0
        cases.append((v2_request(bad), InvalidCalculationRequest, "building: gridlines[1].y_mm"))
        cases.append((v2_request(load_basis="2d"), InvalidCalculationRequest, "load_basis"))
        canopy = copy.deepcopy(DEFAULTS)
        canopy["canopies"].update(right_type="Canopy", right_length_mm="3000")
        offset = building_model_from_preset(canopy).to_dict()
        offset["attachments"][0].update(to_gridline="GL4", end_offset_mm=3000)
        cases.append((v2_request(offset), UnsupportedCalculationInput, "eave beam"))
        for request, error, text in cases:
            with self.subTest(text=text):
                with self.assertRaises(error) as raised:
                    prepare_request(request)
                self.assertIn(text, str(raised.exception))

    def test_each_sizing_group_gets_its_own_frame_inputs(self):
        building = part_length_lean_to_building()
        prepared = prepare_request(v2_request(building))
        self.assertEqual([(g["id"], g["gridlines"], g["representative"]) for g in prepared.groups],
                         [("G1", ["GL1", "GL5", "GL6"], "GL5"), ("G2", ["GL2", "GL3", "GL4"], "GL3")])
        self.assertEqual(prepared.groups[0]["inputs"]["canopies"]["left_type"], "None")
        self.assertEqual(prepared.groups[1]["inputs"]["canopies"]["left_type"], "Lean-to")
        self.assertEqual(prepared.inputs, prepared.groups[0]["inputs"])  # the largest group is primary
        with tempfile.TemporaryDirectory() as temp_dir:
            from portalcalc.service.configurator_review import ReviewConfigurationError, load_review_request
            path = Path(temp_dir) / "request.json"
            path.write_text(json.dumps(v2_request(building)), encoding="utf-8")
            with self.assertRaisesRegex(ReviewConfigurationError, "2 sizing groups"):
                load_review_request(path)

    def test_propped_end_frames_are_their_own_group_with_props(self):
        prepared = prepare_request(v2_request(propped_end_building()))
        self.assertEqual([(g["id"], g["gridlines"]) for g in prepared.groups],
                         [("G1", ["GL1", "GL6"]), ("G2", ["GL2", "GL3", "GL4", "GL5"])])
        geometry = prepared.groups[0]["inputs"]["geometry"]
        self.assertEqual((geometry["frame_system"], geometry["internal_column_count"],
                          geometry["internal_column_positions_m"], geometry["internal_column_connection"]),
                         ("Rafter", "Custom positions", "7, 14", "Pinned"))
        self.assertNotEqual(prepared.groups[1]["inputs"]["geometry"]["internal_column_count"], "Custom positions")
        self.assertEqual(prepared.inputs, prepared.groups[1]["inputs"])  # the interior group is primary

    def test_building_with_frame_inputs_and_review_merge(self):
        building = factory_building()
        frame = copy.deepcopy(building_model_from_preset(DEFAULTS).frame_preset("GL2"))
        frame["geometry"].update(truss_depth_mm="850", span_mm="99999")
        frame["sections"]["left_column"] = "310 UB 32.0 (32.0 kg/m)"
        frame["calculation_service"] = {"main_truss_panel_count": 12}
        frame["bracing"] = {"roof_brace_size": "M16 rod"}
        sized = building_with_frame_inputs(building, frame)
        self.assertEqual(sized["frame_types"][0]["geometry"]["truss_depth_mm"], "850")
        self.assertEqual(sized["frame_types"][0]["sections"]["left_column"], "310 UB 32.0 (32.0 kg/m)")
        self.assertEqual(sized["main"], building["main"])  # building geometry is never taken from a frame
        self.assertEqual(sized["design_inputs"]["calculation_service"], {"main_truss_panel_count": 12})
        self.assertNotEqual(sized["design_inputs"].get("bracing", {}).get("roof_brace_size"), "M16 rod")
        reviewed = reviewed_building(building, frame)
        self.assertEqual(reviewed["design_inputs"]["bracing"]["roof_brace_size"], "M16 rod")
        self.assertEqual(prepare_request(v2_request(reviewed)).inputs["sections"]["left_column"],
                         "310 UB 32.0 (32.0 kg/m)")

    def test_result_envelope(self):
        class FakeBackend:
            def __init__(self, building_results):
                self.building_results = building_results

            def calculate(self, request):
                dossier = {"inputs": copy.deepcopy(dict(request.inputs)),
                           "overall_governing": {"ratio": 0.9, "group": "TOP", "check": "x", "passes_ratio_screen": True}}
                if self.building_results is not None:
                    dossier["building_results"] = self.building_results
                return dossier

        result = PortalCalcCalculationService(FakeBackend({"gridlines": []})).run(v2_request())
        self.assertEqual(result["schema"], RESULT_SCHEMA_V2)
        self.assertEqual(result["building"], {"gridlines": []})
        self.assertEqual(result["provenance"]["calculation_policy"]["load_basis"], "building")
        self.assertEqual(result["provenance"]["calculation_policy"]["frame_inputs_from_gridline"], "GL3")
        with self.assertRaisesRegex(RuntimeError, "no building results"):
            PortalCalcCalculationService(FakeBackend(None)).run(v2_request())


class _Desktop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._appdata = tempfile.TemporaryDirectory(prefix="portalcalc_service_v2_")
        os.environ["APPDATA"] = cls._appdata.name
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def service(self):
        from portalcalc.service.headless import HeadlessPortalCalcBackend
        return PortalCalcCalculationService(HeadlessPortalCalcBackend())


class HeadlessTests(_Desktop):
    def test_check_specified_returns_per_gridline_evidence_and_resubmits(self):
        result = self.service().run(v2_request())
        self.assertEqual((result["schema"], result["status"]), (RESULT_SCHEMA_V2, "checked"))
        building = result["building"]
        self.assertEqual(building["load_basis"], "building")
        self.assertTrue(building["combinations"]["full_set_checked"])
        self.assertEqual([g["id"] for g in building["gridlines"]], ["GL1", "GL2", "GL3", "GL4", "GL5", "GL6"])
        self.assertEqual([g["tributary_mm"] for g in building["gridlines"]], [4000.0, 8000.0, 8000.0, 8000.0, 8000.0, 4000.0])
        self.assertEqual(building["gridlines"][3]["sections"], result["sizing"])
        self.assertEqual(len(building["distinct_load_sets"]), 6)
        governing = {g["path"]: g for g in building["governing"]}
        self.assertTrue(governing)
        for item in governing.values():
            self.assertTrue(item["gridlines"], item)
            self.assertTrue(set(item["gridlines"]) <= {g["id"] for g in building["gridlines"]})
        again = self.service().run(v2_request(building["sized_building"]))
        self.assertAlmostEqual(again["checks"]["overall_governing"]["ratio"],
                               result["checks"]["overall_governing"]["ratio"], places=9)
        self.assertEqual(again["sizing"], result["sizing"])

    def test_lean_to_and_canopy_building(self):
        preset = copy.deepcopy(DEFAULTS)
        preset["canopies"].update(
            left_type="Lean-to", left_length_mm="6000",
            left_lean_enclosure={"left": True, "right": True, "front": True, "back": True},
            right_type="Canopy", right_length_mm="4000", right_pitch_deg="3")
        result = self.service().run(v2_request(building_model_from_preset(preset).to_dict()))
        building = result["building"]
        self.assertTrue(building["combinations"]["full_set_checked"])
        self.assertIn("left_lean_to_column", result["sizing"])
        self.assertTrue(any(g["path"].startswith("LEFT_LEAN_COLUMN.") and g["gridlines"] for g in building["governing"]))
        self.assertTrue(any(g["path"].startswith("RIGHT_CANOPY_TOP.") and g["gridlines"] for g in building["governing"]))
        sized = building["sized_building"]
        self.assertEqual([a["id"] for a in sized["attachments"]], ["left_lean_to", "right_canopy"])
        lean, canopy = sized["attachments"]
        self.assertEqual(lean["sections"]["lean_to_column"], result["sizing"]["left_lean_to_column"])
        self.assertEqual(canopy["sections"]["canopy_top_chord"], result["sizing"]["right_canopy_top_chord"])
        again = self.service().run(v2_request(sized))
        self.assertEqual(again["sizing"], result["sizing"])
        self.assertAlmostEqual(again["checks"]["overall_governing"]["ratio"],
                               result["checks"]["overall_governing"]["ratio"], places=9)

    def test_sizing_groups_are_checked_separately_and_resubmit(self):
        result = self.service().run(v2_request(part_length_lean_to_building()))
        building, groups = result["building"], result["dossier"]["group_dossiers"]
        self.assertEqual([(g["id"], g["sizing_group"]) for g in building["gridlines"]],
                         [("GL1", "G1"), ("GL2", "G2"), ("GL3", "G2"), ("GL4", "G2"), ("GL5", "G1"), ("GL6", "G1")])
        self.assertTrue(building["combinations"]["full_set_checked"])
        self.assertEqual(set(building["combinations"]["by_group"]), {"G1", "G2"})
        worst = max(groups, key=lambda gid: groups[gid]["overall_governing"]["ratio"])
        self.assertEqual(result["dossier"]["overall_governing_group"], worst)
        self.assertAlmostEqual(result["checks"]["overall_governing"]["ratio"], groups[worst]["overall_governing"]["ratio"])
        self.assertIn("left_lean_to_column", groups["G2"]["sections"])
        self.assertTrue(all(g["gridlines"] and set(g["gridlines"]) <= {"GL2", "GL3", "GL4"}
                            for g in building["governing"] if g["sizing_group"] == "G2"))
        # A building whose groups share one frame: ends and interior sized as separate groups.
        plain = factory_building()
        plain["sizing_groups"] = [{"id": "ends", "gridlines": ["GL1", "GL6"]},
                                  {"id": "interior", "gridlines": ["GL2", "GL3", "GL4", "GL5"]}]
        split = self.service().run(v2_request(plain))
        # No crane, so the crane cases are not checked: 5 gravity + 32 side wind + 2 x 32 end wind.
        self.assertEqual(split["building"]["combinations"]["by_group"]["ends"]["total"], 5 + 32 + 2 * 32)
        self.assertLess(split["dossier"]["group_dossiers"]["ends"]["overall_governing"]["ratio"],
                        split["dossier"]["group_dossiers"]["interior"]["overall_governing"]["ratio"])
        sized = result["building"]["sized_building"]
        self.assertEqual([g["id"] for g in sized["sizing_groups"]], ["G1", "G2"])
        again = self.service().run(v2_request(sized))
        self.assertEqual(again["dossier"]["group_dossiers"]["G2"]["sections"], groups["G2"]["sections"])
        self.assertAlmostEqual(again["checks"]["overall_governing"]["ratio"],
                               result["checks"]["overall_governing"]["ratio"], places=9)

    def test_legacy_basis_never_checks_less_than_v1(self):
        v1 = self.service().run({"request_id": "v1", "mode": "checkSpecified", "inputs": DEFAULTS})
        legacy = self.service().run(v2_request(load_basis="legacy_2d"))
        self.assertEqual(legacy["building"]["distinct_load_sets"], [["GL1", "GL6"], ["GL2", "GL3", "GL4", "GL5"]])
        self.assertGreaterEqual(legacy["checks"]["overall_governing"]["ratio"],
                                v1["checks"]["overall_governing"]["ratio"] - 1e-9)


class ProppedMullionTests(_Desktop):
    """The load-bearing mullion check: frame axial force plus end-wall wind bending."""

    def test_mullion_check_matches_an_independent_calculation(self):
        from portalcalc.building.model import BuildingModel
        from portalcalc.end_wall import mullion_profile_design, roof_height_mm
        from portalcalc.desktop.main_window import MainWindow
        model = BuildingModel.from_dict(propped_end_building())
        window = MainWindow()
        window.confirm_on_close = False
        try:
            window.apply_preset_data(model.frame_preset("GL1"))
            window.building_load_mode = "building"
            window.building_model_override = model
            window.building_load_gridlines = ["GL1", "GL6"]
            window.generate_and_solve()
            self.assertIsNotNone(window.design_envelope_actions, window.results_box.toPlainText()[:400])
            envelope = window.design_envelope_actions["INTERNAL_COLUMN"]
            profile = window.selected_profile(window.internal_column_profile_combo)
            check = window.propped_mullion_check(profile, envelope)
            design = window.end_wall_mullion_design
            expected = []
            for x in (7000.0, 14000.0):
                height = roof_height_mm(x, 21000.0, 6000.0, 7.5, "Gable")
                self.assertAlmostEqual(height, 6000.0 + 7000.0 * math.tan(math.radians(7.5)), places=6)
                for axial in (-envelope["compression_kn"]["value"], envelope["tension_kn"]["value"]):
                    expected.append(mullion_profile_design(profile, height, 7000.0, design["strength_pressure_kpa"],
                                                           design["service_kpa"], axial_force_kn=axial)["governing_ratio"])
            self.assertAlmostEqual(check["ratio"], max(expected), places=12)
            self.assertEqual(check["tributary_width_mm"], 7000.0)
            ratio, label, _ = window.member_and_connection_ratio(profile, envelope, "INTERNAL_COLUMN")
            self.assertGreaterEqual(ratio, check["ratio"])
            # End-wall bracing restrains the eaves: k = E·A·cos²θ/L of the column-to-prop panel diagonal.
            info = window.eave_bracing_info
            area = window.selected_end_wall_brace_size()["area_mm2"]
            diagonal = math.hypot(7000.0, 6000.0)
            expected_k = 200000.0 * area * (7000.0 / diagonal) ** 2 / diagonal / 1000.0
            self.assertIn("left", info["springs"])
            self.assertAlmostEqual(info["springs"]["left"]["spring_kn_mm"], expected_k, places=9)
            left_eave = window.concept_main_top_nodes[0]
            self.assertAlmostEqual(window.structure.springs[left_eave.ux], expected_k * 1000.0, places=6)
            sides = {"Both sides": {"left", "right"}, "One side": {"left"}}[window.end_wall_braced_sides_combo.currentText()]
            self.assertEqual(set(info["springs"]), sides)
            # The interior group is an ordinary frame: no mullion check.
            window.building_load_gridlines = ["GL2", "GL3", "GL4", "GL5"]
            self.assertIsNone(window.propped_end_context())
        finally:
            window.close()


class ReviewTests(_Desktop):
    def test_v2_configurator_review_returns_a_v2_result(self):
        from portalcalc.service.configurator_review import REVIEW_STATUS, ReviewLaunchOptions, load_review_request
        from portalcalc.desktop.main_window import MainWindow
        building = factory_building()
        building["frame_types"][0]["geometry"].update(truss_depth_mm="900", max_truss_depth_mm="1800")
        request = v2_request(building)
        with tempfile.TemporaryDirectory(prefix="portalcalc-review-v2-") as temp_dir:
            request_path = Path(temp_dir) / "request.json"
            result_path = Path(temp_dir) / "result.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            loaded = load_review_request(request_path)
            self.assertIsNotNone(loaded.prepared.building)
            window = MainWindow()
            try:
                window.start_configurator_review(ReviewLaunchOptions(request_path, result_path))
                self.assertEqual(window.building_load_mode, "building")
                combo = window.left_column_profile_combo
                combo.setCurrentIndex(min(combo.currentIndex() + 1, combo.count() - 1))
                adjusted = combo.currentText()
                reviewed = window.return_configurator_review(show_message=False, close_after=False)
                self.assertIsNotNone(reviewed)
                self.assertEqual(reviewed["schema"], RESULT_SCHEMA_V2)
                self.assertEqual(reviewed["reviewStatus"], REVIEW_STATUS)
                self.assertEqual(reviewed["originalRequestDigest"], loaded.prepared.input_digest)
                self.assertEqual(reviewed["finalInputDigest"], reviewed["provenance"]["request_digest_sha256"])
                sized = reviewed["building"]["sized_building"]
                self.assertEqual(sized["frame_types"][0]["sections"]["left_column"], adjusted)
                self.assertEqual(sized["main"], building["main"])
                self.assertTrue(reviewed["building"]["combinations"]["full_set_checked"])
                self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), reviewed)
            finally:
                window.configurator_review_finalized = True
                window.confirm_on_close = False
                window.close()


@unittest.skipUnless(os.environ.get("PORTALCALC_SLOW_TESTS"), "set PORTALCALC_SLOW_TESTS=1 for v2 autoSize (~40 s)")
class AutoSizeTests(_Desktop):
    def test_auto_size_returns_a_verified_resubmittable_building(self):
        result = self.service().run(v2_request(mode="autoSize"))
        self.assertTrue(result["verified"])
        self.assertTrue(result["building"]["combinations"]["full_set_checked"])
        self.assertIn("FULL-SET VERIFICATION: OK", result["dossier"]["optimisation"]["report"])
        check = self.service().run(v2_request(result["building"]["sized_building"]))
        self.assertTrue(check["verified"])
        self.assertEqual(check["sizing"], result["sizing"])

    def test_propped_end_frames_size_light_and_verify(self):
        result = self.service().run(v2_request(propped_end_building(), mode="autoSize"))
        self.assertTrue(result["verified"])
        ends = result["dossier"]["group_dossiers"]["G1"]
        props = next(m for m in ends["member_checks"] if m["group"] == "INTERNAL_COLUMN")
        self.assertTrue(props["governing_check"].startswith("Propped mullion"))
        self.assertLessEqual(props["overall_ratio"], 1.0)
        interior = result["dossier"]["group_dossiers"]["G2"]["sections"]
        mass = lambda name: float(name.split("(")[-1].split(" kg/m")[0])
        self.assertLess(mass(ends["sections"]["left_column"]), mass(interior["left_column"]))
        check = self.service().run(v2_request(result["building"]["sized_building"]))
        self.assertTrue(check["verified"])

    def test_auto_size_sizes_each_group_and_resubmits(self):
        result = self.service().run(v2_request(part_length_lean_to_building(), mode="autoSize"))
        self.assertTrue(result["verified"])
        groups = result["dossier"]["group_dossiers"]
        self.assertTrue(all(g["verified"] for g in groups.values()))
        self.assertTrue(all("FULL-SET VERIFICATION: OK" in g["optimisation_report"] for g in groups.values()))
        check = self.service().run(v2_request(result["building"]["sized_building"]))
        self.assertTrue(check["verified"])
        self.assertEqual({gid: g["sections"] for gid, g in check["dossier"]["group_dossiers"].items()},
                         {gid: g["sections"] for gid, g in groups.items()})


if __name__ == "__main__":
    unittest.main()
