"""Independent mechanics, condensation/recovery and offscreen desktop checks."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

from bracing_analysis import (AnalysisError, AxialMember, AxialModel3D, PortalReduction,
                              build_braced_building, end_wall_load_vector, solve_building_case)
from bracing_layout import BracingLayout, LayoutGeometry


def geometry(**kwargs):
    return LayoutGeometry.create(**{"span_mm": 6000, "length_mm": 16000, "eave_mm": 3000,
                                    "pitch_deg": 10, "roof_shape": "Gable", **kwargs})


def frame(g, fixed=False):
    from main import Node, FrameElement2D, Structure2D
    s = Structure2D()
    profile = SimpleNamespace(A=1500., I_analysis=12e6, name="Benchmark section")
    points = [(0., 0.)] + [(x, g.roof_z(x)) for x in g.roof_stations] + [(g.span_mm, 0.)]
    for i, (x, z) in enumerate(points):
        s.add_node(Node(i+1, x, z))
    for i, (a, b) in enumerate(zip(s.nodes, s.nodes[1:])):
        s.add_element(FrameElement2D(i+1, a, b, 200000., profile, group="TOP" if 0 < i < len(points)-2 else "COLUMN"))
    for n in (s.nodes[0], s.nodes[-1]):
        s.add_support(n.ux)
        s.add_support(n.uy)
        if fixed:
            s.add_support(n.rz)
    return s


def fixed_layout(g, end=False):
    layout = BracingLayout(g)
    for p in layout.panels.values():
        if p.id.startswith(("left_wall/bay/0", "right_wall/bay/0", "roof/bay/0/")) or (end and p.face == "front_end"):
            layout.toggle(p.id, "pinned")
    return layout


class MechanicsTests(unittest.TestCase):
    def x_model(self, area=200, rods=True):
        model = AxialModel3D()
        left = model.add_node((0, 0, 0), fixed=True)
        right = model.add_node((8000, 0, 0), fixed=True)
        top = model.add_node((4000, 0, 3000))
        model.fixed.update((3*top+1, 3*top+2))
        for i, base in enumerate((left, right)):
            model.add_member(AxialMember(str(i), base, top, area, tension_only=rods))
        return model, top

    def test_cross_rods_match_closed_form_and_reverse_without_compression(self):
        model, node = self.x_model()
        for load in (10000., -10000.):
            f = np.zeros(len(model.nodes)*3)
            f[node*3] = load
            result = model.solve(f)
            # L=5000, cos(theta)=4/5; only one rod resists.
            expected = load*5000/(200000*200*(4/5)**2)
            self.assertAlmostEqual(result["displacements_mm"][node][0], expected, places=9)
            self.assertEqual(len([m for m in result["members"] if m["state"] == "slack"]), 1)
            self.assertAlmostEqual(max(m["axial_n"] for m in result["members"]), abs(load)/(4/5), places=7)
            self.assertTrue(all(m["axial_n"] >= 0 for m in result["members"]))

    def test_rod_area_scaling_and_bilateral_compression(self):
        a, node = self.x_model()
        b, _ = self.x_model(area=400)
        f = np.zeros(9)
        f[6] = 10000
        ra, rb = a.solve(f), b.solve(f)
        self.assertAlmostEqual(ra["displacements_mm"][node][0], 2*rb["displacements_mm"][node][0])
        c, _ = self.x_model(rods=False)
        rc = c.solve(f)
        self.assertTrue(any(m["axial_n"] < 0 for m in rc["members"]))
        self.assertAlmostEqual(rc["displacements_mm"][node][0], rb["displacements_mm"][node][0])

    def test_true_spatial_bar_network_matches_vector_solution(self):
        # Three perpendicular bars, rotated about two axes; no planar shortcut.
        angle = .61
        rx = np.array([[1, 0, 0], [0, math.cos(angle), -math.sin(angle)], [0, math.sin(angle), math.cos(angle)]])
        rz = np.array([[math.cos(angle), -math.sin(angle), 0], [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])
        axes = rx @ rz
        model = AxialModel3D()
        bases = [model.add_node(-3000*axes[:, i], fixed=True) for i in range(3)]
        top = model.add_node((0, 0, 0))
        for i, base in enumerate(bases):
            model.add_member(AxialMember(str(i), base, top, 300, tension_only=True))
        f = np.zeros(12)
        f[-3:] = axes @ np.array([2000, 3000, 4000])
        result = model.solve(f)
        np.testing.assert_allclose(result["displacements_mm"][top], f[-3:]*3000/(200000*300), atol=1e-10)
        np.testing.assert_allclose([m["axial_n"] for m in result["members"]], [2000, 3000, 4000], atol=1e-6)

    def test_slack_mechanism_and_missing_load_path_fail(self):
        model = AxialModel3D()
        a = model.add_node((0, 0, 0), fixed=True)
        b = model.add_node((4000, 0, 0))
        model.fixed.update((4, 5))
        model.add_member(AxialMember("rod", a, b, 200, tension_only=True))
        f = np.array([0, 0, 0, -1000, 0, 0.])
        unloaded = model.solve(np.zeros(6))
        self.assertEqual(unloaded["tangent_status"], "neutral_rods_required_stability_unverified")
        with self.assertRaises(AnalysisError):
            model.solve(f)
        model.fixed.remove(4)
        with self.assertRaisesRegex(AnalysisError, "Unstable"):
            model.solve(-f)

    def test_portal_attachment_reduction_matches_direct_loaded_solution(self):
        g = geometry()
        # Non-nodal attachments exercise the rotation/Hermite terms.
        points = [(g.span_mm*f, g.roof_z(g.span_mm*f)) for f in (0., .2, .5, .8, 1.)]
        # Refine the FE space first; attachment coordinates need not coincide.
        g2 = geometry(end_wall_stations_mm=[0, 1000, 2000, 3000, 4000, 5000, 6000])
        structure = frame(g2, fixed=True)
        loads = np.zeros(3*len(structure.nodes))
        loads[structure.nodes[2].uy] = -12000
        macro = PortalReduction(structure, points, loads)
        applied = np.array([5000., -3000., 1000., -2000., 0., -1000., -1200., -800., -3000., -500.])
        direct = structure.solve_with_stiffness(macro.k, loads + macro.h.T @ applied)
        movement = np.linalg.solve(macro.stiffness, macro.equivalent_load+applied)
        np.testing.assert_allclose(movement, macro.h @ direct, atol=1e-8)
        recovered = macro.recover(movement)
        np.testing.assert_allclose(recovered["interface_forces_n"], applied.reshape(-1, 2), atol=1e-6)
        for n, item in zip(structure.nodes, recovered["node_displacements"]):
            self.assertAlmostEqual(item["ux_mm"], direct[n.ux], places=7)
            self.assertAlmostEqual(item["uz_mm"], direct[n.uy], places=7)

    def test_end_wall_load_integration_preserves_resultant_and_overturning(self):
        for shape in ("Gable", "Skillion"):
            g = geometry(roof_shape=shape, end_wall_stations_mm=[0, 2000, 4000, 6000])
            layout = fixed_layout(g)
            macro = PortalReduction(frame(g), [(x, g.roof_z(x)) for x in g.roof_stations])
            model, roof, dofs, lookup = build_braced_building(layout, macro, 1500, 200, 200, 200, 1500)
            f, work = end_wall_load_vector(model, layout, lookup, 1.2, -.4)
            area = sum((b-a)*(g.roof_z(a)+g.roof_z(b))/2 for a, b in zip(g.roof_stations, g.roof_stations[1:]))
            self.assertAlmostEqual(sum(f[1::3]), (1.2-.4)*area/1000, places=7)
            for item in work:
                self.assertAlmostEqual(item["roof_resultant_n"], item["direct_ground_resultant_n"])
            # Integral of p*H^2/2 gives base overturning from vertical strips.
            integral_h2 = sum((b-a)*(g.roof_z(a)**2+g.roof_z(a)*g.roof_z(b)+g.roof_z(b)**2)/3
                              for a, b in zip(g.roof_stations, g.roof_stations[1:]))
            moment = np.sum(np.cross(np.asarray(model.nodes), f.reshape(-1, 3)), axis=0)
            self.assertAlmostEqual(moment[0], -(1.2-.4)/2000*integral_h2, places=5)

    def test_unbraced_portal_sway_mechanism_can_be_stabilised_by_real_rod(self):
        from main import Node, FrameElement2D, Structure2D
        structure = Structure2D()
        bottom, top = Node(1, 3000, 0), Node(2, 3000, 4000)
        for n in (bottom, top):
            structure.add_node(n)
        structure.add_support(bottom.ux)
        structure.add_support(bottom.uy)
        profile = SimpleNamespace(A=1500., I_analysis=12e6, name="Benchmark column")
        structure.add_element(FrameElement2D(1, bottom, top, 200000, profile, group="TOP"))
        macro = PortalReduction(structure, [(3000, 4000)])
        # The pin-based column has zero horizontal stiffness before bracing.
        self.assertLess(abs(macro.stiffness[0, 0]), 1e-6)
        model = AxialModel3D()
        base = model.add_node((0, 0, 0), fixed=True)
        roof = model.add_node((3000, 0, 4000))
        model.fixed.add(3*roof+1)
        model.add_block([3*roof, 3*roof+2], macro.stiffness)
        model.add_member(AxialMember("real rod", base, roof, 200, tension_only=True))
        force = np.array([0, 0, 0, 10000, 0, 0.])
        result = model.solve(force)
        expected_z = -10000*(4/3)/(200000*1500/4000)
        expected_x = ((10000/(3/5))*5000/(200000*200)-(4/5)*expected_z)/(3/5)
        self.assertAlmostEqual(result["displacements_mm"][roof][2], expected_z, places=8)
        self.assertAlmostEqual(result["displacements_mm"][roof][0], expected_x, places=8)

    def test_interior_attachment_member_end_forces_include_physical_point_load(self):
        from main import Node, FrameElement2D, Structure2D
        structure = Structure2D()
        a, b = Node(1, 0, 3000), Node(2, 6000, 3000)
        for n in (a, b):
            structure.add_node(n)
        for dof in (a.ux, a.uy, a.rz):
            structure.add_support(dof)
        profile = SimpleNamespace(A=1500., I_analysis=12e6, name="Benchmark cantilever")
        structure.add_element(FrameElement2D(1, a, b, 200000, profile, group="TOP"))
        macro = PortalReduction(structure, [(3000, 3000)])
        applied = np.array([0., -10000.])
        movement = np.linalg.solve(macro.stiffness, applied)
        recovered = macro.recover(movement)
        ends = recovered["member_end_forces"][0]["local_n_n_nmm"]
        # Independent statics: a midpoint downward force gives V=P, M=P*a
        # at the root and zero actions at the unloaded free end.
        np.testing.assert_allclose(ends, [0, 10000, 30e6, 0, 0, 0], atol=1e-6)
        self.assertEqual(len(recovered["interior_attachment_point_loads"]), 1)

    def test_reduced_building_matches_unreduced_portals_and_recovers_reactions(self):
        g = geometry(end_wall_stations_mm=[0, 2000, 4000, 6000])
        layout = fixed_layout(g, end=True)
        structure = frame(g)
        for node in (structure.nodes[0], structure.nodes[-1]):
            structure.add_spring(node.rz, 5e8)
        macro = PortalReduction(structure, [(x, g.roof_z(x)) for x in g.roof_stations])
        model, roof, dofs, lookup = build_braced_building(layout, macro, 1500, 200, 200, 200, 1500)
        case = {"name": "Independent comparison", "front_kpa": 1.1, "back_kpa": .3}
        reduced = solve_building_case(model, layout, macro, dofs, lookup, case)
        loads, _ = end_wall_load_vector(model, layout, lookup, 1.1, .3)
        # Assemble original portal free DOFs plus longitudinal roof translations.
        nf = len(macro.free)
        portals = len(roof)
        ny = sum(len(row) for row in roof)
        transform = np.zeros((3*len(model.nodes), portals*nf+ny))
        full = np.zeros((portals*nf+ny, portals*nf+ny))
        yindex = portals*nf
        for i, nodes in enumerate(roof):
            full[i*nf:(i+1)*nf, i*nf:(i+1)*nf] = macro.k[np.ix_(macro.free, macro.free)]
            for j, node in enumerate(nodes):
                transform[3*node, i*nf:(i+1)*nf] = macro.h[2*j, macro.free]
                transform[3*node+2, i*nf:(i+1)*nf] = macro.h[2*j+1, macro.free]
                transform[3*node+1, yindex] = 1
                yindex += 1
        for member, response in zip(model.members, reduced["members"]):
            if member.tension_only and response["state"] == "slack":
                continue
            delta = np.subtract(model.nodes[member.end], model.nodes[member.start])
            length = np.linalg.norm(delta)
            row = delta/length @ (transform[3*member.end:3*member.end+3]-transform[3*member.start:3*member.start+3])
            full += member.modulus_mpa*member.area_mm2/length*np.outer(row, row)
        original = np.linalg.solve(full, transform.T @ loads)
        np.testing.assert_allclose(transform @ original, np.asarray(reduced["displacements_mm"]).reshape(-1), atol=1e-7)
        for i, recovered in enumerate(reduced["portals"]):
            for n, output in zip(structure.nodes, recovered["node_displacements"]):
                if n.ux in macro.free:
                    self.assertAlmostEqual(output["ux_mm"], original[i*nf+list(macro.free).index(n.ux)], places=7)
        self.assertLess(max(abs(v) for v in reduced["balance_force_error_n"]), 1e-5)
        self.assertLess(max(abs(v) for v in reduced["balance_moment_error_nmm"]), .01)

    def test_unbraced_and_disconnected_buildings_do_not_get_artificial_restraints(self):
        g = geometry()
        macro = PortalReduction(frame(g), [(x, g.roof_z(x)) for x in g.roof_stations])
        with self.assertRaisesRegex(AnalysisError, "Pin"):
            build_braced_building(BracingLayout(g), macro, 1500, 200, 200, 200, 1500)
        layout = BracingLayout(g, pinned=["left_wall/bay/0"])
        model, _, dofs, lookup = build_braced_building(layout, macro, 1500, 200, 200, 200, 1500)
        with self.assertRaises(AnalysisError):
            solve_building_case(model, layout, macro, dofs, lookup, {"front_kpa": 1., "back_kpa": .3})


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls.temp = tempfile.TemporaryDirectory(prefix="portalcalc_3d_analysis_")
        os.environ["APPDATA"] = cls.temp.name
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False
        cls.defaults = copy.deepcopy(cls.window.collect_preset_data())

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.temp.cleanup()

    def setUp(self):
        self.window.apply_preset_data(copy.deepcopy(self.defaults))
        self.window.bracing_location_layout = fixed_layout(self.window.current_bracing_layout_geometry()).to_dict()
        self.window.generate_and_solve()

    def test_directional_pressures_reverse_physical_ends_and_respect_openings(self):
        from bracing_analysis_desktop import end_wind_cases
        from wind import WindInputs
        cases = end_wind_cases(WindInputs(orientation="Any", front_wall_clad=True, back_wall_clad=True))
        for i in range(4):
            self.assertAlmostEqual(cases[i]["front_kpa"], -cases[i+4]["back_kpa"])
            self.assertAlmostEqual(cases[i]["back_kpa"], -cases[i+4]["front_kpa"])
        opened = end_wind_cases(WindInputs(orientation="Any", front_wall_clad=False, back_wall_clad=True))
        self.assertTrue(all(c["front_kpa"] == 0 for c in opened))
        self.assertTrue(any(c["back_kpa"] != 0 for c in opened))
        with self.assertRaisesRegex(AnalysisError, "No end-wall pressure"):
            end_wind_cases(WindInputs(front_wall_clad=False, back_wall_clad=False))

    def test_actual_desktop_analysis_preserves_portal_and_exports_complete_evidence(self):
        from bracing_analysis_desktop import prepare_context, analyse_context, text_report
        w = self.window
        before = copy.deepcopy(w.collect_preset_data())
        springs = dict(w.structure.springs)
        movement = w.displacements.copy()
        context = prepare_context(w)
        profile = min(w.catalog.shs_profiles(), key=lambda p: abs(p.A-1500))
        result = analyse_context(context, profile, profile)
        self.assertEqual(result["status"], "SOLVED_DEMANDS_ONLY")
        self.assertEqual(len(result["cases"]), 8)
        self.assertEqual(w.collect_preset_data(), before)
        self.assertEqual(w.structure.springs, springs)
        np.testing.assert_array_equal(w.displacements, movement)
        encoded = json.loads(json.dumps(result, allow_nan=False))
        self.assertTrue(encoded["cases"][0]["portals"][0]["member_end_forces"])
        self.assertIn("ULS MEMBER DEMAND ENVELOPES", text_report(result))
        self.assertIn("no P-delta", text_report(result))
        self.assertNotEqual(result["nominal_layout"]["geometry"]["span_mm"], result["analysis_layout"]["geometry"]["span_mm"])
        for case in result["cases"]:
            self.assertTrue(all(m["axial_n"] >= 0 for m in case["members"] if m["tension_only"]))
        w.building_length_input.setText("48")
        with self.assertRaisesRegex(AnalysisError, "dimensions changed"):
            prepare_context(w)

    def test_legacy_restraint_removed_in_private_copy_and_new_settings_roundtrip(self):
        from bracing_analysis_desktop import prepare_context
        w = self.window
        preset = w.collect_preset_data()
        preset["geometry"]["end_wall_bracing"] = "Both ends"
        w.apply_preset_data(preset)
        w.generate_and_solve()
        context = prepare_context(w)
        endpoints = [n for e in context["portal"].structure.elements if e.group == "TOP" for n in (e.start, e.end)]
        left = min(endpoints, key=lambda n: n.x)
        right = max(endpoints, key=lambda n: n.x)
        for n in (left, right):
            self.assertNotIn(n.ux, context["portal"].structure.springs)
            self.assertNotIn(n.ux, context["portal"].structure.restrained_dofs)
        self.assertTrue(any(n.ux in w.structure.springs for n in (left, right)))
        w.bracing_3d_settings = {"schema": "portalcalc.bracing-analysis-settings.v1", "strut_section": "test", "end_post_section": "test"}
        saved = copy.deepcopy(w.collect_preset_data())
        w.apply_preset_data(self.defaults)
        self.assertIsNone(w.bracing_3d_settings)
        w.apply_preset_data(saved)
        self.assertEqual(w.bracing_3d_settings, saved["bracing"]["analysis_3d"])

    def test_dialog_runs_exports_and_invalidates_previous_success_on_failure(self):
        from bracing_analysis_desktop import prepare_context
        from bracing_analysis_ui import BracingAnalysisDialog
        dialog = BracingAnalysisDialog(prepare_context(self.window), self.window.catalog)
        try:
            dialog.run_button.click()
            self.assertIsNotNone(dialog.result)
            self.assertGreater(dialog.member_table.rowCount(), 0)
            self.assertEqual(dialog.case_combo.count(), 8)
            legacy_end = copy.deepcopy(dialog.context["end_brace"])
            dialog.end_brace_combo.setCurrentIndex((dialog.end_brace_combo.currentIndex()+1) % dialog.end_brace_combo.count())
            self.assertIsNone(dialog.result)
            self.assertFalse(dialog.export_json_button.isEnabled())
            dialog.run_button.click()
            self.assertEqual(dialog.result["sections"]["end_brace"]["label"], dialog.end_brace_combo.currentText())
            self.assertEqual(dialog.context["end_brace"], legacy_end)
            with tempfile.TemporaryDirectory() as output:
                path = Path(output)/"evidence.json"
                with mock.patch("bracing_workspace_ui.QFileDialog.getSaveFileName", return_value=(str(path), "JSON")):
                    dialog.export_json_button.click()
                self.assertEqual(json.loads(path.read_text())["status"], "SOLVED_DEMANDS_ONLY")
            with mock.patch("bracing_workspace_ui.solve_selection", side_effect=AnalysisError("Unstable benchmark")):
                dialog.run_button.click()
            self.assertIsNone(dialog.result)
            self.assertFalse(dialog.export_json_button.isEnabled())
            self.assertIn("Unstable benchmark", dialog.status_label.text())
            readonly = BracingAnalysisDialog(dialog.context, self.window.catalog, read_only=True)
            self.assertFalse(readonly.strut_combo.isEnabled())
            self.assertFalse(readonly.post_combo.isEnabled())
            self.assertFalse(readonly.end_brace_combo.isEnabled())
            self.assertFalse(readonly.auto_button.isEnabled())
            self.assertTrue(all(not box.isEnabled() for box in readonly.criteria_inputs.values()))
            readonly.reject()
        finally:
            dialog.reject()


if __name__ == "__main__":
    unittest.main(verbosity=2)
