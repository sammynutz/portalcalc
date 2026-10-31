"""Joint roof-purlin selection checks, including actual manufacturer tables."""
import copy
import itertools
import os
import tempfile
import unittest
from unittest import mock

from purlin_selection import profile_key, select_roof_purlin_pair


def candidate(section, area, bridging=1):
    return {"status": "OK", "section": section, "area_mm2": area, "bridging_rows": bridging}


class PairSelectionTests(unittest.TestCase):
    def test_shared_profile_with_independent_thicknesses(self):
        general = [candidate("C/Z 150 12", 350), candidate("C/Z 200 12", 440)]
        edge = [candidate("C/Z 200 19", 700), candidate("C/Z 250 15", 750)]
        normal, local, info = select_roof_purlin_pair(general, edge, 20, 4)
        self.assertEqual(normal["section"], "C/Z 200 12")
        self.assertEqual(local["section"], "C/Z 200 19")
        self.assertEqual(info["weighted_area_mm2"], 20 * 440 + 4 * 700)

    def test_pair_weight_controls_profile_and_not_old_depth_preference(self):
        general = [candidate("C/Z 200 12", 400), candidate("C/Z 250 12", 500)]
        edge = [candidate("C/Z 200 24", 900), candidate("C/Z 250 15", 600)]
        normal, local, _ = select_roof_purlin_pair(general, edge, 20, 4)
        self.assertEqual(profile_key(normal["section"]), "C/Z 200")
        normal, local, _ = select_roof_purlin_pair(general, edge, 4, 20)
        self.assertEqual(profile_key(normal["section"]), "C/Z 250")

    def test_no_compatible_profile_or_zone_without_passing_sections(self):
        self.assertIsNone(select_roof_purlin_pair([candidate("C/Z 150 12", 350)], [candidate("C/Z 200 12", 440)], 20, 4))
        self.assertIsNone(select_roof_purlin_pair([], [candidate("C/Z 200 12", 440)], 20, 4))
        self.assertIsNone(select_roof_purlin_pair([candidate("C/Z 200 12", 440)], [], 20, 4))
        self.assertIsNone(select_roof_purlin_pair([candidate("C 200 12", 440)], [candidate("Z 200 12", 440)], 20, 4))

    def test_global_minimum_and_input_order_independence(self):
        general = [candidate(f"C/Z {depth} {gauge}", depth * gauge)
                   for depth in (150, 200, 250) for gauge in (12, 15, 19)]
        edge = [candidate(f"C/Z {depth} {gauge}", depth * gauge)
                for depth in (200, 250) for gauge in (15, 19, 24)]
        expected = min(17 * a["area_mm2"] + 6 * b["area_mm2"] for a, b in itertools.product(general, edge)
                       if profile_key(a["section"]) == profile_key(b["section"]))
        original = copy.deepcopy((general, edge))
        chosen = select_roof_purlin_pair(general, edge, 17, 6)
        self.assertEqual(chosen[2]["weighted_area_mm2"], expected)
        self.assertEqual(chosen, select_roof_purlin_pair(list(reversed(general)), list(reversed(edge)), 17, 6))
        self.assertEqual((general, edge), original)

    def test_all_edge_rows_ignore_uninstalled_general_weight(self):
        general = [candidate("C/Z 200 12", 400), candidate("C/Z 250 19", 900)]
        edge = [candidate("C/Z 200 24", 950), candidate("C/Z 250 24", 925)]
        normal, local, info = select_roof_purlin_pair(general, edge, 0, 5)
        self.assertEqual(profile_key(local["section"]), "C/Z 250")
        self.assertEqual(info["weighted_area_mm2"], 5 * 925)


class RoofIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls.temp = tempfile.TemporaryDirectory(prefix="portalcalc_purlins_")
        os.environ["APPDATA"] = cls.temp.name
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False
        cls.window._suppress_auto_web_split_on_solve = True
        cls.window.generate_and_solve()

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.temp.cleanup()

    def test_actual_tables_all_span_types_and_zone_capacities(self):
        from loads import BasicRoofLoads
        w = self.window
        wind = w.wind_inputs_from_ui(float(w.span_input.text()) * 1000, float(w.pitch_input.text()), float(w.eave_input.text()) * 1000)
        roof = BasicRoofLoads(bay_size_m=8, g=-0.15, q=-0.25, solar=-0.15)
        for span_type in ("Single span", "Double span", "Continuous lapped"):
            with self.subTest(span_type=span_type):
                result = w.purlin_check_from_inputs(w.purlin_layout, 8, roof, wind, span_type)
                normal, local = result["match"], result["local_match"]
                self.assertEqual(normal["status"], "OK")
                self.assertEqual(local["status"], "OK")
                self.assertEqual(profile_key(normal["section"]), profile_key(local["section"]))
                for match, prefix in ((normal, ""), (local, "local_")):
                    for demand, capacity in (("outward_kn_m", "outward_capacity_kn_m"), ("inward_strength_kn_m", "inward_strength_capacity_kn_m"), ("inward_service_kn_m", "inward_service_capacity_kn_m")):
                        self.assertLessEqual(result[prefix + demand], match[capacity] + 1e-9)
                    self.assertGreaterEqual(match["bridging_rows"], match["minimum_bridging_rows"])
                generals = w.match_metroll_purlin(span_type, 8000, result["outward_kn_m"], result["inward_strength_kn_m"], result["inward_service_kn_m"], include_candidates=True)["passing_candidates"]
                locals = w.match_metroll_purlin(span_type, 8000, result["local_outward_kn_m"], result["local_inward_strength_kn_m"], result["local_inward_service_kn_m"], include_candidates=True)["passing_candidates"]
                selection = result["profile_selection"]
                minimum = min(selection["general_rows"] * a["area_mm2"] + selection["edge_rows_total"] * b["area_mm2"]
                              for a, b in itertools.product(generals, locals) if profile_key(a["section"]) == profile_key(b["section"]))
                self.assertAlmostEqual(selection["weighted_area_mm2"], minimum)
                self.assertNotIn("passing_candidates", normal)
                self.assertNotIn("passing_candidates", local)

    def test_solved_outputs_and_costing_use_the_selected_pair(self):
        w = self.window
        result = w.purlin_check
        self.assertIsNotNone(result)
        self.assertEqual(profile_key(result["match"]["section"]), profile_key(result["local_match"]["section"]))
        report = w.section_checks_box.toPlainText()
        self.assertIn("same profile, independently selected thicknesses", report)
        roof_report = report.split("PURLIN CHECK\n", 1)[1].split("WALL GIRT CHECK\n", 1)[0]
        self.assertNotIn("deepest section within 5%", roof_report)
        items = [item for item in w.costing_summary["purlin_items"] if item["label"].startswith("Roof")]
        for item in items:
            match = result["match"] if item["label"] == "Roof general" else result["local_match"]
            self.assertEqual(item["section"], match["section"])
        self.assertEqual(sum(item["count"] for item in items), len(w.purlin_layout.points))

    def test_default_200_pair_is_heavier_and_thinner_200s_fail_uplift(self):
        w, p = self.window, self.window.purlin_check
        self.assertEqual(p['span_type'], 'Double span')
        self.assertEqual(p['match']['section'], 'C/Z 250 15')
        self.assertEqual(p['local_match']['section'], 'C/Z 250 19')
        options = {item['profile']: item for item in p['profile_selection']['profile_alternatives']}
        shallow = options['C/Z 200']
        self.assertEqual(shallow['general_section'], 'C/Z 200 19')
        self.assertEqual(shallow['edge_section'], 'C/Z 200 24')
        expected = (9*713+6*900)/(9*638+6*808)-1
        self.assertAlmostEqual(shallow['extra_material_percent'], expected*100)
        self.assertGreater(p['outward_kn_m'], w.purlin_capacity_row(w.metroll_tables['tables']['2A'], 'C/Z 200 15', 8000)['strength_capacity_kn_m'])
        self.assertGreater(p['local_outward_kn_m'], w.purlin_capacity_row(w.metroll_tables['tables']['2A'], 'C/Z 200 19', 8000)['strength_capacity_kn_m'])
        self.assertIn('C 200 19 / C 200 24', w.section_checks_box.toPlainText())

    def test_incompatible_passes_are_not_reported_as_a_passing_roof(self):
        from loads import BasicRoofLoads
        w = self.window
        wind = w.wind_inputs_from_ui(21000, 7.5, 6000)
        with mock.patch.object(w, "match_metroll_purlin", side_effect=[
            {**candidate("C/Z 150 12", 350), "passing_candidates": [candidate("C/Z 150 12", 350)]},
            {**candidate("C/Z 200 12", 440), "passing_candidates": [candidate("C/Z 200 12", 440)]},
        ]):
            result = w.purlin_check_from_inputs(w.purlin_layout, 8, BasicRoofLoads(bay_size_m=8), wind, "Double span")
        self.assertEqual(result["match"]["status"], "NOT OK")
        self.assertEqual(result["local_match"]["status"], "NOT OK")
        self.assertIn("No common", result["match"]["message"])
        self.assertNotIn("section", result["match"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
