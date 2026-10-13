"""End-wall mullion geometry and preliminary steel design helpers."""

from __future__ import annotations

import math

from member_checks import E_STEEL_MPA, MemberActions, check_member


AUTO_MULLION_MAX_SPACING_MM = 8000.0
MULLION_DEFLECTION_DIVISOR = 150.0
STEEL_DENSITY_KG_M3 = 7850.0
GRAVITY_M_S2 = 9.80665


def roof_height_mm(x_mm, span_mm, eave_height_mm, pitch_deg, roof_shape):
    """Return the end-wall roof height at a transverse station."""
    x_mm = min(max(float(x_mm), 0.0), max(float(span_mm), 0.0))
    slope = math.tan(math.radians(float(pitch_deg)))
    if str(roof_shape).lower() == "skillion":
        return float(eave_height_mm) + x_mm * slope
    return float(eave_height_mm) + min(x_mm, max(float(span_mm) - x_mm, 0.0)) * slope


def mullion_layout(span_mm, eave_height_mm, pitch_deg, roof_shape, spacing_override_mm=None):
    """Lay intermediate mullions out evenly to an automatic or manual target."""
    span_mm = max(float(span_mm), 0.0)
    override = None
    if spacing_override_mm is not None:
        try:
            entered = float(spacing_override_mm)
        except (TypeError, ValueError):
            entered = 0.0
        if entered > 0.0:
            override = entered
    target_spacing_mm = override or AUTO_MULLION_MAX_SPACING_MM
    spaces = max(1, math.ceil(span_mm / target_spacing_mm)) if span_mm > 0.0 else 1
    actual_spacing_mm = span_mm / spaces if spaces else 0.0
    mullions = []
    for index in range(1, spaces):
        station_mm = span_mm * index / spaces
        mullions.append({
            "index": index,
            "station_mm": station_mm,
            "height_mm": roof_height_mm(station_mm, span_mm, eave_height_mm, pitch_deg, roof_shape),
            "tributary_width_mm": actual_spacing_mm,
        })
    return {
        "span_mm": span_mm,
        "spaces": spaces,
        "mullion_count": len(mullions),
        "target_spacing_mm": target_spacing_mm,
        "actual_spacing_mm": actual_spacing_mm,
        "spacing_override_mm": override,
        "automatic_max_spacing_mm": AUTO_MULLION_MAX_SPACING_MM,
        "mullions": mullions,
    }


def _profile_design(profile, height_mm, tributary_width_mm, strength_pressure_kpa, service_pressure_kpa):
    tributary_width_m = tributary_width_mm / 1000.0
    strength_line_load_kn_m = max(float(strength_pressure_kpa), 0.0) * tributary_width_m
    service_line_load_kn_m = max(float(service_pressure_kpa), 0.0) * tributary_width_m
    height_m = height_mm / 1000.0
    moment_knm = strength_line_load_kn_m * height_m**2 / 8.0
    shear_kn = strength_line_load_kn_m * height_m / 2.0
    mass_kg_m = max(float(profile.A), 0.0) * 1e-6 * STEEL_DENSITY_KG_M3
    self_weight_compression_kn = 1.2 * mass_kg_m * GRAVITY_M_S2 * height_m / 1000.0
    effective_length_mm = height_mm / 2.0
    actions = MemberActions(
        compression_kn=self_weight_compression_kn,
        shear_kn=shear_kn,
        moment_knm=moment_knm,
        effective_length_y_mm=effective_length_mm,
        effective_length_z_mm=effective_length_mm,
        moment_modifier=1.75,
        kt=1.0,
        kl=1.0,
        kr=1.0,
    )
    check = check_member(profile.check_properties(), actions)
    inertia_mm4 = max(float(profile.I_analysis), 1.0)
    # kN/m is numerically equal to N/mm.
    deflection_mm = 5.0 * service_line_load_kn_m * height_mm**4 / (384.0 * E_STEEL_MPA * inertia_mm4)
    deflection_limit_mm = height_mm / MULLION_DEFLECTION_DIVISOR
    deflection_ratio = deflection_mm / deflection_limit_mm if deflection_limit_mm > 0.0 else float("inf")
    strength_ratio = check.governing_ratio
    return {
        "profile": profile,
        "section": profile.name,
        "mass_kg_m": mass_kg_m,
        "actions": actions,
        "member_check": check,
        "strength_line_load_kn_m": strength_line_load_kn_m,
        "service_line_load_kn_m": service_line_load_kn_m,
        "moment_knm": moment_knm,
        "shear_kn": shear_kn,
        "self_weight_compression_kn": self_weight_compression_kn,
        "effective_length_mm": effective_length_mm,
        "deflection_mm": deflection_mm,
        "deflection_limit_mm": deflection_limit_mm,
        "deflection_ratio": deflection_ratio,
        "strength_ratio": strength_ratio,
        "governing_ratio": max(strength_ratio, deflection_ratio),
        "passes": strength_ratio <= 1.0 and deflection_ratio <= 1.0,
    }


def design_end_wall_mullions(
    *,
    span_mm,
    eave_height_mm,
    pitch_deg,
    roof_shape,
    outward_uls_kpa,
    inward_uls_kpa,
    service_kpa,
    profiles,
    spacing_override_mm=None,
    selected_profile=None,
    end_wall_count=2,
):
    """Select/check one mullion section for the governing intermediate mullion."""
    layout = mullion_layout(
        span_mm,
        eave_height_mm,
        pitch_deg,
        roof_shape,
        spacing_override_mm=spacing_override_mm,
    )
    result = {
        **layout,
        "end_wall_count": max(int(end_wall_count), 0),
        "outward_uls_kpa": max(float(outward_uls_kpa), 0.0),
        "inward_uls_kpa": max(float(inward_uls_kpa), 0.0),
        "service_kpa": max(float(service_kpa), 0.0),
        "strength_pressure_kpa": max(float(outward_uls_kpa), float(inward_uls_kpa), 0.0),
        "deflection_divisor": MULLION_DEFLECTION_DIVISOR,
        "connection": "Pinned at roof and ground",
        "restraint": "One mid-height flybrace; effective lengths H/2",
        "selected": None,
        "status": "NOT REQUIRED" if int(end_wall_count) <= 0 else "NOT CHECKED",
    }
    if result["end_wall_count"] <= 0:
        return result
    if not layout["mullions"]:
        result["status"] = "NOT REQUIRED"
        result["message"] = "No intermediate mullions are required at the adopted spacing."
        return result

    governing_mullion = max(layout["mullions"], key=lambda item: item["height_mm"])
    result["governing_mullion"] = governing_mullion
    candidates = [selected_profile] if selected_profile is not None else list(profiles or [])
    checked = []
    for profile in candidates:
        if profile is None:
            continue
        item = _profile_design(
            profile,
            governing_mullion["height_mm"],
            governing_mullion["tributary_width_mm"],
            result["strength_pressure_kpa"],
            result["service_kpa"],
        )
        checked.append(item)
    result["candidates_checked"] = len(checked)
    passing = [item for item in checked if item["passes"]]
    if selected_profile is not None:
        selected = checked[0] if checked else None
    elif passing:
        selected = min(passing, key=lambda item: (item["mass_kg_m"], item["governing_ratio"], item["section"]))
    else:
        selected = min(checked, key=lambda item: (item["governing_ratio"], item["mass_kg_m"])) if checked else None
    result["selected"] = selected
    if selected is None:
        result["status"] = "NOT OK"
        result["message"] = "No eligible mullion sections were available."
    elif selected["passes"]:
        result["status"] = "OK"
    else:
        result["status"] = "NOT OK"
        result["message"] = "The selected section does not satisfy strength and deflection."
    return result

