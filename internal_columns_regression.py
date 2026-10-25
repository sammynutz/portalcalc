"""Offscreen checks for arbitrary internal supports and legacy preset compatibility."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
_appdata = tempfile.TemporaryDirectory(prefix="portalcalc_columns_")
os.environ["APPDATA"] = _appdata.name

from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget
from main import MainWindow, internal_column_positions_mm
from future_canopy_design import geometry_basis


class InternalColumnsRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False
        cls.defaults = json.loads((Path(__file__).parent / "presets/factory_defaults.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def setUp(self):
        self.window.apply_preset_data(copy.deepcopy(self.defaults))
        self.window._suppress_auto_web_split_on_solve = True
        self.window._suppress_panel_topology_search = True

    def solve(self):
        self.window.generate_and_solve()
        self.assertIsNotNone(self.window.structure, self.window.results_box.toPlainText())
        self.assertIsNotNone(self.window.displacements)
        self.assertTrue(np.isfinite(self.window.displacements).all())
        self.assertTrue(self.window.design_envelope_actions)

    def test_custom_layouts_and_connections(self):
        w = self.window
        for shape in ("Gable", "Skillion"):
            for system in ("Truss", "Rafter"):
                for connection in ("Pinned", "Moment"):
                    with self.subTest(shape=shape, system=system, connection=connection):
                        w.roof_shape_combo.setCurrentText(shape)
                        w.frame_system_combo.setCurrentText(system)
                        w.internal_column_count_combo.setCurrentText("Custom positions")
                        w.internal_column_positions_input.setText("20, 5, 12.25")
                        w.internal_column_connection_combo.setCurrentText(connection)
                        self.solve()
                        left_x = w.base_nodes[0].x
                        bases = w.base_nodes[2:5]
                        np.testing.assert_allclose([n.x - left_x for n in bases], [5000, 12250, 20000], atol=1e-6)
                        columns = [e for e in w.structure.elements if e.group == "INTERNAL_COLUMN"]
                        self.assertEqual(len(columns), 6 if system == "Truss" and connection == "Moment" else 3)
                        for base in bases:
                            self.assertTrue(any(e.start is base or e.end is base for e in columns))
                            self.assertIn(base.ux, w.structure.restrained_dofs)
                            self.assertIn(base.uy, w.structure.restrained_dofs)
                        self.assertNotEqual(w.internal_column_check_label.text(), "Not checked")
                        self.assertTrue(math.isfinite(w.design_envelope_actions["INTERNAL_COLUMN"]["compression_kn"]["value"]))

    def test_legacy_equivalence(self):
        w = self.window
        for count in ("1 column", "2 columns"):
            with self.subTest(count=count):
                w.internal_column_count_combo.setCurrentText(count)
                w.internal_column_offset_input.setText("3.3")
                self.solve()
                before = w.displacements.copy()
                positions = sorted({e.start.x - w.base_nodes[0].x for e in w.structure.elements if e.group == "INTERNAL_COLUMN"})
                w.internal_column_positions_input.setText(", ".join(f"{x / 1000:.12g}" for x in positions))
                w.internal_column_count_combo.setCurrentText("Custom positions")
                self.solve()
                np.testing.assert_allclose(w.displacements, before, rtol=1e-8, atol=1e-8)

    def test_preset_round_trip_and_legacy_reset(self):
        w = self.window
        w.internal_column_count_combo.setCurrentText("Custom positions")
        w.internal_column_positions_input.setText("5, 12.25, 20")
        w.internal_column_connection_combo.setCurrentText("Moment")
        saved = json.loads(json.dumps(w.collect_preset_data()))
        self.assertEqual(saved["geometry"]["internal_column_positions_m"], "5, 12.25, 20")
        w.apply_preset_data(copy.deepcopy(self.defaults))
        self.assertEqual(w.internal_column_positions_input.text(), "")
        w.apply_preset_data(saved)
        self.solve()
        self.assertEqual(w.internal_column_count_combo.currentText(), "Custom positions")
        self.assertEqual(w.internal_column_positions_input.text(), "5, 12.25, 20")
        self.assertEqual(w.internal_column_connection_combo.currentText(), "Moment")
        self.assertTrue(w.internal_column_offset_input.isHidden())
        self.assertFalse(w.internal_column_positions_input.isHidden())
        before = geometry_basis(w)
        w.internal_column_positions_input.setText("5, 13, 20")
        self.assertNotEqual(geometry_basis(w), before)
        w.apply_preset_data(copy.deepcopy(self.defaults))
        w.internal_column_count_combo.setCurrentText("None")
        self.solve()
        self.assertFalse(any(e.group == "INTERNAL_COLUMN" for e in w.structure.elements))

    def test_invalid_positions_rejected(self):
        for text in ("", "5,", "a", "5,,12", "nan", "inf", "-1", "0", "30", "31", "5, 5", "5, 5.0000000001"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                internal_column_positions_mm(text, 30000)
        w = self.window
        w.internal_column_count_combo.setCurrentText("Custom positions")
        for text in ("", "5, 5", "100", "nan"):
            w.internal_column_positions_input.setText(text)
            w.generate_and_solve()
            self.assertIn("Input error: Internal column positions", w.results_box.toPlainText())
            self.assertIsNone(w.structure)

    def test_ai_removed_and_local_evidence_available(self):
        w = self.window
        self.assertFalse(any("AI " in b.text() or "Assistant" in b.text() for b in w.findChildren(QPushButton)))
        self.assertFalse(any(t.tabText(i) == "Structural Assistant" for t in w.findChildren(QTabWidget) for i in range(t.count())))
        self.assertFalse(hasattr(w, "start_structural_assistant_request"))
        self.solve()
        dossier = w.calculation_design_dossier()
        self.assertEqual(dossier["schema"], "portalcalc.calculation.dossier.v1")
        self.assertTrue(dossier["member_checks"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
