"""Independent beam benchmarks and actual workspace integration.

All stiffness and capacity inputs below are SYNTHETIC SOFTWARE TEST DATA.
They are not supplier cladding stiffnesses or design member capacities.
"""
import copy
import json
import math
import unittest

from bracing_analysis import AnalysisError
from purlin_beam import solve_run
from purlin_beam_design import ARRANGEMENT, with_beam_demands


def run(**changes):
    params = dict(spans_mm=[8000.], roof_i_mm4=6e6, added_i_mm4=6e6, added_bays=[0],
                  coupling=0., loads=[dict(name="ULS inward", roof_n_mm=1., added_n_mm=0.)])
    params.update(changes)
    return solve_run(**params)


class BeamMechanicsTests(unittest.TestCase):
    def test_single_span_matches_independent_beam_formulae(self):
        solved = run()
        case = solved["cases"][0]
        roof, added = case["bays"][0]["roof"], case["bays"][0]["added"]
        self.assertAlmostEqual(roof["positive_moment_knm"], 1*8000**2/8/1e6, places=8)
        self.assertAlmostEqual(roof["shear_kn"], 1*8000/2/1000, places=8)
        self.assertAlmostEqual(roof["deflection_mm"], 5*1*8000**4/(384*200000*6e6), places=7)
        self.assertLess(added["positive_moment_knm"], 1e-8)
        self.assertLess(abs(case["vertical_balance_error_n"]), 1e-5)

    def test_finite_springs_preserve_total_load_and_do_not_force_half_sharing(self):
        solved = run(coupling=.1)
        case = solved["cases"][0]
        roof, added = case["bays"][0]["roof"], case["bays"][0]["added"]
        self.assertAlmostEqual(roof["positive_moment_knm"]+added["positive_moment_knm"], 8., places=7)
        self.assertAlmostEqual(roof["deflection_mm"]+added["deflection_mm"], 5*8000**4/(384*200000*6e6), places=6)
        self.assertGreater(roof["positive_moment_knm"], added["positive_moment_knm"])
        self.assertGreater(case["bays"][0]["transfer_kn"], 0.)
        self.assertLess(abs(case["vertical_balance_error_n"]), 1e-5)
        stiff = run(coupling=10.)["cases"][0]["bays"][0]
        self.assertLess(abs(stiff["roof"]["positive_moment_knm"]-stiff["added"]["positive_moment_knm"]), .02)

    def test_continuous_roof_retains_support_moment_and_adjacent_bay_can_increase(self):
        base = run(spans_mm=[8000., 8000.], span_type="double")["cases"][0]
        for bay in base["bays"]:
            self.assertAlmostEqual(bay["roof"]["negative_moment_knm"], 8., places=7)
            self.assertAlmostEqual(bay["roof"]["positive_moment_knm"], 4.5, places=7)
        shared = run(spans_mm=[8000., 8000.], span_type="double", coupling=.1)["cases"][0]
        self.assertGreater(shared["bays"][1]["roof"]["positive_moment_knm"], 4.5)
        samples = shared["bays"][0]["added"]["samples"]
        for point in (0., 8000.):
            self.assertLess(abs(next(s["moment_knm"] for s in samples if abs(s["x_mm"]-point) < 1e-6)), 1e-7)
        self.assertGreater(shared["bays"][0]["roof"]["negative_moment_knm"], 7.)

    def test_load_reversal_and_mesh_convergence(self):
        a = run(coupling=.1, spans_mm=[8000., 8000.], span_type="double")
        b = run(coupling=.1, spans_mm=[8000., 8000.], span_type="double", divisions=24)
        reversed_load = run(coupling=.1, spans_mm=[8000., 8000.], span_type="double",
            loads=[dict(name="ULS outward", roof_n_mm=-1., added_n_mm=0.)])
        for bay in range(2):
            for role in ("roof", "added"):
                d = a["cases"][0]["bays"][bay][role]
                refined = b["cases"][0]["bays"][bay][role]
                reverse = reversed_load["cases"][0]["bays"][bay][role]
                self.assertAlmostEqual(d["positive_moment_knm"], refined["positive_moment_knm"], delta=.002)
                self.assertAlmostEqual(d["positive_moment_knm"], reverse["negative_moment_knm"], places=7)
                self.assertAlmostEqual(d["deflection_mm"], refined["deflection_mm"], delta=.002)

    def test_lap_stiffness_and_invalid_inputs(self):
        solved = run(spans_mm=[8000., 8000., 8000.], span_type="continuous_lapped", coupling=.1)
        case = solved["cases"][0]
        self.assertTrue(any(s["plies"] == 2 for b in case["bays"] for s in b["roof"]["samples"]))
        self.assertLess(abs(case["vertical_balance_error_n"]), 1e-5)
        for changes in ({"coupling": -1.}, {"coupling": math.inf}, {"spans_mm": [0.]},
                        {"span_type": "unknown"}, {"divisions": 2}, {"added_bays": [2]},
                        {"span_type": "continuous_lapped", "lap_ratio": .1}, {"coupling": 1000.}):
            with self.assertRaises(AnalysisError):
                run(**changes)
        json.dumps(solved, allow_nan=False)

    def test_direct_load_pickup_is_separate_from_spring_transfer(self):
        solved = run(spans_mm=[8000., 8000.], span_type="double", coupling=0., loads=[dict(name="ULS inward",
            roof_n_mm=1., added_n_mm=0., roof_n_mm_by_bay=[.7, 1.], added_n_mm_by_bay=[.3, 0.])])
        case = solved["cases"][0]
        self.assertAlmostEqual(case["applied_load_n"], 16000.)
        self.assertAlmostEqual(case["bays"][0]["added"]["positive_moment_knm"], .3*8000**2/8/1e6, places=8)
        self.assertEqual(case["bays"][0]["transfer_kn"], 0.)

    def test_explicit_roof_release_schedule_changes_the_continuity(self):
        solved = run(spans_mm=[8000., 8000.], span_type="double", roof_releases_after_bays=[1])
        self.assertEqual(solved["roof_releases_after_bays"], [1])
        for bay in solved["cases"][0]["bays"]:
            self.assertAlmostEqual(bay["roof"]["positive_moment_knm"], 8., places=7)
            self.assertLess(bay["roof"]["negative_moment_knm"], 1e-7)


class BeamWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bracing_components_regression import ComponentWorkspaceTests
        ComponentWorkspaceTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from bracing_components_regression import ComponentWorkspaceTests
        ComponentWorkspaceTests.tearDownClass.__func__(cls)

    def dialog(self):
        from bracing_analysis_desktop import prepare_context
        from bracing_workspace_ui import BracingAnalysisDialog
        return BracingAnalysisDialog(prepare_context(self.window), self.window.catalog)

    def select_pair(self, dialog):
        row = dialog.component_editor.strut_rows[0]
        row[1].setCurrentIndex(row[1].findData(ARRANGEMENT))
        return row[0]["id"]

    def fill_fixture(self, dialog):
        from PySide6.QtWidgets import QTableWidgetItem
        editor = dialog.component_editor
        for row in range(editor.paired_table.rowCount()):
            for col, value in ((1, ".1"), (2, "SYNTHETIC CLADDING TEST ONLY"), (3, "100"), (4, "100"),
                               (5, "100"), (6, "100"), (7, "100"), (8, "100"), (9, "200"),
                               (10, "SYNTHETIC MEMBER TEST ONLY"), (12, "0")):
                editor.paired_table.setItem(row, col, QTableWidgetItem(value))
            editor.purlin_table.setItem(row, 3, QTableWidgetItem("200"))
            editor.purlin_table.setItem(row, 6, QTableWidgetItem("SYNTHETIC TENSION TEST ONLY"))

    def test_missing_cladding_gives_zero_sharing_demands_and_cannot_pass(self):
        dialog = self.dialog()
        try:
            identifier = self.select_pair(dialog)
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            beam = next(iter(dialog.result["purlin_beams"]["runs"].values()))
            self.assertFalse(beam["coupling_defined"])
            self.assertEqual(beam["coupling_kn_m_per_mm"], 0.)
            self.assertIsNone(dialog.result["section_checks"][identifier]["ratio"])
            self.assertIn("fallback", dialog.report_text)
            self.assertTrue(dialog.beam_results.lines.count())
        finally:
            dialog.reject()

    def test_fixture_demands_checks_plot_and_roundtrip(self):
        from PySide6.QtWidgets import QTableWidgetItem
        dialog = self.dialog()
        try:
            identifier = self.select_pair(dialog)
            self.fill_fixture(dialog)
            dialog.run_analysis()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            checked = dialog.result["section_checks"][identifier]
            self.assertTrue(all(r["ratio"] is not None for r in checked["case_checks"]))
            self.assertEqual({r["beam_role"] for r in checked["case_checks"]}, {"roof", "added"})
            self.assertIn("Cladding k", str(dialog.component_editor.paired_table.horizontalHeaderItem(1).text()))
            dialog.check_description(identifier)
            dialog.tabs.setCurrentWidget(dialog.beam_results)
            dialog.show()
            self.app.processEvents()
            for mode in range(dialog.beam_results.mode.count()):
                dialog.beam_results.mode.setCurrentIndex(mode)
                self.assertFalse(dialog.beam_results.grab().isNull())
            saved = dialog.snapshot()
            from bracing_workspace_ui import BracingAnalysisDialog
            restored = BracingAnalysisDialog(dialog.context, dialog.catalog, saved)
            try:
                self.assertEqual(restored.snapshot(), saved)
            finally:
                restored.reject()
            json.dumps(dialog.result, allow_nan=False)
            dialog.component_editor.paired_table.setItem(0, 1, QTableWidgetItem(".2"))
            self.assertIsNone(dialog.result)
            self.assertFalse(dialog.export_json_button.isEnabled())
        finally:
            dialog.reject()

    def test_member_ratings_are_required_and_changed_bending_invalidates_them(self):
        dialog = self.dialog()
        try:
            identifier = self.select_pair(dialog)
            self.fill_fixture(dialog)
            from PySide6.QtWidgets import QTableWidgetItem
            for row in range(dialog.component_editor.paired_table.rowCount()):
                dialog.component_editor.paired_table.setItem(row, 3, QTableWidgetItem(""))
            dialog.run_analysis()
            self.assertIsNone(dialog.result["section_checks"][identifier]["ratio"])
            self.assertTrue(dialog.result["purlin_beams"]["runs"])
            saved = dialog.snapshot()
            context = copy.deepcopy(dialog.context)
            for data in context["installed_purlins"].values():
                data["bending_key"] = "changed loads / capacity basis"
            from bracing_workspace_ui import BracingAnalysisDialog
            stale = BracingAnalysisDialog(context, dialog.catalog, saved)
            try:
                self.assertTrue(all(r["beam_added_positive_moment_knm"] is None for r in stale.snapshot()["purlin_axial_ratings"].values()))
                self.assertTrue(all(r["cladding_coupling_kn_m_per_mm"] == .1 for r in stale.snapshot()["purlin_axial_ratings"].values()))
            finally:
                stale.reject()
        finally:
            dialog.reject()

    def test_sizing_reanalyses_the_whole_run_before_adopting_a_sheeted_candidate(self):
        from unittest.mock import patch
        from bracing_components import location_options
        dialog = self.dialog()
        try:
            self.fill_fixture(dialog)
            def only_sheeted(location, profiles):
                return [a for a in location_options(location, profiles) if a.arrangement == ARRANGEMENT]
            with patch("bracing_mixed_sizing.location_options", side_effect=only_sheeted):
                dialog.run_auto_size()
            self.assertIsNotNone(dialog.result, dialog.status_label.text())
            self.assertTrue(dialog.result["design"]["passes"])
            self.assertTrue(all(a.arrangement == ARRANGEMENT for a in dialog.selections()["strut"].members.values()))
            from bracing_design import solve_selection
            repeat = solve_selection(dialog.current_context(), dialog.selections(), dialog.criteria(), dialog.context["cost_rates"])
            self.assertTrue(repeat["design"]["passes"])
            self.assertEqual({k: c["ratio"] for k,c in dialog.result["section_checks"].items()},
                             {k: c["ratio"] for k,c in repeat["section_checks"].items()})
        finally:
            dialog.reject()


if __name__ == "__main__":
    unittest.main(verbosity=2)
