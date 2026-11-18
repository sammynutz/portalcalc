"""Secondary steel: roof purlins, wall girts, bridging and the purlin capacity tables."""

import math
import re
from dataclasses import replace

from portalcalc.frame.sections import PurlinLayout
from portalcalc.purlins import tables as purlin_tables
from portalcalc.wind import (
    action_combination_factors,
    active_cpi_for_case,
    calculate_wind,
    CPE_CASE_OPTIONS,
    CPI_CASE_OPTIONS,
    external_pressure_factor,
    internal_pressure_factor,
    local_pressure_dimension_a,
    local_pressure_region_enabled,
    open_canopy_cpi,
    ROOF_LOCAL_PRESSURE_REGIONS,
    roof_local_pressure_factor,
    wind_pressure_scale,
)


# Step 3 of docs/SECONDARY_MEMBERS_PLAN.md: gable-end purlin end zones (thicker
# end-bay gauges and corner infills). On since 2026-11-18; the per-region local
# pressure switches in the wind inputs still turn individual patches off.
ROOF_END_ZONES_ACTIVE = True


class SecondarySteelMixin:
    """Purlin and girt layout, design pressures and capacity checks."""

    def purlin_mid_span_limit_mm(self, wind_region, importance_level):
        if str(wind_region).upper() == "C":
            return 1100.0
        if int(importance_level) >= 2:
            return 1600.0
        return 1700.0

    def wall_girt_spacing_mm(self, wind_region):
        if str(wind_region).upper() == "C":
            return 1100.0
        return 1700.0

    def bridging_label_text(self, label):
        text = str(label or "-").strip()
        match = re.search(r"B(\d+)", text.upper())
        if match:
            rows = int(match.group(1))
            suffix = "row" if rows == 1 else "rows"
            extra = " / side bridging" if "SB" in text.upper() else ""
            return f"{text} ({rows} {suffix}{extra})"
        return text

    # Capacity-table helpers live in purlins/tables.py; these keep the old names.
    def bridging_row_count(self, label):
        return purlin_tables.bridging_row_count(label)

    def purlin_depth_from_section(self, section):
        return purlin_tables.section_depth_mm(section)

    @staticmethod
    def metroll_section_display(section, span_type):
        """Display C sections for simple spans and Z sections for lapped spans."""
        text = str(section or "").strip()
        if not text:
            return text
        prefix = "Z" if str(span_type or "") == "Continuous lapped" else "C"
        return re.sub(r"^(?:C\s*/\s*Z|C|Z)\s*", f"{prefix} ", text, flags=re.IGNORECASE).strip()

    def recommended_purlin_bridging_spacing_mm(self, section):
        return purlin_tables.recommended_bridging_spacing_mm(section)

    def minimum_purlin_bridging_rows(self, section, span_mm):
        return purlin_tables.minimum_bridging_rows(section, span_mm)

    def bridged_strength_capacity(self, row, section, span_mm, demand_kn_m):
        return purlin_tables.bridged_strength_capacity(row, section, span_mm, demand_kn_m)

    def actual_wall_girt_spacing_mm(self, eave_height_mm, wind_region, bottom_girt_height_mm=300.0):
        max_spacing = self.wall_girt_spacing_mm(wind_region)
        if eave_height_mm <= 0.0:
            return max_spacing
        if eave_height_mm <= bottom_girt_height_mm + 1e-9:
            return eave_height_mm
        clad_height_mm = max(eave_height_mm - bottom_girt_height_mm, 0.0)
        spaces = max(1, math.ceil(clad_height_mm / max_spacing))
        return clad_height_mm / spaces

    def profile_depth_mm(self, profile):
        if getattr(profile, "depth", 0.0) and profile.depth > 0.0:
            return profile.depth
        match = re.search(r"(\d+(?:\.\d+)?)", profile.name)
        return float(match.group(1)) if match else 0.0

    def adjust_extra_purlin_rows(self, delta):
        self.extra_purlin_rows = max(0, int(getattr(self, "extra_purlin_rows", 0)) + int(delta))
        # A manual change makes every extra row the user's own minimum.
        self.optimiser_purlin_rows = 0
        self.update_extra_purlin_rows_label()
        self.generate_and_solve()

    def update_extra_purlin_rows_label(self):
        if not getattr(self, "extra_purlin_rows_label", None):
            return
        rows = int(getattr(self, "extra_purlin_rows", 0))
        added = min(int(getattr(self, "optimiser_purlin_rows", 0) or 0), rows)
        text = f"Extra roof purlin rows per slope: {rows}"
        if added:
            text += f" ({added} added by Optimise)"
        self.extra_purlin_rows_label.setText(text)
        if getattr(self, "remove_purlin_row_button", None):
            self.remove_purlin_row_button.setEnabled(rows > 0)

    def purlin_bridging_summary_text(self, purlin_check):
        if not purlin_check:
            return "Not checked"
        match = purlin_check.get("match", {}) or {}
        local_match = purlin_check.get("local_match", {}) or {}
        span_type = purlin_check.get("span_type", "")
        if match.get("status") != "OK" and local_match.get("status") != "OK":
            return "No passing Metroll section selected"
        def bridge_suffix(item):
            rows = int(item.get("bridging_rows", 0) or 0)
            return f"({rows}-row)"
        def part(item):
            if item.get("status") != "OK":
                return "Not passing"
            return f"{self.metroll_section_display(item.get('section', '-'), span_type)} {bridge_suffix(item)}"
        if local_match.get("status") == "OK" and (
            local_match.get("section") != match.get("section")
            or int(local_match.get("bridging_rows", 0) or 0) != int(match.get("bridging_rows", 0) or 0)
        ):
            edge_rows = int(purlin_check.get("edge_rows", 0) or 0)
            return (
                f"{part(match)}; first {edge_rows} edge rows "
                f"{self.metroll_section_display(local_match.get('section', '-'), span_type)} "
                f"{bridge_suffix(local_match)}"
            )
        return part(match if match.get("status") == "OK" else local_match)

    def build_purlin_layout(self, top_nodes, wind_region, importance_level):
        end_limit = 1200.0
        mid_limit = self.purlin_mid_span_limit_mm(wind_region, importance_level)
        if len(top_nodes) < 2:
            return PurlinLayout([], mid_limit, end_limit, mid_limit)

        points = []
        spacings = []

        def point_at_distance(nodes, distance):
            remaining = distance
            for n1, n2 in zip(nodes[:-1], nodes[1:]):
                dx = n2.x - n1.x
                dy = n2.y - n1.y
                length = math.hypot(dx, dy)
                if length <= 1e-9:
                    continue
                if remaining <= length:
                    t = remaining / length
                    return (n1.x + dx * t, n1.y + dy * t)
                remaining -= length
            return (nodes[-1].x, nodes[-1].y)

        def slope_points(nodes):
            lengths = [math.hypot(n2.x - n1.x, n2.y - n1.y) for n1, n2 in zip(nodes[:-1], nodes[1:])]
            slope_length = sum(lengths)
            if slope_length <= 1e-9:
                return []
            extra_rows = int(getattr(self, "extra_purlin_rows", 0))
            if slope_length <= 2.0 * end_limit:
                spaces = max(1, extra_rows + 1)
                stations = [slope_length * i / spaces for i in range(spaces + 1)]
            else:
                middle_length = slope_length - 2.0 * end_limit
                middle_spaces = max(1, math.ceil(middle_length / mid_limit) + extra_rows)
                actual_mid_spacing = middle_length / middle_spaces
                stations = [0.0, end_limit]
                stations.extend(end_limit + actual_mid_spacing * i for i in range(1, middle_spaces))
                stations.extend([slope_length - end_limit, slope_length])
            return [(station, point_at_distance(nodes, station)) for station in sorted(set(round(s, 6) for s in stations))]

        for slope in self.roof_slope_node_sets(top_nodes):
            slope_layout = slope_points(slope)
            points.extend(point for _, point in slope_layout)
            spacings.extend(b - a for (a, _), (b, _) in zip(slope_layout[:-1], slope_layout[1:]))

        unique_points = []
        seen = set()
        for x, y in points:
            key = (round(x, 3), round(y, 3))
            if key not in seen:
                seen.add(key)
                unique_points.append((x, y))

        return PurlinLayout(unique_points, max(spacings or [mid_limit]), end_limit, mid_limit)

    def purlin_capacity_row(self, table, section, span_mm):
        return purlin_tables.capacity_row(table, section, span_mm)

    def metroll_section_area_mm2(self, section):
        return purlin_tables.section_area_mm2(section)

    def metroll_section_weight_kg_m(self, section):
        return purlin_tables.section_weight_kg_m(section)

    def purlin_design_pressures(self, wind_inputs, tributary_area_m2=None, edge_zone=None, return_info=False, wind_pressure="ultimate",
                                underside_cpi=None):
        """Net roof purlin pressures (upward, downward) in kPa.

        ``underside_cpi(result, cpe_case)`` replaces the building's internal
        pressure with an open canopy's underside coefficient (with its own Kci).
        """
        max_upward_kpa = 0.0
        max_downward_kpa = 0.0
        max_local_pressure_factor = 1.0
        local_dimension_a_m = 0.0
        purlin_wind_inputs = replace(wind_inputs, left_canopy_length_m=0.0, right_canopy_length_m=0.0)
        for cpe_case in CPE_CASE_OPTIONS:
            source_direction = self.wind_source_direction_for_case(purlin_wind_inputs.orientation, cpe_case)
            result = calculate_wind(replace(purlin_wind_inputs, orientation=source_direction))
            pressure_scale = wind_pressure_scale(result, wind_pressure)
            roof_length = result.roof_slope_length_m
            result_a_m = local_pressure_dimension_a(result)
            local_dimension_a_m = max(local_dimension_a_m, result_a_m)
            edge_width = result_a_m
            for cpi_case in CPI_CASE_OPTIONS:
                if underside_cpi is None:
                    cpi = active_cpi_for_case(result, cpi_case, cpe_case)
                    kci = internal_pressure_factor(result, cpi)
                else:
                    cpi = underside_cpi(result, cpe_case)
                    kci = action_combination_factors(result, cpi)[1]
                cpi_pressure = cpi * result.wu_kn_m2 * pressure_scale * kci
                for zone in result.roof_zones:
                    zone_overlaps_edge = (
                        zone.distance_from_eave_from_m < edge_width
                        or zone.distance_from_eave_to_m > roof_length - edge_width
                    )
                    zone_overlaps_non_edge = (
                        zone.distance_from_eave_to_m > edge_width
                        and zone.distance_from_eave_from_m < roof_length - edge_width
                    )
                    if edge_zone is True and not zone_overlaps_edge:
                        continue
                    if edge_zone is False and not zone_overlaps_non_edge:
                        continue
                    proximity_m = min(
                        max(zone.distance_from_eave_from_m, 0.0),
                        max(roof_length - zone.distance_from_eave_to_m, 0.0),
                    )
                    local_pressure_factor = 1.0
                    if edge_zone is True and tributary_area_m2 is not None:
                        local_pressure_factor = roof_local_pressure_factor(result, tributary_area_m2, proximity_m)
                    max_local_pressure_factor = max(max_local_pressure_factor, local_pressure_factor)
                    if tributary_area_m2 is None:
                        external_pressures = [
                            zone.pressure_max_kn_m2 * pressure_scale,
                            zone.pressure_min_kn_m2 * pressure_scale,
                        ]
                    else:
                        external_factor = external_pressure_factor(result, "roof", tributary_area_m2)
                        external_pressures = [
                            (-zone.cpe_max) * result.wu_kn_m2 * pressure_scale * external_factor,
                            (-zone.cpe_min) * result.wu_kn_m2 * pressure_scale * external_factor,
                        ]
                    for external_pressure in external_pressures:
                        if external_pressure > 0.0:
                            external_pressure *= local_pressure_factor
                        combined_pressure = external_pressure + cpi_pressure
                        max_upward_kpa = max(max_upward_kpa, combined_pressure)
                        max_downward_kpa = max(max_downward_kpa, -combined_pressure)
        if return_info:
            return max_upward_kpa, max_downward_kpa, {
                "local_pressure_factor": max_local_pressure_factor,
                "local_dimension_a_m": local_dimension_a_m,
            }
        return max_upward_kpa, max_downward_kpa

    def wall_girt_design_pressures(self, wind_inputs, tributary_area_m2=None):
        max_outward_kpa = 0.0
        max_inward_kpa = 0.0
        max_inward_service_kpa = 0.0

        for cpe_case in CPE_CASE_OPTIONS:
            source_direction = self.wind_source_direction_for_case(wind_inputs.orientation, cpe_case)
            result = calculate_wind(replace(wind_inputs, orientation=source_direction))
            for cpi_case in CPI_CASE_OPTIONS:
                cpi = active_cpi_for_case(result, cpi_case, cpe_case)
                for cpe, surface in [(0.7, "windward_wall"), (result.leeward_wall_cpe, "leeward_wall")]:
                    for wind_pressure in ["ultimate", "serviceability"]:
                        design_area_m2 = tributary_area_m2
                        if design_area_m2 is None:
                            design_area_m2 = result.inputs.bay_size_m * result.inputs.eave_height_m
                        external_factor = external_pressure_factor(result, surface, max(float(design_area_m2), 0.01))
                        external = cpe * result.wu_kn_m2 * wind_pressure_scale(result, wind_pressure) * external_factor
                        internal = cpi * result.wu_kn_m2 * wind_pressure_scale(result, wind_pressure) * internal_pressure_factor(result, cpi)
                        combined = external - internal
                        if wind_pressure == "ultimate":
                            max_inward_kpa = max(max_inward_kpa, combined)
                            max_outward_kpa = max(max_outward_kpa, -combined)
                        else:
                            max_inward_service_kpa = max(max_inward_service_kpa, abs(combined))

        return max_outward_kpa, max_inward_kpa, max_inward_service_kpa

    def match_metroll_purlin(self, span_type, span_mm, outward_kn_m, inward_strength_kn_m, inward_service_kn_m, *, include_candidates=False):
        return purlin_tables.match_section(self.metroll_tables, span_type, span_mm, outward_kn_m, inward_strength_kn_m,
                                           inward_service_kn_m, include_candidates=include_candidates)

    def purlin_check_from_inputs(self, purlin_layout, bay_size_m, roof_loads, wind_inputs, span_type):
        if purlin_layout is None or purlin_layout.max_spacing_mm <= 0.0 or bay_size_m <= 0.0:
            return None
        spacing_m = purlin_layout.max_spacing_mm / 1000.0
        span_mm = bay_size_m * 1000.0
        local_area_m2 = spacing_m * bay_size_m
        local_dimension_a_m = local_pressure_dimension_a(wind_inputs)
        edge_rows = max(1, math.ceil(local_dimension_a_m / spacing_m))
        permanent_kpa = abs(roof_loads.permanent_vertical_kpa)
        live_kpa = abs(roof_loads.q_vertical_kpa)
        inward_gravity_uls_kpa = 1.2 * permanent_kpa + 1.5 * live_kpa
        wind_upward_kpa, wind_downward_kpa = self.purlin_design_pressures(wind_inputs, edge_zone=False)
        wind_service_upward_kpa, wind_service_downward_kpa = self.purlin_design_pressures(
            wind_inputs,
            edge_zone=False,
            wind_pressure="serviceability",
        )
        outward_kn_m = wind_upward_kpa * spacing_m
        inward_wind_uls_kpa = 1.2 * permanent_kpa + wind_downward_kpa
        inward_strength_kn_m = max(inward_gravity_uls_kpa, inward_wind_uls_kpa) * spacing_m
        inward_service_kpa = max(wind_service_upward_kpa, wind_service_downward_kpa)
        inward_service_kn_m = inward_service_kpa * spacing_m
        match = self.match_metroll_purlin(span_type, span_mm, outward_kn_m, inward_strength_kn_m, inward_service_kn_m, include_candidates=True)
        local_wind_upward_kpa, local_wind_downward_kpa, local_pressure_info = self.purlin_design_pressures(
            wind_inputs,
            tributary_area_m2=local_area_m2,
            edge_zone=True,
            return_info=True,
        )
        local_wind_service_upward_kpa, local_wind_service_downward_kpa = self.purlin_design_pressures(
            wind_inputs,
            tributary_area_m2=local_area_m2,
            edge_zone=True,
            wind_pressure="serviceability",
        )
        local_outward_kn_m = local_wind_upward_kpa * spacing_m
        local_inward_wind_uls_kpa = 1.2 * permanent_kpa + local_wind_downward_kpa
        local_inward_strength_kn_m = max(inward_gravity_uls_kpa, local_inward_wind_uls_kpa) * spacing_m
        local_inward_service_kpa = max(local_wind_service_upward_kpa, local_wind_service_downward_kpa)
        local_inward_service_kn_m = local_inward_service_kpa * spacing_m
        local_match = self.match_metroll_purlin(span_type, span_mm, local_outward_kn_m, local_inward_strength_kn_m, local_inward_service_kn_m, include_candidates=True)
        from portalcalc.frame.purlin_selection import select_roof_purlin_pair
        edge_rows_total = min(len(purlin_layout.points), 2 * edge_rows)
        general_rows = max(len(purlin_layout.points) - edge_rows_total, 0)
        general_passing = match.pop("passing_candidates", [])
        edge_passing = local_match.pop("passing_candidates", [])
        pair = select_roof_purlin_pair(general_passing, edge_passing, general_rows, edge_rows_total)
        selection = {}
        end_zone = None
        if pair is not None:
            match, local_match, selection = pair
            # Corner infill purlins sit in the eave strip: the edge-row line loads.
            uniform = {"spacing_mm": purlin_layout.max_spacing_mm, "outward_kn_m": local_outward_kn_m,
                       "inward_strength_kn_m": local_inward_strength_kn_m,
                       "inward_service_kn_m": local_inward_service_kn_m}
            match, local_match, selection, end_zone = self.roof_purlin_end_zones(
                purlin_layout, span_mm, span_type, edge_rows, wind_inputs, general_passing, edge_passing,
                match, local_match, selection, uniform)
        elif match.get("status") == "OK" and local_match.get("status") == "OK":
            match = {"status": "NOT OK", "message": "No common Metroll profile satisfies both roof zones, even with different thicknesses."}
            local_match = dict(match)
        return {
            "span_type": span_type,
            "spacing_mm": purlin_layout.max_spacing_mm,
            "purlin_rows": len(purlin_layout.points),
            "extra_rows_per_slope": int(getattr(self, "extra_purlin_rows", 0)),
            "span_mm": span_mm,
            "local_area_m2": local_area_m2,
            "edge_rows": edge_rows,
            "permanent_kpa": permanent_kpa,
            "live_kpa": live_kpa,
            "wind_upward_kpa": wind_upward_kpa,
            "wind_downward_kpa": wind_downward_kpa,
            "wind_service_upward_kpa": wind_service_upward_kpa,
            "wind_service_downward_kpa": wind_service_downward_kpa,
            "outward_kn_m": outward_kn_m,
            "inward_strength_kn_m": inward_strength_kn_m,
            "inward_gravity_uls_kpa": inward_gravity_uls_kpa,
            "inward_wind_uls_kpa": inward_wind_uls_kpa,
            "inward_service_kn_m": inward_service_kn_m,
            "match": match,
            "local_wind_upward_kpa": local_wind_upward_kpa,
            "local_wind_downward_kpa": local_wind_downward_kpa,
            "local_wind_service_upward_kpa": local_wind_service_upward_kpa,
            "local_wind_service_downward_kpa": local_wind_service_downward_kpa,
            "local_outward_kn_m": local_outward_kn_m,
            "local_inward_strength_kn_m": local_inward_strength_kn_m,
            "local_inward_service_kn_m": local_inward_service_kn_m,
            "local_pressure_factor": local_pressure_info["local_pressure_factor"],
            "local_dimension_a_m": local_pressure_info["local_dimension_a_m"],
            "local_inward_wind_uls_kpa": local_inward_wind_uls_kpa,
            "local_match": local_match,
            "profile_selection": selection,
            **({"end_zone": end_zone} if end_zone is not None else {}),
            # Only when the user switched a Table 5.6 region off (stated in the report).
            **({"local_pressure_regions_off": off} if (off := [
                region for region in ROOF_LOCAL_PRESSURE_REGIONS
                if not local_pressure_region_enabled(wind_inputs, region)]) else {}),
        }

    def roof_purlin_end_zones(self, purlin_layout, span_mm, span_type, edge_rows, wind_inputs, general_passing,
                              edge_passing, match, local_match, selection, uniform=None):
        """Gable-end local pressure on every purlin row along the actual run (purlins/roof.py).

        Tries the passing common profiles in order of weight and keeps the lightest
        including its end-zone sections; if none passes, keeps the lightest profile
        with its failing end zone reported. Returns (match, local_match, selection, end_zone).
        """
        from portalcalc.bracing.analysis import AnalysisError
        from portalcalc.purlins.roof import end_zone_design
        if not ROOF_END_ZONES_ACTIVE:
            return match, local_match, selection, None
        bays = [float(b) for b in (self.bay_lengths_mm() or []) if b > 0]
        alternatives = selection.get("profile_alternatives") or []
        if not bays or not purlin_layout.points or not self.metroll_tables or not alternatives:
            return match, local_match, selection, None
        length_m = sum(bays) / 1000.0
        checked, best = [], None
        for option in alternatives:
            base_kg = option["weight_kg_per_m_building"] * length_m
            if best is not None and base_kg >= best[0]["total_kg"] - 1e-9:
                break   # heavier before any end zone: cannot beat the best passing profile
            try:
                design = end_zone_design(self.metroll_tables, wind_inputs, purlin_layout.points, bays, span_type,
                                         edge_rows, option["general_section"], option["edge_section"], span_mm, uniform)
            except AnalysisError as error:
                design = {"status": "NOT CHECKED", "message": str(error), "extra_kg": 0.0, "zones": {}}
            row = {"profile": option["profile"], "general_section": option["general_section"],
                   "edge_section": option["edge_section"], "base_kg": base_kg, "end_zone_extra_kg": design["extra_kg"],
                   "total_kg": base_kg + design["extra_kg"], "status": design["status"]}
            checked.append(row)
            if design["status"] == "OK" and (best is None or row["total_kg"] < best[0]["total_kg"]):
                best = (row, option, design)
        if best is None:
            # Nothing passes: keep the table-selected profile and report its end zone.
            design = end_zone_design(self.metroll_tables, wind_inputs, purlin_layout.points, bays, span_type,
                                     edge_rows, match["section"], local_match["section"], span_mm, uniform) \
                if checked and checked[0]["status"] != "NOT CHECKED" else {"status": "NOT CHECKED", "zones": {},
                "message": checked[0].get("message", "") if checked else ""}
            return match, local_match, {**selection, "end_zone_comparison": checked}, design
        row, option, design = best
        if option["profile"] != selection.get("common_profile"):
            match = dict(next(c for c in general_passing if c["section"] == option["general_section"]))
            local_match = dict(next(c for c in edge_passing if c["section"] == option["edge_section"]))
            selection = {**selection, "common_profile": option["profile"],
                         "weighted_area_mm2": option["weighted_area_mm2"]}
        selection = {**selection, "end_zone_comparison": checked,
                     "basis": selection.get("basis", "") + " Includes the gable-end local pressure end zones (thicker "
                              "end-bay sections where needed) in the weight comparison."}
        return match, local_match, selection, design

    def canopy_purlin_check(self, side, wind_inputs, bay_size_m, canopy_loads, span_type, main_spacing_mm):
        """Open canopy edge purlin (the 3D canopy-edge strut): Metroll bending check.

        Edge purlin tributary = half the canopy purlin spacing. Loads: canopy
        G (with solar) and Q, and net wind from the roof edge zones (with the
        local pressure factor) plus the open underside's Table 5.1(B) Cpi.
        "Auto" picks the lightest passing section; a chosen section is checked.
        """
        if bay_size_m <= 0.0:
            return None
        spacing_text = getattr(self, f"{side}_canopy_purlin_spacing_input").text().strip()
        try:
            spacing_mm = float(spacing_text) if spacing_text else float(main_spacing_mm or 0.0)
        except ValueError:
            return {"status": "NOT CHECKED", "message": "Canopy purlin spacing must be a number (mm)."}
        if not math.isfinite(spacing_mm) or spacing_mm <= 0.0:
            return {"status": "NOT CHECKED", "message": "No canopy purlin spacing, and no main roof purlin spacing to default to."}
        width_m = spacing_mm / 2000.0
        span_mm = bay_size_m * 1000.0
        area_m2 = width_m * bay_size_m

        def underside(result, cpe_case):
            return open_canopy_cpi(result, cpe_case, side, wind_inputs.building_length_m, wind_inputs.eave_height_m)[0]
        up, down = self.purlin_design_pressures(wind_inputs, tributary_area_m2=area_m2, edge_zone=True,
                                                underside_cpi=underside)
        up_s, down_s = self.purlin_design_pressures(wind_inputs, tributary_area_m2=area_m2, edge_zone=True,
                                                    wind_pressure="serviceability", underside_cpi=underside)
        permanent_kpa = abs(canopy_loads.permanent_vertical_kpa)
        live_kpa = abs(canopy_loads.q_vertical_kpa)
        outward_kn_m = up * width_m
        inward_strength_kn_m = max(1.2 * permanent_kpa + 1.5 * live_kpa, 1.2 * permanent_kpa + down) * width_m
        inward_service_kn_m = max(up_s, down_s) * width_m
        match = self.match_metroll_purlin(span_type, span_mm, outward_kn_m, inward_strength_kn_m, inward_service_kn_m,
                                          include_candidates=True)
        passing = match.pop("passing_candidates", []) if isinstance(match, dict) else []
        chosen = getattr(self, f"{side}_canopy_purlin_combo").currentText()
        if chosen and not chosen.startswith("Auto"):
            found = next((item for item in passing if item.get("section") == chosen), None)
            match = dict(found) if found else {
                "status": "NOT OK", "section": chosen,
                "message": f"{chosen} does not satisfy the canopy edge purlin line loads." if passing or match.get("status") == "OK"
                else match.get("message", "No section satisfies the canopy edge purlin line loads.")}
        return {
            "side": side, "span_type": span_type, "spacing_mm": spacing_mm,
            "spacing_source": "Canopy input" if spacing_text else "Main roof purlin spacing",
            "tributary_width_mm": spacing_mm / 2.0, "span_mm": span_mm, "loaded_area_m2": area_m2,
            "permanent_kpa": permanent_kpa, "live_kpa": live_kpa,
            "wind_upward_kpa": up, "wind_downward_kpa": down,
            "wind_service_upward_kpa": up_s, "wind_service_downward_kpa": down_s,
            "outward_kn_m": outward_kn_m, "inward_strength_kn_m": inward_strength_kn_m,
            "inward_service_kn_m": inward_service_kn_m, "section_choice": chosen or "Auto", "match": match,
            "basis": ("Canopy edge purlin: half the canopy purlin spacing; canopy G (with solar) and Q; net wind from the "
                      "roof edge zones with the local pressure factor plus the open underside's Table 5.1(B) Cpi."),
        }

    def update_canopy_purlin_labels(self):
        for side in ("left", "right"):
            label = getattr(self, f"{side}_canopy_purlin_check_label", None)
            if label is None:
                continue
            check = (getattr(self, "canopy_purlin_checks", None) or {}).get(side)
            match = (check or {}).get("match", {})
            if not check:
                label.setText("-")
                label.setStyleSheet("")
            elif match.get("status") == "OK":
                ratio = max(check["outward_kn_m"] / match["outward_capacity_kn_m"],
                            check["inward_strength_kn_m"] / match["inward_strength_capacity_kn_m"],
                            check["inward_service_kn_m"] / match["inward_service_capacity_kn_m"])
                label.setText(f"{match['section']} @ {check['spacing_mm']:.0f} mm: ratio {ratio:.3f}")
                label.setStyleSheet("color: #2e7d32; font-weight: 600;")
            else:
                label.setText(match.get("message") or check.get("message") or "Not checked")
                label.setStyleSheet("color: #c62828; font-weight: 600;")

    def wall_girt_check_from_inputs(self, wind_inputs, bay_size_m, eave_height_mm, span_type):
        if bay_size_m <= 0.0:
            return None
        spacing_mm = self.actual_wall_girt_spacing_mm(eave_height_mm, wind_inputs.wind_region)
        spacing_m = spacing_mm / 1000.0
        span_mm = bay_size_m * 1000.0
        wind_outward_kpa, wind_inward_kpa, wind_service_kpa = self.wall_girt_design_pressures(wind_inputs)
        outward_kn_m = wind_outward_kpa * spacing_m
        inward_strength_kn_m = wind_inward_kpa * spacing_m
        inward_service_kn_m = wind_service_kpa * spacing_m
        match = self.match_metroll_purlin(span_type, span_mm, outward_kn_m, inward_strength_kn_m, inward_service_kn_m)
        return {
            "span_type": span_type,
            "spacing_mm": spacing_mm,
            "max_spacing_mm": self.wall_girt_spacing_mm(wind_inputs.wind_region),
            "span_mm": span_mm,
            "wind_outward_kpa": wind_outward_kpa,
            "wind_inward_kpa": wind_inward_kpa,
            "wind_service_kpa": wind_service_kpa,
            "outward_kn_m": outward_kn_m,
            "inward_strength_kn_m": inward_strength_kn_m,
            "inward_service_kn_m": inward_service_kn_m,
            "match": match,
        }
