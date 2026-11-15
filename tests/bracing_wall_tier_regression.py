"""Two-tier side-wall bracing regression (geometry, network and statics).

Tall side-wall bays (height/bay > 1.73) are braced as two X's with a
mid-height strut; crane side walls are split at the runway level with the
runway beam as the continuous strut, which also carries the crane buffer
impact to the braced bays.
"""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
import unittest

from portalcalc.bracing.analysis import (AnalysisError, PortalReduction, WALL_TIER, build_braced_building,
                              building_sway_node, solve_building_case)
from bracing_analysis_regression import fixed_layout, frame, geometry
from portalcalc.bracing.layout import WALL_TIER_ASPECT, BracingLayout, LayoutGeometry


def walls(layout):
    return [p for p in layout.panels.values() if p.face in ("left_wall", "right_wall")]


def side_y(result):
    bases = result["load_path"]["member_base_reactions_n"]
    return sum(bases.get(face, [0., 0., 0.])[1] for face in ("left_wall", "right_wall"))


class GeometryTests(unittest.TestCase):
    def test_tall_bays_split_at_mid_height_only_past_the_aspect_limit(self):
        tall = geometry(eave_mm=8000, bay_mm=4000)          # 8 m / 4 m = 2.0
        for panel in walls(BracingLayout(tall)):
            height = tall.roof_z(panel.vertices[0][0])
            self.assertEqual(panel.tier, "tall")
            self.assertAlmostEqual(panel.tier_z_mm, height/2)
            self.assertEqual(len(panel.brace_diagonals), 4)
            self.assertEqual(len(panel.diagonals), 2)        # cladding diaphragms stay full height
            self.assertIn("mid-height strut", panel.label)
            low, high = panel.tier_points
            self.assertEqual((low[2], high[2]), (panel.tier_z_mm, panel.tier_z_mm))
        for panel in walls(BracingLayout(geometry())):        # 3 m / 8 m
            self.assertIsNone(panel.tier)
            self.assertEqual(panel.brace_diagonals, panel.diagonals)
        at_limit = geometry(eave_mm=WALL_TIER_ASPECT*4000, bay_mm=4000)
        self.assertTrue(all(p.tier is None for p in walls(BracingLayout(at_limit))))
        past = geometry(eave_mm=(WALL_TIER_ASPECT+.01)*4000, bay_mm=4000)
        self.assertTrue(all(p.tier == "tall" for p in walls(BracingLayout(past))))
        # Roof and end walls are never split.
        self.assertTrue(all(p.tier is None for p in BracingLayout(tall).panels.values()
                            if p.face not in ("left_wall", "right_wall")))

    def test_tall_end_wall_panels_split_at_half_their_mean_height(self):
        g = geometry(eave_mm=8000, end_wall_stations_mm=[0, 2000, 4000, 6000])   # 2 m panels
        ends = [p for p in BracingLayout(g).panels.values() if p.face in ("front_end", "back_end")]
        self.assertEqual(len(ends), 6)
        for panel in ends:
            x0, x1 = panel.vertices[0][0], panel.vertices[1][0]
            self.assertEqual(panel.tier, "tall")
            self.assertAlmostEqual(panel.tier_z_mm, (g.roof_z(x0)+g.roof_z(x1))/4)
            a, b, c, d = panel.brace_diagonals
            self.assertEqual((a[1][2], c[1][2]), (panel.tier_z_mm, g.roof_z(x1)))   # tops are the panel corners
            self.assertEqual(d[1], (x0, panel.vertices[0][1], g.roof_z(x0)))
        wide = geometry(eave_mm=8000)                                             # one 6 m panel
        self.assertTrue(all(p.tier is None for p in BracingLayout(wide).panels.values()
                            if p.face in ("front_end", "back_end")))

    def test_rule_uses_the_top_chord_node_and_the_analysis_copies_the_editor(self):
        # Pitching point 7.2 m / 4 m bays = 1.8 (tall); the node at 6.8 m gives 1.7 (one X).
        editor = geometry(eave_mm=7200, bay_mm=4000, tier_eave_mm=6800)
        self.assertTrue(all(p.tier is None for p in walls(BracingLayout(editor))))
        self.assertTrue(all(p.tier == "tall" for p in walls(BracingLayout(geometry(eave_mm=7200, bay_mm=4000)))))
        # The analysis geometry (actual node height, here past the limit) follows the editor's set.
        chosen = ["left_wall/bay/1", "front_end/panel/0"]
        actual = geometry(eave_mm=7400, bay_mm=4000, tiered_panels=chosen)
        tiered = sorted(p.id for p in BracingLayout(actual).panels.values() if p.tier == "tall")
        self.assertEqual(tiered, sorted(chosen))
        self.assertEqual(LayoutGeometry.from_dict(actual.to_dict()).tiered_panels, frozenset(chosen))
        self.assertEqual(LayoutGeometry.from_dict(editor.to_dict()).tier_eave_mm, 6800.)

    def test_crane_runway_level_splits_every_side_wall_bay(self):
        g = geometry(wall_tier_z_mm=2000)
        for panel in walls(BracingLayout(g)):
            self.assertEqual((panel.tier, panel.tier_z_mm), ("crane", 2000.))
            self.assertIn("crane runway", panel.label)
        again = LayoutGeometry.from_dict(g.to_dict())
        self.assertEqual(again.wall_tier_z_mm, 2000.)
        # The runway level is not part of geometry equality: saved layouts stay valid.
        self.assertEqual(g, geometry())
        self.assertNotIn("wall_tier_z_mm", geometry().to_dict())
        self.assertEqual(g.main_geometry().wall_tier_z_mm, 2000.)
        with self.assertRaises(ValueError):
            geometry(wall_tier_z_mm=3000)                    # at the eave
        with self.assertRaises(ValueError):
            geometry(wall_tier_z_mm=-1)


class NetworkTests(unittest.TestCase):
    def test_tall_bay_two_x_with_strut_carries_end_wind_to_the_wall_bases(self):
        g = geometry(eave_mm=8000, bay_mm=4000)
        layout = fixed_layout(g, end=True)
        macro = PortalReduction(frame(g), [(x, g.roof_z(x)) for x in g.roof_stations])
        model, _, dofs, lookup = build_braced_building(layout, macro, 1500, 300, 300, 300, 1500,
                                                       wall_tier_strut_area_mm2=400)
        pinned = [p for p in layout.pinned if layout.panels[p].face in ("left_wall", "right_wall")]
        struts = [m for m in model.members if m.kind == "wall_tier_strut"]
        self.assertEqual(sorted(m.panel_id for m in struts), sorted(pinned))
        self.assertTrue(all(m.area_mm2 == 400 for m in struts))
        self.assertEqual(sum(m.kind == "wall_brace" for m in model.members), 4*len(pinned))
        for strut in struts:
            for index in (strut.start, strut.end):
                self.assertEqual({3*index, 3*index+2} & model.fixed, {3*index, 3*index+2})
                self.assertNotIn(3*index+1, model.fixed)      # free along the building
                self.assertTrue(model.node_labels[index].endswith(WALL_TIER))
                self.assertFalse(building_sway_node(model.node_labels[index], model.nodes[index][2]))
        for sign in (1., -1.):
            result = solve_building_case(model, layout, macro, dofs, lookup, {"front_kpa": sign, "back_kpa": sign*.3})
            path = result["load_path"]
            self.assertAlmostEqual(side_y(result), -path["applied_roof_y_n"], places=5)
            self.assertLess(abs(path["roof_to_member_base_y_residual_n"]), 1e-5)
            forces = {m["id"]: m["axial_n"] for m in result["members"]}
            self.assertTrue(any(abs(forces[f"wall_strut/{p}"]) > 1. for p in pinned))
            # Both tiers of the braced bays work.
            for p in pinned:
                lower = max(forces[f"brace/{p}/{j}"] for j in (0, 1))
                upper = max(forces[f"brace/{p}/{j}"] for j in (2, 3))
                self.assertGreater(lower, 1.)
                self.assertGreater(upper, 1.)

    def test_tall_end_wall_panels_load_the_post_below_the_tier(self):
        g = geometry(eave_mm=8000, end_wall_stations_mm=[0, 2000, 4000, 6000])
        layout = BracingLayout(g, pinned=["left_wall/bay/0", "right_wall/bay/0", "roof/bay/0/strip/0",
                                          "roof/bay/0/strip/1", "roof/bay/0/strip/2", "roof/bay/0/strip/3",
                                          "front_end/panel/0", "front_end/panel/1", "front_end/panel/2"])
        macro = PortalReduction(frame(g), [(x, g.roof_z(x)) for x in g.roof_stations])
        model, _, dofs, lookup = build_braced_building(layout, macro, 1500, 300, 300, 300, 1500,
                                                       wall_tier_strut_area_mm2=400)
        self.assertEqual(sum(m.kind == "wall_tier_strut" for m in model.members), 3)
        self.assertEqual(sorted(model.post_tiers), ["post/front_end/1", "post/front_end/2"])
        for nodes in model.post_tiers.values():
            for index in nodes:
                self.assertEqual({3*index+1, 3*index+2} & model.fixed, {3*index+1, 3*index+2})   # out of plane, vertical
                self.assertNotIn(3*index, model.fixed)                                          # free across the wall
        # Transverse wind loads the end-wall bracing in its plane.
        case = {"name": "across", "level": "ULS", "front_kpa": 0., "back_kpa": 0.}
        loads_case = dict(case)
        result = solve_building_case(model, layout, macro, dofs, lookup, {**loads_case, "front_kpa": .8, "back_kpa": .8})
        posts = [m for m in result["members"] if m["kind"] == "end_post" and m["id"] in model.post_tiers]
        self.assertTrue(posts)
        vertical = {r["node"]: r["force_n"][2] for r in result["support_reactions_n"]}
        for post in posts:
            above, below = post["axial_n_above_tier"], post["axial_n_below_tier"]
            self.assertAlmostEqual(below, above - sum(vertical[n] for n in model.post_tiers[post["id"]]))
            self.assertEqual(post["axial_n"], min(above, below) if min(above, below) < 0 else max(above, below))

    def test_a_corner_node_in_two_walls_is_held_only_vertically(self):
        g = geometry(eave_mm=8000, pitch_deg=0, roof_shape="Skillion", bay_mm=4000,
                     end_wall_stations_mm=[0, 2000, 4000, 6000])
        layout = BracingLayout(g, pinned=["left_wall/bay/0", "front_end/panel/0"])
        macro = PortalReduction(frame(g), [(x, g.roof_z(x)) for x in g.roof_stations])
        model, *_ = build_braced_building(layout, macro, 1500, 300, 300, 300, 1500, wall_tier_strut_area_mm2=400)
        corner = next(i for i, p in enumerate(model.nodes) if p == (0., 0., 4000.))
        self.assertEqual({3*corner, 3*corner+1, 3*corner+2} & model.fixed, {3*corner+2})

    def test_crane_runway_is_a_continuous_strut_and_takes_the_buffer_impact(self):
        g = geometry(wall_tier_z_mm=2000)
        layout = fixed_layout(g, end=True, both_ends=True)
        macro = PortalReduction(frame(g), [(x, g.roof_z(x)) for x in g.roof_stations])
        with self.assertRaisesRegex(AnalysisError, "runway beam area"):
            build_braced_building(layout, macro, 1500, 300, 300, 300, 1500)
        one_wall = BracingLayout(g, pinned=[p for p in layout.pinned if not p.startswith("right_wall/")])
        with self.assertRaisesRegex(AnalysisError, "right wall"):
            build_braced_building(one_wall, macro, 1500, 300, 300, 300, 1500, runway_area_mm2=5000)
        model, _, dofs, lookup = build_braced_building(layout, macro, 1500, 300, 300, 300, 1500,
                                                       runway_area_mm2=5000)
        bays = len(g.bay_stations_mm)-1
        runway = [m for m in model.members if m.kind == "runway_beam"]
        self.assertEqual(len(runway), 2*bays)
        self.assertEqual(len(model.wall_tier_nodes), 2*(bays+1))
        self.assertFalse(any(m.kind == "wall_tier_strut" for m in model.members))
        force = 40000.
        for frame_index, sign in ((0, -1.), (bays, 1.)):
            case = {"name": "buffer", "level": "ULS", "front_kpa": 0., "back_kpa": 0.,
                    "wall_tier_loads": [{"face": face, "frame": frame_index, "fy_n": sign*force}
                                        for face in ("left_wall", "right_wall")]}
            result = solve_building_case(model, layout, macro, dofs, lookup, case)
            self.assertAlmostEqual(side_y(result), -2*sign*force, delta=1e-3)
            self.assertLess(max(map(abs, result["balance_force_error_n"])), 1e-3)
            self.assertLess(abs(result["load_path"]["roof_to_member_base_y_residual_n"]), 1e-3)
            work = next(w for w in result["load_workings"] if w["face"] == "crane_buffer")
            self.assertAlmostEqual(work["roof_resultant_n"], 2*sign*force)
            members = {m["id"]: m for m in result["members"]}
            # The impact runs along the runway to the braced bays at both ends of the wall.
            self.assertGreater(abs(members[f"runway/left_wall/bay/{bays//2}"]["axial_n"]), 1.)
            lower = [m["axial_n"] for m in result["members"] if m["kind"] == "wall_brace"
                     and int(m["id"].rsplit("/", 1)[1]) < 2]
            self.assertGreater(max(lower), 1.)
        with self.assertRaisesRegex(AnalysisError, "runway level"):
            plain = geometry()
            plain_layout = fixed_layout(plain, end=True, both_ends=True)
            plain_macro = PortalReduction(frame(plain), [(x, plain.roof_z(x)) for x in plain.roof_stations])
            m, _, d, l = build_braced_building(plain_layout, plain_macro, 1500, 300, 300, 300, 1500)
            solve_building_case(m, plain_layout, plain_macro, d, l,
                                {"front_kpa": 0., "back_kpa": 0.,
                                 "wall_tier_loads": [{"face": "left_wall", "frame": 0, "fy_n": 1.}]})


class StressedSkinDefaultTests(unittest.TestCase):
    def test_il1_on_il2_and_above_off(self):
        from portalcalc.bracing.diaphragm import DiaphragmSettings, default_settings
        on = DiaphragmSettings.from_dict(default_settings("1"))
        self.assertTrue(on.enabled and on.side_walls_enabled and on.end_walls_enabled)
        self.assertEqual(on.calibration_mode, "rev_b_geometry")
        self.assertEqual((on.flexibility_mm_per_kn, on.side_wall_flexibility_mm_per_kn), (3., 6.))
        for level in ("2", 3, "4", None, "x"):
            self.assertFalse(DiaphragmSettings.from_dict(default_settings(level)).active)


if __name__ == "__main__":
    unittest.main()
