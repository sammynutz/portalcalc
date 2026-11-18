"""Model generation and analysis: geometry, loads, load combinations and the envelope solve."""

import copy
import json
import math
import re
from dataclasses import replace

import numpy as np

from portalcalc.bracing.restraint import (
    OPTION as BRACING_3D_RESTRAINT_OPTION,
    restraint_status as bracing_restraint_status,
)
from portalcalc.frame.common import (
    GIRT_DEPTH_DEFAULT_MM,
    internal_column_positions_mm,
    mm_from_metres_text,
    pitch_compensated_gable_top_stations,
    truss_centreline_vertical_depth,
    PURLIN_DEPTH_DEFAULT_MM,
)
from portalcalc.frame.solver import apply_eave_strip_loads, FrameElement2D, Node, Structure2D
from portalcalc.loads import (
    apply_roof_vertical_loads,
    apply_roof_vertical_loads_in_zones,
    apply_structure_self_weight,
    apply_wall_horizontal_loads,
    BasicLoadCase,
    BasicRoofLoads,
    crane_case,
    WallLoads,
)
from portalcalc.storage_loads import (
    apply_storage_loads_to_columns,
    calculate_storage_loads,
    is_centre_stub_storage_mode,
    storage_factor_for_combination,
    storage_summary_lines,
)
from portalcalc.wind import (
    action_combination_factors,
    add_roof_panel_pressure,
    roof_outward_normal,
    apply_roof_cpi_to_top_nodes,
    calculate_wind,
    CPE_CASE_OPTIONS,
    CPI_BY_FRAME_TYPE,
    CPI_CASE_OPTIONS,
    cyclonic_enclosed_cpi,
    effective_cpi_case_for_wind,
    free_roof_cpn,
    internal_pressure_factor,
    open_canopy_cpi,
    opening_area_reduction_factor,
    transverse_free_roof_cpn,
    wall_cpe_for_surface,
    wall_cpe_line_load_kn_m,
    wall_cpi_line_load_kn_m,
    wall_surface_for_cpe_case,
    wind_pressure_scale,
)


def _y_at_x(nodes, x):
    ordered = sorted(nodes, key=lambda node: node.x)
    if x <= ordered[0].x:
        return ordered[0].y
    if x >= ordered[-1].x:
        return ordered[-1].y
    for n1, n2 in zip(ordered[:-1], ordered[1:]):
        if min(n1.x, n2.x) - 1e-9 <= x <= max(n1.x, n2.x) + 1e-9:
            dx = n2.x - n1.x
            if abs(dx) <= 1e-9:
                return n1.y
            t = (x - n1.x) / dx
            return n1.y + t * (n2.y - n1.y)
    return ordered[-1].y


def _slope_node_length(nodes):
    return sum(math.hypot(n2.x - n1.x, n2.y - n1.y) for n1, n2 in zip(nodes[:-1], nodes[1:]))


def _slope_midspan_check(key, label, nodes):
    length_mm = _slope_node_length(nodes)
    if length_mm <= 1e-9:
        return None
    station = 0.0
    stations = [0.0]
    for n1, n2 in zip(nodes[:-1], nodes[1:]):
        station += math.hypot(n2.x - n1.x, n2.y - n1.y)
        stations.append(station)
    target = length_mm / 2.0
    node = min(zip(nodes, stations), key=lambda item: abs(item[1] - target))[0]
    node_station = min(zip(nodes, stations), key=lambda item: abs(item[1] - target))[1]
    return {
        "key": key,
        "label": label,
        "node": node,
        "start": nodes[0],
        "end": nodes[-1],
        "station_mm": node_station,
        "length_mm": length_mm,
    }


def _slope_path_length_m(nodes):
    total = 0.0
    for n1, n2 in zip(nodes[:-1], nodes[1:]):
        total += math.hypot((n2.x - n1.x) / 1000.0, (n2.y - n1.y) / 1000.0)
    return total


def _wind_interval_load_kn(strips, start_m, end_m, scale):
    total_kn = 0.0
    for a, b, q_kn_m in strips:
        overlap = max(0.0, min(end_m, b) - max(start_m, a))
        total_kn += q_kn_m * scale * overlap
    return total_kn


def _wind_line_at_end(strips, total_m, at_start, scale):
    """kN/m of the wind strip at the start (s = 0) or end (s = total) of a roof path."""
    for a, b, q_kn_m in (strips if at_start else reversed(list(strips))):
        if (a <= 1e-9 < b) if at_start else (a < total_m - 1e-9 <= b + 1e-9):
            return q_kn_m * scale
    return 0.0


def _cumulative_roof_distances(nodes):
    distances = [0.0]
    for n1, n2 in zip(nodes[:-1], nodes[1:]):
        distances.append(distances[-1] + math.hypot((n2.x - n1.x) / 1000.0, (n2.y - n1.y) / 1000.0))
    return distances


def _continuous_roof_strips_for_nodes(wind_result, cpe_case, nodes):
    case = str(cpe_case).strip()
    if case.startswith("End") or wind_result.inputs.roof_pitch_deg < 10.0 or len(nodes) < 2:
        return wind_result.case_roof_strips(cpe_case)
    if getattr(wind_result.inputs, "roof_shape", "Gable") == "Skillion":
        return wind_result.case_roof_strips(cpe_case)

    distances = _cumulative_roof_distances(nodes)
    total = distances[-1]
    if total <= 1e-9:
        return []
    ridge_index = max(range(len(nodes)), key=lambda index: nodes[index].y)
    ridge_s = min(max(distances[ridge_index], 0.0), total)
    use_max = case.endswith("+")

    def zone_line_load(name):
        zone = next((item for item in wind_result.roof_zones if item.name == name), None)
        if zone is None:
            return 0.0
        return zone.line_load_max_kn_m if use_max else zone.line_load_min_kn_m

    upwind_q = zone_line_load("Upwind rafter")
    downwind_q = zone_line_load("Downwind rafter")
    if case.startswith("Right"):
        strips = [(0.0, ridge_s, downwind_q), (ridge_s, total, upwind_q)]
    else:
        strips = [(0.0, ridge_s, upwind_q), (ridge_s, total, downwind_q)]
    return [(a, b, q) for a, b, q in strips if b - a > 1e-9]


def _canopy_free_roof_part(side_key, cpe_case):
    case = str(cpe_case).strip()
    if case.startswith("End"):
        return "windward"
    left_wind = case.startswith("Left")
    if side_key == "left":
        return "windward" if left_wind else "leeward"
    return "leeward" if left_wind else "windward"


def _add_vertical_line_load_to_nodes(target_structure, nodes, line_kn_m):
    arrows = []
    for n1, n2 in zip(nodes[:-1], nodes[1:]):
        dx = (n2.x - n1.x) / 1000.0
        dy = (n2.y - n1.y) / 1000.0
        panel_len_m = math.hypot(dx, dy)
        total_n = line_kn_m * panel_len_m * 1000.0
        target_structure.add_load(n1.uy, total_n / 2.0)
        target_structure.add_load(n2.uy, total_n / 2.0)
        arrows.append(((n1.x + n2.x) / 2.0, (n1.y + n2.y) / 2.0 + 800.0, 0.0, line_kn_m * 1000.0))
    return arrows


def _add_normal_line_load_to_nodes(target_structure, nodes, line_kn_m):
    arrows = []
    for n1, n2 in zip(nodes[:-1], nodes[1:]):
        dx_m = (n2.x - n1.x) / 1000.0
        dy_m = (n2.y - n1.y) / 1000.0
        panel_len_m = math.hypot(dx_m, dy_m)
        if panel_len_m <= 1e-12:
            continue
        # Positive free-roof Cpn frame load is uplift, so choose the
        # segment normal with positive global-Y component.
        nx = -dy_m / panel_len_m
        ny = dx_m / panel_len_m
        if ny < 0.0:
            nx = -nx
            ny = -ny
        total_fx_n = line_kn_m * panel_len_m * 1000.0 * nx
        total_fy_n = line_kn_m * panel_len_m * 1000.0 * ny
        target_structure.add_load(n1.ux, total_fx_n / 2.0)
        target_structure.add_load(n1.uy, total_fy_n / 2.0)
        target_structure.add_load(n2.ux, total_fx_n / 2.0)
        target_structure.add_load(n2.uy, total_fy_n / 2.0)
        arrows.append((
            (n1.x + n2.x) / 2.0,
            (n1.y + n2.y) / 2.0 + 800.0,
            line_kn_m * 1000.0 * nx,
            line_kn_m * 1000.0 * ny,
        ))
    return arrows


def _add_normal_uniform_line_load_to_members(target_structure, nodes, line_kn_m, element_group):
    """Apply a roof-normal UDL to each matching top-chord member segment."""
    arrows = []
    for n1, n2 in zip(nodes[:-1], nodes[1:]):
        dx_m = (n2.x - n1.x) / 1000.0
        dy_m = (n2.y - n1.y) / 1000.0
        panel_len_m = math.hypot(dx_m, dy_m)
        if panel_len_m <= 1e-12:
            continue

        # Positive canopy wind load is uplift. Select the segment normal
        # with a positive global-Y component, then resolve the UDL into
        # global components for the frame-element member-load routine.
        nx = -dy_m / panel_len_m
        ny = dx_m / panel_len_m
        if ny < 0.0:
            nx = -nx
            ny = -ny
        fx_n_per_mm = line_kn_m * nx  # 1 kN/m = 1 N/mm
        fy_n_per_mm = line_kn_m * ny

        if hasattr(target_structure, "add_uniform_global_line_load_between"):
            matched = target_structure.add_uniform_global_line_load_between(
                n1,
                n2,
                fx_n_per_mm=fx_n_per_mm,
                fy_n_per_mm=fy_n_per_mm,
                element_groups={element_group},
            )
            if not matched:
                raise ValueError(
                    f"No {element_group} member found for the requested canopy wind line load."
                )
        else:
            # Lightweight arrow collectors do not contain frame elements.
            # Preserve the equivalent resultant solely for their display data.
            total_fx_n = fx_n_per_mm * panel_len_m * 1000.0
            total_fy_n = fy_n_per_mm * panel_len_m * 1000.0
            target_structure.add_load(n1.ux, total_fx_n / 2.0)
            target_structure.add_load(n1.uy, total_fy_n / 2.0)
            target_structure.add_load(n2.ux, total_fx_n / 2.0)
            target_structure.add_load(n2.uy, total_fy_n / 2.0)

        arrows.append((
            (n1.x + n2.x) / 2.0,
            (n1.y + n2.y) / 2.0 + 800.0,
            line_kn_m * 1000.0 * nx,
            line_kn_m * 1000.0 * ny,
        ))
    return arrows


def _enclosed_cpi_for_case(wind_result, cpi_case):
    cpi_max, cpi_min = (
        cyclonic_enclosed_cpi(wind_result.inputs, "Enclosed")
        or CPI_BY_FRAME_TYPE["Enclosed"]
    )
    return cpi_max if str(cpi_case).strip() == "Cpi +" else cpi_min


def _table_5_1b_cpi_for_opening(wind_result, cpi_case, cpe_case, wall_key, opening_area_m2, other_open_area_m2):
    surface = wall_surface_for_cpe_case(cpe_case, wall_key)
    cpe = wall_cpe_for_surface(wind_result, surface)
    ratio = float("inf") if other_open_area_m2 <= 1e-12 else opening_area_m2 / other_open_area_m2
    ka = opening_area_reduction_factor(surface, opening_area_m2)
    kl = 1.0
    signed_case = str(cpi_case).strip()

    if ratio <= 0.5:
        return 0.0 if signed_case == "Cpi +" else -0.3, ratio, surface, ka, kl
    if ratio < 2.0:
        if surface == "windward_wall":
            return 0.2 if signed_case == "Cpi +" else -0.1, ratio, surface, ka, kl
        return 0.0 if signed_case == "Cpi +" else -0.3, ratio, surface, ka, kl
    if surface == "windward_wall" and ratio < 3.0:
        multiplier = 0.7
    elif surface == "windward_wall" and ratio < 6.0:
        multiplier = 0.85
    else:
        multiplier = 1.0
    return multiplier * ka * kl * cpe, ratio, surface, ka, kl


def _add_horizontal_line_load(
    target_structure,
    base,
    top,
    line_kn_m,
    direction,
    element_group=None,
    arrow_count=5,
):
    if base is None or top is None:
        return []
    height_m = abs(top.y - base.y) / 1000.0
    if height_m <= 1e-9:
        return []
    total_n = line_kn_m * height_m * 1000.0 * direction
    if hasattr(target_structure, "add_uniform_global_line_load_between"):
        groups = {element_group} if element_group else None
        matched = target_structure.add_uniform_global_line_load_between(
            base,
            top,
            fx_n_per_mm=line_kn_m * direction,
            element_groups=groups,
        )
        if not matched:
            raise ValueError(
                f"No {element_group or 'column'} elements found for wall load between "
                f"({base.x:.0f}, {base.y:.0f}) and ({top.x:.0f}, {top.y:.0f})."
            )
    arrows = []
    count = max(2, arrow_count)
    for i in range(count):
        t = (i + 0.5) / count
        x = base.x + (top.x - base.x) * t
        y = base.y + (top.y - base.y) * t
        arrows.append((x, y, line_kn_m * 1000.0 * direction, 0.0))
    return arrows


def _add_signed_horizontal_line_load(
    target_structure,
    base,
    top,
    signed_line_kn_m,
    element_group=None,
    arrow_count=5,
):
    if abs(signed_line_kn_m) <= 1e-12:
        return []
    direction = +1.0 if signed_line_kn_m > 0.0 else -1.0
    return _add_horizontal_line_load(
        target_structure,
        base,
        top,
        abs(signed_line_kn_m),
        direction,
        element_group,
        arrow_count,
    )


def _signed_internal_cpi_line_kn_m(wind_result, cpi, wind_pressure):
    return (
        cpi
        * wind_result.wu_kn_m2
        * wind_pressure_scale(wind_result, wind_pressure)
        * internal_pressure_factor(wind_result, cpi)
        * wind_result.inputs.bay_size_m
    )


def _arrow_collector():
    class LoadOnlyStructure:
        def add_load(self, dof, value):
            return None

    return LoadOnlyStructure()


def _wind_case_for_single_label(label):
    if label.startswith("Wu Cpe Left +"):
        return "Left +", "ultimate"
    if label.startswith("Wu Cpe Left -"):
        return "Left -", "ultimate"
    if label.startswith("Wu Cpe Right +"):
        return "Right +", "ultimate"
    if label.startswith("Wu Cpe Right -"):
        return "Right -", "ultimate"
    if label.startswith("Ws Cpe Left +"):
        return "Left +", "serviceability"
    if label.startswith("Ws Cpe Left -"):
        return "Left -", "serviceability"
    if label.startswith("Ws Cpe Right +"):
        return "Right +", "serviceability"
    if label.startswith("Ws Cpe Right -"):
        return "Right -", "serviceability"
    if label.startswith("Wu Cpe End +"):
        return "End +", "ultimate"
    if label.startswith("Wu Cpe End -"):
        return "End -", "ultimate"
    if label.startswith("Ws Cpe End +"):
        return "End +", "serviceability"
    if label.startswith("Ws Cpe End -"):
        return "End -", "serviceability"
    return None, None


class SolveMixin:
    """Builds the 2D frame model from the inputs and solves every load combination."""

    def solve_structure(self, structure):
        if getattr(self, "second_order_checkbox", None) is not None and self.second_order_checkbox.isChecked():
            return structure.solve_second_order()
        d, R, F = structure.solve()
        info = {
            "type": "first_order",
            "converged": True,
            "iterations": 1,
            "first_order_max_abs_mm": max(float(np.max(np.abs(d))), 0.0),
            "second_order_max_abs_mm": max(float(np.max(np.abs(d))), 0.0),
            "displacement_amplification": 1.0,
        }
        return d, R, F, info

    def apply_base_support(self, structure, node, support_type, spring_k_nmm_per_rad=0.0):
        structure.add_support(node.ux)
        structure.add_support(node.uy)
        if support_type == "Fixed":
            structure.add_support(node.rz)
        elif support_type == "Spring":
            structure.add_spring(node.rz, spring_k_nmm_per_rad)

    def column_cladding_restraints(self, frame_type):
        if hasattr(frame_type, "left_wall_clad") and hasattr(frame_type, "right_wall_clad"):
            return {
                "LEFT_COLUMN": bool(frame_type.left_wall_clad),
                "RIGHT_COLUMN": bool(frame_type.right_wall_clad),
            }
        if frame_type == "Enclosed":
            return {"LEFT_COLUMN": True, "RIGHT_COLUMN": True}
        if frame_type in {"3 Sided", "2 Sided", "1 Sided"}:
            return {"LEFT_COLUMN": True, "RIGHT_COLUMN": False}
        return {"LEFT_COLUMN": False, "RIGHT_COLUMN": False}

    def roof_slope_node_sets(self, top_nodes, roof_shape=None):
        if len(top_nodes) < 2:
            return []
        roof_shape = roof_shape or self.current_roof_shape()
        if roof_shape == "Skillion":
            return [top_nodes]
        apex_index = max(range(len(top_nodes)), key=lambda i: top_nodes[i].y)
        slopes = [top_nodes[:apex_index + 1], list(reversed(top_nodes[apex_index:]))]
        return [nodes for nodes in slopes if len(nodes) >= 2]

    def member_check_restraint_lengths(
        self,
        frame_type,
        left_base,
        right_base,
        bottom_nodes,
        purlin_layout=None,
        column_top_deduction_mm=0.0,
        crane_minor_height_mm=None,
        clad_column_heights_mm=None,
        storage_restraint_levels_mm=None,
    ):
        cladding = self.column_cladding_restraints(frame_type)
        clad_column_heights_mm = clad_column_heights_mm or {}
        storage_restraint_levels_mm = storage_restraint_levels_mm or {}
        column_top_deduction_mm = max(column_top_deduction_mm, 0.0)
        left_unclad_length = max(abs(bottom_nodes[0].y - left_base.y) - column_top_deduction_mm, 0.0)
        right_unclad_length = max(abs(bottom_nodes[-1].y - right_base.y) - column_top_deduction_mm, 0.0)
        top_chord_minor_length = purlin_layout.max_spacing_mm if purlin_layout is not None else 1700.0
        bottom_chord_minor_length = 2.0 * top_chord_minor_length
        flybraced_length = 2.0 * top_chord_minor_length

        crane_minor_height_mm = crane_minor_height_mm if crane_minor_height_mm and crane_minor_height_mm > 0.0 else None

        def column_lengths(group, unclad_length, base_y, top_y):
            if cladding[group]:
                clad_height_mm = max(float(clad_column_heights_mm.get(group, 0.0) or 0.0), abs(top_y - base_y))
                flybraced_column_length = max(clad_height_mm / 2.0, 0.0)
                major_length = flybraced_column_length
                minor_length = flybraced_column_length
                clad = True
            else:
                major_length = unclad_length
                minor_length = unclad_length
                clad = False
            restraint_levels = sorted(
                level for level in storage_restraint_levels_mm.get(group, [])
                if min(base_y, top_y) + 1e-6 < level < max(base_y, top_y) - 1e-6
            )
            if restraint_levels:
                stations = [base_y] + restraint_levels + [top_y]
                storage_segment_length = max(abs(y2 - y1) for y1, y2 in zip(stations[:-1], stations[1:]))
                major_length = min(major_length, storage_segment_length) if major_length > 0.0 else storage_segment_length
                minor_length = min(minor_length, storage_segment_length) if minor_length > 0.0 else storage_segment_length
            if crane_minor_height_mm is not None and group in {"LEFT_COLUMN", "RIGHT_COLUMN"}:
                minor_length = min(minor_length, crane_minor_height_mm) if minor_length > 0.0 else crane_minor_height_mm
            return {
                "major_mm": major_length,
                "minor_mm": minor_length,
                # Fallback factors for dedicated crane/storage checks. Ordinary
                # clad columns resolve automatic segments from solved ULS cases.
                "ke": 1.0 if clad else 0.85,
                "am": 1.3 if clad else 1.75,
                "kt": 1.0,
                "kl": 1.0,
                "kr": 1.0,
                "clad": clad,
                "crane_minor": crane_minor_height_mm is not None and group in {"LEFT_COLUMN", "RIGHT_COLUMN"},
                "storage_restraint": bool(restraint_levels),
            }

        restraints = {
            "LEFT_COLUMN": column_lengths("LEFT_COLUMN", left_unclad_length, left_base.y, bottom_nodes[0].y),
            "RIGHT_COLUMN": column_lengths("RIGHT_COLUMN", right_unclad_length, right_base.y, bottom_nodes[-1].y),
            "WEB": {"major_mm": None, "minor_mm": None, "ke": 1.0, "am": 1.0, "kt": 1.0, "kl": 1.0, "kr": 1.0, "clad": None},
            "LEFT_CANOPY_WEB": {"major_mm": None, "minor_mm": None, "ke": 1.0, "am": 1.0, "kt": 1.0, "kl": 1.0, "kr": 1.0, "clad": None},
            "RIGHT_CANOPY_WEB": {"major_mm": None, "minor_mm": None, "ke": 1.0, "am": 1.0, "kt": 1.0, "kl": 1.0, "kr": 1.0, "clad": None},
            "TOP": {"major_mm": None, "minor_mm": top_chord_minor_length, "ke": 1.0, "am": 1.0, "kt": 1.0, "kl": 1.0, "kr": 1.0, "clad": None},
            "BOTTOM": {"major_mm": None, "minor_mm": bottom_chord_minor_length, "ke": 1.0, "am": 1.0, "kt": 1.0, "kl": 1.0, "kr": 1.0, "clad": None},
            "LEFT_CANOPY_TOP": {"major_mm": None, "minor_mm": top_chord_minor_length, "ke": 1.0, "am": 1.0, "kt": 1.0, "kl": 1.0, "kr": 1.0, "clad": None},
            "LEFT_CANOPY_BOTTOM": {"major_mm": None, "minor_mm": top_chord_minor_length, "ke": 1.0, "am": 1.0, "kt": 1.0, "kl": 1.0, "kr": 1.0, "clad": None},
            "RIGHT_CANOPY_TOP": {"major_mm": None, "minor_mm": top_chord_minor_length, "ke": 1.0, "am": 1.0, "kt": 1.0, "kl": 1.0, "kr": 1.0, "clad": None},
            "RIGHT_CANOPY_BOTTOM": {"major_mm": None, "minor_mm": top_chord_minor_length, "ke": 1.0, "am": 1.0, "kt": 1.0, "kl": 1.0, "kr": 1.0, "clad": None},
            "LEFT_LEAN_COLUMN": {"major_mm": None, "minor_mm": None, "ke": 1.0, "am": 1.0, "kt": 1.0, "kl": 1.0, "kr": 1.0, "clad": False},
            "RIGHT_LEAN_COLUMN": {"major_mm": None, "minor_mm": None, "ke": 1.0, "am": 1.0, "kt": 1.0, "kl": 1.0, "kr": 1.0, "clad": False},
        }
        if getattr(self, "left_canopy_frame_system_combo", None) is not None and self.canopy_side_is_rafter("left"):
            restraints["LEFT_CANOPY_TOP"].update({"major_mm": flybraced_length, "minor_mm": top_chord_minor_length})
        if getattr(self, "right_canopy_frame_system_combo", None) is not None and self.canopy_side_is_rafter("right"):
            restraints["RIGHT_CANOPY_TOP"].update({"major_mm": flybraced_length, "minor_mm": top_chord_minor_length})
        return restraints

    def column_half_width_for_span_offset(self, profile):
        return (profile.depth or profile.flange_width or 0.0) / 2.0

    @staticmethod
    def secondary_member_depth_mm(check, default_mm):
        """Depth of the purlin/girt a check selected ('C/Z 250 15' -> 250), else the default.

        Returns (depth, source) so the results can say when the default was used.
        """
        match = (check or {}).get("match") or {}
        if match.get("status") == "OK":
            found = re.search(r"(\d{3})", str(match.get("section", "")))
            if found:
                return float(found.group(1)), match["section"]
        return float(default_mm), f"default {default_mm:g} mm (no passing section)"

    def eave_node_basis(self, eave_mm, pitch_deg, roof_shape, left_column, right_column, top_chord_depth_mm,
                        purlin_check, girt_check):
        """Top chord node height at the column centrelines from the eave pitching point.

        The eave height is the roof cladding pitching point on the wall
        cladding line (outside of the girts). Building width stays to the
        outside of the column flanges, so the cladding line is the girt depth g
        outside the flange and the column centreline is g + D/2 inside it. The
        top chord centreline is the purlin depth p plus half the chord depth t
        below the cladding, square to the slope:

            node = H + (g + D/2) tan(pitch) - (p + t/2) / cos(pitch)

        A gable uses the mean of the two sides' g + D/2 (both eaves at H, ridge
        on the truss centreline); a skillion uses its low (left) side.
        """
        girt, girt_source = self.secondary_member_depth_mm(girt_check, GIRT_DEPTH_DEFAULT_MM)
        purlin, purlin_source = self.secondary_member_depth_mm(purlin_check, PURLIN_DEPTH_DEFAULT_MM)
        # The eave purlins are in the local (edge) pressure zone: take the
        # deeper of the general and edge-zone selections.
        local = {"match": (purlin_check or {}).get("local_match")}
        local_depth, local_source = self.secondary_member_depth_mm(local, 0.0)
        if local_depth > purlin:
            purlin, purlin_source = local_depth, f"{local_source} (edge zone)"
        offsets = {side: girt + self.column_half_width_for_span_offset(profile)
                   for side, profile in (("left", left_column), ("right", right_column))}
        inset = offsets["left"] if roof_shape == "Skillion" else 0.5 * (offsets["left"] + offsets["right"])
        theta = math.radians(pitch_deg)
        drop = (purlin + 0.5 * top_chord_depth_mm) / math.cos(theta)
        node = eave_mm + inset * math.tan(theta) - drop
        return {"eave_mm": eave_mm, "node_mm": node, "girt_mm": girt, "girt_source": girt_source,
                "purlin_mm": purlin, "purlin_source": purlin_source, "top_chord_depth_mm": top_chord_depth_mm,
                "column_offsets_mm": offsets, "inset_mm": inset, "drop_mm": drop}

    def held_panel_count_key(self):
        """What a chosen main truss panel count was chosen for: span, roof shape, frame system."""
        return (self.span_input.text().strip(), self.current_roof_shape(), self.frame_system_combo.currentText())

    def hold_main_panel_count(self, count):
        """Keep a deliberately chosen main truss panel count (Check panels, Optimise
        frame + bracing, a saved preset) through later input changes, until the
        span, roof shape or frame system changes. The geometry's default count
        is not held, so it keeps following the truss depth."""
        if count is None or self.frame_system_combo.currentText() != "Truss"                 or int(count) == getattr(self, "_default_main_panel_count", None):
            self._held_main_panel_count = None
            self._held_main_panel_key = None
            return
        self._held_main_panel_count = int(count)
        self._held_main_panel_key = self.held_panel_count_key()

    def held_main_panel_count(self):
        """The held panel count, or None (dropped once its span/roof/frame system changes)."""
        count = getattr(self, "_held_main_panel_count", None)
        if count is not None and getattr(self, "_held_main_panel_key", None) != self.held_panel_count_key():
            self._held_main_panel_count = self._held_main_panel_key = None
            return None
        return count

    def panel_topology_input_signature(self):
        """Stable input signature excluding hidden calculated web outputs."""
        data = copy.deepcopy(self.collect_preset_data())
        if isinstance(data, dict) and not data.get("future_canopy_design"):
            data.pop("calculation_service", None)   # the held panel count is the output, not an input
        sections = data.get("sections", {}) if isinstance(data, dict) else {}
        for key in ("webs_posts", "left_canopy_webs_posts", "right_canopy_webs_posts"):
            sections.pop(key, None)
        return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)

    def panel_topology_search_allowed(self):
        return not (
            getattr(self, "_suppress_panel_topology_search", False)
            or getattr(self, "_preserve_web_assignments_on_solve", False)
            or self.optimisation_load_vector_cache is not None
        )

    def main_panel_topology_needs_rescue(self):
        if self.frame_system_combo.currentText() != "Truss":
            return False
        if (self.web_split_failures or {}).get("WEB"):
            return True
        return any(
            zone.get("auto_web_joint_doubler") and zone.get("group") in {"TOP", "BOTTOM"}
            for zone in (self.design_envelope_actions or {}).get("doubled_chord_zones", [])
            if isinstance(zone, dict)
        )

    def panel_topology_candidate_result(self, panel_count):
        active_specs = self.active_auto_size_group_specs()
        ratio, group, check = self.auto_size_overall_ratio(active_specs)
        return {
            "panel_count": int(panel_count),
            "ratio": float(ratio),
            "group": group,
            "check": check,
            "cost": float((self.costing_summary or {}).get("total_cost", float("inf"))),
            "passes": self.auto_size_design_acceptable(active_specs, ratio),
            "joint_doubler_segments": sum(
                1 for zone in (self.design_envelope_actions or {}).get("doubled_chord_zones", [])
                if isinstance(zone, dict)
                and zone.get("auto_web_joint_doubler")
                and zone.get("group") in {"TOP", "BOTTOM"}
            ),
        }

    def refine_main_truss_panel_topology(self, force=False, signature=None):
        """Bounded exact comparison of the adjacent valid main-truss layouts."""
        if self.structure is None or self.frame_system_combo.currentText() != "Truss":
            return "", 0
        if not force and not self.main_panel_topology_needs_rescue():
            return "", 0
        try:
            current_count = int(self.panel_count_label.text())
        except (TypeError, ValueError):
            return "", 0
        roof_shape = self.current_roof_shape()
        step = 4 if roof_shape == "Gable" else 1
        trial_counts = sorted({count for count in (current_count - step, current_count + step) if count >= 4})
        if not trial_counts:
            return "", 0

        base_result = self.panel_topology_candidate_result(current_count)
        results = [base_result]
        evaluated_counts = {current_count}
        solves = 0
        last_trial_count = current_count
        previous_override = getattr(self, "_panel_count_override", None)
        try:
            for trial_count in trial_counts:
                self._panel_count_override = trial_count
                self._generate_and_solve_once()
                solves += 1
                if self.structure is None:
                    continue
                last_trial_count = trial_count
                results.append(self.panel_topology_candidate_result(trial_count))
                evaluated_counts.add(trial_count)

            def choose_result(candidate_results):
                passing = [result for result in candidate_results if result["passes"]]
                if not passing:
                    return base_result
                return min(
                    passing,
                    key=lambda result: (
                        result["cost"],
                        result["joint_doubler_segments"],
                        abs(result["panel_count"] - current_count),
                    ),
                )

            chosen = choose_result(results)
            if chosen["panel_count"] != current_count:
                direction = 1 if chosen["panel_count"] > current_count else -1
                outward_count = chosen["panel_count"] + direction * step
                if outward_count >= 4 and outward_count not in evaluated_counts:
                    self._panel_count_override = outward_count
                    self._generate_and_solve_once()
                    solves += 1
                    if self.structure is not None:
                        last_trial_count = outward_count
                        results.append(self.panel_topology_candidate_result(outward_count))
                        evaluated_counts.add(outward_count)
                        chosen = choose_result(results)

            self._panel_count_override = chosen["panel_count"]
            if chosen["panel_count"] != last_trial_count:
                self._generate_and_solve_once()
                solves += 1
                if self.structure is not None:
                    chosen = self.panel_topology_candidate_result(chosen["panel_count"])

            self._panel_topology_cache_signature = signature or self.panel_topology_input_signature()
            self._panel_topology_cache_count = chosen["panel_count"]
            self.hold_main_panel_count(chosen["panel_count"])
            saving = base_result["cost"] - chosen["cost"]
            if chosen["panel_count"] == current_count:
                report = (
                    f"Panel topology: retained {current_count} panels after trials "
                    f"{sorted(evaluated_counts)} | "
                    f"ratio {chosen['ratio']:.3f} {chosen['group']} {chosen['check']}"
                )
            else:
                report = (
                    f"Panel topology: {current_count} -> {chosen['panel_count']} panels | "
                    f"trials {sorted(evaluated_counts)} | "
                    f"ratio {chosen['ratio']:.3f} {chosen['group']} {chosen['check']} | "
                    f"joint-doubler segments {base_result['joint_doubler_segments']} -> "
                    f"{chosen['joint_doubler_segments']} | saving ${saving:,.0f}"
                )
            self.panel_topology_search_report = report
            return report, solves
        except Exception:
            self._panel_count_override = previous_override
            raise

    def on_frame_load_basis_changed(self, *_):
        """Desktop toggle: one frame-anywhere envelope, or every gridline's building loads."""
        combo = getattr(self, "frame_load_basis_combo", None)
        basis = combo.currentData() if combo is not None else "single_frame"
        self.building_load_mode = None if basis in (None, "single_frame") else basis
        self._building_loads_cache = None

    def frame_load_basis(self):
        combo = getattr(self, "frame_load_basis_combo", None)
        return (combo.currentData() if combo is not None else None) or "single_frame"

    def apply_frame_load_basis(self, basis):
        """Set the toggle from a preset. A service/review building drives the mode itself."""
        if getattr(self, "building_model_override", None) is not None:
            return
        combo = self.frame_load_basis_combo
        index = combo.findData(basis or "single_frame")
        combo.setCurrentIndex(index if index >= 0 else 0)
        self.on_frame_load_basis_changed()

    def current_building_frame_loads(self):
        """Per-gridline building loads for the current inputs (phase 4).

        Uses ``building_model_override`` when set (a BOLT building), else a
        uniform building from the current preset. Cached on the inputs that
        affect loads, so optimiser solves reuse it.
        """
        from portalcalc.building.loads import building_loads
        from portalcalc.building.model import building_model_from_preset
        from portalcalc.frame.frame_loading import distinct_frames
        mode = self.building_load_mode
        model = getattr(self, "building_model_override", None)
        preset = None
        if model is None:
            preset = self.collect_preset_data()
            relevant = {key: preset.get(key) for key in ("geometry", "canopies", "loads", "wind")}
            relevant["geometry"] = {k: v for k, v in (relevant["geometry"] or {}).items()
                                    if k not in {"truss_depth_mm", "max_truss_depth_mm"}}
            key = (mode, json.dumps(relevant, sort_keys=True, default=str))
        else:
            key = (mode, json.dumps(model.to_dict(), sort_keys=True, default=str))
        cached = getattr(self, "_building_loads_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        if model is None:
            model = building_model_from_preset(preset)
        frames = building_loads(model, basis=mode)
        result = {"mode": mode, "model": model, "frames": frames, "distinct_frames": distinct_frames(frames)}
        self._building_loads_cache = (key, result)
        return result

    def generate_and_solve(self):
        if getattr(self, "_defer_generate_and_solve", False):
            self._deferred_generate_and_solve = True
            return
        # Ordinary Generate + Solve deliberately analyses one topology only.
        # A manually adopted panel count is reused while the visible model
        # inputs are unchanged, but adjacent layouts are only explored by the
        # dedicated button or once at the end of the optimiser.
        allow_topology_search = self.panel_topology_search_allowed()
        signature = self.panel_topology_input_signature() if allow_topology_search else None
        cache_matches = bool(
            allow_topology_search
            and signature == getattr(self, "_panel_topology_cache_signature", None)
            and getattr(self, "_panel_topology_cache_count", None) is not None
        )
        if cache_matches:
            self._panel_count_override = int(self._panel_topology_cache_count)
        elif allow_topology_search:
            # Inputs changed: a chosen panel count is kept (hold_main_panel_count);
            # otherwise the geometry's default count is used.
            self._panel_count_override = self.held_main_panel_count()
            self.panel_topology_search_report = ""

        self._generate_and_solve_once()
        if self.structure is None or not allow_topology_search:
            return
        self._panel_topology_cache_signature = signature
        try:
            self._panel_topology_cache_count = int(self.panel_count_label.text())
        except (TypeError, ValueError):
            self._panel_topology_cache_count = None

    def check_main_web_panel_layout(self):
        """Run the bounded panel comparison only when explicitly requested."""
        if self.frame_system_combo.currentText() != "Truss":
            self.notify("information", "Web panel check", "Web panel checking is only available for trusses.")
            return
        button = getattr(self, "web_panel_check_button", None)
        if button is not None:
            button.setEnabled(False)
            button.setText("Checking...")
        self.process_ui_events()
        try:
            self.generate_and_solve()
            if self.structure is None:
                self.notify("critical", "Web panel check", "The model could not be generated.")
                return
            report, _ = self.refine_main_truss_panel_topology(
                force=True,
                signature=self.panel_topology_input_signature(),
            )
            self.notify("information", "Web panel check",
                report or "No adjacent valid panel layouts were available to compare.",
            )
        finally:
            if button is not None:
                button.setText("Check panels")
                button.setEnabled(True)

    def _generate_and_solve_once(self):
        if getattr(self, "_defer_generate_and_solve", False):
            self._deferred_generate_and_solve = True
            return
        from portalcalc.frame.future_canopy_design import geometry_basis
        future_design = getattr(self, "future_canopy_design", None)
        if future_design and future_design.get("geometry_basis") != geometry_basis(self):
            self.future_canopy_design = None
            self._preserve_web_assignments_on_solve = False
            self._suppress_panel_topology_search = False
            self._panel_count_override = None
        if not getattr(self, "_preserve_web_assignments_on_solve", False):
            self.auto_web_profile_assignments = {}
            self.auto_web_assignment_basis = {}
            self.auto_web_joint_doubler_sides = {}
            self.web_split_failures = {}
        else:
            self.clear_stale_web_assignments()
        self.clear_analysis_state()
        try:
            self.sync_crane_beam_span_to_bay_size()
            if self.crane_rating_active_from_ui():
                crane_signature = self.crane_auto_select_signature()
                if crane_signature != getattr(self, "_last_crane_auto_select_signature", None):
                    self.auto_select_crane_beam()
                    self._last_crane_auto_select_signature = self.crane_auto_select_signature()
            outside_span = mm_from_metres_text(self.span_input.text())
            pitch = float(self.pitch_input.text())
            eave = mm_from_metres_text(self.eave_input.text())
            depth = float(self.depth_input.text())
            roof_shape = self.current_roof_shape()
            frame_system = self.frame_system_combo.currentText()
            is_rafter = frame_system == "Rafter"
            rafter_haunch_length = max(mm_from_metres_text(self.rafter_haunch_length_input.text()), 0.0)
            internal_column_count = self.internal_column_count_combo.currentText()
            internal_column_offset = (
                mm_from_metres_text(self.internal_column_offset_input.text())
                if internal_column_count in {"1 column", "1 central", "2 columns"} else 0.0
            )
            internal_column_connection = self.internal_column_connection_combo.currentText()
            left_canopy_length = max(mm_from_metres_text(self.left_canopy_length_input.text()), 0.0)
            right_canopy_length = max(mm_from_metres_text(self.right_canopy_length_input.text()), 0.0)
            left_canopy_type = self.left_canopy_type_combo.currentText()
            right_canopy_type = self.right_canopy_type_combo.currentText()
            if not self.side_addition_selected(self.left_canopy_type_combo):
                left_canopy_length = 0.0
            if not self.side_addition_selected(self.right_canopy_type_combo):
                right_canopy_length = 0.0
            left_canopy_wind_model = self.left_canopy_wind_model_combo.currentText()
            right_canopy_wind_model = self.right_canopy_wind_model_combo.currentText()
            left_canopy_underside = self.left_canopy_underside_combo.currentText()
            right_canopy_underside = self.right_canopy_underside_combo.currentText()
            left_canopy_cpn_uplift = float(self.left_canopy_cpn_uplift_input.text())
            left_canopy_cpn_downward = float(self.left_canopy_cpn_downward_input.text())
            right_canopy_cpn_uplift = float(self.right_canopy_cpn_uplift_input.text())
            right_canopy_cpn_downward = float(self.right_canopy_cpn_downward_input.text())
            left_lean_wall_states = self.left_lean_enclosure_selector.wall_states()
            right_lean_wall_states = self.right_lean_enclosure_selector.wall_states()
            left_lean_outer_wall_clad = left_lean_wall_states.get("left", False)
            right_lean_outer_wall_clad = right_lean_wall_states.get("right", False)
            left_canopy_eave_height = self.optional_height_from_input(self.left_canopy_eave_height_input)
            right_canopy_eave_height = self.optional_height_from_input(self.right_canopy_eave_height_input)
            left_canopy_pitch = float(self.left_canopy_pitch_input.text())
            right_canopy_pitch = float(self.right_canopy_pitch_input.text())
            left_canopy_shallow_depth = max(float(self.left_canopy_shallow_depth_input.text()), 0.0)
            right_canopy_shallow_depth = max(float(self.right_canopy_shallow_depth_input.text()), 0.0)
            left_support = self.left_support_combo.currentText()
            right_support = self.right_support_combo.currentText()
            base_moment_spring_knm_rad = max(float(self.base_x_spring_input.text()), 0.0)
            if "Spring" in {left_support, right_support} and base_moment_spring_knm_rad <= 0.0:
                raise ValueError("base moment spring kR must be greater than zero when spring support is selected.")
            eave_x_spring_kn_mm = 0.0
            end_wall_brace_size = self.selected_end_wall_brace_size()
            left_column_length_adjust_mm = mm_from_metres_text(self.left_column_length_adjust_input.text() or 0.0)
            right_column_length_adjust_mm = mm_from_metres_text(self.right_column_length_adjust_input.text() or 0.0)
            bay_size_m = self.bay_size_m_from_ui()
            building_length_m = float(self.building_length_input.text())
            entered_q_kpa = abs(float(self.q_load_input.text()))
            snow_loads = self.snow_loads_from_ui()
            effective_q_kpa = max(entered_q_kpa, snow_loads["service_kpa"])
            roof_loads = BasicRoofLoads(
                bay_size_m=bay_size_m,
                g=self.downward_roof_load_from_input(self.g_load_input),
                q=-effective_q_kpa,
                solar=self.downward_roof_load_from_input(self.solar_load_input),
                fire_service=self.downward_roof_load_from_input(self.fire_service_load_input),
                hvac=self.downward_roof_load_from_input(self.hvac_load_input),
                other=self.downward_roof_load_from_input(self.other_load_input),
                placements={
                    "G": self.roof_load_placement_from_ui("g"),
                    "Q": self.roof_load_placement_from_ui("q"),
                    "Solar": self.roof_load_placement_from_ui("solar"),
                    "Other": self.roof_load_placement_from_ui("other"),
                },
            )
            crane_rating_t = max(float(self.crane_rating_input.text()), 0.0)
            left_canopy_loads = BasicRoofLoads(
                bay_size_m=bay_size_m,
                g=self.downward_roof_load_from_input(self.left_canopy_g_load_input),
                q=self.downward_roof_load_from_input(self.left_canopy_q_load_input),
                solar=self.downward_roof_load_from_input(self.left_canopy_solar_load_input),
            )
            right_canopy_loads = BasicRoofLoads(
                bay_size_m=bay_size_m,
                g=self.downward_roof_load_from_input(self.right_canopy_g_load_input),
                q=self.downward_roof_load_from_input(self.right_canopy_q_load_input),
                solar=self.downward_roof_load_from_input(self.right_canopy_solar_load_input),
            )
            wall_loads = WallLoads(
                left_plus_x=0.0,
                right_minus_x=0.0,
            )
            storage_inputs = self.storage_load_inputs_from_ui()
            storage_results = calculate_storage_loads(storage_inputs)
            storage_rail_design, storage_panel_design = self.apply_auto_storage_design(storage_results)
        except ValueError as error:
            detail = str(error).strip() or "please enter valid numbers."
            self.results_box.setText(f"Input error: {detail}")
            return

        if outside_span <= 0 or eave <= 0 or (not is_rafter and depth <= 0):
            self.results_box.setText("Input error: span, eave height and truss depth must be positive.")
            return

        for side_label, addition_type, length_mm, pitch_deg, deep_depth_mm in [
            ("Left", left_canopy_type, left_canopy_length, left_canopy_pitch, left_canopy_shallow_depth),
            ("Right", right_canopy_type, right_canopy_length, right_canopy_pitch, right_canopy_shallow_depth),
        ]:
            if addition_type != "Canopy" or length_mm <= 0.0:
                continue
            roof_level_difference = abs(math.tan(math.radians(pitch_deg)) * length_mm)
            if deep_depth_mm <= roof_level_difference:
                self.results_box.setText(
                    f"Input error: {side_label.lower()} canopy deeper-end depth must exceed "
                    f"the {roof_level_difference:.0f} mm roof level difference."
                )
                return

        left_column_profile = self.selected_profile(self.left_column_profile_combo)
        right_column_profile = self.selected_profile(self.right_column_profile_combo)
        internal_column_profile = self.selected_profile(self.internal_column_profile_combo)
        top_profile = self.selected_profile(self.top_profile_combo)
        bottom_profile = self.selected_profile(self.bottom_profile_combo)
        web_profile = self.selected_profile(self.web_profile_combo)
        left_canopy_top_profile = self.selected_profile(self.left_canopy_top_profile_combo)
        left_canopy_bottom_profile = self.selected_profile(self.left_canopy_bottom_profile_combo)
        left_canopy_web_profile = self.selected_profile(self.left_canopy_web_profile_combo)
        left_lean_column_profile = self.selected_profile(self.left_lean_column_profile_combo)
        right_canopy_top_profile = self.selected_profile(self.right_canopy_top_profile_combo)
        right_canopy_bottom_profile = self.selected_profile(self.right_canopy_bottom_profile_combo)
        right_canopy_web_profile = self.selected_profile(self.right_canopy_web_profile_combo)
        right_lean_column_profile = self.selected_profile(self.right_lean_column_profile_combo)
        top_chord_depth = self.profile_depth_mm(top_profile)
        bottom_chord_depth = self.profile_depth_mm(bottom_profile)

        def canopy_centreline_vertical_depth(outside_depth, side_pitch, is_lean_to, side_top, side_bottom):
            return truss_centreline_vertical_depth(
                outside_depth, side_pitch, self.profile_depth_mm(side_top), self.profile_depth_mm(side_bottom),
                parallel=is_lean_to)

        truss_centreline_depth = depth if is_rafter else depth - 0.5 * top_chord_depth - 0.5 * bottom_chord_depth
        if not is_rafter and truss_centreline_depth <= 0.0:
            self.results_box.setText(
                "Input error: truss outside depth must exceed half the selected top and bottom chord depths."
            )
            return
        left_has_canopy = left_canopy_length > 0.0
        right_has_canopy = right_canopy_length > 0.0
        left_has_lean_to = left_has_canopy and left_canopy_type == "Lean-to"
        right_has_lean_to = right_has_canopy and right_canopy_type == "Lean-to"
        left_canopy_is_rafter = left_has_canopy and self.canopy_side_is_rafter("left")
        right_canopy_is_rafter = right_has_canopy and self.canopy_side_is_rafter("right")
        # Canopy truss depths are outside-to-outside: the chord centrelines must
        # still clear the roof fall (canopy) or each other (lean-to).
        for side_label, addition_type, length_mm, pitch_deg, deep_depth_mm, side_is_rafter, side_top, side_bottom in [
            ("Left", left_canopy_type, left_canopy_length, left_canopy_pitch, left_canopy_shallow_depth,
             left_canopy_is_rafter, left_canopy_top_profile, left_canopy_bottom_profile),
            ("Right", right_canopy_type, right_canopy_length, right_canopy_pitch, right_canopy_shallow_depth,
             right_canopy_is_rafter, right_canopy_top_profile, right_canopy_bottom_profile),
        ]:
            if length_mm <= 0.0 or side_is_rafter:
                continue
            is_lean = str(addition_type).lower().startswith("lean")
            centreline = canopy_centreline_vertical_depth(deep_depth_mm, pitch_deg, is_lean, side_top, side_bottom)
            fall = 0.0 if is_lean else abs(math.tan(math.radians(pitch_deg)) * length_mm)
            if centreline <= fall:
                self.results_box.setText(
                    f"Input error: {side_label.lower()} {'lean-to' if is_lean else 'canopy'} truss depth "
                    f"({deep_depth_mm:.0f} mm outside-to-outside) leaves no depth between the chord centrelines"
                    + (f" over the {fall:.0f} mm roof level difference." if fall > 0.0 else ".")
                )
                return
        left_lean_outer_wall_clad = left_lean_outer_wall_clad and left_has_lean_to
        right_lean_outer_wall_clad = right_lean_outer_wall_clad and right_has_lean_to
        left_lean_wall_states = {key: bool(value) and left_has_lean_to for key, value in left_lean_wall_states.items()}
        right_lean_wall_states = {key: bool(value) and right_has_lean_to for key, value in right_lean_wall_states.items()}
        main_structure_open = not any(self.envelope_plan_selector.wall_states().values())
        whole_structure_open = main_structure_open and not any(left_lean_wall_states.values()) and not any(right_lean_wall_states.values())
        main_roof_is_free_roof = whole_structure_open
        left_canopy_is_free_roof = left_has_canopy and whole_structure_open
        right_canopy_is_free_roof = right_has_canopy and whole_structure_open
        self.update_internal_column_visibility()
        self.update_canopy_section_visibility(left_has_canopy, right_has_canopy, left_has_lean_to, right_has_lean_to)
        if hasattr(self.envelope_plan_selector, "set_lean_to_visibility"):
            self.envelope_plan_selector.set_lean_to_visibility(left_has_lean_to, right_has_lean_to)
        self.update_frame_system_visibility()

        left_column_offset = self.column_half_width_for_span_offset(left_column_profile)
        right_column_offset = self.column_half_width_for_span_offset(right_column_profile)
        span = outside_span - left_column_offset - right_column_offset
        if span <= 0:
            self.results_box.setText("Input error: outside span must be greater than the selected column widths.")
            return
        custom_internal_positions = []
        if internal_column_count == "Custom positions":
            try:
                custom_internal_positions = internal_column_positions_mm(self.internal_column_positions_input.text(), span)
            except ValueError as error:
                self.results_box.setText(f"Input error: {error}")
                return
        wind_inputs = self.wind_inputs_from_ui(outside_span, pitch, eave)

        if is_rafter:
            num_panels = max(4, int(math.ceil(span / 2500.0)))
            if roof_shape == "Gable" and num_panels % 2 != 0:
                num_panels += 1
        else:
            target_web_angle_deg = 50.0
            target_panel = truss_centreline_depth / math.tan(math.radians(target_web_angle_deg))
            ideal_panels = span / max(target_panel, 1.0)
            if roof_shape == "Skillion":
                num_panels = max(4, int(round(ideal_panels)))
            else:
                panels_per_side = round(ideal_panels / 2)
                if panels_per_side % 2 != 0:
                    panels_per_side += 1
                panels_per_side = max(2, panels_per_side)
                num_panels = panels_per_side * 2
            # The geometry's own count: a preset saves a held count only when it differs.
            self._default_main_panel_count = num_panels
            panel_override = getattr(self, "_panel_count_override", None)
            if panel_override is not None:
                try:
                    panel_override = int(panel_override)
                except (TypeError, ValueError):
                    panel_override = None
                valid_override = panel_override is not None and panel_override >= 4
                if roof_shape == "Gable":
                    valid_override = valid_override and panel_override % 4 == 0
                if valid_override:
                    num_panels = panel_override
        panel = span / num_panels
        panel_widths = [panel] * num_panels
        top_panel_stations = [panel * index for index in range(num_panels + 1)]
        # Truss depth is measured square to the (parallel) chords. The bottom
        # chord nodes sit this far below the top chord nodes, vertically.
        truss_vertical_depth = 0.0 if is_rafter else truss_centreline_depth / math.cos(math.radians(pitch))
        web_angle = 0.0 if is_rafter else math.degrees(math.atan(truss_vertical_depth / panel))
        if not is_rafter and roof_shape == "Gable":
            top_panel_stations = pitch_compensated_gable_top_stations(span, truss_vertical_depth, pitch, num_panels)
            roof_slope_tan = math.tan(math.radians(pitch))
            web_angle = math.degrees(math.atan(truss_vertical_depth / (panel * (1.0 + roof_slope_tan ** 2))))

        self.panel_label.setText(f"{panel:.0f} mm")
        self.web_angle_label.setText(f"{web_angle:.1f}°")
        self.panel_count_label.setText(str(num_panels))

        half_span = span / 2
        pitch_tan = math.tan(math.radians(pitch))
        rise = pitch_tan * (span if roof_shape == "Skillion" else half_span)

        def roof_line_y(local_x, eave_y):
            if roof_shape == "Skillion" or local_x <= half_span:
                return eave_y + local_x * pitch_tan
            return eave_y + rise - (local_x - half_span) * pitch_tan

        # Secondary members first: they depend only on the building envelope
        # (and the chord's slope length, which a vertical shift doesn't change),
        # then the frame node height from them. No circular dependence.
        from types import SimpleNamespace
        provisional_top = [SimpleNamespace(x=left_column_offset + local_x, y=roof_line_y(local_x, eave))
                           for local_x in top_panel_stations]
        early_purlin_check = self.purlin_check_from_inputs(
            self.build_purlin_layout(provisional_top, wind_inputs.wind_region, wind_inputs.importance_level),
            bay_size_m, roof_loads, wind_inputs, self.purlin_span_type_combo.currentText())
        early_girt_check = self.wall_girt_check_from_inputs(
            wind_inputs, bay_size_m, eave, self.wall_girt_span_type_combo.currentText())
        node_basis = self.eave_node_basis(eave, pitch, roof_shape, left_column_profile, right_column_profile,
                                          top_chord_depth, early_purlin_check, early_girt_check)
        frame_eave = node_basis["node_mm"]
        self.eave_node_basis_record = node_basis
        if frame_eave <= 0.0 or (not is_rafter and frame_eave - truss_vertical_depth <= 0.0):
            self.results_box.setText(
                f"Input error: the top chord node ({frame_eave:.0f} mm, from the {eave:.0f} mm eave pitching point) "
                "leaves no column below the truss.")
            return

        structure = Structure2D()
        E = 200000  # MPa = N/mm²

        ridge_y = frame_eave + rise

        top_nodes = []
        bottom_nodes = []
        node_id = 1

        panel_stations = [panel * index for index in range(num_panels + 1)]
        panel_stations[-1] = span
        for i, local_x in enumerate(top_panel_stations):
            x = left_column_offset + local_x
            node = Node(node_id, x, roof_line_y(local_x, frame_eave))
            structure.add_node(node)
            top_nodes.append(node)
            node_id += 1

        if is_rafter:
            bottom_nodes = top_nodes
            left_column_bottom_chord_node = top_nodes[0]
            right_column_bottom_chord_node = top_nodes[-1]
        else:
            for index, top_node in enumerate(top_nodes):
                local_x = panel_stations[index]
                x = left_column_offset + local_x
                roof_y = roof_line_y(local_x, frame_eave)
                node = Node(node_id, x, roof_y - truss_vertical_depth)
                structure.add_node(node)
                bottom_nodes.append(node)
                node_id += 1

            left_column_bottom_chord_node = bottom_nodes[0]
            right_column_bottom_chord_node = bottom_nodes[-1]

        # Envelope outside the frame lines, carried to the eave nodes
        # (apply_eave_strip_loads): the wall from the node up to the pitching
        # point, and the roof from the column centreline out to the cladding.
        cos_pitch = math.cos(math.radians(pitch))
        girt_mm = node_basis["girt_mm"]
        right_cladding_y = eave if roof_shape != "Skillion" else eave + (outside_span + 2.0 * girt_mm) * pitch_tan
        eave_strips = [
            {"side": side, "x": node.x, "y": node.y, "wall_mm": max(cladding_y - node.y, 0.0),
             "roof_mm": node_basis["column_offsets_mm"][side.lower()] / cos_pitch}
            for side, node, cladding_y in (("LEFT", top_nodes[0], eave), ("RIGHT", top_nodes[-1], right_cladding_y))
        ]
        node_basis["strips"] = eave_strips

        main_truss_panel_top_nodes = list(top_nodes)
        main_truss_panel_bottom_nodes = list(bottom_nodes)

        if not is_rafter:
            # The bottom chord seats on the column but should not impose full
            # rotational continuity into the through-column. Coincident nodes share
            # translations for force transfer while keeping separate rotations.
            left_truss_bottom_node = Node(
                node_id,
                left_column_bottom_chord_node.x,
                left_column_bottom_chord_node.y,
                ux=left_column_bottom_chord_node.ux,
                uy=left_column_bottom_chord_node.uy,
            )
            structure.add_node(left_truss_bottom_node)
            node_id += 1

            right_truss_bottom_node = Node(
                node_id,
                right_column_bottom_chord_node.x,
                right_column_bottom_chord_node.y,
                ux=right_column_bottom_chord_node.ux,
                uy=right_column_bottom_chord_node.uy,
            )
            structure.add_node(right_truss_bottom_node)
            node_id += 1

            bottom_nodes[0] = left_truss_bottom_node
            bottom_nodes[-1] = right_truss_bottom_node

        def bottom_y_at_x(x):
            return _y_at_x(bottom_nodes, x)

        def top_y_at_x(x):
            return _y_at_x(top_nodes, x)

        internal_column_xs = []
        centre_x = left_column_offset + span / 2.0
        min_internal_x = left_column_offset + 500.0
        max_internal_x = outside_span - right_column_offset - 500.0
        if min_internal_x > max_internal_x:
            min_internal_x = max_internal_x = centre_x

        def clamp_internal_x(x):
            return max(min_internal_x, min(x, max_internal_x))

        if internal_column_count == "Custom positions":
            internal_column_xs = [left_column_offset + x for x in custom_internal_positions]
        elif internal_column_count in {"1 column", "1 central"}:
            internal_column_xs = [clamp_internal_x(centre_x + internal_column_offset)]
        elif internal_column_count == "2 columns":
            paired_offset = abs(internal_column_offset)
            internal_column_xs = [
                clamp_internal_x(centre_x - paired_offset),
                clamp_internal_x(centre_x + paired_offset),
            ]
            internal_column_xs = sorted(set(round(x, 6) for x in internal_column_xs))

        internal_column_tops = []
        internal_column_roof_tops = []
        for x in internal_column_xs:
            existing = next((node for node in bottom_nodes if abs(node.x - x) < 1e-6), None)
            if existing is not None:
                top_node = existing
            else:
                top_node = Node(node_id, x, bottom_y_at_x(x))
                structure.add_node(top_node)
                node_id += 1
                if is_rafter:
                    top_nodes.append(top_node)
            internal_column_tops.append(top_node)
            roof_top_node = top_node
            if not is_rafter and internal_column_connection == "Moment":
                existing_roof = next((node for node in top_nodes if abs(node.x - x) < 1e-6), None)
                if existing_roof is not None:
                    roof_top_node = existing_roof
                else:
                    roof_top_node = Node(node_id, x, top_y_at_x(x))
                    structure.add_node(roof_top_node)
                    node_id += 1
                    top_nodes.append(roof_top_node)
            internal_column_roof_tops.append(roof_top_node)

        if is_rafter or internal_column_connection == "Moment":
            top_nodes.sort(key=lambda node: node.x)
            if is_rafter:
                bottom_nodes = top_nodes
        bottom_chord_nodes = sorted(bottom_nodes + [node for node in internal_column_tops if node not in bottom_nodes], key=lambda node: node.x)

        left_base = Node(node_id, left_column_offset, -left_column_length_adjust_mm)
        structure.add_node(left_base)
        node_id += 1

        right_base = Node(node_id, outside_span - right_column_offset, -right_column_length_adjust_mm)
        structure.add_node(right_base)
        node_id += 1

        internal_column_bases = []
        for top_node in internal_column_tops:
            base = Node(node_id, top_node.x, 0)
            structure.add_node(base)
            node_id += 1
            internal_column_bases.append(base)

        # Crane: actions from the runway beam calculation (crane_beam.portal_crane_reactions).
        # Each column's node is at the mid height of its crane stub: the top of
        # the runway beam is CRANE_RAIL_CLEARANCE_MM below the bottom chord node,
        # the stub sits under the beam and is the column section (depth D).
        left_crane_node = None
        right_crane_node = None
        crane_portal = None
        self.crane_portal_actions = None
        if crane_rating_t > 0.0:
            from portalcalc.crane_beam import (CRANE_BEAM_FACE_OFFSET_MM, CRANE_RAIL_CLEARANCE_MM,
                                    portal_crane_reactions)
            crane_portal = portal_crane_reactions(self.crane_beam_inputs_from_ui())
            crane_nodes = {}
            for side, chord_node, profile in (("left", left_column_bottom_chord_node, left_column_profile),
                                              ("right", right_column_bottom_chord_node, right_column_profile)):
                column_depth = 2.0 * self.column_half_width_for_span_offset(profile)
                drop = CRANE_RAIL_CLEARANCE_MM + crane_portal["runway_depth_mm"] + column_depth / 2.0
                crane_nodes[side] = (chord_node.x, chord_node.y - drop, column_depth, drop)
            crane_portal["nodes"] = {side: {"y_mm": y, "column_depth_mm": d, "below_chord_mm": drop,
                                            "eccentricity_mm": d / 2.0 + CRANE_BEAM_FACE_OFFSET_MM}
                                     for side, (_, y, d, drop) in crane_nodes.items()}
            # Runway beam mid depth: the side-wall bracing tier level (two X's with
            # the runway beam between them) and where the buffer force enters.
            crane_portal["runway_mid_height_mm"] = sum(
                chord.y - CRANE_RAIL_CLEARANCE_MM - crane_portal["runway_depth_mm"] / 2.0
                for chord in (left_column_bottom_chord_node, right_column_bottom_chord_node)) / 2.0
            crane_portal["runway_section"] = self.crane_beam_profile_combo.currentText()
            if crane_portal.get("runway_area_mm2", 0.0) <= 0.0:
                runway_profile = self.catalog.get(self.crane_beam_profile_combo.currentText())
                crane_portal["runway_area_mm2"] = float(getattr(runway_profile, "A", 0.0) or 0.0)
            # Crane span = rail centres (column centrelines less the runway offsets).
            crane_portal["crane_span_mm"] = (right_column_bottom_chord_node.x - left_column_bottom_chord_node.x
                                             - sum(n["eccentricity_mm"] for n in crane_portal["nodes"].values()))
            from portalcalc.crane_beam import buffer_force_rule_kn
            entered = self.crane_beam_buffer_input.text().strip()
            try:
                buffer_kn = float(entered) if entered else None
            except ValueError:
                buffer_kn = None
            crane_portal["buffer_source"] = "entered" if buffer_kn is not None else "rule"
            crane_portal["buffer_kn"] = (buffer_kn if buffer_kn is not None
                                         else buffer_force_rule_kn(crane_rating_t, crane_portal["crane_span_mm"]))
            if min(y for _, y, _, _ in crane_nodes.values()) > 1e-6:
                left_crane_node = Node(node_id, crane_nodes["left"][0], crane_nodes["left"][1])
                structure.add_node(left_crane_node)
                node_id += 1
                right_crane_node = Node(node_id, crane_nodes["right"][0], crane_nodes["right"][1])
                structure.add_node(right_crane_node)
                node_id += 1
                # Node ids let the 3D bracing model find the crane nodes in its portal copy.
                for side, node in (("left", left_crane_node), ("right", right_crane_node)):
                    crane_portal["nodes"][side].update(node_id=node.id, x_mm=node.x)
            self.crane_portal_actions = crane_portal

        left_storage_nodes = []
        right_storage_nodes = []
        storage_rail_deflection_nodes = []
        if storage_inputs.enabled and storage_results.rail_loads:
            left_column_top_y = top_nodes[0].y
            right_column_top_y = top_nodes[-1].y
            for active, top_y, base_y in [
                (storage_inputs.left_wall, left_column_top_y, left_base.y),
                (storage_inputs.right_wall, right_column_top_y, right_base.y),
            ]:
                if active and max(storage_inputs.rail_height_mm, storage_inputs.stored_height_m * 1000.0) > top_y - base_y + 1e-6:
                    self.results_box.setText("Input error: storage rail and stored-material height must not exceed the supporting column height.")
                    return
            storage_levels_mm = {rail.level_mm for rail in storage_results.rail_loads}
            if is_centre_stub_storage_mode(storage_inputs):
                storage_levels_mm.add(storage_inputs.stored_height_m * 1000.0)
            for level_mm in sorted(storage_levels_mm):
                left_level_y = left_base.y + level_mm
                right_level_y = right_base.y + level_mm
                if storage_inputs.left_wall and min(left_base.y, left_column_top_y) + 1e-6 < left_level_y < max(left_base.y, left_column_top_y) - 1e-6:
                    node = Node(node_id, left_base.x, left_level_y)
                    structure.add_node(node)
                    node_id += 1
                    left_storage_nodes.append(node)
                    if any(abs(level_mm - rail.level_mm) < 1e-6 for rail in storage_results.rail_loads):
                        storage_rail_deflection_nodes.append((node, "LEFT_COLUMN", level_mm))
                if storage_inputs.right_wall and min(right_base.y, right_column_top_y) + 1e-6 < right_level_y < max(right_base.y, right_column_top_y) - 1e-6:
                    node = Node(node_id, right_base.x, right_level_y)
                    structure.add_node(node)
                    node_id += 1
                    right_storage_nodes.append(node)
                    if any(abs(level_mm - rail.level_mm) < 1e-6 for rail in storage_results.rail_loads):
                        storage_rail_deflection_nodes.append((node, "RIGHT_COLUMN", level_mm))

        element_id = 1

        def add_element(n1, n2, group, profile, analysis_i_factor=1.0):
            nonlocal element_id
            assignment_group = self.auto_web_assignment_group_for_element(group)
            analysis_profile = self.analysis_web_profile_for_group(assignment_group) or profile
            structure.add_element(FrameElement2D(element_id, n1, n2, E, analysis_profile, group, analysis_i_factor=analysis_i_factor))
            element_id += 1

        def pinned_connection_node(host_node):
            nonlocal node_id
            node = Node(node_id, host_node.x, host_node.y, ux=host_node.ux, uy=host_node.uy)
            structure.add_node(node)
            node_id += 1
            return node

        def existing_node_at(x, y, tolerance=1e-6):
            for node in structure.nodes:
                if abs(node.x - x) <= tolerance and abs(node.y - y) <= tolerance:
                    return node
            return None

        def node_at_or_new(x, y):
            nonlocal node_id
            existing = existing_node_at(x, y)
            if existing is not None:
                return existing
            node = Node(node_id, x, y)
            structure.add_node(node)
            node_id += 1
            return node

        def add_canopy(side, length_mm, canopy_type, side_frame_system, side_eave_height, side_pitch, side_depth, side_top_profile, side_bottom_profile, side_web_profile, side_lean_column_profile):
            nonlocal node_id
            if length_mm <= 0.0:
                return [], [], [], None
            outward = -1.0 if side == "left" else 1.0
            prefix = "LEFT" if side == "left" else "RIGHT"
            is_side_rafter = side_frame_system == "Rafter"
            default_anchor_top = top_nodes[0] if side == "left" else top_nodes[-1]
            if side_eave_height is None or abs(side_eave_height - default_anchor_top.y) <= 1e-6:
                anchor_top = default_anchor_top
            else:
                anchor_top = Node(node_id, default_anchor_top.x, side_eave_height)
                structure.add_node(anchor_top)
                node_id += 1
            pitch_drop = math.tan(math.radians(side_pitch))
            outer_top_y = anchor_top.y - pitch_drop * length_mm
            is_lean_to = str(canopy_type).lower().startswith("lean")
            side_outside_depth = side_depth
            side_depth = canopy_centreline_vertical_depth(
                side_depth, side_pitch, is_lean_to, side_top_profile, side_bottom_profile)
            if is_lean_to:
                bottom_y = anchor_top.y - side_depth
            else:
                bottom_y = max(anchor_top.y, outer_top_y) - side_depth
            target_canopy_web_angle_deg = 50.0
            target_canopy_panel = max(side_outside_depth, 50.0) / math.tan(math.radians(target_canopy_web_angle_deg))
            canopy_panels = max(4, round(length_mm / max(target_canopy_panel, 1.0)))
            if canopy_panels % 2 != 0:
                canopy_panels += 1
            panel_len = length_mm / canopy_panels

            canopy_top = [anchor_top]
            canopy_bottom = []
            if not is_side_rafter:
                anchor_bottom = node_at_or_new(anchor_top.x, bottom_y)
                canopy_bottom.append(anchor_bottom)
            for i in range(canopy_panels + 1):
                x = anchor_top.x + outward * i * panel_len
                if i > 0:
                    top_node = Node(node_id, x, anchor_top.y - pitch_drop * i * panel_len)
                    structure.add_node(top_node)
                    node_id += 1
                    canopy_top.append(top_node)
                    if not is_side_rafter:
                        if is_lean_to:
                            bottom_node_y = top_node.y - side_depth
                        else:
                            bottom_node_y = bottom_y
                        bottom_node = Node(node_id, x, bottom_node_y)
                        structure.add_node(bottom_node)
                        node_id += 1
                        canopy_bottom.append(bottom_node)

            if is_side_rafter:
                for n1, n2 in zip(canopy_top[:-1], canopy_top[1:]):
                    add_element(n1, n2, f"{prefix}_CANOPY_TOP", side_top_profile)
                outer_column_base = None
                if is_lean_to:
                    outer_column_base = Node(node_id, canopy_top[-1].x, 0)
                    structure.add_node(outer_column_base)
                    node_id += 1
                    add_element(outer_column_base, canopy_top[-1], f"{prefix}_LEAN_COLUMN", side_lean_column_profile)
                return canopy_top, canopy_top, [anchor_top], outer_column_base

            for n1, n2 in zip(canopy_top[:-1], canopy_top[1:]):
                add_element(n1, n2, f"{prefix}_CANOPY_TOP", side_top_profile)
            for n1, n2 in zip(canopy_bottom[:-1], canopy_bottom[1:]):
                add_element(n1, n2, f"{prefix}_CANOPY_BOTTOM", side_bottom_profile)
            for i in range(canopy_panels):
                if i % 2 == 0:
                    add_element(pinned_connection_node(canopy_bottom[i]), pinned_connection_node(canopy_top[i + 1]), f"{prefix}_CANOPY_WEB", side_web_profile)
                else:
                    add_element(pinned_connection_node(canopy_top[i]), pinned_connection_node(canopy_bottom[i + 1]), f"{prefix}_CANOPY_WEB", side_web_profile)
            add_element(pinned_connection_node(canopy_top[0]), pinned_connection_node(canopy_bottom[0]), f"{prefix}_CANOPY_POST", side_web_profile)
            outer_column_base = None
            if is_lean_to:
                add_element(canopy_top[-1], canopy_bottom[-1], f"{prefix}_LEAN_COLUMN", side_lean_column_profile)
                outer_column_base = Node(node_id, canopy_bottom[-1].x, 0)
                structure.add_node(outer_column_base)
                node_id += 1
                add_element(outer_column_base, canopy_bottom[-1], f"{prefix}_LEAN_COLUMN", side_lean_column_profile)
            else:
                add_element(pinned_connection_node(canopy_top[-1]), pinned_connection_node(canopy_bottom[-1]), f"{prefix}_CANOPY_POST", side_web_profile)
            column_nodes = [anchor_top, anchor_bottom]
            return canopy_top, canopy_bottom, column_nodes, outer_column_base

        top_profile_depth = self.profile_depth_mm(top_profile)

        def rafter_haunch_i_factor(n1, n2):
            if not is_rafter or rafter_haunch_length <= 0.0 or top_profile_depth <= 0.0:
                return 1.0
            mid_x = (n1.x + n2.x) / 2.0
            mid_y = (n1.y + n2.y) / 2.0
            left_distance = math.hypot(mid_x - top_nodes[0].x, mid_y - top_nodes[0].y)
            right_distance = math.hypot(mid_x - top_nodes[-1].x, mid_y - top_nodes[-1].y)
            distance_from_eave = min(left_distance, right_distance)
            if distance_from_eave >= rafter_haunch_length:
                return 1.0
            taper = 1.0 - distance_from_eave / rafter_haunch_length
            effective_depth = top_profile_depth * (1.0 + taper)
            return (effective_depth / top_profile_depth) ** 3

        for i in range(len(top_nodes) - 1):
            add_element(top_nodes[i], top_nodes[i + 1], "TOP", top_profile, analysis_i_factor=rafter_haunch_i_factor(top_nodes[i], top_nodes[i + 1]))

        if not is_rafter:
            for i in range(len(bottom_chord_nodes) - 1):
                add_element(bottom_chord_nodes[i], bottom_chord_nodes[i + 1], "BOTTOM", bottom_profile)

            # The chords land on the column flanges: model the half column depth
            # between the column centreline node and the flange as rigid
            # (as SpaceGass's rigid member offsets do). Horizontal arm = D/2.
            column_arms = {
                (round(node.x, 6), round(node.y, 6)): self.column_half_width_for_span_offset(profile)
                for node, profile in (
                    (top_nodes[0], left_column_profile),
                    (left_column_bottom_chord_node, left_column_profile),
                    (top_nodes[-1], right_column_profile),
                    (right_column_bottom_chord_node, right_column_profile),
                )
            }

            def column_arm(node):
                return column_arms.get((round(node.x, 6), round(node.y, 6)), 0.0)

            for element in structure.elements:
                if element.group not in ("TOP", "BOTTOM"):
                    continue
                along = element.length() / max(abs(element.end.x - element.start.x), 1e-9)
                limit = 0.45 * element.length()
                start_arm = min(column_arm(element.start) * along, limit)
                end_arm = min(column_arm(element.end) * along, limit)
                if start_arm or end_arm:
                    element.set_rigid_ends(start_arm, end_arm)

            def warren_web_top_node_for_bottom_x(x):
                for i, (b1, b2) in enumerate(zip(main_truss_panel_bottom_nodes[:-1], main_truss_panel_bottom_nodes[1:])):
                    if min(b1.x, b2.x) - 1e-6 <= x <= max(b1.x, b2.x) + 1e-6:
                        return main_truss_panel_top_nodes[i + 1] if i % 2 == 0 else main_truss_panel_top_nodes[i]
                return min(main_truss_panel_top_nodes, key=lambda node: abs(node.x - x))

            moment_internal_column_joints = []
            if internal_column_connection == "Moment":
                moment_internal_column_joints = [
                    (bottom_joint, top_joint)
                    for bottom_joint, top_joint in zip(internal_column_tops, internal_column_roof_tops)
                    if top_joint is not bottom_joint
                ]

            def moment_internal_joint_in_panel(panel_index):
                b1 = main_truss_panel_bottom_nodes[panel_index]
                b2 = main_truss_panel_bottom_nodes[panel_index + 1]
                panel_min_x = min(b1.x, b2.x)
                panel_max_x = max(b1.x, b2.x)
                for bottom_joint, top_joint in moment_internal_column_joints:
                    if panel_min_x + 1e-6 < bottom_joint.x < panel_max_x - 1e-6:
                        return bottom_joint, top_joint
                return None

            def web_crosses_moment_internal_column(n1, n2):
                for bottom_joint, top_joint in moment_internal_column_joints:
                    if not (min(n1.x, n2.x) + 1e-6 < bottom_joint.x < max(n1.x, n2.x) - 1e-6):
                        continue
                    if any(
                        abs(node.x - joint.x) <= 1e-6 and abs(node.y - joint.y) <= 1e-6
                        for node in (n1, n2)
                        for joint in (bottom_joint, top_joint)
                    ):
                        continue
                    return True
                return False

            def add_warren_web_if_clear(n1, n2):
                if web_crosses_moment_internal_column(n1, n2):
                    return
                add_element(pinned_connection_node(n1), pinned_connection_node(n2), "WEB", web_profile)

            for i in range(num_panels):
                moment_joint = moment_internal_joint_in_panel(i)
                if moment_joint is not None:
                    bottom_joint, top_joint = moment_joint
                    if i % 2 == 0:
                        add_element(pinned_connection_node(main_truss_panel_bottom_nodes[i]), pinned_connection_node(top_joint), "WEB", web_profile)
                        add_element(pinned_connection_node(bottom_joint), pinned_connection_node(main_truss_panel_top_nodes[i + 1]), "WEB", web_profile)
                    else:
                        add_element(pinned_connection_node(main_truss_panel_top_nodes[i]), pinned_connection_node(bottom_joint), "WEB", web_profile)
                        add_element(pinned_connection_node(top_joint), pinned_connection_node(main_truss_panel_bottom_nodes[i + 1]), "WEB", web_profile)
                elif i % 2 == 0:
                    add_warren_web_if_clear(main_truss_panel_bottom_nodes[i], main_truss_panel_top_nodes[i + 1])
                else:
                    add_warren_web_if_clear(main_truss_panel_top_nodes[i], main_truss_panel_bottom_nodes[i + 1])

            add_element(pinned_connection_node(main_truss_panel_top_nodes[0]), pinned_connection_node(main_truss_panel_bottom_nodes[0]), "POST", web_profile)
            add_element(pinned_connection_node(main_truss_panel_top_nodes[-1]), pinned_connection_node(main_truss_panel_bottom_nodes[-1]), "POST", web_profile)

            if roof_shape == "Gable":
                # Central apex vertical web/post. This keeps the ridge node tied directly
                # to the bottom chord and gives a more realistic Warren truss apex detail.
                apex_index = num_panels // 2
                add_element(pinned_connection_node(main_truss_panel_top_nodes[apex_index]), pinned_connection_node(main_truss_panel_bottom_nodes[apex_index]), "APEX", web_profile)

        for base_node, top_node, roof_top_node in zip(internal_column_bases, internal_column_tops, internal_column_roof_tops):
            if internal_column_connection == "Moment":
                add_element(base_node, top_node, "INTERNAL_COLUMN", internal_column_profile)
                if not is_rafter and roof_top_node is not top_node:
                    add_element(top_node, roof_top_node, "INTERNAL_COLUMN", internal_column_profile)
            else:
                add_element(base_node, pinned_connection_node(top_node), "INTERNAL_COLUMN", internal_column_profile)
            if not is_rafter and internal_column_connection != "Moment":
                web_target_node = warren_web_top_node_for_bottom_x(top_node.x)
                add_element(pinned_connection_node(top_node), pinned_connection_node(web_target_node), "WEB", web_profile)

        left_canopy_top_nodes, left_canopy_bottom_nodes, left_canopy_column_node, left_lean_base = add_canopy(
            "left", left_canopy_length, left_canopy_type, "Rafter" if left_canopy_is_rafter else "Truss", left_canopy_eave_height, left_canopy_pitch, left_canopy_shallow_depth,
            left_canopy_top_profile, left_canopy_bottom_profile, left_canopy_web_profile, left_lean_column_profile,
        )
        right_canopy_top_nodes, right_canopy_bottom_nodes, right_canopy_column_node, right_lean_base = add_canopy(
            "right", right_canopy_length, right_canopy_type, "Rafter" if right_canopy_is_rafter else "Truss", right_canopy_eave_height, right_canopy_pitch, right_canopy_shallow_depth,
            right_canopy_top_profile, right_canopy_bottom_profile, right_canopy_web_profile, right_lean_column_profile,
        )
        canopy_tip_checks = []
        side_midspan_checks = []
        if left_canopy_top_nodes and left_canopy_type == "Canopy":
            canopy_tip_checks.append({
                "key": "LEFT",
                "label": "Left canopy tip",
                "anchor": left_canopy_top_nodes[0],
                "tilt_reference": left_canopy_bottom_nodes[0] if left_canopy_bottom_nodes else None,
                "tip": left_canopy_top_nodes[-1],
                "length_mm": _slope_node_length(left_canopy_top_nodes),
            })
        if left_canopy_top_nodes and left_canopy_type == "Lean-to":
            side_midspan_checks.append(_slope_midspan_check("LEFT", "Left lean-to midspan", left_canopy_top_nodes))
        if right_canopy_top_nodes and right_canopy_type == "Canopy":
            canopy_tip_checks.append({
                "key": "RIGHT",
                "label": "Right canopy tip",
                "anchor": right_canopy_top_nodes[0],
                "tilt_reference": right_canopy_bottom_nodes[0] if right_canopy_bottom_nodes else None,
                "tip": right_canopy_top_nodes[-1],
                "length_mm": _slope_node_length(right_canopy_top_nodes),
            })
        if right_canopy_top_nodes and right_canopy_type == "Lean-to":
            side_midspan_checks.append(_slope_midspan_check("RIGHT", "Right lean-to midspan", right_canopy_top_nodes))
        side_midspan_checks = [check for check in side_midspan_checks if check is not None]
        roof_top_nodes = (
            list(reversed(left_canopy_top_nodes[1:]))
            + top_nodes
            + right_canopy_top_nodes[1:]
        )
        continuous_roof_top_nodes = (
            (list(reversed(left_canopy_top_nodes[1:])) if not left_canopy_is_free_roof else [])
            + top_nodes
            + (right_canopy_top_nodes[1:] if not right_canopy_is_free_roof else [])
        )

        def add_column_segments(nodes, group, profile):
            ordered = []
            for node in sorted([n for n in nodes if n is not None], key=lambda n: n.y):
                if ordered and abs(node.y - ordered[-1].y) < 1e-6:
                    continue
                ordered.append(node)
            for n1, n2 in zip(ordered[:-1], ordered[1:]):
                add_element(n1, n2, group, profile)

        add_column_segments(
            [left_base, left_crane_node, left_column_bottom_chord_node] + left_storage_nodes + left_canopy_column_node + [top_nodes[0]],
            "LEFT_COLUMN",
            left_column_profile,
        )
        add_column_segments(
            [right_base, right_crane_node, right_column_bottom_chord_node] + right_storage_nodes + right_canopy_column_node + [top_nodes[-1]],
            "RIGHT_COLUMN",
            right_column_profile,
        )

        left_support = self.left_support_combo.currentText()
        right_support = self.right_support_combo.currentText()
        eave_x_restraint = self.eave_x_restraint_combo.currentText()
        eave_bracing_info = None
        propped_end = self.propped_end_context()
        if propped_end is not None:
            # A propped end frame lies in the end wall: its own end-wall bracing
            # restrains its eaves (springs added below), not the L/2 path spring
            # an interior frame gets.
            eave_x_restraint = "None"
        active_eave_restraint = eave_x_restraint
        active_eave_x_spring_kn_mm = eave_x_spring_kn_mm
        if eave_x_restraint in {"One end", "Both ends"}:
            end_count = 2 if eave_x_restraint == "Both ends" else 1
            eave_bracing_info = self.end_wall_bracing_eave_spring(
                building_length_m,
                bay_size_m,
                end_wall_brace_size["area_mm2"],
                end_count=end_count,
                brace_label=end_wall_brace_size["label"],
            )
            active_eave_restraint = (
                "Both eaves"
                if self.end_wall_braced_sides_combo.currentText() == "Both sides"
                else "Left eave"
            )
            active_eave_x_spring_kn_mm = eave_bracing_info["effective_spring_kn_mm"]
        self.bracing_3d_restraint_status = None
        trial_restraint_kn_mm = getattr(self, "trial_sway_restraint_kn_mm", None)
        if trial_restraint_kn_mm is not None:
            # Headless optimisers check frame candidates against a trial
            # restraint; it is never saved and overrides any adopted record.
            if trial_restraint_kn_mm > 0.0:
                structure.add_sway_spring((top_nodes[0].ux, top_nodes[-1].ux), trial_restraint_kn_mm * 1000.0)
            active_eave_x_spring_kn_mm = trial_restraint_kn_mm
        elif eave_x_restraint == BRACING_3D_RESTRAINT_OPTION:
            # One spring on the mean eave movement: the 3D network restrains
            # frame sway, not eave spread. Stale records give no credit.
            state, restraint_kn_mm, message = bracing_restraint_status(
                self.bracing_3d_restraint, self.bracing_location_layout,
                self.bracing_3d_settings, self.collect_preset_data())
            self.bracing_3d_restraint_status = {"state": state, "stiffness_kn_mm": restraint_kn_mm, "message": message}
            if restraint_kn_mm > 0.0:
                structure.add_sway_spring((top_nodes[0].ux, top_nodes[-1].ux), restraint_kn_mm * 1000.0)
            active_eave_x_spring_kn_mm = restraint_kn_mm
        if self.bracing_3d_restraint is not None:
            self.update_bracing_layout_summary()
        eave_x_spring_n_mm = active_eave_x_spring_kn_mm * 1000.0
        base_moment_spring_nmm_rad = base_moment_spring_knm_rad * 1_000_000.0
        self.apply_base_support(structure, left_base, left_support, base_moment_spring_nmm_rad)
        self.apply_base_support(structure, right_base, right_support, base_moment_spring_nmm_rad)
        eave_restraint_nodes = []
        eave_restraint_types = []
        if active_eave_restraint in {"Left eave", "Both eaves"}:
            if eave_x_spring_n_mm > 0.0:
                structure.add_spring(top_nodes[0].ux, eave_x_spring_n_mm)
                eave_restraint_types.append("X spring")
            else:
                structure.add_support(top_nodes[0].ux)
                eave_restraint_types.append("X restraint")
            eave_restraint_nodes.append(top_nodes[0])
        if active_eave_restraint in {"Right eave", "Both eaves"}:
            if eave_x_spring_n_mm > 0.0:
                structure.add_spring(top_nodes[-1].ux, eave_x_spring_n_mm)
                eave_restraint_types.append("X spring")
            else:
                structure.add_support(top_nodes[-1].ux)
                eave_restraint_types.append("X restraint")
            eave_restraint_nodes.append(top_nodes[-1])
        if propped_end is not None:
            eave_bracing_info = self.propped_end_bracing_springs(propped_end, end_wall_brace_size)
            for side, spring in eave_bracing_info["springs"].items():
                node = top_nodes[0] if side == "left" else top_nodes[-1]
                structure.add_spring(node.ux, spring["spring_kn_mm"] * 1000.0)
                eave_restraint_nodes.append(node)
                eave_restraint_types.append("X spring")
            active_eave_restraint = eave_bracing_info["restraint"]
            active_eave_x_spring_kn_mm = max((s["spring_kn_mm"] for s in eave_bracing_info["springs"].values()), default=0.0)
        for internal_base in internal_column_bases:
            self.apply_base_support(structure, internal_base, "Pinned")
        for lean_base in [left_lean_base, right_lean_base]:
            if lean_base is not None:
                self.apply_base_support(structure, lean_base, "Pinned")
        self.purlin_layout = self.build_purlin_layout(
            top_nodes,
            wind_inputs.wind_region,
            wind_inputs.importance_level,
        )
        # The layout's spacing equals the provisional one (same chord, shifted
        # vertically), so the purlin check made before the frame stands.
        self.purlin_check = early_purlin_check
        # Open canopy edge purlins (the canopy-edge struts in the 3D bracing model).
        self.canopy_purlin_checks = {}
        for side_key, has_canopy, has_lean_to, side_loads in (
            ("left", left_has_canopy, left_has_lean_to, left_canopy_loads),
            ("right", right_has_canopy, right_has_lean_to, right_canopy_loads),
        ):
            if has_canopy and not has_lean_to:
                self.canopy_purlin_checks[side_key] = self.canopy_purlin_check(
                    side_key, wind_inputs, bay_size_m, side_loads, self.purlin_span_type_combo.currentText(),
                    (self.purlin_check or {}).get("spacing_mm"))
        self.update_canopy_purlin_labels()
        self.calculate_longitudinal_bracing_design(
            wind_inputs, outside_span, eave, pitch, roof_shape, bay_size_m
        )
        self.wall_girt_check = early_girt_check
        self.calculate_end_wall_design(wind_inputs, outside_span, eave, pitch, roof_shape)
        self.member_check_restraints = self.member_check_restraint_lengths(
            wind_inputs,
            left_base,
            right_base,
            bottom_nodes,
            self.purlin_layout,
            column_top_deduction_mm=(2.0 * top_profile_depth if is_rafter and rafter_haunch_length > 0.0 else 0.0),
            crane_minor_height_mm=(
                left_crane_node.y
                if crane_rating_t > 0.0 and left_crane_node is not None and right_crane_node is not None
                else None
            ),
            clad_column_heights_mm={
                "LEFT_COLUMN": top_nodes[0].y - left_base.y,
                "RIGHT_COLUMN": top_nodes[-1].y - right_base.y,
            },
            storage_restraint_levels_mm={
                "LEFT_COLUMN": [node.y for node in left_storage_nodes if any(abs(node.y - left_base.y - rail.level_mm) < 1e-6 for rail in storage_results.rail_loads)],
                "RIGHT_COLUMN": [node.y for node in right_storage_nodes if any(abs(node.y - right_base.y - rail.level_mm) < 1e-6 for rail in storage_results.rail_loads)],
            },
        )
        internal_check_tops = internal_column_roof_tops if internal_column_connection == "Moment" else internal_column_tops
        internal_lengths = [abs(top.y - base.y) for base, top in zip(internal_column_bases, internal_check_tops)]
        if internal_lengths:
            internal_length = max(internal_lengths)
            self.member_check_restraints["INTERNAL_COLUMN"] = {
                "major_mm": internal_length,
                "minor_mm": internal_length,
                "ke": 1.0,
                "am": 1.0,
                "kt": 1.0,
                "kl": 1.0,
                "kr": 1.0,
                "clad": False,
            }

        def set_lean_column_restraint(group, is_clad):
            if group not in self.member_check_restraints:
                return
            if group == "LEFT_LEAN_COLUMN" and left_lean_base is not None and left_canopy_top_nodes:
                base_y = left_lean_base.y
                top_y = max(node.y for node in left_canopy_top_nodes[-1:] + left_canopy_bottom_nodes[-1:])
            elif group == "RIGHT_LEAN_COLUMN" and right_lean_base is not None and right_canopy_top_nodes:
                base_y = right_lean_base.y
                top_y = max(node.y for node in right_canopy_top_nodes[-1:] + right_canopy_bottom_nodes[-1:])
            else:
                return
            column_height = max(abs(top_y - base_y), 0.0)
            if is_clad:
                clad_height_mm = column_height
                major_length = clad_height_mm / 2.0
                minor_length = clad_height_mm / 2.0
            else:
                major_length = column_height
                minor_length = column_height
            self.member_check_restraints[group].update({
                "major_mm": major_length,
                "minor_mm": minor_length,
                "ke": 1.0 if is_clad else 0.85,
                "am": 1.3 if is_clad else 1.75,
                "kt": 1.0,
                "kl": 1.0,
                "kr": 1.0,
                "clad": bool(is_clad),
                "crane_minor": False,
                "storage_restraint": False,
            })

        set_lean_column_restraint("LEFT_LEAN_COLUMN", left_lean_outer_wall_clad)
        set_lean_column_restraint("RIGHT_LEAN_COLUMN", right_lean_outer_wall_clad)

        cladding = self.column_cladding_restraints(wind_inputs)
        wall_cladding_segments = []
        if cladding.get("LEFT_COLUMN"):
            wall_cladding_segments.append({"x": top_nodes[0].x, "y1": left_base.y, "y2": top_nodes[0].y, "label": "Left wall"})
        if cladding.get("RIGHT_COLUMN"):
            wall_cladding_segments.append({"x": top_nodes[-1].x, "y1": right_base.y, "y2": top_nodes[-1].y, "label": "Right wall"})
        if left_lean_outer_wall_clad and left_lean_base is not None and left_canopy_top_nodes:
            wall_cladding_segments.append({"x": left_lean_base.x, "y1": left_lean_base.y, "y2": left_canopy_top_nodes[-1].y, "label": "Left lean-to wall"})
        if right_lean_outer_wall_clad and right_lean_base is not None and right_canopy_top_nodes:
            wall_cladding_segments.append({"x": right_lean_base.x, "y1": right_lean_base.y, "y2": right_canopy_top_nodes[-1].y, "label": "Right lean-to wall"})
        from portalcalc.frame.column_flybracing import girt_levels
        for segment in wall_cladding_segments:
            segment['girt_levels_mm'] = girt_levels(min(segment['y1'], segment['y2']),
                                                   max(segment['y1'], segment['y2']),
                                                   self.wall_girt_spacing_mm(wind_inputs.wind_region))

        # A non-free lean-to on a skillion is part of the continuous roof for
        # external wind.  Use the full plan width and the average height of the
        # assembled roof, rather than retaining the main shed's h and D.  This
        # matters particularly where a signed side pitch continues upward and
        # the attached roof is higher than the main frame.
        if (
            roof_shape == "Skillion"
            and len(continuous_roof_top_nodes) >= 2
            and len(continuous_roof_top_nodes) > len(top_nodes)
        ):
            roof_width_terms_mm = [outside_span]
            if left_canopy_top_nodes and not left_canopy_is_free_roof:
                roof_width_terms_mm.append(left_canopy_length)
            if right_canopy_top_nodes and not right_canopy_is_free_roof:
                roof_width_terms_mm.append(right_canopy_length)
            continuous_width_m = sum(roof_width_terms_mm) / 1000.0
            weighted_height_mm2 = 0.0
            sampled_width_mm = 0.0
            for n1, n2 in zip(continuous_roof_top_nodes[:-1], continuous_roof_top_nodes[1:]):
                segment_width_mm = abs(n2.x - n1.x)
                weighted_height_mm2 += segment_width_mm * (n1.y + n2.y) * 0.5
                sampled_width_mm += segment_width_mm
            average_roof_height_m = (
                weighted_height_mm2 / sampled_width_mm / 1000.0
                if sampled_width_mm > 1e-9 else wind_inputs.eave_height_m
            )
            end_dx = continuous_roof_top_nodes[-1].x - continuous_roof_top_nodes[0].x
            end_dy = continuous_roof_top_nodes[-1].y - continuous_roof_top_nodes[0].y
            effective_pitch_deg = abs(math.degrees(math.atan2(end_dy, end_dx))) if abs(end_dx) > 1e-9 else abs(pitch)
            equivalent_eave_height_m = average_roof_height_m - (
                0.5 * continuous_width_m * math.tan(math.radians(effective_pitch_deg))
            )
            wind_inputs = replace(
                wind_inputs,
                eave_height_m=max(equivalent_eave_height_m, 0.1),
                roof_pitch_deg=effective_pitch_deg,
                building_width_m=max(continuous_width_m, 0.1),
                left_canopy_length_m=0.0,
                right_canopy_length_m=0.0,
            )

        selected_combination = self.load_combinations[self.load_combination_combo.currentIndex()]
        wind_result_cache = {}

        def get_wind_result(cpe_case):
            source_direction = self.wind_source_direction_for_case(wind_inputs.orientation, cpe_case)
            if source_direction not in wind_result_cache:
                wind_result_cache[source_direction] = calculate_wind(
                    replace(wind_inputs, orientation=source_direction)
                )
            return wind_result_cache[source_direction]

        continuous_roof_wind_result_cache = {}

        def get_continuous_roof_wind_result(cpe_case):
            source_direction = self.wind_source_direction_for_case(wind_inputs.orientation, cpe_case)
            if source_direction not in continuous_roof_wind_result_cache:
                continuous_roof_wind_result_cache[source_direction] = calculate_wind(
                    replace(
                        wind_inputs,
                        orientation=source_direction,
                        left_canopy_length_m=(
                            0.0 if roof_shape == "Skillion" and len(continuous_roof_top_nodes) > len(top_nodes)
                            else _slope_path_length_m(left_canopy_top_nodes) if not left_canopy_is_free_roof else 0.0
                        ),
                        right_canopy_length_m=(
                            0.0 if roof_shape == "Skillion" and len(continuous_roof_top_nodes) > len(top_nodes)
                            else _slope_path_length_m(right_canopy_top_nodes) if not right_canopy_is_free_roof else 0.0
                        ),
                    )
                )
            return continuous_roof_wind_result_cache[source_direction]

        left_canopy_top_node_ids = {id(node) for node in left_canopy_top_nodes}
        right_canopy_top_node_ids = {id(node) for node in right_canopy_top_nodes}

        def canopy_top_group_for_segment(n1, n2):
            segment_node_ids = {id(n1), id(n2)}
            if segment_node_ids.issubset(left_canopy_top_node_ids):
                return "LEFT_CANOPY_TOP"
            if segment_node_ids.issubset(right_canopy_top_node_ids):
                return "RIGHT_CANOPY_TOP"
            return None

        def eave_roof_strip_has_canopy(strip):
            """A canopy/lean-to roof starting at the eave node carries that roof (as apply_eave_strip_loads)."""
            group = f"{strip['side']}_CANOPY_TOP"
            return any(element.group == group and any(abs(node.x - strip["x"]) < 1e-6 and abs(node.y - strip["y"]) < 1e-6
                                                       for node in (element.start, element.end))
                       for element in structure.elements)

        def apply_roof_eave_strip_wind(target_structure, nodes, start_kn_m, end_kn_m):
            """Roof wind on the eave strips: the roof from the column centreline out
            to the cladding line, which the eave strips already carry for roof
            gravity (apply_eave_strip_loads). Applied at the eave node, normal to
            the end roof panel, at that panel's wind line load (kN/m, positive
            outward). Only where the path ends at a main-roof eave node. Not drawn
            as load arrows (the single-load views show no eave strips)."""
            if len(nodes) < 2:
                return
            for strip in eave_strips:
                at_start = strip["side"] == "LEFT"
                end, inner = (nodes[0], nodes[1]) if at_start else (nodes[-1], nodes[-2])
                line_kn_m = start_kn_m if at_start else end_kn_m
                if (strip["roof_mm"] <= 1e-9 or not line_kn_m or abs(end.x - strip["x"]) > 1e-6
                        or abs(end.y - strip["y"]) > 1e-6 or eave_roof_strip_has_canopy(strip)):
                    continue
                total_n = line_kn_m * strip["roof_mm"]   # kN/m x mm = N
                nx, ny = roof_outward_normal(inner, end)
                if abs(nx) > 0.0:
                    target_structure.add_load(end.ux, total_n * nx)
                target_structure.add_load(end.uy, total_n * ny)

        def apply_roof_wind_strips_to_nodes(target_structure, nodes, strips, wind_result, wind_pressure):
            scale = wind_pressure_scale(wind_result, wind_pressure)
            distances = _cumulative_roof_distances(nodes)
            arrows = []
            for index, (n1, n2) in enumerate(zip(nodes[:-1], nodes[1:])):
                s1 = distances[index]
                s2 = distances[index + 1]
                panel_len = max(s2 - s1, 1e-9)
                total_kn = _wind_interval_load_kn(strips, s1, s2, scale)
                canopy_group = canopy_top_group_for_segment(n1, n2)
                if canopy_group is not None:
                    arrows.extend(
                        _add_normal_uniform_line_load_to_members(
                            target_structure,
                            [n1, n2],
                            total_kn / panel_len,
                            canopy_group,
                        )
                    )
                    continue
                total_n = total_kn * 1000.0
                # Pressure acts normal to the roof panel, including its
                # horizontal component on pitched roofs.
                nx, ny = add_roof_panel_pressure(target_structure, n1, n2, total_n)
                display_n_per_m = (total_kn / panel_len) * 1000.0
                arrows.append(((n1.x + n2.x) / 2.0, (n1.y + n2.y) / 2.0 + 800.0,
                               display_n_per_m * nx, display_n_per_m * ny))
            apply_roof_eave_strip_wind(target_structure, nodes,
                                       _wind_line_at_end(strips, distances[-1], True, scale),
                                       _wind_line_at_end(strips, distances[-1], False, scale))
            return arrows

        def canopy_wind_model(side_key):
            return "AS/NZS B.5 free roof" if canopy_uses_free_roof(side_key) else "Continuous roof Cpe + chamber Cpi"

        def canopy_uses_free_roof(side_key):
            return left_canopy_is_free_roof if side_key == "left" else right_canopy_is_free_roof

        def canopy_underside(side_key):
            label = left_canopy_underside if side_key == "left" else right_canopy_underside
            return "blocked" if str(label).startswith("Blocked") else "empty"

        def manual_canopy_cpn(side_key, cpe_case):
            use_uplift = str(cpe_case).strip().endswith("+")
            if side_key == "left":
                return left_canopy_cpn_uplift if use_uplift else -abs(left_canopy_cpn_downward)
            return right_canopy_cpn_uplift if use_uplift else -abs(right_canopy_cpn_downward)

        def canopy_free_roof_values(wind_result, side_key, cpe_case):
            use_uplift = str(cpe_case).strip().endswith("+")
            part = _canopy_free_roof_part(side_key, cpe_case)
            underside = canopy_underside(side_key)
            pitch_deg = left_canopy_pitch if side_key == "left" else right_canopy_pitch
            if str(cpe_case).strip().startswith("End"):
                cpn_as = transverse_free_roof_cpn(underside, use_uplift)
            else:
                cpn_as = free_roof_cpn(
                    pitch_deg,
                    wind_result.h_over_d,
                    part,
                    underside,
                    use_uplift,
                )
            return cpn_as, -cpn_as

        def main_roof_free_roof_part(segment_mid_x, cpe_case):
            case = str(cpe_case).strip()
            if case.startswith("End"):
                return "windward"
            apex_x = max(top_nodes, key=lambda node: node.y).x
            left_wind = case.startswith("Left")
            if left_wind:
                return "windward" if segment_mid_x <= apex_x else "leeward"
            return "leeward" if segment_mid_x <= apex_x else "windward"

        def main_roof_free_roof_values(wind_result, segment_mid_x, cpe_case):
            part = main_roof_free_roof_part(segment_mid_x, cpe_case)
            if part is None:
                return None
            use_uplift = str(cpe_case).strip().endswith("+")
            if str(cpe_case).strip().startswith("End"):
                cpn_as = transverse_free_roof_cpn("empty", use_uplift)
            else:
                cpn_as = free_roof_cpn(
                    wind_result.inputs.roof_pitch_deg,
                    wind_result.h_over_d,
                    part,
                    "empty",
                    use_uplift,
                )
            return cpn_as, -cpn_as

        def apply_main_roof_free_roof_cpn(target_structure, wind_result, cpe_case, wind_pressure):
            scale = wind_pressure_scale(wind_result, wind_pressure)
            arrows = []
            lines = []
            for n1, n2 in zip(top_nodes[:-1], top_nodes[1:]):
                mid_x = (n1.x + n2.x) / 2.0
                values = main_roof_free_roof_values(wind_result, mid_x, cpe_case)
                if values is None:
                    lines.append(0.0)
                    continue
                _, frame_cpn = values
                line_kn_m = frame_cpn * wind_result.wu_kn_m2 * scale * wind_result.inputs.bay_size_m
                lines.append(line_kn_m)
                arrows.extend(_add_normal_line_load_to_nodes(target_structure, [n1, n2], line_kn_m))
            if lines:
                apply_roof_eave_strip_wind(target_structure, top_nodes, lines[0], lines[-1])
            return arrows

        def apply_canopy_wind_cpe(target_structure, wind_result, cpe_case, wind_pressure):
            scale = wind_pressure_scale(wind_result, wind_pressure)
            strips = wind_result.case_roof_strips(cpe_case)
            roof_length_m = wind_result.roof_slope_length_m
            arrows = []
            for side_key, canopy_nodes, start_m in [
                ("left", left_canopy_top_nodes, 0.0),
                ("right", right_canopy_top_nodes, roof_length_m - _slope_path_length_m(right_canopy_top_nodes)),
            ]:
                side_slope_m = _slope_path_length_m(canopy_nodes)
                if side_slope_m <= 1e-9:
                    continue
                if canopy_uses_free_roof(side_key):
                    _, frame_cpn = canopy_free_roof_values(wind_result, side_key, cpe_case)
                    line_kn_m = frame_cpn * wind_result.wu_kn_m2 * scale * wind_result.inputs.bay_size_m
                    element_group = "LEFT_CANOPY_TOP" if side_key == "left" else "RIGHT_CANOPY_TOP"
                    arrows.extend(
                        _add_normal_uniform_line_load_to_members(
                            target_structure,
                            canopy_nodes,
                            line_kn_m,
                            element_group,
                        )
                    )
            return arrows

        def chamber_wall_opening_area_m2(chamber_width_m, wall_key):
            height_m = max(eave / 1000.0, 0.0)
            if wall_key in {"left", "right"}:
                return max(building_length_m, 0.0) * height_m
            return max(chamber_width_m, 0.0) * height_m

        def chamber_cpi_from_openings(
            wind_result,
            cpi_case,
            cpe_case,
            wall_states,
            external_keys,
            chamber_width_m,
            opening_areas=None,
        ):
            openings = []
            for key in external_keys:
                if not bool(wall_states.get(key, False)):
                    area = (opening_areas or {}).get(key)
                    if area is None:
                        area = chamber_wall_opening_area_m2(chamber_width_m, key)
                    if area > 1e-12:
                        openings.append((key, area))
            if not openings:
                return _enclosed_cpi_for_case(wind_result, cpi_case), "enclosed chamber Cpi"

            largest_area = max(area for _, area in openings)
            candidates = []
            for wall_key, area in openings:
                if abs(area - largest_area) > 1e-9:
                    continue
                other_area = sum(other for other_key, other in openings if other_key != wall_key)
                candidates.append(_table_5_1b_cpi_for_opening(wind_result, cpi_case, cpe_case, wall_key, area, other_area))

            pick = max if str(cpi_case).strip() == "Cpi +" else min
            cpi, ratio, surface, ka, kl = pick(candidates, key=lambda item: item[0])
            ratio_text = "6+" if ratio >= 6.0 else f"{ratio:.2g}"
            return cpi, f"Table 5.1(B) {surface} opening r={ratio_text}, Ka {ka:.2f}"

        def main_chamber_cpi(wind_result, cpi_case, cpe_case):
            if lean_to_connected_to_main("left") or lean_to_connected_to_main("right"):
                return connected_main_lean_cpi(wind_result, cpi_case, cpe_case)
            return chamber_cpi_from_openings(
                wind_result,
                cpi_case,
                cpe_case,
                {
                    "left": wind_result.inputs.left_wall_clad,
                    "right": wind_result.inputs.right_wall_clad,
                    "front": wind_result.inputs.front_wall_clad,
                    "back": wind_result.inputs.back_wall_clad,
                },
                ["left", "right", "front", "back"],
                outside_span / 1000.0,
            )

        def lean_outer_wall_is_clad(side_key):
            return left_lean_outer_wall_clad if side_key == "left" else right_lean_outer_wall_clad

        def lean_to_wall_states(side_key):
            return left_lean_wall_states if side_key == "left" else right_lean_wall_states

        def lean_to_connected_to_main(side_key):
            if side_key == "left":
                if not left_has_lean_to:
                    return False
                main_interface_clad = wind_inputs.left_wall_clad
                lean_inner_clad = left_lean_wall_states.get("right", False)
            else:
                if not right_has_lean_to:
                    return False
                main_interface_clad = wind_inputs.right_wall_clad
                lean_inner_clad = right_lean_wall_states.get("left", False)
            return not main_interface_clad and not lean_inner_clad

        def connected_main_lean_cpi(wind_result, cpi_case, cpe_case):
            left_connected = lean_to_connected_to_main("left")
            right_connected = lean_to_connected_to_main("right")
            main_width_m = outside_span / 1000.0
            left_width_m = left_canopy_length / 1000.0 if left_connected else 0.0
            right_width_m = right_canopy_length / 1000.0 if right_connected else 0.0
            total_width_m = main_width_m + left_width_m + right_width_m
            height_m = max(eave / 1000.0, 0.0)

            left_clad = left_lean_wall_states.get("left", False) if left_connected else wind_result.inputs.left_wall_clad
            right_clad = right_lean_wall_states.get("right", False) if right_connected else wind_result.inputs.right_wall_clad
            front_open_width = (0.0 if wind_result.inputs.front_wall_clad else main_width_m)
            back_open_width = (0.0 if wind_result.inputs.back_wall_clad else main_width_m)
            if left_connected:
                front_open_width += 0.0 if left_lean_wall_states.get("front", False) else left_width_m
                back_open_width += 0.0 if left_lean_wall_states.get("back", False) else left_width_m
            if right_connected:
                front_open_width += 0.0 if right_lean_wall_states.get("front", False) else right_width_m
                back_open_width += 0.0 if right_lean_wall_states.get("back", False) else right_width_m

            wall_states = {
                "left": bool(left_clad),
                "right": bool(right_clad),
                "front": front_open_width <= 1e-12,
                "back": back_open_width <= 1e-12,
            }
            opening_areas = {
                "left": 0.0 if left_clad else building_length_m * height_m,
                "right": 0.0 if right_clad else building_length_m * height_m,
                "front": front_open_width * height_m,
                "back": back_open_width * height_m,
            }
            cpi, source = chamber_cpi_from_openings(
                wind_result,
                cpi_case,
                cpe_case,
                wall_states,
                ["left", "right", "front", "back"],
                total_width_m,
                opening_areas=opening_areas,
            )
            return cpi, f"connected main/lean chamber, {source}"

        def lean_to_frame_type(side_key):
            states = lean_to_wall_states(side_key)
            inner_key = "right" if side_key == "left" else "left"
            exterior_count = sum(1 for key, value in states.items() if key != inner_key and value)
            if exterior_count == 0:
                return "Roof Only"
            clad_count = exterior_count + (1 if states.get(inner_key) else 0)
            if clad_count == 4:
                return "Enclosed"
            if clad_count == 3:
                return "3 Sided"
            if clad_count == 2:
                return "2 Sided"
            if clad_count == 1:
                return "1 Sided"
            return "Roof Only"

        def lean_to_cpi_for_case(wind_result, cpi_case, cpe_case, side_key):
            if lean_to_connected_to_main(side_key):
                return connected_main_lean_cpi(wind_result, cpi_case, cpe_case)
            states = lean_to_wall_states(side_key)
            external_keys = ["left", "front", "back"] if side_key == "left" else ["right", "front", "back"]
            chamber_width_m = left_canopy_length / 1000.0 if side_key == "left" else right_canopy_length / 1000.0
            return chamber_cpi_from_openings(wind_result, cpi_case, cpe_case, states, external_keys, chamber_width_m)

        def lean_to_enclosure_summary(side_key):
            labels = (
                {"left": "outer", "front": "front", "back": "back"}
                if side_key == "left"
                else {"right": "outer", "front": "front", "back": "back"}
            )
            states = lean_to_wall_states(side_key)
            return ", ".join(f"{label} {'clad' if states.get(key) else 'open'}" for key, label in labels.items())

        def lean_outer_wall_nodes(side_key):
            if side_key == "left" and left_lean_base is not None and left_canopy_top_nodes:
                return left_lean_base, left_canopy_top_nodes[-1]
            if side_key == "right" and right_lean_base is not None and right_canopy_top_nodes:
                return right_lean_base, right_canopy_top_nodes[-1]
            return None, None

        def cpi_for_canopy_side(wind_result, cpi_case, cpe_case, side_key):
            if (side_key == "left" and left_has_lean_to) or (side_key == "right" and right_has_lean_to):
                cpi, _ = lean_to_cpi_for_case(wind_result, cpi_case, cpe_case, side_key)
                return cpi
            chamber_width_m = left_canopy_length / 1000.0 if side_key == "left" else right_canopy_length / 1000.0
            if chamber_wall_opening_area_m2(chamber_width_m, side_key) > 1e-12:
                # Single open side: the shared rule (also used by the 3D bracing model).
                cpi, _, _ = open_canopy_cpi(wind_result, cpe_case, side_key, building_length_m, max(eave / 1000.0, 0.0))
                return cpi
            cpi, _ = chamber_cpi_from_openings(
                wind_result,
                cpi_case,
                cpe_case,
                {"left": False} if side_key == "left" else {"right": False},
                ["left"] if side_key == "left" else ["right"],
                chamber_width_m,
            )
            return cpi

        def internal_wall_net_cpi_line(wind_result, side_key, cpi_case, wind_pressure, cpe_case):
            if side_key == "left":
                if not (left_has_lean_to and wind_result.inputs.left_wall_clad):
                    return 0.0, None
                lean_cpi_for = lambda case: lean_to_cpi_for_case(wind_result, case, cpe_case, "left")[0]
                main_sign = -1.0
                lean_sign = +1.0
            else:
                if not (right_has_lean_to and wind_result.inputs.right_wall_clad):
                    return 0.0, None
                lean_cpi_for = lambda case: lean_to_cpi_for_case(wind_result, case, cpe_case, "right")[0]
                main_sign = +1.0
                lean_sign = -1.0

            candidates = []
            for main_case in CPI_CASE_OPTIONS:
                main_cpi, _ = main_chamber_cpi(wind_result, main_case, cpe_case)
                main_line = _signed_internal_cpi_line_kn_m(wind_result, main_cpi, wind_pressure)
                for lean_case in CPI_CASE_OPTIONS:
                    lean_cpi = lean_cpi_for(lean_case)
                    lean_line = _signed_internal_cpi_line_kn_m(wind_result, lean_cpi, wind_pressure)
                    signed_line = main_sign * main_line + lean_sign * lean_line
                    candidates.append((signed_line, main_case, main_cpi, lean_case, lean_cpi))
            if not candidates:
                return 0.0, None
            pick = max if str(cpi_case).strip() == "Cpi +" else min
            signed_line, main_case, main_cpi, lean_case, lean_cpi = pick(candidates, key=lambda item: item[0])
            return signed_line, {
                "side": side_key,
                "main_case": main_case,
                "main_cpi": main_cpi,
                "lean_case": lean_case,
                "lean_cpi": lean_cpi,
            }

        def apply_wall_cpe_with_lean_to(target_structure, wind_result, cpe_case, wind_pressure):
            frame_type = wind_result.inputs.frame_type
            if frame_type == "No Wind":
                return []
            if frame_type == "Roof Only" and not (left_lean_outer_wall_clad or right_lean_outer_wall_clad):
                return []

            def cpe_line_for_wall(base, top, cpe, surface):
                if base is None or top is None:
                    return 0.0
                actual_area_m2 = (
                    wind_result.inputs.bay_size_m
                    * abs(top.y - base.y)
                    / 1000.0
                )
                return wall_cpe_line_load_kn_m(
                    wind_result,
                    cpe,
                    wind_pressure,
                    surface,
                    actual_area_m2,
                )

            arrows = []
            if str(cpe_case).startswith("End"):
                side_cpe = wall_cpe_for_surface(wind_result, "side_wall")
                if frame_type not in {"Roof Only", "No Wind"}:
                    if wind_result.inputs.left_wall_clad and not left_has_lean_to:
                        side_line = cpe_line_for_wall(left_base, top_nodes[0], side_cpe, "side_wall")
                        arrows.extend(_add_horizontal_line_load(target_structure, left_base, top_nodes[0], side_line, -1.0, "LEFT_COLUMN"))
                    if wind_result.inputs.right_wall_clad and not right_has_lean_to:
                        side_line = cpe_line_for_wall(right_base, top_nodes[-1], side_cpe, "side_wall")
                        arrows.extend(_add_horizontal_line_load(target_structure, right_base, top_nodes[-1], side_line, +1.0, "RIGHT_COLUMN"))
                if left_lean_outer_wall_clad:
                    base, top = lean_outer_wall_nodes("left")
                    side_line = cpe_line_for_wall(base, top, side_cpe, "side_wall")
                    arrows.extend(_add_horizontal_line_load(target_structure, base, top, side_line, -1.0, "LEFT_LEAN_COLUMN"))
                if right_lean_outer_wall_clad:
                    base, top = lean_outer_wall_nodes("right")
                    side_line = cpe_line_for_wall(base, top, side_cpe, "side_wall")
                    arrows.extend(_add_horizontal_line_load(target_structure, base, top, side_line, +1.0, "RIGHT_LEAN_COLUMN"))
                return arrows
            left_wind = str(cpe_case).startswith("Left")

            if frame_type not in {"Roof Only", "No Wind"}:
                if left_wind:
                    if wind_result.inputs.left_wall_clad and not left_lean_outer_wall_clad:
                        line = cpe_line_for_wall(left_base, top_nodes[0], 0.7, "windward_wall")
                        arrows.extend(_add_horizontal_line_load(target_structure, left_base, top_nodes[0], line, +1.0, "LEFT_COLUMN"))
                    if wind_result.inputs.right_wall_clad and not right_lean_outer_wall_clad:
                        line = cpe_line_for_wall(right_base, top_nodes[-1], wind_result.leeward_wall_cpe, "leeward_wall")
                        arrows.extend(_add_horizontal_line_load(target_structure, right_base, top_nodes[-1], line, +1.0, "RIGHT_COLUMN"))
                else:
                    if wind_result.inputs.right_wall_clad and not right_lean_outer_wall_clad:
                        line = cpe_line_for_wall(right_base, top_nodes[-1], 0.7, "windward_wall")
                        arrows.extend(_add_horizontal_line_load(target_structure, right_base, top_nodes[-1], line, -1.0, "RIGHT_COLUMN"))
                    if wind_result.inputs.left_wall_clad and not left_lean_outer_wall_clad:
                        line = cpe_line_for_wall(left_base, top_nodes[0], wind_result.leeward_wall_cpe, "leeward_wall")
                        arrows.extend(_add_horizontal_line_load(target_structure, left_base, top_nodes[0], line, -1.0, "LEFT_COLUMN"))

            if left_lean_outer_wall_clad:
                base, top = lean_outer_wall_nodes("left")
                surface = "windward_wall" if left_wind else "leeward_wall"
                cpe = 0.7 if left_wind else wind_result.leeward_wall_cpe
                line = cpe_line_for_wall(base, top, cpe, surface)
                arrows.extend(_add_horizontal_line_load(target_structure, base, top, line, +1.0 if left_wind else -1.0, "LEFT_LEAN_COLUMN"))
            if right_lean_outer_wall_clad:
                base, top = lean_outer_wall_nodes("right")
                surface = "leeward_wall" if left_wind else "windward_wall"
                cpe = wind_result.leeward_wall_cpe if left_wind else 0.7
                line = cpe_line_for_wall(base, top, cpe, surface)
                arrows.extend(_add_horizontal_line_load(target_structure, base, top, line, +1.0 if left_wind else -1.0, "RIGHT_LEAN_COLUMN"))
            return arrows

        def apply_wall_cpi_with_lean_to(target_structure, wind_result, cpi_case, wind_pressure, cpe_case):
            frame_type = wind_result.inputs.frame_type
            if frame_type == "No Wind":
                return []
            if frame_type == "Roof Only" and not (left_lean_outer_wall_clad or right_lean_outer_wall_clad):
                return []
            arrows = []
            main_cpi, _ = main_chamber_cpi(wind_result, cpi_case, cpe_case)
            main_line = wall_cpi_line_load_kn_m(wind_result, main_cpi, wind_pressure)
            main_left_direction = -1.0 if main_cpi > 0 else +1.0
            main_right_direction = +1.0 if main_cpi > 0 else -1.0

            if frame_type not in {"Roof Only", "No Wind"} and abs(main_line) > 1e-12:
                if wind_result.inputs.left_wall_clad and not left_has_lean_to:
                    arrows.extend(_add_horizontal_line_load(target_structure, left_base, top_nodes[0], main_line, main_left_direction, "LEFT_COLUMN"))
                if wind_result.inputs.right_wall_clad and not right_has_lean_to:
                    arrows.extend(_add_horizontal_line_load(target_structure, right_base, top_nodes[-1], main_line, main_right_direction, "RIGHT_COLUMN"))

            left_net_line, _ = internal_wall_net_cpi_line(wind_result, "left", cpi_case, wind_pressure, cpe_case)
            arrows.extend(_add_signed_horizontal_line_load(target_structure, left_base, top_nodes[0], left_net_line, "LEFT_COLUMN"))
            right_net_line, _ = internal_wall_net_cpi_line(wind_result, "right", cpi_case, wind_pressure, cpe_case)
            arrows.extend(_add_signed_horizontal_line_load(target_structure, right_base, top_nodes[-1], right_net_line, "RIGHT_COLUMN"))

            if left_lean_outer_wall_clad:
                base, top = lean_outer_wall_nodes("left")
                left_lean_cpi, _ = lean_to_cpi_for_case(wind_result, cpi_case, cpe_case, "left")
                left_lean_line = wall_cpi_line_load_kn_m(wind_result, left_lean_cpi, wind_pressure)
                direction = -1.0 if left_lean_cpi > 0 else +1.0
                arrows.extend(_add_horizontal_line_load(target_structure, base, top, left_lean_line, direction, "LEFT_LEAN_COLUMN"))
            if right_lean_outer_wall_clad:
                base, top = lean_outer_wall_nodes("right")
                right_lean_cpi, _ = lean_to_cpi_for_case(wind_result, cpi_case, cpe_case, "right")
                right_lean_line = wall_cpi_line_load_kn_m(wind_result, right_lean_cpi, wind_pressure)
                direction = +1.0 if right_lean_cpi > 0 else -1.0
                arrows.extend(_add_horizontal_line_load(target_structure, base, top, right_lean_line, direction, "RIGHT_LEAN_COLUMN"))
            return arrows

        def internal_wall_cpi_summary_lines(wind_result, combination):
            lines = []
            for label, side_key in [("Left", "left"), ("Right", "right")]:
                signed_line, detail = internal_wall_net_cpi_line(
                    wind_result,
                    side_key,
                    combination.cpi_case,
                    combination.wind_pressure,
                    combination.cpe_case,
                )
                if detail is None or abs(signed_line) <= 1e-12:
                    continue
                direction = "+X" if signed_line > 0.0 else "-X"
                lines.append(
                    f"{label} internal wall Cpi: {signed_line:+.3f} kN/m ({direction}), "
                    f"main {detail['main_case']} {detail['main_cpi']:+.3f}, "
                    f"lean {detail['lean_case']} {detail['lean_cpi']:+.3f}"
                )
            return lines

        def main_roof_wind_summary_lines(wind_result, combination):
            if not main_roof_is_free_roof:
                return []
            scale = wind_pressure_scale(wind_result, combination.wind_pressure)
            segments = {}
            for n1, n2 in zip(top_nodes[:-1], top_nodes[1:]):
                dx_m = (n2.x - n1.x) / 1000.0
                dy_m = (n2.y - n1.y) / 1000.0
                segment_len_m = math.hypot(dx_m, dy_m)
                if segment_len_m <= 1e-9:
                    continue
                mid_x = (n1.x + n2.x) / 2.0
                part = main_roof_free_roof_part(mid_x, combination.cpe_case)
                values = main_roof_free_roof_values(wind_result, mid_x, combination.cpe_case)
                if values is None:
                    continue
                cpn_as, frame_cpn = values
                line_kn_m = frame_cpn * wind_result.wu_kn_m2 * scale * wind_result.inputs.bay_size_m
                item = segments.setdefault(
                    part,
                    {"length_m": 0.0, "cpn_as": cpn_as, "frame_cpn": frame_cpn, "line_kn_m": line_kn_m},
                )
                item["length_m"] += segment_len_m

            if not segments:
                return []
            lines = ["", "MAIN ROOF WIND LOADS"]
            for part in ["windward", "leeward"]:
                item = segments.get(part)
                if not item:
                    continue
                total_kn = item["line_kn_m"] * item["length_m"]
                if str(combination.cpe_case).strip().startswith("End"):
                    label = "End"
                    model_text = "AS/NZS B.4(A) transverse free roof, alpha 0, empty under"
                else:
                    label = part.title()
                    model_text = f"AS/NZS B.5 free roof, {part}, empty under"
                lines.append(f"{label:<9} wind model: {model_text}")
                lines.append(
                    f"{label:<9} Cpn net   : AS {item['cpn_as']:+8.3f}, "
                    f"frame {item['frame_cpn']:+.3f}, {item['line_kn_m']:+.3f} kN/m "
                    f"({total_kn:+.3f} kN)"
                )
            lines.append("Main roof Cpi : not applied for AS/NZS B.5 free-roof net pressure")
            return lines

        def canopy_wind_summary_lines(wind_result, combination, active_cpi):
            left_slope_m = _slope_path_length_m(left_canopy_top_nodes)
            right_slope_m = _slope_path_length_m(right_canopy_top_nodes)
            if left_slope_m <= 1e-9 and right_slope_m <= 1e-9:
                return []

            scale = wind_pressure_scale(wind_result, combination.wind_pressure)
            strips = _continuous_roof_strips_for_nodes(wind_result, combination.cpe_case, continuous_roof_top_nodes)
            roof_length_m = _slope_path_length_m(continuous_roof_top_nodes)
            lines = ["", "CANOPY / LEAN-TO WIND LOADS"]
            for label, side_key, side_slope_m, start_m, end_m in [
                ("Left", "left", left_slope_m, 0.0, left_slope_m),
                ("Right", "right", right_slope_m, roof_length_m - right_slope_m, roof_length_m),
            ]:
                if side_slope_m <= 1e-9:
                    continue
                if canopy_uses_free_roof(side_key):
                    cpn_as, frame_cpn = canopy_free_roof_values(wind_result, side_key, combination.cpe_case)
                    line_kn_m = frame_cpn * wind_result.wu_kn_m2 * scale * wind_result.inputs.bay_size_m
                    total_kn = line_kn_m * side_slope_m
                    part = _canopy_free_roof_part(side_key, combination.cpe_case)
                    if str(combination.cpe_case).strip().startswith("End"):
                        model_text = f"AS/NZS B.4(A) transverse free roof, alpha 0, {canopy_underside(side_key)} under"
                    else:
                        model_text = f"AS/NZS B.5 free roof, {part}, {canopy_underside(side_key)} under"
                    lines.append(f"{label:<5} wind model   : {model_text}")
                    lines.append(
                        f"{label:<5} Cpn net      : AS {cpn_as:+8.3f}, frame {frame_cpn:+.3f}, {line_kn_m:+.3f} kN/m "
                        f"({total_kn:+.3f} kN)"
                    )
                    continue
                cpi_source = "wall Cpi"
                if (side_key == "left" and left_has_lean_to) or (side_key == "right" and right_has_lean_to):
                    _, cpi_source = lean_to_cpi_for_case(wind_result, combination.cpi_case, combination.cpe_case, side_key)
                canopy_cpi = cpi_for_canopy_side(wind_result, combination.cpi_case, combination.cpe_case, side_key)
                canopy_cpi_line_kn_m = (
                    canopy_cpi
                    * wind_result.wu_kn_m2
                    * scale
                    * internal_pressure_factor(wind_result, canopy_cpi)
                    * wind_result.inputs.bay_size_m
                )
                cpe_total_kn = _wind_interval_load_kn(strips, start_m, end_m, scale)
                cpe_avg_kn_m = cpe_total_kn / side_slope_m
                cpi_total_kn = canopy_cpi_line_kn_m * side_slope_m
                combined_total_kn = cpe_total_kn + cpi_total_kn
                combined_avg_kn_m = combined_total_kn / side_slope_m
                lines.append(
                    f"{label:<5} Cpe avg      : {cpe_avg_kn_m:+8.3f} kN/m over {side_slope_m:.3f} m "
                    f"({cpe_total_kn:+.3f} kN)"
                )
                lines.append(
                    f"{label:<5} {cpi_source:<18}: {canopy_cpi:+8.3f} coeff, {canopy_cpi_line_kn_m:+.3f} kN/m "
                    f"({cpi_total_kn:+.3f} kN)"
                )
                if (side_key == "left" and left_has_lean_to) or (side_key == "right" and right_has_lean_to):
                    lines.append(f"{label:<5} lean-to walls: {lean_to_enclosure_summary(side_key)}")
                lines.append(
                    f"{label:<5} combined avg: {combined_avg_kn_m:+8.3f} kN/m "
                    f"({combined_total_kn:+.3f} kN)"
                )
            return lines

        def apply_roof_and_canopy_cpi(target_structure, wind_result, cpi_case, wind_pressure, cpe_case):
            arrows = []
            if not main_roof_is_free_roof:
                main_cpi, _ = main_chamber_cpi(wind_result, cpi_case, cpe_case)
                arrows.extend(
                    apply_roof_cpi_to_top_nodes(
                        target_structure,
                        top_nodes,
                        wind_result,
                        main_cpi,
                        wind_pressure,
                    )
                )
                # The same line load as apply_roof_cpi_to_top_nodes, on the eave strips.
                cpi_line_kn_m = (main_cpi * wind_result.wu_kn_m2 * wind_pressure_scale(wind_result, wind_pressure)
                                 * internal_pressure_factor(wind_result, main_cpi) * wind_result.inputs.bay_size_m)
                apply_roof_eave_strip_wind(target_structure, top_nodes, cpi_line_kn_m, cpi_line_kn_m)
            for side_key, canopy_nodes in [
                ("left", left_canopy_top_nodes),
                ("right", right_canopy_top_nodes),
            ]:
                if len(canopy_nodes) < 2:
                    continue
                if canopy_uses_free_roof(side_key):
                    continue
                canopy_cpi = cpi_for_canopy_side(wind_result, cpi_case, cpe_case, side_key)
                wind_pressure_kn_m2 = wind_result.wu_kn_m2 * wind_pressure_scale(wind_result, wind_pressure)
                line_kn_m = (
                    canopy_cpi
                    * wind_pressure_kn_m2
                    * internal_pressure_factor(wind_result, canopy_cpi)
                    * wind_result.inputs.bay_size_m
                )
                element_group = "LEFT_CANOPY_TOP" if side_key == "left" else "RIGHT_CANOPY_TOP"
                arrows.extend(
                    _add_normal_uniform_line_load_to_members(
                        target_structure,
                        canopy_nodes,
                        line_kn_m,
                        element_group,
                    )
                )
            return arrows

        def crane_column_actions(crane_position, serviceability, g_factor):
            """Crane column actions for one combination (crane_beam.crane_column_actions)."""
            if crane_portal is None or left_crane_node is None or right_crane_node is None:
                return None, ""
            from portalcalc.crane_beam import crane_column_actions as shared_crane_column_actions
            return shared_crane_column_actions(crane_portal, crane_position, serviceability, g_factor)

        def apply_crane_load(target_structure, crane_position="none", serviceability=False, g_factor=1.0):
            # Shared with the 3D bracing model (crane_beam.apply_crane_actions).
            if crane_portal is None or left_crane_node is None or right_crane_node is None:
                return []
            from portalcalc.crane_beam import apply_crane_actions
            return apply_crane_actions(target_structure, {"left": left_crane_node, "right": right_crane_node},
                                       crane_portal, crane_position, serviceability, g_factor)

        def crane_summary_lines(crane_position="none", serviceability=False, g_factor=1.0):
            if crane_rating_t <= 0.0:
                return []
            lines = ["", "CRANE LOAD (from the crane beam calculation)"]
            if crane_portal is None or left_crane_node is None or right_crane_node is None:
                lines.append("Crane load not applied: the crane stub is below the column base.")
                return lines
            actions, description = crane_column_actions(crane_position, serviceability, g_factor)
            p = crane_portal
            lines.append(f"{'Rating':<13}: {crane_rating_t:>8.3f} t")
            lines.append(f"{'Case':<13}: {description}")
            lines.append(f"{'Runway span':<13}: {p['span_mm']:>8.0f} mm, wheelbase {p['wheelbase_mm']:.0f} mm "
                         "(R = P1 + P2 (L - a) / L, one wheel over the frame)")
            lines.append(f"{'ULS vertical':<13}: {p['uls_vertical_max_kn']:>8.2f} kN max / {p['uls_vertical_min_kn']:.2f} kN min "
                         f"(dynamic x {p['hoisted_factor']:.2f})")
            lines.append(f"{'SLS vertical':<13}: {p['sls_vertical_max_kn']:>8.2f} kN max / {p['sls_vertical_min_kn']:.2f} kN min (static)")
            lines.append(f"{'Lateral':<13}: {p['uls_lateral_kn']:>8.2f} kN ULS (x {p['inertial_factor']:.2f}) / "
                         f"{p['sls_lateral_kn']:.2f} kN SLS, same direction on both columns")
            lines.append(f"{'Runway G':<13}: {p['permanent_kn']:>8.2f} kN per column (beam + rail self weight)")
            lines.append(f"{'Buffer':<13}: {p['buffer_kn']:>8.2f} kN per rail ({'entered' if p['buffer_source'] == 'entered' else 'rule from capacity and crane span'}; "
                         f"crane span {p['crane_span_mm']/1000:.2f} m); longitudinal, checked in the 3D bracing model")
            for side in ("left", "right"):
                node = p["nodes"][side]
                vertical, lateral = actions[side]
                lines.append(
                    f"{side.title() + ' column':<13}: {vertical:>8.2f} kN down, {lateral:+.2f} kN lateral | stub mid height "
                    f"{node['y_mm']:.0f} mm ({node['below_chord_mm']:.0f} below bottom chord) | eccentricity "
                    f"{node['eccentricity_mm']:.0f} mm")
            return lines

        def build_single_load_arrows():
            cases = {}

            for label, kpa, placement in roof_loads.factored_components(1.0, 1.0):
                if label in {"G", "Q", "Solar", "Fire service", "HVAC", "Other"}:
                    cases[label] = apply_roof_vertical_loads_in_zones(
                        _arrow_collector(),
                        top_nodes,
                        kpa * roof_loads.bay_size_m,
                        placement.side,
                        placement.from_percent,
                        placement.to_percent,
                    )

            left_wall_arrow = apply_wall_horizontal_loads(_arrow_collector(), left_base, top_nodes[0], wall_loads.left_plus_x, direction=+1)
            right_wall_arrow = apply_wall_horizontal_loads(_arrow_collector(), right_base, top_nodes[-1], wall_loads.right_minus_x, direction=-1)
            if left_wall_arrow:
                cases["Left wall G"] = left_wall_arrow
            if right_wall_arrow:
                cases["Right wall G"] = right_wall_arrow

            def add_storage_case(label, left_wall, right_wall):
                storage_case_results = replace(
                    storage_results,
                    inputs=replace(storage_results.inputs, left_wall=left_wall, right_wall=right_wall),
                )
                storage_arrows = apply_storage_loads_to_columns(
                    _arrow_collector(),
                    storage_case_results,
                    [left_base] + left_storage_nodes + [top_nodes[0]],
                    [right_base] + right_storage_nodes + [top_nodes[-1]],
                    factor=1.0,
                    left_base_y_mm=left_base.y,
                    right_base_y_mm=right_base.y,
                )
                if storage_arrows:
                    cases[label] = storage_arrows

            if storage_results.inputs.left_wall:
                add_storage_case("Storage Q left wall", True, False)
            if storage_results.inputs.right_wall:
                add_storage_case("Storage Q right wall", False, True)
            if storage_results.inputs.left_wall and storage_results.inputs.right_wall:
                add_storage_case("Storage Q both walls", True, True)

            for crane_label, crane_position, g_factor in [
                ("Crane G runway", "none", 1.0),
                ("Crane Q crab left, H +X", "left:+x", 0.0),
                ("Crane Q crab right, H -X", "right:-x", 0.0),
            ]:
                crane_arrows = apply_crane_load(_arrow_collector(), crane_position, True, g_factor)
                if crane_arrows:
                    cases[crane_label] = crane_arrows

            self_weight_arrows = apply_structure_self_weight(_arrow_collector(), structure.elements, 1.0).arrows
            if self_weight_arrows:
                cases["Self weight"] = self_weight_arrows

            for side_label, canopy_nodes, side_canopy_loads in [
                ("Left canopy", left_canopy_top_nodes, left_canopy_loads),
                ("Right canopy", right_canopy_top_nodes, right_canopy_loads),
            ]:
                if len(canopy_nodes) < 2:
                    continue
                for label, kpa, _ in side_canopy_loads.factored_components(1.0, 1.0):
                    if label in {"G", "Q", "Solar"}:
                        arrows = apply_roof_vertical_loads(
                            _arrow_collector(),
                            canopy_nodes,
                            kpa * side_canopy_loads.bay_size_m,
                        )
                        if arrows:
                            cases[f"{side_label} {label}"] = arrows

            for label in ["Wu Cpe Left +", "Wu Cpe Left -", "Wu Cpe Right +", "Wu Cpe Right -", "Wu Cpe End +", "Wu Cpe End -", "Ws Cpe Left +", "Ws Cpe Left -", "Ws Cpe Right +", "Ws Cpe Right -", "Ws Cpe End +", "Ws Cpe End -"]:
                cpe_case, pressure = _wind_case_for_single_label(label)
                wind_result = get_wind_result(cpe_case)
                continuous_roof_wind_result = get_continuous_roof_wind_result(cpe_case)
                arrows = []
                if main_roof_is_free_roof:
                    arrows.extend(apply_main_roof_free_roof_cpn(_arrow_collector(), continuous_roof_wind_result, cpe_case, pressure))
                else:
                    continuous_strips = _continuous_roof_strips_for_nodes(continuous_roof_wind_result, cpe_case, continuous_roof_top_nodes)
                    arrows.extend(apply_roof_wind_strips_to_nodes(_arrow_collector(), continuous_roof_top_nodes, continuous_strips, continuous_roof_wind_result, pressure))
                arrows.extend(apply_canopy_wind_cpe(_arrow_collector(), wind_result, cpe_case, pressure))
                arrows.extend(apply_wall_cpe_with_lean_to(_arrow_collector(), wind_result, cpe_case, pressure))
                if arrows:
                    cases[label] = arrows

            for cpe_case in CPE_CASE_OPTIONS:
                wind_result = get_wind_result(cpe_case)
                for cpi_case in CPI_CASE_OPTIONS:
                    arrows = []
                    arrows.extend(apply_roof_and_canopy_cpi(_arrow_collector(), wind_result, cpi_case, "ultimate", cpe_case))
                    arrows.extend(apply_wall_cpi_with_lean_to(_arrow_collector(), wind_result, cpi_case, "ultimate", cpe_case))
                    if arrows:
                        cases[f"Wu {cpi_case} with {cpe_case}"] = arrows
                    sls_arrows = []
                    sls_arrows.extend(apply_roof_and_canopy_cpi(_arrow_collector(), wind_result, cpi_case, "serviceability", cpe_case))
                    sls_arrows.extend(apply_wall_cpi_with_lean_to(_arrow_collector(), wind_result, cpi_case, "serviceability", cpe_case))
                    if sls_arrows:
                        cases[f"Ws {cpi_case} with {cpe_case}"] = sls_arrows

            return cases

        def apply_frame_wind_loads(target_structure, combination):
            """One gridline's wind (building_loads descriptors) on this 2D frame.

            Uses the same primitives as the 2D wind application, so a
            legacy_2d interior frame loads the model exactly as today.
            """
            from types import SimpleNamespace
            from portalcalc.frame.frame_loading import roof_strips_on_nodes
            case = combination.frame_loads.wind_case(combination.cpe_case)
            pressure = SimpleNamespace(wu_kn_m2=case.wu_kn_m2, ws_kn_m2=case.ws_kn_m2)
            scale = wind_pressure_scale(pressure, combination.wind_pressure)
            roof_nodes = continuous_roof_top_nodes if case.roof_basis == "cpe" else top_nodes
            canopy_bounds = {}
            if case.roof_basis == "cpe" and len(roof_nodes) > len(top_nodes):
                distances = _cumulative_roof_distances(roof_nodes)
                if len(left_canopy_top_nodes) > 1 and not left_canopy_is_free_roof:
                    canopy_bounds["left"] = (0.0, distances[len(left_canopy_top_nodes) - 1])
                if len(right_canopy_top_nodes) > 1 and not right_canopy_is_free_roof:
                    canopy_bounds["right"] = (distances[len(roof_nodes) - len(right_canopy_top_nodes)], distances[-1])
            arrows = apply_roof_wind_strips_to_nodes(
                target_structure, roof_nodes, roof_strips_on_nodes(case, roof_nodes, canopy_bounds), pressure,
                combination.wind_pressure)
            columns = {"left": (left_base, top_nodes[0], "LEFT_COLUMN"),
                       "right": (right_base, top_nodes[-1], "RIGHT_COLUMN"),
                       "left_lean": (*lean_outer_wall_nodes("left"), "LEFT_LEAN_COLUMN"),
                       "right_lean": (*lean_outer_wall_nodes("right"), "RIGHT_LEAN_COLUMN")}
            canopy_members = {"left": (left_canopy_top_nodes, "LEFT_CANOPY_TOP"),
                              "right": (right_canopy_top_nodes, "RIGHT_CANOPY_TOP")}
            for side, line in case.canopy_cpe_kn_m.items():
                nodes, group = canopy_members[side]
                arrows.extend(_add_normal_uniform_line_load_to_members(target_structure, nodes, line * scale, group))
            for column, signed in case.wall_cpe_kn_m.items():
                base, top, group = columns[column]
                arrows.extend(_add_signed_horizontal_line_load(target_structure, base, top, signed * scale, group))
            roof_cpi = case.roof_cpi_kn_m.get(combination.cpi_case)
            if roof_cpi:
                line_kn_m = roof_cpi * scale
                for n1, n2 in zip(top_nodes[:-1], top_nodes[1:]):
                    panel_len_m = math.hypot((n2.x - n1.x) / 1000, (n2.y - n1.y) / 1000)
                    total_n = line_kn_m * panel_len_m * 1000.0
                    nx, ny = add_roof_panel_pressure(target_structure, n1, n2, total_n)
                    arrows.append(((n1.x + n2.x) / 2, (n1.y + n2.y) / 2 + 800,
                                   line_kn_m * 1000.0 * nx, line_kn_m * 1000.0 * ny))
                apply_roof_eave_strip_wind(target_structure, top_nodes, line_kn_m, line_kn_m)
            for side, line in case.canopy_cpi_kn_m.get(combination.cpi_case, {}).items():
                nodes, group = canopy_members[side]
                if len(nodes) >= 2:
                    arrows.extend(_add_normal_uniform_line_load_to_members(target_structure, nodes, line * scale, group))
            for column, signed in case.wall_cpi_kn_m.get(combination.cpi_case, {}).items():
                base, top, group = columns[column]
                arrows.extend(_add_signed_horizontal_line_load(target_structure, base, top, signed * scale, group))
            return arrows

        def apply_combination_loads(combination, include_summary=False, target_structure=None, storage_case=None, include_self_weight=True):
            load_structure = target_structure or structure
            load_structure.clear_loads()
            frame_loads = getattr(combination, "frame_loads", None)
            if frame_loads is not None:
                from portalcalc.frame.frame_loading import frame_roof_loads
                basic_load_case = BasicLoadCase(frame_roof_loads(frame_loads), wall_loads)
            else:
                basic_load_case = BasicLoadCase(roof_loads, wall_loads)
            load_application = basic_load_case.apply_factored(
                load_structure,
                top_nodes,
                left_base,
                right_base,
                combination.g_factor,
                combination.q_factor,
                roof_element_groups={"TOP"},
            )
            arrows = list(load_application.arrows)
            for side_label, canopy_nodes, side_canopy_loads in [
                ("Left canopy", left_canopy_top_nodes, left_canopy_loads),
                ("Right canopy", right_canopy_top_nodes, right_canopy_loads),
            ]:
                if len(canopy_nodes) < 2:
                    continue
                if frame_loads is not None:
                    from portalcalc.frame.frame_loading import frame_attachment_loads
                    side_canopy_loads = frame_attachment_loads(frame_loads, "left" if side_label.startswith("Left") else "right")
                for _, factored_kpa, _ in side_canopy_loads.factored_components(combination.g_factor, combination.q_factor):
                    arrows.extend(
                        apply_roof_vertical_loads(
                            load_structure,
                            canopy_nodes,
                            factored_kpa * side_canopy_loads.bay_size_m,
                            element_groups={
                                "LEFT_CANOPY_TOP" if side_label.startswith("Left")
                                else "RIGHT_CANOPY_TOP"
                            },
                        )
                    )
            def element_loads():
                return dict(getattr(load_structure, "element_uniform_loads", {}) or {})
            loads_before_storage = element_loads()
            self_weight_application = None
            if include_self_weight:
                self_weight_application = apply_structure_self_weight(
                    load_structure,
                    load_structure.elements,
                    combination.g_factor,
                )
                arrows.extend(self_weight_application.arrows)
            arrows.extend(apply_crane_load(load_structure, combination.crane_position,
                                           combination.is_serviceability, combination.g_factor))
            storage_factor = storage_factor_for_combination(storage_inputs, combination)
            applied_storage_results = storage_results
            if storage_case is not None:
                _, left_storage_wall, right_storage_wall = storage_case
                applied_storage_results = replace(
                    storage_results,
                    inputs=replace(storage_results.inputs, left_wall=left_storage_wall, right_wall=right_storage_wall),
                )
            arrows.extend(
                apply_storage_loads_to_columns(
                    load_structure,
                    applied_storage_results,
                    [left_base] + left_storage_nodes + [top_nodes[0]],
                    [right_base] + right_storage_nodes + [top_nodes[-1]],
                    factor=storage_factor,
                    left_base_y_mm=left_base.y,
                    right_base_y_mm=right_base.y,
                )
            )
            loads_after_storage = element_loads()

            summary_lines = []
            if include_summary:
                summary_lines = [
                    "LOAD COMBINATION",
                    combination.name,
                    "",
                ]
                summary_lines.extend(load_application.summary_lines)
                if snow_loads["enabled"]:
                    q_source = "snow" if snow_loads["service_kpa"] >= entered_q_kpa else "entered Q"
                    summary_lines.append("")
                    summary_lines.append("SNOW LOAD")
                    summary_lines.append(f"{'Region':<13}: {snow_loads['region']}")
                    summary_lines.append(f"{'Importance':<13}: {snow_loads['importance']} (from wind inputs)")
                    summary_lines.append(f"{'h0':<13}: {snow_loads['h0_m']:>8.3f} m")
                    summary_lines.append(f"{'Roof pitch':<13}: {snow_loads['pitch_deg']:>8.3f} deg")
                    summary_lines.append(f"{'K1 / kp':<13}: {snow_loads['k1']:>8.3f} / {snow_loads['kp']:.3f}")
                    summary_lines.append(f"{'mu':<13}: {snow_loads['mu']:>8.3f}")
                    summary_lines.append(f"{'sg':<13}: {snow_loads['sg_kpa']:>8.3f} kPa")
                    summary_lines.append(f"{'LLsn ult':<13}: {snow_loads['ultimate_kpa']:>8.3f} kPa")
                    summary_lines.append(f"{'LLsn serv':<13}: {snow_loads['service_kpa']:>8.3f} kPa")
                    summary_lines.append(f"{'Entered Q':<13}: {entered_q_kpa:>8.3f} kPa")
                    summary_lines.append(f"{'Q used':<13}: {effective_q_kpa:>8.3f} kPa ({q_source})")
                summary_lines.extend(crane_summary_lines(combination.crane_position,
                                                         combination.is_serviceability, combination.g_factor))
                summary_lines.extend(storage_summary_lines(applied_storage_results, factor=storage_factor))
                for side_label, side_length, side_canopy_loads in [
                    ("LEFT CANOPY LOAD INPUTS", left_canopy_length, left_canopy_loads),
                    ("RIGHT CANOPY LOAD INPUTS", right_canopy_length, right_canopy_loads),
                ]:
                    if side_length > 0.0 and any(abs(value) > 1e-12 for value in [side_canopy_loads.g, side_canopy_loads.q, side_canopy_loads.solar]):
                        summary_lines.append("")
                        summary_lines.append(side_label)
                        summary_lines.append(f"{'G':<13}: {side_canopy_loads.g:>8.3f} kPa")
                        summary_lines.append(f"{'Q':<13}: {side_canopy_loads.q:>8.3f} kPa")
                        summary_lines.append(f"{'Solar':<13}: {side_canopy_loads.solar:>8.3f} kPa")
                        summary_lines.append(f"{'Factors':<13}: G x {combination.g_factor:.3f}, Q x {combination.q_factor:.3f}")
                summary_lines.append("")
                if self_weight_application is not None:
                    summary_lines.extend(self_weight_application.summary_lines)

            if combination.includes_wind and frame_loads is not None:
                arrows.extend(apply_frame_wind_loads(load_structure, combination))
                if include_summary:
                    from portalcalc.frame.frame_loading import summary_lines as frame_summary_lines
                    summary_lines.extend(frame_summary_lines(combination))
            elif combination.includes_wind:
                wind_result = get_wind_result(combination.cpe_case)
                continuous_roof_wind_result = get_continuous_roof_wind_result(combination.cpe_case)
                if main_roof_is_free_roof:
                    arrows.extend(
                        apply_main_roof_free_roof_cpn(
                            load_structure,
                            continuous_roof_wind_result,
                            combination.cpe_case,
                            combination.wind_pressure,
                        )
                    )
                else:
                    continuous_strips = _continuous_roof_strips_for_nodes(
                        continuous_roof_wind_result,
                        combination.cpe_case,
                        continuous_roof_top_nodes,
                    )
                    arrows.extend(
                        apply_roof_wind_strips_to_nodes(
                            load_structure,
                            continuous_roof_top_nodes,
                            continuous_strips,
                            continuous_roof_wind_result,
                            combination.wind_pressure,
                        )
                    )
                arrows.extend(apply_canopy_wind_cpe(load_structure, wind_result, combination.cpe_case, combination.wind_pressure))
                arrows.extend(apply_wall_cpe_with_lean_to(load_structure, wind_result, combination.cpe_case, combination.wind_pressure))
                arrows.extend(
                    apply_roof_and_canopy_cpi(
                        load_structure,
                        wind_result,
                        combination.cpi_case,
                        combination.wind_pressure,
                        combination.cpe_case,
                    )
                )
                arrows.extend(apply_wall_cpi_with_lean_to(load_structure, wind_result, combination.cpi_case, combination.wind_pressure, combination.cpe_case))
                if include_summary:
                    if lean_to_connected_to_main("left") or lean_to_connected_to_main("right"):
                        effective_cpi_case = combination.cpi_case
                    else:
                        effective_cpi_case = effective_cpi_case_for_wind(
                            wind_result,
                            combination.cpi_case,
                            combination.cpe_case,
                        )
                    if main_roof_is_free_roof:
                        active_cpi = 0.0
                        active_cpi_source = "not applied to free roof"
                    else:
                        active_cpi, active_cpi_source = main_chamber_cpi(
                            wind_result,
                            combination.cpi_case,
                            combination.cpe_case,
                        )
                    applied_kce, applied_kci = action_combination_factors(wind_result, active_cpi)
                    summary_lines.append("")
                    summary_lines.append(
                        f"APPLIED WIND CASES: {combination.cpe_case} + {combination.cpi_case} "
                        f"({combination.wind_pressure})"
                    )
                    orientation_text = "Any orientation" if wind_inputs.orientation == "Any" else wind_inputs.orientation
                    source_text = "Worst region direction" if wind_result.inputs.orientation == "Any" else wind_result.inputs.orientation
                    summary_lines.append(f"Section north-up    : {orientation_text}")
                    summary_lines.append(f"Wind source dir     : {source_text}")
                    if effective_cpi_case != combination.cpi_case:
                        summary_lines.append(f"Open-side Cpi rule  : {combination.cpi_case} overridden to {effective_cpi_case}")
                    summary_lines.append(f"Main chamber Cpi    : {active_cpi:+.3f} ({active_cpi_source})")
                    if main_roof_is_free_roof:
                        summary_lines.append("Applied Kce / Kci   : not applied to main roof free-roof Cpn")
                    else:
                        summary_lines.append(f"Applied Kce / Kci   : {applied_kce:.3f} / {applied_kci:.3f}")
                    summary_lines.extend(internal_wall_cpi_summary_lines(wind_result, combination))
                    summary_lines.extend(main_roof_wind_summary_lines(continuous_roof_wind_result, combination))
                    summary_lines.extend(canopy_wind_summary_lines(continuous_roof_wind_result, combination, active_cpi))
                    summary_lines.extend(wind_result.summary_lines())
            elif include_summary:
                summary_lines.append("")
                summary_lines.append("Wind: not included in this combination")

            # Roof, wall and wind loads continue above the node to the cladding
            # pitching point; crane and storage loads do not (snapshot difference).
            arrows.extend(apply_eave_strip_loads(
                load_structure, eave_strips,
                [(element_loads(), 1.0), (loads_after_storage, -1.0), (loads_before_storage, 1.0)]))

            return arrows, summary_lines

        try:
            load_arrows, load_summary_lines = apply_combination_loads(selected_combination, include_summary=True)
        except Exception as exc:
            self.results_box.setText(f"Load/wind input calculation error: {exc}")
            return

        try:
            d, R, F, analysis_info = self.solve_structure(structure)
        except np.linalg.LinAlgError:
            self.results_box.setText(
                "Solver error: stiffness matrix is singular.\n\n"
                "Try using fixed supports, checking the section properties, or reviewing the generated geometry."
            )
            return
        lean_bases = [base for base in [left_lean_base, right_lean_base] if base is not None]
        foundation_nodes = [("Left column", left_base), ("Right column", right_base)]
        foundation_nodes.extend((f"Internal column {index + 1}", base) for index, base in enumerate(internal_column_bases))
        if left_lean_base is not None:
            foundation_nodes.append(("Left lean-to column", left_lean_base))
        if right_lean_base is not None:
            foundation_nodes.append(("Right lean-to column", right_lean_base))

        storage_check_cases = [(None, storage_inputs.left_wall, storage_inputs.right_wall)]
        if storage_inputs.enabled:
            storage_check_cases = [
                ("No storage", False, False),
            ]
            if storage_inputs.left_wall:
                storage_check_cases.append(("Storage left", True, False))
            if storage_inputs.right_wall:
                storage_check_cases.append(("Storage right", False, True))
            if storage_inputs.left_wall and storage_inputs.right_wall:
                storage_check_cases.append(("Storage both", True, True))

        check_combinations = self.load_combinations
        self.building_frame_loads = None
        all_building_combinations = None
        if getattr(self, "building_load_mode", None):
            try:
                self.building_frame_loads = self.current_building_frame_loads()
                from portalcalc.frame.frame_loading import frame_load_combinations
                # One sizing group at a time: only its gridlines' loads are checked.
                check_combinations = all_building_combinations = frame_load_combinations(
                    self.building_frame_loads["frames"], gridlines=getattr(self, "building_load_gridlines", None))
            except Exception as exc:
                self.results_box.setText(f"Building load error: {exc}")
                return
        if crane_portal is None or left_crane_node is None:
            # Without a crane the crane cases repeat G + Q: don't check them.
            check_combinations = [c for c in check_combinations if crane_case(c.crane_position) is None]
            if all_building_combinations is not None:
                all_building_combinations = check_combinations
        if getattr(self, "building_load_mode", None):
            # Phase 5 active-set screening: optimiser solves check only the
            # combinations that govern (plus all gravity); the full set is the
            # acceptance gate (auto_size_current_preset).
            active_set = getattr(self, "_building_active_set", None)
            if active_set is not None:
                check_combinations = [c for c in check_combinations if c.name in active_set]

        try:
            for cpe_case in CPE_CASE_OPTIONS:
                get_wind_result(cpe_case)
            design_check_lines, design_envelope_actions = self.member_check_envelope_lines(
                structure,
                check_combinations,
                apply_combination_loads,
                top_nodes,
                span,
                foundation_nodes,
                canopy_tip_checks,
                side_midspan_checks,
                storage_check_cases,
                crane_deflection_nodes=[left_crane_node, right_crane_node] if left_crane_node is not None and right_crane_node is not None else None,
                storage_rail_deflection_nodes=storage_rail_deflection_nodes,
                rafter_haunch_length_mm=rafter_haunch_length if is_rafter else 0.0,
            )
        except Exception as exc:
            self.results_box.setText(f"Member check envelope error: {exc}")
            return

        if all_building_combinations is not None:
            from portalcalc.frame.frame_loading import governing_combination_names
            names = [c.name for c in all_building_combinations]
            self.building_check_counts = (len(check_combinations), len(all_building_combinations))
            self.building_combination_names = names
            self.building_combination_gridlines = {c.name: list(c.gridlines) for c in all_building_combinations}
            self.building_gravity_combination_names = {c.name for c in all_building_combinations if not c.includes_wind}
            if len(check_combinations) == len(all_building_combinations):
                self.building_full_governing_names = governing_combination_names(design_envelope_actions, names)

        self.foundation_summary = self.foundation_design_from_envelopes(design_envelope_actions.get("foundation_reactions", []))

        try:
            load_arrows, load_summary_lines = apply_combination_loads(selected_combination, include_summary=True)
            d, R, F, analysis_info = self.solve_structure(structure)
        except Exception as exc:
            self.results_box.setText(f"Selected load combination error after envelope checks: {exc}")
            return

        self.structure = structure
        self.displacements = d
        self.reactions = R
        self.force_vector = F
        self.analysis_info = analysis_info
        self.storage_results = storage_results
        self.storage_rail_design = storage_rail_design
        self.storage_panel_design = storage_panel_design
        self.active_eave_x_restraint = active_eave_restraint
        self.active_eave_x_spring_kn_mm = active_eave_x_spring_kn_mm
        self.eave_bracing_info = eave_bracing_info
        self.design_envelope_actions = design_envelope_actions
        if getattr(self, "_capture_computation_report", False):
            from dataclasses import asdict
            self.design_envelope_actions["computation_wind"] = [
                {"direction_case": case, **asdict(get_wind_result(case)),
                 "continuous_roof_basis": asdict(get_continuous_roof_wind_result(case))}
                for case in ("Left +", "Right +", "End +")
            ]
        if getattr(self, "_preserve_web_assignments_on_solve", False):
            # A fresh exact solve rebuilds the force envelopes, so rebuild the
            # design-only joint zones for the preserved calculated web split.
            # Neither the split nor these zones alter the analysis structure.
            self.clear_auto_web_joint_doublers()
            for web_group, group_assignments in (self.auto_web_profile_assignments or {}).items():
                self.apply_auto_web_joint_doublers(
                    web_group,
                    group_assignments,
                    (getattr(self, "auto_web_joint_doubler_sides", {}) or {}).get(web_group),
                )
        elif not getattr(self, "_suppress_auto_web_split_on_solve", False):
            active_web_specs = [
                spec for spec in self.active_auto_size_group_specs()
                if spec[2] in {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}
            ]
            web_split_report_lines = self.auto_size_two_web_assignments(
                active_web_specs,
                update_base_combos=True,
            )
            if web_split_report_lines:
                if not getattr(self, "preserve_auto_size_report", False):
                    self.auto_size_report_text = "\n".join(["Auto-sized webs", *web_split_report_lines])
        from portalcalc.frame.future_canopy_design import apply_retained_zones
        apply_retained_zones(self)
        final_doubled_zones = self.design_envelope_actions.get("doubled_chord_zones", [])
        for line_index, line in enumerate(design_check_lines):
            if not line.startswith("Doubled chord zones:"):
                continue
            if final_doubled_zones:
                total_doubled_length = sum(float(zone.get("length_mm") or 0.0) for zone in final_doubled_zones)
                design_check_lines[line_index] = (
                    f"Doubled chord zones: {len(final_doubled_zones)} panels, "
                    f"total length {total_doubled_length / 1000.0:.2f} m"
                )
            else:
                design_check_lines[line_index] = "Doubled chord zones: none required"
            break
        self.calculate_storage_load_report()
        self.costing_summary = self.costing_from_structure(structure, building_length_m, bay_size_m)
        self.base_nodes = [left_base, right_base] + internal_column_bases + lean_bases + eave_restraint_nodes
        self.support_types = [left_support, right_support] + ["Pinned"] * (len(internal_column_bases) + len(lean_bases)) + eave_restraint_types
        self.load_arrows = load_arrows
        self.wall_cladding_segments = wall_cladding_segments
        self.concept_roof_top_nodes = roof_top_nodes
        self.concept_main_top_nodes = top_nodes
        self.concept_left_lean_top_nodes = list(reversed(left_canopy_top_nodes)) if left_has_lean_to else []
        self.concept_right_lean_top_nodes = right_canopy_top_nodes if right_has_lean_to else []
        self.concept_building_length_m = building_length_m
        self.concept_bay_size_m = bay_size_m
        self.concept_wall_states = wind_inputs.wall_enclosure if hasattr(wind_inputs, "wall_enclosure") else self.envelope_plan_selector.wall_states()
        self.single_load_arrows = build_single_load_arrows()
        previous_single_load = getattr(self, "pending_single_load_text", None) or self.single_load_combo.currentText()
        self.pending_single_load_text = None
        self.single_load_combo.blockSignals(True)
        self.single_load_combo.clear()
        for label in self.single_load_arrows:
            self.single_load_combo.addItem(label)
        if previous_single_load:
            self.set_combo_text_if_present(self.single_load_combo, previous_single_load)
        self.single_load_combo.blockSignals(False)
        self.serviceability_height_mm = eave
        self.serviceability_span_mm = span
        self.serviceability_importance_level = wind_inputs.importance_level
        self.serviceability_crane_active = crane_rating_t > 0.0 and left_crane_node is not None and right_crane_node is not None
        self.update_results_text(
            num_panels, panel, web_angle, left_support, right_support,
            left_base, right_base, left_column_profile, right_column_profile, top_profile, bottom_profile, web_profile,
            load_summary_lines, design_check_lines,
            outside_span, span, left_column_offset, right_column_offset,
            left_canopy_length, right_canopy_length, left_canopy_pitch, left_canopy_shallow_depth,
            right_canopy_pitch, right_canopy_shallow_depth, left_canopy_type, right_canopy_type,
            left_canopy_eave_height, right_canopy_eave_height,
            self.left_canopy_frame_system_combo.currentText(), self.right_canopy_frame_system_combo.currentText(),
            eave_x_restraint,
            active_eave_x_spring_kn_mm,
            base_moment_spring_knm_rad,
            eave_bracing_info,
            roof_shape=roof_shape,
            rafter_haunch_length=rafter_haunch_length if is_rafter else 0.0,
            truss_external_depth=depth if not is_rafter else None,
            truss_centreline_depth=truss_centreline_depth if not is_rafter else None,
        )
        self.update_section_checks_text(
            left_column_profile,
            right_column_profile,
            internal_column_profile,
            top_profile,
            bottom_profile,
            web_profile,
            left_canopy_top_profile,
            left_canopy_bottom_profile,
            left_canopy_web_profile,
            left_lean_column_profile,
            right_canopy_top_profile,
            right_canopy_bottom_profile,
            right_canopy_web_profile,
            right_lean_column_profile,
        )
        self.update_costing_text()
        self.update_foundation_text()
        self.refresh_plot()
