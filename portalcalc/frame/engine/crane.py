"""Crane runway beam inputs, composite section properties, checks and auto-selection."""

import json
import math
import re

from portalcalc.crane_beam import calculate_crane_beam, CraneBeamInputs
from portalcalc.frame.common import (
    clean_profile_label,
    CRANE_PFC_CAP_PROFILES,
    CRANE_RAIL_PROFILES,
    CRANE_RATING_LOAD_TABLE,
    mm_from_metres_text,
)


class CraneBeamMixin:
    """Crane runway beam design."""

    def sync_crane_beam_span_to_bay_size(self):
        try:
            bay_size_m = max(float(self.load_bay_size_input.text()), 0.001)
        except ValueError:
            return
        self.crane_beam_span_input.setText(f"{bay_size_m:.3f}".rstrip("0").rstrip("."))
        if hasattr(self, "crane_beam_results_box"):
            self.calculate_crane_beam_report()
        if hasattr(self, "storage_results_box"):
            self.calculate_storage_load_report()

    def crane_pfc_cap_data(self):
        return CRANE_PFC_CAP_PROFILES.get(clean_profile_label(self.crane_beam_pfc_cap_combo.currentText()))

    def crane_rail_data(self):
        return CRANE_RAIL_PROFILES.get(self.crane_beam_rail_profile_combo.currentText())

    def profile_family(self, profile):
        text = f"{profile.name} {profile.group_name} {profile.shape_type}".upper()
        for family in ["UC", "UB"]:
            if re.search(rf"(^|[^A-Z]){family}([^A-Z]|$)", text):
                return family
        return ""

    def nominal_section_depth(self, profile):
        match = re.search(r"(\d+(?:\.\d+)?)", profile.name)
        return float(match.group(1)) if match else max(profile.depth, 0.0)

    def allowed_crane_pfc_caps_for_beam(self, beam):
        depth = self.nominal_section_depth(beam)
        if depth <= 310.0:
            minimum_cap_depth = 230.0
        elif depth <= 410.0:
            minimum_cap_depth = 250.0
        elif depth <= 530.0:
            minimum_cap_depth = 300.0
        else:
            minimum_cap_depth = 380.0
        caps = []
        for name, pfc in CRANE_PFC_CAP_PROFILES.items():
            if pfc and pfc.get("depth_mm", 0.0) >= minimum_cap_depth:
                caps.append((name, pfc))
        return caps

    def crane_beam_composite_properties(self, beam, pfc):
        beam_major_i = max(beam.Iyp, beam.Izp)
        beam_minor_i = min(beam.Iyp, beam.Izp)
        beam_area = max(beam.A, 0.0)
        beam_depth = max(beam.depth, 0.0)
        beam_width = max(beam.flange_width, 0.0)
        beam_flange_thickness = max(beam.flange_thickness, 0.0)

        if not pfc:
            z_major = beam.Syp if beam.Syp > 0.0 else (beam_major_i / max(beam_depth / 2.0, 1.0))
            return {
                "area_mm2": beam_area,
                "depth_mm": beam_depth,
                "centroid_top_mm": beam_depth / 2.0 if beam_depth > 0.0 else 0.0,
                "ix_mm4": beam_major_i,
                "iy_mm4": beam_minor_i,
                "iy_top_mm4": beam_flange_thickness * beam_width**3 / 12.0,
                "j_mm4": beam.J,
                "iw_mm6": beam.Iw,
                "ztop_mm3": z_major,
                "zbottom_mm3": z_major,
                "zmin_mm3": z_major,
                "ry_mm": math.sqrt(beam_minor_i / beam_area) if beam_area > 0.0 and beam_minor_i > 0.0 else 0.0,
            }

        cap_area = max(pfc["area_mm2"], 0.0)
        cap_web_t = max(pfc.get("web_thickness_mm", 0.0), 0.0)
        total_area = beam_area + cap_area
        total_depth = beam_depth + cap_web_t
        cap_y = max(pfc.get("centroid_mm", 0.0), 0.0)
        beam_y = cap_web_t + beam_depth / 2.0
        centroid = (cap_area * cap_y + beam_area * beam_y) / total_area if total_area > 0.0 else 0.0

        # Reference sheet orientation: the channel web lies over the beam top flange.
        # Its weak inertia contributes to vertical bending; its strong inertia contributes laterally.
        ix = (
            pfc["iy_mm4"]
            + cap_area * (centroid - cap_y) ** 2
            + beam_major_i
            + beam_area * (beam_y - centroid) ** 2
        )
        iy = beam_minor_i + pfc["ix_mm4"]
        beam_top_flange_iy = beam_flange_thickness * beam_width**3 / 12.0
        iy_top = pfc["ix_mm4"] + beam_top_flange_iy
        ztop = ix / centroid if centroid > 0.0 else 0.0
        zbottom = ix / (total_depth - centroid) if total_depth > centroid else 0.0
        p = pfc["ix_mm4"] / iy_top if iy_top > 0.0 else 0.0
        eccentricity = (
            pfc["width_mm"] ** 2
            * pfc["depth_mm"] ** 2
            * pfc["thickness_mm"]
            / (4.0 * p * iy_top)
            if p > 0.0 and iy_top > 0.0
            else 0.0
        )
        h = total_depth - beam_flange_thickness / 2.0 - (beam_flange_thickness + cap_web_t) / 2.0 + eccentricity
        iw = p * (1.0 - p) * iy_top * h**2 if 0.0 < p < 1.0 else beam.Iw + pfc["iw_mm6"]

        return {
            "area_mm2": total_area,
            "depth_mm": total_depth,
            "centroid_top_mm": centroid,
            "ix_mm4": ix,
            "iy_mm4": iy,
            "iy_top_mm4": iy_top,
            "j_mm4": beam.J + pfc["j_mm4"],
            "iw_mm6": iw,
            "ztop_mm3": ztop,
            "zbottom_mm3": zbottom,
            "zmin_mm3": min(value for value in [ztop, zbottom] if value > 0.0),
            "ry_mm": math.sqrt(min(ix, iy) / total_area) if total_area > 0.0 and min(ix, iy) > 0.0 else 0.0,
        }

    def interpolate_crane_rating_load(self, rating_t, table):
        if rating_t <= table[0][0]:
            x0, y0 = table[0]
            x1, y1 = table[1]
        elif rating_t >= table[-1][0]:
            x0, y0 = table[-2]
            x1, y1 = table[-1]
        else:
            for index in range(len(table) - 1):
                x0, y0 = table[index]
                x1, y1 = table[index + 1]
                if x0 <= rating_t <= x1:
                    break
        if abs(x1 - x0) < 1e-9:
            return y0
        return y0 + (rating_t - x0) * (y1 - y0) / (x1 - x0)

    def crane_loads_from_rating(self, rating_t):
        return {
            key: self.interpolate_crane_rating_load(rating_t, table)
            for key, table in CRANE_RATING_LOAD_TABLE.items()
        }

    def apply_crane_rating_to_crane_beam_inputs(self):
        try:
            rating_t = max(float(self.crane_rating_input.text()), 0.0)
        except ValueError:
            return
        if rating_t <= 0.0:
            return
        loads = self.crane_loads_from_rating(rating_t)

        def set_pair(left_widget, right_widget, value):
            text = f"{max(value, 0.0):.1f}"
            left_widget.setText(text)
            right_widget.setText(text)

        set_pair(self.crane_beam_pmax21_input, self.crane_beam_pmax22_input, loads["pmax_dynamic"])
        set_pair(self.crane_beam_pmin11_input, self.crane_beam_pmin12_input, loads["pmin_dynamic"])
        set_pair(self.crane_beam_pstatic21_input, self.crane_beam_pstatic22_input, loads["pmax_static"])
        set_pair(self.crane_beam_pstatic_min11_input, self.crane_beam_pstatic_min12_input, loads["pmin_static"])
        lateral_inertia = max(loads["lateral_inertia"], 0.0)
        oblique = max(loads["oblique"], 0.0)
        self.crane_beam_phb21_input.setText(f"{lateral_inertia:.2f}")
        self.crane_beam_phb22_input.setText(f"{-lateral_inertia:.2f}")
        self.crane_beam_phi2_input.setText(f"{oblique:.2f}")
        if hasattr(self, "crane_beam_results_box"):
            self.calculate_crane_beam_report()

    def on_crane_beam_profile_changed(self):
        self.apply_crane_beam_profile_defaults()
        if hasattr(self, "crane_beam_results_box"):
            self.calculate_crane_beam_report()

    def apply_crane_beam_profile_defaults(self):
        if self.crane_beam_profile_combo.count() <= 0:
            return
        beam = self.catalog.get(self.crane_beam_profile_combo.currentText())
        pfc = self.crane_pfc_cap_data()
        rail = self.crane_rail_data()

        beam_weight = self.profile_mass_kn_m(beam)
        pfc_weight = (pfc["mass_kg_m"] * 9.81 / 1000.0) if pfc else 0.0
        self.crane_beam_self_weight_input.setText(f"{beam_weight + pfc_weight:.3f}")
        composite = self.crane_beam_composite_properties(beam, pfc)
        self.crane_beam_ix_input.setText(f"{composite['ix_mm4'] / 1e6:.1f}")
        self.crane_beam_iy_top_input.setText(f"{composite['iy_top_mm4'] / 1e6:.1f}")
        self.crane_beam_iy_oblique_input.setText(f"{composite['iy_top_mm4'] / 1e6:.1f}")
        section_moduli = [v for v in [beam.Syp, beam.Szp] if v > 0.0]
        if section_moduli:
            self.crane_beam_zx_input.setText(f"{max(section_moduli) / 1000.0:.0f}")
            self.crane_beam_zy_input.setText(f"{min(section_moduli) / 1000.0:.0f}")
        self.crane_beam_depth_input.setText(f"{beam.depth:.1f}" if beam.depth else self.crane_beam_depth_input.text())
        self.crane_beam_flange_thickness_input.setText(f"{beam.flange_thickness:.1f}" if beam.flange_thickness else self.crane_beam_flange_thickness_input.text())
        self.crane_beam_fy_input.setText("300")

        if rail:
            self.crane_beam_rail_weight_input.setText(f"{rail['weight_kn_m']:.3f}")
            self.crane_beam_load_height_input.setText(
                f"{rail['height_mm'] / 1000.0:.3f}".rstrip("0").rstrip(".")
            )
            self.crane_beam_rail_bte_input.setText(f"{rail['bte_mm']:.0f}")
            self.crane_beam_rail_k_input.setText(f"{rail['k']:.0f}")

    def crane_beam_inputs_for_section(self, beam, pfc, pfc_name):
        composite = self.crane_beam_composite_properties(beam, pfc)
        beam_weight = self.profile_mass_kn_m(beam)
        pfc_weight = (pfc["mass_kg_m"] * 9.81 / 1000.0) if pfc else 0.0
        return CraneBeamInputs(
            span_mm=mm_from_metres_text(self.crane_beam_span_input.text()),
            wheelbase_mm=mm_from_metres_text(self.crane_beam_wheelbase_input.text()),
            load_height_mm=mm_from_metres_text(self.crane_beam_load_height_input.text()),
            beam_self_weight_kn_m=beam_weight + pfc_weight,
            rail_self_weight_kn_m=float(self.crane_beam_rail_weight_input.text()),
            pmax_21_kn=float(self.crane_beam_pmax21_input.text()),
            pmax_22_kn=float(self.crane_beam_pmax22_input.text()),
            pmin_11_kn=float(self.crane_beam_pmin11_input.text()),
            pmin_12_kn=float(self.crane_beam_pmin12_input.text()),
            pstatic_21_kn=float(self.crane_beam_pstatic21_input.text()),
            pstatic_22_kn=float(self.crane_beam_pstatic22_input.text()),
            pstatic_min_11_kn=float(self.crane_beam_pstatic_min11_input.text()),
            pstatic_min_12_kn=float(self.crane_beam_pstatic_min12_input.text()),
            phb_21_kn=float(self.crane_beam_phb21_input.text()),
            phb_22_kn=float(self.crane_beam_phb22_input.text()),
            phi_2_kn=float(self.crane_beam_phi2_input.text()),
            ix_mm4=composite["ix_mm4"],
            iy_top_mm4=composite["iy_top_mm4"],
            iy_oblique_mm4=composite["iy_top_mm4"],
            include_self_weight_in_deflection=self.crane_beam_include_sw_deflection_checkbox.isChecked(),
            beam_name=beam.name,
            pfc_cap_name=pfc_name,
            rail_name=self.crane_beam_rail_profile_combo.currentText(),
            beam_depth_mm=beam.depth,
            beam_flange_width_mm=beam.flange_width,
            beam_flange_thickness_mm=beam.flange_thickness,
            beam_minor_iy_mm4=min(beam.Iyp, beam.Izp),
            beam_j_mm4=beam.J,
            beam_iw_mm6=beam.Iw,
            beam_zx_mm3=max(beam.Syp, beam.Szp),
            beam_zy_mm3=min(value for value in [beam.Syp, beam.Szp] if value > 0.0),
            beam_fy_mpa=float(self.crane_beam_fy_input.text()),
            beam_flange_fy_mpa=beam.fy,
            pfc_cap_centroid_mm=(pfc["centroid_mm"] if pfc else beam.flange_thickness / 2.0),
            pfc_cap_area_mm2=(pfc["area_mm2"] if pfc else 0.0),
            pfc_cap_ix_mm4=(pfc["ix_mm4"] if pfc else 0.0),
            pfc_cap_iy_mm4=(pfc["iy_mm4"] if pfc else 0.0),
            pfc_cap_zex_mm3=(pfc["zex_mm3"] if pfc else 0.0),
            pfc_cap_zey_mm3=(pfc["zey_mm3"] if pfc else 0.0),
            pfc_cap_j_mm4=(pfc["j_mm4"] if pfc else 0.0),
            pfc_cap_iw_mm6=(pfc["iw_mm6"] if pfc else 0.0),
            pfc_cap_depth_mm=(pfc["depth_mm"] if pfc else 0.0),
            pfc_cap_width_mm=(pfc["width_mm"] if pfc else 0.0),
            pfc_cap_thickness_mm=(pfc["thickness_mm"] if pfc else 0.0),
            pfc_cap_web_thickness_mm=(pfc["web_thickness_mm"] if pfc else 0.0),
            pfc_cap_fy_mpa=beam.fy,
            composite_area_mm2=composite["area_mm2"],
            composite_depth_mm=composite["depth_mm"],
            composite_centroid_top_mm=composite["centroid_top_mm"],
            composite_minor_iy_mm4=composite["iy_mm4"],
            composite_top_flange_iy_mm4=composite["iy_top_mm4"],
            composite_j_mm4=composite["j_mm4"],
            composite_iw_mm6=composite["iw_mm6"],
            composite_ztop_mm3=composite["ztop_mm3"],
            composite_zbottom_mm3=composite["zbottom_mm3"],
            composite_zmin_mm3=composite["zmin_mm3"],
            composite_ry_mm=composite["ry_mm"],
            rail_bte_mm=float(self.crane_beam_rail_bte_input.text()),
            rail_eccentricity_k=float(self.crane_beam_rail_k_input.text()),
        )

    def crane_beam_inputs_from_ui(self):
        pfc = self.crane_pfc_cap_data()
        beam = self.catalog.get(self.crane_beam_profile_combo.currentText())
        return self.crane_beam_inputs_for_section(beam, pfc, clean_profile_label(self.crane_beam_pfc_cap_combo.currentText()))

    def crane_beam_utilizations(self, result):
        capacity_utilization = max(
            result.vertical_bending_ratio,
            result.top_flange_ratio,
            result.bottom_flange_ratio,
            result.combined_top_ratio,
            result.combined_bottom_ratio,
        )
        deflection_utilization = max(
            result.vertical_deflection_mm / result.vertical_limit_mm if result.vertical_limit_mm > 1e-9 else math.inf,
            result.lateral_deflection_mm / result.horizontal_limit_mm if result.horizontal_limit_mm > 1e-9 else math.inf,
            result.oblique_deflection_mm / result.horizontal_limit_mm if result.horizontal_limit_mm > 1e-9 else math.inf,
        )
        return capacity_utilization, deflection_utilization

    def current_crane_beam_summary(self):
        inputs = self.crane_beam_inputs_from_ui()
        result = calculate_crane_beam(inputs)
        capacity_utilization, deflection_utilization = self.crane_beam_utilizations(result)
        return {
            "section": f"{inputs.beam_name} + {inputs.pfc_cap_name}",
            "span_mm": inputs.span_mm,
            "weight_kn_m": inputs.beam_self_weight_kn_m,
            "weight_kg_m": inputs.beam_self_weight_kn_m * 1000.0 / 9.81,
            "capacity_utilization": capacity_utilization,
            "deflection_utilization": deflection_utilization,
            "result": result,
        }

    def crane_rating_active_from_ui(self):
        try:
            return max(float(self.crane_rating_input.text()), 0.0) > 0.0
        except ValueError:
            return False

    def crane_auto_select_signature(self):
        data = dict(self.collect_preset_data().get("crane_beam", {}))
        for key in {
            "beam_profile", "pfc_cap", "beam_self_weight_kn_m",
            "ix_x10_6_mm4", "iy_top_x10_6_mm4", "iy_oblique_x10_6_mm4",
            "zx_x10_3_mm3", "zy_x10_3_mm3", "depth_mm", "flange_thickness_mm",
        }:
            data.pop(key, None)
        data["crane_rating_t"] = self.crane_rating_input.text().strip()
        return json.dumps(data, sort_keys=True)

    def passing_crane_beam_candidate(self, beam, pfc, pfc_name):
        inputs = self.crane_beam_inputs_for_section(beam, pfc, pfc_name)
        result = calculate_crane_beam(inputs)
        capacity_utilization, deflection_utilization = self.crane_beam_utilizations(result)
        return {
            "beam": beam,
            "pfc": pfc,
            "pfc_name": pfc_name,
            "inputs": inputs,
            "result": result,
            "family": self.profile_family(beam),
            "weight_kn_m": inputs.beam_self_weight_kn_m,
            "capacity_utilization": capacity_utilization,
            "deflection_utilization": deflection_utilization,
            "passes": capacity_utilization <= 1.0 and deflection_utilization <= 1.0,
        }

    def auto_select_crane_beam(self):
        try:
            candidates = []
            for beam in self.catalog.all_profiles():
                family = self.profile_family(beam)
                if family == "UC":
                    candidates.append(self.passing_crane_beam_candidate(beam, None, "No PFC cap"))
                elif family == "UB":
                    for pfc_name, pfc in self.allowed_crane_pfc_caps_for_beam(beam):
                        candidates.append(self.passing_crane_beam_candidate(beam, pfc, pfc_name))
        except ValueError as exc:
            self.notify("warning", "Crane beam auto select", str(exc))
            return

        passing = [candidate for candidate in candidates if candidate["passes"]]
        if not passing:
            worst = sorted(
                candidates,
                key=lambda candidate: max(candidate["capacity_utilization"], candidate["deflection_utilization"]),
            )[:5]
            lines = [
                "No passing crane beam found for the current inputs.",
                "",
                "Closest candidates:",
            ]
            for candidate in worst:
                lines.append(
                    f"{candidate['beam'].name} + {candidate['pfc_name']}: "
                    f"{candidate['weight_kn_m']:.3f} kN/m, "
                    f"capacity util {candidate['capacity_utilization']:.2f}, "
                    f"deflection util {candidate['deflection_utilization']:.2f}"
                )
            self.crane_beam_results_box.setPlainText("\n".join(lines))
            self.update_crane_beam_status_labels(worst[0]["result"] if worst else calculate_crane_beam(self.crane_beam_inputs_from_ui()))
            return

        ub_candidates = sorted(
            [candidate for candidate in passing if candidate["family"] == "UB"],
            key=lambda candidate: (candidate["weight_kn_m"], candidate["beam"].name, candidate["pfc_name"]),
        )
        uc_candidates = sorted(
            [candidate for candidate in passing if candidate["family"] == "UC"],
            key=lambda candidate: (candidate["weight_kn_m"], candidate["beam"].name),
        )
        best_by_weight = sorted(passing, key=lambda candidate: (candidate["weight_kn_m"], candidate["family"], candidate["beam"].name))[0]
        selected = best_by_weight
        uc_preference_note = ""
        if ub_candidates and uc_candidates:
            lightest_ub = ub_candidates[0]
            lightest_uc = uc_candidates[0]
            if lightest_uc["weight_kn_m"] <= lightest_ub["weight_kn_m"] * 1.10:
                selected = lightest_uc
                uc_preference_note = (
                    f"UC preference applied: {lightest_uc['beam'].name} is within 10% of "
                    f"{lightest_ub['beam'].name} + {lightest_ub['pfc_name']}."
                )

        self.set_combo_text_if_present(self.crane_beam_profile_combo, selected["beam"].name)
        self.set_combo_text_if_present(self.crane_beam_pfc_cap_combo, selected["pfc_name"])
        self.apply_crane_beam_profile_defaults()
        self.calculate_crane_beam_report()

        comparison_lines = [
            "",
            "Auto selection",
            f"Selected                  : {selected['beam'].name} + {selected['pfc_name']}",
            f"Beam self weight          : {selected['weight_kn_m']:.3f} kN/m",
            f"Capacity / deflection util: {selected['capacity_utilization']:.2f} / {selected['deflection_utilization']:.2f}",
        ]
        if uc_preference_note:
            comparison_lines.append(uc_preference_note)
        if uc_candidates:
            candidate = uc_candidates[0]
            comparison_lines.append(
                f"Lightest passing UC       : {candidate['beam'].name}, {candidate['weight_kn_m']:.3f} kN/m"
            )
        if ub_candidates:
            candidate = ub_candidates[0]
            comparison_lines.append(
                f"Lightest passing UB + PFC : {candidate['beam'].name} + {candidate['pfc_name']}, {candidate['weight_kn_m']:.3f} kN/m"
            )
        self.crane_beam_results_box.append("\n".join(comparison_lines))

    def calculate_crane_beam_report(self):
        try:
            inputs = self.crane_beam_inputs_from_ui()
            result = calculate_crane_beam(inputs)
        except ValueError as exc:
            self.notify("warning", "Crane beam inputs", str(exc))
            return

        def ratio_text(value):
            if math.isinf(value):
                return "Span / inf"
            return f"Span / {value:.0f}"

        lines = [
            "STEEL CRANE BEAM - ACTIONS / DEFLECTION FIRST PASS",
            "",
            "Inputs",
            f"Beam / PFC cap / rail    : {inputs.beam_name} / {inputs.pfc_cap_name} / {inputs.rail_name}",
            f"Span L                  : {inputs.span_mm:8.0f} mm",
            f"Wheelbase wb            : {inputs.wheelbase_mm:8.0f} mm",
            f"Load height ht          : {inputs.load_height_mm:8.0f} mm",
            f"Rail / beam S.Wt        : {inputs.rail_self_weight_kn_m:8.3f} / {inputs.beam_self_weight_kn_m:8.3f} kN/m",
            f"Dynamic Pmax.21 / .22   : {inputs.pmax_21_kn:8.1f} / {inputs.pmax_22_kn:8.1f} kN",
            f"Static Pmax.21 / .22    : {inputs.pstatic_21_kn:8.1f} / {inputs.pstatic_22_kn:8.1f} kN",
            f"Static Pmin.11 / .12    : {inputs.pstatic_min_11_kn:8.1f} / {inputs.pstatic_min_12_kn:8.1f} kN",
            f"Lateral Phb.21 / .22    : {inputs.phb_21_kn:8.1f} / {inputs.phb_22_kn:8.1f} kN",
            f"Oblique Phi.2           : {inputs.phi_2_kn:8.1f} kN",
            "",
            "Load factors - AS1418.18 Cl 4.4.4",
            f"Dead / hoisted          : {inputs.dead_factor:8.2f} / {inputs.hoisted_factor:8.2f}",
            f"Inertial / oblique      : {inputs.inertial_factor:8.2f} / {inputs.oblique_factor:8.2f}",
            "",
            "Design moments",
            f"c = min(P1,P2)*wb/(P1+P2) : {result.c_mm:8.0f} mm",
            f"Max moment location xm     : {result.xm_mm:8.0f} mm",
            f"n = L-xm-wb                : {result.n_mm:8.0f} mm",
            f"Reaction RAv*              : {result.rav_kn:8.1f} kN",
            f"Reaction RBv*              : {result.rbv_kn:8.1f} kN",
            f"Shear left / right P1      : {result.shear_left_p1_kn:8.1f} / {result.shear_right_p1_kn:8.1f} kN",
            f"Shear left / right P2      : {result.shear_left_p2_kn:8.1f} / {result.shear_right_p2_kn:8.1f} kN",
            f"Moment at P1 / P2          : {result.mv1_knm:8.1f} / {result.mv2_knm:8.1f} kNm",
            f"Max P1/P2 moment           : {result.mv12_knm:8.1f} kNm",
            f"Max midspan moment         : {result.mvm_knm:8.1f} kNm",
            f"Mv*                        : {result.mv_max_knm:8.1f} kNm",
            "",
            "Lateral inertia - coincident loading",
            f"Coincident Mhl.c*          : {result.mhl_coincident_knm:8.1f} kNm",
            f"Midspan max single Mhl     : {result.mhl_midspan_single_knm:8.1f} kNm (info)",
            f"Mhl*                       : {result.mhl_knm:8.1f} kNm",
            f"RAI* / RBI*                : {result.rai_kn:8.1f} / {result.rbi_kn:8.1f} kN",
            "",
            "Horizontal oblique - coincident loading",
            f"Coincident Mho.c*          : {result.mho_coincident_knm:8.1f} kNm",
            f"Midspan max single Mho     : {result.mho_midspan_single_knm:8.1f} kNm (info)",
            f"Mho*                       : {result.mho_knm:8.1f} kNm",
            f"RAO* / RBO*                : {result.rao_kn:8.1f} / {result.rbo_kn:8.1f} kN",
            "",
            "Reactions",
            f"Crane beam rail reaction   : {result.rail_reaction_ends_kn:8.1f} kN at each end",
            f"Frame reaction max/min     : {result.frame_reaction_max_kn:8.1f} / {result.frame_reaction_min_kn:8.1f} kN",
            f"Lateral reaction           : {result.lateral_reaction_both_sides_kn:8.1f} kN",
            f"Oblique reaction max/min   : {result.oblique_reaction_max_kn:8.1f} / {result.oblique_reaction_min_kn:8.1f} kN",
            "",
            "Deflections - static loads",
            f"Ix / Iy.top / Iy.oblique   : {inputs.ix_mm4/1e6:8.1f} / {inputs.iy_top_mm4/1e6:8.1f} / {inputs.iy_oblique_mm4/1e6:8.1f} x10^6 mm^4",
            f"Vertical deflection        : {result.vertical_deflection_mm:8.1f} mm ({ratio_text(result.vertical_deflection_ratio)})",
            f"Lateral deflection         : {result.lateral_deflection_mm:8.1f} mm ({ratio_text(result.lateral_deflection_ratio)})",
            f"Oblique deflection         : {result.oblique_deflection_mm:8.1f} mm ({ratio_text(result.oblique_deflection_ratio)})",
            "",
            "Reference-style composite section properties",
            f"Area / depth              : {result.composite_area_mm2:8.0f} mm2 / {result.composite_depth_mm:8.1f} mm",
            f"Elastic centroid from top : {result.composite_centroid_top_mm:8.1f} mm",
            f"Ztop / Zbottom / Zmin     : {result.composite_ztop_mm3/1e3:8.1f} / {result.composite_zbottom_mm3/1e3:8.1f} / {result.composite_zmin_mm3/1e3:8.1f} x10^3 mm3",
            f"Iy / Iy.top               : {result.composite_minor_iy_mm4/1e6:8.1f} / {result.composite_top_flange_iy_mm4/1e6:8.1f} x10^6 mm4",
            f"J / Iw / ry               : {result.composite_j_mm4/1e3:8.1f} x10^3 mm4 / {result.composite_iw_mm6/1e9:8.1f} x10^9 mm6 / {result.composite_ry_mm:8.1f} mm",
            "",
            "Deflection limits",
            f"Vertical limit             : {result.vertical_limit_mm:8.1f} mm (L/{inputs.vertical_limit_ratio:.0f} or {inputs.vertical_limit_cap_mm:.0f} mm)",
            f"Horizontal limit           : {result.horizontal_limit_mm:8.1f} mm (L/{inputs.horizontal_limit_ratio:.0f} or {inputs.horizontal_limit_cap_mm:.0f} mm)",
            f"Required Ix vertical       : {result.required_ix_vertical_mm4/1e6:8.1f} x10^6 mm^4",
            f"Required Iy lateral        : {result.required_iy_lateral_mm4/1e6:8.1f} x10^6 mm^4",
            f"Required Iy oblique        : {result.required_iy_oblique_mm4/1e6:8.1f} x10^6 mm^4",
            "",
            "Preliminary section capacity",
            f"Rail eccentricity ey       : {result.rail_eccentricity_mm:8.1f} mm (L/1000 + BTE/k)",
            f"Top / bottom centroid      : {result.top_centroid_mm:8.1f} / {result.bottom_centroid_mm:8.1f} mm",
            f"Lever df                   : {result.lever_df_mm:8.1f} mm",
            f"Mh* for flange split       : {result.mh_design_knm:8.1f} kNm",
            f"Ft / Fb / Fv               : {result.top_flange_multiplier:8.3f} / {result.bottom_flange_multiplier:8.3f} / {result.eccentricity_multiplier:8.3f}",
            f"Mh.top*                    : {result.mh_top_knm:8.1f} kNm",
            f"Mh.bottom*                 : {result.mh_bottom_knm:8.1f} kNm",
            "",
            "AS4100 vertical member capacity",
            f"Le / Mo                    : {result.as4100_le_mm:8.0f} mm / {result.as4100_mo_knm:8.1f} kNm",
            f"beta_x                     : {result.as4100_beta_x_mm:8.1f} mm",
            f"alpha_s / alpha_m          : {result.as4100_alpha_s:8.3f} / {result.as4100_alpha_m_effective:8.3f}",
            f"phi Msx / phi Mbx AS4100   : {result.phi_msx_knm:8.1f} / {result.phi_mbx_as4100_knm:8.1f} kNm",
            "",
            "Ref 8 beam and channel capacity",
            f"Mob / alpha_sb             : {result.ref8_mob_knm:8.1f} / {result.ref8_alpha_sb:8.3f}",
            f"phi Mbx Ref 8 / adopted    : {result.phi_mbx_ref8_knm:8.1f} / {result.phi_mbx_knm:8.1f} kNm",
            "",
            "Horizontal flange capacity",
            f"phi Msy top / bottom       : {result.phi_msy_top_knm:8.1f} / {result.phi_msy_bottom_knm:8.1f} kNm",
            f"Vertical bending ratio     : {result.vertical_bending_ratio:8.2f}",
            f"Top flange ratio           : {result.top_flange_ratio:8.2f}",
            f"Bottom flange ratio        : {result.bottom_flange_ratio:8.2f}",
            f"Combined top / bottom      : {result.combined_top_ratio:8.2f} / {result.combined_bottom_ratio:8.2f}",
            "",
            "Notes",
            "Dynamic wheel loads are used for strength moments.",
            "Static wheel loads are used for deflection checks.",
            "Composite section properties follow the reference sheet orientation: PFC weak axis for vertical Ix, PFC strong axis for lateral Iy/top-flange bending.",
            "Vertical capacity adopts the lower of the AS4100 and Ref 8 beam-and-channel member capacities.",
            "Horizontal flange capacity is still a section-capacity approximation while the beam-plus-channel method is refined.",
        ]
        self.crane_beam_results_box.setPlainText("\n".join(lines))
        self.update_crane_beam_status_labels(result)

    def utilization_status_text(self, ratio):
        if math.isinf(ratio):
            return "NOT OK - utilization inf"
        status = "OK" if ratio <= 1.0 else "NOT OK"
        return f"{status} - utilization {ratio:.2f}"

    def update_crane_beam_status_labels(self, result):
        capacity_utilization, deflection_utilization = self.crane_beam_utilizations(result)
        for label, ratio in [
            (self.crane_beam_capacity_status_label, capacity_utilization),
            (self.crane_beam_deflection_status_label, deflection_utilization),
        ]:
            label.setText(self.utilization_status_text(ratio))
            label.setStyleSheet("color: #2e7d32; font-weight: 600;" if ratio <= 1.0 else "color: #c62828; font-weight: 600;")
