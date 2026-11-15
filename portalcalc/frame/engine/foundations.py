"""Pier footing design from base reactions, and the costing roll-up."""

import copy
import math

from portalcalc import costing
from portalcalc.frame.common import COSTING_DEFAULTS, STANDARD_PIER_DIAMETERS_MM, UB_STOCK_LENGTHS_M


class FoundationsCostingMixin:
    """Foundation design and cost summary."""

    def steel_weight_kg(self, area_mm2, length_mm):
        return area_mm2 * length_mm * 1e-9 * 7850.0

    def stock_length_m_for_cut(self, cut_length_m):
        if cut_length_m <= 0.0:
            return 0.0
        for stock_length in UB_STOCK_LENGTHS_M:
            if cut_length_m <= stock_length + 1e-9:
                return stock_length
        pieces = math.ceil(cut_length_m / UB_STOCK_LENGTHS_M[-1])
        return pieces * UB_STOCK_LENGTHS_M[-1]

    def section_cost_bucket(self, profile):
        return costing.section_cost_bucket(profile)

    def costing_physical_cut_lengths_m(self, structure):
        return costing.costing_physical_cut_lengths_m(structure)

    def costing_rate_from_input(self, widget, default_key):
        try:
            return max(float(widget.text()), 0.0)
        except ValueError:
            return float(COSTING_DEFAULTS[default_key])

    def foundation_float_from_input(self, widget, default):
        try:
            return max(float(widget.text()), 0.0)
        except ValueError:
            return default

    def design_pier_footing(self, compression_kn, uplift_kn, bearing_kpa, skin_friction_kpa, use_skin_friction_for_bearing=False):
        compression_kn = max(compression_kn, 0.0)
        uplift_kn = max(uplift_kn, 0.0)
        depth_step_m = 0.25
        for diameter_mm in STANDARD_PIER_DIAMETERS_MM:
            diameter_m = diameter_mm / 1000.0
            base_area_m2 = math.pi * diameter_m**2 / 4.0
            perimeter_m = math.pi * diameter_m
            depth_m = 1.0
            while depth_m <= 4.0 + 1e-9:
                skin_capacity_kn = skin_friction_kpa * perimeter_m * max(depth_m - 1.0, 0.0)
                bearing_skin_capacity_kn = skin_capacity_kn if use_skin_friction_for_bearing else 0.0
                bearing_capacity_kn = bearing_kpa * base_area_m2 + bearing_skin_capacity_kn
                if bearing_capacity_kn + 1e-9 < compression_kn:
                    depth_m += depth_step_m
                    continue
                skin_capacity_kn = skin_friction_kpa * perimeter_m * max(depth_m - 1.0, 0.0)
                if skin_capacity_kn + 1e-9 >= uplift_kn:
                    return {
                        "diameter_mm": diameter_mm,
                        "depth_m": depth_m,
                        "bearing_capacity_kn": bearing_capacity_kn,
                        "base_bearing_capacity_kn": bearing_kpa * base_area_m2,
                        "bearing_skin_capacity_kn": bearing_skin_capacity_kn,
                        "skin_capacity_kn": skin_capacity_kn,
                        "use_skin_friction_for_bearing": use_skin_friction_for_bearing,
                        "status": "OK",
                    }
                depth_m += depth_step_m

        diameter_mm = STANDARD_PIER_DIAMETERS_MM[-1]
        diameter_m = diameter_mm / 1000.0
        base_area_m2 = math.pi * diameter_m**2 / 4.0
        perimeter_m = math.pi * diameter_m
        required_skin_depth_m = 1.0
        if skin_friction_kpa * perimeter_m > 1e-9:
            required_skin_depth_m = 1.0 + uplift_kn / (skin_friction_kpa * perimeter_m)
        required_bearing_diameter_m = 0.0
        if bearing_kpa > 1e-9:
            required_bearing_diameter_m = math.sqrt(4.0 * compression_kn / (math.pi * bearing_kpa))
        depth_m = max(4.0, math.ceil(required_skin_depth_m / depth_step_m) * depth_step_m)
        skin_capacity_kn = skin_friction_kpa * perimeter_m * max(depth_m - 1.0, 0.0)
        bearing_skin_capacity_kn = skin_capacity_kn if use_skin_friction_for_bearing else 0.0
        return {
            "diameter_mm": diameter_mm,
            "depth_m": depth_m,
            "bearing_capacity_kn": bearing_kpa * base_area_m2 + bearing_skin_capacity_kn,
            "base_bearing_capacity_kn": bearing_kpa * base_area_m2,
            "bearing_skin_capacity_kn": bearing_skin_capacity_kn,
            "skin_capacity_kn": skin_capacity_kn,
            "required_bearing_diameter_mm": required_bearing_diameter_m * 1000.0,
            "use_skin_friction_for_bearing": use_skin_friction_for_bearing,
            "status": "CHECK" if required_bearing_diameter_m * 1000.0 > diameter_mm + 1e-9 else "OK >4m",
        }

    def foundation_design_from_envelopes(self, reaction_envelopes):
        bearing_kpa = self.foundation_float_from_input(self.foundation_bearing_input, 150.0)
        skin_friction_kpa = self.foundation_float_from_input(self.foundation_skin_friction_input, 15.0)
        use_skin_friction_for_bearing = self.foundation_skin_bearing_checkbox.isChecked()
        items = []
        for envelope in reaction_envelopes:
            design = self.design_pier_footing(
                envelope.get("compression_kn", 0.0),
                envelope.get("uplift_kn", 0.0),
                bearing_kpa,
                skin_friction_kpa,
                use_skin_friction_for_bearing,
            )
            items.append({**envelope, **design})
        return {
            "bearing_kpa": bearing_kpa,
            "skin_friction_kpa": skin_friction_kpa,
            "use_skin_friction_for_bearing": use_skin_friction_for_bearing,
            "diameters_mm": STANDARD_PIER_DIAMETERS_MM,
            "items": items,
        }

    def costing_from_structure(self, structure, building_length_m, bay_size_m):
        costing_structure = copy.copy(structure)
        costing_structure.elements = [copy.copy(element) for element in structure.elements]
        self.apply_auto_web_assignments_to_structure(costing_structure)
        return costing.costing_from_structure(self, costing_structure, building_length_m, bay_size_m)
