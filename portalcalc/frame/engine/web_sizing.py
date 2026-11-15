"""Truss web sizing: demand envelopes, split web assignments and chord doublers."""

import copy

from portalcalc.frame.common import (
    DESIGN_UTILIZATION_PASS_LIMIT,
    DOUBLED_CHORD_TRIGGER_RATIO,
    plain_copy,
)


class WebSizingMixin:
    """Automatic web selection and joint doubler placement."""

    def web_group_combo(self, group):
        if group == "WEB":
            return self.web_profile_combo
        if group == "LEFT_CANOPY_WEB":
            return self.left_canopy_web_profile_combo
        if group == "RIGHT_CANOPY_WEB":
            return self.right_canopy_web_profile_combo
        return None

    def analysis_web_profile_for_group(self, group):
        name = (getattr(self, "analysis_web_placeholder_names", {}) or {}).get(group)
        return self.catalog.get(name) if name else None

    def web_member_demand_envelope(self, demand):
        def item(key):
            return {
                "value": max(demand.get(key, 0.0), 0.0),
                "combo": demand.get(key.replace("_kn", "_combo"), demand.get("combo", "-")),
                "member": demand.get("member", "-"),
                "group": "-",
                "length_mm": demand.get("length_mm", 0.0),
            }
        envelope = {
            "compression_kn": item("compression_kn"),
            "tension_kn": item("tension_kn"),
            "shear_kn": {"value": 0.0, "combo": demand.get("combo", "-"), "member": demand.get("member", "-"), "group": "-", "length_mm": demand.get("length_mm", 0.0)},
            "moment_knm": {"value": 0.0, "combo": demand.get("combo", "-"), "member": demand.get("member", "-"), "group": "-", "length_mm": demand.get("length_mm", 0.0)},
        }
        envelope["connection"] = demand.get("connection", {})
        envelope["connection_demand_kn"] = float(demand.get("connection_demand_kn") or demand.get("axial_kn") or 0.0)
        return envelope

    def web_profile_member_ratio(self, profile, group, demand):
        envelope = self.web_member_demand_envelope(demand)
        ratio, check_label, _ = self.member_and_connection_ratio(profile, envelope, group)
        return ratio, check_label

    def web_design_envelope(self, demand, assignments=None, joint_doubler_sides=None):
        """Return a design-only web envelope with the calculated peer-web size.

        The analysis model deliberately keeps fixed placeholder webs.  This copy
        supplies the selected web sizes to the K-joint checks without modifying
        analysis members or the force envelope.
        """
        envelope = self.web_member_demand_envelope(demand)
        envelope["connection"] = plain_copy(envelope.get("connection", {}))
        contexts = envelope["connection"] if isinstance(envelope["connection"], dict) else {}
        assignments = assignments or {}
        joint_doubler_sides = joint_doubler_sides or set()
        try:
            member_id = int(demand.get("member"))
        except (TypeError, ValueError):
            member_id = demand.get("member")
        for chord_label, context in contexts.items():
            if not isinstance(context, dict):
                continue
            k_joint = context.get("k_joint")
            if isinstance(k_joint, dict):
                peer_member = k_joint.get("peer_member")
                peer_name = (
                    assignments.get(peer_member)
                    or assignments.get(str(peer_member))
                    or assignments.get(int(peer_member) if str(peer_member).isdigit() else peer_member)
                )
                peer_profile = self.catalog.get(peer_name) if peer_name else None
                peer_dimensions = self.hollow_connection_dimensions(peer_profile)
                if peer_dimensions is not None:
                    k_joint["peer_width_mm"] = peer_dimensions["width"]
            if (member_id, chord_label) in joint_doubler_sides and not context.get("doubled_chord"):
                context["doubled_chord"] = True
                context["chord_axial_ratio"] = 0.5 * float(context.get("chord_axial_ratio") or 0.0)
        return envelope

    def required_web_joint_doubler_sides(self, group, demands=None, candidates=None):
        """Return only chord sides that no legal web profile can make pass."""
        demands = list(demands or [
            demand for demand in (
                ((self.design_envelope_actions or {}).get("web_member_demands", {}) or {}).get(group, {}) or {}
            ).values()
            if self.is_output_web_demand(demand)
        ])
        chord_profiles = self.web_connection_group_profiles(group)
        combo = self.web_group_combo(group)
        if candidates is None:
            candidates = []
            if combo is not None:
                for index in self.auto_size_candidate_indices(combo, group, min_metric_step=0.0):
                    profile = self.catalog.get(combo.itemText(index))
                    if profile is not None and self.web_profile_fits_chords(
                        profile, group, chord_profiles=chord_profiles
                    ):
                        candidates.append(profile)
        candidates = list(candidates or [])
        if not demands or not candidates:
            return set()

        def profile_width(profile):
            dimensions = self.hollow_connection_dimensions(profile)
            return float(dimensions["width"]) if dimensions is not None else 0.0

        widest = max(candidates, key=profile_width)
        generous_peer_assignments = {int(demand["member"]): widest.name for demand in demands}
        chord_modes = {"K face plast", "Chord wall shear", "Chord punching", "Chord face yield"}
        chord_profile_lookup = {
            "top chord": chord_profiles[0],
            "bottom chord": chord_profiles[1] or chord_profiles[0],
        }
        required = set()
        for demand in demands:
            try:
                member_id = int(demand.get("member"))
            except (TypeError, ValueError):
                continue
            envelope = self.web_design_envelope(demand, assignments=generous_peer_assignments)
            contexts = envelope.get("connection", {}) if isinstance(envelope, dict) else {}
            demand_kn = float(demand.get("connection_demand_kn") or demand.get("axial_kn") or 0.0)
            for chord_label, chord_profile in chord_profile_lookup.items():
                context = contexts.get(chord_label)
                if chord_profile is None or not isinstance(context, dict) or context.get("doubled_chord"):
                    continue
                best_ratio = float("inf")
                for candidate in candidates:
                    checks = [
                        check for check in self.welded_chord_web_connection_checks(
                            candidate, chord_profile, demand_kn, context
                        ) if check.get("label") in chord_modes
                    ]
                    if checks:
                        best_ratio = min(best_ratio, max(check["ratio"] for check in checks))
                if best_ratio > DOUBLED_CHORD_TRIGGER_RATIO:
                    required.add((member_id, chord_label))
        return required

    def clear_auto_web_joint_doublers(self):
        """Remove only postprocessed web-joint doublers before a fresh web design."""
        envelopes = self.design_envelope_actions or {}
        envelopes["doubled_chord_zones"] = [
            zone for zone in envelopes.get("doubled_chord_zones", [])
            if not (isinstance(zone, dict) and zone.get("auto_web_joint_doubler"))
        ]
        for group_demands in (envelopes.get("web_member_demands", {}) or {}).values():
            for demand in (group_demands or {}).values():
                for context in (demand.get("connection", {}) or {}).values():
                    if not isinstance(context, dict) or not context.pop("auto_web_joint_doubler", False):
                        continue
                    context["doubled_chord"] = False
                    context["chord_axial_ratio"] = float(
                        context.pop("single_chord_axial_ratio", context.get("chord_axial_ratio") or 0.0)
                    )

    def expand_doubled_chord_zones_to_web_joints(self, structure, zones):
        """Extend chord zones to fabrication-valid boundaries at web nodes."""
        if not zones:
            return zones
        web_groups_by_chord = {
            "TOP": {"WEB", "POST", "APEX"},
            "BOTTOM": {"WEB", "POST", "APEX"},
            "LEFT_CANOPY_TOP": {"LEFT_CANOPY_WEB", "LEFT_CANOPY_POST"},
            "LEFT_CANOPY_BOTTOM": {"LEFT_CANOPY_WEB", "LEFT_CANOPY_POST"},
            "RIGHT_CANOPY_TOP": {"RIGHT_CANOPY_WEB", "RIGHT_CANOPY_POST"},
            "RIGHT_CANOPY_BOTTOM": {"RIGHT_CANOPY_WEB", "RIGHT_CANOPY_POST"},
        }

        def node_key(node):
            return round(node.x, 6), round(node.y, 6)

        def low_node(element):
            return min((element.start, element.end), key=lambda node: (node.x, node.y))

        def high_node(element):
            return max((element.start, element.end), key=lambda node: (node.x, node.y))

        expanded = dict(zones)
        chord_groups = sorted({
            zone.get("group") for zone in zones.values()
            if zone.get("group") in web_groups_by_chord
        })
        for group in chord_groups:
            web_joint_nodes = {
                node_key(node)
                for element in structure.elements
                if element.group in web_groups_by_chord[group]
                for node in (element.start, element.end)
            }
            if not web_joint_nodes:
                continue
            group_elements = [element for element in structure.elements if element.group == group]
            group_elements.sort(key=lambda element: (low_node(element).x, low_node(element).y, element.id))
            index_by_id = {element.id: index for index, element in enumerate(group_elements)}
            selected_ids = [
                int(zone["member"])
                for zone in zones.values()
                if zone.get("group") == group and str(zone.get("member", "")).isdigit()
            ]
            for selected_id in selected_ids:
                if selected_id not in index_by_id:
                    continue
                source_zone = zones.get((group, selected_id), {})
                left_index = index_by_id[selected_id]
                right_index = left_index
                while left_index > 0 and node_key(low_node(group_elements[left_index])) not in web_joint_nodes:
                    previous = group_elements[left_index - 1]
                    if node_key(high_node(previous)) != node_key(low_node(group_elements[left_index])):
                        break
                    left_index -= 1
                while right_index < len(group_elements) - 1 and node_key(high_node(group_elements[right_index])) not in web_joint_nodes:
                    following = group_elements[right_index + 1]
                    if node_key(low_node(following)) != node_key(high_node(group_elements[right_index])):
                        break
                    right_index += 1
                for chord_element in group_elements[left_index:right_index + 1]:
                    base_zone = expanded.get((group, chord_element.id), source_zone)
                    new_zone = copy.deepcopy(base_zone)
                    new_zone.update({
                        "group": group,
                        "member": chord_element.id,
                        "x1": chord_element.start.x,
                        "y1": chord_element.start.y,
                        "x2": chord_element.end.x,
                        "y2": chord_element.end.y,
                        "length_mm": chord_element.length(),
                    })
                    if chord_element.id != selected_id:
                        new_zone["web_node_span_expanded"] = True
                        new_zone.setdefault("span_source_member", selected_id)
                    current = expanded.get((group, chord_element.id))
                    if current is None or new_zone.get("single_ratio", 0.0) >= current.get("single_ratio", 0.0):
                        expanded[(group, chord_element.id)] = new_zone
        return expanded

    def apply_auto_web_joint_doublers(self, group, assignments, required_sides=None):
        """Create design-only chord zones for selected web joints that need them."""
        envelopes = self.design_envelope_actions or {}
        demands = ((envelopes.get("web_member_demands", {}) or {}).get(group) or {})
        top_profile, bottom_profile = self.web_connection_group_profiles(group)
        chord_profiles = (("top chord", top_profile), ("bottom chord", bottom_profile or top_profile))
        chord_modes = {"K face plast", "Chord wall shear", "Chord punching", "Chord face yield"}
        elements_by_id = {element.id: element for element in getattr(self.structure, "elements", [])}
        preexisting_keys = {
            (zone.get("group"), int(zone.get("member")))
            for zone in envelopes.get("doubled_chord_zones", [])
            if isinstance(zone, dict) and str(zone.get("member", "")).isdigit()
        }
        existing_keys = set(preexisting_keys)
        added_zones = []
        if required_sides is None:
            required_sides = self.required_web_joint_doubler_sides(group)
        required_sides = set(required_sides or ())
        for member_id, demand in demands.items():
            if not self.is_output_web_demand(demand):
                continue
            assigned_name = (
                assignments.get(member_id)
                or assignments.get(str(member_id))
                or assignments.get(int(member_id) if str(member_id).isdigit() else member_id)
            )
            web_profile = self.catalog.get(assigned_name) if assigned_name else None
            if web_profile is None:
                continue
            design_envelope = self.web_design_envelope(demand, assignments=assignments)
            demand_kn = float(demand.get("connection_demand_kn") or demand.get("axial_kn") or 0.0)
            design_contexts = design_envelope.get("connection", {})
            original_contexts = demand.get("connection", {})
            for chord_label, chord_profile in chord_profiles:
                try:
                    side_key = (int(member_id), chord_label)
                except (TypeError, ValueError):
                    continue
                if side_key not in required_sides:
                    continue
                context = design_contexts.get(chord_label)
                original_context = original_contexts.get(chord_label)
                if chord_profile is None or not isinstance(context, dict) or not isinstance(original_context, dict):
                    continue
                # A member-strength doubler already present at the joint is
                # authoritative and must not be counted a second time.
                if context.get("doubled_chord"):
                    continue
                single_checks = [
                    check for check in self.welded_chord_web_connection_checks(
                        web_profile, chord_profile, demand_kn, context
                    ) if check.get("label") in chord_modes
                ]
                if not single_checks:
                    continue
                single_governing = max(single_checks, key=lambda check: check["ratio"])
                if single_governing["ratio"] <= DOUBLED_CHORD_TRIGGER_RATIO:
                    continue
                doubled_context = copy.deepcopy(context)
                doubled_context["doubled_chord"] = True
                doubled_context["chord_axial_ratio"] = 0.5 * float(context.get("chord_axial_ratio") or 0.0)
                doubled_checks = [
                    check for check in self.welded_chord_web_connection_checks(
                        web_profile, chord_profile, demand_kn, doubled_context
                    ) if check.get("label") in chord_modes
                ]
                if not doubled_checks:
                    continue
                doubled_governing = max(doubled_checks, key=lambda check: check["ratio"])
                if doubled_governing["ratio"] > DESIGN_UTILIZATION_PASS_LIMIT:
                    continue

                original_context["single_chord_axial_ratio"] = float(original_context.get("chord_axial_ratio") or 0.0)
                original_context["chord_axial_ratio"] = doubled_context["chord_axial_ratio"]
                original_context["doubled_chord"] = True
                original_context["auto_web_joint_doubler"] = True
                if isinstance(context.get("k_joint"), dict):
                    original_context["k_joint"] = copy.deepcopy(context["k_joint"])

                chord_member_ids = context.get("chord_members") or [context.get("chord_member")]
                for chord_member_id in chord_member_ids:
                    chord_element = elements_by_id.get(chord_member_id)
                    if chord_element is None:
                        continue
                    zone_key = (chord_element.group, chord_element.id)
                    if zone_key in existing_keys:
                        continue
                    existing_keys.add(zone_key)
                    added_zones.append({
                        "group": chord_element.group,
                        "member": chord_element.id,
                        "x1": chord_element.start.x,
                        "y1": chord_element.start.y,
                        "x2": chord_element.end.x,
                        "y2": chord_element.end.y,
                        "length_mm": chord_element.length(),
                        "combo": demand.get("connection_combo") or demand.get("combo", "-"),
                        "single_ratio": float(single_governing["ratio"]),
                        "single_check": f"Web joint {single_governing['label']}",
                        "doubled_ratio": float(doubled_governing["ratio"]),
                        "doubled_check": f"Web joint {doubled_governing['label']}",
                        "doubled_envelope": None,
                        "web_joint_trigger": True,
                        "auto_web_joint_doubler": True,
                        "web_member": int(member_id),
                        "joint_chord_member": context.get("chord_member"),
                        "joint_incident_members": list(chord_member_ids),
                        "joint_incident_count": len(chord_member_ids),
                        "chord_label": chord_label,
                        "connection_context": copy.deepcopy(context),
                        "connection_demand_kn": demand_kn,
                    })
        if added_zones:
            expanded_zones = self.expand_doubled_chord_zones_to_web_joints(
                self.structure,
                {(zone["group"], zone["member"]): zone for zone in added_zones},
            )
            added_zones = [
                zone for key, zone in expanded_zones.items()
                if key not in preexisting_keys
            ]
            envelopes["doubled_chord_zones"] = sorted(
                [*envelopes.get("doubled_chord_zones", []), *added_zones],
                key=lambda zone: (zone["group"], min(zone["x1"], zone["x2"]), zone["member"]),
            )
        return len(added_zones)

    def web_related_chord_groups(self, group):
        return {
            "WEB": ("TOP", "BOTTOM"),
            "LEFT_CANOPY_WEB": ("LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM"),
            "RIGHT_CANOPY_WEB": ("RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM"),
        }.get(group, ())

    def chord_group_combo(self, group):
        return {
            "TOP": self.top_profile_combo,
            "BOTTOM": self.bottom_profile_combo,
            "LEFT_CANOPY_TOP": self.left_canopy_top_profile_combo,
            "LEFT_CANOPY_BOTTOM": self.left_canopy_bottom_profile_combo,
            "RIGHT_CANOPY_TOP": self.right_canopy_top_profile_combo,
            "RIGHT_CANOPY_BOTTOM": self.right_canopy_bottom_profile_combo,
        }.get(group)

    def web_assignment_bucket_summary(self, profile, group, demands, ratio_lookup=None):
        if not demands:
            return "0 members"
        count_text = self.web_member_count_text(demands)
        ratios = [
            (*(ratio_lookup[(profile.name, id(demand))] if ratio_lookup is not None
               else self.web_profile_member_ratio(profile, group, demand)), demand)
            for demand in demands
        ]
        ratio, check_label, demand = max(ratios, key=lambda item: item[0])
        return (
            f"{count_text}, gov {ratio:.3f} {check_label} "
            f"(M{demand.get('member', '-')}, {demand.get('combo', '-')})"
        )

    def web_assignment_basis_signature(self, group):
        combo = self.web_group_combo(group)
        chord_profiles = self.web_connection_group_profiles(group)
        return {
            "base_web": combo.currentText() if combo is not None else "",
            "chords": [profile.name if profile is not None else "" for profile in chord_profiles],
        }

    def web_assignment_basis_matches(self, group):
        assignments = (self.auto_web_profile_assignments or {}).get(group)
        if not assignments:
            return False
        basis = (getattr(self, "auto_web_assignment_basis", {}) or {}).get(group)
        if not basis:
            return True
        return basis == self.web_assignment_basis_signature(group)

    def clear_stale_web_assignments(self):
        for group in list((self.auto_web_profile_assignments or {}).keys()):
            if not self.web_assignment_basis_matches(group):
                self.auto_web_profile_assignments.pop(group, None)
                (getattr(self, "auto_web_assignment_basis", {}) or {}).pop(group, None)
                (getattr(self, "auto_web_joint_doubler_sides", {}) or {}).pop(group, None)

    def web_assignment_overall_ratio(self, base_profile, group):
        if not self.web_assignment_basis_matches(group):
            return None
        assignments = (self.auto_web_profile_assignments or {}).get(group, {})
        demand_items = ((self.design_envelope_actions or {}).get("web_member_demands", {}) or {}).get(group, {})
        if not assignments or not demand_items:
            return None
        ratios = []
        chord_profiles = self.web_connection_group_profiles(group)
        for member_id, demand in demand_items.items():
            if not self.is_output_web_demand(demand):
                continue
            assigned_name = assignments.get(int(member_id)) or assignments.get(str(member_id)) or assignments.get(member_id)
            profile = self.catalog.get(assigned_name) if assigned_name else base_profile
            if profile is None:
                continue
            if not self.web_profile_fits_chords(profile, group, chord_profiles=chord_profiles):
                return None
            envelope = self.web_design_envelope(demand, assignments=assignments)
            ratio, check_label, _ = self.member_and_connection_ratio(profile, envelope, group)
            ratios.append((ratio, group, check_label))
        return max(ratios, default=None, key=lambda item: item[0])

    def is_output_web_demand(self, demand):
        element_group = str(demand.get("element_group") or "WEB").upper()
        return "POST" not in element_group and element_group != "APEX"

    def web_member_count_text(self, demands):
        count = sum(1 for demand in demands if self.is_output_web_demand(demand))
        return f"{count} webs" if count != 1 else "1 web"

    def web_group_single_size_ratio(self, group, base_profile=None):
        combo = self.web_group_combo(group)
        profile = base_profile or (self.selected_profile(combo) if combo is not None else None)
        if profile is None:
            return 0.0, "-"
        demands = ((self.design_envelope_actions or {}).get("web_member_demands", {}) or {}).get(group, {})
        output_demands = [demand for demand in demands.values() if self.is_output_web_demand(demand)]
        if not output_demands:
            return 0.0, "-"
        chord_profiles = self.web_connection_group_profiles(group)
        if not self.web_profile_fits_chords(profile, group, chord_profiles=chord_profiles):
            return float("inf"), "Connection geometry"
        ratios = []
        single_assignments = {int(demand["member"]): profile.name for demand in output_demands}
        for demand in output_demands:
            envelope = self.web_design_envelope(demand, assignments=single_assignments)
            ratio, check_label, _ = self.member_and_connection_ratio(
                profile,
                envelope,
                group,
                chord_profiles=chord_profiles,
            )
            ratios.append((ratio, check_label))
        return max(ratios, default=(0.0, "-"), key=lambda item: item[0])

    def auto_size_two_web_assignments(self, active_specs, update_base_combos=True):
        self.clear_auto_web_joint_doublers()
        envelopes = self.design_envelope_actions or {}
        web_demands = envelopes.get("web_member_demands", {})
        assignments = {}
        split_failures = {}
        reports = []
        adopted_base_sections = {}
        joint_doubler_sides_by_group = {}
        for label, _, group in active_specs:
            if group not in {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}:
                continue
            combo = self.web_group_combo(group)
            demands = [
                demand for demand in (web_demands.get(group) or {}).values()
                if self.is_output_web_demand(demand)
            ]
            if combo is None or len(demands) < 2:
                continue
            chord_profiles = self.web_connection_group_profiles(group)
            candidates = []
            for index in self.auto_size_candidate_indices(combo, group, min_metric_step=0.0):
                profile = self.catalog.get(combo.itemText(index))
                if profile is not None and self.web_profile_fits_chords(profile, group, chord_profiles=chord_profiles):
                    candidates.append(profile)
            if not candidates:
                continue
            candidates = sorted(
                candidates,
                key=lambda profile: self.auto_size_profile_order_key(profile, group),
            )
            evaluation_cache = {}
            member_ratio_cache = {}

            def peer_assignment_signature(demand, trial_assignments):
                signature = []
                contexts = demand.get("connection", {}) if isinstance(demand, dict) else {}
                for chord_label, context in sorted((contexts or {}).items()):
                    if not isinstance(context, dict):
                        continue
                    k_joint = context.get("k_joint")
                    peer_member = k_joint.get("peer_member") if isinstance(k_joint, dict) else None
                    peer_name = (
                        trial_assignments.get(peer_member)
                        or trial_assignments.get(str(peer_member))
                        or trial_assignments.get(int(peer_member) if str(peer_member).isdigit() else peer_member)
                    )
                    signature.append((chord_label, peer_name or ""))
                return tuple(signature)

            def evaluate_candidate_demand(candidate, demand, trial_assignments, joint_doubler_sides):
                doubler_signature = tuple(sorted(joint_doubler_sides or ()))
                cache_key = (
                    candidate.name,
                    id(demand),
                    doubler_signature,
                    peer_assignment_signature(demand, trial_assignments),
                )
                cached = evaluation_cache.get(cache_key)
                if cached is not None:
                    return cached
                member_key = (candidate.name, id(demand))
                member_result = member_ratio_cache.get(member_key)
                if member_result is None:
                    result = self.member_check_result(
                        candidate,
                        self.web_member_demand_envelope(demand),
                        group,
                    )
                    member_result = result.governing_ratio, result.governing_check
                    member_ratio_cache[member_key] = member_result
                envelope = self.web_design_envelope(
                    demand,
                    assignments=trial_assignments,
                    joint_doubler_sides=joint_doubler_sides,
                )
                connection = self.web_connection_check(
                    candidate, envelope, group, chord_profiles=chord_profiles
                )
                value = member_result
                if connection["ratio"] > value[0]:
                    value = connection["ratio"], connection["label"]
                evaluation_cache[cache_key] = value
                return value

            def find_best_split(joint_doubler_sides):
                found = None
                closest = None
                found_lookup = {}
                # Closest single-size result is only diagnostic.  Peer widths
                # still come from that same trial size, never the FEA placeholder.
                for candidate in candidates:
                    trial_assignments = {int(demand["member"]): candidate.name for demand in demands}
                    ratios = [
                        (*evaluate_candidate_demand(
                            candidate, demand, trial_assignments, joint_doubler_sides
                        ), demand)
                        for demand in demands
                    ]
                    candidate_ratio, candidate_check, candidate_demand = max(ratios, key=lambda item: item[0])
                    option = (candidate_ratio, candidate, candidate_check, candidate_demand)
                    if closest is None or option[0] < closest[0]:
                        closest = option
                # For every legal light/heavy pair, start from the all-light
                # result and promote its failing members. A wider peer can only
                # improve the K-face check, so initially passing light members
                # cannot be made to fail by those promotions. This preserves an
                # exhaustive section search without repeating the all-light
                # checks for every possible heavy profile.
                for light_index, light in enumerate(candidates):
                    all_light_assignments = {int(demand["member"]): light.name for demand in demands}
                    all_light_lookup = {}
                    light_failures = []
                    for demand in demands:
                        ratio, check_label = evaluate_candidate_demand(
                            light, demand, all_light_assignments, joint_doubler_sides
                        )
                        all_light_lookup[(light.name, id(demand))] = (ratio, check_label)
                        if ratio > DESIGN_UTILIZATION_PASS_LIMIT:
                            light_failures.append(demand)
                    for heavy in candidates[light_index:]:
                        if not light_failures and heavy.name != light.name:
                            continue
                        trial_assignments = {int(demand["member"]): light.name for demand in demands}
                        for demand in light_failures:
                            trial_assignments[int(demand["member"])] = heavy.name
                        trial_lookup = dict(all_light_lookup)
                        heavy_passes = True
                        for demand in light_failures:
                            ratio, check_label = evaluate_candidate_demand(
                                heavy, demand, trial_assignments, joint_doubler_sides
                            )
                            trial_lookup[(heavy.name, id(demand))] = (ratio, check_label)
                            if ratio > DESIGN_UTILIZATION_PASS_LIMIT:
                                heavy_passes = False
                                break
                        if not heavy_passes:
                            continue
                        light_items = [
                            demand for demand in demands
                            if trial_assignments[int(demand["member"])] == light.name
                        ]
                        heavy_items = [] if heavy.name == light.name else [
                            demand for demand in demands
                            if trial_assignments[int(demand["member"])] == heavy.name
                        ]
                        total_cost = (
                            sum((d["length_mm"] / 1000.0) * self.auto_size_profile_cost_key(light) for d in light_items)
                            + sum((d["length_mm"] / 1000.0) * self.auto_size_profile_cost_key(heavy) for d in heavy_items)
                        )
                        option = (total_cost, light, heavy, light_items, heavy_items)
                        if found is None or option[0] < found[0]:
                            found = option
                            # Refresh the selected option once with its actual
                            # peer assignment for accurate reporting.
                            found_lookup = {}
                            for demand in demands:
                                profile = self.catalog.get(trial_assignments[int(demand["member"])])
                                found_lookup[(profile.name, id(demand))] = evaluate_candidate_demand(
                                    profile, demand, trial_assignments, joint_doubler_sides
                                )
                return found, closest, found_lookup

            # A wider web must be exhausted as the first remedy for a chord
            # joint failure. Only if no single-chord split passes may the
            # existing doubled-chord fallback participate in web selection.
            best, closest_failure, ratio_lookup = find_best_split(set())
            required_sides = set()
            if best is None:
                required_sides = self.required_web_joint_doubler_sides(
                    group, demands=demands, candidates=candidates
                )
                if required_sides:
                    best, closest_failure, ratio_lookup = find_best_split(required_sides)
            if best is None:
                if closest_failure is not None:
                    ratio, candidate, check_label, demand = closest_failure
                    base_profile = self.selected_profile(combo)
                    base_ratio, base_check = self.web_group_single_size_ratio(group, base_profile)
                    if self.design_utilization_ok(base_ratio):
                        reports.append(
                            f"{label:<31}: no economical passing split found; retained single size "
                            f"{base_profile.name} at {base_ratio:.3f} {base_check}"
                        )
                        continue
                    advice = ""
                    if "K face" in check_label or "Chord" in check_label or "Branch fit" in check_label:
                        advice = " | joint/chord governed - increase the chord wall thickness or chord size"
                    reports.append(
                        f"{label:<31}: no passing web split found; closest single size {candidate.name} "
                        f"at {ratio:.3f} {check_label} (M{demand.get('member', '-')}, {demand.get('combo', '-')}){advice}"
                    )
                    split_failures[group] = f"No passing split - {check_label} {ratio:.3f}"
                else:
                    reports.append(f"{label:<31}: no passing web split found")
                    split_failures[group] = "No passing split"
                continue
            _, light, heavy, light_items, heavy_items = best
            group_assignments = {}
            for demand in light_items:
                group_assignments[int(demand["member"])] = light.name
            for demand in heavy_items:
                group_assignments[int(demand["member"])] = heavy.name
            assignments[group] = group_assignments
            adopted_base_sections[group] = heavy.name if heavy_items else light.name
            if required_sides:
                joint_doubler_sides_by_group[group] = required_sides
            doubler_note = (
                f" | {len(required_sides)} local joint side(s) require doubler"
                if required_sides else ""
            )
            if light.name == heavy.name:
                reports.append(
                    f"{label:<31}: {light.name} for all | "
                    f"{self.web_assignment_bucket_summary(light, group, light_items, ratio_lookup)}"
                    + doubler_note
                )
            else:
                reports.append(
                    f"{label:<31}: light {light.name} | {self.web_assignment_bucket_summary(light, group, light_items, ratio_lookup)}; "
                    f"heavy {heavy.name} | {self.web_assignment_bucket_summary(heavy, group, heavy_items, ratio_lookup)}"
                    + doubler_note
                )
        self.auto_web_profile_assignments = assignments
        self.auto_web_joint_doubler_sides = joint_doubler_sides_by_group
        self.web_split_failures = split_failures
        for group, required_sides in list(joint_doubler_sides_by_group.items()):
            if self.apply_auto_web_joint_doublers(
                group, assignments.get(group, {}), required_sides
            ) <= 0:
                # Defensive: a forced-doubler solution is not valid unless its
                # physical design zones can be identified and recorded.
                self.auto_web_profile_assignments.pop(group, None)
                self.auto_web_joint_doubler_sides.pop(group, None)
                assignments.pop(group, None)
                adopted_base_sections.pop(group, None)
                split_failures[group] = "No passing split - joint doubler zone could not be resolved"
        if update_base_combos:
            for group, adopted_name in adopted_base_sections.items():
                combo = self.web_group_combo(group)
                if combo is not None:
                    was_blocked = combo.blockSignals(True)
                    try:
                        self.set_combo_text_if_present(combo, adopted_name)
                    finally:
                        combo.blockSignals(was_blocked)
        self.auto_web_assignment_basis = {
            group: self.web_assignment_basis_signature(group)
            for group in assignments
        }
        return reports
