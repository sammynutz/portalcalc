"""UI-independent preliminary longitudinal roof and wall bracing design."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BracingInputs:
    span_mm: float
    eave_height_mm: float
    roof_pitch_deg: float
    roof_shape: str
    bay_width_mm: float
    roof_panel_widths_mm: tuple[float, ...]
    roof_panel_projected_areas_m2: tuple[float, ...]
    roof_node_lines: tuple[dict, ...]
    wall_side_count: int
    braced_bays_per_side: int
    roof_brace_area_mm2: float
    roof_brace_label: str
    wall_brace_area_mm2: float
    wall_brace_label: str
    ultimate_pressure_kpa: float
    service_pressure_kpa: float
    ultimate_case: str = "-"
    service_case: str = "-"
    strut_candidates: tuple[dict, ...] = field(default_factory=tuple)
    steel_modulus_mpa: float = 200000.0
    brace_yield_mpa: float = 250.0
    capacity_factor: float = 0.9


def _diagonal_response(shear_kn: float, run_mm: float, rise_mm: float, area_mm2: float, inputs: BracingInputs) -> dict:
    run_mm = max(float(run_mm), 1e-9)
    rise_mm = max(float(rise_mm), 0.0)
    diagonal_mm = math.hypot(run_mm, rise_mm)
    cosine = run_mm / diagonal_mm
    brace_force_kn = shear_kn / cosine
    capacity_kn = inputs.capacity_factor * area_mm2 * inputs.brace_yield_mpa / 1000.0
    elongation_mm = brace_force_kn * 1000.0 * diagonal_mm / max(inputs.steel_modulus_mpa * area_mm2, 1e-9)
    return {
        "run_mm": run_mm,
        "rise_mm": rise_mm,
        "diagonal_mm": diagonal_mm,
        "angle_deg": math.degrees(math.atan2(rise_mm, run_mm)),
        "shear_kn": shear_kn,
        "brace_force_kn": brace_force_kn,
        "capacity_kn": capacity_kn,
        "capacity_ratio": brace_force_kn / capacity_kn if capacity_kn > 0.0 else math.inf,
        "elongation_mm": elongation_mm,
        "horizontal_drift_mm": elongation_mm / cosine,
        "transverse_component_kn": brace_force_kn * rise_mm / diagonal_mm,
    }


def compression_capacity_kn(area_mm2: float, i_min_mm4: float, fy_mpa: float, length_mm: float, count: float = 1.0) -> float:
    """Conservative Rankine combination of squash and Euler capacities."""
    area_mm2 = max(float(area_mm2), 0.0)
    i_min_mm4 = max(float(i_min_mm4), 0.0)
    length_mm = max(float(length_mm), 1e-9)
    squash_kn = 0.9 * area_mm2 * max(float(fy_mpa), 0.0) / 1000.0
    euler_kn = math.pi**2 * 200000.0 * i_min_mm4 / length_mm**2 / 1000.0
    single_kn = 1.0 / (1.0 / squash_kn + 1.0 / euler_kn) if squash_kn > 0.0 and euler_kn > 0.0 else 0.0
    return max(float(count), 0.0) * single_kn


def approximate_purlin_weak_axis_inertia_mm4(section: str, area_mm2: float) -> float:
    """Estimate C-purlin weak-axis inertia from its nominal depth/thickness.

    This is intentionally conservative and is only used to rank preliminary
    strut arrangements until manufacturer axial data is introduced.
    """
    numbers = [float(value) for value in __import__("re").findall(r"\d+", str(section or ""))]
    depth = numbers[0] if numbers else 150.0
    thickness = numbers[1] / 10.0 if len(numbers) > 1 else 1.9
    flange_by_depth = {150: 57.0, 200: 69.0, 250: 69.0, 300: 87.0, 350: 113.0, 400: 125.0}
    flange = flange_by_depth[min(flange_by_depth, key=lambda item: abs(item - depth))]
    lip = min(25.0, max(15.0, 0.25 * flange))
    parts = [
        (thickness * depth, thickness / 2.0, thickness**3 * depth / 12.0),
        (flange * thickness, flange / 2.0, thickness * flange**3 / 12.0),
        (flange * thickness, flange / 2.0, thickness * flange**3 / 12.0),
        (lip * thickness, flange - thickness / 2.0, lip * thickness**3 / 12.0),
        (lip * thickness, flange - thickness / 2.0, lip * thickness**3 / 12.0),
    ]
    model_area = sum(item[0] for item in parts)
    centroid = sum(a * x for a, x, _ in parts) / max(model_area, 1e-9)
    inertia = sum(local_i + a * (x - centroid) ** 2 for a, x, local_i in parts)
    return inertia * max(float(area_mm2), 0.0) / max(model_area, 1e-9)


def _select_strut(demand_kn: float, candidates: tuple[dict, ...], length_mm: float) -> dict:
    evaluated = []
    for candidate in candidates:
        capacity = compression_capacity_kn(
            candidate.get("area_mm2", 0.0), candidate.get("i_min_mm4", 0.0),
            candidate.get("fy_mpa", 300.0), length_mm, candidate.get("count", 1.0),
        ) * candidate.get("available_fraction", 1.0)
        item = {**candidate, "capacity_kn": capacity, "ratio": demand_kn / capacity if capacity > 0.0 else math.inf}
        evaluated.append(item)
        if item["ratio"] <= 1.0:
            return {**item, "status": "OK", "candidates": evaluated}
    return {
        "status": "NOT OK", "label": "No listed strut passes", "capacity_kn": 0.0,
        "ratio": math.inf, "candidates": evaluated,
    }


def calculate_preliminary_bracing(inputs: BracingInputs) -> dict:
    """Calculate distinct roof-plane, side-wall and longitudinal strut demands."""
    span_mm = max(float(inputs.span_mm), 0.0)
    eave_mm = max(float(inputs.eave_height_mm), 0.0)
    bay_mm = max(float(inputs.bay_width_mm), 1e-9)
    pitch_rad = math.radians(abs(float(inputs.roof_pitch_deg)))
    roof_rise_mm = span_mm * math.tan(pitch_rad) * (0.5 if inputs.roof_shape == "Gable" else 1.0)
    end_wall_area_m2 = (span_mm * eave_mm + 0.5 * span_mm * roof_rise_mm) / 1_000_000.0

    roof_panel_areas = tuple(max(float(area), 0.0) for area in inputs.roof_panel_projected_areas_m2)
    if not roof_panel_areas:
        roof_panel_areas = (end_wall_area_m2,)
    roof_panel_count = len(roof_panel_areas)
    wall_sides = max(int(inputs.wall_side_count), 1)
    braced_bays = max(int(inputs.braced_bays_per_side), 1)
    total_uls_kn = max(float(inputs.ultimate_pressure_kpa), 0.0) * end_wall_area_m2
    total_sls_kn = max(float(inputs.service_pressure_kpa), 0.0) * end_wall_area_m2

    # End-wall cladding/mullions span between ground and roof. Uniform pressure
    # therefore delivers half of the resultant into the roof diaphragm.
    roof_reaction_uls_kn = 0.5 * total_uls_kn
    roof_reaction_sls_kn = 0.5 * total_sls_kn
    # Roof braces run in discrete panels between adjacent end-wall mullion
    # lines. Each panel carries only the roof reaction from its tributary strip.
    governing_roof_panel_index = max(range(len(roof_panel_areas)), key=roof_panel_areas.__getitem__)
    max_roof_panel_area_m2 = roof_panel_areas[governing_roof_panel_index]
    roof_panel_widths = tuple(max(float(width), 0.0) for width in inputs.roof_panel_widths_mm)
    governing_roof_panel_width_mm = (
        roof_panel_widths[governing_roof_panel_index]
        if governing_roof_panel_index < len(roof_panel_widths)
        else span_mm
    )
    roof_shear_uls_kn = 0.5 * max(float(inputs.ultimate_pressure_kpa), 0.0) * max_roof_panel_area_m2
    roof_shear_sls_kn = 0.5 * max(float(inputs.service_pressure_kpa), 0.0) * max_roof_panel_area_m2
    wall_shear_uls_kn = roof_reaction_uls_kn / (wall_sides * braced_bays)
    wall_shear_sls_kn = roof_reaction_sls_kn / (wall_sides * braced_bays)

    roof_plane_width_mm = governing_roof_panel_width_mm / max(math.cos(pitch_rad), 1e-9)
    roof_uls = _diagonal_response(roof_shear_uls_kn, bay_mm, roof_plane_width_mm, inputs.roof_brace_area_mm2, inputs)
    roof_sls = _diagonal_response(roof_shear_sls_kn, bay_mm, roof_plane_width_mm, inputs.roof_brace_area_mm2, inputs)
    wall_uls = _diagonal_response(wall_shear_uls_kn, bay_mm, eave_mm, inputs.wall_brace_area_mm2, inputs)
    wall_sls = _diagonal_response(wall_shear_sls_kn, bay_mm, eave_mm, inputs.wall_brace_area_mm2, inputs)
    nodal_areas_m2 = [0.0] * (len(roof_panel_areas) + 1)
    for index, panel_area_m2 in enumerate(roof_panel_areas):
        nodal_areas_m2[index] += 0.5 * panel_area_m2
        nodal_areas_m2[index + 1] += 0.5 * panel_area_m2

    strut_lines = []
    for index, node_line in enumerate(inputs.roof_node_lines):
        tributary_area_m2 = nodal_areas_m2[index] if index < len(nodal_areas_m2) else 0.0
        demand_kn = 0.5 * max(float(inputs.ultimate_pressure_kpa), 0.0) * tributary_area_m2
        service_demand_kn = 0.5 * max(float(inputs.service_pressure_kpa), 0.0) * tributary_area_m2
        selection = _select_strut(demand_kn, inputs.strut_candidates, bay_mm)
        strut_lines.append({
            **node_line,
            "tributary_area_m2": tributary_area_m2,
            "demand_kn": demand_kn,
            "service_demand_kn": service_demand_kn,
            "selection": selection,
        })
    eave_struts = [item for item in strut_lines if item.get("type") == "eave_strut"]
    mullion_ties = [item for item in strut_lines if item.get("type") == "mullion_tie"]
    ridge_struts = [item for item in strut_lines if item.get("type") == "ridge_strut"]
    governing_strut_line = max(
        strut_lines,
        key=lambda item: item["selection"].get("ratio", math.inf),
        default=None,
    )
    strut = governing_strut_line["selection"] if governing_strut_line else {
        "status": "NOT OK", "label": "No roof strut lines", "capacity_kn": 0.0, "ratio": math.inf,
    }
    strut_demand_kn = governing_strut_line["demand_kn"] if governing_strut_line else 0.0
    governing_ratio = max(roof_uls["capacity_ratio"], wall_uls["capacity_ratio"], strut.get("ratio", math.inf))

    return {
        "status": "OK" if governing_ratio <= 1.0 else "NOT OK",
        "method": "separate longitudinal roof-plane and side-wall bracing load paths",
        "projected_end_wall_area_m2": end_wall_area_m2,
        "roof_rise_mm": roof_rise_mm,
        "ultimate_pressure_kpa": inputs.ultimate_pressure_kpa,
        "service_pressure_kpa": inputs.service_pressure_kpa,
        "ultimate_case": inputs.ultimate_case,
        "service_case": inputs.service_case,
        "ultimate_resultant_kn": total_uls_kn,
        "service_resultant_kn": total_sls_kn,
        "roof_reaction_uls_kn": roof_reaction_uls_kn,
        "roof_reaction_sls_kn": roof_reaction_sls_kn,
        "roof_panel_count": roof_panel_count,
        "roof_panel_width_mm": governing_roof_panel_width_mm,
        "max_roof_panel_area_m2": max_roof_panel_area_m2,
        "wall_side_count": wall_sides,
        "braced_bays_per_side": braced_bays,
        "roof_brace_label": inputs.roof_brace_label,
        "wall_brace_label": inputs.wall_brace_label,
        "roof_uls": roof_uls,
        "roof_sls": roof_sls,
        "wall_uls": wall_uls,
        "wall_sls": wall_sls,
        "strut_demand_kn": strut_demand_kn,
        "strut": strut,
        "eave_struts": eave_struts,
        "mullion_ties": mullion_ties,
        "ridge_struts": ridge_struts,
        "governing_strut_line": governing_strut_line,
        "governing_capacity_ratio": governing_ratio,
        "limitations": [
            "End-wall roof reaction assumes vertical strips simply supported at ground and roof.",
            "Brace checks cover gross-section yielding and elastic elongation only.",
            "Purlin strut checks use a conservative estimated weak-axis inertia pending manufacturer axial data.",
            "Connections, net section, block shear and diaphragm distribution are not yet checked.",
        ],
    }
