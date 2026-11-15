"""Bulk-storage wall loads: rail, stub and panel design."""

import math
import re

import numpy as np

from portalcalc.frame.common import mm_from_metres_text
from portalcalc.member_checks import check_member, E_STEEL_MPA, MemberActions
from portalcalc.storage_loads import (
    calculate_storage_loads,
    design_storage_panel,
    is_centre_stub_storage_mode,
    is_continuous_stub_panel_mode,
    storage_summary_lines,
    StorageLoadInputs,
    StoragePanelDesignInputs,
)


class StorageMixin:
    """Storage rail/stub/panel selection, checks and report text."""

    def storage_rail_profile_allowed(self, profile):
        text = f"{profile.name} {profile.group_name} {profile.shape_type}".upper()
        return (
            re.search(r"(^|[^A-Z])UB([^A-Z]|$)", text) is not None
            or re.search(r"(^|[^A-Z])PFC([^A-Z]|$)", text) is not None
            or "CHANNEL" in text
        )

    def storage_stub_profile_allowed(self, profile):
        text = f"{profile.name} {profile.group_name} {profile.shape_type}".upper()
        return re.search(r"(^|[^A-Z])UB([^A-Z]|$)", text) is not None

    def selected_storage_rail_profile(self):
        if self.storage_rail_profile_combo.count() <= 0:
            return None
        return self.catalog.get(self.storage_rail_profile_combo.currentText())

    def selected_storage_stub_profile(self):
        if self.storage_stub_profile_combo.count() <= 0:
            return None
        return self.catalog.get(self.storage_stub_profile_combo.currentText())

    def storage_rail_design_for_profile(self, profile, storage_results):
        if profile is None or not storage_results.rail_loads:
            return None
        rail = storage_results.rail_loads[0]
        span_mm = max(storage_results.inputs.bay_spacing_m * 1000.0, 1.0)
        uls_factor = max(storage_results.inputs.uls_factor, 0.0)
        sls_factor = max(storage_results.inputs.sls_factor, 0.0)
        w_uls_kn_m = rail.rail_line_load_kn_m * uls_factor
        w_sls_kn_m = rail.rail_line_load_kn_m * sls_factor
        moment_knm = rail.rail_bending_moment_knm * uls_factor
        if rail.rail_load_type == "centre_point":
            p_uls_kn = rail.stub_head_reaction_kn * uls_factor
            p_sls_kn = rail.stub_head_reaction_kn * sls_factor
            shear_kn = p_uls_kn / 2.0
        else:
            p_uls_kn = 0.0
            p_sls_kn = 0.0
            shear_kn = w_uls_kn_m * storage_results.inputs.bay_spacing_m / 2.0
        inertia_mm4 = max(profile.Iyp, profile.Izp)
        if rail.rail_load_type == "centre_point":
            deflection_mm = p_sls_kn * 1000.0 * span_mm**3 / (48.0 * E_STEEL_MPA * max(inertia_mm4, 1.0))
        else:
            deflection_mm = 5.0 * w_sls_kn_m * span_mm**4 / (384.0 * E_STEEL_MPA * max(inertia_mm4, 1.0))
        deflection_limit_mm = span_mm / self.serviceability_limit_value("storage_steel")
        check = check_member(
            profile.check_properties(),
            MemberActions(
                shear_kn=shear_kn,
                moment_knm=moment_knm,
                effective_length_y_mm=span_mm,
                effective_length_z_mm=span_mm / 2.0,
                moment_modifier=1.0,
            ),
        )
        bending = check.checks["bending"]
        shear = check.checks["shear"]
        capacity_utilization = max(bending.ratio, shear.ratio)
        deflection_utilization = deflection_mm / deflection_limit_mm if deflection_limit_mm > 1e-9 else math.inf
        return {
            "profile": profile,
            "span_mm": span_mm,
            "w_uls_kn_m": w_uls_kn_m,
            "w_sls_kn_m": w_sls_kn_m,
            "p_uls_kn": p_uls_kn,
            "p_sls_kn": p_sls_kn,
            "load_type": rail.rail_load_type,
            "moment_knm": moment_knm,
            "shear_kn": shear_kn,
            "inertia_mm4": inertia_mm4,
            "deflection_mm": deflection_mm,
            "deflection_limit_mm": deflection_limit_mm,
            "deflection_ratio": span_mm / deflection_mm if deflection_mm > 1e-9 else math.inf,
            "bending_capacity_knm": bending.capacity,
            "shear_capacity_kn": shear.capacity,
            "capacity_utilization": capacity_utilization,
            "deflection_utilization": deflection_utilization,
            "passes": capacity_utilization <= 1.0 and deflection_utilization <= 1.0,
            "check": check,
        }

    def storage_stub_design_for_profile(self, profile, storage_results):
        if profile is None or not storage_results.rail_loads or not is_centre_stub_storage_mode(storage_results.inputs):
            return None
        rail = storage_results.rail_loads[0]
        span_mm = max(storage_results.inputs.rail_height_mm, 1.0)
        span_m = span_mm / 1000.0
        height_m = max(storage_results.inputs.stored_height_m, 0.0)
        half_bay_m = max(rail.panel_span_m, 0.0)
        base_pressure_kpa = max(storage_results.base_pressure_kpa, 0.0)
        stub_tributary_factor = 1.25 if is_continuous_stub_panel_mode(storage_results.inputs) else 1.0
        uls_factor = max(storage_results.inputs.uls_factor, 0.0)
        sls_factor = max(storage_results.inputs.sls_factor, 0.0)
        if span_m <= 0.0 or height_m <= 0.0 or half_bay_m <= 0.0 or base_pressure_kpa <= 0.0:
            return None

        top_m = max(height_m, span_m)
        stations = {0.0, height_m, span_m}
        station_count = 160
        stations.update(top_m * index / station_count for index in range(station_count + 1))
        stations.update(min(height_m, span_m) * index / station_count for index in range(station_count + 1))
        y_nodes_m = sorted(y for y in stations if -1e-9 <= y <= top_m + 1e-9)

        def q_kn_m(y_m, factor):
            if y_m > height_m:
                return 0.0
            return base_pressure_kpa * max(1.0 - y_m / max(height_m, 1e-9), 0.0) * half_bay_m * stub_tributary_factor * factor

        def total_load_kn(factor):
            return base_pressure_kpa * half_bay_m * height_m * 0.5 * stub_tributary_factor * factor

        def top_reaction_kn(factor):
            return storage_results.rail_reaction_per_wall_kn * factor

        def base_reaction_kn(factor):
            return storage_results.base_reaction_per_wall_kn * factor

        def load_integral_to(y_m, factor):
            y = min(max(y_m, 0.0), height_m)
            return base_pressure_kpa * half_bay_m * stub_tributary_factor * factor * (height_m * y - y * y / 2.0) / max(height_m, 1e-9)

        def moment_at(y_m, factor):
            loaded_height = min(max(y_m, 0.0), height_m)
            load_moment = base_pressure_kpa * half_bay_m * stub_tributary_factor * factor * (
                y_m * (loaded_height - loaded_height**2 / (2.0 * max(height_m, 1e-9)))
                - (loaded_height**2 / 2.0 - loaded_height**3 / (3.0 * max(height_m, 1e-9)))
            )
            moment = base_reaction_kn(factor) * y_m
            if y_m > span_m:
                moment += top_reaction_kn(factor) * (y_m - span_m)
            return moment - load_moment

        moment_stations = set(y_nodes_m)
        q0_uls = base_pressure_kpa * half_bay_m * stub_tributary_factor * uls_factor
        if q0_uls > 0.0:
            for reaction, low, high in [(base_reaction_kn(uls_factor), 0.0, min(span_m, height_m)),
                                        (base_reaction_kn(uls_factor) + top_reaction_kn(uls_factor), span_m, height_m)]:
                disc = 1.0 - 2.0 * reaction / (q0_uls * height_m)
                if disc >= 0.0:
                    for y in (height_m * (1.0 - math.sqrt(disc)), height_m * (1.0 + math.sqrt(disc))):
                        if low <= y <= high:
                            moment_stations.add(y)
        moment_candidates = []
        for y_m in moment_stations:
            moment_candidates.append(abs(moment_at(y_m, uls_factor)))
        moment_knm = max(moment_candidates) if moment_candidates else 0.0
        shear_kn = abs(base_reaction_kn(uls_factor))
        for y_m in y_nodes_m:
            shear = base_reaction_kn(uls_factor) - load_integral_to(y_m, uls_factor)
            if y_m >= span_m:
                shear_kn = max(shear_kn, abs(shear + top_reaction_kn(uls_factor)))
            if y_m <= span_m:
                shear_kn = max(shear_kn, abs(shear))

        inertia_mm4 = max(profile.Iyp, profile.Izp)
        y_nodes_mm = [y * 1000.0 for y in y_nodes_m]
        node_count = len(y_nodes_mm)
        dof_count = 2 * node_count
        stiffness = np.zeros((dof_count, dof_count))
        loads = np.zeros(dof_count)
        ei = E_STEEL_MPA * max(inertia_mm4, 1.0)
        for index, (y1, y2) in enumerate(zip(y_nodes_mm[:-1], y_nodes_mm[1:])):
            length_mm = y2 - y1
            if length_mm <= 1e-9:
                continue
            k = ei / length_mm**3 * np.array([
                [12.0, 6.0 * length_mm, -12.0, 6.0 * length_mm],
                [6.0 * length_mm, 4.0 * length_mm**2, -6.0 * length_mm, 2.0 * length_mm**2],
                [-12.0, -6.0 * length_mm, 12.0, -6.0 * length_mm],
                [6.0 * length_mm, 2.0 * length_mm**2, -6.0 * length_mm, 4.0 * length_mm**2],
            ])
            q1 = q_kn_m(y1 / 1000.0, sls_factor)
            q2 = q_kn_m(y2 / 1000.0, sls_factor)
            f = np.array([
                length_mm * (7.0 * q1 + 3.0 * q2) / 20.0,
                length_mm**2 * (3.0 * q1 + 2.0 * q2) / 60.0,
                length_mm * (3.0 * q1 + 7.0 * q2) / 20.0,
                -length_mm**2 * (2.0 * q1 + 3.0 * q2) / 60.0,
            ])
            dofs = [2 * index, 2 * index + 1, 2 * (index + 1), 2 * (index + 1) + 1]
            for a in range(4):
                loads[dofs[a]] += f[a]
                for b in range(4):
                    stiffness[dofs[a], dofs[b]] += k[a, b]

        rail_index = min(range(node_count), key=lambda i: abs(y_nodes_mm[i] - span_mm))
        fixed_dofs = {0, 2 * rail_index}
        free_dofs = [index for index in range(dof_count) if index not in fixed_dofs]
        displacements = np.zeros(dof_count)
        if free_dofs:
            kff = stiffness[np.ix_(free_dofs, free_dofs)]
            ff = loads[free_dofs]
            displacements[free_dofs] = np.linalg.solve(kff, ff)
        lateral_deflections = [abs(displacements[2 * index]) for index in range(node_count)]
        max_deflection_index = max(range(node_count), key=lambda index: lateral_deflections[index])

        check = check_member(
            profile.check_properties(),
            MemberActions(
                shear_kn=shear_kn,
                moment_knm=moment_knm,
                effective_length_y_mm=span_mm,
                effective_length_z_mm=span_mm,
                moment_modifier=1.0,
            ),
        )
        bending = check.checks["bending"]
        shear = check.checks["shear"]
        deflection_limit_mm = span_mm / self.serviceability_limit_value("storage_steel")
        deflection_mm = lateral_deflections[max_deflection_index]
        capacity_utilization = max(bending.ratio, shear.ratio)
        deflection_utilization = deflection_mm / deflection_limit_mm if deflection_limit_mm > 1e-9 else math.inf
        return {
            "profile": profile,
            "span_mm": span_mm,
            "height_m": height_m,
            "panel_span_m": half_bay_m,
            "total_uls_kn": total_load_kn(uls_factor),
            "base_reaction_uls_kn": base_reaction_kn(uls_factor),
            "head_reaction_uls_kn": top_reaction_kn(uls_factor),
            "moment_knm": moment_knm,
            "shear_kn": shear_kn,
            "inertia_mm4": inertia_mm4,
            "deflection_mm": deflection_mm,
            "deflection_level_mm": y_nodes_mm[max_deflection_index],
            "deflection_limit_mm": deflection_limit_mm,
            "deflection_ratio": span_mm / deflection_mm if deflection_mm > 1e-9 else math.inf,
            "bending_capacity_knm": bending.capacity,
            "shear_capacity_kn": shear.capacity,
            "capacity_utilization": capacity_utilization,
            "deflection_utilization": deflection_utilization,
            "passes": capacity_utilization <= 1.0 and deflection_utilization <= 1.0,
            "check": check,
        }

    def update_storage_rail_status_labels(self, design):
        if not hasattr(self, "storage_rail_capacity_status_label"):
            return
        if design is None:
            for label in [self.storage_rail_capacity_status_label, self.storage_rail_deflection_status_label]:
                label.setText("-")
                label.setStyleSheet("")
            return
        for label, ratio in [
            (self.storage_rail_capacity_status_label, design["capacity_utilization"]),
            (self.storage_rail_deflection_status_label, design["deflection_utilization"]),
        ]:
            label.setText(self.utilization_status_text(ratio))
            label.setStyleSheet("color: #2e7d32; font-weight: 600;" if ratio <= 1.0 else "color: #c62828; font-weight: 600;")

    def update_storage_stub_status_labels(self, design):
        if not hasattr(self, "storage_stub_capacity_status_label"):
            return
        if design is None:
            for label in [self.storage_stub_capacity_status_label, self.storage_stub_deflection_status_label]:
                label.setText("-")
                label.setStyleSheet("")
            return
        for label, ratio in [
            (self.storage_stub_capacity_status_label, design["capacity_utilization"]),
            (self.storage_stub_deflection_status_label, design["deflection_utilization"]),
        ]:
            label.setText(self.utilization_status_text(ratio))
            label.setStyleSheet("color: #2e7d32; font-weight: 600;" if ratio <= 1.0 else "color: #c62828; font-weight: 600;")

    def storage_rail_design_lines(self, design):
        if design is None:
            return []
        ratio_text = "Span / inf" if math.isinf(design["deflection_ratio"]) else f"Span / {design['deflection_ratio']:.0f}"
        if design.get("load_type") == "centre_point":
            load_lines = [
                f"{'Design point load P*':<24}: {design['p_uls_kn']:>8.3f} kN at midspan",
                f"{'Service point load P':<24}: {design['p_sls_kn']:>8.3f} kN at midspan",
            ]
        else:
            load_lines = [
                f"{'Design line load w*':<24}: {design['w_uls_kn_m']:>8.3f} kN/m",
                f"{'Service line load w':<24}: {design['w_sls_kn_m']:>8.3f} kN/m",
            ]
        return [
            "",
            "STORAGE HORIZONTAL RAIL",
            f"{'Section':<24}: {design['profile'].name}",
            f"{'Span L':<24}: {design['span_mm']:>8.0f} mm",
            *load_lines,
            f"{'Moment M*':<24}: {design['moment_knm']:>8.3f} kNm",
            f"{'Shear V*':<24}: {design['shear_kn']:>8.3f} kN",
            f"{'phi Mb / phi Vv':<24}: {design['bending_capacity_knm']:>8.3f} kNm / {design['shear_capacity_kn']:>8.3f} kN",
            f"{'Capacity utilization':<24}: {design['capacity_utilization']:>8.3f}",
            f"{'I used for deflection':<24}: {design['inertia_mm4'] / 1e6:>8.3f} x10^6 mm4",
            f"{'Deflection':<24}: {design['deflection_mm']:>8.3f} mm <= {design['deflection_limit_mm']:>8.3f} mm (L/{self.serviceability_limit_value('storage_steel'):.0f}, {ratio_text})",
            f"{'Deflection utilization':<24}: {design['deflection_utilization']:>8.3f}",
            "Effective length assumes a flybrace at midspan for member bending capacity.",
        ]

    def storage_stub_design_lines(self, design):
        if design is None:
            return []
        ratio_text = "Span / inf" if math.isinf(design["deflection_ratio"]) else f"Span / {design['deflection_ratio']:.0f}"
        return [
            "",
            "STORAGE VERTICAL STUB",
            f"{'Section':<24}: {design['profile'].name}",
            f"{'Span L':<24}: {design['span_mm']:>8.0f} mm",
            f"{'Panel span each side':<24}: {design['panel_span_m']:>8.3f} m",
            f"{'Design lateral load':<24}: {design['total_uls_kn']:>8.3f} kN total triangular",
            f"{'Base / head reaction*':<24}: {design['base_reaction_uls_kn']:>8.3f} / {design['head_reaction_uls_kn']:>8.3f} kN",
            f"{'Moment M*':<24}: {design['moment_knm']:>8.3f} kNm",
            f"{'Shear V*':<24}: {design['shear_kn']:>8.3f} kN",
            f"{'phi Mb / phi Vv':<24}: {design['bending_capacity_knm']:>8.3f} kNm / {design['shear_capacity_kn']:>8.3f} kN",
            f"{'Capacity utilization':<24}: {design['capacity_utilization']:>8.3f}",
            f"{'I used for deflection':<24}: {design['inertia_mm4'] / 1e6:>8.3f} x10^6 mm4",
            f"{'Deflection':<24}: {design['deflection_mm']:>8.3f} mm at y={design['deflection_level_mm']:>7.0f} mm <= {design['deflection_limit_mm']:>8.3f} mm (L/{self.serviceability_limit_value('storage_steel'):.0f}, {ratio_text})",
            f"{'Deflection utilization':<24}: {design['deflection_utilization']:>8.3f}",
            "Stub is checked as simply supported between footing and horizontal rail.",
        ]

    def auto_select_storage_rail(self):
        try:
            inputs = self.storage_load_inputs_from_ui()
            storage_results = calculate_storage_loads(inputs)
        except ValueError as error:
            self.storage_results_box.setPlainText(f"Input error: {error}")
            return
        selected, passing = self.select_storage_rail_candidate(storage_results)
        if selected is None:
            self.storage_results_box.setPlainText("No UB/PFC storage rail candidates are available in the section catalogue.")
            self.update_storage_rail_status_labels(None)
            return
        self.storage_rail_profile_combo.setCurrentIndex(selected["combo_index"])
        stub_selected, stub_passing = self.select_storage_stub_candidate(storage_results)
        if stub_selected is not None:
            self.storage_stub_profile_combo.setCurrentIndex(stub_selected["combo_index"])
        self.calculate_storage_load_report()
        if not passing:
            self.storage_results_box.append("\nNo passing storage rail found. Showing closest catalogue candidate.")
        else:
            self.storage_results_box.append(
                f"\nAuto rail selection: selected {selected['profile'].name} "
                f"({self.profile_mass_kn_m(selected['profile']):.3f} kN/m), "
                f"capacity util {selected['capacity_utilization']:.2f}, "
                f"deflection util {selected['deflection_utilization']:.2f}."
            )
        if is_centre_stub_storage_mode(storage_results.inputs):
            if stub_selected is None:
                self.storage_results_box.append("\nNo vertical stub candidates are available in the section catalogue.")
            elif not stub_passing:
                self.storage_results_box.append("\nNo passing vertical stub found. Showing closest catalogue candidate.")
            else:
                self.storage_results_box.append(
                    f"\nAuto stub selection: selected {stub_selected['profile'].name} "
                    f"({self.profile_mass_kn_m(stub_selected['profile']):.3f} kN/m), "
                    f"capacity util {stub_selected['capacity_utilization']:.2f}, "
                    f"deflection util {stub_selected['deflection_utilization']:.2f}."
                )

    def select_storage_rail_candidate(self, storage_results):
        candidates = []
        for index in range(self.storage_rail_profile_combo.count()):
            profile = self.catalog.get(self.storage_rail_profile_combo.itemText(index))
            design = self.storage_rail_design_for_profile(profile, storage_results)
            if design is not None:
                design["combo_index"] = index
                candidates.append(design)
        passing = [candidate for candidate in candidates if candidate["passes"]]
        ranked = passing or sorted(
            candidates,
            key=lambda candidate: max(candidate["capacity_utilization"], candidate["deflection_utilization"]),
        )[:1]
        if not ranked:
            return None, False
        selected = sorted(
            ranked,
            key=lambda candidate: (self.profile_mass_kn_m(candidate["profile"]), candidate["profile"].A, candidate["profile"].name),
        )[0]
        return selected, bool(passing)

    def select_storage_stub_candidate(self, storage_results):
        candidates = []
        if not is_centre_stub_storage_mode(storage_results.inputs):
            return None, False
        for index in range(self.storage_stub_profile_combo.count()):
            profile = self.catalog.get(self.storage_stub_profile_combo.itemText(index))
            design = self.storage_stub_design_for_profile(profile, storage_results)
            if design is not None:
                design["combo_index"] = index
                candidates.append(design)
        passing = [candidate for candidate in candidates if candidate["passes"]]
        ranked = passing or sorted(
            candidates,
            key=lambda candidate: max(candidate["capacity_utilization"], candidate["deflection_utilization"]),
        )[:1]
        if not ranked:
            return None, False
        selected = sorted(
            ranked,
            key=lambda candidate: (self.profile_mass_kn_m(candidate["profile"]), candidate["profile"].A, candidate["profile"].name),
        )[0]
        return selected, bool(passing)

    def apply_auto_storage_design(self, storage_results):
        if not storage_results.inputs.enabled:
            self.storage_rail_design = None
            self.storage_stub_design = None
            self.storage_panel_design = None
            return None, None
        rail_design, _ = self.select_storage_rail_candidate(storage_results)
        if rail_design is not None:
            self.storage_rail_profile_combo.blockSignals(True)
            self.storage_rail_profile_combo.setCurrentIndex(rail_design["combo_index"])
            self.storage_rail_profile_combo.blockSignals(False)
        stub_design, _ = self.select_storage_stub_candidate(storage_results)
        if stub_design is not None:
            self.storage_stub_profile_combo.blockSignals(True)
            self.storage_stub_profile_combo.setCurrentIndex(stub_design["combo_index"])
            self.storage_stub_profile_combo.blockSignals(False)
        panel_design = design_storage_panel(storage_results, self.storage_panel_design_inputs_from_ui())
        if panel_design is not None and self.storage_panel_auto_thickness_checkbox.isChecked():
            self.storage_panel_selected_thickness_input.blockSignals(True)
            self.storage_panel_selected_thickness_input.setValue(int(round(panel_design.thickness_mm)))
            self.storage_panel_selected_thickness_input.blockSignals(False)
        self.storage_rail_design = rail_design
        self.storage_stub_design = stub_design
        self.storage_panel_design = panel_design
        return rail_design, panel_design

    def storage_load_inputs_from_ui(self):
        return StorageLoadInputs(
            enabled=self.storage_enabled_checkbox.isChecked(),
            load_path_mode=self.storage_load_path_combo.currentText(),
            panel_support_mode=self.storage_panel_support_combo.currentText(),
            material_name=self.storage_material_input.text().strip() or "Stored material",
            bulk_density_kn_m3=self.numeric_input(self.storage_density_input, "Storage bulk density"),
            internal_friction_angle_deg=self.numeric_input(self.storage_phi_input, "Storage internal friction angle"),
            backfill_slope_deg=self.numeric_input(self.storage_beta_input, "Storage backfill slope"),
            stored_height_m=self.numeric_input(self.storage_height_input, "Stored material height"),
            bay_spacing_m=self.bay_size_m_from_ui(),
            rail_height_mm=mm_from_metres_text(self.storage_rail_height_input.text()),
            left_wall=self.storage_left_wall_checkbox.isChecked(),
            right_wall=self.storage_right_wall_checkbox.isChecked(),
            reverse_direction=self.storage_reverse_direction_checkbox.isChecked(),
            uls_factor=self.numeric_input(self.storage_uls_factor_input, "Storage ULS factor"),
            sls_factor=self.numeric_input(self.storage_sls_factor_input, "Storage SLS factor"),
        )

    def storage_number_list_from_input(self, widget):
        values = []
        for part in widget.text().replace(";", ",").split(","):
            try:
                value = float(part.strip())
            except ValueError:
                continue
            if value > 0.0:
                values.append(value)
        return tuple(values)

    def storage_mesh_options_from_input(self, widget):
        values = []
        for part in widget.text().replace(";", ",").split(","):
            tokens = [token.strip() for token in part.split(":")]
            if len(tokens) not in {3, 4}:
                continue
            try:
                area = float(tokens[1])
                diameter = float(tokens[2])
                spacing = float(tokens[3]) if len(tokens) == 4 else 200.0
            except ValueError:
                continue
            if tokens[0] and area > 0.0 and diameter > 0.0 and spacing > 0.0:
                values.append((tokens[0], area, diameter, spacing))
        return tuple(values)

    def storage_panel_design_inputs_from_ui(self):
        thicknesses = self.storage_number_list_from_input(self.storage_panel_thicknesses_input)
        if not self.storage_panel_auto_thickness_checkbox.isChecked():
            thicknesses = (float(self.storage_panel_selected_thickness_input.value()),)
        mesh_options = self.storage_mesh_options_from_input(self.storage_panel_mesh_input)
        bars = self.storage_number_list_from_input(self.storage_panel_bars_input)
        spacings = self.storage_number_list_from_input(self.storage_panel_spacings_input)
        return StoragePanelDesignInputs(
            concrete_f_c_mpa=self.numeric_input(self.storage_panel_fc_input, "Panel concrete strength"),
            reo_f_y_mpa=self.numeric_input(self.storage_panel_reo_fy_input, "Panel reinforcement yield strength"),
            cover_mm=self.numeric_input(self.storage_panel_cover_input, "Panel cover"),
            deflection_limit_ratio=self.numeric_input(self.storage_panel_deflection_limit_input, "Panel deflection limit ratio"),
            deflection_stiffness_factor=self.numeric_input(self.storage_panel_stiffness_factor_input, "Panel stiffness factor"),
            crack_min_thickness_mm=self.numeric_input(self.storage_panel_crack_min_thickness_input, "Panel crack minimum thickness"),
            crack_max_spacing_mm=self.numeric_input(self.storage_panel_crack_max_spacing_input, "Panel crack maximum spacing"),
            crack_service_stress_limit_mpa=self.numeric_input(self.storage_panel_crack_stress_limit_input, "Panel crack service stress limit"),
            crack_max_width_mm=self.numeric_input(self.storage_panel_crack_width_limit_input, "Panel crack width limit"),
            shrinkage_strain=self.numeric_input(self.storage_panel_shrinkage_input, "Panel shrinkage strain") * 1e-6,
            creep_coefficient=self.numeric_input(self.storage_panel_creep_input, "Panel creep coefficient"),
            thicknesses_mm=thicknesses or (125.0, 150.0, 175.0, 200.0, 225.0, 250.0, 275.0, 300.0),
            mesh_options=mesh_options or (
                ("SL62", 141.0, 6.0, 200.0),
                ("SL72", 179.0, 6.75, 200.0),
                ("SL82", 227.0, 7.6, 200.0),
                ("SL92", 290.0, 8.6, 200.0),
                ("SL102", 354.0, 9.5, 200.0),
                ("SL81", 454.0, 7.6, 100.0),
                ("RL718", 358.0, 6.75, 100.0),
                ("RL818", 454.0, 7.6, 100.0),
                ("RL918", 581.0, 8.6, 100.0),
                ("RL1018", 709.0, 9.5, 100.0),
                ("RL1118", 899.0, 10.7, 100.0),
                ("RL1218", 1112.0, 11.9, 100.0),
            ),
            bar_diameters_mm=bars or (12.0, 16.0, 20.0),
            spacings_mm=spacings or (300.0, 250.0, 200.0, 150.0, 125.0, 100.0),
        )

    def storage_panel_design_lines(self, design):
        if design is None:
            return [
                "",
                "STORAGE PANEL PRELIMINARY REO",
                "No passing panel thickness/reinforcement option found for the current candidate lists.",
            ]

        def face_line(face):
            return (
                f"{face.label:<8}: M*={face.demand_knm_per_m:>7.3f} kNm/m at y={face.demand_level_mm:>7.0f} mm, "
                f"{face.reinforcement_label}, "
                f"As={face.as_provided_mm2_m:>6.0f} mm2/m, phi Mu={face.capacity_knm_per_m:>7.3f} kNm/m, "
                f"fs={face.service_steel_stress_mpa:>6.1f}/{face.service_stress_limit_mpa:.0f} MPa, "
                f"s={face.spacing_mm:.0f}/{face.crack_spacing_limit_mm:.0f} mm, "
                f"w={face.crack_width_mm:.3f}/{face.crack_width_limit_mm:.2f} mm, util={face.utilization:.2f}"
            )

        return [
            "",
            "STORAGE PANEL PRELIMINARY REO",
            f"{'Selected thickness':<24}: {design.thickness_mm:>8.0f} mm",
            f"{'f_c / fy':<24}: {design.concrete_f_c_mpa:>8.1f} / {design.reo_f_y_mpa:>8.1f} MPa",
            f"{'Cover / d approx':<24}: {design.cover_mm:>8.0f} / {design.effective_depth_mm:>8.0f} mm",
            f"{'Crack control gates':<24}: t >= {self.storage_panel_crack_min_thickness_input.text()} mm, spacing <= {self.storage_panel_crack_max_spacing_input.text()} mm, fs <= {self.storage_panel_crack_stress_limit_input.text()} MPa, w <= {self.storage_panel_crack_width_limit_input.text()} mm",
            f"{'Shrinkage / creep':<24}: {self.storage_panel_shrinkage_input.text()} x10^-6 / {self.storage_panel_creep_input.text()}",
            f"{'Service deflection':<24}: {design.deflection_mm:>8.3f} mm at y={design.deflection_level_mm:>7.0f} mm <= {design.deflection_limit_mm:>8.3f} mm",
            f"{'Ec / Igross / Ieff':<24}: {design.elastic_modulus_mpa:>8.0f} MPa / {design.gross_i_mm4_m / 1e6:>8.3f} / {design.effective_i_mm4_m / 1e6:>8.3f} x10^6 mm4/m",
            face_line(design.positive_face),
            face_line(design.negative_face),
            "Capacity uses a 1 m strip, rectangular stress block 0.85 f'c, phi 0.80, and a starter minimum steel ratio of 0.0025.",
            "Crack width follows the visible AS 3600 Cl 9.5/8.6.2.3 style calculation: w = sr.max * edm.",
            "RL mesh areas use the longitudinal direction; orient the RL long wires in the checked span direction.",
        ]

    def storage_cost_rate_from_input(self, widget, default_value):
        try:
            value = float(widget.text())
        except ValueError:
            return default_value
        return max(value, 0.0)

    def storage_panel_material_summary(self, results, design, building_length_m=None):
        if design is None or not results.inputs.enabled:
            return None
        try:
            length_m = max(float(self.building_length_input.text()) if building_length_m is None else float(building_length_m), 0.0)
        except (TypeError, ValueError):
            length_m = 0.0
        wall_count = max(results.active_wall_count, 0)
        panel_height_m = max(results.inputs.stored_height_m, results.inputs.rail_height_mm / 1000.0, 0.0)
        wall_area_m2 = length_m * panel_height_m * wall_count
        concrete_volume_m3 = wall_area_m2 * design.thickness_mm / 1000.0
        reo_area_mm2_m = design.positive_face.as_provided_mm2_m + design.negative_face.as_provided_mm2_m
        reo_kg_m2 = reo_area_mm2_m * 0.00785
        reo_total_kg = wall_area_m2 * reo_kg_m2
        concrete_rate = self.storage_cost_rate_from_input(self.storage_panel_concrete_rate_input, 280.0)
        reo_rate = self.storage_cost_rate_from_input(self.storage_panel_reo_rate_input, 2.50)
        concrete_cost = concrete_volume_m3 * concrete_rate
        reo_cost = reo_total_kg * reo_rate
        return {
            "wall_count": wall_count,
            "building_length_m": length_m,
            "panel_height_m": panel_height_m,
            "wall_area_m2": wall_area_m2,
            "concrete_volume_m3": concrete_volume_m3,
            "reo_area_mm2_m": reo_area_mm2_m,
            "reo_kg_m2": reo_kg_m2,
            "reo_total_kg": reo_total_kg,
            "concrete_rate": concrete_rate,
            "reo_rate": reo_rate,
            "concrete_cost": concrete_cost,
            "reo_cost": reo_cost,
            "total_cost": concrete_cost + reo_cost,
        }

    def storage_panel_costing_lines(self, results, design):
        summary = self.storage_panel_material_summary(results, design)
        if not summary:
            return []
        return [
            "",
            "STORAGE PANEL MATERIAL COST",
            f"{'Wall area':<24}: {summary['wall_area_m2']:>8.2f} m2 ({summary['wall_count']} wall(s) x {summary['building_length_m']:.2f} m x {summary['panel_height_m']:.2f} m)",
            f"{'Concrete volume':<24}: {summary['concrete_volume_m3']:>8.2f} m3 x ${summary['concrete_rate']:.2f}/m3 = ${summary['concrete_cost']:,.0f}",
            f"{'Reinforcement':<24}: {summary['reo_total_kg']:>8.1f} kg ({summary['reo_kg_m2']:.2f} kg/m2) x ${summary['reo_rate']:.2f}/kg = ${summary['reo_cost']:,.0f}",
            f"{'Panel material total':<24}: ${summary['total_cost']:>8,.0f}",
            "Storage panel costing excludes laps, chairs, waste, embeds, forming, footing/slab, placing, and labour.",
        ]

    def calculate_storage_load_report(self):
        try:
            inputs = self.storage_load_inputs_from_ui()
            results = calculate_storage_loads(inputs)
            panel_design_inputs = self.storage_panel_design_inputs_from_ui()
        except ValueError as error:
            self.storage_results_box.setPlainText(f"Input error: {error}")
            self.update_storage_rail_status_labels(None)
            self.update_storage_stub_status_labels(None)
            return
        lines = storage_summary_lines(results, factor=1.0)
        if not lines:
            lines = ["Storage loads are disabled."]
            self.update_storage_rail_status_labels(None)
            self.update_storage_stub_status_labels(None)
            self.storage_results = results
            self.storage_rail_design = None
            self.storage_stub_design = None
            self.storage_panel_design = None
        else:
            rail_design = self.storage_rail_design_for_profile(self.selected_storage_rail_profile(), results)
            lines.extend(self.storage_rail_design_lines(rail_design))
            self.update_storage_rail_status_labels(rail_design)
            stub_design = self.storage_stub_design_for_profile(self.selected_storage_stub_profile(), results)
            lines.extend(self.storage_stub_design_lines(stub_design))
            self.update_storage_stub_status_labels(stub_design)
            panel_design = design_storage_panel(results, panel_design_inputs)
            if panel_design is not None and self.storage_panel_auto_thickness_checkbox.isChecked():
                self.storage_panel_selected_thickness_input.blockSignals(True)
                self.storage_panel_selected_thickness_input.setValue(int(round(panel_design.thickness_mm)))
                self.storage_panel_selected_thickness_input.blockSignals(False)
            lines.extend(self.storage_panel_design_lines(panel_design))
            lines.extend(self.storage_panel_costing_lines(results, panel_design))
            self.storage_results = results
            self.storage_rail_design = rail_design
            self.storage_stub_design = stub_design
            self.storage_panel_design = panel_design
            lines.append("")
            lines.append("Directions assume material stored inside the building: left wall acts -X, right wall acts +X.")
            lines.append("Panel +M and -M are signed face envelopes for reinforcement sizing; map the sign convention to the selected panel face detail.")
            if is_centre_stub_storage_mode(results.inputs):
                lines.append("Side-running panels use the selected break/continuous support mode over two half-bay spans. The stub head load is carried by the rail as a midspan point load.")
                lines.append("Each rail end receives half the stub head load per bay; an internal portal column receives both adjacent rail-end reactions plus the direct triangular panel reaction. The stub base reaction transfers to its footing/slab.")
            else:
                lines.append("The frame load is the horizontal rail reaction; the base reaction is assumed to transfer into the footing/slab.")
        self.storage_results_box.setPlainText("\n".join(lines))
