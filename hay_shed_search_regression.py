"""Fast checks for cost-comparison policy, compass mapping and search restoration."""
import argparse
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hay_shed_search import (DIRECTIONS, ROOT, geometry_grid, hay_inputs,
                             make_candidates, north_selector, write_summary, secondary_verified)
from portalcalc_service import InvalidCalculationRequest, prepare_request


class SearchPolicyTests(unittest.TestCase):
    def setUp(self):
        self.base = json.loads((ROOT / "presets" / "factory_defaults.json").read_text(encoding="utf-8"))

    def test_equal_bays_preserve_area_and_portal_count(self):
        geometries = list(geometry_grid(840, range(15, 31), 5, 10))
        self.assertEqual(len(geometries), 65)
        for geometry in geometries:
            self.assertLess(abs(geometry["area_m2"] - 840), 0.1)
            self.assertTrue(5 <= geometry["bay_m"] <= 10)
            self.assertEqual(math.ceil(geometry["length_m"] / geometry["bay_m"]), geometry["bay_count"])
        original = next(g for g in geometries if g["width_m"] == 21 and g["bay_count"] == 5)
        self.assertAlmostEqual(original["length_m"], 40)
        self.assertEqual(original["bay_m"], 8)

    def test_compass_maps_open_right_wall(self):
        for direction in DIRECTIONS:
            north = north_selector(direction)
            source_direction = DIRECTIONS[(2 - DIRECTIONS.index(north)) % 8]
            self.assertEqual(source_direction, direction)

    def test_direction_grid_retains_millimetre_rounded_bay_layout(self):
        for original in geometry_grid(840, [15, 19, 20], 5, 10):
            directional = list(geometry_grid(840, [original["width_m"]], original["bay_m"] - 0.0005, original["bay_m"] + 0.0005))
            self.assertEqual(len(directional), 1)
            self.assertEqual(directional[0], original)

    def test_secondary_failure_cannot_be_ranked_as_a_passing_design(self):
        self.assertFalse(secondary_verified({}))
        self.assertFalse(secondary_verified({"secondary_steel_checks": {"roof": {"status": "OK"}, "wall": {"status": "NOT OK"}}}))
        self.assertTrue(secondary_verified({"secondary_steel_checks": {"roof": {"status": "OK"}, "wall": {"status": "OK"}}}))

    def test_main_truss_cap_is_span_over_twenty(self):
        for geometry in geometry_grid(840, [15, 21, 30], 5, 10):
            inputs = hay_inputs(self.base, geometry, "A5", "E", 1, 2, 7.5, future_canopy_m=6)
            self.assertEqual(inputs["geometry"]["max_truss_depth_mm"], geometry["width_m"] * 50)
            self.assertLessEqual(inputs["geometry"]["truss_depth_mm"], inputs["geometry"]["max_truss_depth_mm"])
            self.assertEqual(inputs["hay_shed_search"]["optimiser"], "physics")

    def test_search_backend_calls_physics_flow(self):
        from unittest.mock import Mock
        from hay_shed_backend import PhysicsSearchBackend
        window = Mock()
        window.left_canopy_type_combo.currentText.return_value = "None"
        window.right_canopy_type_combo.currentText.return_value = "None"
        window.run_physics_structure_optimisation.return_value = {"verified": True}
        result = PhysicsSearchBackend.optimise_window(window)
        window.run_physics_structure_optimisation.assert_called_once_with()
        window.auto_size_current_preset.assert_not_called()
        self.assertEqual(result["algorithm"], "physics")

    def test_search_sets_actual_open_side_and_isolates_base(self):
        frozen = json.dumps(self.base, sort_keys=True)
        geometry = next(geometry_grid(840, [21], 5, 10))
        inputs = hay_inputs(self.base, geometry, "A2", "SW", 2, 2, 7.5)
        self.assertEqual(inputs["wind"]["wall_enclosure"],
                         {"left": True, "right": False, "front": True, "back": True})
        self.assertEqual(inputs["loads"]["bay_size_mm"], inputs["loads"]["bay_size_m"] * 1000)
        self.assertEqual(inputs["loads"]["solar_kpa"], 0)
        self.assertEqual(inputs["sections"]["purlin_span_type"], "Continuous lapped")
        self.assertEqual(inputs["sections"]["wall_girt_span_type"], "Single span")
        self.assertEqual(inputs["wind"]["importance_level"], 2)
        self.assertEqual(json.dumps(self.base, sort_keys=True), frozen)

    def test_grid_identity_includes_region_and_direction(self):
        args = argparse.Namespace(widths="21", area=840, min_bay=5, max_bay=10,
                                  regions="A0,A5", facings="N,E", importance=1, terrain=2, pitch=7.5)
        candidates = make_candidates(args, self.base)
        self.assertEqual(len(candidates), 20)
        self.assertEqual(len({c["id"] for c in candidates}), 20)
        self.assertEqual(candidates[0]["bay_m"], 8)

    def test_service_validates_frozen_panel_topology(self):
        self.base["calculation_service"] = {"main_truss_panel_count": 28}
        prepared = prepare_request({"request_id": "topology", "mode": "checkSpecified", "inputs": self.base})
        self.assertEqual(prepared.inputs["calculation_service"]["main_truss_panel_count"], 28)
        for bad in (0, 29, 28.5, float("inf")):
            self.base["calculation_service"]["main_truss_panel_count"] = bad
            with self.assertRaises(InvalidCalculationRequest):
                prepare_request({"request_id": "bad-topology", "mode": "checkSpecified", "inputs": self.base})

    def test_solar_is_separate_dead_load_and_has_distinct_candidate_identity(self):
        args = argparse.Namespace(widths="21", area=840, min_bay=5, max_bay=10,
                                  regions="A5", facings="E", importance=1, terrain=2, pitch=7.5,
                                  solar_kpa=0, solar_side="Both")
        bare = make_candidates(args, self.base)[0]
        args.solar_kpa = 0.15
        solar = make_candidates(args, self.base)[0]
        self.assertNotEqual(bare["id"], solar["id"])
        self.assertEqual(solar["inputs"]["loads"]["solar_kpa"], 0.15)
        self.assertEqual(bare["inputs"]["loads"]["g_kpa"], solar["inputs"]["loads"]["g_kpa"])
        self.assertEqual(solar["inputs"]["loads"]["roof_load_placements"]["solar"],
                         {"side": "Both", "from_percent": "0", "to_percent": "100"})
        self.base["calculation_service"] = {"main_truss_panel_count": 28}
        self.assertNotIn("calculation_service", make_candidates(args, self.base)[0]["inputs"])

    def test_future_canopy_has_no_solar_and_does_not_reduce_main_area(self):
        geometry = next(geometry_grid(840, [21], 5, 10))
        inputs = hay_inputs(self.base, geometry, "A5", "E", 1, 2, 7.5, 0.15, "Both", 6)
        self.assertEqual(inputs["canopies"]["right_length_mm"], 6000)
        self.assertEqual(inputs["canopies"]["right_type"], "Canopy")
        self.assertEqual(inputs["canopies"]["right_solar_kpa"], 0)
        self.assertEqual(inputs["loads"]["solar_kpa"], 0.15)
        self.assertEqual(inputs["geometry"]["span_mm"], 21000)
        self.assertEqual(inputs["hay_shed_search"]["cost_scope"], "main_now")

    def test_main_now_cost_excludes_future_frame_purchase_only(self):
        from hay_shed_backend import main_now_cost_summary
        profile = SimpleNamespace(A=1000)
        canopy = SimpleNamespace(group="RIGHT_CANOPY_TOP", profile=profile, length=lambda: 6000)
        main = SimpleNamespace(group="TOP", profile=profile, length=lambda: 2000)
        window = SimpleNamespace(apply_auto_web_assignments_to_structure=lambda s: None,
                                 section_cost_bucket=lambda p: "shs", design_envelope_actions={},
                                 left_canopy_top_profile_combo=None, left_canopy_bottom_profile_combo=None,
                                 right_canopy_top_profile_combo=None, right_canopy_bottom_profile_combo=None)
        kg = 0.001 * 7850 * 8 * 1.07 * 2
        summary = {"portal_count": 2, "rates": {"shs": 5, "ub": 3, "wb": 4, "doubled_chord": 6},
                   "costs": {"shs": kg * 5, "ub": 0, "wb": 0, "doubled_chord": 0, "purlin": 100},
                   "total_cost": kg * 5 + 100}
        result = main_now_cost_summary(window, SimpleNamespace(elements=[main, canopy]), summary)
        self.assertAlmostEqual(result["future_canopy_frame_cost"], 0.001 * 7850 * 6 * 1.07 * 2 * 5)
        self.assertAlmostEqual(result["total_cost"], 0.001 * 7850 * 2 * 1.07 * 2 * 5 + 100)
        self.assertEqual(result["costs"]["purlin"], 100)
        self.assertAlmostEqual(result["total_cost"] + result["future_canopy_frame_cost"], summary["total_cost"])
        self.assertEqual(summary["total_cost"], result["full_modelled_steel_cost"])

    def test_failed_cheap_design_never_wins_and_regions_stay_separate(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            output = Path(temporary)
            (output / "calculations").mkdir()
            rows = [dict(id="fail", status="completed", verified=False, cost=1, cost_per_m2=0.01, region="A0"),
                    dict(id="a", status="completed", verified=True, cost=100, cost_per_m2=1, region="A0"),
                    dict(id="b", status="completed", verified=True, cost=50, cost_per_m2=0.5, region="A5")]
            for row in rows[1:]:
                (output / "calculations" / f"{row['id']}.json").write_text(
                    json.dumps({"dossier": {"inputs": {"wind": {"region": row["region"]}}}}), encoding="utf-8")
            manifest = {"regions": ["A0", "A5", "A3"], "candidate_count": 4, "scope": {}}
            summary = write_summary(output, manifest, rows)
            self.assertEqual(summary["passing"], 2)
            self.assertEqual(summary["best_by_region"]["A0"]["id"], "a")
            self.assertEqual(summary["best_by_region"]["A5"]["id"], "b")
            self.assertIsNone(summary["best_by_region"]["A3"])
            self.assertEqual(summary["top_20"][0]["id"], "b")
            self.assertTrue((output / "best_A0_preset.json").exists())


if __name__ == "__main__":
    unittest.main()
