"""End-wall and longitudinal bracing, the adopted 3D sway restraint and propped end frames."""

import math
from dataclasses import replace

from portalcalc.bracing.restraint import (
    adoption_record as bracing_restraint_adoption_record,
    OPTION as BRACING_3D_RESTRAINT_OPTION,
)
from portalcalc.end_wall import design_end_wall_mullions, mullion_layout, roof_height_mm
from portalcalc.frame.common import (
    EAVE_BRACING_PATH_CALIBRATION_FACTOR,
    END_WALL_BRACING_SIZE_OPTIONS,
    mm_from_metres_text,
)
from portalcalc.frame.preliminary_bracing import (
    approximate_purlin_weak_axis_inertia_mm4,
    BracingInputs,
    calculate_preliminary_bracing,
)
from portalcalc.wind import (
    active_cpi_for_case,
    calculate_wind,
    CPE_CASE_OPTIONS,
    CPI_CASE_OPTIONS,
    external_pressure_factor,
    internal_pressure_factor,
    wind_pressure_scale,
)


class BracingMixin:
    """Bracing design that feeds, or is fed by, the 2D frame."""

    def selected_end_wall_brace_size(self):
        return self.selected_brace_size(self.end_wall_brace_size_combo)

    def selected_brace_size(self, combo):
        data = combo.currentData()
        if isinstance(data, dict):
            return {
                "label": data.get("label", combo.currentText()),
                "area_mm2": float(data.get("area_mm2", 0.0)),
                "description": data.get("description", combo.currentText()),
            }
        label = combo.currentText()
        for option_label, area_mm2, description in END_WALL_BRACING_SIZE_OPTIONS:
            if option_label == label:
                return {"label": option_label, "area_mm2": area_mm2, "description": description}
        return {"label": "16 DIA ROD", "area_mm2": math.pi * 16.0**2 / 4.0, "description": "16 mm round bar"}

    def end_wall_bracing_eave_spring(self, building_length_m, bay_size_m, brace_area_mm2, end_count=1, brace_label="Brace"):
        bay_size_m = max(bay_size_m, 1e-9)
        building_length_m = max(building_length_m, 0.0)
        bay_size_mm = bay_size_m * 1000.0
        brace_area_mm2 = max(brace_area_mm2, 0.0)
        end_count = 2 if end_count >= 2 else 1
        diagonal_length_mm = math.sqrt(2.0) * bay_size_mm
        base_spring_kn_mm = (200000.0 * brace_area_mm2 / diagonal_length_mm) * 0.5 / 1000.0
        calibrated_base_spring_kn_mm = base_spring_kn_mm * EAVE_BRACING_PATH_CALIBRATION_FACTOR

        path_m = building_length_m / 2.0
        squares_to_end = max(1, math.ceil(path_m / bay_size_m))
        one_end_spring_kn_mm = calibrated_base_spring_kn_mm / squares_to_end
        effective_spring_kn_mm = one_end_spring_kn_mm * end_count

        return {
            "modelled": True,
            "end_count": end_count,
            "squares_to_end": squares_to_end,
            "path_m": path_m,
            "near_path_m": path_m,
            "far_path_m": path_m,
            "near_squares": squares_to_end,
            "far_squares": squares_to_end,
            "brace_label": brace_label,
            "brace_diameter_mm": math.sqrt(4.0 * brace_area_mm2 / math.pi) if brace_area_mm2 > 0.0 else 0.0,
            "brace_area_mm2": brace_area_mm2,
            "diagonal_length_mm": diagonal_length_mm,
            "base_spring_kn_mm": base_spring_kn_mm,
            "calibrated_base_spring_kn_mm": calibrated_base_spring_kn_mm,
            "calibration_factor": EAVE_BRACING_PATH_CALIBRATION_FACTOR,
            "one_end_spring_kn_mm": one_end_spring_kn_mm,
            "effective_spring_kn_mm": effective_spring_kn_mm,
        }

    def end_wall_bracing_design_pressures(self, wind_inputs):
        """Return the governing net end-wall pressures and their source cases."""
        pitch_rad = math.radians(abs(float(wind_inputs.roof_pitch_deg)))
        roof_rise_m = wind_inputs.building_width_m * math.tan(pitch_rad) * (0.5 if wind_inputs.roof_shape == "Gable" else 1.0)
        projected_area_m2 = wind_inputs.building_width_m * wind_inputs.eave_height_m + 0.5 * wind_inputs.building_width_m * roof_rise_m
        governing = {
            "ultimate_pressure_kpa": 0.0,
            "ultimate_case": "-",
            "service_pressure_kpa": 0.0,
            "service_case": "-",
        }
        for cpe_case in [case for case in CPE_CASE_OPTIONS if case.startswith("End")]:
            source_direction = self.wind_source_direction_for_case(wind_inputs.orientation, cpe_case)
            result = calculate_wind(replace(wind_inputs, orientation=source_direction))
            for cpi_case in CPI_CASE_OPTIONS:
                cpi = active_cpi_for_case(result, cpi_case, cpe_case)
                for surface_label, cpe, surface in (
                    ("windward end", 0.7, "windward_wall"),
                    ("leeward end", result.leeward_wall_cpe, "leeward_wall"),
                ):
                    external_factor = external_pressure_factor(result, surface, max(projected_area_m2, 0.01))
                    internal_factor = internal_pressure_factor(result, cpi)
                    for pressure_type, pressure_key, case_key in (
                        ("ultimate", "ultimate_pressure_kpa", "ultimate_case"),
                        ("serviceability", "service_pressure_kpa", "service_case"),
                    ):
                        scale = wind_pressure_scale(result, pressure_type)
                        net_pressure = abs(
                            cpe * result.wu_kn_m2 * scale * external_factor
                            - cpi * result.wu_kn_m2 * scale * internal_factor
                        )
                        if net_pressure > governing[pressure_key]:
                            governing[pressure_key] = net_pressure
                            governing[case_key] = f"{pressure_type.title()}: {cpe_case}, {cpi_case}, {surface_label}"
        return governing

    def current_bracing_layout_geometry(self):
        from portalcalc.bracing.layout import LayoutGeometry
        stations = self.bay_stations_mm()
        geometry = LayoutGeometry.create(mm_from_metres_text(self.span_input.text()), stations[-1],
                                         mm_from_metres_text(self.eave_input.text()), float(self.pitch_input.text()),
                                         self.current_roof_shape(), bay_stations_mm=stations)
        spacing = self.end_wall_spacing_override_mm()
        if spacing is not None:
            if not math.isfinite(spacing) or spacing <= 0:
                raise ValueError("End-wall panel spacing must be finite and positive")
            spaces = max(1, math.ceil(geometry.span_mm / spacing))
            if spaces > 100:
                raise ValueError("The layout editor supports up to 100 end-wall panels")
            values = geometry.to_dict()
            values["end_wall_stations_mm"] = [geometry.span_mm * i / spaces for i in range(spaces + 1)]
            geometry = LayoutGeometry.from_dict(values)
        canopies = self.bracing_layout_canopies()
        if canopies and geometry.roof_shape == "Gable":
            # Open canopies are braceable 3D faces (skillion canopies are
            # rejected by the 3D analysis with an explanation instead).
            values = geometry.to_dict()
            values["canopies"] = canopies
            geometry = LayoutGeometry.from_dict(values)
        from portalcalc.bracing.analysis_desktop import crane_wall_tier_mm
        values = geometry.to_dict()
        tier = crane_wall_tier_mm(self)
        if tier is not None and tier < min(geometry.roof_z(0), geometry.roof_z(geometry.span_mm)):
            # Crane side walls: two X's split at the runway level.
            values["wall_tier_z_mm"] = tier
        node = (getattr(self, "eave_node_basis_record", None) or {}).get("node_mm")
        if node and node > 0:
            # Tall-panel rule (two X's when height / width > 1.73) on the brace
            # height: to the top chord node, not the cladding pitching point.
            values["tier_eave_mm"] = round(float(node), 1)
        return LayoutGeometry.from_dict(values)

    def bracing_layout_canopies(self):
        """Nominal open canopies for the 3D bracing layout: plan width and fall."""
        canopies = []
        for side in ("left", "right"):
            type_combo = getattr(self, f"{side}_canopy_type_combo", None)
            if type_combo is None or type_combo.currentText() != "Canopy":
                continue
            try:
                width = mm_from_metres_text(getattr(self, f"{side}_canopy_length_input").text())
                pitch = float(getattr(self, f"{side}_canopy_pitch_input").text())
            except (TypeError, ValueError):
                continue
            if width > 0.0:
                canopies.append({"side": side, "width_mm": width, "drop_mm": math.tan(math.radians(pitch)) * width})
        return canopies

    def update_bracing_layout_summary(self):
        if not hasattr(self, "bracing_layout_summary_label"):
            return
        from portalcalc.bracing.layout import BracingLayout
        if self.bracing_location_layout is None:
            text = "No exclusions or pinned locations saved."
        else:
            try:
                layout = BracingLayout.from_dict(self.bracing_location_layout)
                text = f"{len(layout.blocked)} blocked panels; {len(layout.pinned)} pinned braces."
                if layout.geometry != self.current_bracing_layout_geometry():
                    text += " Dimensions changed - review the saved locations."
            except (TypeError, ValueError, OverflowError) as error:
                text = f"Saved locations need review: {error}"
        restraint = self.bracing_3d_restraint_summary()
        self.bracing_layout_summary_label.setText(text + ("\n" + restraint if restraint else ""))

    def bracing_3d_restraint_summary(self):
        """One line tracing the 3D frame restraint: adopted, in use, or why not."""
        note = getattr(self, "bracing_3d_restraint_note", None)
        record = getattr(self, "bracing_3d_restraint", None)
        if note:
            # Informational: the 2D frame is then checked without restraint credit.
            text = f"No 2D frame restraint credit from this bracing: {note}."
            if record:
                return text + f" Earlier adopted restraint kept: {record['stiffness_kn_mm']:.3f} kN/mm."
            return text + " The 2D frame is checked unrestrained; Optimise frame + bracing compares restraining layouts."
        if not record:
            return ""
        text = f"2D frame restraint adopted from 3D: {record['stiffness_kn_mm']:.3f} kN/mm (frame {record['governing_frame']})."
        if self.eave_x_restraint_combo.currentText() != BRACING_3D_RESTRAINT_OPTION:
            return text + f" Not in use: set End-wall portal restraint to {BRACING_3D_RESTRAINT_OPTION}."
        state = (getattr(self, "bracing_3d_restraint_status", None) or {}).get("state")
        return text + {"applied": " In use.", "recheck": " In use - RECHECK 3D (sections, loads or wind changed).",
                       "stale": " STALE - bracing changed; not applied.", None: ""}.get(state, "")

    def adopt_bracing_3d_restraint(self, result):
        """Record the frame restraint from a checked 3D result for the 2D portal.

        Uses the saved bracing layout/settings, so call after they are stored.
        Returns (record, reason); an unadoptable result leaves any earlier
        record in place, which its fingerprint will mark stale if it no longer
        matches the bracing.
        """
        record, reason = bracing_restraint_adoption_record(
            result, self.bracing_location_layout, self.bracing_3d_settings, self.collect_preset_data())
        self.bracing_3d_restraint_note = reason
        if record is not None and record != self.bracing_3d_restraint:
            self.bracing_3d_restraint = record
            if self.eave_x_restraint_combo.currentText() == BRACING_3D_RESTRAINT_OPTION:
                self.generate_and_solve()
            self.record_model_snapshot()
        self.update_bracing_layout_summary()
        return record, reason

    def calculate_longitudinal_bracing_design(self, wind_inputs, span_mm, eave_height_mm, pitch_deg, roof_shape, bay_size_m):
        if not self.longitudinal_bracing_enabled_checkbox.isChecked():
            self.longitudinal_bracing_design = None
            self.update_longitudinal_bracing_results()
            return

        pressure_cases = self.end_wall_bracing_design_pressures(wind_inputs)
        end_layout = mullion_layout(
            span_mm,
            eave_height_mm,
            pitch_deg,
            roof_shape,
            spacing_override_mm=self.end_wall_spacing_override_mm(),
        )
        panel_stations = [span_mm * index / end_layout["spaces"] for index in range(end_layout["spaces"] + 1)]
        if roof_shape == "Gable":
            panel_stations.append(span_mm / 2.0)
        panel_stations = sorted(set(panel_stations))
        roof_panel_areas_m2 = []
        roof_panel_widths_mm = []
        for x1, x2 in zip(panel_stations[:-1], panel_stations[1:]):
            h1 = roof_height_mm(x1, span_mm, eave_height_mm, pitch_deg, roof_shape)
            h2 = roof_height_mm(x2, span_mm, eave_height_mm, pitch_deg, roof_shape)
            roof_panel_areas_m2.append((x2 - x1) * (h1 + h2) / 2.0 / 1_000_000.0)
            roof_panel_widths_mm.append(x2 - x1)
        mullion_by_station = {
            round(float(item["station_mm"]), 6): item
            for item in end_layout.get("mullions", [])
        }
        roof_node_lines = []
        for station_mm in panel_stations:
            station_key = round(float(station_mm), 6)
            height_mm = roof_height_mm(station_mm, span_mm, eave_height_mm, pitch_deg, roof_shape)
            if abs(station_mm) <= 1e-6:
                line_type = "eave_strut"
                label = "Left eave strut" if roof_shape == "Gable" else "Low-eave strut"
            elif abs(station_mm - span_mm) <= 1e-6:
                line_type = "eave_strut"
                label = "Right eave strut" if roof_shape == "Gable" else "High-eave strut"
            elif station_key in mullion_by_station:
                mullion = mullion_by_station[station_key]
                line_type = "mullion_tie"
                label = f"Mullion tie M{mullion.get('index', len(roof_node_lines))}"
            else:
                line_type = "ridge_strut"
                label = "Ridge strut"
            roof_node_lines.append({
                "type": line_type,
                "label": label,
                "station_mm": station_mm,
                "height_mm": height_mm,
            })
        purlin_match = (self.purlin_check or {}).get("match", {}) or {}
        purlin_section = purlin_match.get("section") if purlin_match.get("status") == "OK" else None
        strut_candidates = []
        if purlin_section:
            area_mm2 = float(purlin_match.get("area_mm2") or self.metroll_section_area_mm2(purlin_section))
            i_min_mm4 = approximate_purlin_weak_axis_inertia_mm4(purlin_section, area_mm2)
            base = {"area_mm2": area_mm2, "i_min_mm4": i_min_mm4, "fy_mpa": 450.0}
            strut_candidates.extend([
                {**base, "label": f"Existing {purlin_section} purlin", "count": 1.0, "available_fraction": 0.5, "arrangement": "existing_purlin"},
                {**base, "label": f"Additional {purlin_section} purlin", "count": 1.0, "available_fraction": 1.0, "arrangement": "additional_purlin"},
                {**base, "label": f"Back-to-back {purlin_section} purlins", "count": 2.0, "available_fraction": 1.0, "arrangement": "back_to_back_purlins"},
            ])
        hollow_profiles = sorted(
            [*self.catalog.shs_profiles(), *self.catalog.rhs_profiles()],
            key=lambda profile: (profile.A, profile.name),
        )
        for profile in hollow_profiles:
            strut_candidates.append({
                "label": f"{profile.name} strut",
                "area_mm2": profile.A,
                "i_min_mm4": min(profile.Iyp, profile.Izp),
                "fy_mpa": profile.fy,
                "count": 1.0,
                "available_fraction": 1.0,
                "arrangement": "hollow_section",
            })

        roof_brace = self.selected_brace_size(self.roof_brace_size_combo)
        wall_brace = self.selected_brace_size(self.wall_brace_size_combo)
        self.longitudinal_bracing_design = calculate_preliminary_bracing(BracingInputs(
            span_mm=span_mm,
            eave_height_mm=eave_height_mm,
            roof_pitch_deg=pitch_deg,
            roof_shape=roof_shape,
            bay_width_mm=bay_size_m * 1000.0,
            roof_panel_widths_mm=tuple(roof_panel_widths_mm),
            roof_panel_projected_areas_m2=tuple(roof_panel_areas_m2),
            roof_node_lines=tuple(roof_node_lines),
            wall_side_count=2 if self.longitudinal_wall_sides_combo.currentText() == "Both sides" else 1,
            braced_bays_per_side=self.end_wall_braced_bay_count_input.value(),
            roof_brace_area_mm2=roof_brace["area_mm2"],
            roof_brace_label=roof_brace["label"],
            wall_brace_area_mm2=wall_brace["area_mm2"],
            wall_brace_label=wall_brace["label"],
            ultimate_pressure_kpa=pressure_cases["ultimate_pressure_kpa"],
            service_pressure_kpa=pressure_cases["service_pressure_kpa"],
            ultimate_case=pressure_cases["ultimate_case"],
            service_case=pressure_cases["service_case"],
            strut_candidates=tuple(strut_candidates),
        ))
        self.update_longitudinal_bracing_results()

    def end_wall_spacing_override_mm(self):
        widget = getattr(self, "end_wall_mullion_spacing_override_input", None)
        text = widget.text().strip() if widget is not None else ""
        if not text:
            return None
        value = mm_from_metres_text(text)
        if value <= 0.0:
            raise ValueError("end-wall mullion spacing override must be greater than zero")
        return value

    def calculate_end_wall_design(self, wind_inputs, span_mm, eave_height_mm, pitch_deg, roof_shape):
        end_wall_count = int(bool(wind_inputs.front_wall_clad)) + int(bool(wind_inputs.back_wall_clad))
        spacing_override_mm = self.end_wall_spacing_override_mm()
        preliminary_layout = mullion_layout(
            span_mm,
            eave_height_mm,
            pitch_deg,
            roof_shape,
            spacing_override_mm=spacing_override_mm,
        )
        governing_height_mm = max(
            (item["height_mm"] for item in preliminary_layout["mullions"]),
            default=eave_height_mm,
        )
        mullion_tributary_area_m2 = (
            preliminary_layout["actual_spacing_mm"] * governing_height_mm / 1_000_000.0
        )
        outward_kpa, inward_kpa, service_kpa = self.wall_girt_design_pressures(
            wind_inputs,
            tributary_area_m2=mullion_tributary_area_m2,
        )
        self.end_wall_mullion_design = design_end_wall_mullions(
            span_mm=span_mm,
            eave_height_mm=eave_height_mm,
            pitch_deg=pitch_deg,
            roof_shape=roof_shape,
            outward_uls_kpa=outward_kpa,
            inward_uls_kpa=inward_kpa,
            service_kpa=service_kpa,
            profiles=self.catalog.end_wall_mullion_profiles(),
            selected_profile=self.catalog.profile_by_name.get((self.bracing_3d_settings or {}).get('adopted_mullion_section')),
            spacing_override_mm=spacing_override_mm,
            end_wall_count=end_wall_count,
        )
        self.end_wall_mullion_design['section_source']=('Adopted from the aligned 3D bracing mullion check' if (self.bracing_3d_settings or {}).get('adopted_mullion_section') else 'Automatic main wall-wind sizing')
        if end_wall_count > 0:
            self.end_wall_girt_check = self.wall_girt_check_from_inputs(
                wind_inputs,
                self.end_wall_mullion_design["actual_spacing_mm"] / 1000.0,
                eave_height_mm,
                self.wall_girt_span_type_combo.currentText(),
            )
        else:
            self.end_wall_girt_check = None
        self.update_end_wall_results()

    def update_end_wall_results(self):
        design = self.end_wall_mullion_design or {}
        girt = self.end_wall_girt_check or {}
        if not design:
            self.end_wall_layout_label.setText("Not checked")
            self.end_wall_mullion_check_label.setText("Not checked")
            self.end_wall_girt_check_label.setText("Not checked")
            return

        end_count = int(design.get("end_wall_count", 0) or 0)
        if end_count <= 0:
            self.end_wall_layout_label.setText("No clad front or back end wall selected.")
            self.end_wall_mullion_check_label.setText("NOT REQUIRED - end walls open")
            self.end_wall_girt_check_label.setText("NOT REQUIRED - end walls open")
            self.end_wall_layout_label.setStyleSheet("font-weight: bold;")
            self.end_wall_mullion_check_label.setStyleSheet("font-weight: bold;")
            self.end_wall_girt_check_label.setStyleSheet("font-weight: bold;")
            return

        override = design.get("spacing_override_mm")
        mode_suffix = f" (manual target {override / 1000.0:.3f} m)" if override else ""
        self.end_wall_layout_label.setText(
            f"{design.get('spaces', 0)} equal spaces @ {design.get('actual_spacing_mm', 0.0) / 1000.0:.3f} m; "
            f"{design.get('mullion_count', 0)} intermediate mullion(s) per clad end{mode_suffix}."
        )
        override_warning = bool(override and override > design.get("automatic_max_spacing_mm", 8000.0) + 1e-9)
        self.end_wall_layout_label.setStyleSheet(
            f"font-weight: bold; color: {'#b42318' if override_warning else '#1b7f3a'};"
        )
        mullion_status = design.get("status", "NOT CHECKED")
        selected = design.get("selected") or {}
        mullion_ok = mullion_status in {"OK", "NOT REQUIRED"}
        if selected:
            self.end_wall_mullion_check_label.setText(
                f"{mullion_status} - {selected.get('section', '-')} | strength {selected.get('strength_ratio', float('inf')):.3f}, "
                f"deflection {selected.get('deflection_ratio', float('inf')):.3f}"
                + (' | 3D-adopted' if design.get('section_source','').startswith('Adopted') else '')
            )
            self.end_wall_mullion_check_label.setToolTip(design.get('section_source','')+'. These ratios are the main wind check; the added bracing axial/combined check is shown in the 3D workspace.')
        else:
            self.end_wall_mullion_check_label.setText(
                f"{mullion_status} - {design.get('message', 'No intermediate mullion section required')}"
            )
        self.end_wall_mullion_check_label.setStyleSheet(
            f"font-weight: bold; color: {'#1b7f3a' if mullion_ok else '#b42318'};"
        )

        girt_match = girt.get("match", {})
        girt_status = girt_match.get("status", "NOT CHECKED")
        if girt_status == "OK":
            self.end_wall_girt_check_label.setText(
                f"OK - {self.metroll_section_display(girt_match.get('section', '-'), girt.get('span_type', ''))} "
                f"@ {girt.get('spacing_mm', 0.0):.0f} mm vertical, "
                f"{girt.get('span_mm', 0.0):.0f} mm span"
            )
        else:
            self.end_wall_girt_check_label.setText(f"{girt_status} - {girt_match.get('message', 'No match')}")
        self.end_wall_girt_check_label.setStyleSheet(
            f"font-weight: bold; color: {'#1b7f3a' if girt_status == 'OK' else '#b42318'};"
        )

    def update_longitudinal_bracing_results(self):
        if getattr(self, "longitudinal_bracing_status_label", None) is None:
            return
        design = getattr(self, "longitudinal_bracing_design", None) or {}
        if not self.longitudinal_bracing_enabled_checkbox.isChecked():
            self.longitudinal_bracing_status_label.setText("Disabled")
            self.longitudinal_bracing_status_label.setStyleSheet("font-weight: bold;")
            self.longitudinal_bracing_case_label.setText("No governing end-wind case calculated.")
            self.roof_bracing_load_label.setText("Not calculated")
            self.wall_bracing_load_label.setText("Not calculated")
            self.bracing_strut_result_label.setText(
                "Automatic hierarchy: existing purlin, additional purlin, back-to-back purlins, then SHS/RHS."
            )
            return
        if not design:
            self.longitudinal_bracing_status_label.setText("Enabled - click Generate & Solve.")
            self.longitudinal_bracing_status_label.setStyleSheet("font-weight: bold; color: #9a6700;")
            return
        status = design.get("status", "NOT CHECKED")
        ratio = design.get("governing_capacity_ratio", float("inf"))
        self.longitudinal_bracing_status_label.setText(
            f"{status} - governing utilization {ratio:.3f}"
        )
        self.longitudinal_bracing_status_label.setStyleSheet(
            f"font-weight: bold; color: {'#1b7f3a' if status == 'OK' else '#b42318'};"
        )
        self.longitudinal_bracing_case_label.setText(
            f"{design.get('ultimate_case', '-')} | end-wall resultant {design.get('ultimate_resultant_kn', 0.0):.2f} kN | "
            f"roof reaction {design.get('roof_reaction_uls_kn', 0.0):.2f} kN"
        )
        roof = design.get("roof_uls", {})
        wall = design.get("wall_uls", {})
        self.roof_bracing_load_label.setText(
            f"{design.get('roof_brace_label', '-')} | N* {roof.get('brace_force_kn', 0.0):.2f} kN | "
            f"capacity {roof.get('capacity_kn', 0.0):.2f} kN | utilization {roof.get('capacity_ratio', 0.0):.3f}"
        )
        self.wall_bracing_load_label.setText(
            f"{design.get('wall_brace_label', '-')} | N* {wall.get('brace_force_kn', 0.0):.2f} kN | "
            f"capacity {wall.get('capacity_kn', 0.0):.2f} kN | utilization {wall.get('capacity_ratio', 0.0):.3f}"
        )
        strut_lines = []
        for heading, key in (
            ("Eave struts", "eave_struts"),
            ("Mullion ties", "mullion_ties"),
            ("Ridge struts", "ridge_struts"),
        ):
            items = design.get(key, []) or []
            if not items:
                continue
            strut_lines.append(f"{heading}:")
            for item in items:
                selection = item.get("selection", {})
                strut_lines.append(
                    f"- {item.get('label', '-')} @ x={item.get('station_mm', 0.0) / 1000.0:.3f} m: "
                    f"N* {item.get('demand_kn', 0.0):.2f} kN | {selection.get('label', '-')} | "
                    f"capacity {selection.get('capacity_kn', 0.0):.2f} kN | utilization {selection.get('ratio', 0.0):.3f}"
                )
        self.bracing_strut_result_label.setText("\n".join(strut_lines) if strut_lines else "No strut lines calculated.")

    def propped_end_context(self):
        """The current sizing group's lightweight end frame, if it is one (building-load mode)."""
        if not getattr(self, "building_load_mode", None):
            return None
        model = getattr(self, "building_model_override", None)
        if model is None:
            return None
        gridlines = getattr(self, "building_load_gridlines", None) or model.gridline_ids
        gridline = model.gridlines[model.gridline_index(gridlines[0])]
        frame_type = model.frame_type(gridline.frame_type)
        if frame_type.kind != "propped_end":
            return None
        end_face = "front" if gridline.id == model.gridline_ids[0] else "back"
        return {"model": model, "frame_type": frame_type, "end_face": end_face,
                "end_wall_clad": not model.wall(end_face).fully_open}

    def propped_end_bracing_springs(self, context, brace):
        """End-wall bracing restraint for a propped end frame's eaves.

        One tension diagonal in the end-wall panel between each column and its
        nearest prop: k = E·A·cos²θ / L. It acts on the left eave, or on both
        eaves when the end walls are braced on both sides. Brace forces stay
        with the bracing design (D2a).
        """
        model, frame_type = context["model"], context["frame_type"]
        span, eave = model.main.span_mm, model.main.eave_mm
        area = float(brace.get("area_mm2", 0.0) or 0.0)
        widths = {"left": frame_type.prop_x_mm[0], "right": span - frame_type.prop_x_mm[-1]}
        sides = ("left", "right") if self.end_wall_braced_sides_combo.currentText() == "Both sides" else ("left",)
        springs = {}
        for side in sides:
            width = widths[side]
            diagonal = math.hypot(width, eave)
            cos_theta = width / diagonal
            springs[side] = {"panel_width_mm": width, "diagonal_mm": diagonal, "cos_theta": cos_theta,
                             "spring_kn_mm": 200000.0 * area * cos_theta ** 2 / diagonal / 1000.0}
        return {"modelled": True, "basis": "propped end frame: end-wall bracing panel next to each column",
                "brace_label": brace.get("label"), "brace_area_mm2": area,
                "restraint": "Both eaves" if len(sides) == 2 else "Left eave", "springs": springs}

    def propped_mullion_check(self, profile, envelope):
        """Load-bearing mullion of a propped end frame: frame axial force plus end-wall wind bending.

        Each prop spans from the ground to the rafter, pinned at both ends, with
        the end-wall mullion basis: mid-height flybrace (effective length H/2),
        am 1.75 and H/150 deflection. End-wall pressure acts on its tributary
        width (half the distance to each neighbouring prop or column). The 2D
        envelope's largest compression and largest tension are each combined
        with the full wind bending. This is conservative, because those
        maxima come from different cases.
        """
        context = self.propped_end_context()
        if context is None:
            return None
        from portalcalc.end_wall import mullion_profile_design, roof_height_mm
        model, frame_type = context["model"], context["frame_type"]
        design = getattr(self, "end_wall_mullion_design", None) or {}
        strength_kpa = float(design.get("strength_pressure_kpa", 0.0) or 0.0) if context["end_wall_clad"] else 0.0
        service_kpa = float(design.get("service_kpa", 0.0) or 0.0) if context["end_wall_clad"] else 0.0
        compression = float(((envelope or {}).get("compression_kn") or {}).get("value", 0.0) or 0.0)
        tension = float(((envelope or {}).get("tension_kn") or {}).get("value", 0.0) or 0.0)
        span = model.main.span_mm
        stations = [0.0, *frame_type.prop_x_mm, span]
        governing = None
        for index, x_mm in enumerate(frame_type.prop_x_mm, start=1):
            height = roof_height_mm(x_mm, span, model.main.eave_mm, model.main.pitch_deg, model.main.roof_shape)
            width = (stations[index + 1] - stations[index - 1]) / 2.0
            for axial in (-compression, tension):
                item = mullion_profile_design(profile, height, width, strength_kpa, service_kpa, axial_force_kn=axial)
                if governing is None or item["governing_ratio"] > governing["ratio"]:
                    mode = "deflection" if item["deflection_ratio"] >= item["strength_ratio"] else "axial + bending"
                    governing = {"ratio": item["governing_ratio"], "x_mm": x_mm, "height_mm": height,
                                 "tributary_width_mm": width, "axial_kn": axial, "item": item,
                                 "label": f"Propped mullion ({mode}: frame axial + end-wall wind)"}
        return governing
