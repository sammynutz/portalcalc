"""Presets: the saved form of every design input, and the factory defaults."""

import copy
import json
import math
import os
import re
import sys

from portalcalc import bay_layout
from portalcalc.bracing.restraint import OPTION as BRACING_3D_RESTRAINT_OPTION
from portalcalc.frame.common import (
    canopy_depth_mm_from_preset,
    clean_profile_label,
    COSTING_DEFAULTS,
    DEFAULT_ROOF_CLADDING_PROFILE,
    FACTORY_DEFAULTS_FILENAME,
    metres_from_mm_text,
    mm_text_from_metres_widget,
    normalize_end_wall_brace_label,
    resource_path,
    SERVICEABILITY_LIMIT_DEFAULTS,
)
from portalcalc.resources import DATA_DIR


class PresetsMixin:
    """Collects the inputs into a preset dictionary and applies one back."""

    def canopy_purlin_preset(self, side):
        """Canopy edge purlin inputs, saved only for an open canopy (so presets
        and their fingerprints are unchanged for buildings without one)."""
        if getattr(self, f"{side}_canopy_type_combo").currentText() != "Canopy":
            return {}
        return {f"{side}_purlin_section": getattr(self, f"{side}_canopy_purlin_combo").currentText(),
                f"{side}_purlin_spacing_mm": getattr(self, f"{side}_canopy_purlin_spacing_input").text()}

    def apply_bays_from_preset(self, geometry, loads, wind):
        """The bay list from a preset; older presets convert from length + bay size."""
        text = (geometry or {}).get("bays_mm")
        if text not in (None, ""):
            try:
                self.set_bays(bay_layout.parse_bays(text))
                return
            except bay_layout.BayListError:
                pass  # unreadable: fall back to the length and bay size
        try:
            length = float((wind or {}).get("building_length_m", self.building_length_input.text())) * 1000.0
            if (loads or {}).get("bay_size_mm") not in (None, ""):
                bay = float(loads["bay_size_mm"])
            elif (loads or {}).get("bay_size_m") not in (None, ""):
                bay = float(loads["bay_size_m"]) * 1000.0
            else:
                bay = float(self.load_bay_size_input.text()) * 1000.0
            self.set_bays(bay_layout.bays_from_length(length, bay))
        except (TypeError, ValueError):
            pass  # keep the current bays

    def factory_defaults_paths(self):
        # A packaged install may carry an editable copy beside the EXE; it
        # takes precedence over the bundled factory defaults.
        app_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else str(DATA_DIR)
        external_path = os.path.join(app_dir, "presets", FACTORY_DEFAULTS_FILENAME)
        bundled_path = resource_path(os.path.join("presets", FACTORY_DEFAULTS_FILENAME))
        paths = [external_path]
        if os.path.abspath(bundled_path) != os.path.abspath(external_path):
            paths.append(bundled_path)
        return paths

    def factory_defaults_path(self):
        for path in self.factory_defaults_paths():
            if os.path.exists(path):
                return path
        return self.factory_defaults_paths()[0]

    def update_factory_defaults_status_label(self):
        label = getattr(self, "factory_defaults_status_label", None)
        if label is None:
            return
        label.setText(self.factory_defaults_load_status)

    def load_factory_defaults(self, silent=False):
        path = self.factory_defaults_path()
        self.factory_defaults_loaded_path = path
        if not os.path.exists(path):
            self.factory_defaults_load_status = f"Not found: {path}"
            self.update_factory_defaults_status_label()
            return False
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            self.apply_preset_data(data)
            self.factory_default_data = copy.deepcopy(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self.factory_defaults_load_status = f"Failed: {path} ({exc})"
            self.update_factory_defaults_status_label()
            if not silent:
                self.notify("critical", "Load factory defaults failed", str(exc))
            return False
        self.factory_defaults_load_status = (
            f"Loaded: {path}\n"
            f"Span {self.span_input.text()} m, G {self.g_load_input.text()} kPa, "
            f"left column {self.left_column_profile_combo.currentText()}"
        )
        self.update_factory_defaults_status_label()
        return True

    def collect_preset_data(self):
        data = {
            "version": 1,
            "geometry": {
                "span_mm": mm_text_from_metres_widget(self.span_input),
                # The length input; wind.building_length_m and loads.bay_size_* are derived from it.
                "bays_mm": bay_layout.format_bays(self._bay_lengths_mm),
                "roof_pitch_deg": self.pitch_input.text(),
                "eave_height_mm": mm_text_from_metres_widget(self.eave_input),
                "max_truss_depth_mm": self.max_truss_depth_input.text(),
                "truss_depth_mm": self.depth_input.text(),
                "roof_shape": self.roof_shape_combo.currentText(),
                "frame_system": self.frame_system_combo.currentText(),
                "rafter_haunch_length_mm": mm_text_from_metres_widget(self.rafter_haunch_length_input),
                "internal_column_count": self.internal_column_count_combo.currentText(),
                "internal_column_offset_mm": mm_text_from_metres_widget(self.internal_column_offset_input),
                "internal_column_positions_m": self.internal_column_positions_input.text().strip(),
                "internal_column_connection": self.internal_column_connection_combo.currentText(),
                "left_support": self.left_support_combo.currentText(),
                "right_support": self.right_support_combo.currentText(),
                "base_moment_spring_knm_rad": self.base_x_spring_input.text(),
                "left_column_length_adjust_mm": mm_text_from_metres_widget(self.left_column_length_adjust_input),
                "right_column_length_adjust_mm": mm_text_from_metres_widget(self.right_column_length_adjust_input),
                "end_wall_mullion_spacing_override_mm": mm_text_from_metres_widget(self.end_wall_mullion_spacing_override_input),
                "end_wall_bracing": self.eave_x_restraint_combo.currentText(),
                "end_wall_braced_sides": self.end_wall_braced_sides_combo.currentText(),
                "end_wall_brace_size": self.selected_end_wall_brace_size()["label"],
            },
            "bracing": {
                "enabled": self.longitudinal_bracing_enabled_checkbox.isChecked(),
                "wall_sides": self.longitudinal_wall_sides_combo.currentText(),
                "braced_bays_per_side": self.end_wall_braced_bay_count_input.value(),
                "roof_brace_size": self.selected_brace_size(self.roof_brace_size_combo)["label"],
                "wall_brace_size": self.selected_brace_size(self.wall_brace_size_combo)["label"],
                **({"layout": copy.deepcopy(self.bracing_location_layout)} if self.bracing_location_layout is not None else {}),
                **({"analysis_3d": copy.deepcopy(self.bracing_3d_settings)} if self.bracing_3d_settings is not None else {}),
                **({"restraint_3d": copy.deepcopy(self.bracing_3d_restraint)} if self.bracing_3d_restraint is not None else {}),
            },
            "canopies": {
                "left_length_mm": mm_text_from_metres_widget(self.left_canopy_length_input),
                "left_type": self.left_canopy_type_combo.currentText(),
                "left_frame_system": self.left_canopy_frame_system_combo.currentText(),
                "left_wind_model": self.left_canopy_wind_model_combo.currentText(),
                "left_underside": self.left_canopy_underside_combo.currentText(),
                "left_cpn_uplift": self.left_canopy_cpn_uplift_input.text(),
                "left_cpn_downward": self.left_canopy_cpn_downward_input.text(),
                "left_lean_outer_wall_clad": self.legacy_lean_outer_wall_clad_text("left"),
                "left_lean_enclosure": self.left_lean_enclosure_selector.wall_states(),
                "left_eave_height_mm": mm_text_from_metres_widget(self.left_canopy_eave_height_input),
                "left_pitch_deg": self.left_canopy_pitch_input.text(),
                "left_depth_mm": self.left_canopy_shallow_depth_input.text(),
                "left_g_kpa": self.left_canopy_g_load_input.text(),
                "left_q_kpa": self.left_canopy_q_load_input.text(),
                "left_solar_kpa": self.left_canopy_solar_load_input.text(),
                **self.canopy_purlin_preset("left"),
                "right_length_mm": mm_text_from_metres_widget(self.right_canopy_length_input),
                "right_type": self.right_canopy_type_combo.currentText(),
                "right_frame_system": self.right_canopy_frame_system_combo.currentText(),
                "right_wind_model": self.right_canopy_wind_model_combo.currentText(),
                "right_underside": self.right_canopy_underside_combo.currentText(),
                "right_cpn_uplift": self.right_canopy_cpn_uplift_input.text(),
                "right_cpn_downward": self.right_canopy_cpn_downward_input.text(),
                "right_lean_outer_wall_clad": self.legacy_lean_outer_wall_clad_text("right"),
                "right_lean_enclosure": self.right_lean_enclosure_selector.wall_states(),
                "right_eave_height_mm": mm_text_from_metres_widget(self.right_canopy_eave_height_input),
                "right_pitch_deg": self.right_canopy_pitch_input.text(),
                "right_depth_mm": self.right_canopy_shallow_depth_input.text(),
                "right_g_kpa": self.right_canopy_g_load_input.text(),
                "right_q_kpa": self.right_canopy_q_load_input.text(),
                "right_solar_kpa": self.right_canopy_solar_load_input.text(),
                **self.canopy_purlin_preset("right"),
            },
            "sections": {
                "left_column": self.left_column_profile_combo.currentText(),
                "right_column": self.right_column_profile_combo.currentText(),
                "internal_column": self.internal_column_profile_combo.currentText(),
                "top_chord": self.top_profile_combo.currentText(),
                "bottom_chord": self.top_profile_combo.currentText(),
                "purlin_span_type": self.purlin_span_type_combo.currentText(),
                "extra_purlin_rows": int(getattr(self, "extra_purlin_rows", 0)),
                "wall_girt_span_type": self.wall_girt_span_type_combo.currentText(),
                "webs_posts": self.web_profile_combo.currentText(),
                "left_canopy_top_chord": self.left_canopy_top_profile_combo.currentText(),
                "left_canopy_bottom_chord": self.left_canopy_bottom_profile_combo.currentText(),
                "left_canopy_webs_posts": self.left_canopy_web_profile_combo.currentText(),
                "left_lean_to_column": self.left_lean_column_profile_combo.currentText(),
                "right_canopy_top_chord": self.right_canopy_top_profile_combo.currentText(),
                "right_canopy_bottom_chord": self.right_canopy_bottom_profile_combo.currentText(),
                "right_canopy_webs_posts": self.right_canopy_web_profile_combo.currentText(),
                "right_lean_to_column": self.right_lean_column_profile_combo.currentText(),
            },
            "costing": {
                "shs_rate_per_kg": self.shs_cost_input.text(),
                "doubled_chord_rate_per_kg": self.doubled_chord_cost_input.text(),
                "ub_rate_per_kg": self.ub_cost_input.text(),
                "wb_rate_per_kg": self.wb_cost_input.text(),
                "purlin_rate_per_kg": self.purlin_cost_input.text(),
                "storage_concrete_rate_per_m3": self.storage_panel_concrete_rate_input.text(),
                "storage_reo_rate_per_kg": self.storage_panel_reo_rate_input.text(),
            },
            "finishes": {
                "roof_profile": self.roof_cladding_profile_combo.currentText(),
            },
            "foundations": {
                "allowable_bearing_kpa": self.foundation_bearing_input.text(),
                "skin_friction_kpa": self.foundation_skin_friction_input.text(),
                "use_skin_friction_for_bearing": self.foundation_skin_bearing_checkbox.isChecked(),
            },
            "storage_loads": {
                "enabled": self.storage_enabled_checkbox.isChecked(),
                "load_path_mode": self.storage_load_path_combo.currentText(),
                "panel_support_mode": self.storage_panel_support_combo.currentText(),
                "material": self.storage_material_input.text(),
                "bulk_density_kn_m3": self.storage_density_input.text(),
                "internal_friction_angle_deg": self.storage_phi_input.text(),
                "backfill_slope_deg": self.storage_beta_input.text(),
                "stored_height_m": self.storage_height_input.text(),
                "rail_height_mm": mm_text_from_metres_widget(self.storage_rail_height_input),
                "rail_profile": self.storage_rail_profile_combo.currentText(),
                "stub_profile": self.storage_stub_profile_combo.currentText(),
                "panel_concrete_f_c_mpa": self.storage_panel_fc_input.text(),
                "panel_reo_f_y_mpa": self.storage_panel_reo_fy_input.text(),
                "panel_cover_mm": self.storage_panel_cover_input.text(),
                "panel_deflection_limit_ratio": self.storage_panel_deflection_limit_input.text(),
                "panel_deflection_stiffness_factor": self.storage_panel_stiffness_factor_input.text(),
                "panel_crack_min_thickness_mm": self.storage_panel_crack_min_thickness_input.text(),
                "panel_crack_max_spacing_mm": self.storage_panel_crack_max_spacing_input.text(),
                "panel_crack_service_stress_limit_mpa": self.storage_panel_crack_stress_limit_input.text(),
                "panel_crack_width_limit_mm": self.storage_panel_crack_width_limit_input.text(),
                "panel_shrinkage_microstrain": self.storage_panel_shrinkage_input.text(),
                "panel_creep_coefficient": self.storage_panel_creep_input.text(),
                "panel_thicknesses_mm": self.storage_panel_thicknesses_input.text(),
                "panel_auto_thickness": self.storage_panel_auto_thickness_checkbox.isChecked(),
                "panel_selected_thickness_mm": self.storage_panel_selected_thickness_input.value(),
                "panel_mesh_options": self.storage_panel_mesh_input.text(),
                "panel_bar_diameters_mm": self.storage_panel_bars_input.text(),
                "panel_bar_spacings_mm": self.storage_panel_spacings_input.text(),
                "left_wall": self.storage_left_wall_checkbox.isChecked(),
                "right_wall": self.storage_right_wall_checkbox.isChecked(),
                "front_wall": self.storage_front_wall_checkbox.isChecked(),
                "back_wall": self.storage_back_wall_checkbox.isChecked(),
                "reverse_direction": self.storage_reverse_direction_checkbox.isChecked(),
                "uls_factor": self.storage_uls_factor_input.text(),
                "sls_factor": self.storage_sls_factor_input.text(),
            },
            "crane_beam": {
                "beam_profile": self.crane_beam_profile_combo.currentText(),
                "pfc_cap": self.crane_beam_pfc_cap_combo.currentText(),
                "rail_profile": self.crane_beam_rail_profile_combo.currentText(),
                "span_mm": mm_text_from_metres_widget(self.crane_beam_span_input),
                "wheelbase_mm": mm_text_from_metres_widget(self.crane_beam_wheelbase_input),
                "load_height_mm": mm_text_from_metres_widget(self.crane_beam_load_height_input),
                "beam_self_weight_kn_m": self.crane_beam_self_weight_input.text(),
                "rail_self_weight_kn_m": self.crane_beam_rail_weight_input.text(),
                "rail_bte_mm": self.crane_beam_rail_bte_input.text(),
                "rail_k": self.crane_beam_rail_k_input.text(),
                "pmax_21_kn": self.crane_beam_pmax21_input.text(),
                "pmax_22_kn": self.crane_beam_pmax22_input.text(),
                "pmin_11_kn": self.crane_beam_pmin11_input.text(),
                "pmin_12_kn": self.crane_beam_pmin12_input.text(),
                "pstatic_21_kn": self.crane_beam_pstatic21_input.text(),
                "pstatic_22_kn": self.crane_beam_pstatic22_input.text(),
                "pstatic_min_11_kn": self.crane_beam_pstatic_min11_input.text(),
                "pstatic_min_12_kn": self.crane_beam_pstatic_min12_input.text(),
                "phb_21_kn": self.crane_beam_phb21_input.text(),
                "phb_22_kn": self.crane_beam_phb22_input.text(),
                "phi_2_kn": self.crane_beam_phi2_input.text(),
                "buffer_force_kn": self.crane_beam_buffer_input.text().strip(),
                "ix_x10_6_mm4": self.crane_beam_ix_input.text(),
                "iy_top_x10_6_mm4": self.crane_beam_iy_top_input.text(),
                "iy_oblique_x10_6_mm4": self.crane_beam_iy_oblique_input.text(),
                "zx_x10_3_mm3": self.crane_beam_zx_input.text(),
                "zy_x10_3_mm3": self.crane_beam_zy_input.text(),
                "depth_mm": self.crane_beam_depth_input.text(),
                "flange_thickness_mm": self.crane_beam_flange_thickness_input.text(),
                "fy_mpa": self.crane_beam_fy_input.text(),
                "include_self_weight_in_deflection": self.crane_beam_include_sw_deflection_checkbox.isChecked(),
            },
            "loads": {
                "bay_size_mm": mm_text_from_metres_widget(self.load_bay_size_input),
                "bay_size_m": self.bay_size_m_from_ui(),
                "g_kpa": self.g_load_input.text(),
                "q_kpa": self.q_load_input.text(),
                "solar_kpa": self.solar_load_input.text(),
                "fire_service_kpa": self.fire_service_load_input.text(),
                "hvac_kpa": self.hvac_load_input.text(),
                "other_kpa": self.other_load_input.text(),
                "crane_rating_t": self.crane_rating_input.text(),
                "snow_region": self.snow_region_combo.currentText(),
                "snow_ahd_height_m": self.snow_ahd_height_input.text(),
                "roof_load_placements": {
                    "g": self.roof_load_placement_data("g"),
                    "q": self.roof_load_placement_data("q"),
                    "solar": self.roof_load_placement_data("solar"),
                    "other": self.roof_load_placement_data("other"),
                },
                "left_wall_kn_m": self.left_wall_load_input.text(),
                "right_wall_kn_m": self.right_wall_load_input.text(),
                "load_combination": self.load_combination_combo.currentText(),
            },
            "wind": {
                "region": self.wind_region_combo.currentText(),
                "importance_level": self.importance_combo.currentText(),
                "terrain_category": self.terrain_combo.currentText(),
                "orientation": self.wind_orientation_selector.currentText(),
                "frame_type": self.frame_type_from_wall_states(),
                "wall_enclosure": self.envelope_plan_selector.wall_states(),
                "internal_pressure_mode": self.internal_pressure_combo.currentText(),
                **({"frame_load_basis": self.frame_load_basis()} if self.frame_load_basis() != "single_frame" else {}),
                "single_wind_case": self.wind_case_combo.currentText(),
                "building_length_m": self.building_length_input.text(),
                "additional_pressure_factor": self.wind_reduction_input.text(),
            },
            "display": {
                "diagram_view": self.result_combo.currentText(),
                "load_view_mode": self.load_view_mode_combo.currentText(),
                "single_load": self.single_load_combo.currentText(),
                "second_order_analysis": self.second_order_checkbox.isChecked(),
                "show_governing_deflection": self.governing_deflection_overlay_checkbox.isChecked(),
                "deflection_scale": self.deflection_scale_input.text(),
                "diagram_scale": self.diagram_scale_input.text(),
                "ui_theme": self.ui_theme_combo.currentText(),
                "eave_deflection_limit_override": self.eave_deflection_limit_override,
                "serviceability_limit_overrides": copy.deepcopy(self.serviceability_limit_overrides),
            },
        }

        future = getattr(self, "future_canopy_design", None)
        if future:
            from portalcalc.service.contract import json_safe
            data["future_canopy_design"] = json_safe(future)
            data["calculation_service"] = json_safe({
                "main_truss_panel_count": future["main_truss_panel_count"],
                "auto_web_profile_assignments": self.auto_web_profile_assignments,
                "auto_web_joint_doubler_sides": self.auto_web_joint_doubler_sides,
            })
        return data

    def set_combo_text_if_present(self, combo, value):
        if value is None:
            return
        target = str(value).strip()
        aliases = {
            "1 central": "1 column",
        }
        target = aliases.get(target, target)
        index = combo.findText(target)
        if index >= 0:
            combo.setCurrentIndex(index)
            return
        for item_index in range(combo.count()):
            item_text = combo.itemText(item_index)
            clean_item = clean_profile_label(item_text)
            if clean_item == target:
                combo.setCurrentIndex(item_index)
                return

    def set_end_wall_brace_size_from_preset(self, value):
        target = normalize_end_wall_brace_label(value)
        if target is None:
            return
        for item_index in range(self.end_wall_brace_size_combo.count()):
            item_text = normalize_end_wall_brace_label(self.end_wall_brace_size_combo.itemText(item_index))
            item_data = self.end_wall_brace_size_combo.itemData(item_index)
            item_label = None
            if isinstance(item_data, dict):
                item_label = normalize_end_wall_brace_label(item_data.get("label"))
            if target in {item_text, item_label}:
                self.end_wall_brace_size_combo.setCurrentIndex(item_index)
                return

    def legacy_lean_outer_wall_clad_text(self, side):
        if side == "left":
            states = self.left_lean_enclosure_selector.wall_states()
            return "Yes" if states.get("left", False) else "No"
        states = self.right_lean_enclosure_selector.wall_states()
        return "Yes" if states.get("right", False) else "No"

    def apply_lean_enclosure_from_preset(self, side, enclosure, legacy_outer_wall_clad):
        selector = self.left_lean_enclosure_selector if side == "left" else self.right_lean_enclosure_selector
        legacy_combo = self.left_lean_outer_wall_clad_combo if side == "left" else self.right_lean_outer_wall_clad_combo
        outer_key = "left" if side == "left" else "right"

        if isinstance(enclosure, dict):
            selector.set_wall_states(enclosure)
        else:
            selector.set_wall_states({outer_key: str(legacy_outer_wall_clad).strip().lower() == "yes"})

        self.set_combo_text_if_present(legacy_combo, self.legacy_lean_outer_wall_clad_text(side))

    def normalized_canopy_wind_model(self, value):
        if value == "Free roof net Cpn":
            return "Manual net Cpn"
        return value

    def normalized_single_load_label(self, value):
        if value is None:
            return value
        text = str(value)
        if text.startswith("Wu Cpe ") or text.startswith("Ws Cpe ") or text.startswith("Wu Cpi ") or text.startswith("Ws Cpi "):
            return text
        wind_prefixes = ["Wu Left", "Wu Right", "Wu End", "Ws Left", "Ws Right", "Ws End"]
        for prefix in wind_prefixes:
            if text.startswith(prefix):
                return text.replace("Wu ", "Wu Cpe ", 1).replace("Ws ", "Ws Cpe ", 1)
        if text.startswith("Cpi "):
            return f"Wu {text}"
        if text.startswith("Ws Cpi "):
            return text
        return text

    def apply_preset_data(self, data):
        self.future_canopy_design = copy.deepcopy(data.get("future_canopy_design"))
        self._preserve_web_assignments_on_solve = bool(self.future_canopy_design)
        self._suppress_panel_topology_search = bool(self.future_canopy_design)
        if not self.future_canopy_design:
            self._panel_count_override = None
        previous_defer_solve = getattr(self, "_defer_generate_and_solve", False)
        self._defer_generate_and_solve = True
        self._deferred_generate_and_solve = False
        preset_signal_blockers = self.block_input_signals()
        geometry = data.get("geometry", {})
        self.span_input.setText(metres_from_mm_text(geometry.get("span_mm"), self.span_input.text()))
        self.pitch_input.setText(str(geometry.get("roof_pitch_deg", self.pitch_input.text())))
        self.eave_input.setText(metres_from_mm_text(geometry.get("eave_height_mm"), self.eave_input.text()))
        max_truss_depth_value = geometry.get("max_truss_depth_mm", self.max_truss_depth_input.text())
        self.max_truss_depth_input.setText("" if max_truss_depth_value is None else str(max_truss_depth_value))
        truss_depth_value = geometry.get("truss_depth_mm")
        if truss_depth_value not in (None, ""):
            self.depth_input.setText(str(truss_depth_value))
        self.set_combo_text_if_present(self.roof_shape_combo, geometry.get("roof_shape", "Gable"))
        self.set_combo_text_if_present(self.frame_system_combo, geometry.get("frame_system"))
        self.rafter_haunch_length_input.setText(metres_from_mm_text(geometry.get("rafter_haunch_length_mm"), self.rafter_haunch_length_input.text()))
        self.set_combo_text_if_present(self.internal_column_count_combo, geometry.get("internal_column_count"))
        self.internal_column_offset_input.setText(metres_from_mm_text(geometry.get("internal_column_offset_mm"), self.internal_column_offset_input.text()))
        self.internal_column_positions_input.setText(str(geometry.get("internal_column_positions_m", "")))
        self.set_combo_text_if_present(self.internal_column_connection_combo, geometry.get("internal_column_connection", "Pinned"))
        self.set_combo_text_if_present(self.left_support_combo, geometry.get("left_support"))
        self.set_combo_text_if_present(self.right_support_combo, geometry.get("right_support"))
        self.base_x_spring_input.setText(str(geometry.get(
            "base_moment_spring_knm_rad",
            geometry.get("base_x_spring_kn_mm", self.base_x_spring_input.text()),
        )))
        self.update_base_spring_visibility()
        self.left_column_length_adjust_input.setText(metres_from_mm_text(geometry.get("left_column_length_adjust_mm"), self.left_column_length_adjust_input.text()))
        self.right_column_length_adjust_input.setText(metres_from_mm_text(geometry.get("right_column_length_adjust_mm"), self.right_column_length_adjust_input.text()))
        legacy_end_wall = data.get("end_wall", {})
        self.end_wall_mullion_spacing_override_input.setText(metres_from_mm_text(
            geometry.get("end_wall_mullion_spacing_override_mm", legacy_end_wall.get("mullion_spacing_override_mm")),
            self.end_wall_mullion_spacing_override_input.text(),
        ))
        end_wall_bracing = geometry.get("end_wall_bracing", geometry.get("eave_x_restraint"))
        legacy_end_wall_bracing = end_wall_bracing
        legacy_offset = geometry.get("end_wall_brace_frame_offset_m")
        if end_wall_bracing in {"End wall bracing approx", "End wall bracing model"}:
            end_wall_bracing = "Both ends" if legacy_offset in (None, "") else "One end"
        elif end_wall_bracing in {"Left eave", "Right eave", "One eave", "One side"}:
            end_wall_bracing = "One end"
        elif end_wall_bracing in {"Both eaves", "Both sides"}:
            end_wall_bracing = "Both ends"
        elif end_wall_bracing not in {"None", "One end", "Both ends", BRACING_3D_RESTRAINT_OPTION}:
            end_wall_bracing = "None"
        self.set_combo_text_if_present(self.eave_x_restraint_combo, end_wall_bracing)
        end_wall_braced_sides = geometry.get("end_wall_braced_sides")
        if end_wall_braced_sides is None:
            if legacy_end_wall_bracing in {"End wall bracing approx", "End wall bracing model", "Both eaves", "Both sides"}:
                end_wall_braced_sides = "Both sides"
            else:
                end_wall_braced_sides = "One side"
        self.set_combo_text_if_present(
            self.end_wall_braced_sides_combo,
            end_wall_braced_sides,
        )
        brace_size = geometry.get("end_wall_brace_size")
        if brace_size:
            self.set_end_wall_brace_size_from_preset(brace_size)
        elif geometry.get("end_wall_brace_dia_mm") is not None:
            try:
                self.set_end_wall_brace_size_from_preset(f"{float(geometry.get('end_wall_brace_dia_mm')):.0f} DIA ROD")
            except (TypeError, ValueError):
                pass
        bracing = data.get("bracing", {})
        self.bracing_location_layout = copy.deepcopy(bracing.get("layout"))
        self.bracing_3d_settings = copy.deepcopy(bracing.get("analysis_3d"))
        self.bracing_3d_calculation_cache = copy.deepcopy(bracing.get('calculation_3d'))
        self.bracing_3d_restraint = copy.deepcopy(bracing.get('restraint_3d'))
        self.bracing_3d_restraint_note = None
        self.longitudinal_bracing_enabled_checkbox.setChecked(bool(bracing.get("enabled", False)))
        self.set_combo_text_if_present(self.longitudinal_wall_sides_combo, bracing.get("wall_sides", "Both sides"))
        try:
            self.end_wall_braced_bay_count_input.setValue(max(1, int(bracing.get(
                "braced_bays_per_side", geometry.get("end_wall_braced_bays_per_side", 1)
            ))))
        except (TypeError, ValueError):
            self.end_wall_braced_bay_count_input.setValue(1)
        for combo, value in (
            (self.roof_brace_size_combo, bracing.get("roof_brace_size")),
            (self.wall_brace_size_combo, bracing.get("wall_brace_size")),
        ):
            target = normalize_end_wall_brace_label(value)
            if target:
                for index in range(combo.count()):
                    item = combo.itemData(index)
                    if isinstance(item, dict) and normalize_end_wall_brace_label(item.get("label")) == target:
                        combo.setCurrentIndex(index)
                        break

        canopies = data.get("canopies", {})
        self.left_canopy_length_input.setText(metres_from_mm_text(canopies.get("left_length_mm"), self.left_canopy_length_input.text()))
        left_type = canopies.get("left_type")
        if left_type is None:
            try:
                left_type = "Canopy" if float(self.left_canopy_length_input.text()) > 0.0 else None
            except ValueError:
                left_type = None
        self.set_combo_text_if_present(self.left_canopy_type_combo, left_type)
        self.set_combo_text_if_present(self.left_canopy_frame_system_combo, canopies.get("left_frame_system", canopies.get("frame_system", "Truss")))
        self.set_combo_text_if_present(self.left_canopy_wind_model_combo, self.normalized_canopy_wind_model(canopies.get("left_wind_model")))
        self.set_combo_text_if_present(self.left_canopy_underside_combo, canopies.get("left_underside"))
        self.left_canopy_cpn_uplift_input.setText(str(canopies.get("left_cpn_uplift", self.left_canopy_cpn_uplift_input.text())))
        self.left_canopy_cpn_downward_input.setText(str(canopies.get("left_cpn_downward", self.left_canopy_cpn_downward_input.text())))
        self.apply_lean_enclosure_from_preset(
            "left",
            canopies.get("left_lean_enclosure"),
            canopies.get("left_lean_outer_wall_clad"),
        )
        self.left_canopy_eave_height_input.setText(metres_from_mm_text(canopies.get("left_eave_height_mm", canopies.get("eave_height_mm")), self.left_canopy_eave_height_input.text()))
        self.left_canopy_pitch_input.setText(str(canopies.get("left_pitch_deg", canopies.get("pitch_deg", self.left_canopy_pitch_input.text()))))
        self.left_canopy_shallow_depth_input.setText(canopy_depth_mm_from_preset(
            canopies, "left", left_type, self.left_canopy_shallow_depth_input.text()
        ))
        self.left_canopy_g_load_input.setText(self.downward_roof_load_text(canopies.get("left_g_kpa", canopies.get("g_kpa", self.left_canopy_g_load_input.text()))))
        self.left_canopy_q_load_input.setText(self.downward_roof_load_text(canopies.get("left_q_kpa", canopies.get("q_kpa", self.left_canopy_q_load_input.text()))))
        self.left_canopy_solar_load_input.setText(self.downward_roof_load_text(canopies.get("left_solar_kpa", canopies.get("solar_kpa", self.left_canopy_solar_load_input.text()))))
        self.right_canopy_length_input.setText(metres_from_mm_text(canopies.get("right_length_mm"), self.right_canopy_length_input.text()))
        right_type = canopies.get("right_type")
        if right_type is None:
            try:
                right_type = "Canopy" if float(self.right_canopy_length_input.text()) > 0.0 else None
            except ValueError:
                right_type = None
        self.set_combo_text_if_present(self.right_canopy_type_combo, right_type)
        self.set_combo_text_if_present(self.right_canopy_frame_system_combo, canopies.get("right_frame_system", canopies.get("frame_system", "Truss")))
        self.set_combo_text_if_present(self.right_canopy_wind_model_combo, self.normalized_canopy_wind_model(canopies.get("right_wind_model")))
        self.set_combo_text_if_present(self.right_canopy_underside_combo, canopies.get("right_underside"))
        self.right_canopy_cpn_uplift_input.setText(str(canopies.get("right_cpn_uplift", self.right_canopy_cpn_uplift_input.text())))
        self.right_canopy_cpn_downward_input.setText(str(canopies.get("right_cpn_downward", self.right_canopy_cpn_downward_input.text())))
        self.apply_lean_enclosure_from_preset(
            "right",
            canopies.get("right_lean_enclosure"),
            canopies.get("right_lean_outer_wall_clad"),
        )
        self.right_canopy_eave_height_input.setText(metres_from_mm_text(canopies.get("right_eave_height_mm", canopies.get("eave_height_mm")), self.right_canopy_eave_height_input.text()))
        self.right_canopy_pitch_input.setText(str(canopies.get("right_pitch_deg", canopies.get("pitch_deg", self.right_canopy_pitch_input.text()))))
        self.right_canopy_shallow_depth_input.setText(canopy_depth_mm_from_preset(
            canopies, "right", right_type, self.right_canopy_shallow_depth_input.text()
        ))
        self.right_canopy_g_load_input.setText(self.downward_roof_load_text(canopies.get("right_g_kpa", canopies.get("g_kpa", self.right_canopy_g_load_input.text()))))
        self.right_canopy_q_load_input.setText(self.downward_roof_load_text(canopies.get("right_q_kpa", canopies.get("q_kpa", self.right_canopy_q_load_input.text()))))
        self.right_canopy_solar_load_input.setText(self.downward_roof_load_text(canopies.get("right_solar_kpa", canopies.get("solar_kpa", self.right_canopy_solar_load_input.text()))))
        for side in ("left", "right"):
            combo = getattr(self, f"{side}_canopy_purlin_combo")
            section = canopies.get(f"{side}_purlin_section")
            combo.setCurrentIndex(max(combo.findText(section), 0) if section else 0)
            getattr(self, f"{side}_canopy_purlin_spacing_input").setText(str(canopies.get(f"{side}_purlin_spacing_mm", "") or ""))

        sections = data.get("sections", {})
        self.set_combo_text_if_present(self.left_column_profile_combo, sections.get("left_column"))
        self.set_combo_text_if_present(self.right_column_profile_combo, sections.get("right_column"))
        self.set_combo_text_if_present(self.internal_column_profile_combo, sections.get("internal_column"))
        self.set_combo_text_if_present(self.top_profile_combo, sections.get("top_chord", sections.get("bottom_chord")))
        self.set_combo_text_if_present(self.purlin_span_type_combo, sections.get("purlin_span_type"))
        try:
            self.extra_purlin_rows = max(0, int(sections.get("extra_purlin_rows", getattr(self, "extra_purlin_rows", 0))))
        except (TypeError, ValueError):
            self.extra_purlin_rows = 0
        self.update_extra_purlin_rows_label()
        self.set_combo_text_if_present(self.wall_girt_span_type_combo, sections.get("wall_girt_span_type"))
        self.set_combo_text_if_present(self.web_profile_combo, sections.get("webs_posts"))
        self.set_combo_text_if_present(
            self.left_canopy_top_profile_combo,
            sections.get("left_canopy_top_chord", sections.get("left_canopy_bottom_chord", sections.get("canopy_top_chord", sections.get("canopy_bottom_chord"))))
        )
        self.set_combo_text_if_present(self.left_canopy_web_profile_combo, sections.get("left_canopy_webs_posts", sections.get("canopy_webs_posts")))
        self.set_combo_text_if_present(self.left_lean_column_profile_combo, sections.get("left_lean_to_column", sections.get("lean_to_column")))
        self.set_combo_text_if_present(
            self.right_canopy_top_profile_combo,
            sections.get("right_canopy_top_chord", sections.get("right_canopy_bottom_chord", sections.get("canopy_top_chord", sections.get("canopy_bottom_chord"))))
        )
        self.set_combo_text_if_present(self.right_canopy_web_profile_combo, sections.get("right_canopy_webs_posts", sections.get("canopy_webs_posts")))
        self.set_combo_text_if_present(self.right_lean_column_profile_combo, sections.get("right_lean_to_column", sections.get("lean_to_column")))

        costing = data.get("costing", {})
        self.shs_cost_input.setText(str(costing.get("shs_rate_per_kg", COSTING_DEFAULTS["shs_rate_per_kg"])))
        self.doubled_chord_cost_input.setText(str(costing.get(
            "doubled_chord_rate_per_kg", COSTING_DEFAULTS["doubled_chord_rate_per_kg"]
        )))
        self.ub_cost_input.setText(str(costing.get("ub_rate_per_kg", COSTING_DEFAULTS["ub_rate_per_kg"])))
        self.wb_cost_input.setText(str(costing.get("wb_rate_per_kg", COSTING_DEFAULTS["wb_rate_per_kg"])))
        self.purlin_cost_input.setText(str(costing.get("purlin_rate_per_kg", COSTING_DEFAULTS["purlin_rate_per_kg"])))

        finishes = data.get("finishes", {})
        self.set_combo_text_if_present(self.roof_cladding_profile_combo, finishes.get("roof_profile", DEFAULT_ROOF_CLADDING_PROFILE))

        foundations = data.get("foundations", {})
        self.foundation_bearing_input.setText(str(foundations.get("allowable_bearing_kpa", self.foundation_bearing_input.text())))
        self.foundation_skin_friction_input.setText(str(foundations.get("skin_friction_kpa", self.foundation_skin_friction_input.text())))
        if foundations.get("use_skin_friction_for_bearing") is not None:
            self.foundation_skin_bearing_checkbox.setChecked(bool(foundations.get("use_skin_friction_for_bearing")))

        storage_loads = data.get("storage_loads", {})
        if storage_loads:
            self.storage_enabled_checkbox.setChecked(bool(storage_loads.get("enabled", self.storage_enabled_checkbox.isChecked())))
            self.set_combo_text_if_present(self.storage_load_path_combo, storage_loads.get("load_path_mode"))
            self.set_combo_text_if_present(self.storage_panel_support_combo, storage_loads.get("panel_support_mode"))
            self.storage_material_input.setText(str(storage_loads.get("material", self.storage_material_input.text())))
            self.storage_density_input.setText(str(storage_loads.get("bulk_density_kn_m3", self.storage_density_input.text())))
            legacy_k = storage_loads.get("lateral_pressure_coefficient")
            if storage_loads.get("internal_friction_angle_deg") is not None:
                self.storage_phi_input.setText(str(storage_loads.get("internal_friction_angle_deg")))
            elif legacy_k is not None:
                try:
                    legacy_phi = 2.0 * (45.0 - math.degrees(math.atan(math.sqrt(max(float(legacy_k), 0.0)))))
                    self.storage_phi_input.setText(f"{legacy_phi:.3f}")
                except (TypeError, ValueError):
                    pass
            self.storage_beta_input.setText(str(storage_loads.get("backfill_slope_deg", self.storage_beta_input.text())))
            self.storage_height_input.setText(str(storage_loads.get("stored_height_m", self.storage_height_input.text())))
            rail_height_value = storage_loads.get("rail_height_mm", storage_loads.get("beam_spacing_mm", self.storage_rail_height_input.text()))
            if storage_loads.get("bottom_height_mm") is not None:
                try:
                    rail_height_value = float(rail_height_value) + float(storage_loads.get("bottom_height_mm"))
                except (TypeError, ValueError):
                    pass
            self.storage_rail_height_input.setText(metres_from_mm_text(rail_height_value, self.storage_rail_height_input.text()))
            if storage_loads.get("left_wall") is not None:
                self.storage_left_wall_checkbox.setChecked(bool(storage_loads.get("left_wall")))
            if storage_loads.get("right_wall") is not None:
                self.storage_right_wall_checkbox.setChecked(bool(storage_loads.get("right_wall")))
            if storage_loads.get("front_wall") is not None:
                self.storage_front_wall_checkbox.setChecked(bool(storage_loads.get("front_wall")))
            if storage_loads.get("back_wall") is not None:
                self.storage_back_wall_checkbox.setChecked(bool(storage_loads.get("back_wall")))
            if storage_loads.get("reverse_direction") is not None:
                self.storage_reverse_direction_checkbox.setChecked(bool(storage_loads.get("reverse_direction")))
            self.set_combo_text_if_present(self.storage_rail_profile_combo, storage_loads.get("rail_profile"))
            self.set_combo_text_if_present(self.storage_stub_profile_combo, storage_loads.get("stub_profile"))
            self.storage_panel_fc_input.setText(str(storage_loads.get("panel_concrete_f_c_mpa", self.storage_panel_fc_input.text())))
            self.storage_panel_reo_fy_input.setText(str(storage_loads.get("panel_reo_f_y_mpa", self.storage_panel_reo_fy_input.text())))
            self.storage_panel_cover_input.setText(str(storage_loads.get("panel_cover_mm", self.storage_panel_cover_input.text())))
            self.storage_panel_deflection_limit_input.setText(str(storage_loads.get("panel_deflection_limit_ratio", self.storage_panel_deflection_limit_input.text())))
            self.storage_panel_stiffness_factor_input.setText(str(storage_loads.get("panel_deflection_stiffness_factor", self.storage_panel_stiffness_factor_input.text())))
            self.storage_panel_crack_min_thickness_input.setText(str(storage_loads.get("panel_crack_min_thickness_mm", self.storage_panel_crack_min_thickness_input.text())))
            self.storage_panel_crack_max_spacing_input.setText(str(storage_loads.get("panel_crack_max_spacing_mm", self.storage_panel_crack_max_spacing_input.text())))
            self.storage_panel_crack_stress_limit_input.setText(str(storage_loads.get("panel_crack_service_stress_limit_mpa", self.storage_panel_crack_stress_limit_input.text())))
            self.storage_panel_crack_width_limit_input.setText(str(storage_loads.get("panel_crack_width_limit_mm", self.storage_panel_crack_width_limit_input.text())))
            self.storage_panel_shrinkage_input.setText(str(storage_loads.get("panel_shrinkage_microstrain", self.storage_panel_shrinkage_input.text())))
            self.storage_panel_creep_input.setText(str(storage_loads.get("panel_creep_coefficient", self.storage_panel_creep_input.text())))
            self.storage_panel_thicknesses_input.setText(str(storage_loads.get("panel_thicknesses_mm", self.storage_panel_thicknesses_input.text())))
            if storage_loads.get("panel_auto_thickness") is not None:
                self.storage_panel_auto_thickness_checkbox.setChecked(bool(storage_loads.get("panel_auto_thickness")))
            if storage_loads.get("panel_selected_thickness_mm") is not None:
                try:
                    self.storage_panel_selected_thickness_input.setValue(int(round(float(storage_loads.get("panel_selected_thickness_mm")))))
                except (TypeError, ValueError):
                    pass
            self.storage_panel_mesh_input.setText(str(storage_loads.get("panel_mesh_options", self.storage_panel_mesh_input.text())))
            self.storage_panel_bars_input.setText(str(storage_loads.get("panel_bar_diameters_mm", self.storage_panel_bars_input.text())))
            self.storage_panel_spacings_input.setText(str(storage_loads.get("panel_bar_spacings_mm", self.storage_panel_spacings_input.text())))
            self.storage_uls_factor_input.setText(str(storage_loads.get("uls_factor", self.storage_uls_factor_input.text())))
            self.storage_sls_factor_input.setText(str(storage_loads.get("sls_factor", self.storage_sls_factor_input.text())))
        self.storage_panel_concrete_rate_input.setText(str(costing.get(
            "storage_concrete_rate_per_m3",
            storage_loads.get("panel_concrete_rate_per_m3", self.storage_panel_concrete_rate_input.text()),
        )))
        self.storage_panel_reo_rate_input.setText(str(costing.get(
            "storage_reo_rate_per_kg",
            storage_loads.get("panel_reo_rate_per_kg", self.storage_panel_reo_rate_input.text()),
        )))

        crane_beam = data.get("crane_beam", {})
        self.set_combo_text_if_present(self.crane_beam_profile_combo, crane_beam.get("beam_profile"))
        pfc_cap_value = crane_beam.get("pfc_cap")
        self.set_combo_text_if_present(self.crane_beam_pfc_cap_combo, pfc_cap_value)
        if pfc_cap_value and clean_profile_label(self.crane_beam_pfc_cap_combo.currentText()) != clean_profile_label(pfc_cap_value):
            match = re.search(r"(\d+)", str(pfc_cap_value))
            if match:
                self.set_combo_text_if_present(self.crane_beam_pfc_cap_combo, f"{match.group(1)} PFC")
        self.set_combo_text_if_present(self.crane_beam_rail_profile_combo, crane_beam.get("rail_profile"))
        self.crane_beam_span_input.setText(metres_from_mm_text(crane_beam.get("span_mm"), self.crane_beam_span_input.text()))
        self.crane_beam_wheelbase_input.setText(metres_from_mm_text(crane_beam.get("wheelbase_mm"), self.crane_beam_wheelbase_input.text()))
        self.crane_beam_load_height_input.setText(metres_from_mm_text(crane_beam.get("load_height_mm"), self.crane_beam_load_height_input.text()))
        self.crane_beam_self_weight_input.setText(str(crane_beam.get("beam_self_weight_kn_m", self.crane_beam_self_weight_input.text())))
        self.crane_beam_rail_weight_input.setText(str(crane_beam.get("rail_self_weight_kn_m", self.crane_beam_rail_weight_input.text())))
        self.crane_beam_rail_bte_input.setText(str(crane_beam.get("rail_bte_mm", self.crane_beam_rail_bte_input.text())))
        self.crane_beam_rail_k_input.setText(str(crane_beam.get("rail_k", self.crane_beam_rail_k_input.text())))
        self.crane_beam_pmax21_input.setText(str(crane_beam.get("pmax_21_kn", self.crane_beam_pmax21_input.text())))
        self.crane_beam_pmax22_input.setText(str(crane_beam.get("pmax_22_kn", self.crane_beam_pmax22_input.text())))
        self.crane_beam_pmin11_input.setText(str(crane_beam.get("pmin_11_kn", self.crane_beam_pmin11_input.text())))
        self.crane_beam_pmin12_input.setText(str(crane_beam.get("pmin_12_kn", self.crane_beam_pmin12_input.text())))
        self.crane_beam_pstatic21_input.setText(str(crane_beam.get("pstatic_21_kn", self.crane_beam_pstatic21_input.text())))
        self.crane_beam_pstatic22_input.setText(str(crane_beam.get("pstatic_22_kn", self.crane_beam_pstatic22_input.text())))
        self.crane_beam_pstatic_min11_input.setText(str(crane_beam.get("pstatic_min_11_kn", self.crane_beam_pstatic_min11_input.text())))
        self.crane_beam_pstatic_min12_input.setText(str(crane_beam.get("pstatic_min_12_kn", self.crane_beam_pstatic_min12_input.text())))
        self.crane_beam_phb21_input.setText(str(crane_beam.get("phb_21_kn", self.crane_beam_phb21_input.text())))
        self.crane_beam_phb22_input.setText(str(crane_beam.get("phb_22_kn", self.crane_beam_phb22_input.text())))
        self.crane_beam_phi2_input.setText(str(crane_beam.get("phi_2_kn", self.crane_beam_phi2_input.text())))
        self.crane_beam_buffer_input.setText(str(crane_beam.get("buffer_force_kn") or ""))
        self.crane_beam_ix_input.setText(str(crane_beam.get("ix_x10_6_mm4", self.crane_beam_ix_input.text())))
        self.crane_beam_iy_top_input.setText(str(crane_beam.get("iy_top_x10_6_mm4", self.crane_beam_iy_top_input.text())))
        self.crane_beam_iy_oblique_input.setText(str(crane_beam.get("iy_oblique_x10_6_mm4", self.crane_beam_iy_oblique_input.text())))
        self.crane_beam_zx_input.setText(str(crane_beam.get("zx_x10_3_mm3", self.crane_beam_zx_input.text())))
        self.crane_beam_zy_input.setText(str(crane_beam.get("zy_x10_3_mm3", self.crane_beam_zy_input.text())))
        self.crane_beam_depth_input.setText(str(crane_beam.get("depth_mm", self.crane_beam_depth_input.text())))
        self.crane_beam_flange_thickness_input.setText(str(crane_beam.get("flange_thickness_mm", self.crane_beam_flange_thickness_input.text())))
        self.crane_beam_fy_input.setText(str(crane_beam.get("fy_mpa", self.crane_beam_fy_input.text())))
        if crane_beam.get("include_self_weight_in_deflection") is not None:
            self.crane_beam_include_sw_deflection_checkbox.setChecked(bool(crane_beam.get("include_self_weight_in_deflection")))
        if crane_beam:
            # Section properties are derived from the selected beam/channel. Recalculate them
            # so older presets do not keep stale bare-beam stiffness or catalogue Fy values.
            self.apply_crane_beam_profile_defaults()

        loads = data.get("loads", {})
        if loads.get("bay_size_mm") is not None:
            self.load_bay_size_input.setText(metres_from_mm_text(loads.get("bay_size_mm"), self.load_bay_size_input.text()))
        elif loads.get("bay_size_m") is not None:
            self.load_bay_size_input.setText(str(loads.get("bay_size_m")))
        self.sync_crane_beam_span_to_bay_size()
        self.g_load_input.setText(self.downward_roof_load_text(loads.get("g_kpa", self.g_load_input.text())))
        self.q_load_input.setText(self.downward_roof_load_text(loads.get("q_kpa", self.q_load_input.text())))
        self.solar_load_input.setText(self.downward_roof_load_text(loads.get("solar_kpa", self.solar_load_input.text())))
        self.fire_service_load_input.setText(self.downward_roof_load_text(loads.get("fire_service_kpa", self.fire_service_load_input.text())))
        self.hvac_load_input.setText(self.downward_roof_load_text(loads.get("hvac_kpa", self.hvac_load_input.text())))
        self.other_load_input.setText(self.downward_roof_load_text(loads.get("other_kpa", self.other_load_input.text())))
        self.crane_rating_input.setText(str(loads.get("crane_rating_t", self.crane_rating_input.text())))
        if not crane_beam:
            self.apply_crane_rating_to_crane_beam_inputs()
        self.set_combo_text_if_present(self.snow_region_combo, loads.get("snow_region"))
        self.snow_ahd_height_input.setText(str(loads.get("snow_ahd_height_m", self.snow_ahd_height_input.text())))
        placements = loads.get("roof_load_placements", {})
        legacy_placement = {
            "side": loads.get("roof_load_side"),
            "from_percent": loads.get("roof_load_from_percent"),
            "to_percent": loads.get("roof_load_to_percent"),
        }
        for prefix in ["g", "q", "solar", "other"]:
            self.apply_roof_load_placement_data(prefix, placements.get(prefix) or legacy_placement)
        self.left_wall_load_input.setText(str(loads.get("left_wall_kn_m", self.left_wall_load_input.text())))
        self.right_wall_load_input.setText(str(loads.get("right_wall_kn_m", self.right_wall_load_input.text())))
        self.set_combo_text_if_present(self.load_combination_combo, loads.get("load_combination"))

        wind = data.get("wind", {})
        self.set_combo_text_if_present(self.wind_region_combo, wind.get("region"))
        self.set_combo_text_if_present(self.importance_combo, wind.get("importance_level"))
        self.set_combo_text_if_present(self.terrain_combo, wind.get("terrain_category"))
        self.wind_orientation_selector.setCurrentText(wind.get("orientation", self.wind_orientation_selector.currentText()))
        wall_enclosure = wind.get("wall_enclosure")
        if wall_enclosure is None:
            wall_enclosure = self.wall_states_from_legacy_frame_type(wind.get("frame_type", self.frame_type_from_wall_states()))
        self.envelope_plan_selector.set_wall_states(wall_enclosure)
        self.set_combo_text_if_present(self.internal_pressure_combo, wind.get("internal_pressure_mode"))
        self.apply_frame_load_basis(wind.get("frame_load_basis"))
        self.set_combo_text_if_present(self.wind_case_combo, wind.get("single_wind_case"))
        self.building_length_input.setText(str(wind.get("building_length_m", self.building_length_input.text())))
        self.apply_bays_from_preset(geometry, loads, wind)
        self.wind_reduction_input.setText(str(wind.get("additional_pressure_factor", self.wind_reduction_input.text())))
        self.update_derived_frame_type_label()

        display = data.get("display", {})
        self.set_combo_text_if_present(self.result_combo, display.get("diagram_view"))
        self.set_combo_text_if_present(self.load_view_mode_combo, display.get("load_view_mode"))
        normalized_single_load = self.normalized_single_load_label(display.get("single_load"))
        self.set_combo_text_if_present(self.single_load_combo, normalized_single_load)
        self.pending_single_load_text = normalized_single_load
        if display.get("second_order_analysis") is not None:
            self.second_order_checkbox.setChecked(bool(display.get("second_order_analysis")))
        if display.get("show_governing_deflection") is not None:
            self.governing_deflection_overlay_checkbox.setChecked(bool(display.get("show_governing_deflection")))
        self.deflection_scale_input.setText(str(display.get("deflection_scale", self.deflection_scale_input.text())))
        self.diagram_scale_input.setText(str(display.get("diagram_scale", self.diagram_scale_input.text())))
        self.set_combo_text_if_present(self.ui_theme_combo, display.get("ui_theme", self.ui_theme_combo.currentText()))
        override = display.get("eave_deflection_limit_override")
        try:
            override = float(override) if override not in (None, "") else None
        except (TypeError, ValueError):
            override = None
        self.eave_deflection_limit_override = override if override is not None and override > 0.0 else None
        serviceability_overrides = display.get("serviceability_limit_overrides", {})
        self.serviceability_limit_overrides = {}
        if isinstance(serviceability_overrides, dict):
            for key, value in serviceability_overrides.items():
                if key in SERVICEABILITY_LIMIT_DEFAULTS:
                    self.set_serviceability_limit_value(key, value)
        self.apply_ui_theme()
        self.calculate_storage_load_report()
        self.update_web_section_visibility()
        self.update_internal_column_visibility()
        self.update_current_canopy_visibility()
        self.update_eave_restraint_visibility()
        self.update_bracing_layout_summary()
        self.refresh_settings_menu_checks()
        if self.future_canopy_design:
            from portalcalc.service.contract import calculation_web_selection_state
            selections = calculation_web_selection_state(data)
            self._panel_count_override = self.future_canopy_design["main_truss_panel_count"]
            self.auto_web_profile_assignments = selections["assignments"]
            self.auto_web_joint_doubler_sides = selections["doubler_sides"]
            self.auto_web_assignment_basis = {
                group: self.web_assignment_basis_signature(group) for group in selections["assignments"]
            }
        del preset_signal_blockers
        self._defer_generate_and_solve = previous_defer_solve
        if not previous_defer_solve:
            self._deferred_generate_and_solve = False
