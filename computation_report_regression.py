"""Real-solver checks for report provenance, case coverage and PDF integrity."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"

from PySide6.QtWidgets import QApplication
from pypdf import PdfReader
from main import MainWindow
from computation_report import export_computation_pdf
from computation_report_data import input_digest


class ComputationReportRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="portalcalc_report_test_")
        os.environ["APPDATA"] = cls.temp.name
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False
        cls.window.generate_and_solve()
        cls.before_inputs = copy.deepcopy(cls.window.collect_preset_data())
        cls.before_d = cls.window.displacements.copy()
        cls.before_f = cls.window.force_vector.copy()
        cls.before_ratio = cls.window.auto_size_overall_ratio(cls.window.active_auto_size_group_specs())
        cls.data = cls.window.capture_computation_report({"project": "Report regression", "revision": "Test"})

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.temp.cleanup()

    def test_exact_case_evidence_and_unchanged_design(self):
        w = self.window
        self.assertEqual(w.collect_preset_data(), self.before_inputs)
        self.assertEqual(input_digest(w.collect_preset_data()), self.data["input_digest"])
        changed = copy.deepcopy(self.before_inputs)
        changed["display"]["second_order_analysis"] = not changed["display"]["second_order_analysis"]
        self.assertNotEqual(input_digest(changed), self.data["input_digest"])
        self.assertIn("analysis_settings", self.data["inputs"])
        np.testing.assert_allclose(w.displacements, self.before_d, rtol=1e-9, atol=1e-9)
        np.testing.assert_allclose(w.force_vector, self.before_f, rtol=1e-12, atol=1e-10)
        current = w.auto_size_overall_ratio(w.active_auto_size_group_specs())
        self.assertAlmostEqual(current[0], self.before_ratio[0], places=9)
        self.assertEqual(current[1:], self.before_ratio[1:])
        self.assertEqual(len(self.data["cases"]), len(w.load_combinations))
        case = next(c for c in self.data["cases"] if c["name"] == w.load_combination_combo.currentText())
        np.testing.assert_allclose([n["ux_mm"] for n in case["nodes"]], [w.displacements[n.ux] for n in w.structure.nodes], atol=1e-9)
        self.assertAlmostEqual(sum(n["fy_kn"] for n in case["nodes"]), float(np.sum(w.force_vector[1::3])) / 1000, places=8)
        self.assertAlmostEqual(sum(n["fx_kn"] for n in case["nodes"]), float(np.sum(w.force_vector[0::3])) / 1000, places=8)
        for record in case["members"]:
            element = next(e for e in w.structure.elements if e.id == record["member"])
            summary = w.structure.element_force_summary(element, w.displacements)
            for key in ("compression_kn", "tension_kn", "shear_kn", "moment_knm"):
                self.assertAlmostEqual(summary[key], record[key], places=8)
        self.assertFalse(w._capture_computation_report)
        self.assertFalse(w._preserve_web_assignments_on_solve)

    def test_storage_patterns_second_order_and_connected_roof_basis(self):
        window = MainWindow()
        window.confirm_on_close = False
        try:
            inputs = copy.deepcopy(self.before_inputs)
            inputs["geometry"].update(frame_system="Rafter", roof_shape="Skillion",
                                      internal_column_count="Custom positions", internal_column_positions_m="5, 10, 15",
                                      internal_column_connection="Moment")
            inputs["sections"]["top_chord"] = inputs["sections"]["left_column"]
            inputs["sections"]["left_canopy_top_chord"] = inputs["sections"]["left_column"]
            inputs["canopies"].update(left_type="Lean-to", left_length_mm="3000", left_frame_system="Rafter",
                                       left_depth_mm="600", left_eave_height_mm="5000")
            inputs["storage_loads"].update(enabled=True)
            inputs["display"]["second_order_analysis"] = True
            window.apply_preset_data(inputs)
            data = window.capture_computation_report({"project": "Connected roof and storage"})
            self.assertEqual(len(data["cases"]), len(window.load_combinations) * 4)
            for case in data["cases"]:
                self.assertIn(case["name"].rsplit("[", 1)[-1].rstrip("]"), {"No storage", "Storage left", "Storage right", "Storage both"})
                self.assertEqual(case["analysis"]["type"], "second_order")
                self.assertTrue(case["load_lines"])
                self.assertTrue(all(math.isfinite(n["ux_mm"]) for n in case["nodes"]))
            self.assertTrue(any(r["group"] == "INTERNAL_COLUMN" for r in data["members"]))
            # The equivalent connected skillion wind width exceeds the main span.
            self.assertGreater(data["wind"][0]["inputs"]["building_width_m"], float(inputs["geometry"]["span_mm"]) / 1000)
            self.assertTrue(data["wind"][0]["continuous_roof_basis"])
        finally:
            window.close()

    def test_web_design_profiles_and_governing_sources(self):
        w = self.window
        case_names = {c["name"] for c in self.data["cases"]}
        self.assertTrue(self.data["webs"])
        for web in self.data["webs"]:
            demand = w.design_envelope_actions["web_member_demands"][web["group"]][web["member"]]
            self.assertEqual(web["compression_kn"], demand["compression_kn"])
            self.assertEqual(web["tension_kn"], demand["tension_kn"])
            self.assertIn(web["compression_combo"], case_names)
            self.assertIn(web["tension_combo"], case_names)
            assigned = w.auto_web_profile_assignments.get(web["group"], {}).get(web["member"])
            if assigned:
                self.assertEqual(w.catalog.get(assigned).name, web["profile"])
            for connection in web["connections"]:
                self.assertIn(connection["combo"], case_names)
                self.assertIn("context", connection)
                if connection["label"] == "Weld throat":
                    self.assertTrue(connection["working"])
        # Capture is a frozen result, unaffected by later edits to the UI.
        previous = w.span_input.text()
        try:
            w.span_input.setText("99")
            self.assertNotEqual(input_digest(w.collect_preset_data()), self.data["input_digest"])
            self.assertEqual(self.data["inputs"]["geometry"]["span_mm"], self.before_inputs["geometry"]["span_mm"])
        finally:
            w.span_input.setText(previous)

    def test_pdf_bookmarks_content_and_failure_status(self):
        path = Path(self.temp.name) / "computations.pdf"
        export_computation_pdf(self.data, path)
        pdf = PdfReader(path)
        self.assertGreater(len(pdf.pages), 20)
        text = "\n".join(p.extract_text() for p in pdf.pages)
        for title in ("Complete input register", "Wind load calculations", "Gravity, imposed", "Frame model and analysis outputs", "Governing section checks", "Web members and connection checks", "Governing-case analysis records", "Detailed design-check record"):
            self.assertIn(title, text)
        self.assertIn("0.8 x 0.6 x", text)
        self.assertIn("Envelope interaction", text)
        self.assertIn("9.81 m/s2", text)
        self.assertTrue(pdf.outline)
        if self.data["overall"]["ratio"] > 1:
            self.assertIn("CHECKS EXCEED REPORTED LIMIT", pdf.pages[0].extract_text())
        # Repeated table headers and numbered footers should survive pagination.
        for i, page in enumerate(pdf.pages, 1):
            self.assertIn(f"Page {i}", page.extract_text())
            self.assertEqual(float(page.mediabox.width), 595.2756)

    def test_full_case_tables_and_atomic_output(self):
        data = copy.deepcopy(self.data)
        data["cases"] = data["cases"][:1]
        path = Path(self.temp.name) / "detailed.pdf"
        export_computation_pdf(data, path, include_case_details=True)
        text = "\n".join(p.extract_text() for p in PdfReader(path).pages)
        self.assertIn("Nodal displacement and applied-load schedule", text)
        self.assertIn("Local nodal end forces", text)
        original = path.read_bytes()
        with mock.patch("computation_report.ComputationDoc.multiBuild", side_effect=RuntimeError("test layout error")):
            with self.assertRaisesRegex(RuntimeError, "test layout error"):
                export_computation_pdf(data, path)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(list(path.parent.glob(".portalcalc-computations-*.pdf")))

    def test_capture_failure_restores_flags(self):
        w = self.window
        original = w.span_input.text()
        try:
            w.span_input.setText("bad span")
            with self.assertRaises(ValueError):
                w.capture_computation_report()
            self.assertFalse(w._capture_computation_report)
            self.assertFalse(w._preserve_web_assignments_on_solve)
            self.assertFalse(w._suppress_auto_web_split_on_solve)
        finally:
            w.span_input.setText(original)
            w.generate_and_solve()


if __name__ == "__main__":
    unittest.main(verbosity=2)
