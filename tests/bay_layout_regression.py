"""Bay list input (bay_layout) regression.

BayListTests are Qt-free: parsing, formatting, the legacy length + bay size
conversion and the building model built from a bay list. DesktopTests load
presets into an offscreen MainWindow: legacy presets convert to the same
gridlines, the bay list round-trips through a saved preset, and the derived
length, design bay, 3D layout stations and costing frame count follow it.
"""
import _bootstrap  # noqa: F401  (puts the repository root on sys.path)
from portalcalc.resources import data_path
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from portalcalc import bay_layout
from portalcalc.bay_layout import BayListError, bays_from_length, format_bays, parse_bays
from portalcalc.building.model import building_model_from_preset

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = json.loads(data_path("presets/factory_defaults.json").read_text(encoding="utf-8"))


class BayListTests(unittest.TestCase):
    def test_parse_forms(self):
        self.assertEqual(parse_bays("5*8000"), [8000.0] * 5)
        self.assertEqual(parse_bays("3*8000, 2*7500, 8000"), [8000.0] * 3 + [7500.0] * 2 + [8000.0])
        self.assertEqual(parse_bays("3*8000 2*7500 8000"), parse_bays("3*8000,2*7500,8000"))
        self.assertEqual(parse_bays(" 3 * 8000 ; 7000 "), [8000.0] * 3 + [7000.0])
        self.assertEqual(parse_bays("2x7500"), [7500.0, 7500.0])
        self.assertEqual(parse_bays("7999.5"), [7999.5])

    def test_parse_errors_explain_the_format(self):
        for text in ("", "8", "abc", "3*", "*8000", "0*8000", "8000,,-5", "3*8000 2**7500"):
            with self.subTest(text=text), self.assertRaises(BayListError) as caught:
                parse_bays(text)
            self.assertIn("e.g.", str(caught.exception) + " e.g.")
        with self.assertRaisesRegex(BayListError, "mm"):
            parse_bays("8, 8, 8")  # metres by mistake
        with self.assertRaisesRegex(BayListError, "500 bays"):
            parse_bays("501*8000")

    def test_format_groups_runs(self):
        self.assertEqual(format_bays([8000.0] * 7 + [7000.0]), "7*8000, 7000")
        self.assertEqual(format_bays([8000, 8000, 7500, 7500, 8000]), "2*8000, 2*7500, 8000")
        self.assertEqual(format_bays([7999.5]), "7999.5")
        for text in ("7*8000, 7000", "2*8000, 2*7500, 8000", "6000"):
            self.assertEqual(format_bays(parse_bays(text)), text)

    def test_legacy_length_and_bay_size(self):
        self.assertEqual(bays_from_length(63000, 8000), [8000.0] * 7 + [7000.0])
        self.assertEqual(bays_from_length(40000, 8000), [8000.0] * 5)
        # A floating-point whole number of bays (hay shed search) stays equal.
        import math
        self.assertEqual(bays_from_length(math.nextafter(5 * 7500.0, 0.0), 7500.0), [7500.0] * 5)
        self.assertEqual(bay_layout.stations([8000.0] * 7 + [7000.0])[-2:], [56000.0, 63000.0])

    def test_summary(self):
        self.assertEqual(bay_layout.summary([8000.0] * 5), "40 m long · 5 bays · design bay 8 m")
        self.assertEqual(bay_layout.summary([8000.0] * 7 + [7000.0]),
                         "63 m long · 8 bays · design bay 8 m (longest)")

    def test_building_model_uses_the_bays(self):
        preset = copy.deepcopy(DEFAULTS)
        preset["geometry"]["bays_mm"] = "2*8000, 2*6000, 7000"
        model = building_model_from_preset(preset)
        self.assertEqual([g.y_mm for g in model.gridlines], [0.0, 8000.0, 16000.0, 22000.0, 28000.0, 35000.0])
        self.assertEqual(model.length_mm, 35000.0)
        frame = model.frame_preset(model.gridlines[2].id)
        self.assertEqual(frame["geometry"]["bays_mm"], "2*8000, 2*6000, 7000")
        self.assertEqual(float(frame["wind"]["building_length_m"]), 35.0)
        self.assertEqual(float(frame["loads"]["bay_size_mm"]), 8000.0)
        self.assertNotIn("bays_mm", model.frame_types[0].geometry)

    def test_legacy_preset_model_unchanged(self):
        preset = copy.deepcopy(DEFAULTS)
        preset["geometry"].pop("bays_mm", None)
        preset["wind"]["building_length_m"] = "63"
        preset["loads"]["bay_size_mm"] = "8000"
        legacy = building_model_from_preset(preset)
        preset["geometry"]["bays_mm"] = "7*8000, 7000"
        listed = building_model_from_preset(preset)
        self.assertEqual([g.y_mm for g in legacy.gridlines], [g.y_mm for g in listed.gridlines])


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        cls._appdata = tempfile.TemporaryDirectory(prefix="portalcalc_bays_")
        os.environ["APPDATA"] = cls._appdata.name
        from PySide6.QtWidgets import QApplication
        from portalcalc.desktop.main_window import MainWindow
        cls.app = QApplication.instance() or QApplication([])
        cls.window = MainWindow()
        cls.window.confirm_on_close = False

    @classmethod
    def tearDownClass(cls):
        cls.window.close()

    def legacy(self, length_m, bay_mm):
        preset = copy.deepcopy(DEFAULTS)
        preset["geometry"].pop("bays_mm", None)
        preset["wind"]["building_length_m"] = str(length_m)
        preset["loads"]["bay_size_mm"] = str(bay_mm)
        preset["loads"]["bay_size_m"] = bay_mm / 1000.0
        return preset

    def test_legacy_preset_converts_and_keeps_its_bracing_grid(self):
        w = self.window
        preset = self.legacy(63, 8000)
        w.apply_preset_data(preset)
        self.assertEqual(w.bays_input.text(), "7*8000, 7000")
        self.assertEqual(w.bay_lengths_mm(), [8000.0] * 7 + [7000.0])
        self.assertEqual(float(w.building_length_input.text()), 63.0)
        self.assertEqual(w.bay_size_m_from_ui(), 8.0)
        g = w.current_bracing_layout_geometry()
        self.assertEqual(list(g.bay_stations_mm), [0.0, 8000.0, 16000.0, 24000.0, 32000.0, 40000.0,
                                                   48000.0, 56000.0, 63000.0])
        saved = w.collect_preset_data()
        self.assertEqual(saved["geometry"]["bays_mm"], "7*8000, 7000")
        self.assertEqual(float(saved["wind"]["building_length_m"]), 63.0)
        self.assertEqual(float(saved["loads"]["bay_size_mm"]), 8000.0)

    def test_bay_list_round_trips_and_drives_derived_values(self):
        w = self.window
        preset = copy.deepcopy(DEFAULTS)
        preset["geometry"]["bays_mm"] = "3*8000, 2*7500, 6000"
        w.apply_preset_data(preset)
        self.assertEqual(w.bays_input.text(), "3*8000, 2*7500, 6000")
        self.assertEqual(float(w.building_length_input.text()), 45.0)
        self.assertEqual(w.bay_size_m_from_ui(), 8.0)  # design bay = longest
        self.assertIn("6 bays", w.bays_summary_label.text())
        self.assertEqual(list(w.current_bracing_layout_geometry().bay_stations_mm)[-3:], [31500.0, 39000.0, 45000.0])
        w.generate_and_solve()
        self.assertEqual(w.costing_summary["bay_size_m"], 8.0)
        # ceil(45 / 8) = 6 happens to agree; costing counts the entered bays.
        self.assertEqual((w.costing_summary["bay_count"], w.costing_summary["portal_count"]), (6, 7))
        w.set_bays([8000.0, 4000.0, 4000.0, 8000.0])  # ceil(24 / 8) = 3 would miss a frame
        w.generate_and_solve()
        self.assertEqual((w.costing_summary["bay_count"], w.costing_summary["portal_count"]), (4, 5))
        w.set_bays(parse_bays("3*8000, 2*7500, 6000"))
        again = w.collect_preset_data()
        self.assertEqual(again["geometry"]["bays_mm"], "3*8000, 2*7500, 6000")
        w.apply_preset_data(self.legacy(40, 8000))
        w.apply_preset_data(again)
        self.assertEqual(w.bay_lengths_mm(), [8000.0] * 3 + [7500.0] * 2 + [6000.0])

    def test_typed_bays_apply_and_bad_text_keeps_the_last_valid_list(self):
        w = self.window
        w.apply_preset_data(copy.deepcopy(DEFAULTS))
        w.bays_input.setText("4*7000 6500")
        w.on_bays_edited()
        self.assertEqual(w.bay_lengths_mm(), [7000.0] * 4 + [6500.0])
        self.assertEqual(float(w.building_length_input.text()), 34.5)
        self.assertEqual(w.crane_beam_span_input.text(), "7")
        w.bays_input.setText("4*7 6.5")
        w.on_bays_edited()
        self.assertEqual(w.bay_lengths_mm(), [7000.0] * 4 + [6500.0])
        self.assertIn("Still using 4*7000, 6500", w.bays_summary_label.text())
        self.assertEqual(w.collect_preset_data()["geometry"]["bays_mm"], "4*7000, 6500")


if __name__ == "__main__":
    unittest.main()
