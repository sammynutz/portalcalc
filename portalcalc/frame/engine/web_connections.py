"""Truss web-to-chord welded connection checks."""

import math

from portalcalc.frame.common import WEB_CONNECTION_CFW_LEG_MM


class WebConnectionsMixin:
    """Welded hollow-section connection checks and combined member/connection ratios."""

    def web_connection_group_profiles(self, group):
        if group == "WEB":
            return self.selected_profile(self.top_profile_combo), self.selected_profile(self.bottom_profile_combo)
        if group == "LEFT_CANOPY_WEB":
            return self.selected_profile(self.left_canopy_top_profile_combo), self.selected_profile(self.left_canopy_bottom_profile_combo)
        if group == "RIGHT_CANOPY_WEB":
            return self.selected_profile(self.right_canopy_top_profile_combo), self.selected_profile(self.right_canopy_bottom_profile_combo)
        return None, None

    def auto_web_assignment_group_for_element(self, group):
        if group in {"WEB", "POST", "APEX"}:
            return "WEB"
        if group in {"LEFT_CANOPY_WEB", "LEFT_CANOPY_POST"}:
            return "LEFT_CANOPY_WEB"
        if group in {"RIGHT_CANOPY_WEB", "RIGHT_CANOPY_POST"}:
            return "RIGHT_CANOPY_WEB"
        return group

    def apply_auto_web_assignments_to_structure(self, structure):
        assignments_by_group = self.auto_web_profile_assignments or {}
        if structure is None or not assignments_by_group:
            return
        for element in structure.elements:
            assignment_group = self.auto_web_assignment_group_for_element(element.group)
            assignments = assignments_by_group.get(assignment_group)
            if not assignments:
                continue
            assigned_name = (
                assignments.get(element.id)
                or assignments.get(str(element.id))
                or assignments.get(int(element.id) if str(element.id).isdigit() else element.id)
            )
            assigned_profile = self.catalog.get(assigned_name) if assigned_name else None
            if assigned_profile is not None:
                element.profile = assigned_profile

    def hollow_connection_dimensions(self, profile):
        if profile is None:
            return None
        depth = profile.depth or self.profile_name_depth(profile.name)
        width = profile.flange_width or depth
        wall = profile.web_thickness or profile.flange_thickness or 0.0
        fy = profile.fyw or profile.fy or 300.0
        fu = profile.fu or 440.0
        if depth <= 0.0 or width <= 0.0 or wall <= 0.0 or fy <= 0.0 or fu <= 0.0:
            return None
        return {
            "depth": depth,
            "width": width,
            "wall": wall,
            "fy": fy,
            "fu": fu,
            "perimeter": 2.0 * (depth + width),
        }

    def web_profile_fits_chords(self, web_profile, group, chord_profiles=None):
        web = self.hollow_connection_dimensions(web_profile)
        if web is None:
            return False
        chord_profiles = chord_profiles or self.web_connection_group_profiles(group)
        chords = [profile for profile in chord_profiles if profile is not None]
        if not chords:
            return True
        for chord_profile in chords:
            chord = self.hollow_connection_dimensions(chord_profile)
            # Fabrication rule: the branch must be at least one nominal size
            # below the chord. Catalog widths are discrete, so strict width
            # inequality gives 89 max on a 100 chord and 100 max on a 125.
            if chord is not None and web["width"] >= chord["width"] - 1e-9:
                return False
        return True

    def welded_chord_web_connection_checks(self, web_profile, chord_profile, demand_kn, connection_context=None):
        web = self.hollow_connection_dimensions(web_profile)
        chord = self.hollow_connection_dimensions(chord_profile)
        if web is None or chord is None:
            return []

        connection_context = connection_context or {}
        branch_angle_deg = float(connection_context.get("branch_angle_deg") or 90.0)
        branch_angle_deg = min(max(branch_angle_deg, 15.0), 90.0)
        normal_component_factor = max(math.sin(math.radians(branch_angle_deg)), 0.35)
        chord_axial_ratio = max(float(connection_context.get("chord_axial_ratio") or 0.0), 0.0)
        doubled_chord = bool(connection_context.get("doubled_chord"))
        chord_demand_factor = 0.5 if doubled_chord else 1.0
        chord_interaction_factor = max(0.50, 1.0 - 0.35 * min(chord_axial_ratio, 1.0))
        chord_local_demand_kn = demand_kn * chord_demand_factor * normal_component_factor
        phi_member = 0.9
        phi_weld = 0.8
        weld_leg = WEB_CONNECTION_CFW_LEG_MM
        weld_throat = 0.707 * weld_leg
        weld_fu = min(web["fu"], chord["fu"], 490.0)
        perimeter = web["perimeter"]
        contact_width = min(web["width"], chord["width"])
        if web["width"] >= chord["width"] - 1e-9:
            return [{
                "label": "Branch fit",
                "demand": web["width"],
                "capacity": chord["width"],
                "unit": "mm",
                "notes": (
                    f"Branch/web width {web['width']:.1f} mm must be one nominal size below "
                    f"the {chord['width']:.1f} mm chord; equal-width or larger branches are not permitted."
                ),
                "ratio": float("inf"),
            }]
        branch_face_length = 2.0 * contact_width
        branch_side_length = 2.0 * min(web["depth"], chord["depth"])
        effective_wall_length = branch_face_length + branch_side_length
        beta = web["width"] / chord["width"] if chord["width"] > 0.0 else float("inf")
        gamma = chord["width"] / (2.0 * chord["wall"]) if chord["wall"] > 0.0 else float("inf")
        k_joint = connection_context.get("k_joint") or {}
        k_joint_checks = []
        if k_joint:
            peer_width = float(k_joint.get("peer_width_mm") or web["width"])
            beta_k = min((web["width"] + peer_width) / (2.0 * chord["width"]), 1.5) if chord["width"] > 0.0 else float("inf")
            theta = branch_angle_deg
            sin_theta = max(math.sin(math.radians(theta)), 1e-9)
            chord_qf = max(0.50, (1.0 - min(chord_axial_ratio, 0.95)) ** 0.10)
            # Calibrated to the ASI/SpaceGASS sample: beta=0.60, gamma=15.63 gives Qu about 19.2.
            # Wider branches distribute the load over more of the chord face, so Qu trends upward with beta.
            qu = max(4.0, min(35.0, 19.16 * (max(beta_k, 0.25) / 0.60) ** 0.75))
            k_face_capacity = qu * chord_qf * chord["fy"] * chord["wall"] ** 2 / sin_theta / 1000.0
            k_notes = (
                f"K-joint chord face plastification screen calibrated to ASI/SpaceGASS sample; "
                f"theta {theta:.1f} deg, beta {beta_k:.2f} (branches {web['width']:.0f}/{peer_width:.0f} mm), "
                f"gamma {gamma:.1f}, Qu {qu:.2f}, Qf {chord_qf:.2f}."
            )
            if doubled_chord:
                k_notes += " Doubled chord zone: web joint demand and chord axial force are shared equally between the paired chords."
            k_joint_checks.append({
                "label": "K face plast",
                "demand": demand_kn * chord_demand_factor,
                "capacity": k_face_capacity,
                "unit": "kN",
                "notes": k_notes,
            })
            validity_items = []
            min_beta = 0.1 + 0.01 * (chord["width"] / chord["wall"]) if chord["wall"] > 0.0 else float("inf")
            if theta < 30.0:
                validity_items.append(f"theta {theta:.1f} deg < 30 deg")
            if beta < max(0.25, min_beta):
                validity_items.append(f"beta {beta:.2f} below validity lower bound {max(0.25, min_beta):.2f}")
            if gamma > 25.0:
                validity_items.append(f"2*gamma {2.0 * gamma:.1f} > 50")
            if web["depth"] / web["wall"] > 40.0 or web["width"] / web["wall"] > 40.0:
                validity_items.append("brace d/t or b/t exceeds 40")
            if chord["depth"] / chord["wall"] > 40.0 or chord["width"] / chord["wall"] > 40.0:
                validity_items.append("chord d/t or b/t exceeds 40")
            if validity_items:
                k_joint_checks.append({
                    "label": "K validity warning",
                    "demand": 0.0,
                    "capacity": 1.0,
                    "unit": "",
                    "notes": "K-joint outside screening validity: " + "; ".join(validity_items),
                })

        checks = [
            {
                "label": "Weld throat",
                "demand": demand_kn,
                "capacity": phi_weld * 0.6 * weld_fu * weld_throat * perimeter / 1000.0,
                "unit": "kN",
                "notes": f"6 mm continuous fillet weld assumed all around; throat {weld_throat:.1f} mm.",
            },
            {
                "label": "Web wall yield",
                "demand": demand_kn,
                "capacity": phi_member * web["fy"] * web["wall"] * perimeter / 1000.0,
                "unit": "kN",
                "notes": "Branch/web wall axial yield around the welded perimeter.",
            },
            {
                "label": "Web wall fracture",
                "demand": demand_kn,
                "capacity": phi_member * web["fu"] * web["wall"] * perimeter / 1000.0,
                "unit": "kN",
                "notes": "Branch/web wall fracture around the welded perimeter; no net-area deduction applied.",
            },
            {
                "label": "Chord wall shear",
                "demand": chord_local_demand_kn,
                "capacity": chord_interaction_factor * phi_member * 0.6 * chord["fy"] * chord["wall"] * effective_wall_length / 1000.0,
                "unit": "kN",
                "notes": f"Chord local wall shear/yield line around the branch footprint; branch angle {branch_angle_deg:.1f} deg gives normal-component demand factor {normal_component_factor:.2f}, reduced for chord axial utilisation {chord_axial_ratio:.2f}.",
            },
            {
                "label": "Chord punching",
                "demand": chord_local_demand_kn,
                "capacity": chord_interaction_factor * phi_member * 0.6 * chord["fy"] * chord["wall"] * perimeter / 1000.0,
                "unit": "kN",
                "notes": f"Chord punching shear around the welded branch perimeter; branch angle {branch_angle_deg:.1f} deg gives normal-component demand factor {normal_component_factor:.2f}, reduced for chord axial utilisation {chord_axial_ratio:.2f}.",
            },
            {
                "label": "Chord face yield",
                "demand": chord_local_demand_kn,
                "capacity": chord_interaction_factor * phi_member * chord["fy"] * chord["wall"] * effective_wall_length / 1000.0,
                "unit": "kN",
                "notes": f"Chord face local yielding; beta {beta:.2f}, gamma {gamma:.1f}, branch angle {branch_angle_deg:.1f} deg, chord axial utilisation {chord_axial_ratio:.2f}, capacity factor {chord_interaction_factor:.2f}.",
            },
        ] + k_joint_checks
        for check in checks:
            check["ratio"] = check["demand"] / check["capacity"] if check["capacity"] > 1e-12 else (float("inf") if check["demand"] > 1e-12 else 0.0)
        return checks

    def web_connection_check(self, web_profile, envelope, group, chord_profiles=None):
        top_profile, bottom_profile = chord_profiles or self.web_connection_group_profiles(group)
        demand = float(envelope.get("connection_demand_kn") or 0.0)
        if demand <= 0.0:
            demand = max(
                self.member_envelope_value(envelope, "compression_kn"),
                self.member_envelope_value(envelope, "tension_kn"),
            )
        connection_contexts = envelope.get("connection", {}) if isinstance(envelope, dict) else {}
        chord_results = []
        for chord_label, chord_profile in [("top chord", top_profile), ("bottom chord", bottom_profile or top_profile)]:
            checks = self.welded_chord_web_connection_checks(
                web_profile,
                chord_profile,
                demand,
                connection_contexts.get(chord_label),
            )
            if checks:
                governing = max(checks, key=lambda check: check["ratio"])
                chord_results.append({
                    "chord": chord_label,
                    "checks": checks,
                    "governing": governing,
                })
        if chord_results:
            governing_result = max(chord_results, key=lambda item: item["governing"]["ratio"])
            governing_check = governing_result["governing"]
            capacity = governing_check["capacity"]
            ratio = governing_check["ratio"]
            governing_chord = governing_result["chord"]
            checks = governing_result["checks"]
        else:
            capacity = 0.0
            ratio = float("inf") if demand > 1e-12 else 0.0
            governing_chord = "connection"
            checks = []
            governing_check = {"label": "Connection geometry", "capacity": 0.0, "ratio": ratio}
        return {
            "label": f"Chord-web {governing_check['label']}",
            "demand": demand,
            "capacity": capacity,
            "ratio": ratio,
            "unit": "kN",
            "governing_chord": governing_chord,
            "governing_mode": governing_check["label"],
            "checks": checks,
            "chord_results": chord_results,
        }

    def chord_side_connection_result(self, connection, chord_label):
        chord_side_modes = {
            "K face plast",
            "Chord wall shear",
            "Chord punching",
            "Chord face yield",
        }
        result = next(
            (item for item in connection.get("chord_results", []) if item.get("chord") == chord_label),
            None,
        )
        if not result:
            return None
        chord_checks = [
            check for check in result.get("checks", [])
            if check.get("label") in chord_side_modes
        ]
        if not chord_checks:
            return None
        governing = max(chord_checks, key=lambda check: check["ratio"])
        return {**result, "governing": governing, "checks": chord_checks}

    def member_and_connection_ratio(self, profile, envelope, group, chord_profiles=None):
        result = self.member_check_result(profile, envelope, group)
        ratio = result.governing_ratio
        check_label = result.governing_check
        doubled_ratio, doubled_label = self.doubled_chord_zone_ratio(profile, envelope, group)
        if doubled_ratio > ratio:
            ratio = doubled_ratio
            check_label = doubled_label
        if group in {
            "TOP", "BOTTOM",
            "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM",
            "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM",
        }:
            web_joint_ratio, web_joint_label = self.web_assignment_chord_joint_ratio(profile, group)
            if web_joint_ratio > ratio:
                ratio = web_joint_ratio
                check_label = web_joint_label
        if group in {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}:
            connection = self.web_connection_check(profile, envelope, group, chord_profiles=chord_profiles)
            if connection["ratio"] > ratio:
                ratio = connection["ratio"]
                check_label = connection["label"]
        if group == "INTERNAL_COLUMN":
            mullion = self.propped_mullion_check(profile, envelope)
            if mullion is not None and mullion["ratio"] > ratio:
                ratio = mullion["ratio"]
                check_label = mullion["label"]
        return ratio, check_label, result
