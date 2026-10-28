"""Geometry, constraint persistence and actual Qt interaction checks."""
import copy
import json
import os
import tempfile
import unittest
from unittest import mock

from bracing_layout import BracingLayout, LayoutGeometry, layout_panels


def geometry(**values):
    return LayoutGeometry.create(**{"span_mm": 21000, "length_mm": 40000, "eave_mm": 6000,
                                    "pitch_deg": 7.5, "roof_shape": "Gable", **values})


class LayoutModelTests(unittest.TestCase):
    def test_panels_cover_each_face_and_actual_last_bay(self):
        g = geometry(length_mm=42000)
        panels = layout_panels(g)
        self.assertEqual(g.bay_stations_mm, (0, 8000, 16000, 24000, 32000, 40000, 42000))
        self.assertEqual(len(panels), 6 * (2 + 4) + 6)
        self.assertEqual(len({p.id for p in panels}), len(panels))
        last_wall = next(p for p in panels if p.id == "left_wall/bay/5")
        self.assertEqual({v[1] for v in last_wall.vertices}, {40000, 42000})
        self.assertEqual({p.face for p in panels}, {"left_wall", "right_wall", "roof_left", "roof_right", "front_end", "back_end"})

    def test_end_panel_across_ridge_has_real_mullion_brace_endpoints(self):
        panel = next(p for p in layout_panels(geometry()) if p.id == "front_end/panel/1")
        self.assertEqual(len(panel.vertices), 5)
        self.assertIn(10500, {v[0] for v in panel.vertices})
        for diagonal in panel.diagonals:
            self.assertEqual({p[0] for p in diagonal}, {7000, 14000})

    def test_unequal_bolt_bays_and_skillion(self):
        g = geometry(roof_shape="Skillion", bay_stations_mm=[0, 6000, 15000, 27000, 40000],
                     end_wall_stations_mm=[0, 5000, 13000, 21000])
        restored = LayoutGeometry.from_dict(json.loads(json.dumps(g.to_dict())))
        self.assertEqual(g, restored)
        self.assertNotIn("roof_left", {p.face for p in layout_panels(g)})
        self.assertEqual(len([p for p in layout_panels(g) if p.face == "roof"]), 12)
        self.assertEqual(g.roof_stations, g.end_wall_stations_mm)

    def test_exclusions_are_hard_constraints_and_pins_cannot_override_them(self):
        model = BracingLayout(geometry())
        identifier = "left_wall/bay/1"
        self.assertIn(identifier, model.available)
        model.toggle(identifier)
        self.assertNotIn(identifier, model.available)
        with self.assertRaisesRegex(ValueError, "Unblock"):
            model.toggle(identifier, "pinned")
        model.toggle(identifier)
        model.toggle(identifier, "pinned")
        self.assertIn(identifier, model.pinned)
        model.toggle(identifier)
        self.assertIn(identifier, model.blocked)
        self.assertNotIn(identifier, model.pinned)
        self.assertEqual(BracingLayout.from_dict(json.loads(json.dumps(model.to_dict()))).to_dict(), model.to_dict())

    def test_bad_external_payloads_rejected(self):
        for field, value in (("span_mm", float("nan")), ("eave_mm", True), ("length_mm", -1),
                             ("pitch_deg", 90), ("bay_mm", 0), ("bay_stations_mm", "040000"),
                             ("bay_stations_mm", [0, 8000, 8000, 40000]),
                             ("end_wall_stations_mm", [0, 10000])):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                geometry(**{field: value})
        saved = BracingLayout(geometry()).to_dict()
        for patch in ({"schema": "unsupported"}, {"blocked_panels": ["nonexistent"]},
                      {"blocked_panels": ["left_wall/bay/0"], "pinned_panels": ["left_wall/bay/0"]}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                BracingLayout.from_dict({**saved, **patch})


class LayoutUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls.temp = tempfile.TemporaryDirectory(prefix="portalcalc_bracing_layout_")
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

    def test_real_canvas_click_filtered_face_and_undo(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from bracing_layout_ui import BracingLayoutDialog
        dialog = BracingLayoutDialog(geometry())
        try:
            dialog.show()
            dialog.face_filter.setCurrentIndex(dialog.face_filter.findData("left_wall"))
            self.app.processEvents()
            target = dialog.model.panels["left_wall/bay/1"]
            centre = tuple(sum(p[axis] for p in target.vertices) / 4 for axis in range(3))
            click = dialog.canvas.projection()(centre)[0]
            self.assertEqual(dialog.canvas.panel_at(click), target.id)
            QTest.mouseClick(dialog.canvas, Qt.LeftButton, pos=click.toPoint())
            self.assertEqual(dialog.model.blocked, {target.id})
            dialog.undo_button.click()
            self.assertFalse(dialog.model.blocked)
            dialog.redo_button.click()
            self.assertEqual(dialog.model.blocked, {target.id})
            dialog.edit_mode.setCurrentIndex(1)
            dialog.toggle_panel(target.id)
            self.assertFalse(dialog.model.pinned)
            self.assertIn("Unblock", dialog.location_label.text())
            dialog.view_combo.setCurrentText("Back right")
            self.assertEqual(dialog.canvas.azimuth, 45)
        finally:
            dialog.reject()

    def test_stale_geometry_reset_is_explicit_and_reversible(self):
        from bracing_layout_ui import BracingLayoutDialog
        old = BracingLayout(geometry(), blocked=["front_end/panel/0"])
        saved = old.to_dict()
        dialog = BracingLayoutDialog(geometry(length_mm=48000), saved)
        try:
            self.assertFalse(dialog.save_button.isEnabled())
            self.assertIn("dimensions have changed", dialog.geometry_note.text())
            self.assertEqual(dialog.snapshot(), saved)
            dialog.reset_geometry_button.click()
            self.assertFalse(dialog.model.blocked)
            self.assertTrue(dialog.save_button.isEnabled())
            dialog.undo_button.click()
            self.assertEqual(dialog.snapshot(), saved)
            self.assertFalse(dialog.save_button.isEnabled())
            self.assertEqual(saved, old.to_dict())
        finally:
            dialog.reject()

    def test_cancel_save_preset_roundtrip_and_legacy_reset(self):
        from PySide6.QtWidgets import QDialog
        w = self.window
        def edit(dialog):
            dialog.toggle_panel("left_wall/bay/1")
            return QDialog.Rejected
        with mock.patch("bracing_layout_ui.BracingLayoutDialog.exec", edit):
            w.edit_bracing_locations()
        self.assertIsNone(w.bracing_location_layout)
        self.assertNotIn("layout", w.collect_preset_data()["bracing"])
        def save(dialog):
            dialog.toggle_panel("left_wall/bay/1")
            dialog.edit_mode.setCurrentIndex(1)
            dialog.toggle_panel("front_end/panel/0")
            return QDialog.Accepted
        with mock.patch("bracing_layout_ui.BracingLayoutDialog.exec", save):
            w.edit_bracing_locations()
        saved = json.loads(json.dumps(w.collect_preset_data()))
        self.assertEqual(saved["bracing"]["layout"]["blocked_panels"], ["left_wall/bay/1"])
        self.assertIn("1 blocked", w.bracing_layout_summary_label.text())
        w.apply_preset_data(copy.deepcopy(self.defaults))
        self.assertIsNone(w.bracing_location_layout)
        w.apply_preset_data(saved)
        self.assertEqual(w.bracing_location_layout, saved["bracing"]["layout"])
        self.assertNotIn("Dimensions changed", w.bracing_layout_summary_label.text())
        w.building_length_input.setText("48")
        w.update_bracing_layout_summary()
        self.assertIn("Dimensions changed", w.bracing_layout_summary_label.text())
        self.assertEqual(w.bracing_location_layout, saved["bracing"]["layout"])

    def test_read_only_review_cannot_change_locations(self):
        from bracing_layout_ui import BracingLayoutDialog
        dialog = BracingLayoutDialog(geometry(), read_only=True)
        try:
            before = dialog.snapshot()
            dialog.toggle_panel("front_end/panel/0")
            dialog.use_current_geometry()
            self.assertEqual(dialog.snapshot(), before)
            self.assertFalse(dialog.save_button.isEnabled())
            self.assertFalse(dialog.edit_mode.isEnabled())
        finally:
            dialog.reject()

    def test_location_planning_does_not_grant_restraint_or_change_phase1(self):
        from PySide6.QtWidgets import QDialog
        w = self.window
        inputs = copy.deepcopy(self.defaults)
        inputs["bracing"]["enabled"] = True
        inputs["geometry"]["end_wall_bracing"] = "Both ends"
        w.apply_preset_data(inputs)
        w._suppress_auto_web_split_on_solve = True
        w.generate_and_solve()
        before = w.displacements.copy()
        spring = w.active_eave_x_spring_kn_mm
        design = copy.deepcopy(w.longitudinal_bracing_design)
        self.assertGreater(spring, 0)
        def save(dialog):
            for panel in dialog.model.panels.values():
                if panel.face in {"front_end", "back_end"}:
                    dialog.toggle_panel(panel.id)
            return QDialog.Accepted
        with mock.patch("bracing_layout_ui.BracingLayoutDialog.exec", save), mock.patch.object(w, "generate_and_solve", side_effect=AssertionError("Editing must not solve")):
            w.edit_bracing_locations()
        self.assertEqual(w.active_eave_x_spring_kn_mm, spring)
        self.assertEqual(w.longitudinal_bracing_design, design)
        self.assertTrue((w.displacements == before).all())
        w.generate_and_solve()
        self.assertEqual(w.active_eave_x_spring_kn_mm, spring)
        self.assertEqual(w.longitudinal_bracing_design, design)


if __name__ == "__main__":
    unittest.main(verbosity=2)
