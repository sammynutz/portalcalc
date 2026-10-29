"""Rated components, sharing/interaction, mixed sizing and data-entry checks.

All numerical ratings in tests are synthetic software fixtures, not design data.
"""
import copy
import json
import math
import os
import tempfile
import unittest

from bracing_analysis import AnalysisError, AxialModel3D, AxialMember
from bracing_components import (StrutPlan, assembly_assessment, location_options,
                                wire_check, wire_modulus, wire_options, wire_price)
from bracing_design import DesignCriteria, group_cost, member_assessment, solve_selection, auto_size
from bracing_schedule import rod_options, SOURCE
from purlin_combined import properties, compression_capacity, METHOD


def location(sharing=False):
    data = {"section": "FIXTURE PURLIN", "area_mm2": 600., "basis_key": "FIXTURE BASIS",
            "bending_key": "FIXTURE BENDING", "analysis_span_mm": 8000., "bending_span_mm": 8000.,
            "bending_ratio": .5, "service_ratio": .2, "added_bending_ratio": .02, "added_service_ratio": .01,
            "axial_rating": {"basis_key": "FIXTURE BASIS", "compression_kn": 40., "tension_kn": 80.,
                             "elastic_buckling_kn": 100., "source": "SYNTHETIC TEST DATA ONLY",
                             "back_to_back_sharing": sharing, "extra_bending_key": "FIXTURE BENDING",
                             "extra_compression_kn": 50., "extra_tension_kn": 70., "extra_sls_deflection_mm": 2.,
                             "back_to_back_compression_kn": 50., "back_to_back_tension_kn": 70., "back_to_back_sls_deflection_mm": 3.}}
    return {"id": "strut/bay/0/line/0", "purlin": data, "existing_count": 1,
            "bay": 0, "line": 0, "length_mm": 8000., "x_mm": 0., "braced_bay": True}


def fixture(force=-20000.):
    member = {"id": "strut/bay/0/line/0", "kind": "longitudinal_strut", "tension_only": False,
              "length_mm": 8000., "start": 0, "end": 1, "axial_n": force}
    return {"cases": [{"case": {"name": level, "level": level}, "members": [dict(member)]}
                      for level in ("ULS", "SLS")]}


class RatedComponentTests(unittest.TestCase):
    def test_simplified_purlin_workings_match_sheet_and_metroll_axes(self):
        self.assertAlmostEqual(compression_capacity(713., 79.6, 8000.), 63.7416705975)
        props = properties("C/Z 200 19", "Z")
        self.assertEqual(props["web_rx_mm"], 78.55)
        self.assertEqual(props["web_ix_mm4"], 4400000.)
        self.assertNotEqual(props["web_ix_mm4"], 4840000.)  # principal Ix is not used
        loc = location()
        data = loc["purlin"]
        data.update(section="C/Z 200 19", area_mm2=713., bending_ratio=1.34/2.55,
                    axial_rating={"compression_method": METHOD, "purlin_shape": "Z"})
        checked = assembly_assessment(fixture(-28000.), 0, location_options(loc, [])[0], DesignCriteria(), member_assessment)
        row = checked["case_checks"][0]
        ns = 903*713*(78.55/8000)**2
        self.assertAlmostEqual(row["compression_capacity_kn"], ns)
        self.assertAlmostEqual(row["available_compression_kn"], ns*(1-1.34/2.55))
        self.assertAlmostEqual(row["ratio"], 28/ns+1.34/2.55)
        self.assertTrue(checked["passes"])
        changed_criteria = DesignCriteria(net_area_factor=.1, tension_distribution_factor=.1)
        self.assertEqual(checked["ratio"], member_assessment(fixture(-28000.), 0, location_options(loc, [])[0], changed_criteria)["ratio"])
        # A second connected purlin changes the axial share, not its table radius.
        loc["existing_count"] = 2
        pair = assembly_assessment(fixture(-28000.), 0, location_options(loc, [])[0], DesignCriteria(), member_assessment)
        self.assertAlmostEqual(pair["ratio"], 14/ns+1.34/2.55)

    def test_simplified_limits_tension_missing_properties_and_exhausted_bending(self):
        loc = location()
        data = loc["purlin"]
        data.update(section="C/Z 250 15", area_mm2=638., axial_rating={"compression_method": METHOD})
        def assess(force):
            return assembly_assessment(fixture(force), 0, location_options(loc, [])[0], DesignCriteria(), member_assessment)
        self.assertTrue(assess(-1000.)["passes"])
        self.assertIsNone(assess(1000.)["ratio"])
        data["axial_rating"].update(tension_kn=80., source="SYNTHETIC TENSION TEST", basis_key=data["basis_key"])
        self.assertAlmostEqual(assess(1000.)["ratio"], .5+1/80)
        data["bending_ratio"] = 1.1
        failed = assess(-1000.)
        self.assertFalse(failed["passes"])
        self.assertEqual(failed["case_checks"][0]["available_compression_kn"], 0.)
        data["section"] = "unsupported"
        self.assertIsNone(assess(-1000.)["ratio"])
        with self.assertRaisesRegex(AnalysisError, "Inconsistent published"):
            properties("C/Z 150 24", "Z")
        self.assertEqual(properties("C/Z 150 24", "C")["area_mm2"], 708.)
        for shape in ("C", "Z"):
            self.assertGreater(properties("C/Z 250 15", shape)["web_rx_mm"], 96.)
        with self.assertRaises(AnalysisError):
            compression_capacity(638., 96.4, 0.)

    def test_wire_uses_declared_ea_and_capacity_not_solid_diameter(self):
        data = {"10": {"ea_kn": 4000., "design_tension_kn": 30., "construction": "TEST CONSTRUCTION",
                       "source": "SYNTHETIC RATED ASSEMBLY", "price_per_m": 2., "fittings_per_run": 10.}}
        options = wire_options(data)
        self.assertEqual([o["diameter_mm"] for o in options], [8, 10, 12, 14, 16])
        wire = options[1]
        model = AxialModel3D()
        a = model.add_node((0, 0, 0), fixed=True)
        b = model.add_node((5000, 0, 0))
        model.fixed.update((4, 5))
        model.add_member(AxialMember("wire", a, b, wire["area_mm2"], wire_modulus(wire), True))
        result = model.solve([0, 0, 0, 10000, 0, 0])
        self.assertAlmostEqual(result["displacements_mm"][1][0], 10000*5000/4000000)
        self.assertAlmostEqual(wire_check(wire, 10, "test")["ratio"], 1/3)
        self.assertAlmostEqual(wire_price(wire, 10, 2)["total"], 40.)
        with self.assertRaises(AnalysisError):
            wire_modulus(options[2])
        self.assertIsNone(wire_check(options[2], 0, "test")["ratio"])

    def test_supplied_rod_strengths_and_exact_fittings(self):
        rods = rod_options()
        self.assertEqual([r["label"] for r in rods], ["16 DIA ROD", "20 DIA ROD", "24 DIA ROD", "30 DIA ROD"])
        self.assertEqual([r["design_tension_kn"] for r in rods], [54.28672, 84.823, 122.1451, 190.8518])
        self.assertEqual([r["forged_turnbuckle"] for r in rods], ["M16", "M20", "M22", "M28"])
        result = fixture(54286.72)
        for case in result["cases"]:
            case["members"][0]["kind"] = "roof_brace"
            case["members"][0]["tension_only"] = True
        checked = member_assessment(result, 0, rods[0], DesignCriteria(net_area_factor=.1))
        self.assertAlmostEqual(checked["ratio"], 1.)
        self.assertEqual(checked["case_checks"][0]["grade_mpa"], 300.)
        self.assertIsNone(rods[-1]["fitting_unit_cost"])
        with self.assertRaisesRegex(AnalysisError, "M28"):
            group_cost(result, "roof_brace", rods[-1], {"ub": 3.})
        priced = rod_options({"30": 100.})[-1]
        self.assertEqual(group_cost(result, "roof_brace", priced, {"ub": 3.})["listed_fittings_cost"], 100.)

    def test_schedule_wire_strength_needs_separate_ea_source(self):
        self.assertEqual([o["rating"]["design_tension_kn"] for o in wire_options()], [22.96, 35.92, 51.2, 70.4, 92.])
        data = {"10": {"ea_kn": 4000.}}
        wire = wire_options(data)[1]
        with self.assertRaisesRegex(AnalysisError, "EA source"):
            wire_modulus(wire)
        data["10"]["ea_source"] = "SYNTHETIC SOFTWARE TEST EA ONLY"
        wire = wire_options(data)[1]
        self.assertAlmostEqual(wire_check(wire, 35.92, "test")["ratio"], 1.)
        self.assertEqual(wire["rating"]["source"], SOURCE)
        data["10"]["design_tension_kn"] = 100.
        self.assertFalse(wire_check(wire_options(data)[1], 35.92, "test").get("passes", True))

    def test_two_existing_purlins_share_force_and_are_not_costed_again(self):
        single = location_options(location(), [])[0]
        double_location = location()
        double_location["existing_count"] = 2
        double = location_options(double_location, [])[0]
        one = assembly_assessment(fixture(), 0, single, DesignCriteria(), member_assessment)
        two = assembly_assessment(fixture(), 0, double, DesignCriteria(), member_assessment)
        self.assertAlmostEqual(one["ratio"], .5+.5/(1-.2))
        self.assertAlmostEqual(two["ratio"], .25+.5/(1-.1))
        self.assertFalse(one["passes"])
        self.assertTrue(two["passes"])
        self.assertTrue(all(row["load_share"] == .5 for row in two["case_checks"]))
        cost = group_cost(fixture(), "strut", StrutPlan({double_location["id"]: double}, double), {"ub": 3., "purlin": 4.})
        self.assertEqual(cost["total"], 0.)
        self.assertEqual(cost["purchased_kg"], 0.)
        self.assertGreater(cost["installed_kg"], 0.)

    def test_added_cold_ply_requires_separate_restraint_and_bending_rating(self):
        loc = location()
        options = location_options(loc, [])
        extra = next(o for o in options if o.arrangement == "additional_purlin")
        # One existing ply keeps its full bending; the added ply uses its
        # separately rated residual axial capacity, not the installed table.
        checked = assembly_assessment(fixture(), 0, extra, DesignCriteria(), member_assessment)
        self.assertTrue(checked["passes"])
        cost = group_cost(fixture(), "strut", StrutPlan({loc["id"]: extra}, extra), {"ub": 3., "purlin": 4.})
        self.assertAlmostEqual(cost["total"], 600*8*.00785*1.07*4)
        loc["purlin"]["axial_rating"]["extra_compression_kn"] = None
        extra = next(o for o in location_options(loc, []) if o.arrangement == "additional_purlin")
        missing = assembly_assessment(fixture(), 0, extra, DesignCriteria(), member_assessment)
        self.assertFalse(missing["passes"])
        self.assertIsNone(missing["ratio"])

    def test_back_to_back_bending_sharing_is_explicit_not_composite_inertia(self):
        loc = location()
        loc["purlin"]["bending_ratio"] = .9
        loc["purlin"]["axial_rating"]["compression_kn"] = 20.
        options = location_options(loc, [])
        plain = next(o for o in options if o.arrangement == "back_to_back")
        a = assembly_assessment(fixture(-10000), 0, plain, DesignCriteria(), member_assessment)
        loc["purlin"]["axial_rating"]["back_to_back_sharing"] = True
        shared = next(o for o in location_options(loc, []) if o.arrangement == "back_to_back")
        b = assembly_assessment(fixture(-10000), 0, shared, DesignCriteria(), member_assessment)
        self.assertFalse(a["passes"])
        self.assertTrue(b["passes"])
        self.assertEqual(plain.A, shared.A)
        self.assertEqual(shared.A, 1200.)

    def test_stale_or_wrong_span_ratings_cannot_pass(self):
        for field, value in (("basis_key", "wrong"), ("compression_kn", None)):
            loc = location()
            loc["purlin"]["axial_rating"][field] = value
            row = assembly_assessment(fixture(), 0, location_options(loc, [])[0], DesignCriteria(), member_assessment)
            self.assertFalse(row["passes"])
            self.assertIsNone(row["ratio"])
        loc = location()
        loc["purlin"]["bending_span_mm"] = 6000
        row = assembly_assessment(fixture(), 0, location_options(loc, [])[0], DesignCriteria(), member_assessment)
        self.assertFalse(row["passes"])


class ComponentWorkspaceTests(unittest.TestCase):
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
        cls.window.generate_and_solve()
        from bracing_layout import BracingLayout
        layout = BracingLayout(cls.window.current_bracing_layout_geometry())
        for panel in layout.panels.values():
            if "/bay/0" in panel.id and ("wall" in panel.face or panel.face.startswith("roof")):
                layout.toggle(panel.id, "pinned")
        cls.window.bracing_location_layout = layout.to_dict()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.temp.cleanup()

    def dialog(self, saved=None):
        from bracing_analysis_desktop import prepare_context
        from bracing_analysis_ui import BracingAnalysisDialog
        return BracingAnalysisDialog(prepare_context(self.window), self.window.catalog, saved)

    def fill_fixture_ratings(self, dialog):
        from PySide6.QtWidgets import QTableWidgetItem
        from PySide6.QtCore import Qt
        editor = dialog.component_editor
        for combo in editor.purlin_methods.values():
            combo.setCurrentIndex(combo.findData("declared"))
        for row in range(editor.purlin_table.rowCount()):
            for col, value in ((2, "100"), (3, "160"), (4, "300"), (6, "SYNTHETIC SOFTWARE TEST ONLY")):
                editor.purlin_table.setItem(row, col, QTableWidgetItem(value))
            editor.purlin_table.item(row, 5).setCheckState(Qt.Checked)
            for col, value in ((1, "80"), (2, "120"), (3, "3"), (4, "80"), (5, "120"), (6, "3")):
                editor.extra_table.setItem(row, col, QTableWidgetItem(value))
        g = dialog.context["layout"].geometry
        ridge = min(range(len(g.roof_stations)), key=lambda i: abs(g.roof_stations[i]-g.span_mm/2))
        for row, (loc, arr, steel) in enumerate(editor.strut_rows):
            if loc["line"] == ridge:
                editor.strut_table.setItem(row, 3, QTableWidgetItem("2"))

    def test_unrated_real_purlins_are_visible_but_cannot_pass_or_auto_size(self):
        dialog = self.dialog()
        try:
            self.assertTrue(dialog.component_editor.mixed.isChecked())
            self.assertEqual(len(dialog.component_editor.wire_candidates()), 5)
            self.assertEqual([o["label"] for o in dialog.component_editor.brace_candidates() if o.get("component_type") == "scheduled_rod"], ["16 DIA ROD", "20 DIA ROD", "24 DIA ROD", "30 DIA ROD"])
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result)
            self.assertFalse(dialog.result["design"]["passes"])
            self.assertTrue(any(r["ratio"] is None for r in dialog.result["section_checks"].values()))
            worked = [r for checked in dialog.result["section_checks"].values() for r in checked["case_checks"] if r.get("method") == METHOD and "compression_capacity_kn" in r]
            self.assertTrue(worked)
            self.assertTrue(any(r["axial_kn"] < 0 and r["ratio"] is not None for r in worked))
            self.assertIn("SIMPLIFIED PURLIN COMPRESSION", dialog.report_text)
            before = dialog.snapshot()
            baseline_sections = copy.deepcopy(dialog.result["design_request"]["sections"])
            dialog.run_auto_size()
            self.assertIsNotNone(dialog.result)
            self.assertEqual(dialog.snapshot(), before)
            self.assertEqual(dialog.result["design_request"]["sections"], baseline_sections)
            issue = dialog.result["sizing_failure"]
            self.assertEqual(issue["status"], "STOPPED_NO_ADOPTION")
            self.assertIn("Nt", dialog.status_label.text())
            self.assertTrue(issue["rejected_alternatives"])
            self.assertIn("REJECTED ALTERNATIVES", dialog.report_text)
            self.assertTrue(dialog.export_json_button.isEnabled())
            self.assertFalse(dialog.sizing_issue_button.isHidden())
            dialog.sizing_issue_button.click()
            self.assertIs(dialog.tabs.currentWidget(), dialog.component_editor)
            self.assertIs(dialog.component_editor.tabs.currentWidget(), dialog.component_editor.purlin_table)
            self.assertEqual(dialog.component_editor.purlin_table.currentColumn(), 3)
            json.dumps(dialog.result, allow_nan=False)
            # Supplying the missing tensile data resolves this stop without
            # inventing compression ratings or adopting a failed candidate.
            from PySide6.QtWidgets import QTableWidgetItem
            for row in range(dialog.component_editor.purlin_table.rowCount()):
                dialog.component_editor.purlin_table.setItem(row, 3, QTableWidgetItem("160"))
                dialog.component_editor.purlin_table.setItem(row, 6, QTableWidgetItem("SYNTHETIC TENSION TEST ONLY"))
            self.assertIsNone(dialog.result)
            self.assertTrue(dialog.sizing_issue_button.isHidden())
            dialog.run_auto_size()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            self.assertTrue(dialog.result["design"]["passes"])
            self.assertNotIn("sizing_failure", dialog.result)
        finally:
            dialog.reject()

    def test_simplified_method_shape_and_tension_data_roundtrip(self):
        from PySide6.QtWidgets import QTableWidgetItem
        dialog = self.dialog()
        try:
            for row, key in enumerate(dialog.component_editor.rating_keys):
                dialog.component_editor.purlin_shapes[key].setCurrentText("C")
                dialog.component_editor.purlin_table.setItem(row, 3, QTableWidgetItem("160"))
                dialog.component_editor.purlin_table.setItem(row, 6, QTableWidgetItem("SYNTHETIC TEST TENSION DATA"))
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            self.assertTrue(all(r["ratio"] is not None for r in dialog.result["section_checks"].values()))
            dialog.run_auto_size()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            self.assertTrue(dialog.result["design"]["passes"])
            self.assertTrue(all(a.location["purlin"]["axial_rating"]["compression_method"] == METHOD
                                for a in dialog.selections()["strut"].members.values()))
            saved = dialog.snapshot()
            restored = self.dialog(saved)
            try:
                self.assertEqual(saved, restored.snapshot())
                self.assertTrue(all(v.currentText() == "C" for v in restored.component_editor.purlin_shapes.values()))
            finally:
                restored.reject()
            json.dumps(dialog.result, allow_nan=False)
        finally:
            dialog.reject()

    def test_scheduled_wire_ea_reaches_actual_building_solve_and_export(self):
        from PySide6.QtWidgets import QTableWidgetItem
        dialog = self.dialog({"mixed_struts": False})
        try:
            table = dialog.component_editor.wire_table
            for col, value in ((2, "4000"), (4, "2"), (5, "12"), (7, "SYNTHETIC SOFTWARE TEST EA ONLY")):
                table.setItem(1, col, QTableWidgetItem(value))
            for combo in dialog.brace_combos.values():
                combo.setCurrentIndex(combo.findText("10 mm WIRE ROPE"))
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            physical = [m for m in dialog.result["cases"][0]["members"] if m["tension_only"]]
            self.assertTrue(physical)
            for member in physical:
                self.assertAlmostEqual(member["area_mm2"]*member["modulus_mpa"], 4000000.)
                self.assertEqual(dialog.result["section_checks"][member["id"]]["case_checks"][0]["capacity_kn"], 35.92)
            json.dumps(dialog.result, allow_nan=False)
        finally:
            dialog.reject()

    def test_mixed_fixture_reanalyses_locations_and_preserves_data_roundtrip(self):
        dialog = self.dialog()
        try:
            self.fill_fixture_ratings(dialog)
            dialog.run_auto_size()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            self.assertTrue(dialog.result["design"]["passes"])
            plan = dialog.selections()["strut"]
            self.assertIsInstance(plan, StrutPlan)
            forms = {a.arrangement for a in plan.members.values()}
            self.assertIn("existing_purlins", forms)
            self.assertGreater(len(forms), 1)
            for m in dialog.result["cases"][0]["members"]:
                if m["kind"] == "longitudinal_strut":
                    self.assertAlmostEqual(m["area_mm2"], plan.members[m["id"]].A)
            rerun = solve_selection(dialog.current_context(), dialog.selections(), dialog.criteria(), dialog.context["cost_rates"])
            self.assertTrue(rerun["design"]["passes"])
            json.dumps(dialog.result, allow_nan=False)
            saved = dialog.snapshot()
            restored = self.dialog(saved)
            try:
                self.assertEqual(restored.component_editor.snapshot(), dialog.component_editor.snapshot())
            finally:
                restored.reject()
            # Changed roof bending invalidates added-ply ratings, while pure
            # axial ratings remain keyed to the unchanged section/restraints.
            context = copy.deepcopy(dialog.context)
            for data in context["installed_purlins"].values():
                data["bending_key"] = "changed roof bending"
            from bracing_analysis_ui import BracingAnalysisDialog
            stale = BracingAnalysisDialog(context, self.window.catalog, saved)
            try:
                self.assertTrue(all(r["extra_compression_kn"] is None for r in stale.component_editor.snapshot()["purlin_axial_ratings"].values()))
            finally:
                stale.reject()
        finally:
            dialog.reject()


if __name__ == "__main__":
    unittest.main(verbosity=2)
