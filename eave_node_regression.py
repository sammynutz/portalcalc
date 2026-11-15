"""Eave height at the cladding pitching point; top chord node below it.

The node at the column centrelines is
    H + (g + D/2) tan(pitch) - (p + t/2) / cos(pitch)
(girt g, column depth D, purlin p, top chord t; gables use the mean of both
sides' g + D/2, skillions the low side's). The wall above the node and the
roof out to the cladding line are carried to the eave nodes, so the vertical
reactions take the roof over the full width to the cladding line.
"""
import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unittest

ROOT = Path(__file__).parent
DEFAULTS = json.loads((ROOT / "presets/factory_defaults.json").read_text(encoding="utf-8"))


class EaveNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls._appdata = tempfile.TemporaryDirectory(prefix="portalcalc_eave_")
        os.environ["APPDATA"] = cls._appdata.name
        from PySide6.QtWidgets import QApplication
        from main import MainWindow
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def solve(self, **geometry):
        w = self.window
        preset = copy.deepcopy(DEFAULTS)
        preset["geometry"].update(geometry)
        preset["canopies"].update(left_type="None", right_type="None", left_length_mm="0", right_length_mm="0")
        w.apply_preset_data(preset)
        w._suppress_panel_topology_search = True
        w.generate_and_solve()
        self.assertIsNotNone(w.structure, w.results_box.toPlainText()[:400])
        return w

    def hand_node(self, w, skillion=False):
        b = w.eave_node_basis_record
        left = w.selected_profile(w.left_column_profile_combo).depth / 2 + b["girt_mm"]
        right = w.selected_profile(w.right_column_profile_combo).depth / 2 + b["girt_mm"]
        inset = left if skillion else (left + right) / 2
        theta = math.radians(float(w.pitch_input.text()))
        top = w.profile_depth_mm(w.selected_profile(w.top_profile_combo))
        return float(w.eave_input.text()) * 1000 + inset * math.tan(theta) - (b["purlin_mm"] + top / 2) / math.cos(theta)

    def test_truss_depth_outside_the_optimise_range_is_warned(self):
        w = self.solve(roof_shape="Gable")
        self.assertEqual(w.truss_depth_outside_range_note(), "")
        span = float(w.span_input.text()) * 1000
        w.max_truss_depth_input.setText("950")
        w.depth_input.setText("1200")
        try:
            note = w.truss_depth_outside_range_note()
            self.assertIn("1200 mm", note)
            self.assertIn("Max truss depth", note)
            self.assertIn("started from 950 mm", note)
            w.depth_input.setText(f"{0.03 * span - 200:.0f}")          # below the 3% minimum
            self.assertIn("minimum (3% of span)", w.truss_depth_outside_range_note())
            w.depth_input.setText("940")                                # rounds up into range: no warning
            self.assertEqual(w.truss_depth_outside_range_note(), "")
        finally:
            w.max_truss_depth_input.setText("")

    def test_matched_columns_on_symmetric_buildings(self):
        w = self.solve(roof_shape="Gable")
        self.assertTrue(w.columns_should_match())
        left, right = w.left_column_profile_combo, w.right_column_profile_combo
        names = [left.itemText(i) for i in range(left.count())]
        try:
            w._matched_columns = True
            specs = {group: combo for _, combo, group in w.auto_size_group_specs()}
            self.assertIs(specs["RIGHT_COLUMN"], left)                 # one decision
            left.setCurrentIndex(names.index(names[3]))
            self.assertEqual(right.currentText(), names[3])
            right.setCurrentIndex(right.findText(names[5]))
            self.assertEqual(left.currentText(), names[5])           # either side leads
        finally:
            w._matched_columns = False
        left.setCurrentIndex(2)
        self.assertEqual(right.currentText(), names[5])               # independent outside Optimise
        self.assertFalse(self.solve(roof_shape="Skillion").columns_should_match())
        self.assertFalse(self.solve(roof_shape="Gable", left_support="Fixed", right_support="Pinned").columns_should_match())

    def test_gable_node_formula_and_frame_geometry(self):
        w = self.solve(roof_shape="Gable")
        node = self.hand_node(w)
        self.assertAlmostEqual(w.eave_node_basis_record["node_mm"], node, places=6)
        tops = [e for e in w.structure.elements if e.group == "TOP"]
        xs = [n for e in tops for n in (e.start, e.end)]
        left = min(xs, key=lambda n: n.x)
        self.assertAlmostEqual(left.y, node, places=6)
        self.assertLess(node, float(w.eave_input.text()) * 1000)
        text = w.results_box.toPlainText()
        self.assertIn("at the cladding pitching point", text)
        self.assertIn("Top chord node:", text)

    def test_skillion_uses_the_low_side(self):
        w = self.solve(roof_shape="Skillion")
        self.assertAlmostEqual(w.eave_node_basis_record["node_mm"], self.hand_node(w, skillion=True), places=6)
        strips = {s["side"]: s for s in w.eave_node_basis_record["strips"]}
        # The high side's cladding is above its node by more than the low side's.
        self.assertGreater(strips["RIGHT"]["wall_mm"], strips["LEFT"]["wall_mm"])

    def test_no_passing_purlin_uses_the_200_default(self):
        w = self.window
        basis = w.secondary_member_depth_mm({"match": {"status": "FAIL"}}, 200.0)
        self.assertEqual(basis[0], 200.0)
        self.assertIn("default", basis[1])
        self.assertEqual(w.secondary_member_depth_mm({"match": {"status": "OK", "section": "C/Z 300 24"}}, 200.0)[0], 300.0)

    def test_roof_load_reaches_the_cladding_line(self):
        """Gravity case: reactions = roof line load x (centreline span + both overhangs) + self weight."""
        w = self.solve(roof_shape="Gable")
        text = w.results_box.toPlainText()
        roof = float(re.search(r"Factored roof:.*=\s*(-?[\d.]+) kN/m", text).group(1))
        sw = float(re.search(r"Applied\s*:\s*(-?[\d.]+) kN vertical", text).group(1))
        span = float(re.search(r"Column centreline span: ([\d.]+) mm", text).group(1))
        ry = sum(float(v) for v in re.findall(r"(?:Left|Right) RY\s*=\s*(-?[\d.]+) kN", text))
        offsets = w.eave_node_basis_record["column_offsets_mm"]
        expected = -roof * (span + offsets["left"] + offsets["right"]) / 1000.0 - sw
        self.assertIn("1.2G + 1.5Q", text)
        self.assertAlmostEqual(ry, expected, delta=0.02)


if __name__ == "__main__":
    unittest.main()
