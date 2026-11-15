"""3D crane rail racking (bracing model) regression.

With a crane, the 3D bracing model loads the interior frame nearest the middle
of the building with the 2D frame's crane actions (crane_beam.apply_crane_actions):
SLS G + Crane and G + 0.7Q + Crane for crane rail racking (mean rail UX from the
G-only shape, against H/n) and ULS 1.2G + 1.5Q + Crane for brace strength.
Racking is a 3D acceptance check; in 2D it is report only.
"""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
import copy
import os
import tempfile
import unittest

import numpy as np

from bracing_analysis_regression import fixed_layout


class CraneRackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls.temp = tempfile.TemporaryDirectory(prefix="portalcalc_crane_3d_")
        os.environ["APPDATA"] = cls.temp.name
        from PySide6.QtWidgets import QApplication
        from portalcalc.desktop.main_window import MainWindow
        cls.app = QApplication.instance() or QApplication([])
        cls.window = w = MainWindow()
        w.confirm_on_close = False
        preset = copy.deepcopy(w.collect_preset_data())
        preset["loads"]["crane_rating_t"] = "10"
        preset.pop("crane_beam", None)                       # wheel loads from the rating
        preset["sections"].update(left_column="410 UB 53.7 (54.1 kg/m)", right_column="410 UB 53.7 (54.1 kg/m)")
        w.apply_preset_data(preset)
        w.bracing_location_layout = fixed_layout(w.current_bracing_layout_geometry(), end=True, both_ends=True).to_dict()
        w._last_crane_auto_select_signature = None
        w.generate_and_solve()
        from portalcalc.sizing import combined as combined_sizing
        cls.end_bay_layout = w.bracing_location_layout
        cls.result = combined_sizing.store_calculation(w)        # roof bracing in the end bays only
        full = fixed_layout(w.current_bracing_layout_geometry(), end=True, both_ends=True)
        for panel in full.panels.values():
            if panel.id.startswith("roof/") and panel.id not in full.pinned:
                full.toggle(panel.id, "pinned")
        w.bracing_location_layout = full.to_dict()
        cls.full_roof_result = combined_sizing.store_calculation(w)  # every roof bay braced
        w.bracing_location_layout = cls.end_bay_layout

    @classmethod
    def tearDownClass(cls):
        cls.window.serviceability_limit_overrides.pop("crane_rail_height", None)
        cls.window.close()
        cls.temp.cleanup()

    def test_crane_loads_only_the_middle_interior_frame(self):
        from portalcalc.bracing.analysis_desktop import prepare_context
        context = prepare_context(self.window)
        basis = context["crane_racking_basis"]
        stations = context["layout"].geometry.bay_stations_mm
        frame = basis["frame_index"]
        interior = range(1, len(stations) - 1)
        self.assertIn(frame, interior)
        self.assertEqual(frame, min(interior, key=lambda i: (abs(stations[i] - stations[-1] / 2), i)))
        crane = [c for c in context["cases"] if c.get("wind_family") == "crane"]
        self.assertEqual(sorted((c["level"], c["q_factor"]) for c in crane),
                         sorted([("SLS", 0.0)] * 4 + [("SLS", 0.7)] * 4 + [("ULS", 1.5)] * 4
                                + [("ULS", 0.0)] * 2))   # + buffer impact, front and back stops
        gravity = next(c for c in context["cases"] if c["name"] == "SLS / G")
        g_crane = next(c for c in crane if c["level"] == "SLS" and c["q_factor"] == 0.0)
        for i, (loaded, dead) in enumerate(zip(g_crane["portal_loads"], gravity["portal_loads"])):
            same = np.allclose(loaded["loads_n"], dead["loads_n"])
            self.assertEqual(same, i != frame, f"frame {i}")
        # The crane state carries the eccentric vertical reactions and the lateral force at the stub nodes.
        nodes = {n.id: n for n in context["portal"].structure.nodes}
        crane_part = np.asarray(g_crane["portal_loads"][frame]["loads_n"]) - np.asarray(gravity["portal_loads"][frame]["loads_n"])
        for side in ("left", "right"):
            node = nodes[basis["node_ids"][side]]
            self.assertLess(crane_part[node.uy], 0.0)
            self.assertNotEqual(crane_part[node.rz], 0.0)
        left, right = (nodes[basis["node_ids"][s]] for s in ("left", "right"))
        self.assertGreater(crane_part[left.ux] * crane_part[right.ux], 0.0)   # same direction: racking

    def test_racking_is_measured_from_the_dead_load_shape_with_3d_sharing(self):
        crane = self.result["design"]["crane_racking"]
        self.assertIsNotNone(crane)
        for row in crane["rows"]:
            self.assertAlmostEqual(row["racking_mm"], 0.5 * (row["left_mm"] + row["right_mm"]))
            self.assertAlmostEqual(row["ratio"], abs(row["racking_mm"]) / crane["limit_mm"])
        governing = crane["governing"]
        # Roof bracing in the end bays only: nothing reaches the middle frame, so it racks
        # as if alone (roof struts run along the building and carry no transverse shear).
        self.assertAlmostEqual(governing["racking_mm"], governing["frame_alone_mm"], delta=1e-6)
        # With every roof bay braced the neighbours share the crane load through the roof.
        shared = self.full_roof_result["design"]["crane_racking"]["governing"]
        self.assertLess(abs(shared["racking_mm"]), 0.5 * abs(shared["frame_alone_mm"]))
        self.assertAlmostEqual(crane["limit_mm"], crane["height_mm"] / 250.0)
        self.assertEqual(len(crane["rows"]), 8)
        # Crane ULS cases (4 crane positions + 2 buffer impacts) enter the bracing member envelopes.
        self.assertEqual(sum(1 for c in self.result["cases"]
                             if c["case"].get("wind_family") == "crane" and c["case"]["level"] == "ULS"), 6)
        from portalcalc.bracing.analysis_desktop import text_report
        self.assertIn("CRANE RAIL RACKING (3D)", text_report(self.result))
        # Not part of the longitudinal sway check.
        self.assertNotIn("Crane", self.result["design"]["global_sls"]["case"])

    def test_failing_racking_fails_the_design_and_changes_the_fingerprint(self):
        from portalcalc.sizing import combined as combined_sizing
        from portalcalc.bracing.analysis_desktop import prepare_context
        from portalcalc.bracing.calculation_cache import fingerprint
        from portalcalc.bracing.design import DesignCriteria
        w = self.window
        before = fingerprint(prepare_context(w), {}, {}, DesignCriteria())
        w.serviceability_limit_overrides["crane_rail_height"] = 1.0e6     # an impossible H/n
        try:
            after = fingerprint(prepare_context(w), {}, {}, DesignCriteria())
            self.assertNotEqual(before, after)
            strict = combined_sizing.store_calculation(w)
        finally:
            w.serviceability_limit_overrides.pop("crane_rail_height", None)
        crane = strict["design"]["crane_racking"]
        self.assertFalse(crane["passes"])
        self.assertFalse(strict["design"]["serviceability_passes"])
        self.assertFalse(strict["design"]["passes"])

    def test_crane_walls_are_two_tier_with_the_runway_beam_as_strut(self):
        from portalcalc.bracing.analysis_desktop import prepare_context
        context = prepare_context(self.window)
        layout = context["layout"]
        g = layout.geometry
        actions = self.window.crane_portal_actions
        self.assertAlmostEqual(g.wall_tier_z_mm, actions["runway_mid_height_mm"], delta=0.1)
        self.assertLess(g.wall_tier_z_mm, g.eave_mm)
        walls = [p for p in layout.panels.values() if p.face in ("left_wall", "right_wall")]
        self.assertTrue(all(p.tier == "crane" and p.tier_z_mm == g.wall_tier_z_mm for p in walls))
        self.assertTrue(all(len(p.brace_diagonals) == 4 for p in walls))
        members = self.result["cases"][0]["members"]
        runway = [m for m in members if m["kind"] == "runway_beam"]
        bays = len(g.bay_stations_mm) - 1
        self.assertEqual(len(runway), 2 * bays)                       # continuous along both walls
        self.assertEqual(self.result["sections"]["crane_runway"]["area_mm2"], actions["runway_area_mm2"])
        pinned_walls = [p for p in layout.pinned if layout.panels[p].face in ("left_wall", "right_wall")]
        self.assertEqual(sum(m["kind"] == "wall_brace" for m in members), 4 * len(pinned_walls))
        self.assertFalse(any(m["kind"] == "wall_tier_strut" for m in members))   # crane walls use the runway
        # Two X's per two-tier bay in the installation allowance.
        install = next(c for c in self.result["design"]["cost_items"] if c["group"] == "panel_installation")
        single = sum(1 for p in install["panels"] if not p.startswith(("left_wall", "right_wall")))
        self.assertEqual(install["panel_count"], single + 2 * len(pinned_walls))

    def test_buffer_impact_on_both_runways_at_the_end_stops(self):
        from portalcalc.bracing.analysis_desktop import prepare_context
        from portalcalc.crane_beam import BUFFER_ULS_FACTOR, buffer_force_rule_kn
        actions = self.window.crane_portal_actions
        self.assertEqual(actions["buffer_source"], "rule")
        self.assertAlmostEqual(actions["buffer_kn"], buffer_force_rule_kn(10.0, actions["crane_span_mm"]))
        self.assertAlmostEqual(buffer_force_rule_kn(10.0, 24000.0), 9.0 + 28.0 + 1.33 * 24.0)
        context = prepare_context(self.window)
        last = len(context["layout"].geometry.bay_stations_mm) - 1
        buffer = {c["crane_buffer"]: c for c in context["cases"] if c.get("crane_buffer")}
        self.assertEqual(set(buffer), {"front stops", "back stops"})
        force = BUFFER_ULS_FACTOR * actions["buffer_kn"] * 1000.0
        for label, frame, sign in (("front stops", 0, -1.0), ("back stops", last, 1.0)):
            case = buffer[label]
            self.assertEqual((case["level"], case["g_factor"], case["q_factor"]), ("ULS", 1.2, 0.0))
            self.assertEqual(sorted((i["face"], i["frame"]) for i in case["wall_tier_loads"]),
                             [("left_wall", frame), ("right_wall", frame)])
            self.assertTrue(all(abs(i["fy_n"] - sign * force) < 1e-6 for i in case["wall_tier_loads"]))
        # The solved case carries the full impact into the side-wall brace bases, and balances.
        solved = next(c for c in self.result["cases"] if c["case"].get("crane_buffer") == "front stops")
        bases = solved["load_path"]["member_base_reactions_n"]
        wall_y = sum(bases.get(face, [0, 0, 0])[1] for face in ("left_wall", "right_wall"))
        self.assertAlmostEqual(wall_y, 2.0 * force, delta=1e-3 * force)
        self.assertLess(max(map(abs, solved["balance_force_error_n"])), 1e-3 * force)
        self.assertGreater(max(m["axial_n"] for m in solved["members"] if m["kind"] == "wall_brace"), 0.0)

    def test_crane_buildings_use_rod_bracing_only(self):
        from portalcalc.bracing.analysis_desktop import prepare_context
        from portalcalc.bracing.policy import apply_group_rule, eligible, evidence
        context = prepare_context(self.window)
        wire = {"component_type": "wire_rope", "label": "6 mm wire rope"}
        rod = {"component_type": "scheduled_rod", "label": "M16 ROD"}
        for group in ("roof_brace", "wall_brace", "end_brace"):
            self.assertFalse(eligible(context, group, wire))
            self.assertTrue(eligible(context, group, rod))
        rows = apply_group_rule(context, "roof_brace", wire, [{"id": "x", "passes": True, "ratio": 0.2}])
        self.assertFalse(rows[0]["passes"])
        self.assertIn("crane building", rows[0]["policy_violation"])
        self.assertTrue(evidence(context, {"roof_brace": rod})["crane_building_rods_only"])

    def test_no_crane_no_crane_cases(self):
        from portalcalc.bracing.analysis_desktop import prepare_context
        w = self.window
        preset = copy.deepcopy(w.collect_preset_data())
        layout = w.bracing_location_layout
        preset["loads"]["crane_rating_t"] = "0"
        w.apply_preset_data(preset)
        w.bracing_location_layout = layout
        w.generate_and_solve()
        try:
            context = prepare_context(w)
            self.assertIsNone(context["crane_racking_basis"])
            self.assertFalse(any(c.get("wind_family") == "crane" for c in context["cases"]))
            self.assertIsNone(context["layout"].geometry.wall_tier_z_mm)
            self.assertIsNone(w.current_bracing_layout_geometry().wall_tier_z_mm)
        finally:
            preset["loads"]["crane_rating_t"] = "10"
            w.apply_preset_data(preset)
            w.bracing_location_layout = layout
            w.generate_and_solve()


if __name__ == "__main__":
    unittest.main()
