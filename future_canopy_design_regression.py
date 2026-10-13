"""Checks for physical section/reinforcement envelopes and frozen reloads."""
import copy
import unittest
from types import SimpleNamespace

from future_canopy_design import apply_retained_zones, choose_profile, geometry_basis, merge


class Combo:
    def __init__(self, names, selected=None):
        self.names = names
        self.selected = selected or names[0]
    def count(self):
        return len(self.names)
    def itemText(self, index):
        return self.names[index]
    def setCurrentText(self, name):
        self.selected = name


def profile(name, area, inertia, weak=None):
    return SimpleNamespace(name=name, A=area, I_analysis=inertia, Iyp=inertia,
                           Izp=weak or inertia, fy=300, Syp=inertia / 100, Szp=(weak or inertia) / 100)


class EnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {p.name: p for p in [profile("small", 1, 1), profile("large", 2, 2),
                                           profile("thin-stiff", 1.5, 3, 0.5), profile("envelope", 2.5, 3)]}
        self.window = SimpleNamespace(catalog=self.catalog, auto_size_profile_cost_key=lambda p: p.A)
        self.window.set_combo_text_if_present = lambda combo, name: combo.setCurrentText(name)

    def test_section_choice_is_not_just_the_heaviest_or_highest_inertia(self):
        combo = Combo(list(self.catalog))
        self.assertEqual(choose_profile(self.window, combo, "LEFT_COLUMN", ["large", "thin-stiff"]), "envelope")
        self.assertEqual(choose_profile(self.window, combo, "RIGHT_COLUMN", ["small", "large"]), "large")

    def test_hybrid_keeps_opposite_columns_and_disjoint_doubler_panels(self):
        left, right, web = [Combo(list(self.catalog)) for _ in range(3)]
        self.window.auto_size_group_specs = lambda: [("Left", left, "LEFT_COLUMN"),
                                                     ("Right", right, "RIGHT_COLUMN"), ("Web", web, "WEB")]
        self.window.web_assignment_basis_signature = lambda group: "common-layout"
        self.window.auto_size_capture_state = lambda specs: {
            "web_assignments": copy.deepcopy(self.window.auto_web_profile_assignments),
            "web_joint_doubler_sides": copy.deepcopy(self.window.auto_web_joint_doubler_sides)}
        first = {"sections": {"LEFT_COLUMN": "large", "RIGHT_COLUMN": "small", "WEB": "large"},
                 "state": {"web_assignments": {"WEB": {10: "small", 11: "large"}},
                           "web_joint_doubler_sides": {"WEB": {(10, "top chord")}}},
                 "zones": [{"group": "TOP", "member": 1}, {"group": "BOTTOM", "member": 2}]}
        second = {"sections": {"LEFT_COLUMN": "small", "RIGHT_COLUMN": "large", "WEB": "large"},
                  "state": {"web_assignments": {"WEB": {10: "large", 11: "small"}},
                            "web_joint_doubler_sides": {"WEB": {(11, "bottom chord")}}},
                  "zones": [{"group": "TOP", "member": 3}, {"group": "BOTTOM", "member": 2}]}
        original = copy.deepcopy(first)
        hybrid = merge(self.window, first, second)
        self.assertEqual((left.selected, right.selected), ("large", "large"))
        self.assertEqual(hybrid["state"]["web_assignments"]["WEB"], {10: "large", 11: "large"})
        self.assertEqual(len(hybrid["state"]["web_joint_doubler_sides"]["WEB"]), 2)
        self.assertEqual({(z["group"], z["member"]) for z in hybrid["zones"]}, {("TOP", 1), ("BOTTOM", 2), ("TOP", 3)})
        self.assertEqual(first, original)

    def test_retained_doublers_keep_cost_without_reducing_state_demands(self):
        field = lambda value: SimpleNamespace(text=lambda: value)
        combo = lambda value: SimpleNamespace(currentText=lambda: value)
        window = SimpleNamespace(span_input=field("21"), eave_input=field("6"), pitch_input=field("7.5"),
                                 depth_input=field("1000"), roof_shape_combo=combo("Gable"),
                                 frame_system_combo=combo("Truss"), internal_column_count_combo=combo("None"),
                                 max_truss_depth_input=field("1050"))
        node = lambda x, y: SimpleNamespace(x=x, y=y)
        element = SimpleNamespace(group="TOP", id=1, start=node(0, 0), end=node(1000, 0), length=lambda: 1000)
        window.structure = SimpleNamespace(elements=[element])
        window.design_envelope_actions = {"TOP": {"compression_kn": {"value": 100}}, "doubled_chord_zones": []}
        window.future_canopy_design = {"geometry_basis": geometry_basis(window),
                                       "doubled_chord_members": [["TOP", 1], ["RIGHT_CANOPY_TOP", 99]]}
        apply_retained_zones(window)
        self.assertEqual(window.design_envelope_actions["TOP"]["compression_kn"]["value"], 100)
        self.assertEqual(len(window.design_envelope_actions["doubled_chord_zones"]), 1)
        apply_retained_zones(window)
        self.assertEqual(len(window.design_envelope_actions["doubled_chord_zones"]), 1)
        window.future_canopy_design["doubled_chord_members"].append(["TOP", 2])
        with self.assertRaises(ValueError):
            apply_retained_zones(window)


if __name__ == "__main__":
    unittest.main()
