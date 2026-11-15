"""Auto-size building blocks: candidate walks, exact neighbour checks and repair passes."""

import copy
import math
import re

from portalcalc.crane_beam import CRANE_GAUGE_CHANGE_LIMIT_MM
from portalcalc.frame.common import (
    ADDITION_TRUSS_DEPTH_MAX_RATIO,
    MAX_DOUBLED_CHORD_LENGTH_FRACTION,
    mm_from_metres_text,
    plain_copy,
)


class AutoSizeMixin:
    """Section search helpers used by the optimiser."""

    def auto_size_is_truss_chord_group(self, group):
        return group in {
            "TOP", "BOTTOM",
            "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM",
            "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM",
        } and self.frame_system_combo.currentText() == "Truss"

    def auto_size_profile_metric(self, profile, group):
        if profile is None:
            return 0.0
        if self.auto_size_is_truss_chord_group(group):
            inertias = [value for value in (profile.Iyp, profile.Izp) if value > 0.0]
            if inertias:
                return min(inertias)
        return profile.I_analysis if profile.I_analysis > 0.0 else profile.A

    def auto_size_profile_order_key(self, profile, group):
        if profile is None:
            return (0.0, float("inf"), float("inf"), "")
        return (
            self.auto_size_profile_metric(profile, group),
            self.auto_size_search_cost_key(profile, group),
            profile.A,
            profile.name,
        )

    def auto_size_profile_cost_key(self, profile):
        bucket = self.section_cost_bucket(profile)
        default_key = "shs_rate_per_kg" if bucket == "shs" else "ub_rate_per_kg"
        rate = self.costing_rate_from_input(
            self.shs_cost_input if bucket == "shs" else self.ub_cost_input,
            default_key,
        )
        kg_per_m = profile.A * 1e-6 * 7850.0
        return kg_per_m * rate

    def auto_size_search_cost_key(self, profile, group):
        cost = self.auto_size_profile_cost_key(profile)
        if self.auto_size_is_truss_chord_group(group) and self.profile_family_name(profile) not in {"SHS", "RHS", "CHS"}:
            return cost * 1.75
        return cost

    def auto_size_profile_allowed_for_group(self, profile, group):
        if profile is None:
            return False
        family = self.profile_family_name(profile)
        if group in {"LEFT_LEAN_COLUMN", "RIGHT_LEAN_COLUMN"}:
            return family in {"UB", "UC", "PFC"}
        return True

    def auto_size_current_selection_allowed(self, active_specs):
        return all(
            self.auto_size_profile_allowed_for_group(self.selected_profile(combo), group)
            for _, combo, group in active_specs
        )

    def auto_size_design_acceptable(self, active_specs, ratio):
        return bool(active_specs) and self.design_utilization_ok(ratio) and self.auto_size_current_selection_allowed(active_specs)

    def auto_size_candidate_indices(self, combo, group, min_metric_step=0.18):
        profiles = []
        for index in range(combo.count()):
            profile = self.catalog.get(combo.itemText(index))
            if profile is None:
                continue
            if not self.auto_size_profile_allowed_for_group(profile, group):
                continue
            profiles.append((index, profile, self.auto_size_profile_metric(profile, group)))
        profiles = sorted(profiles, key=lambda item: self.auto_size_profile_order_key(item[1], group))
        if not profiles:
            return []

        current_index = combo.currentIndex()
        ladder = []
        last_metric = None
        last_depth = None
        for index, profile, metric in profiles:
            depth = profile.depth or self.profile_name_depth(profile.name)
            keep = index == current_index or not ladder
            if last_metric and metric >= last_metric * (1.0 + min_metric_step):
                keep = True
            if last_depth and depth > last_depth:
                keep = True
            if keep:
                ladder.append(index)
                last_metric = metric
                last_depth = depth

        current_profile = self.catalog.get(combo.itemText(current_index))
        if current_index not in ladder and self.auto_size_profile_allowed_for_group(current_profile, group):
            ladder.append(current_index)
        return sorted(set(ladder), key=lambda index: next(
            self.auto_size_profile_order_key(profile, group)
            for item_index, profile, _ in profiles if item_index == index
        ))

    def profile_name_depth(self, name):
        numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", str(name))]
        return numbers[0] if numbers else 0.0

    def auto_size_candidate_walk(self, combo, group, current_passes):
        ladder = self.auto_size_candidate_indices(combo, group)
        if not ladder:
            return []
        current_index = combo.currentIndex()
        if current_index not in ladder:
            current_profile = self.catalog.get(combo.itemText(current_index))
            if not self.auto_size_profile_allowed_for_group(current_profile, group):
                return ladder
            ladder.append(current_index)
            ladder = sorted(
                set(ladder),
                key=lambda index: self.auto_size_profile_order_key(self.catalog.get(combo.itemText(index)), group),
            )
        position = ladder.index(current_index)
        if current_passes:
            return list(reversed(ladder[:position]))
        return ladder[position + 1:]

    def auto_size_frozen_candidate(self, combo, specs, ratio_cache):
        """Return the cheapest section passing all attached frozen envelopes."""
        envelopes = self.design_envelope_actions or {}
        candidates = []
        for index in self.auto_size_candidate_indices(combo, specs[0][2], min_metric_step=0.0):
            profile = self.catalog.get(combo.itemText(index))
            if profile is not None:
                candidates.append((self.auto_size_search_cost_key(profile, specs[0][2]), profile.A, profile.name, index, profile))
        # Search cost is a heuristic ordering key, not the final costing result.
        # Exact solved passing states are still compared by the full model cost.
        for candidate_cost, candidate_area, candidate_name, index, profile in sorted(candidates):
            profile = self.catalog.get(combo.itemText(index))
            worst_ratio = 0.0
            for _, _, group in specs:
                envelope = envelopes.get(group)
                if not self.member_envelope_has_demands(envelope):
                    continue
                chord_profiles = self.web_connection_group_profiles(group) if group in {
                    "WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB",
                } else None
                if chord_profiles is not None and not self.web_profile_fits_chords(profile, group, chord_profiles=chord_profiles):
                    worst_ratio = float("inf")
                    break
                chord_names = tuple(item.name if item is not None else "-" for item in (chord_profiles or ()))
                cache_key = (group, profile.name, id(envelope), chord_names)
                if cache_key not in ratio_cache:
                    ratio_cache[cache_key] = self.member_and_connection_ratio(
                        profile,
                        envelope,
                        group,
                        chord_profiles=chord_profiles,
                    )[0]
                worst_ratio = max(worst_ratio, ratio_cache[cache_key])
                if group in {
                    "TOP", "BOTTOM", "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM",
                    "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM",
                } and self.doubled_chord_length_fraction(group) > MAX_DOUBLED_CHORD_LENGTH_FRACTION:
                    single_envelope = (envelopes.get("single_chord_envelopes") or {}).get(group)
                    if self.member_envelope_has_demands(single_envelope):
                        worst_ratio = max(worst_ratio, self.member_check_result(profile, single_envelope, group).governing_ratio)
                    worst_ratio = max(worst_ratio, self.auto_size_chord_joint_ratio(profile, group))
                if worst_ratio > 1.0:
                    break
            if worst_ratio <= 1.0:
                return candidate_cost, candidate_area, candidate_name, index
        return None

    def auto_size_chord_joint_ratio(self, chord_profile, chord_group):
        group_map = {
            "TOP": ("WEB", "top chord"),
            "BOTTOM": ("WEB", "bottom chord"),
            "LEFT_CANOPY_TOP": ("LEFT_CANOPY_WEB", "top chord"),
            "LEFT_CANOPY_BOTTOM": ("LEFT_CANOPY_WEB", "bottom chord"),
            "RIGHT_CANOPY_TOP": ("RIGHT_CANOPY_WEB", "top chord"),
            "RIGHT_CANOPY_BOTTOM": ("RIGHT_CANOPY_WEB", "bottom chord"),
        }
        mapped = group_map.get(chord_group)
        if mapped is None:
            return 0.0
        web_group, chord_label = mapped
        web_combo = self.web_group_combo(web_group)
        web_profile = self.selected_profile(web_combo) if web_combo is not None else None
        if web_profile is None:
            return 0.0
        top_profile, bottom_profile = self.web_connection_group_profiles(web_group)
        if chord_label == "top chord":
            top_profile = chord_profile
        else:
            bottom_profile = chord_profile
        ratios = []
        demands = ((self.design_envelope_actions or {}).get("web_member_demands", {}).get(web_group) or {}).values()
        single_assignments = {
            int(demand["member"]): web_profile.name
            for demand in demands if self.is_output_web_demand(demand)
        }
        demands = ((self.design_envelope_actions or {}).get("web_member_demands", {}).get(web_group) or {}).values()
        for demand in demands:
            envelope = self.web_design_envelope(demand, assignments=single_assignments)
            envelope["connection"] = plain_copy(envelope.get("connection", {}))
            connection_contexts = envelope["connection"] if isinstance(envelope["connection"], dict) else {}
            for context in connection_contexts.values():
                if isinstance(context, dict):
                    context["doubled_chord"] = False
            connection = self.web_connection_check(
                web_profile, envelope, web_group, chord_profiles=(top_profile, bottom_profile)
            )
            result = self.chord_side_connection_result(connection, chord_label)
            if result:
                ratios.append(result["governing"]["ratio"])
        return max(ratios, default=0.0)

    def web_assignment_chord_joint_ratio(self, chord_profile, chord_group):
        group_map = {
            "TOP": ("WEB", "top chord"),
            "BOTTOM": ("WEB", "bottom chord"),
            "LEFT_CANOPY_TOP": ("LEFT_CANOPY_WEB", "top chord"),
            "LEFT_CANOPY_BOTTOM": ("LEFT_CANOPY_WEB", "bottom chord"),
            "RIGHT_CANOPY_TOP": ("RIGHT_CANOPY_WEB", "top chord"),
            "RIGHT_CANOPY_BOTTOM": ("RIGHT_CANOPY_WEB", "bottom chord"),
        }
        mapped = group_map.get(chord_group)
        if mapped is None or chord_profile is None:
            return 0.0, ""
        web_group, chord_label = mapped
        assignments = (self.auto_web_profile_assignments or {}).get(web_group, {})
        demands = ((self.design_envelope_actions or {}).get("web_member_demands", {}) or {}).get(web_group, {})
        if not assignments or not demands:
            return 0.0, ""
        top_profile, bottom_profile = self.web_connection_group_profiles(web_group)
        if chord_label == "top chord":
            top_profile = chord_profile
        else:
            bottom_profile = chord_profile
        ratios = []
        for member_id, demand in demands.items():
            if not self.is_output_web_demand(demand):
                continue
            assigned_name = assignments.get(int(member_id)) or assignments.get(str(member_id)) or assignments.get(member_id)
            web_profile = self.catalog.get(assigned_name)
            if web_profile is None:
                continue
            envelope = self.web_design_envelope(demand, assignments=assignments)
            connection = self.web_connection_check(
                web_profile,
                envelope,
                web_group,
                chord_profiles=(top_profile, bottom_profile),
            )
            result = self.chord_side_connection_result(connection, chord_label)
            if result:
                ratios.append((result["governing"]["ratio"], f"Web split chord-joint {result['governing']['label']}"))
        return max(ratios, default=(0.0, ""), key=lambda item: item[0])

    def auto_size_exact_neighbour_candidate(
        self,
        active_specs,
        ratio_cache,
        max_frozen_ratio=1.35,
        rejected=None,
        min_estimated_saving=0.0,
    ):
        """Return one cheaper near-pass candidate worth an exact coupled trial."""
        envelopes = self.design_envelope_actions or {}
        rejected = rejected if rejected is not None else set()
        decisions = {}
        group_lengths = {}
        for element in getattr(self, "structure", None).elements if getattr(self, "structure", None) else []:
            group_lengths[element.group] = group_lengths.get(element.group, 0.0) + element.length() / 1000.0
        for spec in active_specs:
            decision = decisions.setdefault(id(spec[1]), {"combo": spec[1], "specs": []})
            decision["specs"].append(spec)

        shortlist = []
        for decision in decisions.values():
            combo = decision["combo"]
            current = self.selected_profile(combo)
            group = decision["specs"][0][2]
            current_cost = self.auto_size_search_cost_key(current, group)
            local = []
            for index in self.auto_size_candidate_indices(combo, group, min_metric_step=0.0):
                profile = self.catalog.get(combo.itemText(index))
                if profile is None or profile.name == current.name:
                    continue
                candidate_cost = self.auto_size_search_cost_key(profile, group)
                if candidate_cost >= current_cost - 1e-9:
                    continue
                move_key = ("section", id(combo), current.name, profile.name)
                if move_key in rejected:
                    continue
                worst_ratio = 0.0
                for _, _, group in decision["specs"]:
                    envelope = envelopes.get(group)
                    if not self.member_envelope_has_demands(envelope):
                        continue
                    chord_profiles = self.web_connection_group_profiles(group) if group in {
                        "WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB",
                    } else None
                    chord_names = tuple(item.name if item is not None else "-" for item in (chord_profiles or ()))
                    cache_key = ("neighbour", group, profile.name, id(envelope), chord_names)
                    if cache_key not in ratio_cache:
                        ratio_cache[cache_key] = self.member_and_connection_ratio(
                            profile, envelope, group, chord_profiles=chord_profiles
                        )[0]
                    worst_ratio = max(worst_ratio, ratio_cache[cache_key])
                if worst_ratio <= max_frozen_ratio:
                    total_length = sum(group_lengths.get(group, 0.0) for _, _, group in decision["specs"])
                    saving = max(current_cost - candidate_cost, 0.0) * max(total_length, 1.0)
                    if saving < min_estimated_saving:
                        continue
                    local.append((worst_ratio, -saving, profile.name, combo, index, current.name, saving, move_key))
            if local:
                # This is an economic refinement: within each member group,
                # promote the largest credible saving to the exact solver.
                # Ordering by the lowest frozen ratio first caused tiny, very
                # safe reductions to hide more valuable verified neighbours.
                shortlist.append(min(local, key=lambda item: (item[1], item[0], item[2])))
        if not shortlist:
            return None
        def failed_attempts_for_combo(item):
            combo_id = id(item[3])
            return sum(
                1 for move_key in rejected
                if len(move_key) > 1 and move_key[0] == "section" and move_key[1] == combo_id
            )

        # Explore the best economic move from each active section group before
        # spending another exact solve on the same group.  Without this
        # diversity, a long column catalogue can starve an immediately useful
        # chord reduction from the bounded neighbour budget.
        return min(
            shortlist,
            key=lambda item: (
                failed_attempts_for_combo(item),
                item[1],
                item[0],
                item[2],
            ),
        )

    def auto_size_depth_increment_from_ratio(self, current_depth, ratio, max_step=250.0):
        if ratio <= 1.0:
            return 50.0
        target_depth = current_depth * min(max(math.sqrt(ratio) * 1.03, 1.05), 1.50)
        return min(max(self.round_up_to_increment(target_depth - current_depth, 50.0), 50.0), max_step)

    def auto_size_state_signature(self, active_specs):
        seen = set()
        sections = []
        for _, combo, _ in active_specs:
            if id(combo) in seen:
                continue
            seen.add(id(combo))
            sections.append((id(combo), combo.currentText()))
        return (
            self.depth_input.text(),
            self.left_canopy_shallow_depth_input.text(),
            self.right_canopy_shallow_depth_input.text(),
            tuple(sections),
        )

    def auto_size_restore_state(self, state):
        self.depth_input.setText(state["depth"])
        if state.get("left_depth") is not None:
            self.left_canopy_shallow_depth_input.setText(state["left_depth"])
        if state.get("right_depth") is not None:
            self.right_canopy_shallow_depth_input.setText(state["right_depth"])
        for combo, index in state["combos"]:
            combo.setCurrentIndex(index)
        self.auto_web_profile_assignments = copy.deepcopy(state["web_assignments"])
        self.auto_web_assignment_basis = copy.deepcopy(state.get("web_assignment_basis", {}))
        self.auto_web_joint_doubler_sides = copy.deepcopy(state.get("web_joint_doubler_sides", {}))

    def auto_size_capture_state(self, active_specs, ratio=None, cost=None):
        unique_combos = []
        seen_combos = set()
        for _, combo, _ in active_specs:
            if id(combo) not in seen_combos:
                seen_combos.add(id(combo))
                unique_combos.append((combo, combo.currentIndex()))
        if ratio is None:
            ratio, _, _ = self.auto_size_overall_ratio(active_specs)
        if cost is None:
            cost = (self.costing_summary or {}).get("total_cost", float("inf"))
        return {
            "cost": cost,
            "ratio": ratio,
            "depth": self.depth_input.text(),
            "left_depth": self.left_canopy_shallow_depth_input.text(),
            "right_depth": self.right_canopy_shallow_depth_input.text(),
            "combos": unique_combos,
            "web_assignments": copy.deepcopy(self.auto_web_profile_assignments),
            "web_assignment_basis": copy.deepcopy(getattr(self, "auto_web_assignment_basis", {})),
            "web_joint_doubler_sides": copy.deepcopy(getattr(self, "auto_web_joint_doubler_sides", {})),
        }

    def auto_size_overall_ratio(self, active_specs):
        ratios = []
        envelopes = self.design_envelope_actions or {}
        for _, combo, group in active_specs:
            envelope = envelopes.get(group)
            if not self.member_envelope_has_demands(envelope):
                continue
            if group in {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}:
                split_ratio = self.web_assignment_overall_ratio(self.selected_profile(combo), group)
                if split_ratio is not None:
                    ratios.append(split_ratio)
                    continue
            ratio, check_label, _ = self.member_and_connection_ratio(self.selected_profile(combo), envelope, group)
            ratios.append((ratio, group, check_label))

        for group in {
            "TOP", "BOTTOM", "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM",
            "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM",
        }:
            fraction = self.doubled_chord_length_fraction(group)
            if fraction > 0.0:
                ratios.append((fraction / MAX_DOUBLED_CHORD_LENGTH_FRACTION, group, "Doubled chord length limit"))

        ratios.extend(self.auto_size_serviceability_ratios())
        return max(ratios, default=(0.0, "-", "-"), key=lambda item: item[0])

    def auto_size_serviceability_ratios(self):
        ratios = []
        envelopes = self.design_envelope_actions or {}
        height = self.serviceability_height_mm or 0.0
        if height > 0.0 and "sls_column_top_deflection" in envelopes:
            item = envelopes["sls_column_top_deflection"]
            height = item.get("height_mm") or height
            divisor = self.eave_deflection_divisor()
            limit = height / divisor
            demand = max(item.get("value", -1.0), 0.0)
            ratios.append((demand / limit if limit > 1e-12 else float("inf"), item.get("label", "Column deflection"), f"H/{divisor:.0f}"))
        # Crane rail racking is not a 2D acceptance check: the frame alone has no
        # help from its neighbours, which share a crane load through the roof
        # bracing. It is reported (greyed) and belongs to the 3D model.
        gauge_item = envelopes.get("sls_crane_gauge_change") or {}
        if gauge_item.get("value", -1.0) >= 0.0:
            limit = gauge_item.get("limit_mm") or CRANE_GAUGE_CHANGE_LIMIT_MM
            ratios.append((gauge_item["value"] / limit, gauge_item.get("label", "Crane rail gauge change"), f"{limit:.0f} mm"))
        if "sls_column_midspan_ws_deflection" in envelopes:
            item = envelopes["sls_column_midspan_ws_deflection"]
            if item.get("value", -1.0) >= 0.0:
                divisor = self.column_deflection_divisor()
                limit = (item.get("height_mm") or height) / divisor if (item.get("height_mm") or height) > 0.0 else 0.0
                demand = max(item["value"], 0.0)
                ratios.append((demand / limit if limit > 1e-12 else float("inf"), "Column midspan Ws deflection", f"H/{divisor:.0f}"))

        span = self.serviceability_span_mm or 0.0
        if span > 0.0 and "sls_truss_midspan_deflections" in envelopes:
            ratios.extend(self.truss_midspan_deflection_ratios())
        ratios.extend(self.side_midspan_deflection_ratios())
        if "sls_canopy_tip_deflections" in envelopes:
            divisor = self.canopy_tip_deflection_divisor()
            for item in envelopes["sls_canopy_tip_deflections"].values():
                length = item.get("length_mm") or 0.0
                if length <= 0.0:
                    continue
                limit = length / divisor
                demand = max(item.get("value", -1.0), 0.0)
                ratios.append((demand / limit if limit > 1e-12 else float("inf"), item.get("label", "Canopy tip deflection"), f"L/{divisor:.0f}"))

        return ratios

    def auto_size_serviceability_ratio(self):
        return max(self.auto_size_serviceability_ratios(), default=(0.0, "-", "-"), key=lambda item: item[0])

    def truss_midspan_deflection_ratios(self):
        """Return stiffness-governing midspan ratios; permanent-load sag is advisory."""
        span = self.serviceability_span_mm or 0.0
        items = (self.design_envelope_actions or {}).get("sls_truss_midspan_deflections", {})
        if span <= 0.0 or not items:
            return []
        ratios = []
        divisors = self.midspan_component_deflection_divisors()
        for component in self.governing_midspan_deflection_components():
            divisor = divisors[component]
            demand = max(items.get(component, {}).get("value", -1.0), 0.0)
            limit = span / divisor
            ratios.append((demand / limit, f"Midspan {component} deflection", f"Span/{divisor:.0f}"))
        return ratios

    def side_midspan_deflection_ratios(self, side_filter=None):
        side_items = (self.design_envelope_actions or {}).get("sls_side_midspan_deflections", {})
        ratios = []
        for key, side_result in side_items.items():
            if side_filter and side_filter.lower() not in str(key).lower():
                continue
            span = side_result.get("length_mm") or 0.0
            components = side_result.get("components", {})
            if span <= 0.0 or not components:
                continue
            label = side_result.get("label", "Canopy/lean-to midspan")
            divisors = self.midspan_component_deflection_divisors()
            for component in self.governing_midspan_deflection_components():
                divisor = divisors[component]
                demand = max(components.get(component, {}).get("value", -1.0), 0.0)
                limit = span / divisor
                ratios.append((demand / limit if limit > 1e-12 else float("inf"), f"{label} {component} deflection", f"Span/{divisor:.0f}"))
        return ratios

    def addition_depth_bounds(self, side):
        """Return the valid 50 mm depth range for an active canopy/lean-to truss."""
        frame_combo = getattr(self, f"{side}_canopy_frame_system_combo")
        length_input = getattr(self, f"{side}_canopy_length_input")
        depth_input = getattr(self, f"{side}_canopy_shallow_depth_input")
        type_combo = getattr(self, f"{side}_canopy_type_combo")
        if frame_combo.currentText() != "Truss":
            return None
        try:
            length_mm = mm_from_metres_text(length_input.text())
            current_depth = float(depth_input.text())
            pitch = abs(float(getattr(self, f"{side}_canopy_pitch_input").text()))
        except ValueError:
            return None
        if length_mm <= 0.0:
            return None
        rise_mm = length_mm * math.tan(math.radians(pitch)) if type_combo.currentText() == "Canopy" else 0.0
        min_depth = self.round_up_to_increment(rise_mm + (300.0 if rise_mm else 0.03 * length_mm), 50.0)
        max_depth = self.round_up_to_increment(
            rise_mm + (max(350.0, ADDITION_TRUSS_DEPTH_MAX_RATIO * length_mm) if rise_mm else ADDITION_TRUSS_DEPTH_MAX_RATIO * length_mm),
            50.0,
        )
        max_depth = max(max_depth, min_depth, self.round_up_to_increment(current_depth, 50.0))
        return min_depth, max_depth

    def auto_size_repair_deflections(self):
        """Increase truss depths after final member selection until SLS deflections pass."""
        results = []
        if self.frame_system_combo.currentText() == "Truss":
            start_depth = float(self.depth_input.text())
            max_depth = self.max_main_truss_depth_mm()
            ratios = self.truss_midspan_deflection_ratios()
            governing = max(ratios, default=(0.0, "-", "-"), key=lambda item: item[0])
            while governing[0] > 1.0 and float(self.depth_input.text()) + 50.0 <= max_depth:
                current_depth = float(self.depth_input.text())
                next_depth = min(
                    current_depth + self.auto_size_depth_increment_from_ratio(current_depth, governing[0]),
                    max_depth,
                )
                self.depth_input.setText(f"{next_depth:.0f}")
                self.generate_and_solve()
                self.process_ui_events()
                governing = max(self.truss_midspan_deflection_ratios(), default=(0.0, "-", "-"), key=lambda item: item[0])
            end_depth = float(self.depth_input.text())
            if end_depth != start_depth or governing[0] > 1.0:
                results.append({
                    "label": "Main truss deflection repair", "start": start_depth, "end": end_depth,
                    "min": start_depth, "max": max_depth, "ratio": governing[0],
                    "governing_group": governing[1], "governing_check": governing[2],
                })

        envelopes = self.design_envelope_actions or {}
        for side in ("left", "right"):
            frame_combo = getattr(self, f"{side}_canopy_frame_system_combo")
            if frame_combo.currentText() != "Truss":
                continue
            depth_input = getattr(self, f"{side}_canopy_shallow_depth_input")
            length_input = getattr(self, f"{side}_canopy_length_input")
            pitch_input = getattr(self, f"{side}_canopy_pitch_input")
            type_combo = getattr(self, f"{side}_canopy_type_combo")
            try:
                length_mm = mm_from_metres_text(length_input.text())
                pitch = abs(float(pitch_input.text()))
                start_depth = float(depth_input.text())
            except ValueError:
                continue
            if length_mm <= 0.0:
                continue
            rise_mm = length_mm * math.tan(math.radians(pitch)) if type_combo.currentText() == "Canopy" else 0.0
            max_depth = self.round_up_to_increment(rise_mm + max(350.0, ADDITION_TRUSS_DEPTH_MAX_RATIO * length_mm), 50.0)

            def side_serviceability_ratio():
                current_envelopes = self.design_envelope_actions or {}
                values = []
                if type_combo.currentText() == "Lean-to":
                    values.extend(
                        (ratio, label)
                        for ratio, label, _ in self.side_midspan_deflection_ratios(side_filter=side)
                    )
                else:
                    for key, item in current_envelopes.get("sls_canopy_tip_deflections", {}).items():
                        if side not in str(key).lower() or item.get("length_mm", 0.0) <= 0.0:
                            continue
                        limit = item["length_mm"] / self.canopy_tip_deflection_divisor()
                        values.append((max(item.get("value", 0.0), 0.0) / limit, item.get("label", "Tip deflection")))
                return max(values, default=(0.0, "-"), key=lambda item: item[0])

            governing = side_serviceability_ratio()
            while governing[0] > 1.0 and float(depth_input.text()) + 50.0 <= max_depth:
                current_depth = float(depth_input.text())
                next_depth = min(
                    current_depth + self.auto_size_depth_increment_from_ratio(current_depth, governing[0]),
                    max_depth,
                )
                depth_input.setText(f"{next_depth:.0f}")
                self.generate_and_solve()
                self.process_ui_events()
                governing = side_serviceability_ratio()
            end_depth = float(depth_input.text())
            if end_depth != start_depth or governing[0] > 1.0:
                results.append({
                    "label": f"{side.title()} truss deflection repair", "start": start_depth, "end": end_depth,
                    "min": start_depth, "max": max_depth, "ratio": governing[0],
                    "governing_group": side.upper(), "governing_check": governing[1],
                })
        return results

    def auto_size_repair_serviceability(self, active_specs, ratio_cache, max_passes=3):
        """Run targeted exact-solve repairs for deflection-governed optimisation states."""
        report_lines = []
        depth_results = []
        exact_solves = 0
        for pass_index in range(max_passes):
            governing = self.auto_size_serviceability_ratio()
            if self.design_utilization_ok(governing[0]):
                break
            before_signature = self.auto_size_state_signature(active_specs)
            pass_lines = [
                f"Pass {pass_index + 1}: governing {governing[1]} {governing[2]} {governing[0]:.3f}"
            ]

            column_lines, column_solves = self.auto_size_repair_column_deflections(active_specs)
            exact_solves += column_solves
            pass_lines.extend(f"  {line}" for line in column_lines)

            repair_results = self.auto_size_repair_deflections()
            depth_results.extend(repair_results)
            exact_solves += sum(
                max(int(round((result["end"] - result["start"]) / 50.0)), 0)
                for result in repair_results
            )
            for result in repair_results:
                start_text = f"{result['start']:.0f}"
                end_text = f"{result['end']:.0f}"
                change_text = f"{start_text} -> {end_text}" if start_text != end_text else f"{end_text} (unchanged)"
                pass_lines.append(
                    f"  {result['label']}: {change_text} | governs "
                    f"{result['governing_group']} {result['governing_check']} {result['ratio']:.3f}"
                )

            after_governing = self.auto_size_serviceability_ratio()
            if self.design_utilization_ok(after_governing[0]):
                pass_lines.append(
                    f"  SLS verified: {after_governing[1]} {after_governing[2]} {after_governing[0]:.3f}"
                )
                report_lines.extend(pass_lines)
                break

            member_changes = self.auto_size_repair_failed_members(active_specs, ratio_cache)
            if member_changes:
                pass_lines.append(f"  Strength closure after SLS move: {len(member_changes)} member adjustment(s)")
                self.generate_and_solve()
                self.process_ui_events()
                exact_solves += 1

            after_signature = self.auto_size_state_signature(active_specs)
            after_governing = self.auto_size_serviceability_ratio()
            if after_signature == before_signature and not column_lines and not repair_results and not member_changes:
                pass_lines.append(
                    f"  No further targeted stiffness move available; closest SLS ratio {after_governing[0]:.3f}"
                )
                report_lines.extend(pass_lines)
                break

            pass_lines.append(
                f"  After pass: governing {after_governing[1]} {after_governing[2]} {after_governing[0]:.3f}"
            )
            report_lines.extend(pass_lines)
        return report_lines, depth_results, exact_solves

    def column_group_deflection_ratio(self, group):
        envelopes = self.design_envelope_actions or {}
        ratios = []
        if group in {"LEFT_COLUMN", "RIGHT_COLUMN"}:
            # Crane rail racking is report-only in 2D (see auto_size_serviceability_ratio).
            gauge_item = envelopes.get("sls_crane_gauge_change") or {}
            if gauge_item.get("value", -1.0) >= 0.0:
                limit = gauge_item.get("limit_mm") or CRANE_GAUGE_CHANGE_LIMIT_MM
                ratios.append((gauge_item["value"] / limit, "Crane rail gauge change", f"{limit:.0f} mm"))
        top_item = envelopes.get("sls_column_top_deflections", {}).get(group, {})
        top_height = top_item.get("height_mm") or 0.0
        if top_height > 0.0 and top_item.get("value", -1.0) >= 0.0:
            divisor = self.eave_deflection_divisor()
            ratios.append((top_item["value"] / (top_height / divisor), "Top deflection", f"H/{divisor:.0f}"))
        mid_item = envelopes.get("sls_column_midspan_ws_deflections", {}).get(group, {})
        mid_height = mid_item.get("height_mm") or 0.0
        if mid_height > 0.0 and mid_item.get("value", -1.0) >= 0.0:
            divisor = self.column_deflection_divisor()
            ratios.append((mid_item["value"] / (mid_height / divisor), "Midspan Ws deflection", f"H/{divisor:.0f}"))
        return max(ratios, default=(0.0, "-", "-"), key=lambda item: item[0])

    def auto_size_repair_column_pair(self, column_specs, max_trials=6):
        """Shared column sway: stiffen the two main columns together.

        When both columns fail sway, each is scaled by the shortfall: the
        cheapest section with I >= its current I x ratio x 1.05, then
        re-estimated from the exact result. Stiffening one column alone does
        not work: on PortalTest2 (stripped to 460UB67.1 / 410UB53.7, sway 1.955)
        the cheapest pair by combined I was 1200WB317 / 410UB53.7, still 1.341,
        and the old one-column repair went to 1000WB215 and then 1200WB423.
        Neither column gets lighter. Matched columns are one decision and use
        the single-column repair. Returns (report lines, exact solves).
        """
        by_group = {group: (label, combo) for label, combo, group in column_specs
                    if group in ("LEFT_COLUMN", "RIGHT_COLUMN")}
        if len(by_group) < 2 or by_group["LEFT_COLUMN"][1] is by_group["RIGHT_COLUMN"][1]:
            return [], 0
        ratios = {group: self.column_group_deflection_ratio(group)[0] for group in by_group}
        if min(ratios.values()) <= 1.0:
            return [], 0          # not a shared sway failure
        lengths = self.auto_size_group_lengths_m()
        options, original = {}, {}
        for group, (_, combo) in by_group.items():
            current = self.selected_profile(combo)
            if current is None:
                return [], 0
            original[group] = (combo.currentIndex(), current)
            envelope = (self.design_envelope_actions or {}).get(group)
            current_metric = self.auto_size_profile_metric(current, group)
            rows = []
            for index in self.auto_size_candidate_indices(combo, group, min_metric_step=0.0):
                profile = self.catalog.get(combo.itemText(index))
                if profile is None:
                    continue
                metric = self.auto_size_profile_metric(profile, group)
                if metric < current_metric * 0.999:
                    continue
                if index != combo.currentIndex() and self.member_and_connection_ratio(profile, envelope, group)[0] > 1.0:
                    continue
                rows.append((index, profile, metric,
                             self.auto_size_profile_cost_key(profile) * max(lengths.get(group, 1.0), 1e-6)))
            options[group] = rows
        if not options["LEFT_COLUMN"] or not options["RIGHT_COLUMN"]:
            return [], 0
        factor = max(max(ratios.values()) * 1.05, 1.05)
        basis = {g: self.auto_size_profile_metric(original[g][1], g) for g in by_group}
        tried, best, solves = set(), None, 0
        start_names = f"{original['LEFT_COLUMN'][1].name} / {original['RIGHT_COLUMN'][1].name}"
        for _ in range(max_trials):
            chosen = {}
            for group, rows in options.items():
                enough = [row for row in rows if row[2] >= basis[group] * factor]
                chosen[group] = (min(enough, key=lambda row: (row[3], row[2])) if enough
                                 else max(rows, key=lambda row: (row[2], -row[3])))
            left, right = chosen["LEFT_COLUMN"], chosen["RIGHT_COLUMN"]
            if (left[0], right[0]) in tried:
                break
            tried.add((left[0], right[0]))
            by_group["LEFT_COLUMN"][1].setCurrentIndex(left[0])
            by_group["RIGHT_COLUMN"][1].setCurrentIndex(right[0])
            self.generate_and_solve()
            self.process_ui_events()
            solves += 1
            ratio = max(self.column_group_deflection_ratio(g)[0] for g in by_group)
            strength = max(self.member_and_connection_ratio(
                self.selected_profile(by_group[g][1]), (self.design_envelope_actions or {}).get(g), g)[0] for g in by_group)
            if best is None or ratio < best[0]:
                best = (ratio, left, right)
            if ratio <= 1.0 and strength <= 1.0:
                return [f"{'Columns (pair)':<31}: {start_names} -> {left[1].name} / {right[1].name} | "
                        f"deflection {ratio:.3f} | strength {strength:.3f} | OK"], solves
            # Re-estimate from this exact result: both columns scale again.
            basis = {g: chosen[g][2] for g in by_group}
            factor = max(ratio * 1.05, 1.05)
        # Unresolved: keep the closest pair tried for the single-column repair to continue from.
        if best is not None:
            by_group["LEFT_COLUMN"][1].setCurrentIndex(best[1][0])
            by_group["RIGHT_COLUMN"][1].setCurrentIndex(best[2][0])
            self.generate_and_solve()
            solves += 1
            return [f"{'Columns (pair)':<31}: {start_names} -> {best[1][1].name} / {best[2][1].name} | "
                    f"closest deflection {best[0]:.3f} | NOT OK"], solves
        return [], solves

    def auto_size_repair_column_deflections(self, active_specs):
        column_specs = [
            spec for spec in active_specs
            if spec[2] in {"LEFT_COLUMN", "RIGHT_COLUMN", "INTERNAL_COLUMN", "LEFT_LEAN_COLUMN", "RIGHT_LEAN_COLUMN"}
        ]
        reports, exact_solves = self.auto_size_repair_column_pair(column_specs)
        unresolved_groups = set()
        for _ in range(max(len(column_specs) * 2, 1)):
            failing = [
                (self.column_group_deflection_ratio(group), label, combo, group)
                for label, combo, group in column_specs
                if group not in unresolved_groups and self.column_group_deflection_ratio(group)[0] > 1.0
            ]
            if not failing:
                break
            governing, label, combo, group = max(failing, key=lambda item: item[0][0])
            current_profile = self.selected_profile(combo)
            current_metric = self.auto_size_profile_metric(current_profile, group)
            original_index = combo.currentIndex()
            candidates = []
            for index in self.auto_size_candidate_indices(combo, group, min_metric_step=0.0):
                profile = self.catalog.get(combo.itemText(index))
                if profile is None or index == original_index:
                    continue
                if self.auto_size_profile_metric(profile, group) <= current_metric * 1.001:
                    continue
                envelope = (self.design_envelope_actions or {}).get(group)
                strength_ratio = self.member_and_connection_ratio(profile, envelope, group)[0]
                if strength_ratio <= 1.0:
                    candidates.append({
                        "cost": self.auto_size_profile_cost_key(profile),
                        "area": profile.A,
                        "name": profile.name,
                        "index": index,
                        "metric": self.auto_size_profile_metric(profile, group),
                    })
            adopted = None
            best_failure = (governing[0], original_index, current_profile.name, governing)
            required_metric = current_metric * max(governing[0] * 1.08, 1.10)
            tried_indices = set()
            previous_ratio = governing[0]
            previous_metric = current_metric
            starting_serviceability = self.auto_size_serviceability_ratio()
            best_adopted = None
            for _trial in range(8):
                eligible = [
                    item for item in candidates
                    if item["index"] not in tried_indices and item["metric"] >= required_metric
                ]
                if not eligible:
                    eligible = [item for item in candidates if item["index"] not in tried_indices]
                    if not eligible:
                        break
                    candidate = max(eligible, key=lambda item: (item["metric"], -item["cost"]))
                else:
                    candidate = min(eligible, key=lambda item: (item["cost"], item["metric"], item["area"], item["name"]))
                tried_indices.add(candidate["index"])
                combo.setCurrentIndex(candidate["index"])
                self.generate_and_solve()
                self.process_ui_events()
                exact_solves += 1
                deflection = self.column_group_deflection_ratio(group)
                envelope = (self.design_envelope_actions or {}).get(group)
                strength_ratio = self.member_and_connection_ratio(self.selected_profile(combo), envelope, group)[0]
                serviceability = self.auto_size_serviceability_ratio()
                if (
                    deflection[0] < best_failure[0]
                    or (
                        math.isclose(deflection[0], best_failure[0], rel_tol=1e-9, abs_tol=1e-9)
                        and serviceability[0] < best_failure[3][0]
                    )
                ):
                    best_failure = (deflection[0], candidate["index"], candidate["name"], deflection)
                if deflection[0] <= 1.0 and strength_ratio <= 1.0:
                    serviceability_improved = serviceability[0] < starting_serviceability[0] * 0.995
                    serviceability_passes = self.design_utilization_ok(serviceability[0])
                    group_was_global_governing = governing[0] >= starting_serviceability[0] * 0.995
                    if serviceability_passes or serviceability_improved or group_was_global_governing:
                        option = (serviceability[0], self.auto_size_profile_cost_key(self.selected_profile(combo)), candidate["name"], deflection, strength_ratio, candidate["index"])
                        if best_adopted is None or option < best_adopted:
                            best_adopted = option
                        if serviceability_passes:
                            break
                stiffness_gain = candidate["metric"] / max(previous_metric, 1e-12)
                improvement = (previous_ratio - deflection[0]) / max(previous_ratio, 1e-12)
                if best_adopted is None and stiffness_gain >= 1.25 and improvement < 0.02:
                    break
                required_metric = candidate["metric"] * max(deflection[0] * 1.05, 1.10)
                previous_ratio = deflection[0]
                previous_metric = candidate["metric"]
            if best_adopted is not None:
                _, _, adopted_name, adopted_deflection, adopted_strength, adopted_index = best_adopted
                if combo.currentIndex() != adopted_index:
                    combo.setCurrentIndex(adopted_index)
                    self.generate_and_solve()
                    self.process_ui_events()
                    exact_solves += 1
                adopted = (adopted_name, adopted_deflection, adopted_strength)
            if adopted is None:
                if combo.currentIndex() != best_failure[1]:
                    combo.setCurrentIndex(best_failure[1])
                    self.generate_and_solve()
                    self.process_ui_events()
                    exact_solves += 1
                reports.append(
                    f"{label:<31}: {current_profile.name} -> {best_failure[2]} | closest deflection "
                    f"{best_failure[3][0]:.3f} ({best_failure[3][1]}, {best_failure[3][2]}) | NOT OK"
                )
                unresolved_groups.add(group)
                continue
            reports.append(
                f"{label:<31}: {current_profile.name} -> {adopted[0]} | deflection "
                f"{adopted[1][0]:.3f} ({adopted[1][1]}, {adopted[1][2]}) | strength {adopted[2]:.3f} | OK"
            )
        return reports, exact_solves

    def auto_size_group_lengths_m(self):
        lengths = {}
        structure = getattr(self, "structure", None)
        if structure is None:
            return lengths
        for element in structure.elements:
            lengths[element.group] = lengths.get(element.group, 0.0) + element.length() / 1000.0
        return lengths

    def auto_size_coupled_web_chord_candidate(self, web_group, ratio_cache):
        chord_groups = self.web_related_chord_groups(web_group)
        if not chord_groups:
            return None
        web_combo = self.web_group_combo(web_group)
        chord_combos = [self.chord_group_combo(group) for group in chord_groups]
        if web_combo is None or any(combo is None for combo in chord_combos):
            return None
        unique_chord_combos = {id(combo): combo for combo in chord_combos}
        if len(unique_chord_combos) != 1:
            return None
        chord_combo = next(iter(unique_chord_combos.values()))
        envelopes = self.design_envelope_actions or {}
        if not self.member_envelope_has_demands(envelopes.get(web_group)):
            return None

        current_web = self.selected_profile(web_combo)
        current_chord = self.selected_profile(chord_combo)
        current_web_metric = self.auto_size_profile_metric(current_web, web_group)
        current_chord_metric = max(
            self.auto_size_profile_metric(current_chord, chord_group)
            for chord_group in chord_groups
        )

        web_candidates = []
        for index in self.auto_size_candidate_indices(web_combo, web_group, min_metric_step=0.0):
            profile = self.catalog.get(web_combo.itemText(index))
            if profile is None:
                continue
            if current_web is not None and self.auto_size_profile_metric(profile, web_group) < current_web_metric * 0.999:
                continue
            web_candidates.append((index, profile))

        chord_candidates = []
        first_chord_group = chord_groups[0]
        for index in self.auto_size_candidate_indices(chord_combo, first_chord_group, min_metric_step=0.0):
            profile = self.catalog.get(chord_combo.itemText(index))
            if profile is None:
                continue
            if current_chord is not None and self.auto_size_profile_metric(profile, first_chord_group) < current_chord_metric * 0.999:
                continue
            chord_candidates.append((index, profile))
        if not web_candidates or not chord_candidates:
            return None

        lengths = self.auto_size_group_lengths_m()
        web_length = max(lengths.get(web_group, 0.0), 1.0)
        chord_length = max(sum(lengths.get(group, 0.0) for group in chord_groups), 1.0)
        best = None

        for chord_index, chord_profile in chord_candidates:
            chord_worst = 0.0
            chord_check = "-"
            for chord_group in chord_groups:
                envelope = envelopes.get(chord_group)
                if not self.member_envelope_has_demands(envelope):
                    continue
                cache_key = ("coupled-chord", chord_group, chord_profile.name, id(envelope))
                if cache_key not in ratio_cache:
                    ratio_cache[cache_key] = self.member_and_connection_ratio(
                        chord_profile,
                        envelope,
                        chord_group,
                    )[:2]
                ratio, check_label = ratio_cache[cache_key]
                if ratio > chord_worst:
                    chord_worst = ratio
                    chord_check = check_label
            if chord_worst > 1.0:
                continue

            for web_index, web_profile in web_candidates:
                chord_profiles = (chord_profile, chord_profile)
                if not self.web_profile_fits_chords(web_profile, web_group, chord_profiles=chord_profiles):
                    continue
                web_envelope = envelopes.get(web_group)
                cache_key = (
                    "coupled-web",
                    web_group,
                    web_profile.name,
                    chord_profile.name,
                    id(web_envelope),
                )
                if cache_key not in ratio_cache:
                    ratio_cache[cache_key] = self.member_and_connection_ratio(
                        web_profile,
                        web_envelope,
                        web_group,
                        chord_profiles=chord_profiles,
                    )[:2]
                web_ratio, web_check = ratio_cache[cache_key]
                worst_ratio = max(chord_worst, web_ratio)
                if worst_ratio > 1.0:
                    continue
                total_cost = (
                    self.auto_size_profile_cost_key(web_profile) * web_length
                    + self.auto_size_profile_cost_key(chord_profile) * chord_length
                )
                option = (
                    total_cost,
                    worst_ratio,
                    chord_profile.name,
                    web_profile.name,
                    chord_index,
                    web_index,
                    chord_check if chord_worst >= web_ratio else web_check,
                )
                if best is None or option < best:
                    best = option
        return best

    def auto_size_repair_coupled_web_chords(self, active_specs, ratio_cache):
        active_groups = {group for _, _, group in active_specs}
        changes = []
        repaired_groups = set()
        for web_group in ("WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"):
            if web_group not in active_groups:
                continue
            candidate = self.auto_size_coupled_web_chord_candidate(web_group, ratio_cache)
            if candidate is None:
                continue
            _, frozen_ratio, chord_name, web_name, chord_index, web_index, governing_check = candidate
            web_combo = self.web_group_combo(web_group)
            chord_combo = self.chord_group_combo(self.web_related_chord_groups(web_group)[0])
            if web_combo is None or chord_combo is None:
                continue
            before_chord = chord_combo.currentText()
            before_web = web_combo.currentText()
            changed = False
            if chord_combo.currentIndex() != chord_index:
                chord_combo.setCurrentIndex(chord_index)
                changed = True
            if web_combo.currentIndex() != web_index:
                web_combo.setCurrentIndex(web_index)
                changed = True
            if changed:
                repaired_groups.add(web_group)
                repaired_groups.update(self.web_related_chord_groups(web_group))
                changes.append(
                    f"{web_group} coupled web/chord: chord {before_chord} -> {chord_name}, "
                    f"web {before_web} -> {web_name} | frozen {frozen_ratio:.3f} {governing_check}"
                )
        self._last_coupled_repair_groups = repaired_groups
        return changes

    def auto_size_repair_failed_members(self, active_specs, ratio_cache):
        decisions = {}
        for spec in active_specs:
            decisions.setdefault(id(spec[1]), {"combo": spec[1], "specs": []})["specs"].append(spec)
        forced_groups = set()
        web_chord_groups = {
            "WEB": ("TOP", "BOTTOM"),
            "LEFT_CANOPY_WEB": ("LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM"),
            "RIGHT_CANOPY_WEB": ("RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM"),
        }
        chord_web_groups = {
            chord_group: web_group
            for web_group, chord_groups in web_chord_groups.items()
            for chord_group in chord_groups
        }
        for _, combo, group in active_specs:
            if group not in web_chord_groups:
                continue
            envelope = (self.design_envelope_actions or {}).get(group)
            if not self.member_envelope_has_demands(envelope):
                continue
            ratio, check_label, _ = self.member_and_connection_ratio(self.selected_profile(combo), envelope, group)
            if ratio > 1.0 and "chord-web" in str(check_label).lower():
                forced_groups.update(web_chord_groups[group])
        for _, combo, group in active_specs:
            web_group = chord_web_groups.get(group)
            if web_group is None:
                continue
            envelope = (self.design_envelope_actions or {}).get(group)
            if not self.member_envelope_has_demands(envelope):
                continue
            zone = envelope.get("doubled_chord_zone") if isinstance(envelope, dict) else None
            if not (isinstance(zone, dict) and zone.get("web_joint_trigger")):
                continue
            doubled_ratio, _ = self.doubled_chord_zone_ratio(self.selected_profile(combo), envelope, group)
            if doubled_ratio > 1.0:
                forced_groups.add(web_group)
        changes = []
        for decision in decisions.values():
            failed = False
            for _, combo, group in decision["specs"]:
                if group in forced_groups:
                    failed = True
                    break
                envelope = (self.design_envelope_actions or {}).get(group)
                if not self.member_envelope_has_demands(envelope):
                    continue
                ratio = self.member_and_connection_ratio(self.selected_profile(combo), envelope, group)[0]
                if group in {
                    "TOP", "BOTTOM", "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM",
                    "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM",
                }:
                    ratio = max(ratio, self.doubled_chord_length_fraction(group) / MAX_DOUBLED_CHORD_LENGTH_FRACTION)
                if ratio > 1.0:
                    failed = True
                    break
            if not failed:
                continue
            candidate = self.auto_size_frozen_candidate(decision["combo"], decision["specs"], ratio_cache)
            cleared_split = False
            for _, _, group in decision["specs"]:
                if group in forced_groups and group in web_chord_groups:
                    if (self.auto_web_profile_assignments or {}).pop(group, None):
                        self.auto_web_assignment_basis.pop(group, None)
                        self.auto_web_joint_doubler_sides.pop(group, None)
                        cleared_split = True
            if candidate is not None and decision["combo"].currentIndex() != candidate[3]:
                before = decision["combo"].currentText()
                decision["combo"].setCurrentIndex(candidate[3])
                change = f"{before} -> {decision['combo'].currentText()}"
                if cleared_split:
                    change += " | cleared web split for joint-zone repair"
                changes.append(change)
            elif cleared_split:
                changes.append(f"{decision['combo'].currentText()} | cleared web split for joint-zone repair")
        return changes

    def _sync_matched_columns(self, *_args, source=None):
        """Matched columns: whichever column combo changed, the other follows."""
        if not self._matched_columns:
            return
        source = source if source is not None else self.left_column_profile_combo
        target = (self.right_column_profile_combo if source is self.left_column_profile_combo
                  else self.left_column_profile_combo)
        index = target.findText(source.currentText())
        if index >= 0 and index != target.currentIndex():
            target.setCurrentIndex(index)

    def columns_should_match(self):
        """True for a symmetric building, where Optimise keeps the two main columns the same.

        Symmetric: gable roof, same supports and column length adjustments,
        the same canopy/lean-to on each side (type, size, frame and loads), and
        internal columns mirrored about the mid-span. Wind, cladding and
        openings may differ: matching columns is a design choice for a building
        that is the same both sides.
        """
        data = self.collect_preset_data()
        geometry, canopies = data.get("geometry", {}), data.get("canopies", {})
        if geometry.get("roof_shape") != "Gable":
            return False
        for left, right in (("left_support", "right_support"),
                            ("left_column_length_adjust_mm", "right_column_length_adjust_mm")):
            if str(geometry.get(left, "")).strip() != str(geometry.get(right, "")).strip():
                return False
        if not (str(canopies.get("left_type", "None")) == str(canopies.get("right_type", "None")) == "None"):
            for key in ("type", "length_mm", "depth_mm", "eave_height_mm", "pitch_deg", "frame_system",
                        "g_kpa", "q_kpa", "solar_kpa"):
                if str(canopies.get(f"left_{key}", "")).strip() != str(canopies.get(f"right_{key}", "")).strip():
                    return False
        structure = getattr(self, "structure", None)
        if structure is not None:
            def xs(group):
                return [x for e in structure.elements if e.group == group for x in (e.start.x, e.end.x)]
            left, right = xs("LEFT_COLUMN"), xs("RIGHT_COLUMN")
            if left and right:
                centre = (min(left) + max(right)) / 2.0
                internal = sorted(set(xs("INTERNAL_COLUMN")))
                mirrored = sorted(2 * centre - x for x in internal)
                if any(abs(a - b) > 2.0 for a, b in zip(internal, mirrored)):
                    return False
        return True

    def auto_size_group_specs(self):
        # Matched columns: both column groups are one decision on the left combo
        # (as the top and bottom chords share one), which the right one follows.
        right_combo = self.left_column_profile_combo if self._matched_columns else self.right_column_profile_combo
        return [
            ("Left column", self.left_column_profile_combo, "LEFT_COLUMN"),
            ("Right column", right_combo, "RIGHT_COLUMN"),
            ("Internal column", self.internal_column_profile_combo, "INTERNAL_COLUMN"),
            ("Top chord", self.top_profile_combo, "TOP"),
            ("Bottom chord", self.bottom_profile_combo, "BOTTOM"),
            ("Web", self.web_profile_combo, "WEB"),
            ("Left canopy/lean-to top", self.left_canopy_top_profile_combo, "LEFT_CANOPY_TOP"),
            ("Left canopy/lean-to bottom", self.left_canopy_bottom_profile_combo, "LEFT_CANOPY_BOTTOM"),
            ("Left canopy/lean-to web", self.left_canopy_web_profile_combo, "LEFT_CANOPY_WEB"),
            ("Left lean-to outer column", self.left_lean_column_profile_combo, "LEFT_LEAN_COLUMN"),
            ("Right canopy/lean-to top", self.right_canopy_top_profile_combo, "RIGHT_CANOPY_TOP"),
            ("Right canopy/lean-to bottom", self.right_canopy_bottom_profile_combo, "RIGHT_CANOPY_BOTTOM"),
            ("Right canopy/lean-to web", self.right_canopy_web_profile_combo, "RIGHT_CANOPY_WEB"),
            ("Right lean-to outer column", self.right_lean_column_profile_combo, "RIGHT_LEAN_COLUMN"),
        ]

    def active_auto_size_group_specs(self):
        envelopes = self.design_envelope_actions or {}
        return [
            spec for spec in self.auto_size_group_specs()
            if self.member_envelope_has_demands(envelopes.get(spec[2]))
        ]
