"""Independent section/cost workings, tiny exhaustive sizing and workspace UI."""
import copy
from dataclasses import replace
import itertools
import json
import math
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

from bracing_analysis import AnalysisError, PortalReduction
from bracing_analysis_desktop import prepare_context
from bracing_analysis_regression import frame
from bracing_design import (DesignCriteria, auto_size, group_cost, member_assessment,
                            solve_selection, candidate_stiffness, stiffness_ceiling)
from bracing_sizing_diagnostics import SizingStopped
from bracing_layout import BracingLayout, LayoutGeometry


def member_fixture(kind="roof_brace", length=5000., force=40000.):
    member = {"id": "member", "kind": kind, "length_mm": length, "axial_n": force,
              "tension_only": kind.endswith("brace"), "start": 0, "end": 1}
    return {"nodes": [{"index": 0, "point_mm": (3000, 0, 0)}, {"index": 1, "point_mm": (3000, 0, length)}],
            "analysis_layout": {"geometry": {"end_wall_stations_mm": [0, 3000, 6000]}},
            "cases": [{"case": {"name": level, "level": level, "front_kpa": .5, "back_kpa": -.2},
                       "members": [dict(member)], "displacements_mm": [[0, 0, 0], [0, 0, 0]]} for level in ("ULS", "SLS")]}


def rod(d):
    return {"label": f"{d} DIA ROD", "area_mm2": math.pi*d*d/4, "description": "Fixture rod"}


class DesignMechanicsTests(unittest.TestCase):
    def test_apex_roof_tributary_width_is_half_actual_sheet_span_not_frame_span(self):
        from bracing_analysis_desktop import apex_tributary_widths
        self.assertEqual(apex_tributary_widths([(-1200,0),(0,0),(1200,0)],(0,0)),[600.,600.])
        self.assertEqual(apex_tributary_widths([(-1800,0),(0,0),(1200,0)],(0,0)),[900.,600.])
        self.assertEqual(apex_tributary_widths([(0,0),(1200,0)],(0,0)),[])
    @classmethod
    def setUpClass(cls):
        from main import ShapeCatalog
        cls.catalog = ShapeCatalog("Shape_Catalog.xlsx")

    def test_adopted_brace_tension_is_09_gross_yield_connections_separate(self):
        criteria = DesignCriteria()
        area = math.pi*16**2/4
        expected = .9*area*300/1000
        row = member_assessment(member_fixture(), 0, rod(16), criteria)
        self.assertAlmostEqual(row["case_checks"][0]["capacity_kn"], expected, places=9)
        self.assertAlmostEqual(row["ratio"], 40/expected, places=9)
        self.assertTrue(row["passes"])
        self.assertTrue(row['case_checks'][0]['preliminary'])
        self.assertFalse(row['case_checks'][0]['connections_checked'])
        reduced = member_assessment(member_fixture(),0,rod(16),replace(criteria,net_area_factor=.1,tension_distribution_factor=.1))
        self.assertEqual(reduced['case_checks'][0]['capacity_kn'],expected)
        with self.assertRaises(AnalysisError):
            replace(criteria, net_area_factor=1.01).validate()
        with self.assertRaises(AnalysisError):
            replace(criteria, brace_fu_mpa=200).validate()

    def test_bracing_compression_area_is_independent_of_tension_connection_factors(self):
        from bracing_design import section_properties
        from member_checks import compression_section_capacity_kn, member_compression_capacity_kn, MemberActions
        profile = self.catalog.profile_by_name['100*4 SHS']
        a = section_properties(profile,DesignCriteria(net_area_factor=.7))
        b = section_properties(profile,DesignCriteria(net_area_factor=.2,tension_distribution_factor=.1))
        self.assertEqual(a.compression_area_mm2,profile.A)
        self.assertAlmostEqual(compression_section_capacity_kn(a),.9*a.kf*profile.A*a.fy_mpa/1000)
        actions = MemberActions(effective_length_y_mm=8000.,effective_length_z_mm=8000.)
        self.assertEqual(member_compression_capacity_kn(a,actions),member_compression_capacity_kn(b,actions))
        # General engine callers retain their original net-area behaviour when
        # no separate compression-body area is declared.
        legacy = replace(a,compression_area_mm2=None)
        self.assertAlmostEqual(compression_section_capacity_kn(legacy),.7*compression_section_capacity_kn(a))

    def test_physical_cost_counts_slack_rods_fittings_waste_stock_and_rates(self):
        fixture = member_fixture(length=5000)
        fixture["cases"][0]["members"].append({**fixture["cases"][0]["members"][0], "id": "slack", "axial_n": 0.})
        rate = {"ub": 2., "shs": 1., "wb": 3.}
        priced = group_cost(fixture, "roof_brace", rod(16), rate)
        self.assertAlmostEqual(priced["listed_fittings_cost"], 2*9.62)
        self.assertAlmostEqual(priced["total"], math.pi*16**2/4*10*.00785*2 + 2*9.62)
        beam = member_fixture("longitudinal_strut", length=4600)
        ub = SimpleNamespace(name="Test UB", group_name="UB", shape_type="I", A=1000)
        shs = SimpleNamespace(name="Test SHS", group_name="SHS", shape_type="HOLLOW", A=1200)
        cu, ch = group_cost(beam, "strut", ub, rate), group_cost(beam, "strut", shs, rate)
        self.assertAlmostEqual(cu["total"], 1000*5*.00785*2)
        self.assertAlmostEqual(ch["total"], 1200*4.6*1.07*.00785)
        self.assertLess(ch["total"], cu["total"])  # heavier is cheaper at these rates

    def test_strut_selfweight_and_post_wind_workings_match_beam_statics(self):
        profile = self.catalog.profile_by_name["100*4 SHS"]
        criteria = DesignCriteria()
        strut = member_assessment(member_fixture("longitudinal_strut", 8000, -20000), 0, profile, criteria)
        weight = profile.A*7.85e-6*9.80665
        ultimate, service = strut["case_checks"]
        self.assertAlmostEqual(ultimate["actions"]["moment_knm"], 1.2*weight*8000**2/8/1e6)
        self.assertEqual(ultimate["actions"]["effective_length_y_mm"], 8000)
        self.assertAlmostEqual(service["deflection_mm"], 5*weight*8000**4/(384*200000*profile.I_analysis))
        post = member_assessment(member_fixture("end_post", 4000, -10000), 0, profile, criteria)
        # 0.5 kPa over a 3 m tributary width -> 1.5 kN/m, M=3 kNm.
        self.assertAlmostEqual(post["case_checks"][0]["actions"]["moment_knm"], 3.)
        self.assertAlmostEqual(post["case_checks"][0]["actions"]["shear_kn"], 3.)
        self.assertGreater(post["case_checks"][0]["actions"]["compression_kn"], 10.)

    def small_context(self):
        g = LayoutGeometry.create(6000, 8000, 3000, 10, "Gable", end_wall_stations_mm=[0, 6000])
        layout = BracingLayout(g)
        for p in layout.panels.values():
            if "/bay/0" in p.id:
                layout.toggle(p.id, "pinned")
        macro = PortalReduction(frame(g), [(x, g.roof_z(x)) for x in g.roof_stations])
        cases = [{"name": f"{level} {direction}", "level": level,
                  "front_kpa": (.4 if direction == 1 else -.1)*scale,
                  "back_kpa": (.1 if direction == 1 else -.4)*scale}
                 for level, scale in (("ULS", 1.), ("SLS", .6)) for direction in (1, -1)]
        context = {"layout": layout, "nominal_layout": layout.to_dict(), "portal": macro, "cases": cases,
                   "source_hash": "fixture", "source_preset": {}, "front_clad": True, "back_clad": True,
                   "roof_brace": rod(16), "wall_brace": rod(16), "end_brace": rod(16)}
        p = self.catalog.profile_by_name
        selections = {"strut": p["100*4 SHS"], "end_post": p["100*4 SHS"],
                      "roof_brace": rod(20), "wall_brace": rod(20), "end_brace": rod(16)}
        candidates = {"strut": [p["100*3 SHS"], p["100*4 SHS"]], "end_post": [p["100*4 SHS"]],
                      "roof_brace": [rod(16), rod(20)], "wall_brace": [rod(16), rod(20)], "end_brace": [rod(16)]}
        return context, selections, candidates

    def test_cost_sizing_matches_tiny_exhaustive_catalogue_and_exact_rerun(self):
        context, selections, candidates = self.small_context()
        rates, criteria = {"shs": 3., "ub": 4., "wb": 5.}, DesignCriteria()
        costs = []
        for strut, roof, wall in itertools.product(candidates["strut"], candidates["roof_brace"], candidates["wall_brace"]):
            option = {**selections, "strut": strut, "roof_brace": roof, "wall_brace": wall}
            result = solve_selection(context, option, criteria, rates)
            if result["design"]["passes"]:
                costs.append(result["design"]["estimated_cost"])
        best, chosen = auto_size(context, selections, candidates, criteria, rates)
        self.assertTrue(best["design"]["passes"])
        self.assertAlmostEqual(best["design"]["estimated_cost"], min(costs), places=6)
        rerun = solve_selection(context, chosen, criteria, rates)
        np.testing.assert_allclose(rerun["cases"][0]["displacements_mm"], best["cases"][0]["displacements_mm"])
        self.assertTrue(all(row["passes"] for row in rerun["section_checks"].values()))
        json.dumps(best, allow_nan=False)
        before = dict(selections)
        with self.assertRaisesRegex(AnalysisError, "cancelled"):
            auto_size(context, selections, candidates, criteria, rates,
                      progress=lambda *args: (_ for _ in ()).throw(AnalysisError("cancelled")))
        self.assertEqual(before, selections)

    def test_infeasible_sls_never_returns_a_cost_only_candidate(self):
        context, selections, candidates = self.small_context()
        with self.assertRaises(AnalysisError):
            auto_size(context, selections, candidates, replace(DesignCriteria(), sls_divisor=100000),
                      {"shs": 3., "ub": 4., "wb": 5.}, max_solves=4)

    def collector_fixture(self, overload=1.25):
        """Independent parallel-spring SLS oracle; real member checks remain."""
        context, selected, pools = self.small_context()
        selected.update(roof_brace=rod(16), wall_brace=rod(16))
        pools['wall_brace'] = [rod(16)]  # already at its catalogue maximum
        pools['roof_brace'] = [rod(16), rod(20)]
        stiffness_load = overload*2*candidate_stiffness(rod(16))
        real_solve = solve_selection
        def analysed(c, sections, criteria, rates):
            result = real_solve(c, sections, criteria, rates)
            # For two parallel collector springs, displacement is P/(k1+k2).
            ratio = stiffness_load/sum(candidate_stiffness(sections[g]) for g in ('roof_brace', 'wall_brace'))
            sls = result['design']['global_sls']
            sls.update(ratio=ratio, movement_mm=ratio*sls['limit_mm'])
            result['design']['passes'] = result['design']['member_checks_pass'] and ratio <= 1 and result['design']['taut_tangent_verified']
            return result
        return context, selected, pools, analysed

    def test_saturated_group_can_remain_while_other_groups_meet_sls(self):
        c,s,p,analysed = self.collector_fixture()
        with mock.patch('bracing_design.solve_selection', side_effect=analysed) as solver:
            result, chosen = auto_size(c,s,p,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.},max_solves=8)
        self.assertTrue(result['design']['passes'])
        self.assertEqual(chosen['wall_brace']['label'], '16 DIA ROD')
        self.assertEqual(chosen['roof_brace']['label'], '20 DIA ROD')
        self.assertEqual(chosen['strut'].name,p['strut'][0].name)
        self.assertAlmostEqual(result['design']['global_sls']['ratio'], 1.25*2*16**2/(16**2+20**2))
        self.assertLessEqual(solver.call_count, 8)
        self.assertEqual(solver.call_count, result['sizing_search']['exact_solves'])
        # The cheaper 16 mm roof alternative fails the exact SLS oracle and
        # must not overwrite the passing sections or their frozen evidence.
        self.assertTrue(any(not r.get('passes', False) and not r['adopted'] for r in result['sizing_search']['cost_reduction_trials']))
        self.assertEqual(result['design_request']['sections']['roof_brace'], chosen['roof_brace']['label'])

    def test_exhausted_bounded_search_retains_inputs_and_reports_movement(self):
        c,s,p,analysed = self.collector_fixture(overload=10.)
        with mock.patch('bracing_design.solve_selection', side_effect=analysed):
            with self.assertRaises(SizingStopped) as caught:
                auto_size(c,s,p,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.},max_solves=5)
        error = caught.exception
        self.assertEqual(error.diagnostic['input_target'], 'layout')
        self.assertIn('not proof', error.diagnostic['action'])
        self.assertEqual(error.result['design_request']['sections']['roof_brace'], '16 DIA ROD')
        self.assertGreater(error.diagnostic['screening_global_sls']['ratio'], 1.)
        json.dumps(error.result, allow_nan=False)

    def test_global_sway_does_not_select_a_large_strut_when_uls_and_local_sls_pass(self):
        c,s,p,analysed=self.collector_fixture()
        p['strut'].append(self.catalog.profile_by_name['250*12.5 SHS'])
        with mock.patch('bracing_design.solve_selection',side_effect=analysed):
            result,chosen=auto_size(c,s,p,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.},max_solves=8)
        self.assertTrue(result['design']['passes'])
        self.assertEqual(chosen['strut'].name,'100*3 SHS')
        self.assertEqual(chosen['roof_brace']['label'],'20 DIA ROD')
        self.assertTrue(all(row['sections']['strut']!='250*12.5 SHS' for row in result['sizing_search']['rounds']))
        self.assertIn('no global-sway stiffness floor',result['sizing_search']['strut_sizing_basis'])

    def test_stiffness_ceiling_excludes_unpriced_and_policy_ineligible_sections(self):
        from bracing_schedule import rod_options
        c,s,p = self.small_context()
        c['face_cladding'] = {'left_wall': False, 'right_wall': True}
        result = solve_selection(c,s,DesignCriteria(),{'shs':3.,'ub':4.,'wb':5.})
        rods = rod_options()
        angle = {'label': 'EA 100x100x10', 'area_mm2': 1900.}
        ceiling = stiffness_ceiling(c,result,'wall_brace',[*rods,angle],{'shs':3.,'ub':4.,'wb':5.})
        self.assertAlmostEqual(ceiling, candidate_stiffness(rods[2]))  # M28 unpriced, angle prohibited


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls.temp = tempfile.TemporaryDirectory()
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
        self.window.generate_and_solve()

    def dialog(self):
        from bracing_analysis_ui import BracingAnalysisDialog
        dialog = BracingAnalysisDialog(prepare_context(self.window, allow_unconfigured=True), self.window.catalog,
                                      saved={"mixed_struts": False})
        for p in dialog.layout_editor.model.panels.values():
            if "/bay/0" in p.id and ("wall" in p.face or p.face.startswith("roof")):
                dialog.layout_editor.model.toggle(p.id, "pinned")
        dialog.layout_editor.remember()
        return dialog

    def test_blank_layout_can_be_edited_analysed_and_inspected_on_one_screen(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        before = copy.deepcopy(self.window.collect_preset_data())
        dialog = self.dialog()
        try:
            dialog.show()
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result)
            self.assertTrue(dialog.result["section_checks"])
            self.app.processEvents()
            self.assertTrue(dialog.canvas.hit_members)
            # Click an actual rendered member midpoint; the nearest visible
            # segment is selected and its check/section is shown.
            a, b, identifier, depth = dialog.canvas.hit_members[-1]
            point = (a+b)*.5
            picked = dialog.canvas.member_at(point.toPoint())
            QTest.mouseClick(dialog.canvas, Qt.LeftButton, pos=point.toPoint())
            self.assertEqual(dialog.canvas.member_selected, picked)
            self.assertIn(picked, dialog.member_detail.text())
            dialog.visual_mode.setCurrentIndex(dialog.visual_mode.findData("deformed"))
            original, displaced = dialog.canvas.result_points(), dialog.canvas.result_points(True)
            self.assertGreater(np.max(np.abs(np.subtract(original, displaced))), 0.)
            dialog.visual_mode.setCurrentIndex(0)
            old = dialog.layout_editor.snapshot()
            dialog.layout_editor.toggle_panel("left_wall/bay/1")
            self.assertIsNone(dialog.result)
            self.assertFalse(dialog.export_json_button.isEnabled())
            dialog.layout_editor.restore_history(-1)
            self.assertEqual(dialog.layout_editor.snapshot(), old)
            self.assertEqual(self.window.collect_preset_data(), before)
        finally:
            dialog.reject()

    def test_requested_orbit_convention_is_locked_and_saved(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        dialog = self.dialog()
        canvas = dialog.canvas
        before = dialog.layout_snapshot()
        try:
            self.assertTrue(canvas.invert_orbit_horizontal)
            self.assertFalse(canvas.invert_orbit_vertical)
            self.assertFalse(dialog.layout_editor.orbit_horizontal.isEnabled())
            self.assertFalse(dialog.layout_editor.orbit_vertical.isEnabled())
            for unused in range(2):
                from PySide6.QtTest import QTest
                QTest.mouseClick(dialog.layout_editor.orbit_horizontal, Qt.LeftButton)
                QTest.mouseClick(dialog.layout_editor.orbit_vertical, Qt.LeftButton)
                start, end = QPointF(100, 100), QPointF(140, 120)
                azimuth, elevation = canvas.azimuth, canvas.elevation
                self.app.sendEvent(canvas, QMouseEvent(QEvent.MouseButtonPress, start, start, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
                self.app.sendEvent(canvas, QMouseEvent(QEvent.MouseMove, end, end, Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
                self.app.sendEvent(canvas, QMouseEvent(QEvent.MouseButtonRelease, end, end, Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
                self.assertLess(canvas.azimuth, azimuth)
                self.assertGreater(canvas.elevation, elevation)
                self.assertEqual(dialog.layout_snapshot(), before)
            self.assertTrue(dialog.snapshot()["invert_orbit_horizontal"])
            self.assertFalse(dialog.snapshot()["invert_orbit_vertical"])
            from bracing_analysis_ui import BracingAnalysisDialog
            for legacy in (False, True):
                migrated = BracingAnalysisDialog(dialog.context, self.window.catalog, {"reverse_orbit": legacy,
                                                                                       "invert_orbit_horizontal": False, "invert_orbit_vertical": True})
                self.assertTrue(migrated.canvas.invert_orbit_horizontal)
                self.assertFalse(migrated.canvas.invert_orbit_vertical)
                restored = BracingAnalysisDialog(dialog.context, self.window.catalog, migrated.snapshot())
                self.assertTrue(restored.canvas.invert_orbit_horizontal)
                self.assertFalse(restored.canvas.invert_orbit_vertical)
                migrated.reject()
                restored.reject()
        finally:
            dialog.reject()

    def test_actual_workspace_button_saves_or_cancels_layout_and_settings_atomically(self):
        from PySide6.QtWidgets import QDialog
        from bracing_analysis_ui import BracingAnalysisDialog
        w = self.window
        def edit(dialog):
            dialog.layout_editor.model.toggle("left_wall/bay/0", "pinned")
            dialog.layout_editor.remember()
            dialog.layout_editor.orbit_horizontal.setChecked(True)
            dialog.layout_editor.orbit_vertical.setChecked(False)
        def cancel(dialog):
            edit(dialog)
            return QDialog.Rejected
        with mock.patch.object(BracingAnalysisDialog, "exec", new=cancel):
            w.bracing_layout_button.click()
        self.assertIsNone(w.bracing_location_layout)
        self.assertIsNone(w.bracing_3d_settings)
        def save(dialog):
            edit(dialog)
            return QDialog.Accepted
        with mock.patch.object(BracingAnalysisDialog, "exec", new=save):
            w.bracing_layout_button.click()
        self.assertIn("left_wall/bay/0", w.bracing_location_layout["pinned_panels"])
        self.assertTrue(w.bracing_3d_settings["invert_orbit_horizontal"])
        self.assertFalse(w.bracing_3d_settings["invert_orbit_vertical"])
        stored = copy.deepcopy(w.collect_preset_data())
        w.apply_preset_data(self.defaults)
        w.apply_preset_data(stored)
        self.assertEqual(w.bracing_3d_settings, stored["bracing"]["analysis_3d"])

    def test_autosizing_updates_controls_only_after_a_verified_result(self):
        dialog = self.dialog()
        try:
            before_layout = dialog.layout_snapshot()
            dialog.run_auto_size()
            self.assertIsNotNone(dialog.result)
            self.assertTrue(dialog.result["design"]["passes"])
            self.assertIn("sizing_search", dialog.result)
            self.assertEqual(dialog.snapshot()["strut_section"], dialog.result["sections"]["strut"]["section"])
            self.assertEqual(dialog.layout_snapshot(), before_layout)
            before_sections = dialog.snapshot()
            with mock.patch("bracing_workspace_ui.auto_size", side_effect=AnalysisError("Cancelled fixture")):
                dialog.run_auto_size()
            self.assertEqual(dialog.snapshot(), before_sections)
            self.assertIsNone(dialog.result)
        finally:
            dialog.reject()


if __name__ == "__main__":
    unittest.main(verbosity=2)
