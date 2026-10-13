"""Costing helpers and report text for PortalCalc."""

import math
import re


UB_STOCK_LENGTHS_M = [4.5, 5.0, 5.5, 6.0, 6.75, 7.5, 8.25, 9.0, 10.5, 12.0, 13.5, 15.0, 16.5, 18.0, 20.0]

END_WALL_ROD_TURNBUCKLES = {
    16: {"turnbuckle_size": "16", "unit_cost": 9.62},
    20: {"turnbuckle_size": "20", "unit_cost": 16.60},
    24: {"turnbuckle_size": "22", "unit_cost": 23.75},
    30: {"turnbuckle_size": "30", "unit_cost": 45.00},
}


def mm_from_metres_text(value):
    try:
        return float(value) * 1000.0
    except ValueError:
        return 0.0


def section_cost_bucket(profile):
    text = f"{profile.name} {profile.group_name} {profile.shape_type}".upper()
    if any(token in text for token in ["SHS", "RHS", "CHS", "HOLLOW", "TUBE"]):
        return "shs"
    if re.search(r"(^|[^A-Z])WB([^A-Z]|$)", text):
        return "wb"
    return "ub"


def end_wall_portal_restraint_item(
    eave_bracing_info,
    restrained_eave_count,
    physical_x_count_per_end_side=None,
):
    """Return physical X-brace steel and turnbuckles for the restraint allowance."""
    info = eave_bracing_info if isinstance(eave_bracing_info, dict) else {}
    if not info.get("modelled"):
        return None
    area_mm2 = max(float(info.get("brace_area_mm2", 0.0) or 0.0), 0.0)
    diagonal_length_m = max(float(info.get("diagonal_length_mm", 0.0) or 0.0), 0.0) / 1000.0
    end_count = max(int(info.get("end_count", 0) or 0), 0)
    path_squares_per_end = max(int(info.get("squares_to_end", 0) or 0), 0)
    physical_x_count_per_end_side = max(
        int(physical_x_count_per_end_side or path_squares_per_end or 0),
        0,
    )
    restrained_eave_count = min(max(int(restrained_eave_count or 0), 0), 2)
    if (
        area_mm2 <= 0.0
        or diagonal_length_m <= 0.0
        or end_count <= 0
        or restrained_eave_count <= 0
        or physical_x_count_per_end_side <= 0
    ):
        return None

    # Stiffness uses the centre-frame-to-end series path. Physical costing is
    # intentionally separate: one X at every portal position selected by the
    # building layout, with two diagonal runs in each X.
    runs_per_square = 2
    braced_square_count = end_count * restrained_eave_count * physical_x_count_per_end_side
    run_count = braced_square_count * runs_per_square
    total_m = run_count * diagonal_length_m
    kg_m = area_mm2 * 1e-6 * 7850.0
    total_kg = total_m * kg_m
    brace_label = str(info.get("brace_label") or "Brace")
    is_rod = "ROD" in brace_label.upper() or "ROUND" in brace_label.upper()
    diameter_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:DIA|MM|ROD)", brace_label.upper()) if is_rod else None
    rod_diameter_mm = int(round(float(diameter_match.group(1)))) if diameter_match else None
    turnbuckle_rate = END_WALL_ROD_TURNBUCKLES.get(rod_diameter_mm) if rod_diameter_mm else None
    turnbuckle_count = run_count if is_rod else 0
    turnbuckle_unit_cost = turnbuckle_rate["unit_cost"] if turnbuckle_rate else None
    turnbuckle_cost = turnbuckle_count * turnbuckle_unit_cost if turnbuckle_unit_cost is not None else 0.0
    return {
        "brace_label": brace_label,
        "rate_bucket": "ub",
        "end_count": end_count,
        "restrained_eave_count": restrained_eave_count,
        "path_squares_per_end": path_squares_per_end,
        "physical_x_count_per_end_side": physical_x_count_per_end_side,
        "braced_square_count": braced_square_count,
        "runs_per_square": runs_per_square,
        "run_count": run_count,
        "diagonal_length_m": diagonal_length_m,
        "total_m": total_m,
        "area_mm2": area_mm2,
        "kg_m": kg_m,
        "kg": total_kg,
        "is_rod": is_rod,
        "rod_diameter_mm": rod_diameter_mm,
        "turnbuckle_size": turnbuckle_rate["turnbuckle_size"] if turnbuckle_rate else None,
        "turnbuckle_count": turnbuckle_count,
        "turnbuckle_unit_cost": turnbuckle_unit_cost,
        "turnbuckle_cost": turnbuckle_cost,
        "turnbuckle_cost_included": not is_rod or turnbuckle_unit_cost is not None,
    }



def costing_physical_cut_lengths_m(structure):
    column_groups = {
        "COLUMN",
        "LEFT_COLUMN",
        "RIGHT_COLUMN",
        "INTERNAL_COLUMN",
        "LEFT_LEAN_COLUMN",
        "RIGHT_LEAN_COLUMN",
    }
    vertical_column_spans = {}
    vertical_column_profiles = {}
    cut_lengths = []

    def profile_key(profile):
        return (
            getattr(profile, "name", ""),
            getattr(profile, "group_name", ""),
            getattr(profile, "shape_type", ""),
            float(getattr(profile, "A", 0.0) or 0.0),
        )

    for element in structure.elements:
        is_vertical_column = (
            element.group in column_groups
            and abs(element.start.x - element.end.x) <= 1e-6
        )
        if not is_vertical_column:
            cut_lengths.append((element.profile, element.length() / 1000.0))
            continue
        key = (element.group, profile_key(element.profile), round(element.start.x, 6))
        y1 = min(element.start.y, element.end.y)
        y2 = max(element.start.y, element.end.y)
        vertical_column_spans.setdefault(key, []).append((y1, y2))
        vertical_column_profiles[key] = element.profile

    for key, spans in vertical_column_spans.items():
        ordered = sorted(spans)
        merged = []
        for y1, y2 in ordered:
            if not merged or y1 > merged[-1][1] + 1e-6:
                merged.append([y1, y2])
            else:
                merged[-1][1] = max(merged[-1][1], y2)
        profile = vertical_column_profiles[key]
        cut_lengths.extend((profile, (y2 - y1) / 1000.0) for y1, y2 in merged)

    return cut_lengths



def costing_from_structure(self, structure, building_length_m, bay_size_m):
    shs_rate = self.costing_rate_from_input(self.shs_cost_input, "shs_rate_per_kg")
    doubled_chord_rate = self.costing_rate_from_input(
        self.doubled_chord_cost_input, "doubled_chord_rate_per_kg"
    )
    ub_rate = self.costing_rate_from_input(self.ub_cost_input, "ub_rate_per_kg")
    wb_rate = self.costing_rate_from_input(self.wb_cost_input, "wb_rate_per_kg")
    purlin_rate = self.costing_rate_from_input(self.purlin_cost_input, "purlin_rate_per_kg")

    bay_count = max(1, math.ceil(max(building_length_m, 0.0) / max(bay_size_m, 1e-9)))
    portal_count = bay_count + 1

    frame_weights = {"shs": 0.0, "ub": 0.0, "wb": 0.0}
    frame_base_weights = {"shs": 0.0, "ub": 0.0, "wb": 0.0}
    stock_length_m = {"ub": 0.0, "wb": 0.0}
    cut_length_m = {"ub": 0.0, "wb": 0.0}
    for element in structure.elements:
        bucket = self.section_cost_bucket(element.profile)
        exact_weight = self.steel_weight_kg(element.profile.A, element.length())
        frame_base_weights[bucket] += exact_weight

    for profile, physical_cut_length_m in self.costing_physical_cut_lengths_m(structure):
        bucket = self.section_cost_bucket(profile)
        if bucket == "shs":
            frame_weights["shs"] += self.steel_weight_kg(profile.A, physical_cut_length_m * 1000.0) * 1.07
        else:
            element_stock_length_m = self.stock_length_m_for_cut(physical_cut_length_m)
            cut_length_m[bucket] += physical_cut_length_m
            stock_length_m[bucket] += element_stock_length_m
            frame_weights[bucket] += profile.A * element_stock_length_m * 1e-6 * 7850.0

    def chord_profile_for_group(group):
        combos = {
            "TOP": self.top_profile_combo,
            "BOTTOM": self.bottom_profile_combo,
            "LEFT_CANOPY_TOP": self.left_canopy_top_profile_combo,
            "LEFT_CANOPY_BOTTOM": self.left_canopy_bottom_profile_combo,
            "RIGHT_CANOPY_TOP": self.right_canopy_top_profile_combo,
            "RIGHT_CANOPY_BOTTOM": self.right_canopy_bottom_profile_combo,
        }
        combo = combos.get(group)
        return self.selected_profile(combo) if combo is not None else None

    doubled_chord_items_by_section = {}
    for zone in (self.design_envelope_actions or {}).get("doubled_chord_zones", []):
        profile = chord_profile_for_group(zone.get("group"))
        length_m = max(float(zone.get("length_mm", 0.0) or 0.0), 0.0) / 1000.0
        if profile is None or length_m <= 0.0:
            continue
        bucket = self.section_cost_bucket(profile)
        exact_weight = self.steel_weight_kg(profile.A, length_m * 1000.0)
        frame_base_weights[bucket] += exact_weight
        if bucket == "shs":
            costed_weight = exact_weight * 1.07
            frame_weights[bucket] += costed_weight
        else:
            costed_weight = exact_weight
            frame_weights[bucket] += costed_weight
        item = doubled_chord_items_by_section.setdefault(profile.name, {
            "section": profile.name,
            "rate_bucket": bucket,
            "count": 0,
            "total_m": 0.0,
            "kg_m": profile.A * 1e-6 * 7850.0,
            "kg_base": 0.0,
            "kg": 0.0,
        })
        item["count"] += 1
        item["total_m"] += length_m
        item["kg_base"] += exact_weight
        item["kg"] += costed_weight

    roof_general = (self.purlin_check or {}).get("match", {})
    roof_edge = (self.purlin_check or {}).get("local_match", {})
    edge_rows = int((self.purlin_check or {}).get("edge_rows", 0) or 0)
    roof_rows_total = len((self.purlin_layout.points if self.purlin_layout is not None else []) or [])
    roof_edge_rows_total = min(roof_rows_total, 2 * edge_rows)
    roof_general_rows = max(roof_rows_total - roof_edge_rows_total, 0)
    roof_length_m = building_length_m

    wall_match = (self.wall_girt_check or {}).get("match", {})
    wall_spacing_mm = (self.wall_girt_check or {}).get("spacing_mm", 0.0) or 0.0
    eave_mm = max(mm_from_metres_text(self.eave_input.text()), 0.0)
    wall_rows_each_side = max(1, math.ceil(eave_mm / wall_spacing_mm)) if wall_spacing_mm > 0.0 else 0
    enclosure = self.envelope_plan_selector.wall_states()
    clad_long_wall_count = sum(bool(enclosure.get(side)) for side in ("left", "right"))
    wall_rows_total = clad_long_wall_count * wall_rows_each_side

    purlin_items = []
    for label, match, count, length_m, span_type in [
        ("Roof general", roof_general, roof_general_rows, roof_length_m, (self.purlin_check or {}).get("span_type", "")),
        ("Roof edge/local", roof_edge, roof_edge_rows_total, roof_length_m, (self.purlin_check or {}).get("span_type", "")),
        ("Wall girts", wall_match, wall_rows_total, roof_length_m, (self.wall_girt_check or {}).get("span_type", "")),
    ]:
        section = match.get("section")
        if not section or count <= 0 or length_m <= 0.0:
            continue
        kg_m = self.metroll_section_weight_kg_m(section)
        total_m = count * length_m
        lap_factor = 1.15 if span_type == "Continuous lapped" else 1.0
        purlin_items.append({
            "label": label,
            "section": section,
            "display_section": self.metroll_section_display(section, span_type),
            "span_type": span_type,
            "count": count,
            "length_m": length_m,
            "total_m": total_m,
            "kg_m": kg_m,
            "lap_factor": lap_factor,
            "kg_base": kg_m * total_m,
            "kg": kg_m * total_m * lap_factor,
        })

    end_wall_design = getattr(self, "end_wall_mullion_design", None) or {}
    end_wall_count = int(end_wall_design.get("end_wall_count", 0) or 0)
    end_wall_girt = getattr(self, "end_wall_girt_check", None) or {}
    end_wall_girt_match = end_wall_girt.get("match", {})
    end_wall_girt_spacing_mm = float(end_wall_girt.get("spacing_mm", 0.0) or 0.0)
    end_wall_girt_section = end_wall_girt_match.get("section")
    if end_wall_count > 0 and end_wall_girt_spacing_mm > 0.0 and end_wall_girt_section:
        span_mm = max(float(end_wall_design.get("span_mm", 0.0) or 0.0), 0.0)
        eave_mm = max(mm_from_metres_text(self.eave_input.text()), 0.0)
        pitch_tan = math.tan(math.radians(float(self.pitch_input.text())))
        roof_shape = self.current_roof_shape()
        maximum_height_mm = eave_mm + pitch_tan * (span_mm if roof_shape == "Skillion" else span_mm / 2.0)

        def girt_length_at_height_mm(height_mm):
            if height_mm <= eave_mm + 1e-9 or pitch_tan <= 1e-12:
                return span_mm
            roof_zone_height = height_mm - eave_mm
            if roof_shape == "Skillion":
                return max(span_mm - roof_zone_height / pitch_tan, 0.0)
            return max(span_mm - 2.0 * roof_zone_height / pitch_tan, 0.0)

        row_levels_mm = [0.0]
        level_mm = end_wall_girt_spacing_mm
        while level_mm < maximum_height_mm - 1e-6:
            row_levels_mm.append(level_mm)
            level_mm += end_wall_girt_spacing_mm
        row_lengths_m = [
            girt_length_at_height_mm(level_mm) / 1000.0
            for level_mm in row_levels_mm
            if girt_length_at_height_mm(level_mm) > 1e-6
        ]
        total_girt_m = end_wall_count * sum(row_lengths_m)
        girt_count = end_wall_count * len(row_lengths_m)
        if total_girt_m > 0.0 and girt_count > 0:
            kg_m = self.metroll_section_weight_kg_m(end_wall_girt_section)
            span_type = end_wall_girt.get("span_type", "")
            lap_factor = 1.15 if span_type == "Continuous lapped" else 1.0
            purlin_items.append({
                "label": "End-wall girts",
                "section": end_wall_girt_section,
                "display_section": self.metroll_section_display(end_wall_girt_section, span_type),
                "span_type": span_type,
                "count": girt_count,
                "length_m": total_girt_m / girt_count,
                "total_m": total_girt_m,
                "kg_m": kg_m,
                "lap_factor": lap_factor,
                "kg_base": kg_m * total_girt_m,
                "kg": kg_m * total_girt_m * lap_factor,
                "variable_lengths": True,
                "rows_per_end": len(row_lengths_m),
            })

    end_wall_mullion_item = None
    selected_mullion = end_wall_design.get("selected") or {}
    mullion_profile = selected_mullion.get("profile")
    mullions = end_wall_design.get("mullions") or []
    if end_wall_count > 0 and mullion_profile is not None and mullions:
        physical_lengths_m = [max(float(item.get("height_mm", 0.0) or 0.0), 0.0) / 1000.0 for item in mullions]
        physical_total_m = end_wall_count * sum(physical_lengths_m)
        member_count = end_wall_count * len(physical_lengths_m)
        kg_m = mullion_profile.A * 1e-6 * 7850.0
        rate_bucket = self.section_cost_bucket(mullion_profile)
        if rate_bucket == "shs":
            costed_total_m = physical_total_m * 1.07
            allowance = "7% waste"
        else:
            costed_total_m = end_wall_count * sum(self.stock_length_m_for_cut(length_m) for length_m in physical_lengths_m)
            allowance = "stock lengths"
        end_wall_mullion_item = {
            "section": mullion_profile.name,
            "rate_bucket": rate_bucket,
            "count": member_count,
            "physical_total_m": physical_total_m,
            "costed_total_m": costed_total_m,
            "kg_m": kg_m,
            "kg_base": kg_m * physical_total_m,
            "kg": kg_m * costed_total_m,
            "allowance": allowance,
        }

    crane_beam_item = None
    try:
        if not self.crane_rating_active_from_ui():
            raise ValueError("No crane rating")
        crane_summary = self.current_crane_beam_summary()
        crane_length_m = max(building_length_m, 0.0)
        crane_count = 2
        crane_total_m = crane_count * crane_length_m
        crane_kg = crane_summary["weight_kg_m"] * crane_total_m
        crane_beam_item = {
            "section": crane_summary["section"],
            "rate_bucket": self.section_cost_bucket(self.catalog.get(self.crane_beam_profile_combo.currentText())),
            "count": crane_count,
            "length_m": crane_length_m,
            "total_m": crane_total_m,
            "kg_m": crane_summary["weight_kg_m"],
            "kg": crane_kg,
            "capacity_utilization": crane_summary["capacity_utilization"],
            "deflection_utilization": crane_summary["deflection_utilization"],
        }
    except (KeyError, ValueError, ZeroDivisionError):
        crane_beam_item = None

    frame_total_kg = {key: value * portal_count for key, value in frame_weights.items()}
    doubled_chord_kg_by_bucket = {"shs": 0.0, "ub": 0.0, "wb": 0.0}
    for item in doubled_chord_items_by_section.values():
        doubled_chord_kg_by_bucket[item["rate_bucket"]] += item["kg"] * portal_count
    doubled_chord_total_kg = sum(doubled_chord_kg_by_bucket.values())
    standard_frame_total_kg = {
        bucket: max(frame_total_kg[bucket] - doubled_chord_kg_by_bucket[bucket], 0.0)
        for bucket in frame_total_kg
    }
    purlin_total_kg = sum(item["kg"] for item in purlin_items)
    end_wall_mullion_total_kg = end_wall_mullion_item["kg"] if end_wall_mullion_item else 0.0
    end_wall_mullion_rate_bucket = end_wall_mullion_item["rate_bucket"] if end_wall_mullion_item else "ub"
    end_wall_mullion_rate = {
        "shs": shs_rate,
        "ub": ub_rate,
        "wb": wb_rate,
    }.get(end_wall_mullion_rate_bucket, ub_rate)
    crane_beam_total_kg = crane_beam_item["kg"] if crane_beam_item else 0.0
    crane_beam_rate_bucket = crane_beam_item["rate_bucket"] if crane_beam_item else "ub"
    crane_beam_rate = wb_rate if crane_beam_rate_bucket == "wb" else ub_rate
    storage_results = getattr(self, "storage_results", None)
    storage_rail_design = getattr(self, "storage_rail_design", None)
    storage_panel_design = getattr(self, "storage_panel_design", None)
    storage_rail_item = None
    storage_rail_total_kg = 0.0
    if storage_results is not None and storage_results.inputs.enabled and storage_rail_design is not None:
        rail_profile = storage_rail_design["profile"]
        rail_count = storage_results.active_wall_count
        rail_length_m = max(building_length_m, 0.0)
        rail_total_m = rail_count * rail_length_m
        rail_kg_m = rail_profile.A * 1e-6 * 7850.0
        storage_rail_total_kg = rail_kg_m * rail_total_m
        storage_rail_item = {
            "section": rail_profile.name,
            "rate_bucket": self.section_cost_bucket(rail_profile),
            "count": rail_count,
            "length_m": rail_length_m,
            "total_m": rail_total_m,
            "kg_m": rail_kg_m,
            "kg": storage_rail_total_kg,
            "capacity_utilization": storage_rail_design["capacity_utilization"],
            "deflection_utilization": storage_rail_design["deflection_utilization"],
        }
    storage_rail_rate_bucket = storage_rail_item["rate_bucket"] if storage_rail_item else "ub"
    storage_rail_rate = wb_rate if storage_rail_rate_bucket == "wb" else ub_rate
    storage_panel_material = self.storage_panel_material_summary(storage_results, storage_panel_design, building_length_m) if storage_results is not None else None
    storage_panel_cost = storage_panel_material["total_cost"] if storage_panel_material else 0.0
    restrained_eave_count = 1
    restrained_sides_combo = getattr(self, "end_wall_braced_sides_combo", None)
    if restrained_sides_combo is not None and restrained_sides_combo.currentText() == "Both sides":
        restrained_eave_count = 2
    end_wall_restraint_item = end_wall_portal_restraint_item(
        getattr(self, "eave_bracing_info", None),
        restrained_eave_count,
        physical_x_count_per_end_side=portal_count,
    )
    end_wall_restraint_total_kg = end_wall_restraint_item["kg"] if end_wall_restraint_item else 0.0
    end_wall_restraint_steel_cost = end_wall_restraint_total_kg * ub_rate
    end_wall_restraint_turnbuckle_cost = (
        end_wall_restraint_item["turnbuckle_cost"] if end_wall_restraint_item else 0.0
    )
    costs = {
        "shs": standard_frame_total_kg["shs"] * shs_rate,
        "ub": standard_frame_total_kg["ub"] * ub_rate,
        "wb": standard_frame_total_kg["wb"] * wb_rate,
        "doubled_chord": doubled_chord_total_kg * doubled_chord_rate,
        "purlin": purlin_total_kg * purlin_rate,
        "end_wall_restraint": end_wall_restraint_steel_cost + end_wall_restraint_turnbuckle_cost,
        "end_wall_mullion": end_wall_mullion_total_kg * end_wall_mullion_rate,
        "crane_beam": crane_beam_total_kg * crane_beam_rate,
        "storage_rail": storage_rail_total_kg * storage_rail_rate,
        "storage_panel": storage_panel_cost,
    }
    return {
        "bay_count": bay_count,
        "portal_count": portal_count,
        "building_length_m": building_length_m,
        "bay_size_m": bay_size_m,
        "per_portal_weights": frame_weights,
        "per_portal_base_weights": frame_base_weights,
        "frame_total_kg": frame_total_kg,
        "standard_frame_total_kg": standard_frame_total_kg,
        "doubled_chord_total_kg": doubled_chord_total_kg,
        "ub_cut_length_m_per_portal": cut_length_m["ub"],
        "ub_stock_length_m_per_portal": stock_length_m["ub"],
        "wb_cut_length_m_per_portal": cut_length_m["wb"],
        "wb_stock_length_m_per_portal": stock_length_m["wb"],
        "shs_wastage_factor": 1.07,
        "doubled_chord_items": sorted(
            doubled_chord_items_by_section.values(),
            key=lambda item: (item["section"], item["total_m"]),
        ),
        "continuous_lap_factor": 1.15,
        "ub_stock_lengths_m": UB_STOCK_LENGTHS_M,
        "purlin_items": purlin_items,
        "purlin_total_kg": purlin_total_kg,
        "end_wall_restraint_item": end_wall_restraint_item,
        "end_wall_restraint_total_kg": end_wall_restraint_total_kg,
        "end_wall_restraint_steel_cost": end_wall_restraint_steel_cost,
        "end_wall_restraint_turnbuckle_cost": end_wall_restraint_turnbuckle_cost,
        "end_wall_mullion_item": end_wall_mullion_item,
        "end_wall_mullion_total_kg": end_wall_mullion_total_kg,
        "crane_beam_item": crane_beam_item,
        "crane_beam_total_kg": crane_beam_total_kg,
        "storage_rail_item": storage_rail_item,
        "storage_rail_total_kg": storage_rail_total_kg,
        "storage_panel_material": storage_panel_material,
        "rates": {
            "shs": shs_rate,
            "doubled_chord": doubled_chord_rate,
            "ub": ub_rate,
            "wb": wb_rate,
            "purlin": purlin_rate,
        },
        "costs": costs,
        "total_cost": sum(costs.values()),
    }



def update_costing_text(self):
    if not getattr(self, "costing_box", None):
        return
    summary = self.costing_summary
    if not summary:
        self.costing_box.setText("Generate and solve the frame to view costing.")
        if getattr(self, "total_cost_label", None):
            self.total_cost_label.setText("Not calculated")
            self.total_cost_label.setStyleSheet("font-weight: bold;")
        return
    lines = [
        "COSTING SUMMARY",
        f"Building length: {summary['building_length_m']:.2f} m",
        f"Bay size: {summary['bay_size_m']:.2f} m",
        f"Bays / portals: {summary['bay_count']} / {summary['portal_count']}",
        "",
        "FRAME STEEL",
        f"Per portal SHS/RHS/CHS exact: {summary['per_portal_base_weights']['shs']:.1f} kg + 7% waste = {summary['per_portal_weights']['shs']:.1f} kg",
        f"Per portal UB/UC/PFC/open exact: {summary['per_portal_base_weights']['ub']:.1f} kg; cut length {summary['ub_cut_length_m_per_portal']:.2f} m rounded to stock length {summary['ub_stock_length_m_per_portal']:.2f} m = {summary['per_portal_weights']['ub']:.1f} kg",
        f"Per portal WB exact: {summary['per_portal_base_weights']['wb']:.1f} kg; cut length {summary['wb_cut_length_m_per_portal']:.2f} m rounded to stock length {summary['wb_stock_length_m_per_portal']:.2f} m = {summary['per_portal_weights']['wb']:.1f} kg",
    ]
    if summary.get("doubled_chord_items"):
        for item in summary["doubled_chord_items"]:
            waste_text = " + 7% waste" if item.get("rate_bucket") == "shs" else ""
            lines.append(
                f"Doubled chord extra: {item['count']} panel(s), {item['total_m']:.2f} m {item['section']} "
                f"= {item['kg_base']:.1f} kg{waste_text} = {item['kg']:.1f} kg per portal"
            )
    lines.extend([
        f"Standard SHS/RHS/CHS incl. waste: {summary['standard_frame_total_kg']['shs']:.1f} kg x ${summary['rates']['shs']:.2f}/kg = ${summary['costs']['shs']:,.0f}",
        f"Standard UB/UC/PFC/open stock: {summary['standard_frame_total_kg']['ub']:.1f} kg x ${summary['rates']['ub']:.2f}/kg = ${summary['costs']['ub']:,.0f}",
        f"Standard WB stock: {summary['standard_frame_total_kg']['wb']:.1f} kg x ${summary['rates']['wb']:.2f}/kg = ${summary['costs']['wb']:,.0f}",
        f"Doubled chord extra: {summary['doubled_chord_total_kg']:.1f} kg x ${summary['rates']['doubled_chord']:.2f}/kg = ${summary['costs']['doubled_chord']:,.0f}",
        "",
        "END-WALL PORTAL RESTRAINT",
    ])
    end_wall_restraint_item = summary.get("end_wall_restraint_item")
    if end_wall_restraint_item:
        lines.extend([
            (
                f"{end_wall_restraint_item['end_count']} selected end(s) x "
                f"{end_wall_restraint_item['restrained_eave_count']} restrained eave side(s) x "
                f"{end_wall_restraint_item['physical_x_count_per_end_side']} portal position(s) = "
                f"{end_wall_restraint_item['braced_square_count']} X-brace(s)"
            ),
            (
                f"{end_wall_restraint_item['braced_square_count']} X-brace(s) x "
                f"{end_wall_restraint_item['runs_per_square']} diagonals = "
                f"{end_wall_restraint_item['run_count']} diagonal runs"
            ),
            (
                f"{end_wall_restraint_item['brace_label']}: {end_wall_restraint_item['run_count']} x "
                f"{end_wall_restraint_item['diagonal_length_m']:.2f} m = "
                f"{end_wall_restraint_item['total_m']:.1f} m x "
                f"{end_wall_restraint_item['kg_m']:.2f} kg/m = "
                f"{end_wall_restraint_item['kg']:.1f} kg"
            ),
            (
                f"Brace steel: {summary['end_wall_restraint_total_kg']:.1f} kg x "
                f"${summary['rates']['ub']:.2f}/kg (UB rate) = "
                f"${summary['end_wall_restraint_steel_cost']:,.2f}"
            ),
        ])
        if end_wall_restraint_item.get("turnbuckle_count", 0) > 0:
            if end_wall_restraint_item.get("turnbuckle_cost_included"):
                lines.append(
                    f"Rod turnbuckles: {end_wall_restraint_item['turnbuckle_count']} x "
                    f"size {end_wall_restraint_item['turnbuckle_size']} at "
                    f"${end_wall_restraint_item['turnbuckle_unit_cost']:.2f} = "
                    f"${summary['end_wall_restraint_turnbuckle_cost']:,.2f}"
                )
            else:
                lines.append(
                    f"Rod turnbuckles: {end_wall_restraint_item['turnbuckle_count']} required; "
                    f"the {end_wall_restraint_item.get('rod_diameter_mm') or '?'} DIA rod rate is pending, "
                    "so turnbuckle cost is not yet included."
                )
        lines.append(f"End-wall restraint total: ${summary['costs']['end_wall_restraint']:,.2f}")
    else:
        lines.append("Not included - end-wall portal restraint is set to None.")
    lines.extend([
        "",
        "END-WALL MULLIONS",
    ])
    end_wall_mullion_item = summary.get("end_wall_mullion_item")
    if end_wall_mullion_item:
        lines.extend([
            (
                f"{end_wall_mullion_item['count']} x variable height {end_wall_mullion_item['section']} = "
                f"{end_wall_mullion_item['physical_total_m']:.1f} m physical x {end_wall_mullion_item['kg_m']:.1f} kg/m "
                f"= {end_wall_mullion_item['kg_base']:.1f} kg exact"
            ),
            (
                f"Costed at {end_wall_mullion_item['costed_total_m']:.1f} m including {end_wall_mullion_item['allowance']} "
                f"= {summary['end_wall_mullion_total_kg']:.1f} kg x "
                f"${summary['rates'][end_wall_mullion_item['rate_bucket']]:.2f}/kg "
                f"({end_wall_mullion_item['rate_bucket'].upper()} rate) = ${summary['costs']['end_wall_mullion']:,.0f}"
            ),
        ])
    else:
        lines.append("Not included - no clad end wall or no intermediate mullions required.")
    lines.extend([
        "",
        "CRANE RUNWAY BEAMS",
    ])
    crane_item = summary.get("crane_beam_item")
    if crane_item:
        lines.extend([
            (
                f"{crane_item['count']} x {crane_item['length_m']:.2f} m {crane_item['section']} "
                f"= {crane_item['total_m']:.1f} m x {crane_item['kg_m']:.1f} kg/m "
                f"= {crane_item['kg']:.1f} kg"
            ),
            (
                f"Total crane runway beams: {summary['crane_beam_total_kg']:.1f} kg x "
                f"${summary['rates'][crane_item.get('rate_bucket', 'ub')]:.2f}/kg "
                f"({crane_item.get('rate_bucket', 'ub').upper()} rate) = ${summary['costs']['crane_beam']:,.0f}"
            ),
            f"Check utilisations: capacity {crane_item['capacity_utilization']:.2f}, deflection {crane_item['deflection_utilization']:.2f}",
        ])
    else:
        lines.append("Not included - no crane rating entered.")
    lines.extend([
        "",
        "PURLINS / GIRTS",
    ])
    if summary["purlin_items"]:
        for item in summary["purlin_items"]:
            if item.get("variable_lengths"):
                line = (
                    f"{item['label']}: {item['count']} variable-length rows "
                    f"({item.get('rows_per_end', 0)} per clad end) {item.get('display_section', item['section'])} "
                    f"= {item['total_m']:.1f} m x {item['kg_m']:.2f} kg/m"
                )
            else:
                line = (
                    f"{item['label']}: {item['count']} x {item['length_m']:.2f} m "
                    f"{item.get('display_section', item['section'])} "
                    f"= {item['total_m']:.1f} m x {item['kg_m']:.2f} kg/m"
                )
            if item.get("lap_factor", 1.0) > 1.0:
                line += f" x 1.15 lap = {item['kg']:.1f} kg"
            else:
                line += f" = {item['kg']:.1f} kg"
            lines.append(line)
        lines.append(
            f"Total purlins/girts: {summary['purlin_total_kg']:.1f} kg x "
            f"${summary['rates']['purlin']:.2f}/kg = ${summary['costs']['purlin']:,.0f}"
        )
    else:
        lines.append("No purlin/girt match available.")
    lines.extend([
        "",
        "STORAGE LOADS",
    ])
    storage_rail_item = summary.get("storage_rail_item")
    storage_panel_material = summary.get("storage_panel_material")
    if storage_rail_item:
        lines.extend([
            (
                f"Storage rails: {storage_rail_item['count']} x {storage_rail_item['length_m']:.2f} m "
                f"{storage_rail_item['section']} = {storage_rail_item['total_m']:.1f} m x "
                f"{storage_rail_item['kg_m']:.1f} kg/m = {storage_rail_item['kg']:.1f} kg"
            ),
            (
                f"Total storage rails: {summary['storage_rail_total_kg']:.1f} kg x "
                f"${summary['rates'][storage_rail_item.get('rate_bucket', 'ub')]:.2f}/kg "
                f"({storage_rail_item.get('rate_bucket', 'ub').upper()} rate) = ${summary['costs']['storage_rail']:,.0f}"
            ),
        ])
    if storage_panel_material:
        lines.extend([
            (
                f"Storage panel concrete: {storage_panel_material['concrete_volume_m3']:.2f} m3 x "
                f"${storage_panel_material['concrete_rate']:.2f}/m3 = ${storage_panel_material['concrete_cost']:,.0f}"
            ),
            (
                f"Storage panel reo: {storage_panel_material['reo_total_kg']:.1f} kg "
                f"({storage_panel_material['reo_kg_m2']:.2f} kg/m2) x "
                f"${storage_panel_material['reo_rate']:.2f}/kg = ${storage_panel_material['reo_cost']:,.0f}"
            ),
            f"Storage panel material total: ${storage_panel_material['total_cost']:,.0f}",
        ])
    if not storage_rail_item and not storage_panel_material:
        lines.append("Not included - storage loads are disabled or no passing storage design is available.")
    lines.extend([
        "",
        f"TOTAL ESTIMATED COST: ${summary['total_cost']:,.0f}",
        "",
        "Rates are supply placeholders only; no allowance is made for cleats, bolts, plates, fabrication, coating, transport, waste, laps, chairs, labour, GST, or mark-up.",
    ])
    self.costing_box.setText("\n".join(lines))
    if getattr(self, "total_cost_label", None):
        self.total_cost_label.setText(f"${summary['total_cost']:,.0f}")
        self.total_cost_label.setStyleSheet("font-weight: bold; color: #1b7f3a;")

