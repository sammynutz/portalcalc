"""Reading and normalising the design inputs (geometry, bays, loads, wind, snow)."""

import math
import re

from portalcalc import bay_layout
from portalcalc.frame.common import (
    CANOPY_CHORD_DEFAULT_FRAGMENTS,
    LEAN_TO_COLUMN_DEFAULT_FRAGMENTS,
    MAIN_TRUSS_DEPTH_HEIGHT_BONUS_RATIO,
    MAIN_TRUSS_DEPTH_HEIGHT_BONUS_THRESHOLD_MM,
    MAIN_TRUSS_DEPTH_MAX_RATIO,
    MAIN_TRUSS_DEPTH_START_RATIO,
    mm_from_metres_text,
    SNOW_REGION_K1,
)
from portalcalc.loads import RoofLoadPlacement
from portalcalc.service.configurator_review import review_truss_depth_bounds
from portalcalc.wind import WindInputs


class InputsMixin:
    """Typed values derived from the input fields."""

    def set_default_combo(self, combo, preferred_fragments):
        for fragment in preferred_fragments:
            fragment = fragment.upper()
            for i in range(combo.count()):
                if fragment in combo.itemText(i).upper():
                    combo.setCurrentIndex(i)
                    return

    def apply_builtin_canopy_section_defaults(self):
        for combo in [self.left_canopy_top_profile_combo, self.right_canopy_top_profile_combo]:
            self.set_default_combo(combo, CANOPY_CHORD_DEFAULT_FRAGMENTS)
        for combo in [self.left_lean_column_profile_combo, self.right_lean_column_profile_combo]:
            self.set_default_combo(combo, LEAN_TO_COLUMN_DEFAULT_FRAGMENTS)

    def selected_profile(self, combo):
        return self.catalog.get(combo.currentText())

    def bay_size_m_from_ui(self):
        """The design bay (longest bay), m."""
        return float(self.load_bay_size_input.text())

    def bay_lengths_mm(self):
        """The building's bays front to back, mm (the last valid bay list)."""
        return list(self._bay_lengths_mm)

    def bay_stations_mm(self):
        return bay_layout.stations(self._bay_lengths_mm)

    def set_bays(self, bays, text=None):
        """Adopt a bay list: derive the building length and design bay from it."""
        bays = [float(b) for b in bays]
        self._bay_lengths_mm = bays
        self.bays_input.setText(bay_layout.format_bays(bays) if text is None else text)
        self.building_length_input.setText(f"{bay_layout.length_mm(bays) / 1000.0:.6f}".rstrip("0").rstrip("."))
        self.load_bay_size_input.setText(f"{bay_layout.design_bay_mm(bays) / 1000.0:.6f}".rstrip("0").rstrip("."))
        self.bays_summary_label.setStyleSheet("")
        self.bays_summary_label.setText(bay_layout.summary(bays))

    def on_bays_edited(self):
        try:
            bays = bay_layout.parse_bays(self.bays_input.text())
        except bay_layout.BayListError as error:
            self.bays_summary_label.setStyleSheet("color: #c0392b;")
            self.bays_summary_label.setText(
                f"{error}. Still using {bay_layout.format_bays(self._bay_lengths_mm)}.")
            return
        if bays == self._bay_lengths_mm:
            self.bays_summary_label.setStyleSheet("")
            self.bays_summary_label.setText(bay_layout.summary(bays))
            return
        self.set_bays(bays, text=self.bays_input.text().strip())
        self.sync_crane_beam_span_to_bay_size()
        self.update_bracing_layout_summary()

    def downward_roof_load_from_input(self, widget):
        return -abs(float(widget.text()))

    def numeric_input(self, widget, field_name):
        text = widget.text().strip()
        try:
            return float(text)
        except (TypeError, ValueError) as error:
            shown_value = text if text else "(blank)"
            raise ValueError(f"{field_name} must be a number; entered value: {shown_value!r}.") from error

    def downward_roof_load_text(self, value):
        text = str(value)
        try:
            amount = float(text)
        except (TypeError, ValueError):
            return text
        if amount < 0.0:
            stripped = text.strip()
            return stripped[1:] if stripped.startswith("-") else str(abs(amount))
        return text

    def profile_mass_kn_m(self, profile):
        return profile.A * 0.00785 * 9.81 / 1000.0

    def optional_height_from_input(self, widget):
        text = widget.text().strip()
        if not text:
            return None
        return max(mm_from_metres_text(text), 0.0)

    def side_addition_selected(self, combo):
        return combo.currentText() in {"Canopy", "Lean-to"}

    def canopy_side_is_rafter(self, side):
        combo = self.left_canopy_frame_system_combo if side == "left" else self.right_canopy_frame_system_combo
        return combo.currentText() == "Rafter"

    def frame_type_from_wall_states(self, states=None):
        states = states or self.envelope_plan_selector.wall_states()
        clad_count = sum(1 for clad in states.values() if clad)
        if clad_count == 4:
            return "Enclosed"
        if clad_count == 0:
            return "Roof Only"
        if clad_count == 3:
            return "3 Sided"
        if clad_count == 2:
            return "2 Sided"
        return "1 Sided"

    def wall_states_from_legacy_frame_type(self, frame_type):
        if frame_type == "Enclosed":
            return {"left": True, "right": True, "front": True, "back": True}
        if frame_type in {"Roof Only", "No Wind"}:
            return {"left": False, "right": False, "front": False, "back": False}
        if frame_type == "3 Sided":
            return {"left": True, "right": True, "front": False, "back": True}
        if frame_type == "2 Sided":
            return {"left": True, "right": False, "front": False, "back": True}
        if frame_type == "1 Sided":
            return {"left": True, "right": False, "front": False, "back": False}
        return {"left": True, "right": True, "front": True, "back": True}

    def update_derived_frame_type_label(self):
        if getattr(self, "envelope_plan_selector", None) is not None and hasattr(self.envelope_plan_selector, "sync_inner_walls"):
            self.envelope_plan_selector.sync_inner_walls()
        if getattr(self, "derived_frame_type_label", None) is not None:
            self.derived_frame_type_label.setText(self.frame_type_from_wall_states())

    def round_up_to_increment(self, value, increment):
        if increment <= 0.0:
            return value
        return math.ceil(value / increment) * increment

    def truss_depth_from_span(self, ratio=0.03):
        fixed = getattr(self, "_future_canopy_fixed_main_depth", None)
        if fixed is not None:
            return fixed
        span_mm = mm_from_metres_text(self.span_input.text())
        return self.round_up_to_increment(ratio * span_mm, 50.0)

    def max_main_truss_depth_mm(self):
        fixed = getattr(self, "_future_canopy_fixed_main_depth", None)
        if fixed is not None:
            return fixed
        if getattr(self, "configurator_review", None) is not None:
            maximum_mm = review_truss_depth_bounds(
                self.configurator_review.prepared.inputs
            )[1]
            return math.floor(maximum_mm / 50.0) * 50.0
        span_mm = mm_from_metres_text(self.span_input.text())
        eave_mm = mm_from_metres_text(self.eave_input.text())
        height_bonus = max(eave_mm - MAIN_TRUSS_DEPTH_HEIGHT_BONUS_THRESHOLD_MM, 0.0) * MAIN_TRUSS_DEPTH_HEIGHT_BONUS_RATIO
        automatic_max = self.round_up_to_increment(
            MAIN_TRUSS_DEPTH_MAX_RATIO * span_mm + height_bonus,
            50.0,
        )
        text = getattr(self, "max_truss_depth_input", None).text().strip() if getattr(self, "max_truss_depth_input", None) is not None else ""
        if not text:
            return automatic_max
        try:
            entered_max = float(text)
        except ValueError:
            return automatic_max
        if entered_max <= 0.0:
            return automatic_max
        # A supplied value replaces the automatic search ceiling.  It is still
        # a hard cap, but lets an engineer intentionally investigate a deeper
        # truss than the span/eave heuristic would otherwise permit.
        return entered_max

    def update_truss_depth_from_span(self):
        try:
            depth_mm = self.truss_depth_from_span(MAIN_TRUSS_DEPTH_START_RATIO)
            max_depth_mm = self.max_main_truss_depth_mm()
        except ValueError:
            return
        if depth_mm <= 0.0:
            return
        depth_mm = min(depth_mm, max_depth_mm)
        self.depth_input.setText(f"{depth_mm:.0f}")
        self.generate_and_solve()

    def snow_kp_for_importance(self, importance_level):
        return {
            1: 1.4,
            2: 1.5,
            3: 1.65,
            4: 1.85,
        }.get(int(importance_level), 1.5)

    def snow_loads_from_ui(self):
        region = self.snow_region_combo.currentText()
        if region == "None":
            return {
                "region": region,
                "enabled": False,
                "k1": 0.0,
                "kp": 0.0,
                "mu": 0.0,
                "sg_kpa": 0.0,
                "ultimate_kpa": 0.0,
                "service_kpa": 0.0,
            }
        importance = int(self.importance_combo.currentText())
        h0_m = max(float(self.snow_ahd_height_input.text()), 0.0)
        pitch_deg = float(self.pitch_input.text())
        k1 = SNOW_REGION_K1.get(region, 0.0)
        kp = self.snow_kp_for_importance(importance)
        mu = min(0.7 * (60.0 - pitch_deg) / 50.0, 0.7)
        mu = max(mu, 0.0)
        sg_kpa = max(kp * k1 * ((2.8 * h0_m) / 1000.0 - 1.2), 0.0)
        ultimate_kpa = mu * sg_kpa
        service_kpa = ultimate_kpa / 1.5
        return {
            "region": region,
            "enabled": True,
            "importance": importance,
            "h0_m": h0_m,
            "pitch_deg": pitch_deg,
            "k1": k1,
            "kp": kp,
            "mu": mu,
            "sg_kpa": sg_kpa,
            "ultimate_kpa": ultimate_kpa,
            "service_kpa": service_kpa,
        }

    def roof_load_placement_from_ui(self, prefix):
        return RoofLoadPlacement(
            side=getattr(self, f"{prefix}_load_side_combo").currentText(),
            from_percent=float(getattr(self, f"{prefix}_load_from_input").text()),
            to_percent=float(getattr(self, f"{prefix}_load_to_input").text()),
        )

    def roof_load_placement_data(self, prefix):
        return {
            "side": getattr(self, f"{prefix}_load_side_combo").currentText(),
            "from_percent": getattr(self, f"{prefix}_load_from_input").text(),
            "to_percent": getattr(self, f"{prefix}_load_to_input").text(),
        }

    def apply_roof_load_placement_data(self, prefix, data):
        data = data or {}
        self.set_combo_text_if_present(getattr(self, f"{prefix}_load_side_combo"), data.get("side"))
        getattr(self, f"{prefix}_load_from_input").setText(str(data.get("from_percent", getattr(self, f"{prefix}_load_from_input").text())))
        getattr(self, f"{prefix}_load_to_input").setText(str(data.get("to_percent", getattr(self, f"{prefix}_load_to_input").text())))

    def profile_family_name(self, profile_or_name):
        name = profile_or_name.name if hasattr(profile_or_name, "name") else str(profile_or_name or "")
        text = name.upper()
        for token in ("UB", "UC", "PFC", "RHS", "SHS", "CHS"):
            if re.search(rf"(^|[^A-Z]){token}([^A-Z]|$)", text):
                return token
        return ""

    def current_roof_shape(self):
        if getattr(self, "roof_shape_combo", None) is None:
            return "Gable"
        return self.roof_shape_combo.currentText() or "Gable"

    def wind_inputs_from_ui(self, span_mm, pitch_deg, eave_mm):
        building_length_m = float(self.building_length_input.text())
        wall_states = self.envelope_plan_selector.wall_states()
        frame_type = self.frame_type_from_wall_states(wall_states)
        return WindInputs(
            wind_region=self.wind_region_combo.currentText(),
            importance_level=int(self.importance_combo.currentText()),
            terrain_category=float(self.terrain_combo.currentText()),
            eave_height_m=eave_mm / 1000.0,
            roof_pitch_deg=pitch_deg,
            roof_shape=self.current_roof_shape(),
            building_width_m=span_mm / 1000.0,
            building_length_m=building_length_m,
            bay_size_m=self.bay_size_m_from_ui(),
            orientation=self.wind_orientation_selector.currentText(),
            frame_type=frame_type,
            cpi_case="Cpi +",
            left_wall_clad=wall_states["left"],
            right_wall_clad=wall_states["right"],
            front_wall_clad=wall_states["front"],
            back_wall_clad=wall_states["back"],
            left_canopy_length_m=(max(float(self.left_canopy_length_input.text()), 0.0) if self.side_addition_selected(self.left_canopy_type_combo) else 0.0),
            right_canopy_length_m=(max(float(self.right_canopy_length_input.text()), 0.0) if self.side_addition_selected(self.right_canopy_type_combo) else 0.0),
            reduction_factor=float(self.wind_reduction_input.text()),
            internal_pressure_mode=self.internal_pressure_combo.currentText(),
            local_pressure_regions=self.local_pressure_regions_from_ui(),
        )

    LOCAL_PRESSURE_CHECKBOXES = (("RA1", "local_pressure_ra1_checkbox"), ("RA2", "local_pressure_ra2_checkbox"),
                                 ("RC1", "local_pressure_rc1_checkbox"), ("RC2", "local_pressure_rc2_checkbox"))

    def local_pressure_regions_from_ui(self):
        """Table 5.6 roof regions switched on in the wind inputs (all, if the form has no switches)."""
        return tuple(region for region, name in self.LOCAL_PRESSURE_CHECKBOXES
                     if getattr(self, name, None) is None or getattr(self, name).isChecked())

    def local_pressure_regions_off(self):
        return [region for region, _ in self.LOCAL_PRESSURE_CHECKBOXES
                if region not in self.local_pressure_regions_from_ui()]

    def wind_source_direction_for_case(self, north_up_direction, cpe_case):
        directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        if north_up_direction == "Any":
            return "Any"
        from portalcalc.wind import direction_bearing_degrees
        north_angle = direction_bearing_degrees(north_up_direction)

        def compass_direction_for_screen_direction(screen_direction):
            bearing = (direction_bearing_degrees(screen_direction) - north_angle) % 360
            if north_up_direction in directions:
                return directions[int(round(bearing / 45)) % 8]
            return bearing

        if str(cpe_case).startswith("Left"):
            return compass_direction_for_screen_direction("W")
        if str(cpe_case).startswith("Right"):
            return compass_direction_for_screen_direction("E")
        if str(cpe_case).startswith("End"):
            direction = compass_direction_for_screen_direction("N")
            walls = self.envelope_plan_selector.wall_states()
            if walls["front"] and walls["back"]:
                from portalcalc.wind import symmetric_end_source_direction
                return symmetric_end_source_direction(self.wind_region_combo.currentText(), direction)
            return direction
        return north_up_direction

    def update_canopy_depth_from_geometry(self, side):
        type_combo = self.left_canopy_type_combo if side == "left" else self.right_canopy_type_combo
        if type_combo.currentText() != "Canopy":
            return
        length_input = self.left_canopy_length_input if side == "left" else self.right_canopy_length_input
        pitch_input = self.left_canopy_pitch_input if side == "left" else self.right_canopy_pitch_input
        depth_input = self.left_canopy_shallow_depth_input if side == "left" else self.right_canopy_shallow_depth_input
        try:
            length_mm = max(mm_from_metres_text(length_input.text()), 0.0)
            pitch_deg = float(pitch_input.text())
        except ValueError:
            return
        roof_fall_mm = abs(math.tan(math.radians(pitch_deg)) * length_mm)
        deep_end_depth_mm = self.round_up_to_increment(300.0 + roof_fall_mm, 50.0)
        depth_input.setText(f"{deep_end_depth_mm:.0f}")
