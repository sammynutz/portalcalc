"""Metroll purlin/girt capacity tables: row lookup, bridging choice and section matching.

Plain functions over the loaded table cache (``state.metroll_tables``), used by
the 2D roof purlin, wall girt and canopy edge purlin checks. Moved unchanged
from ``frame/engine/secondary.py`` (step 1 of docs/SECONDARY_MEMBERS_PLAN.md);
the engine keeps thin methods with the old names.

``strut_tables.table_row`` is a separate, stricter lookup used by the 3D
bracing checks (no extrapolation below the first tabulated span). The two are
not merged here because that would change results.
"""
import math
import re

TABLE_IDS = {
    "Single span": ("1A", "1B"),
    "Double span": ("2A", "2B"),
    "Continuous lapped": ("5A", "5B"),
}

SECTION_AREAS_MM2 = {
    "C/Z 150 12": 354.0,
    "C/Z 150 15": 442.0,
    "C/Z 150 19": 561.0,
    "C/Z 150 24": 708.0,
    "C/Z 200 12": 444.0,
    "C/Z 200 15": 555.0,
    "C/Z 200 19": 713.0,
    "C/Z 200 24": 900.0,
    "C/Z 250 15": 638.0,
    "C/Z 250 19": 808.0,
    "C/Z 250 24": 1020.0,
    "C/Z 300 19": 998.0,
    "C/Z 300 24": 1260.0,
    "C/Z 300 30": 1575.0,
    "C/Z 350 19": 1207.0,
    "C/Z 350 24": 1524.0,
    "C/Z 350 30": 1905.0,
}


def bridging_row_count(label):
    match = re.search(r"B(\d+)", str(label or "").upper())
    return int(match.group(1)) if match else 0


def section_depth_mm(section):
    numbers = [float(value) for value in re.findall(r"\d+", section or "")]
    return numbers[0] if numbers else 0.0


def recommended_bridging_spacing_mm(section):
    depth = section_depth_mm(section)
    if depth <= 0.0:
        return 0.0
    return 3000.0 if depth <= 150.0 else 4000.0


def minimum_bridging_rows(section, span_mm):
    max_spacing = recommended_bridging_spacing_mm(section)
    if max_spacing <= 0.0 or span_mm <= 0.0:
        return 0
    return max(0, math.ceil(span_mm / max_spacing) - 1)


def bridged_strength_capacity(row, section, span_mm, demand_kn_m):
    """The fewest bridging rows (at least the minimum) whose capacity carries the demand."""
    values = row.get("strength_by_bridging_kn_m") or {}
    if not values:
        label = row.get("strength_bridging", "-")
        capacity = row.get("strength_capacity_kn_m", 0.0)
        rows = bridging_row_count(label)
        max_spacing = recommended_bridging_spacing_mm(section)
        return {
            "capacity": capacity,
            "label": label,
            "rows": rows,
            "minimum_rows": minimum_bridging_rows(section, span_mm),
            "max_spacing_mm": max_spacing,
            "max_bay_mm": max_spacing * (rows + 1) if max_spacing > 0.0 else 0.0,
        }
    minimum_rows = minimum_bridging_rows(section, span_mm)
    options = [
        (bridging_row_count(label), label, capacity)
        for label, capacity in values.items()
        if bridging_row_count(label) >= minimum_rows
    ]
    if not options:
        options = [
            (bridging_row_count(label), label, capacity)
            for label, capacity in values.items()
        ]
    options.sort(key=lambda item: (item[0], item[1]))
    passing = [item for item in options if demand_kn_m <= item[2] + 1e-9]
    rows, label, capacity = passing[0] if passing else max(options, key=lambda item: item[2])
    max_spacing = recommended_bridging_spacing_mm(section)
    return {
        "capacity": capacity,
        "label": label,
        "rows": rows,
        "minimum_rows": minimum_rows,
        "max_spacing_mm": max_spacing,
        "max_bay_mm": max_spacing * (rows + 1) if max_spacing > 0.0 else 0.0,
    }


def capacity_row(table, section, span_mm):
    """Table row at the span: the first row below the tabulated range, interpolated
    within it, None beyond the last span."""
    for profile in table.get("profiles", []):
        if profile.get("section") != section:
            continue
        rows = profile.get("rows", [])
        if not rows:
            return None
        if span_mm <= rows[0].get("span_mm", 0.0):
            return dict(rows[0])
        if span_mm > rows[-1].get("span_mm", 0.0) + 1e-9:
            return None
        for lower, upper in zip(rows[:-1], rows[1:]):
            lower_span = lower.get("span_mm", 0.0)
            upper_span = upper.get("span_mm", 0.0)
            if lower_span <= span_mm <= upper_span + 1e-9:
                if abs(upper_span - lower_span) <= 1e-9:
                    return dict(upper)
                ratio = (span_mm - lower_span) / (upper_span - lower_span)
                result = {"span_mm": span_mm, "interpolated": abs(span_mm - lower_span) > 1e-9 and abs(span_mm - upper_span) > 1e-9}
                for key, lower_value in lower.items():
                    if key == "span_mm":
                        continue
                    upper_value = upper.get(key)
                    if isinstance(lower_value, (int, float)) and isinstance(upper_value, (int, float)):
                        result[key] = lower_value + (upper_value - lower_value) * ratio
                    elif key == "strength_by_bridging_kn_m" and isinstance(lower_value, dict) and isinstance(upper_value, dict):
                        values = {}
                        for bridge_key in sorted(set(lower_value).intersection(upper_value)):
                            values[bridge_key] = lower_value[bridge_key] + (upper_value[bridge_key] - lower_value[bridge_key]) * ratio
                        if values:
                            result[key] = values
                            result["strength_bridging"] = max(values, key=values.get)
                return result
    return None


def section_area_mm2(section):
    if section in SECTION_AREAS_MM2:
        return SECTION_AREAS_MM2[section]
    numbers = [float(value) for value in re.findall(r"\d+", section or "")]
    if len(numbers) >= 2:
        return numbers[0] * numbers[1] / 10.0
    return float("inf")


def section_weight_kg_m(section):
    area = section_area_mm2(section)
    if not math.isfinite(area):
        return 0.0
    return area * 1e-6 * 7850.0


def section_candidate(metroll_tables, span_type, span_mm, section, outward_kn_m, inward_strength_kn_m, inward_service_kn_m):
    """One section's table capacities at the span, with bridging chosen for the demands
    (fewest rows, at least the minimum, that carry them). None if the section has no row there."""
    table_ids = TABLE_IDS[span_type]
    outward_table = metroll_tables["tables"].get(table_ids[0], {})
    inward_table = metroll_tables["tables"].get(table_ids[1], {})
    outward_row = capacity_row(outward_table, section, span_mm)
    inward_row = capacity_row(inward_table, section, span_mm)
    if not outward_row or not inward_row:
        return None
    outward_choice = bridged_strength_capacity(outward_row, section, span_mm, outward_kn_m)
    inward_choice = bridged_strength_capacity(inward_row, section, span_mm, inward_strength_kn_m)
    outward_capacity = outward_choice["capacity"]
    inward_strength_capacity = inward_choice["capacity"]
    inward_service_capacity = inward_row["service_l150_capacity_kn_m"]
    outward_bridging = outward_choice["label"]
    inward_bridging = inward_choice["label"]
    bridging_rows = max(outward_choice["rows"], inward_choice["rows"])
    minimum_rows = max(outward_choice["minimum_rows"], inward_choice["minimum_rows"])
    max_bridging_spacing_mm = max(outward_choice["max_spacing_mm"], inward_choice["max_spacing_mm"])
    max_bridged_bay_mm = max_bridging_spacing_mm * (bridging_rows + 1) if max_bridging_spacing_mm > 0.0 else 0.0
    capped_by_double = False
    if span_type == "Single span":
        double_outward_row = capacity_row(metroll_tables["tables"].get("2A", {}), section, span_mm)
        double_inward_row = capacity_row(metroll_tables["tables"].get("2B", {}), section, span_mm)
        if double_outward_row:
            double_outward_choice = bridged_strength_capacity(double_outward_row, section, span_mm, outward_kn_m)
            capped_by_double = capped_by_double or double_outward_choice["capacity"] < outward_capacity
            if double_outward_choice["capacity"] < outward_capacity:
                outward_bridging = double_outward_choice["label"]
                bridging_rows = max(bridging_rows, double_outward_choice["rows"])
            outward_capacity = min(outward_capacity, double_outward_choice["capacity"])
        if double_inward_row:
            double_inward_choice = bridged_strength_capacity(double_inward_row, section, span_mm, inward_strength_kn_m)
            capped_by_double = capped_by_double or double_inward_choice["capacity"] < inward_strength_capacity
            capped_by_double = capped_by_double or double_inward_row["service_l150_capacity_kn_m"] < inward_service_capacity
            if double_inward_choice["capacity"] < inward_strength_capacity:
                inward_bridging = double_inward_choice["label"]
                bridging_rows = max(bridging_rows, double_inward_choice["rows"])
            inward_strength_capacity = min(inward_strength_capacity, double_inward_choice["capacity"])
            inward_service_capacity = min(inward_service_capacity, double_inward_row["service_l150_capacity_kn_m"])
        max_bridged_bay_mm = max_bridging_spacing_mm * (bridging_rows + 1) if max_bridging_spacing_mm > 0.0 else 0.0
    section_numbers = [float(value) for value in re.findall(r"\d+", section or "")]
    depth_mm = section_numbers[0] if section_numbers else 0.0
    thickness_tenths_mm = section_numbers[1] if len(section_numbers) > 1 else float("inf")
    candidate = {
        "section": section,
        "area_mm2": section_area_mm2(section),
        "depth_mm": depth_mm,
        "thickness_tenths_mm": thickness_tenths_mm,
        "span_row_mm": outward_row["span_mm"],
        "outward_capacity_kn_m": outward_capacity,
        "inward_strength_capacity_kn_m": inward_strength_capacity,
        "inward_service_capacity_kn_m": inward_service_capacity,
        "outward_bridging": outward_bridging,
        "inward_bridging": inward_bridging,
        "bridging_rows": bridging_rows,
        "minimum_bridging_rows": minimum_rows,
        "max_bridging_spacing_mm": max_bridging_spacing_mm,
        "max_bridged_bay_mm": max_bridged_bay_mm,
        "capped_by_double_span": capped_by_double,
        "outward_ratio": outward_kn_m / outward_capacity if outward_capacity > 1e-12 else float("inf"),
        "inward_strength_ratio": inward_strength_kn_m / inward_strength_capacity if inward_strength_capacity > 1e-12 else float("inf"),
        "inward_service_ratio": inward_service_kn_m / inward_service_capacity if inward_service_capacity > 1e-12 else float("inf"),
    }
    candidate["_rows"] = (outward_row, inward_row)
    return candidate


def match_section(metroll_tables, span_type, span_mm, outward_kn_m, inward_strength_kn_m, inward_service_kn_m,
                  *, include_candidates=False):
    """Lightest listed section carrying the three line loads (outward, inward
    strength, inward L/150 service), preferring the deeper section within 5%
    of the lightest area. Single spans are capped by the double-span tables."""
    if not metroll_tables:
        return {"status": "NOT CHECKED", "message": "Metroll capacity table cache not found."}
    table_ids = TABLE_IDS.get(span_type)
    if table_ids is None:
        return {"status": "NOT CHECKED", "message": f"Unsupported purlin span type: {span_type}"}
    outward_table = metroll_tables["tables"].get(table_ids[0])
    inward_table = metroll_tables["tables"].get(table_ids[1])
    if not outward_table or not inward_table:
        return {"status": "NOT CHECKED", "message": f"Missing Metroll tables {table_ids[0]} / {table_ids[1]}."}
    first_profile = outward_table["profiles"][0]
    last_span = first_profile["rows"][-1]["span_mm"] if first_profile.get("rows") else 0.0
    if span_mm > last_span:
        return {"status": "NOT OK", "message": f"Bay span {span_mm:.0f} mm exceeds table maximum {last_span:.0f} mm."}

    passing = []
    candidates = []
    for outward_profile in outward_table.get("profiles", []):
        section = outward_profile.get("section")
        candidate = section_candidate(metroll_tables, span_type, span_mm, section, outward_kn_m,
                                      inward_strength_kn_m, inward_service_kn_m)
        if candidate is None:
            continue
        outward_row, inward_row = candidate.pop("_rows")
        outward_capacity = candidate["outward_capacity_kn_m"]
        inward_strength_capacity = candidate["inward_strength_capacity_kn_m"]
        inward_service_capacity = candidate["inward_service_capacity_kn_m"]
        outward_bridging, inward_bridging = candidate["outward_bridging"], candidate["inward_bridging"]
        bridging_rows, minimum_rows = candidate["bridging_rows"], candidate["minimum_bridging_rows"]
        max_bridging_spacing_mm = candidate["max_bridging_spacing_mm"]
        max_bridged_bay_mm = candidate["max_bridged_bay_mm"]
        capped_by_double = candidate["capped_by_double_span"]
        depth_mm, thickness_tenths_mm = candidate["depth_mm"], candidate["thickness_tenths_mm"]
        candidates.append(candidate)
        if (
            outward_kn_m <= outward_capacity + 1e-9
            and inward_strength_kn_m <= inward_strength_capacity + 1e-9
            and inward_service_kn_m <= inward_service_capacity + 1e-9
        ):
            passing.append({
                "status": "OK",
                "section": section,
                "area_mm2": section_area_mm2(section),
                "depth_mm": depth_mm,
                "thickness_tenths_mm": thickness_tenths_mm,
                "span_row_mm": outward_row["span_mm"],
                "interpolated": outward_row.get("interpolated", False) or inward_row.get("interpolated", False),
                "tables": f"{table_ids[0]} / {table_ids[1]}",
                "outward_capacity_kn_m": outward_capacity,
                "inward_strength_capacity_kn_m": inward_strength_capacity,
                "inward_service_capacity_kn_m": inward_service_capacity,
                "outward_bridging": outward_bridging,
                "inward_bridging": inward_bridging,
                "bridging_rows": bridging_rows,
                "minimum_bridging_rows": minimum_rows,
                "max_bridging_spacing_mm": max_bridging_spacing_mm,
                "max_bridged_bay_mm": max_bridged_bay_mm,
                "capped_by_double_span": capped_by_double,
            })
    if passing:
        lightest_area = min(item["area_mm2"] for item in passing)
        near_lightest = [
            item for item in passing
            if item["area_mm2"] <= lightest_area * 1.05
        ]
        selected = dict(min(near_lightest, key=lambda item: (-item["depth_mm"], item["area_mm2"], item["section"])))
        failed = [
            item for item in candidates
            if max(item["outward_ratio"], item["inward_strength_ratio"], item["inward_service_ratio"]) > 1.0
        ]
        failed.sort(key=lambda item: max(item["outward_ratio"], item["inward_strength_ratio"], item["inward_service_ratio"]))
        selected["near_misses"] = failed[:3]
        if include_candidates:
            selected["passing_candidates"] = passing
        return selected
    return {"status": "NOT OK", "message": "No listed Metroll section satisfies the calculated line loads."}
