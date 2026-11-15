"""Member strength and stability checks on the solved envelope."""

import copy
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np

from portalcalc.crane_beam import CRANE_GAUGE_CHANGE_LIMIT_MM
from portalcalc.frame.common import (
    DESIGN_UTILIZATION_PASS_LIMIT,
    DOUBLED_CHORD_TRIGGER_RATIO,
    governing_height_deflection_item,
    merge_web_member_demand,
    parallel_worker_count,
)
from portalcalc.loads import apply_structure_self_weight, crane_case
from portalcalc.member_checks import check_member, MemberActions


def _empty_action():
    return {"value": -1.0, "combo": "-", "member": "-", "group": "-", "length_mm": 0.0}


def _update_action(envelope, key, value, combination, element, coincident=None):
    if value > envelope[key]["value"]:
        envelope[key] = {
            "value": value,
            "combo": combination.name,
            "member": element.id,
            "group": element.group,
            "length_mm": element.length(),
        }
        if coincident:
            envelope[key].update(coincident)


def _update_average_action(envelope, key, value, combination, group, length_mm):
    if value > envelope[key]["value"]:
        envelope[key] = {
            "value": value,
            "combo": combination.name,
            "member": f"{group} average",
            "group": group,
            "length_mm": length_mm,
        }


def _action_line(label, item, unit):
    member_value = str(item["member"])
    member = f"Member {member_value}" if member_value.isdigit() else member_value
    return f"{label:<18}: {item['value']:>8.2f} {unit:<3} | {member:<10} | {item['combo']}"


def _new_member_envelope():
    return {
        "compression_kn": _empty_action(),
        "tension_kn": _empty_action(),
        "shear_kn": _empty_action(),
        "moment_knm": _empty_action(),
    }


def _new_column_top_deflections():
    return {
        group: {"value": -1.0, "combo": "-", "node": "-", "height_mm": 0.0, "group": group, "label": "Column top deflection SLS"}
        for group in ("LEFT_COLUMN", "RIGHT_COLUMN")
    }


def _new_truss_midspan_deflections():
    return {
        "G": {"value": -1.0, "combo": "-", "node": "-"},
        "Q": {"value": -1.0, "combo": "-", "node": "-"},
        "Ws": {"value": -1.0, "combo": "-", "node": "-"},
    }


def _merge_action_envelope(target, source):
    for key, source_item in source.items():
        if source_item["value"] > target[key]["value"]:
            target[key] = source_item


def _merge_foundation_reactions(target, source):
    for target_item, source_item in zip(target, source):
        for key, combo_key in [
            ("compression_kn", "compression_combo"),
            ("uplift_kn", "uplift_combo"),
            ("shear_kn", "shear_combo"),
            ("moment_knm", "moment_combo"),
        ]:
            if source_item[key] > target_item[key]:
                target_item[key] = source_item[key]
                target_item[combo_key] = source_item[combo_key]


def _merge_web_member_demands(target, source):
    for group, items in source.items():
        group_target = target.setdefault(group, {})
        for member_id, source_item in items.items():
            group_target[member_id] = merge_web_member_demand(group_target.get(member_id), source_item, owned=True)


def _merge_doubled_chord_zones(target, source):
    for zone in source.values():
        key = (zone["group"], zone["member"])
        current = target.get(key)
        if current is None or zone["single_ratio"] > current["single_ratio"]:
            target[key] = zone


def _apply_selected_doublers_to_envelope(envelope, group, zones):
    doubled_members = {
        int(zone["member"]) for zone in zones.values()
        if zone["group"] == group and str(zone["member"]).isdigit()
    }
    if not doubled_members:
        return
    for key in ["compression_kn", "tension_kn", "shear_kn", "moment_knm"]:
        item = envelope.get(key)
        if not isinstance(item, dict):
            continue
        member = item.get("member")
        if isinstance(member, str) and not member.isdigit():
            continue
        try:
            member_id = int(member)
        except (TypeError, ValueError):
            continue
        if member_id not in doubled_members:
            continue
        item["value"] *= 0.5
        item["doubled_chord"] = True
        for coincident_key in ["coincident_tension_kn", "coincident_shear_kn", "coincident_moment_knm"]:
            if coincident_key in item:
                item[coincident_key] *= 0.5


class MemberChecksMixin:
    """Envelope member checks and their report lines."""

    def _expand_doubled_chord_zones_to_web_joints(self, candidate_structure, zones):
        return self.expand_doubled_chord_zones_to_web_joints(candidate_structure, zones)

    def member_check_envelope_lines(self, structure, combinations, apply_combination_loads, top_nodes, span, foundation_nodes=None, canopy_tip_checks=None, side_midspan_checks=None, storage_check_cases=None, crane_deflection_nodes=None, storage_rail_deflection_nodes=None, rafter_haunch_length_mm=0.0):
        from portalcalc.frame.column_flybracing import COLUMN_GROUPS, capture_case, girt_levels
        self._column_flybrace_models = {}
        flybrace_bases = {}
        column_cases = {}
        roof_coordinates = {(round(n.x, 6), round(n.y, 6)) for e in structure.elements
                            if e.group in {'TOP', 'BOTTOM', 'LEFT_CANOPY_TOP', 'LEFT_CANOPY_BOTTOM',
                                           'RIGHT_CANOPY_TOP', 'RIGHT_CANOPY_BOTTOM'}
                            for n in (e.start, e.end)}
        for group in COLUMN_GROUPS:
            restraint = (self.member_check_restraints or {}).get(group, {})
            members = [e for e in structure.elements if e.group == group]
            # Crane/storage restraints retain their existing dedicated treatment.
            if not members or not restraint.get('clad') or restraint.get('crane_minor') or restraint.get('storage_restraint'):
                continue
            nodes = {n.id: n for e in members for n in (e.start, e.end)}
            base = min(nodes.values(), key=lambda n: n.y)
            top = max(nodes.values(), key=lambda n: n.y)
            if any(abs(n.x-base.x) > 1e-6 for n in nodes.values()):
                continue
            roof = [n.y for n in nodes.values() if n.y > base.y+1e-6
                    and (round(n.x, 6), round(n.y, 6)) in roof_coordinates]
            roof_node = min(roof) if roof else top.y
            flybrace_bases[group] = {
                'base_mm': base.y or 0.0, 'top_mm': top.y, 'roof_node_mm': roof_node,
                'x_mm': base.x, 'in_plane_length_mm': roof_node-base.y,
                'fixed_levels_mm': [roof_node] if roof_node < top.y-1e-6 else [],
                'girt_levels_mm': girt_levels(base.y, top.y, self.wall_girt_spacing_mm(self.wind_region_combo.currentText())),
            }
            column_cases[group] = []
        capture_computations = bool(getattr(self, "_capture_computation_report", False))
        computation_cases = []
        computation_load_lines = {}
        is_rafter = getattr(self, "frame_system_combo", None) is not None and self.frame_system_combo.currentText() == "Rafter"
        canopy_rafter_groups = set()
        if getattr(self, "left_canopy_frame_system_combo", None) is not None and self.canopy_side_is_rafter("left"):
            canopy_rafter_groups.add("LEFT_CANOPY_TOP")
        if getattr(self, "right_canopy_frame_system_combo", None) is not None and self.canopy_side_is_rafter("right"):
            canopy_rafter_groups.add("RIGHT_CANOPY_TOP")
        foundation_nodes = foundation_nodes or []
        canopy_tip_checks = canopy_tip_checks or []
        side_midspan_checks = side_midspan_checks or []
        storage_check_cases = storage_check_cases or [(None, True, True)]
        crane_deflection_nodes = [node for node in (crane_deflection_nodes or []) if node is not None]
        storage_rail_deflection_nodes = storage_rail_deflection_nodes or []
        use_second_order = getattr(self, "second_order_checkbox", None) is not None and self.second_order_checkbox.isChecked()

        column_envelopes = {
            "LEFT_COLUMN": _new_member_envelope(),
            "RIGHT_COLUMN": _new_member_envelope(),
            "INTERNAL_COLUMN": _new_member_envelope(),
            "LEFT_LEAN_COLUMN": _new_member_envelope(),
            "RIGHT_LEAN_COLUMN": _new_member_envelope(),
            "WEB": _new_member_envelope(),
            "LEFT_CANOPY_WEB": _new_member_envelope(),
            "RIGHT_CANOPY_WEB": _new_member_envelope(),
        }
        column_envelope = {
            "compression_kn": _empty_action(),
            "tension_kn": _empty_action(),
            "shear_kn": _empty_action(),
            "moment_knm": _empty_action(),
        }
        chord_envelopes = {
            "TOP": _new_member_envelope(),
            "BOTTOM": _new_member_envelope(),
            "LEFT_CANOPY_TOP": _new_member_envelope(),
            "LEFT_CANOPY_BOTTOM": _new_member_envelope(),
            "RIGHT_CANOPY_TOP": _new_member_envelope(),
            "RIGHT_CANOPY_BOTTOM": _new_member_envelope(),
        }
        single_chord_envelopes = {
            "TOP": _new_member_envelope(),
            "BOTTOM": _new_member_envelope(),
            "LEFT_CANOPY_TOP": _new_member_envelope(),
            "LEFT_CANOPY_BOTTOM": _new_member_envelope(),
            "RIGHT_CANOPY_TOP": _new_member_envelope(),
            "RIGHT_CANOPY_BOTTOM": _new_member_envelope(),
        }
        doubled_chord_zones = {}
        sls_column_top_deflection = {"value": -1.0, "combo": "-", "node": "-", "height_mm": 0.0, "label": "Column top deflection SLS"}
        sls_column_top_deflections = _new_column_top_deflections()
        sls_crane_rail_deflection = {"value": -1.0, "combo": "-", "node": "-", "height_mm": 0.0, "label": "Crane rail racking SLS"}
        sls_crane_gauge_change = {"value": -1.0, "combo": "-", "node": "-", "height_mm": 0.0, "label": "Crane rail gauge change SLS"}
        sls_column_midspan_ws_deflection = {"value": -1.0, "combo": "-", "member": "-", "group": "-", "height_mm": 0.0}
        column_deflection_groups = ["LEFT_COLUMN", "RIGHT_COLUMN", "INTERNAL_COLUMN", "LEFT_LEAN_COLUMN", "RIGHT_LEAN_COLUMN"]
        def new_column_midspan_ws_deflections():
            return {
                group: {"value": -1.0, "combo": "-", "member": "-", "group": group, "height_mm": 0.0}
                for group in column_deflection_groups
            }

        sls_column_midspan_ws_deflections = new_column_midspan_ws_deflections()
        sls_truss_midspan_deflections = _new_truss_midspan_deflections()
        def new_side_midspan_deflections():
            return {
                check["key"]: {
                    "label": check["label"],
                    "length_mm": check["length_mm"],
                    "node": getattr(check["node"], "id", "-"),
                    "components": _new_truss_midspan_deflections(),
                }
                for check in side_midspan_checks
            }

        sls_side_midspan_deflections = new_side_midspan_deflections()
        def new_canopy_tip_deflections():
            return {
                check["key"]: {
                    "value": -1.0,
                    "combo": "-",
                    "node": getattr(check["tip"], "id", "-"),
                    "anchor": getattr(check["anchor"], "id", "-"),
                    "tilt_reference": getattr(check.get("tilt_reference"), "id", "-"),
                    "label": check["label"],
                    "length_mm": check["length_mm"],
                }
                for check in canopy_tip_checks
            }

        sls_canopy_tip_deflections = new_canopy_tip_deflections()
        def new_foundation_reactions():
            reactions = []
            for label, node in foundation_nodes:
                reactions.append({
                    "label": label,
                    "node": node,
                    "node_id": node.id,
                    "compression_kn": 0.0,
                    "compression_combo": "-",
                    "compression_limit_state": "SLS",
                    "uplift_kn": 0.0,
                    "uplift_combo": "-",
                    "uplift_limit_state": "ULS",
                    "shear_kn": 0.0,
                    "shear_combo": "-",
                    "moment_knm": 0.0,
                    "moment_combo": "-",
                })
            return reactions

        foundation_reactions = new_foundation_reactions()

        top_left_x = min(node.x for node in top_nodes)
        top_right_x = max(node.x for node in top_nodes)
        midspan_x = (top_left_x + top_right_x) / 2.0
        midspan_node = min(top_nodes, key=lambda node: abs(node.x - midspan_x))
        chord_span = top_right_x - top_left_x
        inner_from = top_left_x + chord_span * 0.125
        inner_to = top_left_x + chord_span * 0.875
        bottom_inner_from = top_left_x + chord_span * 0.30
        bottom_inner_to = top_left_x + chord_span * 0.70
        uls_count = 0
        sls_count = 0
        web_member_demands = {
            "WEB": {},
            "LEFT_CANOPY_WEB": {},
            "RIGHT_CANOPY_WEB": {},
        }

        def is_main_chord_centre_panel(element):
            return (
                not is_rafter
                and
                element.group in {"TOP", "BOTTOM"}
                and (
                    abs(element.start.x - midspan_x) <= 1e-6
                    or abs(element.end.x - midspan_x) <= 1e-6
                )
            )

        def is_canopy_chord_end_panel(element, group_key):
            if group_key in canopy_rafter_groups:
                return False
            if not (group_key.endswith("CANOPY_TOP") or group_key.endswith("CANOPY_BOTTOM")):
                return False
            group_elements = [item for item in structure.elements if item.group == group_key]
            if len(group_elements) <= 2:
                return False
            min_x = min(min(item.start.x, item.end.x) for item in group_elements)
            max_x = max(max(item.start.x, item.end.x) for item in group_elements)
            element_min_x = min(element.start.x, element.end.x)
            element_max_x = max(element.start.x, element.end.x)
            return abs(element_min_x - min_x) <= 1e-6 or abs(element_max_x - max_x) <= 1e-6

        def is_single_chord_design_panel(element, group_key):
            if is_rafter and group_key == "TOP":
                return True
            if is_main_chord_centre_panel(element):
                return False
            midpoint_x = (element.start.x + element.end.x) / 2.0
            if group_key == "TOP":
                return inner_from <= midpoint_x <= inner_to
            if group_key == "BOTTOM":
                return bottom_inner_from <= midpoint_x <= bottom_inner_to
            return group_key.endswith("CANOPY_TOP") or group_key.endswith("CANOPY_BOTTOM") or inner_from <= midpoint_x <= inner_to

        def is_main_chord_haunch_panel(element, group_key):
            if not is_rafter or group_key != "TOP":
                return False
            haunch_length = max(float(rafter_haunch_length_mm or 0.0), 0.0)
            if haunch_length <= 1e-9:
                return False
            element_min_x = min(element.start.x, element.end.x)
            element_max_x = max(element.start.x, element.end.x)
            left_haunch_to = top_left_x + haunch_length
            right_haunch_from = top_right_x - haunch_length
            return element_min_x < left_haunch_to or element_max_x > right_haunch_from

        def is_doubled_chord_candidate_panel(element, group_key):
            if group_key in {"TOP", "BOTTOM"}:
                return True
            return is_single_chord_design_panel(element, group_key)

        def column_midheight_deflection_items(candidate_structure, displacements):
            if storage_rail_deflection_nodes:
                return []
            items = []
            for group in column_deflection_groups:
                group_elements = [element for element in candidate_structure.elements if element.group == group]
                if not group_elements:
                    continue
                nodes = [node for element in group_elements for node in [element.start, element.end]]
                min_y = min(node.y for node in nodes)
                max_y = max(node.y for node in nodes)
                height = max_y - min_y
                if height <= 1e-9:
                    continue
                target_y = min_y + 0.5 * height
                chosen = None
                chosen_t = 0.5
                chosen_distance = float("inf")
                for element in group_elements:
                    y1 = element.start.y
                    y2 = element.end.y
                    if abs(y2 - y1) > 1e-9:
                        t = (target_y - y1) / (y2 - y1)
                    else:
                        t = 0.5
                    clamped_t = min(max(t, 0.0), 1.0)
                    y_at_t = y1 + (y2 - y1) * clamped_t
                    distance = abs(y_at_t - target_y)
                    if distance < chosen_distance:
                        chosen = element
                        chosen_t = clamped_t
                        chosen_distance = distance
                if chosen is None:
                    continue
                ux, _ = candidate_structure.element_global_displacement_at(
                    chosen,
                    displacements,
                    chosen_t,
                )
                items.append({
                    "value": abs(ux),
                    "signed_value": ux,
                    "displacements": displacements.copy(),
                    "element_load_state": candidate_structure.element_load_snapshot(),
                    "combo": "-",
                    "member": chosen.id,
                    "group": group,
                    "height_mm": height,
                })
            return items

        def solve_candidate_structure(candidate_structure):
            if use_second_order:
                return candidate_structure.solve_second_order()
            d, reactions, force_vector = candidate_structure.solve()
            return d, reactions, force_vector, None

        def merge_combination_result(result):
            for group, case in result.get('column_flybrace_cases', {}).items():
                column_cases[group].append(case)
            if result.get("computation_case") is not None:
                computation_cases.append(result["computation_case"])
            nonlocal sls_column_top_deflection, sls_column_top_deflections, sls_crane_rail_deflection, sls_crane_gauge_change, sls_column_midspan_ws_deflection, sls_column_midspan_ws_deflections, sls_truss_midspan_deflections, sls_side_midspan_deflections, sls_canopy_tip_deflections, uls_count, sls_count
            uls_count += result["uls_count"]
            sls_count += result["sls_count"]
            for group, envelope in result["column_envelopes"].items():
                _merge_action_envelope(column_envelopes[group], envelope)
            _merge_action_envelope(column_envelope, result["column_envelope"])
            for group, envelope in result["chord_envelopes"].items():
                _merge_action_envelope(chord_envelopes[group], envelope)
            for group, envelope in result["single_chord_envelopes"].items():
                _merge_action_envelope(single_chord_envelopes[group], envelope)
            _merge_foundation_reactions(foundation_reactions, result["foundation_reactions"])
            if result["sls_column_top_deflection"]["value"] > sls_column_top_deflection["value"]:
                sls_column_top_deflection = result["sls_column_top_deflection"]
            for group, item in result["sls_column_top_deflections"].items():
                if item["value"] > sls_column_top_deflections[group]["value"]:
                    sls_column_top_deflections[group] = item
            if result["sls_crane_rail_deflection"]["value"] > sls_crane_rail_deflection["value"]:
                sls_crane_rail_deflection = result["sls_crane_rail_deflection"]
            if result["sls_crane_gauge_change"]["value"] > sls_crane_gauge_change["value"]:
                sls_crane_gauge_change = result["sls_crane_gauge_change"]
            if result["sls_column_midspan_ws_deflection"]["value"] > sls_column_midspan_ws_deflection["value"]:
                sls_column_midspan_ws_deflection = result["sls_column_midspan_ws_deflection"]
            for group, item in result["sls_column_midspan_ws_deflections"].items():
                if item["value"] > sls_column_midspan_ws_deflections[group]["value"]:
                    sls_column_midspan_ws_deflections[group] = item
            for component, item in result["sls_truss_midspan_deflections"].items():
                if item["value"] > sls_truss_midspan_deflections[component]["value"]:
                    sls_truss_midspan_deflections[component] = item
            for key, side_result in result["sls_side_midspan_deflections"].items():
                side_target = sls_side_midspan_deflections.get(key)
                if side_target is None:
                    continue
                for component, item in side_result["components"].items():
                    if item["value"] > side_target["components"][component]["value"]:
                        side_target["components"][component] = item
            for key, item in result["sls_canopy_tip_deflections"].items():
                if item["value"] > sls_canopy_tip_deflections[key]["value"]:
                    sls_canopy_tip_deflections[key] = item
            _merge_web_member_demands(web_member_demands, result["web_member_demands"])
            _merge_doubled_chord_zones(doubled_chord_zones, result["doubled_chord_zones"])

        def evaluate_combination(evaluation_item):
            evaluation_case, precomputed_solution = evaluation_item
            combination, storage_case = evaluation_case
            case_label = storage_case[0] if storage_case else None
            display_combination = replace(combination, name=f"{combination.name} [{case_label}]") if case_label else combination
            if precomputed_solution is None:
                candidate_structure = copy.deepcopy(structure)
            else:
                candidate_structure = copy.copy(structure)
                candidate_structure.restore_element_load_snapshot(precomputed_solution[3])
            local_column_envelopes = {
                "LEFT_COLUMN": _new_member_envelope(),
                "RIGHT_COLUMN": _new_member_envelope(),
                "INTERNAL_COLUMN": _new_member_envelope(),
                "LEFT_LEAN_COLUMN": _new_member_envelope(),
                "RIGHT_LEAN_COLUMN": _new_member_envelope(),
                "WEB": _new_member_envelope(),
                "LEFT_CANOPY_WEB": _new_member_envelope(),
                "RIGHT_CANOPY_WEB": _new_member_envelope(),
            }
            local_column_envelope = _new_member_envelope()
            local_chord_envelopes = {
                "TOP": _new_member_envelope(),
                "BOTTOM": _new_member_envelope(),
                "LEFT_CANOPY_TOP": _new_member_envelope(),
                "LEFT_CANOPY_BOTTOM": _new_member_envelope(),
                "RIGHT_CANOPY_TOP": _new_member_envelope(),
                "RIGHT_CANOPY_BOTTOM": _new_member_envelope(),
            }
            local_single_chord_envelopes = {
                "TOP": _new_member_envelope(),
                "BOTTOM": _new_member_envelope(),
                "LEFT_CANOPY_TOP": _new_member_envelope(),
                "LEFT_CANOPY_BOTTOM": _new_member_envelope(),
                "RIGHT_CANOPY_TOP": _new_member_envelope(),
                "RIGHT_CANOPY_BOTTOM": _new_member_envelope(),
            }
            local_foundation_reactions = new_foundation_reactions()
            local_sls_column_top_deflection = {"value": -1.0, "combo": "-", "node": "-", "height_mm": 0.0, "label": "Column top deflection SLS"}
            local_sls_column_top_deflections = _new_column_top_deflections()
            local_sls_crane_rail_deflection = {"value": -1.0, "combo": "-", "node": "-", "height_mm": 0.0, "label": "Crane rail racking SLS"}
            local_sls_crane_gauge_change = {"value": -1.0, "combo": "-", "node": "-", "height_mm": 0.0, "label": "Crane rail gauge change SLS"}
            local_sls_column_midspan_ws_deflection = {"value": -1.0, "combo": "-", "member": "-", "group": "-", "height_mm": 0.0}
            local_sls_column_midspan_ws_deflections = new_column_midspan_ws_deflections()
            local_sls_truss_midspan_deflections = _new_truss_midspan_deflections()
            local_sls_side_midspan_deflections = new_side_midspan_deflections()
            local_sls_canopy_tip_deflections = new_canopy_tip_deflections()
            local_web_member_demands = {
                "WEB": {},
                "LEFT_CANOPY_WEB": {},
                "RIGHT_CANOPY_WEB": {},
            }
            local_doubled_chord_zones = {}
            local_uls_count = 0
            local_sls_count = 0

            if precomputed_solution is None:
                _, case_load_lines = apply_combination_loads(combination, include_summary=capture_computations,
                                                            target_structure=candidate_structure, storage_case=storage_case)
                d, reactions, case_forces, case_analysis = solve_candidate_structure(candidate_structure)
            else:
                d, reactions, case_forces, _ = precomputed_solution
                case_analysis = None
                case_load_lines = computation_load_lines.get(display_combination.name, [])

            for item in local_foundation_reactions:
                node = item["node"]
                ry_kn = reactions[node.uy] / 1000.0
                rx_kn = reactions[node.ux] / 1000.0
                mz_knm = reactions[node.rz] / 1_000_000.0
                compression_kn = max(ry_kn, 0.0)
                uplift_kn = max(-ry_kn, 0.0)
                shear_kn = abs(rx_kn)
                moment_knm = abs(mz_knm)
                if combination.is_serviceability and compression_kn > item["compression_kn"]:
                    item["compression_kn"] = compression_kn
                    item["compression_combo"] = display_combination.name
                if combination.is_ultimate and uplift_kn > item["uplift_kn"]:
                    item["uplift_kn"] = uplift_kn
                    item["uplift_combo"] = display_combination.name
                if combination.is_ultimate and shear_kn > item["shear_kn"]:
                    item["shear_kn"] = shear_kn
                    item["shear_combo"] = display_combination.name
                if combination.is_ultimate and moment_knm > item["moment_knm"]:
                    item["moment_knm"] = moment_knm
                    item["moment_combo"] = display_combination.name

            if combination.is_ultimate:
                local_uls_count += 1
                chord_case_items = {group: [] for group in local_chord_envelopes}
                element_summaries = {
                    element.id: candidate_structure.element_force_summary(element, d)
                    for element in candidate_structure.elements
                }
                doubled_chord_element_ids = set()

                def element_design_envelope(element, summary, scale=1.0, doubled=False, moment_override=None):
                    def item(key):
                        value = moment_override if key == "moment_knm" and moment_override is not None else summary[key]
                        return {
                            "value": value * scale,
                            "combo": display_combination.name,
                            "member": element.id,
                            "group": element.group,
                            "length_mm": element.length(),
                            "doubled_chord": doubled,
                        }
                    envelope = {
                        "compression_kn": item("compression_kn"),
                        "tension_kn": item("tension_kn"),
                        "shear_kn": item("shear_kn"),
                        "moment_knm": item("moment_knm"),
                    }
                    if moment_override is not None:
                        envelope["compression_kn"]["coincident_moment_knm"] = moment_override * scale
                        envelope["compression_kn"]["coincident_moment_member"] = element.id
                    return envelope

                def add_doubled_chord_zone(chord_element, single_result, doubled_result, doubled_envelope):
                    doubled_chord_element_ids.add(chord_element.id)
                    zone_key = (chord_element.group, chord_element.id)
                    existing = local_doubled_chord_zones.get(zone_key)
                    if existing is not None and existing["single_ratio"] >= single_result.governing_ratio:
                        return
                    local_doubled_chord_zones[zone_key] = {
                        "group": chord_element.group,
                        "member": chord_element.id,
                        "x1": chord_element.start.x,
                        "y1": chord_element.start.y,
                        "x2": chord_element.end.x,
                        "y2": chord_element.end.y,
                        "length_mm": chord_element.length(),
                        "combo": display_combination.name,
                        "single_ratio": float(single_result.governing_ratio),
                        "single_check": single_result.governing_check,
                        "doubled_ratio": float(doubled_result.governing_ratio),
                        "doubled_check": doubled_result.governing_check,
                        "doubled_envelope": copy.deepcopy(doubled_envelope),
                    }

                truss_chord_groups = [
                    group for group in local_chord_envelopes
                    if group not in canopy_rafter_groups
                ]
                chord_groups_requiring_doublers = set()
                for chord_group in truss_chord_groups:
                    preliminary_items = [
                        (element, element_summaries[element.id])
                        for element in candidate_structure.elements
                        if element.group == chord_group and is_single_chord_design_panel(element, chord_group)
                    ]
                    if not preliminary_items:
                        continue
                    total_length = sum(element.length() for element, _ in preliminary_items)
                    if total_length <= 1e-9:
                        continue
                    average_moment = sum(summary["moment_knm"] * element.length() for element, summary in preliminary_items) / total_length
                    preliminary_envelope = _new_member_envelope()
                    _update_average_action(preliminary_envelope, "moment_knm", average_moment, display_combination, chord_group, total_length / len(preliminary_items))
                    for chord_element, summary in preliminary_items:
                        _update_action(
                            preliminary_envelope,
                            "compression_kn",
                            summary["compression_kn"],
                            display_combination,
                            chord_element,
                            coincident={
                                "coincident_tension_kn": summary["tension_kn"],
                                "coincident_shear_kn": summary["shear_kn"],
                                "coincident_moment_knm": average_moment,
                                "coincident_moment_member": f"{chord_group} average",
                            },
                        )
                        _update_action(preliminary_envelope, "tension_kn", summary["tension_kn"], display_combination, chord_element)
                        _update_action(preliminary_envelope, "shear_kn", summary["shear_kn"], display_combination, chord_element)
                    group_result = self.member_check_result(preliminary_items[0][0].profile, preliminary_envelope, chord_group)
                    if group_result.governing_ratio > DOUBLED_CHORD_TRIGGER_RATIO:
                        chord_groups_requiring_doublers.add(chord_group)

                for chord_element in candidate_structure.elements:
                    if chord_element.group not in truss_chord_groups:
                        continue
                    if chord_element.group not in chord_groups_requiring_doublers:
                        continue
                    if not is_doubled_chord_candidate_panel(chord_element, chord_element.group):
                        continue
                    summary = element_summaries[chord_element.id]
                    single_result = self.member_check_result(
                        chord_element.profile,
                        element_design_envelope(chord_element, summary),
                        chord_element.group,
                    )
                    if single_result.governing_ratio <= DOUBLED_CHORD_TRIGGER_RATIO:
                        continue
                    doubled_envelope = element_design_envelope(chord_element, summary, scale=0.5, doubled=True)
                    doubled_result = self.member_check_result(
                        chord_element.profile,
                        doubled_envelope,
                        chord_element.group,
                    )
                    add_doubled_chord_zone(chord_element, single_result, doubled_result, doubled_envelope)

                for chord_group in truss_chord_groups:
                    if chord_group not in chord_groups_requiring_doublers:
                        continue
                    preliminary_items = [
                        (element, element_summaries[element.id])
                        for element in candidate_structure.elements
                        if element.group == chord_group and is_doubled_chord_candidate_panel(element, chord_group)
                    ]
                    total_length = sum(element.length() for element, _ in preliminary_items)
                    if total_length <= 1e-9:
                        continue
                    average_moment = sum(summary["moment_knm"] * element.length() for element, summary in preliminary_items) / total_length
                    for chord_element, summary in preliminary_items:
                        single_result = self.member_check_result(
                            chord_element.profile,
                            element_design_envelope(chord_element, summary, moment_override=average_moment),
                            chord_group,
                        )
                        if single_result.governing_ratio <= DOUBLED_CHORD_TRIGGER_RATIO:
                            continue
                        doubled_envelope = element_design_envelope(
                            chord_element, summary, scale=0.5, doubled=True, moment_override=average_moment
                        )
                        doubled_result = self.member_check_result(
                            chord_element.profile,
                            doubled_envelope,
                            chord_group,
                        )
                        add_doubled_chord_zone(chord_element, single_result, doubled_result, doubled_envelope)

                for chord_group in truss_chord_groups:
                    if chord_group not in chord_groups_requiring_doublers:
                        continue
                    haunch_panels = [
                        element for element in candidate_structure.elements
                        if element.group == chord_group and is_main_chord_haunch_panel(element, chord_group)
                    ]
                    triggered_sides = set()
                    for zone in local_doubled_chord_zones.values():
                        if zone["group"] != chord_group:
                            continue
                        zone_mid = (zone["x1"] + zone["x2"]) / 2.0
                        if zone_mid < midspan_x and zone["x1"] < top_left_x + max(float(rafter_haunch_length_mm or 0.0), 0.0):
                            triggered_sides.add("left")
                        if zone_mid > midspan_x and zone["x2"] > top_right_x - max(float(rafter_haunch_length_mm or 0.0), 0.0):
                            triggered_sides.add("right")
                    for chord_element in haunch_panels:
                        midpoint_x = (chord_element.start.x + chord_element.end.x) / 2.0
                        side = "left" if midpoint_x < midspan_x else "right"
                        if side not in triggered_sides:
                            continue
                        if chord_element.id in doubled_chord_element_ids:
                            continue
                        summary = element_summaries[chord_element.id]
                        single_result = self.member_check_result(
                            chord_element.profile,
                            element_design_envelope(chord_element, summary),
                            chord_group,
                        )
                        doubled_envelope = element_design_envelope(chord_element, summary, scale=0.5, doubled=True)
                        doubled_result = self.member_check_result(
                            chord_element.profile,
                            doubled_envelope,
                            chord_group,
                        )
                        add_doubled_chord_zone(chord_element, single_result, doubled_result, doubled_envelope)

                if not is_rafter:
                    companion_requests = []
                    for zone in list(local_doubled_chord_zones.values()):
                        if zone["group"] not in {"TOP", "BOTTOM"}:
                            continue
                        zone_mid = (zone["x1"] + zone["x2"]) / 2.0
                        if inner_from <= zone_mid <= inner_to:
                            continue
                        companion_group = "BOTTOM" if zone["group"] == "TOP" else "TOP"
                        companion_requests.append((companion_group, min(zone["x1"], zone["x2"]), max(zone["x1"], zone["x2"])))
                    for companion_group, request_min_x, request_max_x in companion_requests:
                        for chord_element in candidate_structure.elements:
                            if chord_element.group != companion_group or chord_element.id in doubled_chord_element_ids:
                                continue
                            element_min_x = min(chord_element.start.x, chord_element.end.x)
                            element_max_x = max(chord_element.start.x, chord_element.end.x)
                            overlap = min(element_max_x, request_max_x) - max(element_min_x, request_min_x)
                            if overlap <= 1e-6:
                                continue
                            summary = element_summaries[chord_element.id]
                            single_result = self.member_check_result(
                                chord_element.profile,
                                element_design_envelope(chord_element, summary),
                                companion_group,
                            )
                            doubled_envelope = element_design_envelope(chord_element, summary, scale=0.5, doubled=True)
                            doubled_result = self.member_check_result(
                                chord_element.profile,
                                doubled_envelope,
                                companion_group,
                            )
                            add_doubled_chord_zone(chord_element, single_result, doubled_result, doubled_envelope)
                            local_doubled_chord_zones[(companion_group, chord_element.id)]["companion_zone"] = True

                local_doubled_chord_zones = self._expand_doubled_chord_zones_to_web_joints(
                    candidate_structure,
                    local_doubled_chord_zones,
                )
                doubled_chord_element_ids = {
                    int(zone["member"])
                    for zone in local_doubled_chord_zones.values()
                    if str(zone.get("member", "")).isdigit()
                }

                def coord_key(node):
                    return (round(node.x, 6), round(node.y, 6))

                chord_groups_by_web_group = {
                    "WEB": {"top chord": {"TOP"}, "bottom chord": {"BOTTOM"}},
                    "LEFT_CANOPY_WEB": {"top chord": {"LEFT_CANOPY_TOP"}, "bottom chord": {"LEFT_CANOPY_BOTTOM"}},
                    "RIGHT_CANOPY_WEB": {"top chord": {"RIGHT_CANOPY_TOP"}, "bottom chord": {"RIGHT_CANOPY_BOTTOM"}},
                }
                chord_elements_at = {}
                chord_group_names = set().union(*(groups for item in chord_groups_by_web_group.values() for groups in item.values()))
                for chord_element in candidate_structure.elements:
                    if chord_element.group not in chord_group_names:
                        continue
                    for node in [chord_element.start, chord_element.end]:
                        chord_elements_at.setdefault(coord_key(node), []).append(chord_element)
                web_elements_at = {}
                web_group_names = {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}
                for web_element in candidate_structure.elements:
                    if web_element.group not in web_group_names:
                        continue
                    for node in [web_element.start, web_element.end]:
                        web_elements_at.setdefault(coord_key(node), []).append(web_element)

                def member_axial_ratio(element):
                    summary = element_summaries.get(element.id) or candidate_structure.element_force_summary(element, d)
                    axial_kn = max(summary["compression_kn"], summary["tension_kn"])
                    if element.id in doubled_chord_element_ids:
                        axial_kn *= 0.5
                    capacity_kn = 0.9 * element.profile.A * (element.profile.fy or 300.0) / 1000.0
                    return axial_kn / capacity_kn if capacity_kn > 1e-12 else 0.0

                def angle_between_members_deg(element_a, element_b):
                    angle = abs(math.degrees(element_a.angle() - element_b.angle())) % 180.0
                    if angle > 90.0:
                        angle = 180.0 - angle
                    return max(angle, 1.0)

                def web_joint_context(element, web_group):
                    contexts = {}
                    chord_groups = chord_groups_by_web_group.get(web_group, {})
                    for chord_label, allowed_groups in chord_groups.items():
                        candidates_by_node = []
                        for node in [element.start, element.end]:
                            node_candidates = [
                                chord_element
                                for chord_element in chord_elements_at.get(coord_key(node), [])
                                if chord_element.group in allowed_groups
                            ]
                            if node_candidates:
                                candidates_by_node.append((node, node_candidates))
                        candidates = [
                            chord_element
                            for _, node_candidates in candidates_by_node
                            for chord_element in node_candidates
                        ]
                        if not candidates:
                            continue
                        chosen = max(
                            candidates,
                            key=lambda candidate: (
                                angle_between_members_deg(element, candidate),
                                member_axial_ratio(candidate),
                            ),
                        )
                        chord_node = next(
                            (
                                node for node, node_candidates in candidates_by_node
                                if any(candidate.id == chosen.id for candidate in node_candidates)
                            ),
                            None,
                        )
                        incident_chord_members = []
                        if chord_node is not None:
                            incident_chord_members = [
                                chord_element
                                for chord_element in chord_elements_at.get(coord_key(chord_node), [])
                                if chord_element.group in allowed_groups
                            ]
                        peer_webs = []
                        if chord_node is not None:
                            peer_webs = [
                                peer for peer in web_elements_at.get(coord_key(chord_node), [])
                                if peer.id != element.id and peer.group == element.group
                            ]
                        peer = max(peer_webs, key=lambda peer_element: max(
                            element_summaries[peer_element.id]["compression_kn"],
                            element_summaries[peer_element.id]["tension_kn"],
                        ), default=None)
                        contexts[chord_label] = {
                            "branch_angle_deg": angle_between_members_deg(element, chosen),
                            "chord_axial_ratio": member_axial_ratio(chosen),
                            "chord_member": chosen.id,
                            "chord_members": sorted({candidate.id for candidate in incident_chord_members or [chosen]}),
                            "chord_node": coord_key(chord_node) if chord_node is not None else None,
                            "doubled_chord": chosen.id in doubled_chord_element_ids,
                        }
                        if peer is not None:
                            peer_summary = element_summaries[peer.id]
                            peer_dimensions = self.hollow_connection_dimensions(peer.profile)
                            contexts[chord_label]["k_joint"] = {
                                "peer_member": peer.id,
                                "peer_angle_deg": angle_between_members_deg(peer, chosen),
                                "peer_axial_kn": max(peer_summary["compression_kn"], peer_summary["tension_kn"]),
                                "peer_width_mm": (
                                    peer_dimensions["width"] if peer_dimensions is not None else None
                                ),
                                "branch_count": 2,
                            }
                    return contexts

                for element in candidate_structure.elements:
                    summary = element_summaries[element.id]
                    group_key = element.group
                    if element.group in {"LEFT_CANOPY_WEB", "LEFT_CANOPY_POST"}:
                        group_key = "LEFT_CANOPY_WEB"
                    elif element.group in {"RIGHT_CANOPY_WEB", "RIGHT_CANOPY_POST"}:
                        group_key = "RIGHT_CANOPY_WEB"
                    elif element.group in {"WEB", "POST", "APEX"}:
                        group_key = "WEB"
                    moment_knm = summary["moment_knm"]
                    if group_key == "INTERNAL_COLUMN":
                        strong_depth_mm = element.profile.depth or element.profile.flange_width or 0.0
                        eccentric_moment_knm = summary["compression_kn"] * (strong_depth_mm / 2.0) / 1000.0
                        moment_knm = max(moment_knm, eccentric_moment_knm)
                    if group_key in local_column_envelopes:
                        envelope = local_column_envelopes[group_key]
                        _update_action(envelope, "compression_kn", summary["compression_kn"], display_combination, element)
                        _update_action(envelope, "tension_kn", summary["tension_kn"], display_combination, element)
                        _update_action(envelope, "shear_kn", summary["shear_kn"], display_combination, element)
                        _update_action(envelope, "moment_knm", moment_knm, display_combination, element)
                        if group_key in {"LEFT_COLUMN", "RIGHT_COLUMN", "INTERNAL_COLUMN"}:
                            _update_action(local_column_envelope, "compression_kn", summary["compression_kn"], display_combination, element)
                            _update_action(local_column_envelope, "tension_kn", summary["tension_kn"], display_combination, element)
                            _update_action(local_column_envelope, "shear_kn", summary["shear_kn"], display_combination, element)
                            _update_action(local_column_envelope, "moment_knm", moment_knm, display_combination, element)
                        if group_key in local_web_member_demands:
                            axial_kn = max(summary["compression_kn"], summary["tension_kn"])
                            connection_context = web_joint_context(element, group_key)
                            connection_ratio = 0.0
                            top_connection_profile, bottom_connection_profile = self.web_connection_group_profiles(group_key)
                            for chord_label, chord_profile in (
                                ("top chord", top_connection_profile),
                                ("bottom chord", bottom_connection_profile or top_connection_profile),
                            ):
                                checks = self.welded_chord_web_connection_checks(
                                    element.profile,
                                    chord_profile,
                                    axial_kn,
                                    connection_context.get(chord_label),
                                )
                                connection_ratio = max(
                                    connection_ratio,
                                    max((check["ratio"] for check in checks), default=0.0),
                                )
                            source_demand = {
                                    "axial_kn": axial_kn,
                                    "compression_kn": summary["compression_kn"],
                                    "tension_kn": summary["tension_kn"],
                                    "combo": display_combination.name,
                                    "member": element.id,
                                    "element_group": element.group,
                                    "connection": connection_context,
                                    "connection_ratio": connection_ratio,
                                    "connection_demand_kn": axial_kn,
                                    "connection_combo": display_combination.name,
                                    "length_mm": element.length(),
                            }
                            local_web_member_demands[group_key][element.id] = merge_web_member_demand(
                                local_web_member_demands[group_key].get(element.id), source_demand, owned=True)
                    elif group_key in local_chord_envelopes:
                        if is_canopy_chord_end_panel(element, group_key):
                            continue
                        include_chord = is_single_chord_design_panel(element, group_key)
                        if include_chord:
                            design_scale = 0.5 if element.id in doubled_chord_element_ids else 1.0
                            chord_case_items[group_key].append((element, summary, design_scale))

                for group, items in chord_case_items.items():
                    if not items:
                        continue
                    total_length = sum(element.length() for element, _, _ in items)
                    average_moment = 0.0
                    if total_length > 0.0:
                        single_average_moment = sum(summary["moment_knm"] * element.length() for element, summary, _ in items) / total_length
                        average_moment = sum(summary["moment_knm"] * design_scale * element.length() for element, summary, design_scale in items) / total_length
                    else:
                        single_average_moment = 0.0
                    _update_average_action(local_single_chord_envelopes[group], "moment_knm", single_average_moment, display_combination, group, total_length / len(items))
                    _update_average_action(local_chord_envelopes[group], "moment_knm", average_moment, display_combination, group, total_length / len(items))
                    for element, summary, design_scale in items:
                        single_coincident = {
                            "coincident_tension_kn": summary["tension_kn"],
                            "coincident_shear_kn": summary["shear_kn"],
                            "coincident_moment_knm": single_average_moment,
                            "coincident_moment_member": f"{group} average",
                        }
                        _update_action(
                            local_single_chord_envelopes[group],
                            "compression_kn",
                            summary["compression_kn"],
                            display_combination,
                            element,
                            coincident=single_coincident,
                        )
                        _update_action(local_single_chord_envelopes[group], "tension_kn", summary["tension_kn"], display_combination, element)
                        _update_action(local_single_chord_envelopes[group], "shear_kn", summary["shear_kn"], display_combination, element)
                        coincident = {
                            "coincident_tension_kn": summary["tension_kn"] * design_scale,
                            "coincident_shear_kn": summary["shear_kn"] * design_scale,
                            "coincident_moment_knm": average_moment,
                            "coincident_moment_member": f"{group} average",
                        }
                        if design_scale < 1.0:
                            coincident["doubled_chord"] = True
                        _update_action(
                            local_chord_envelopes[group],
                            "compression_kn",
                            summary["compression_kn"] * design_scale,
                            display_combination,
                            element,
                            coincident=coincident,
                        )
                        _update_action(local_chord_envelopes[group], "tension_kn", summary["tension_kn"] * design_scale, display_combination, element, coincident if design_scale < 1.0 else None)
                        _update_action(local_chord_envelopes[group], "shear_kn", summary["shear_kn"] * design_scale, display_combination, element, coincident if design_scale < 1.0 else None)

            if combination.is_serviceability:
                local_sls_count += 1
                ws_only_serviceability = getattr(combination, "serviceability_component", None) == "Ws"
                # Crane rails, under the crane-in-service cases: racking (the
                # frame's sway, the mean of the two rails) against H/n, and the
                # change in rail gauge (right minus left) against a fixed limit.
                if len(crane_deflection_nodes) == 2 and crane_case(getattr(combination, "crane_position", None)):
                    left_rail, right_rail = crane_deflection_nodes
                    u_left, u_right = d[left_rail.ux], d[right_rail.ux]
                    racking = 0.5 * (u_left + u_right)
                    gauge = u_right - u_left
                    common = {"displacements": d.copy(), "combo": display_combination.name,
                              "node": f"{left_rail.id}/{right_rail.id}",
                              "height_mm": 0.5 * (left_rail.y + right_rail.y),
                              "left_mm": u_left, "right_mm": u_right}
                    if abs(racking) > local_sls_crane_rail_deflection["value"]:
                        local_sls_crane_rail_deflection = {**common, "value": abs(racking), "signed_value": racking,
                                                           "label": "Crane rail racking SLS"}
                    if abs(gauge) > local_sls_crane_gauge_change["value"]:
                        local_sls_crane_gauge_change = {**common, "value": abs(gauge), "signed_value": gauge,
                                                        "limit_mm": CRANE_GAUGE_CHANGE_LIMIT_MM,
                                                        "label": "Crane rail gauge change SLS"}
                if ws_only_serviceability:
                    for node, group in [(top_nodes[0], "LEFT_COLUMN"), (top_nodes[-1], "RIGHT_COLUMN")]:
                        signed_ux = d[node.ux]
                        ux = abs(signed_ux)
                        if ux > local_sls_column_top_deflection["value"]:
                            local_sls_column_top_deflection = {
                                "value": ux,
                                "signed_value": signed_ux,
                                "displacements": d.copy(),
                                "combo": display_combination.name,
                                "node": node.id,
                                "height_mm": node.y,
                                "label": "Column top deflection SLS",
                            }
                        if ux > local_sls_column_top_deflections[group]["value"]:
                            local_sls_column_top_deflections[group] = {
                                "value": ux,
                                "signed_value": signed_ux,
                                "displacements": d.copy(),
                                "combo": display_combination.name,
                                "node": node.id,
                                "height_mm": node.y,
                                "group": group,
                                "label": "Column top deflection SLS",
                            }
                if combination.includes_wind and combination.wind_pressure == "serviceability":
                    for item in column_midheight_deflection_items(candidate_structure, d):
                        if item["value"] > local_sls_column_midspan_ws_deflection["value"]:
                            local_sls_column_midspan_ws_deflection = {
                                **item,
                                "combo": display_combination.name,
                            }
                        group = item["group"]
                        if item["value"] > local_sls_column_midspan_ws_deflections[group]["value"]:
                            local_sls_column_midspan_ws_deflections[group] = {
                                **item,
                                "combo": display_combination.name,
                            }
                truss_component = getattr(combination, "serviceability_component", None)
                if truss_component in local_sls_truss_midspan_deflections:
                    signed_midspan_uy = d[midspan_node.uy]
                    midspan_uy = abs(signed_midspan_uy)
                    if midspan_uy > local_sls_truss_midspan_deflections[truss_component]["value"]:
                        local_sls_truss_midspan_deflections[truss_component] = {
                            "value": midspan_uy,
                            "signed_value": signed_midspan_uy,
                            "displacements": d.copy(),
                            "combo": display_combination.name,
                            "node": midspan_node.id,
                        }
                    for check in side_midspan_checks:
                        t = min(max((check.get("station_mm") or 0.0) / max(check.get("length_mm") or 0.0, 1e-9), 0.0), 1.0)
                        support_line_uy = (1.0 - t) * d[check["start"].uy] + t * d[check["end"].uy]
                        signed_side_midspan_uy = d[check["node"].uy] - support_line_uy
                        side_midspan_uy = abs(signed_side_midspan_uy)
                        side_target = local_sls_side_midspan_deflections[check["key"]]["components"][truss_component]
                        if side_midspan_uy > side_target["value"]:
                            local_sls_side_midspan_deflections[check["key"]]["components"][truss_component] = {
                                "value": side_midspan_uy,
                                "signed_value": signed_side_midspan_uy,
                                "displacements": d.copy(),
                                "combo": display_combination.name,
                                "node": check["node"].id,
                                "start": check["start"].id,
                                "end": check["end"].id,
                            }
                for check in canopy_tip_checks:
                    tip = check["tip"]
                    anchor = check["anchor"]
                    tilt_reference = check.get("tilt_reference")
                    reference_uy = d[anchor.uy]
                    reference_note = "anchor vertical"
                    if tilt_reference is not None:
                        depth_mm = anchor.y - tilt_reference.y
                        if abs(depth_mm) > 1e-9:
                            original_dx = anchor.x - tilt_reference.x
                            deformed_dx = (
                                (anchor.x + d[anchor.ux])
                                - (tilt_reference.x + d[tilt_reference.ux])
                            )
                            support_rotation_rad = -(deformed_dx - original_dx) / depth_mm
                            reference_uy += support_rotation_rad * (tip.x - anchor.x)
                            reference_note = "anchor tilt line"
                    signed_tip_delta = d[tip.uy] - reference_uy
                    tip_delta = abs(signed_tip_delta)
                    if tip_delta > local_sls_canopy_tip_deflections[check["key"]]["value"]:
                        local_sls_canopy_tip_deflections[check["key"]] = {
                            "value": tip_delta,
                            "signed_value": signed_tip_delta,
                            "displacements": d.copy(),
                            "combo": display_combination.name,
                            "node": tip.id,
                            "anchor": anchor.id,
                            "tilt_reference": getattr(tilt_reference, "id", "-"),
                            "reference": reference_note,
                            "label": check["label"],
                            "length_mm": check["length_mm"],
                        }

            computation_case = None
            if capture_computations:
                from portalcalc.reports.computation_report_data import case_record
                computation_case = case_record(candidate_structure, combination, display_combination.name,
                                                d, reactions, case_forces, case_analysis, case_load_lines)
            return {
                "computation_case": computation_case,
                "column_flybrace_cases": capture_case(candidate_structure, d, flybrace_bases, display_combination.name)
                    if combination.is_ultimate and flybrace_bases else {},
                "column_envelopes": local_column_envelopes,
                "column_envelope": local_column_envelope,
                "chord_envelopes": local_chord_envelopes,
                "single_chord_envelopes": local_single_chord_envelopes,
                "sls_column_top_deflection": local_sls_column_top_deflection,
                "sls_column_top_deflections": local_sls_column_top_deflections,
                "sls_crane_rail_deflection": local_sls_crane_rail_deflection,
                "sls_crane_gauge_change": local_sls_crane_gauge_change,
                "sls_column_midspan_ws_deflection": local_sls_column_midspan_ws_deflection,
                "sls_column_midspan_ws_deflections": local_sls_column_midspan_ws_deflections,
                "sls_truss_midspan_deflections": local_sls_truss_midspan_deflections,
                "sls_side_midspan_deflections": local_sls_side_midspan_deflections,
                "sls_canopy_tip_deflections": local_sls_canopy_tip_deflections,
                "web_member_demands": local_web_member_demands,
                "doubled_chord_zones": local_doubled_chord_zones,
                "foundation_reactions": local_foundation_reactions,
                "uls_count": local_uls_count,
                "sls_count": local_sls_count,
            }

        evaluation_cases = [(combination, storage_case) for combination in combinations for storage_case in storage_check_cases]
        if use_second_order:
            evaluation_items = [(evaluation_case, None) for evaluation_case in evaluation_cases]
        else:
            elastic_k = structure.elastic_stiffness_matrix()
            cache = None if capture_computations else getattr(self, "optimisation_load_vector_cache", None)
            geometry_signature = None
            case_signature = None
            base_load_matrix = None
            base_element_load_states = None
            if cache is not None:
                geometry_signature = (
                    tuple((round(node.x, 6), round(node.y, 6), node.ux, node.uy, node.rz) for node in structure.nodes),
                    tuple((element.start.id, element.end.id, element.group) for element in structure.elements),
                    tuple(sorted(structure.springs.items())),
                    tuple(getattr(structure, "sway_springs", ())),
                    tuple(sorted(structure.restrained_dofs)),
                )
                case_signature = tuple(
                    (
                        combination.name,
                        combination.g_factor,
                        combination.q_factor,
                        combination.wind_pressure,
                        combination.cpe_case,
                        combination.cpi_case,
                        combination.crane_position,
                        storage_case,
                    )
                    for combination, storage_case in evaluation_cases
                )
                cached_load_data = cache.get((geometry_signature, case_signature))
                if isinstance(cached_load_data, dict):
                    base_load_matrix = cached_load_data.get("matrix")
                    base_element_load_states = cached_load_data.get("element_load_states")

            if base_load_matrix is None or base_element_load_states is None:
                base_load_vectors = []
                base_element_load_states = []
                for combination, storage_case in evaluation_cases:
                    _, captured_lines = apply_combination_loads(
                        combination,
                        include_summary=capture_computations,
                        target_structure=structure,
                        storage_case=storage_case,
                        include_self_weight=False,
                    )
                    if capture_computations:
                        case_label = storage_case[0] if storage_case else None
                        name = f"{combination.name} [{case_label}]" if case_label else combination.name
                        computation_load_lines[name] = captured_lines
                    base_load_vectors.append(structure.load_vector())
                    base_element_load_states.append(structure.element_load_snapshot())
                base_load_matrix = np.column_stack(base_load_vectors)
                if cache is not None:
                    cache[(geometry_signature, case_signature)] = {
                        "matrix": base_load_matrix.copy(),
                        "element_load_states": copy.deepcopy(base_element_load_states),
                    }

            self_weight_vectors = []
            for combination, _ in evaluation_cases:
                structure.clear_loads()
                apply_structure_self_weight(structure, structure.elements, combination.g_factor)
                self_weight_vectors.append(structure.load_vector())
            structure.clear_loads()
            load_matrix = base_load_matrix + np.column_stack(self_weight_vectors)
            displacement_matrix = structure.solve_load_matrix_with_stiffness(elastic_k, load_matrix)
            reaction_matrix = elastic_k @ displacement_matrix - load_matrix
            evaluation_items = [
                (
                    evaluation_case,
                    (
                        displacement_matrix[:, index],
                        reaction_matrix[:, index],
                        load_matrix[:, index],
                        base_element_load_states[index],
                    ),
                )
                for index, evaluation_case in enumerate(evaluation_cases)
            ]
        # First-order cases are already solved together above; what remains per
        # combination is pure-Python post-processing, which threads cannot run
        # in parallel (the GIL), so pool overhead only slows it (measured
        # 1.08 s threaded v 0.96 s serial per factory-default solve).
        max_workers = parallel_worker_count(len(evaluation_cases)) if use_second_order else 1
        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for result in executor.map(evaluate_combination, evaluation_items):
                    merge_combination_result(result)
        else:
            for evaluation_item in evaluation_items:
                merge_combination_result(evaluate_combination(evaluation_item))
        # A skillion frame has different eave heights. The governing envelope
        # must therefore be selected by demand/limit (value/height), not by the
        # largest raw displacement, or a section change can make the reported
        # limit jump from one eave height to the other.
        governing_top_item = governing_height_deflection_item(sls_column_top_deflections.values())
        if governing_top_item is not None:
            sls_column_top_deflection = governing_top_item
        governing_mid_item = governing_height_deflection_item(sls_column_midspan_ws_deflections.values())
        if governing_mid_item is not None:
            sls_column_midspan_ws_deflection = governing_mid_item

        def first_group_profile(group):
            for element in structure.elements:
                if element.group == group:
                    return element.profile
            return None

        def add_aggregate_doubled_chord_zone(group, profile, single_envelope, single_result):
            if single_result is None or single_result.governing_ratio <= DOUBLED_CHORD_TRIGGER_RATIO:
                return
            source_key = "compression_kn"
            if single_result.governing_check == "Tension":
                source_key = "tension_kn"
            elif single_result.governing_check == "Shear":
                source_key = "shear_kn"
            source = single_envelope.get(source_key, {})
            member = source.get("member")
            if isinstance(member, str) and not member.isdigit():
                return
            try:
                member_id = int(member)
            except (TypeError, ValueError):
                return
            chord_element = next(
                (element for element in structure.elements if element.group == group and element.id == member_id),
                None,
            )
            if chord_element is None:
                return
            adjacent_members = {
                element.id
                for element in structure.elements
                if element.group == group
                and (
                    element.id == chord_element.id
                    or abs(element.start.x - chord_element.start.x) <= 1e-6
                    or abs(element.end.x - chord_element.start.x) <= 1e-6
                    or abs(element.start.x - chord_element.end.x) <= 1e-6
                    or abs(element.end.x - chord_element.end.x) <= 1e-6
                )
            }
            aggregate_zones = {}
            for element in structure.elements:
                if element.group != group or element.id not in adjacent_members:
                    continue
                aggregate_zones[(group, element.id)] = {
                    "group": group,
                    "member": element.id,
                    "x1": element.start.x,
                    "y1": element.start.y,
                    "x2": element.end.x,
                    "y2": element.end.y,
                    "length_mm": element.length(),
                    "combo": source.get("combo", "-"),
                    "single_ratio": float(single_result.governing_ratio),
                    "single_check": single_result.governing_check,
                    "doubled_ratio": float(single_result.governing_ratio),
                    "doubled_check": single_result.governing_check,
                    "doubled_envelope": None,
                    "aggregate_envelope_trigger": True,
                    "aggregate_source_member": chord_element.id,
                    "aggregate_incident_members": sorted(adjacent_members),
                    "aggregate_incident_count": len(adjacent_members),
                }
            if not aggregate_zones:
                return
            aggregate_zones = self._expand_doubled_chord_zones_to_web_joints(structure, aggregate_zones)
            doubled_envelope = copy.deepcopy(single_envelope)
            _apply_selected_doublers_to_envelope(doubled_envelope, group, aggregate_zones)
            doubled_result = self.member_check_result(profile, doubled_envelope, group)
            for zone_key, aggregate_zone in aggregate_zones.items():
                aggregate_zone["doubled_ratio"] = float(doubled_result.governing_ratio)
                aggregate_zone["doubled_check"] = doubled_result.governing_check
                aggregate_zone["doubled_envelope"] = copy.deepcopy(doubled_envelope)
                existing = doubled_chord_zones.get(zone_key)
                if existing is None or aggregate_zone["single_ratio"] > existing["single_ratio"]:
                    doubled_chord_zones[zone_key] = aggregate_zone

        truss_chord_groups = [
            "TOP", "BOTTOM",
            "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM",
            "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM",
        ]
        for chord_group in truss_chord_groups:
            profile = first_group_profile(chord_group)
            single_envelope = single_chord_envelopes[chord_group]
            single_result = self.member_check_result(profile, single_envelope, chord_group) if profile is not None else None
            add_aggregate_doubled_chord_zone(chord_group, profile, single_envelope, single_result)
            if single_result is None or single_result.governing_ratio <= DOUBLED_CHORD_TRIGGER_RATIO:
                retained_zones = {
                    key: zone for key, zone in doubled_chord_zones.items()
                    if zone["group"] == chord_group
                    and (zone.get("companion_zone") or zone.get("web_joint_trigger"))
                }
                chord_envelopes[chord_group] = single_envelope
                doubled_chord_zones = {
                    key: zone for key, zone in doubled_chord_zones.items()
                    if zone["group"] != chord_group or key in retained_zones
                }
                if retained_zones:
                    _apply_selected_doublers_to_envelope(chord_envelopes[chord_group], chord_group, retained_zones)
                    worst_zone = max(retained_zones.values(), key=lambda zone: zone["doubled_ratio"])
                    chord_envelopes[chord_group]["doubled_chord_zone"] = {
                        "value": worst_zone["doubled_ratio"],
                        "combo": worst_zone["combo"],
                        "member": worst_zone["member"],
                        "group": chord_group,
                        "length_mm": worst_zone["length_mm"],
                        "single_ratio": worst_zone["single_ratio"],
                        "doubled_ratio": worst_zone["doubled_ratio"],
                        "single_check": worst_zone["single_check"],
                        "doubled_check": worst_zone["doubled_check"],
                        "doubled_envelope": copy.deepcopy(worst_zone.get("doubled_envelope")),
                    }
            else:
                _apply_selected_doublers_to_envelope(chord_envelopes[chord_group], chord_group, doubled_chord_zones)
                group_zones = [zone for zone in doubled_chord_zones.values() if zone["group"] == chord_group]
                if group_zones:
                    worst_zone = max(group_zones, key=lambda zone: zone["doubled_ratio"])
                    chord_envelopes[chord_group]["doubled_chord_zone"] = {
                        "value": worst_zone["doubled_ratio"],
                        "combo": worst_zone["combo"],
                        "member": worst_zone["member"],
                        "group": chord_group,
                        "length_mm": worst_zone["length_mm"],
                        "single_ratio": worst_zone["single_ratio"],
                        "doubled_ratio": worst_zone["doubled_ratio"],
                        "single_check": worst_zone["single_check"],
                        "doubled_check": worst_zone["doubled_check"],
                        "doubled_envelope": copy.deepcopy(worst_zone.get("doubled_envelope")),
                    }

        lines = ["DESIGN CHECK ENVELOPES"]
        lines.append(f"ULS cases checked : {uls_count}")
        lines.append(f"SLS cases checked : {sls_count}")
        lines.append("")
        lines.append("SERVICEABILITY ENVELOPES")
        lines.append(
            f"{sls_column_top_deflection.get('label', 'Column max UX'):<18}: {sls_column_top_deflection['value']:>8.3f} mm  | "
            f"Node {sls_column_top_deflection['node']:<7} | {sls_column_top_deflection['combo']}"
        )
        for crane_item, crane_label in ((sls_crane_rail_deflection, "Crane rail racking"),
                                        (sls_crane_gauge_change, "Crane gauge change")):
            if crane_item["value"] >= 0.0:
                lines.append(
                    f"{crane_label:<18}: {crane_item['value']:>8.3f} mm  | "
                    f"Nodes {crane_item['node']:<7} | {crane_item['combo']}"
                )
        if sls_column_midspan_ws_deflection.get("value", -1.0) >= 0.0:
            lines.append(
                f"{'Column mid UX Ws':<18}: {sls_column_midspan_ws_deflection['value']:>8.3f} mm  | "
                f"{sls_column_midspan_ws_deflection['group']:<14} | {sls_column_midspan_ws_deflection['combo']}"
            )
        for component in ["G", "Q", "Ws"]:
            item = sls_truss_midspan_deflections[component]
            lines.append(
                f"{'Truss midspan ' + component:<18}: {item['value']:>8.3f} mm  | "
                f"Node {item['node']:<7} | {item['combo']}"
            )
        for side_result in sls_side_midspan_deflections.values():
            for component in ["G", "Q", "Ws"]:
                item = side_result["components"][component]
                lines.append(
                    f"{side_result['label'] + ' ' + component:<18}: {item['value']:>8.3f} mm  | "
                    f"Node {item['node']:<7} | {item['combo']}"
                )
        for item in sls_canopy_tip_deflections.values():
            lines.append(
                f"{item['label']:<18}: {item['value']:>8.3f} mm  | "
                f"Node {item['node']:<7} | {item['combo']}"
            )
        lines.append("")
        lines.append("STRENGTH ENVELOPES")
        lines.append("")
        lines.append("COLUMNS - ULS ENVELOPE")
        lines.append(_action_line("Compression", column_envelope["compression_kn"], "kN"))
        lines.append(_action_line("Tension", column_envelope["tension_kn"], "kN"))
        lines.append(_action_line("Shear", column_envelope["shear_kn"], "kN"))
        lines.append(_action_line("Bending", column_envelope["moment_knm"], "kNm"))
        lines.append("")
        lines.append("RAFTER - ULS ENVELOPE" if is_rafter else "TRUSS CHORDS - ULS ENVELOPE")
        if is_rafter:
            lines.append("Rafter envelope uses the full rafter length")
        else:
            if doubled_chord_zones:
                total_doubled_length = sum(zone["length_mm"] for zone in doubled_chord_zones.values())
                lines.append(f"Doubled chord zones: {len(doubled_chord_zones)} panels, total length {total_doubled_length / 1000.0:.2f} m")
                lines.append("Doubled chord panels share axial, shear and bending actions 50/50 for member checks and chord axial effect in K-joints")
            else:
                lines.append("Doubled chord zones: none required by single-chord strength checks")
            lines.append(
                f"Doubled panels are triggered where chord-member or chord-local web-joint utilisation exceeds "
                f"{DOUBLED_CHORD_TRIGGER_RATIO:.4f}; web member and weld failures do not trigger chord doubling"
            )
        if canopy_rafter_groups:
            lines.append("Canopy/lean-to rafter envelopes use the full rafter length")
        lines.append("Canopy/lean-to truss chord envelopes ignore end panels where an internal panel exists")
        lines.append("Chord bending demand: length-weighted average of chord member moments in the design zone")
        for group, title in [
            ("TOP", "Top chord"),
            ("BOTTOM", "Bottom chord"),
            ("LEFT_CANOPY_TOP", "Left canopy/lean-to top chord"),
            ("LEFT_CANOPY_BOTTOM", "Left canopy/lean-to bottom chord"),
            ("RIGHT_CANOPY_TOP", "Right canopy/lean-to top chord"),
            ("RIGHT_CANOPY_BOTTOM", "Right canopy/lean-to bottom chord"),
        ]:
            if chord_envelopes[group]["compression_kn"]["value"] < 0.0 and chord_envelopes[group]["moment_knm"]["value"] < 0.0:
                continue
            lines.append("")
            lines.append(title)
            lines.append(_action_line("Compression", chord_envelopes[group]["compression_kn"], "kN"))
            lines.append(_action_line("Tension", chord_envelopes[group]["tension_kn"], "kN"))
            lines.append(_action_line("Shear", chord_envelopes[group]["shear_kn"], "kN"))
            lines.append(_action_line("Bending", chord_envelopes[group]["moment_knm"], "kNm"))

        if column_envelopes["WEB"]["compression_kn"]["value"] >= 0.0:
            lines.append("")
            lines.append("WEBS / POSTS - ULS ENVELOPE")
            lines.append(_action_line("Compression", column_envelopes["WEB"]["compression_kn"], "kN"))
            lines.append(_action_line("Tension", column_envelopes["WEB"]["tension_kn"], "kN"))
            lines.append(_action_line("Shear", column_envelopes["WEB"]["shear_kn"], "kN"))
            lines.append(_action_line("Bending", column_envelopes["WEB"]["moment_knm"], "kNm"))
        envelope_actions = {
            "COLUMN": column_envelope,
            "LEFT_COLUMN": column_envelopes["LEFT_COLUMN"],
            "RIGHT_COLUMN": column_envelopes["RIGHT_COLUMN"],
            "INTERNAL_COLUMN": column_envelopes["INTERNAL_COLUMN"],
            "LEFT_LEAN_COLUMN": column_envelopes["LEFT_LEAN_COLUMN"],
            "RIGHT_LEAN_COLUMN": column_envelopes["RIGHT_LEAN_COLUMN"],
            "WEB": column_envelopes["WEB"],
            "LEFT_CANOPY_WEB": column_envelopes["LEFT_CANOPY_WEB"],
            "RIGHT_CANOPY_WEB": column_envelopes["RIGHT_CANOPY_WEB"],
            "TOP": chord_envelopes["TOP"],
            "BOTTOM": chord_envelopes["BOTTOM"],
            "LEFT_CANOPY_TOP": chord_envelopes["LEFT_CANOPY_TOP"],
            "LEFT_CANOPY_BOTTOM": chord_envelopes["LEFT_CANOPY_BOTTOM"],
            "RIGHT_CANOPY_TOP": chord_envelopes["RIGHT_CANOPY_TOP"],
            "RIGHT_CANOPY_BOTTOM": chord_envelopes["RIGHT_CANOPY_BOTTOM"],
            "single_chord_envelopes": single_chord_envelopes,
            "sls_column_top_deflection": sls_column_top_deflection,
            "sls_column_top_deflections": sls_column_top_deflections,
            "sls_crane_rail_deflection": sls_crane_rail_deflection,
            "sls_crane_gauge_change": sls_crane_gauge_change,
            "sls_column_midspan_ws_deflection": sls_column_midspan_ws_deflection,
            "sls_column_midspan_ws_deflections": sls_column_midspan_ws_deflections,
            "sls_truss_midspan_deflections": sls_truss_midspan_deflections,
            "sls_side_midspan_deflections": sls_side_midspan_deflections,
            "sls_canopy_tip_deflections": sls_canopy_tip_deflections,
            "web_member_demands": web_member_demands,
            "doubled_chord_zones": sorted(
                doubled_chord_zones.values(),
                key=lambda zone: (zone["group"], min(zone["x1"], zone["x2"]), zone["member"]),
            ),
            "uls_count": uls_count,
            "sls_count": sls_count,
            "foundation_reactions": [
                {key: value for key, value in item.items() if key != "node"}
                for item in foundation_reactions
            ],
        }
        for web_group in ["WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"]:
            demands = list((web_member_demands.get(web_group) or {}).values())
            if not demands:
                continue
            governing_connection_demand = max(
                demands,
                key=lambda demand: max(
                    demand.get("compression_kn", 0.0),
                    demand.get("tension_kn", 0.0),
                ),
            )
            envelope_actions[web_group]["connection"] = governing_connection_demand.get("connection", {})
        for group, basis in flybrace_bases.items():
            envelope_actions[group]['column_flybrace_basis'] = {**basis, 'cases': column_cases[group]}
        if capture_computations:
            envelope_actions["computation_cases"] = computation_cases
        return lines, envelope_actions

    def member_envelope_has_demands(self, envelope):
        if not envelope:
            return False
        return any(
            isinstance(item, dict) and item.get("value", -1.0) >= 0.0
            for item in envelope.values()
        )

    def member_envelope_value(self, envelope, key):
        return max(envelope[key]["value"], 0.0)

    def design_utilization_ok(self, ratio):
        return ratio <= DESIGN_UTILIZATION_PASS_LIMIT

    def doubled_chord_length_fraction(self, group):
        chord_sets = {
            "TOP": ("TOP", "BOTTOM"),
            "BOTTOM": ("TOP", "BOTTOM"),
            "LEFT_CANOPY_TOP": ("LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM"),
            "LEFT_CANOPY_BOTTOM": ("LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM"),
            "RIGHT_CANOPY_TOP": ("RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM"),
            "RIGHT_CANOPY_BOTTOM": ("RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM"),
        }
        measured_groups = chord_sets.get(group, (group,))
        total_length = sum(
            element.length() for element in getattr(self.structure, "elements", [])
            if element.group in measured_groups
        )
        if total_length <= 1e-9:
            return 0.0
        doubled_length = sum(
            float(zone.get("length_mm") or 0.0)
            for zone in (self.design_envelope_actions or {}).get("doubled_chord_zones", [])
            if zone.get("group") in measured_groups
        )
        return doubled_length / total_length

    def member_envelope_effective_length(self, envelope, key, fallback=0.0):
        return envelope[key].get("length_mm") or fallback

    def column_flybrace_evaluation(self, profile, envelope, group):
        basis = envelope.get('column_flybrace_basis') if isinstance(envelope, dict) else None
        if not basis or not basis.get('cases'):
            return None
        from portalcalc.frame.column_flybracing import FlybraceColumn
        cache = getattr(self, '_column_flybrace_models', None)
        if cache is None:
            cache = self._column_flybrace_models = {}
        key = id(envelope)
        if key not in cache:
            # Retain the envelope to prevent identity reuse during sizing trials.
            cache[key] = (envelope, FlybraceColumn(basis))
        return cache[key][1].choose(profile.check_properties())

    def column_flybrace_schedule(self):
        rows = []
        for _, combo, group in self.active_auto_size_group_specs():
            envelope = (self.design_envelope_actions or {}).get(group, {})
            profile = self.selected_profile(combo)
            evaluated = self.column_flybrace_evaluation(profile, envelope, group)
            if evaluated is not None:
                basis = envelope['column_flybrace_basis']
                rows.append({'group': group, 'profile': profile.name, 'x_mm': basis['x_mm'],
                             'base_mm': basis['base_mm'], 'roof_node_mm': basis['roof_node_mm'],
                             **copy.deepcopy(evaluated[2])})
        return rows

    def member_actions_from_envelope(self, envelope, group, restraint_override=None):
        if not restraint_override and isinstance(envelope, dict) and envelope.get('column_flybrace_basis'):
            combos = {'LEFT_COLUMN': 'left_column_profile_combo', 'RIGHT_COLUMN': 'right_column_profile_combo',
                      'LEFT_LEAN_COLUMN': 'left_lean_column_profile_combo', 'RIGHT_LEAN_COLUMN': 'right_lean_column_profile_combo'}
            combo = getattr(self, combos.get(group, ''), None)
            if combo is not None:
                evaluated = self.column_flybrace_evaluation(self.selected_profile(combo), envelope, group)
                if evaluated is not None:
                    return evaluated[1]
        restraint = dict((self.member_check_restraints or {}).get(group, {}))
        if restraint_override:
            restraint.update(restraint_override)
        major_length = restraint.get("major_mm") or self.member_envelope_effective_length(envelope, "compression_kn")
        minor_length = restraint.get("minor_mm") or major_length
        if major_length <= 0.0:
            lengths = [item.get("length_mm", 0.0) for item in envelope.values() if isinstance(item, dict)]
            major_length = max(lengths or [0.0])
        if minor_length <= 0.0:
            minor_length = major_length
        ke = restraint.get("ke", 1.0)
        moment_knm = self.member_envelope_value(envelope, "moment_knm")
        if group in {"TOP", "BOTTOM", "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM", "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM"}:
            moment_knm = max(envelope["compression_kn"].get("coincident_moment_knm", 0.0), 0.0)
        return MemberActions(
            compression_kn=self.member_envelope_value(envelope, "compression_kn"),
            tension_kn=self.member_envelope_value(envelope, "tension_kn"),
            shear_kn=self.member_envelope_value(envelope, "shear_kn"),
            moment_knm=moment_knm,
            effective_length_y_mm=(ke * minor_length) or None,
            effective_length_z_mm=(ke * major_length) or None,
            moment_modifier=restraint.get("am", 1.0),
            kt=restraint.get("kt", 1.0),
            kl=restraint.get("kl", 1.0),
            kr=restraint.get("kr", 1.0),
        )

    def resolved_member_check(self, profile, envelope, group):
        base_restraint = dict((self.member_check_restraints or {}).get(group, {}))
        evaluated = self.column_flybrace_evaluation(profile, envelope, group)
        if evaluated is not None:
            result, actions, plan = evaluated
            restraint = {**base_restraint, 'major_mm': plan['in_plane_length_mm'],
                         'minor_mm': actions.effective_length_y_mm, 'ke': 1.0,
                         'am': actions.moment_modifier, 'flybraces': plan}
            positions = ', '.join(f'{y:.0f}' for y in plan['heights_above_base_mm']) or 'none'
            return result, actions, restraint, [
                f'Automatic girt flybraces: {positions} mm above base; calculated am by segment and ULS case.',
                'Flybraces provide assumed weak-axis lateral/twist restraint; no in-plane support is added.',
            ] + [f"Segment {row['from_mm']:.0f}-{row['to_mm']:.0f} mm | LTB {row['actions']['ltb_length_mm']:.0f} mm | "
                 f"am {row['actions']['moment_modifier']:.3f} | ratio {row['ratio']:.3f} | {row['combo']}"
                 for row in plan['segments']]
        base_actions = self.member_actions_from_envelope(envelope, group)
        base_result = self.member_check_result(profile, envelope, group)
        if group == "BOTTOM" and not self.design_utilization_ok(base_result.governing_ratio):
            top_minor = ((self.member_check_restraints or {}).get("TOP", {}) or {}).get("minor_mm")
            bottom_minor = base_restraint.get("minor_mm")
            if top_minor and bottom_minor and top_minor < bottom_minor:
                retry_restraint = {**base_restraint, "minor_mm": top_minor}
                retry_result = self.member_check_result(profile, envelope, group, retry_restraint)
                if self.design_utilization_ok(retry_result.governing_ratio):
                    retry_actions = self.member_actions_from_envelope(envelope, group, retry_restraint)
                    return retry_result, retry_actions, retry_restraint, [
                        "Additional flybracing required: default every-second-purlin restraint failed; provide flybracing at every purlin over the checked bottom-chord region."
                    ]
        return base_result, base_actions, base_restraint, []

    def member_check_result(self, profile, envelope, group, restraint_override=None):
        if not restraint_override:
            evaluated = self.column_flybrace_evaluation(profile, envelope, group)
            if evaluated is not None:
                return evaluated[0]
        return check_member(profile.check_properties(), self.member_actions_from_envelope(envelope, group, restraint_override))

    def doubled_chord_zone_ratio(self, profile, envelope, group):
        if group not in {
            "TOP", "BOTTOM",
            "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM",
            "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM",
        }:
            return 0.0, ""
        zone = envelope.get("doubled_chord_zone") if isinstance(envelope, dict) else None
        if not zone:
            return 0.0, ""
        doubled_envelope = zone.get("doubled_envelope")
        if doubled_envelope:
            result = self.member_check_result(profile, doubled_envelope, group)
            return result.governing_ratio, "Doubled chord zone"
        current_combo = self.chord_group_combo(group)
        current_profile = self.selected_profile(current_combo) if current_combo is not None else None
        if current_profile is not None and getattr(profile, "name", None) != current_profile.name:
            return 0.0, ""
        return float(zone.get("doubled_ratio", 0.0)), "Doubled chord zone"
