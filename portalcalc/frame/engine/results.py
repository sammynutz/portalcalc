"""Results: report text, the design dossier and computation-report capture."""

import copy
import html
from datetime import datetime, timezone

from portalcalc import bay_layout, costing
from portalcalc.app_metadata import APP_VERSION
from portalcalc.bracing.restraint import OPTION as BRACING_3D_RESTRAINT_OPTION
from portalcalc.crane_beam import CRANE_GAUGE_CHANGE_LIMIT_MM
from portalcalc.frame.common import (
    MAX_DOUBLED_CHORD_LENGTH_FRACTION,
    mm_from_metres_text,
    WEB_CONNECTION_CFW_LEG_MM,
)
from portalcalc.storage_loads import is_centre_stub_storage_mode, storage_summary_lines
from portalcalc.wind import calculate_wind


class ResultsMixin:
    """Turns a solved, checked model into text reports and the service dossier."""

    def engineering_summary_html(self):
        """Build a concise, print-safe summary from the current solved model."""
        if self.structure is None or not self.section_checks_box.toPlainText().strip():
            raise ValueError("Generate and solve the frame before creating the engineering summary.")

        esc = lambda value: html.escape(str(value))
        span_mm = mm_from_metres_text(self.span_input.text())
        eave_mm = mm_from_metres_text(self.eave_input.text())
        pitch = float(self.pitch_input.text())
        wind = calculate_wind(self.wind_inputs_from_ui(span_mm, pitch, eave_mm))

        def rows(items):
            return "".join(f"<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>" for label, value in items)

        geometry = [
            ("Building", f"{self.span_input.text()} m span x {self.building_length_input.text()} m long"),
            ("Portal spacing", f"{bay_layout.format_bays(self._bay_lengths_mm)} mm"),
            ("Eave / roof", f"{self.eave_input.text()} m / {self.pitch_input.text()} deg {self.current_roof_shape().lower()}"),
            ("Frame", self.frame_system_combo.currentText()),
            ("Internal columns", (
                f"Custom positions: {self.internal_column_positions_input.text()} m from left column centreline"
                if self.internal_column_count_combo.currentText() == "Custom positions"
                else self.internal_column_count_combo.currentText()
            )),
            ("Side additions", f"Left {self.left_canopy_type_combo.currentText()} {self.left_canopy_length_input.text()} m; Right {self.right_canopy_type_combo.currentText()} {self.right_canopy_length_input.text()} m"),
        ]
        loads = [
            ("Roof G / Q", f"{self.g_load_input.text()} / {self.q_load_input.text()} kPa downward"),
            ("Solar / fire / HVAC / other", f"{self.solar_load_input.text()} / {self.fire_service_load_input.text()} / {self.hvac_load_input.text()} / {self.other_load_input.text()} kPa"),
            ("Snow", f"{self.snow_region_combo.currentText()}, AHD {self.snow_ahd_height_input.text()} m"),
            ("Crane rating", f"{self.crane_rating_input.text()} t"),
        ]
        wind_lines = wind.summary_lines()[1:]
        wind_rows = []
        for line in wind_lines:
            if ":" in line:
                label, value = line.split(":", 1)
                wind_rows.append((label.strip(), value.strip()))

        check_lines = self.section_checks_box.toPlainText().splitlines()
        result_lines = []
        keywords = ("Governing:", "utilisation", "deflection:", "deflection SLS", "Auto-selected/current section")
        for line in check_lines:
            clean = line.strip()
            if clean and any(key.lower() in clean.lower() for key in keywords):
                result_lines.append(clean)
        # Preserve order while removing repeated overview/detail entries.
        result_lines = list(dict.fromkeys(result_lines))
        result_html = "".join(f"<li>{esc(line)}</li>" for line in result_lines) or "<li>No governing checks available.</li>"

        return f"""<!doctype html><html><head><style>
        @page {{ size: A4; margin: 14mm; }} body {{ font-family: Arial, sans-serif; font-size: 9pt; color: #18222d; }}
        h1 {{ color:#13377e; font-size:18pt; margin:0; }} h2 {{ color:#13377e; font-size:11pt; border-bottom:1px solid #00b5af; margin:12px 0 4px; }}
        .meta {{ color:#4d5b78; margin:2px 0 10px; }} table {{ width:100%; border-collapse:collapse; }} th,td {{ border-bottom:1px solid #d5dcea; padding:3px 5px; vertical-align:top; }} th {{ width:31%; text-align:left; background:#eef2f9; }}
        ul {{ margin:4px 0; padding-left:18px; }} li {{ margin:2px 0; }} .note {{ margin-top:12px; font-size:8pt; color:#52616d; }}
        </style></head><body><h1>PortalCalc Engineering Summary</h1>
        <div class="meta">Issued {esc(datetime.now().astimezone().strftime('%d %b %Y, %H:%M %Z'))} &nbsp;|&nbsp; PortalCalc {esc(APP_VERSION)}</div>
        <h2>Building geometry</h2><table>{rows(geometry)}</table>
        <h2>Wind loading factors, pressures, Cpe and Cpi</h2><table>{rows(wind_rows)}</table>
        <h2>Roof and other loads</h2><table>{rows(loads)}</table>
        <h2>Governing results</h2><ul>{result_html}</ul>
        <div class="note">Summary of the current solved model. Review the full calculation output, assumptions and load combinations before design certification.</div>
        </body></html>"""

    def capture_computation_report(self, metadata=None):
        """Analyse current selections and freeze the exact report evidence."""
        from portalcalc.reports.computation_report_data import build_report_data
        names = ("_capture_computation_report", "_preserve_web_assignments_on_solve",
                 "_suppress_auto_web_split_on_solve", "_suppress_panel_topology_search")
        previous = {name: getattr(self, name, False) for name in names}
        try:
            for name in names:
                setattr(self, name, True)
            self.generate_and_solve()
            if self.structure is None:
                raise ValueError(self.results_box.toPlainText() or "The current design could not be solved.")
            return build_report_data(self, metadata)
        finally:
            for name, value in previous.items():
                setattr(self, name, value)

    def design_evidence_key_lines(self, text, max_lines=260):
        """Extract compact, evidence-bearing lines from the detailed reports."""
        keywords = (
            "governing", "utilisation", "utilization", "deflection", "pass", "fail",
            "warning", "limitation", "not checked", "not yet", "converged", "assumed",
            "auto-selected", "current section", "no passing", "outside", "invalid",
        )
        selected = []
        seen = set()
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            is_heading = len(line) <= 100 and line == line.upper() and any(character.isalpha() for character in line)
            if not is_heading and not any(keyword in line.lower() for keyword in keywords):
                continue
            if line in seen:
                continue
            seen.add(line)
            selected.append(line)
            if len(selected) >= max_lines:
                selected.append("[Additional report lines omitted from the calculation dossier]")
                break
        return selected

    def member_stability_context(self, group, envelope):
        """Expose the calculator-owned column restraint and sway assumptions."""
        column_groups = {
            "LEFT_COLUMN", "RIGHT_COLUMN", "INTERNAL_COLUMN",
            "LEFT_LEAN_COLUMN", "RIGHT_LEAN_COLUMN",
        }
        if group not in column_groups or not isinstance(envelope, dict):
            return None
        restraint = dict((self.member_check_restraints or {}).get(group, {}))
        try:
            actions = self.member_actions_from_envelope(envelope, group)
        except (TypeError, ValueError):
            actions = None
        automatic_plan = None
        if envelope.get('column_flybrace_basis'):
            for _, combo, candidate_group in self.active_auto_size_group_specs():
                if candidate_group == group:
                    evaluated = self.column_flybrace_evaluation(self.selected_profile(combo), envelope, group)
                    if evaluated is not None:
                        automatic_plan = evaluated[2]
                        restraint.update({'major_mm': automatic_plan['in_plane_length_mm'],
                                          'minor_mm': actions.effective_length_y_mm,
                                          'ke': 1.0, 'am': actions.moment_modifier})
                    break
        physical_lengths = [
            float(item.get("length_mm") or 0.0)
            for item in envelope.values()
            if isinstance(item, dict) and float(item.get("length_mm") or 0.0) > 0.0
        ]
        mid_item = (
            (self.design_envelope_actions or {})
            .get("sls_column_midspan_ws_deflections", {})
            .get(group, {})
        )
        mid_height = float(mid_item.get("height_mm") or 0.0)
        mid_divisor = float(self.column_deflection_divisor())
        mid_limit = mid_height / mid_divisor if mid_height > 0.0 else 0.0
        mid_demand = max(float(mid_item.get("value") or 0.0), 0.0)
        is_lean = group in {"LEFT_LEAN_COLUMN", "RIGHT_LEAN_COLUMN"}
        clad = bool(restraint.get("clad"))
        if automatic_plan is not None:
            assumption = automatic_plan['restraint']
        elif is_lean and clad:
            assumption = (
                "Clad lean-to outer wall: one mid-height flybrace is assumed; "
                "major- and minor-axis restraint lengths are half the column height."
            )
        elif is_lean:
            assumption = (
                "Unclad lean-to outer wall: no mid-height flybrace is assumed; "
                "major- and minor-axis restraint lengths use the full column height."
            )
        else:
            assumption = "Column restraint lengths are taken from the current PortalCalc restraint model."
        return {
            "group": group,
            "physical_member_length_mm": max(physical_lengths, default=None),
            "wall_clad": clad,
            "restraint_assumption": assumption,
            "flybrace_schedule": copy.deepcopy(automatic_plan),
            "major_restraint_length_mm": restraint.get("major_mm"),
            "minor_restraint_length_mm": restraint.get("minor_mm"),
            "effective_length_factor_ke": restraint.get("ke", 1.0),
            "effective_major_length_mm": (
                actions.effective_length_z_mm if actions is not None else None
            ),
            "effective_minor_length_mm": (
                actions.effective_length_y_mm if actions is not None else None
            ),
            "moment_modifier_am": actions.moment_modifier if actions is not None else restraint.get("am"),
            "mid_height_ws_deflection": {
                "checked": bool(mid_item.get("value", -1.0) >= 0.0 and mid_limit > 0.0),
                "demand_mm": mid_demand if mid_item.get("value", -1.0) >= 0.0 else None,
                "limit_mm": mid_limit if mid_limit > 0.0 else None,
                "limit_basis": f"H/{mid_divisor:.0f}",
                "ratio": mid_demand / mid_limit if mid_limit > 0.0 and mid_item.get("value", -1.0) >= 0.0 else None,
                "combination": mid_item.get("combo"),
                "member": mid_item.get("member"),
            },
        }

    def column_stability_context_rows(self):
        rows = []
        seen = set()
        for _, _, group in self.active_auto_size_group_specs():
            if group in seen:
                continue
            seen.add(group)
            context = self.member_stability_context(
                group,
                (self.design_envelope_actions or {}).get(group),
            )
            if context is not None:
                rows.append(context)
        return rows

    def calculation_design_dossier(self):
        """Build a compact, serialisable snapshot of the current solved design."""
        if self.structure is None or self.design_envelope_actions is None:
            raise ValueError("Generate and solve the frame before collecting calculation evidence.")

        preset = copy.deepcopy(self.collect_preset_data())
        preset.pop("display", None)
        preset.pop("finishes", None)
        crane_rating = float((preset.get("loads") or {}).get("crane_rating_t") or 0.0)
        if crane_rating <= 0.0:
            preset.pop("crane_beam", None)

        member_checks = []
        seen_groups = set()
        for label, combo, group in self.active_auto_size_group_specs():
            if group in seen_groups:
                continue
            seen_groups.add(group)
            envelope = (self.design_envelope_actions or {}).get(group)
            profile = self.selected_profile(combo)
            if profile is None or not self.member_envelope_has_demands(envelope):
                continue
            ratio, governing_check, result = self.member_and_connection_ratio(profile, envelope, group)
            checks = []
            for key, check in result.checks.items():
                checks.append({
                    "key": key,
                    "label": check.label,
                    "demand": check.demand,
                    "capacity": check.capacity,
                    "ratio": check.ratio,
                    "unit": check.unit,
                    "notes": check.notes,
                })
            evaluated = self.column_flybrace_evaluation(profile, envelope, group)
            if evaluated is not None:
                for check in checks:
                    check['source'] = copy.deepcopy(evaluated[2]['sources'].get(check['key'], {}))
            action_sources = {}
            for action_key in ("compression_kn", "tension_kn", "shear_kn", "moment_knm"):
                item = envelope.get(action_key) if isinstance(envelope, dict) else None
                if isinstance(item, dict):
                    action_sources[action_key] = {
                        "value": item.get("value"),
                        "combination": item.get("combo"),
                        "member": item.get("member"),
                        "length_mm": item.get("length_mm"),
                    }
            item = {
                "label": label,
                "group": group,
                "profile": profile.name,
                "overall_ratio": ratio,
                "governing_check": governing_check,
                "action_sources": action_sources,
                "member_capacity_checks": checks,
            }
            stability_context = self.member_stability_context(group, envelope)
            if stability_context is not None:
                item["stability_context"] = stability_context
            if group in {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}:
                connection = self.web_connection_check(profile, envelope, group)
                item["governing_connection"] = {
                    "label": connection.get("label"),
                    "demand": connection.get("demand"),
                    "capacity": connection.get("capacity"),
                    "ratio": connection.get("ratio"),
                    "unit": connection.get("unit"),
                    "governing_chord": connection.get("governing_chord"),
                    "governing_mode": connection.get("governing_mode"),
                }
            member_checks.append(item)

        active_specs = self.active_auto_size_group_specs()
        overall_ratio, overall_group, overall_check = self.auto_size_overall_ratio(active_specs)
        serviceability = [
            {"ratio": ratio, "check": check, "limit_basis": basis}
            for ratio, check, basis in self.auto_size_serviceability_ratios()
        ]
        report_lines = self.design_evidence_key_lines(self.section_checks_box.toPlainText())
        results_lines = self.design_evidence_key_lines(self.results_box.toPlainText(), max_lines=120)
        return {
            "schema": "portalcalc.calculation.dossier.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "portalcalc_version": APP_VERSION,
            "status": "current solved model",
            "analysis": self.analysis_info or {},
            "inputs": preset,
            "overall_governing": {
                "ratio": overall_ratio,
                "group": overall_group,
                "check": overall_check,
                "passes_ratio_screen": self.design_utilization_ok(overall_ratio),
            },
            "member_checks": member_checks,
            "column_stability_context": self.column_stability_context_rows(),
            "column_flybraces": self.column_flybrace_schedule(),
            "serviceability_checks": serviceability,
            "purlin_check": self.purlin_check,
            "wall_girt_check": self.wall_girt_check,
            "end_wall_mullion_design": self.end_wall_mullion_design,
            "end_wall_girt_check": self.end_wall_girt_check,
            "longitudinal_bracing_design": self.longitudinal_bracing_design,
            "crane_beam_summary": self.current_crane_beam_summary() if crane_rating > 0.0 else None,
            "storage_results": self.storage_results,
            "storage_rail_design": self.storage_rail_design,
            "storage_panel_design": self.storage_panel_design,
            "foundation_summary": self.foundation_summary,
            "costing_summary": self.costing_summary,
            "optimiser_report": self.auto_size_report_text,
            "key_section_check_report_lines": report_lines,
            "key_analysis_report_lines": results_lines,
            "scope_and_limitations": [
                "PortalCalc is a preliminary engineering aid, not design certification.",
                "Suggestions must be trialled by solving a new PortalCalc model and reviewing every resulting check.",
                "Any check absent from this dossier must be treated as not checked.",
            ],
        }

    def building_load_summary_lines(self):
        """Results-text section for building-load mode (empty for a single frame)."""
        loads = getattr(self, "building_frame_loads", None)
        if not getattr(self, "building_load_mode", None) or not loads:
            return []
        from portalcalc.frame.frame_loading import gridline_label, governing_report
        frames = loads["frames"]
        gridlines = getattr(self, "building_load_gridlines", None) or [f.gridline for f in frames]
        checked, total = getattr(self, "building_check_counts", (0, 0))
        basis = {"building": "building loads", "legacy_2d": "building loads with 2D coefficients"}.get(loads["mode"], loads["mode"])
        lines = [
            "BUILDING LOADS",
            f"Basis            : {basis} (Kc {frames[0].kc_rule if loads['mode'] == 'building' else 'legacy'})",
            f"Gridlines checked: {gridline_label(gridlines)} of {len(frames)}",
            "Distinct loads   : " + "; ".join(gridline_label(group) for group in loads["distinct_frames"]
                                         if set(group) & set(gridlines)),
            f"Combinations     : {checked} of {total}" + (" (screened)" if checked != total else ""),
            "Diagram and selected-combination loads show the single-frame case.",
        ]
        lines.extend(governing_report(self.design_envelope_actions))
        lines.append("")
        return lines

    def building_service_results(self, building, frame_inputs, group_id=None):
        """v2 service evidence for one sizing group: its gridlines, sections, load sets and governing cases."""
        from portalcalc.frame.frame_loading import governing_actions
        from portalcalc.service.contract import building_with_frame_inputs
        loads = self.building_frame_loads or self.current_building_frame_loads()
        model = loads["model"]
        frames = {frame.gridline: frame for frame in loads["frames"]}
        groups = model.resolved_sizing_groups()
        group = next((g for g in groups if g.id == group_id), groups[0])
        sections = dict((frame_inputs or {}).get("sections") or {})
        geometry = (frame_inputs or {}).get("geometry") or {}
        checked, total = getattr(self, "building_check_counts", (None, None))
        sized_building = building_with_frame_inputs(building, frame_inputs)
        governing = governing_actions(self.design_envelope_actions,
                                      getattr(self, "building_combination_gridlines", {}) or {})
        for item in governing:
            item["sizing_group"] = group.id
        return {
            "load_basis": loads["mode"],
            "kc_rule": next(iter(frames.values())).kc_rule if loads["mode"] == "building" else "legacy",
            "all_gridlines": [{"id": gridline.id} for gridline in model.gridlines],
            "gridlines": [
                {"id": gridline.id, "y_mm": gridline.y_mm, "frame_type": gridline.frame_type,
                 "sizing_group": group.id, "tributary_mm": frames[gridline.id].tributary_mm,
                 "attachments": sorted(model.attachments_at(gridline.id)),
                 "sections": sections}
                for gridline in model.gridlines if gridline.id in group.gridlines
            ],
            "sizing_groups": [{
                "id": group.id, "gridlines": list(group.gridlines),
                "frame_type": model.gridlines[model.gridline_index(group.gridlines[0])].frame_type,
                "sections": sections, "truss_depth_mm": geometry.get("truss_depth_mm"),
            }],
            "distinct_load_sets": [list(ids) for ids in loads["distinct_frames"]],
            "combinations": {"checked": checked, "total": total,
                             "full_set_checked": checked is not None and checked == total},
            "governing": governing,
            "sized_building": sized_building,
        }

    def update_section_checks_text(
        self,
        left_column_profile,
        right_column_profile,
        internal_column_profile,
        top_profile,
        bottom_profile,
        web_profile,
        left_canopy_top_profile=None,
        left_canopy_bottom_profile=None,
        left_canopy_web_profile=None,
        left_lean_column_profile=None,
        right_canopy_top_profile=None,
        right_canopy_bottom_profile=None,
        right_canopy_web_profile=None,
        right_lean_column_profile=None,
    ):
        self.deflection_hover_overlays = {}
        self._active_deflection_hover_label = None
        self._deflection_hover_active = False
        if not self.design_envelope_actions:
            for label in [
                getattr(self, "web_split_label", None),
                getattr(self, "left_canopy_web_split_label", None),
                getattr(self, "right_canopy_web_split_label", None),
            ]:
                if label is not None:
                    label.setText("Single size")
                    label.setStyleSheet("font-weight: bold;")
            self.section_checks_box.setText("Generate and solve the frame to view section checks.")
            return

        def resolved_section_check(profile, envelope, group):
            return self.resolved_member_check(profile, envelope, group)

        def format_check_set(title, profile, envelope, group):
            result, actions, restraint, extra_notes = resolved_section_check(profile, envelope, group)
            doubled_zone_check = envelope.get("doubled_chord_zone") if group in {
                "TOP", "BOTTOM",
                "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM",
                "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM",
            } else None
            doubled_zone_ratio, _ = self.doubled_chord_zone_ratio(profile, envelope, group)
            governing_ratio = max(result.governing_ratio, doubled_zone_ratio)
            governing_check = "Doubled chord zone" if doubled_zone_ratio > result.governing_ratio else result.governing_check
            doubled_fraction = self.doubled_chord_length_fraction(group)
            doubled_length_ratio = doubled_fraction / MAX_DOUBLED_CHORD_LENGTH_FRACTION
            if doubled_length_ratio > governing_ratio:
                governing_ratio = doubled_length_ratio
                governing_check = "Doubled chord length limit"
            web_joint_ratio, web_joint_label = self.web_assignment_chord_joint_ratio(profile, group)
            if web_joint_ratio > governing_ratio:
                governing_ratio = web_joint_ratio
                governing_check = web_joint_label
            status = "OK" if self.design_utilization_ok(governing_ratio) else "NOT OK"
            clad_text = ""
            if restraint.get("clad") is not None:
                clad_text = f" | clad: {'yes' if restraint.get('clad') else 'no'}"
            crane_minor_text = " | crane minor length: base to crane height" if restraint.get("crane_minor") else ""
            storage_restraint_text = " | storage rail restraint" if restraint.get("storage_restraint") else ""
            ke = max(restraint.get("ke", 1.0), 1e-9)
            restraint_major = restraint.get("major_mm")
            restraint_minor = restraint.get("minor_mm")
            if restraint_major is None:
                restraint_major = (actions.effective_length_z_mm or 0.0) / ke if actions.effective_length_z_mm else 0.0
            if restraint_minor is None:
                restraint_minor = (actions.effective_length_y_mm or 0.0) / ke if actions.effective_length_y_mm else 0.0
            lines = [
                title,
                f"Section: {profile.name}",
                f"Restraint lengths: major {restraint_major:.0f} mm, minor {restraint_minor:.0f} mm | effective: major {actions.effective_length_z_mm or 0.0:.0f} mm, minor {actions.effective_length_y_mm or 0.0:.0f} mm | ke {restraint.get('ke', 1.0):.2f} | am {actions.moment_modifier:.2f} | kt/kl/kr {actions.kt:.2f}/{actions.kl:.2f}/{actions.kr:.2f}{clad_text}{crane_minor_text}{storage_restraint_text}",
                f"Governing: {governing_check} | ratio {governing_ratio:.3f} | {status}",
                "Check        | Demand      | Capacity    | Ratio  | Status | Source",
            ]
            if group == "INTERNAL_COLUMN":
                lines.append("Internal column bending includes AS4100 eccentricity moment Mx = Nc x D/2.")
            source_keys = {
                "compression": "compression_kn",
                "local_compression": "compression_kn",
                "tension": "tension_kn",
                "shear": "shear_kn",
                "bending": "moment_knm",
            }
            check_keys = ["compression"]
            if "local_compression" in result.checks:
                check_keys.append("local_compression")
            check_keys.extend(["tension", "shear", "bending", "combined"])
            for key in check_keys:
                check = result.checks[key]
                unit = f" {check.unit}" if check.unit else ""
                check_status = "OK" if self.design_utilization_ok(check.ratio) else "NOT OK"
                if restraint.get('flybraces'):
                    source = restraint['flybraces']['sources'][key]
                    source_text = f"{source['from_mm']:.0f}-{source['to_mm']:.0f} mm | {source['combo']} | am {source['am']:.3f}"
                elif key == "bending" and group in {"TOP", "BOTTOM", "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM", "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM"}:
                    source = envelope["compression_kn"]
                    source_member = source.get("coincident_moment_member", f"{group} average")
                    source_text = f"{source_member} | {source['combo']} | compression case"
                elif key in source_keys:
                    source = envelope[source_keys[key]]
                    source_text = f"Member {source['member']} | {source['combo']}"
                else:
                    source_text = "Envelope interaction"
                lines.append(
                    f"{check.label:<12} | {check.demand:>8.2f}{unit:<4} | "
                    f"{check.capacity:>8.2f}{unit:<4} | {check.ratio:>6.3f} | {check_status:<6} | {source_text}"
                )
            if doubled_zone_check:
                doubled_status = "OK" if self.design_utilization_ok(doubled_zone_ratio) else "NOT OK"
                lines.append(
                    f"{'Doubled zone':<12} | {doubled_zone_check['single_ratio']:>8.3f}     | "
                    f"{1.0:>8.2f}     | {doubled_zone_ratio:>6.3f} | {doubled_status:<6} | "
                    f"Member {doubled_zone_check['member']} | {doubled_zone_check['combo']} | after 50/50 chord sharing"
                )
            if group in {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}:
                connection = self.web_connection_check(profile, envelope, group)
                governing_source = envelope["compression_kn"]
                if envelope["tension_kn"]["value"] > governing_source["value"]:
                    governing_source = envelope["tension_kn"]
                for connection_check in connection.get("checks", []):
                    connection_status = "OK" if self.design_utilization_ok(connection_check["ratio"]) else "NOT OK"
                    source_text = f"Member {governing_source['member']} | {governing_source['combo']} | {connection['governing_chord']}"
                    lines.append(
                        f"{connection_check['label']:<12} | {connection_check['demand']:>8.2f} {connection_check['unit']:<3} | "
                        f"{connection_check['capacity']:>8.2f} {connection_check['unit']:<3} | {connection_check['ratio']:>6.3f} | {connection_status:<6} | {source_text}"
                    )
                if not connection.get("checks"):
                    connection_status = "OK" if self.design_utilization_ok(connection["ratio"]) else "NOT OK"
                    lines.append(
                        f"{connection['label']:<12} | {connection['demand']:>8.2f} {connection['unit']:<3} | "
                        f"{connection['capacity']:>8.2f} {connection['unit']:<3} | {connection['ratio']:>6.3f} | {connection_status:<6} | Connection geometry unavailable"
                    )
            notes = extra_notes + [check.notes for check in result.checks.values() if check.notes]
            if group in {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}:
                notes.append(f"Chord-web connection assumes a {WEB_CONNECTION_CFW_LEG_MM:.0f} mm continuous fillet weld all around, using the larger of web compression and tension demand.")
                notes.extend(check.get("notes", "") for check in connection.get("checks", []) if check.get("notes"))
            if notes:
                lines.append("Notes:")
                for note in dict.fromkeys(notes):
                    lines.append(f"- {note}")
            return lines

        def web_assignment_buckets(base_profile, group):
            if not self.web_assignment_basis_matches(group):
                return []
            assignments = (self.auto_web_profile_assignments or {}).get(group, {})
            demand_items = (self.design_envelope_actions.get("web_member_demands", {}) or {}).get(group, {})
            if not assignments or not demand_items:
                return []
            chord_profiles = self.web_connection_group_profiles(group)
            invalid_assignment = False

            def merge_bucket_envelope(bucket, envelope):
                for key in ["compression_kn", "tension_kn", "shear_kn", "moment_knm"]:
                    if envelope[key]["value"] > bucket["envelope"][key]["value"]:
                        bucket["envelope"][key] = envelope[key]
                axial_value = max(envelope["compression_kn"]["value"], envelope["tension_kn"]["value"])
                if axial_value > bucket["connection_axial_value"]:
                    bucket["connection_axial_value"] = axial_value
                    bucket["envelope"]["connection"] = envelope.get("connection", {})

            buckets = {}
            for member_id, demand in demand_items.items():
                if not self.is_output_web_demand(demand):
                    continue
                assigned_name = assignments.get(int(member_id)) or assignments.get(member_id)
                profile = self.catalog.get(assigned_name) if assigned_name else base_profile
                if profile is None:
                    continue
                if not self.web_profile_fits_chords(profile, group, chord_profiles=chord_profiles):
                    invalid_assignment = True
                    continue
                envelope = self.web_member_demand_envelope(demand)
                ratio, check_label, _ = self.member_and_connection_ratio(profile, envelope, group)
                bucket = buckets.setdefault(profile.name, {
                    "profile": profile,
                    "count": 0,
                    "type_counts": {},
                    "envelope": {
                        "compression_kn": {"value": -1.0, "combo": "-", "member": "-", "group": "-", "length_mm": 0.0},
                        "tension_kn": {"value": -1.0, "combo": "-", "member": "-", "group": "-", "length_mm": 0.0},
                        "shear_kn": {"value": -1.0, "combo": "-", "member": "-", "group": "-", "length_mm": 0.0},
                        "moment_knm": {"value": -1.0, "combo": "-", "member": "-", "group": "-", "length_mm": 0.0},
                        "connection": {},
                    },
                    "connection_axial_value": -1.0,
                    "worst_ratio": -1.0,
                    "worst_check": "-",
                    "worst_envelope": envelope,
                    "worst_demand": demand,
                })
                bucket["count"] += 1
                bucket["type_counts"]["webs"] = bucket["type_counts"].get("webs", 0) + 1
                merge_bucket_envelope(bucket, envelope)
                if ratio > bucket["worst_ratio"]:
                    bucket["worst_ratio"] = ratio
                    bucket["worst_check"] = check_label
                    bucket["worst_envelope"] = envelope
                    bucket["worst_demand"] = demand
            if invalid_assignment:
                (self.auto_web_profile_assignments or {}).pop(group, None)
                (getattr(self, "auto_web_assignment_basis", {}) or {}).pop(group, None)
                (getattr(self, "auto_web_joint_doubler_sides", {}) or {}).pop(group, None)
                (getattr(self, "web_split_failures", {}) or {}).pop(group, None)
                return []
            return sorted(
                buckets.values(),
                key=lambda item: (item["profile"].A, item["profile"].name),
            )

        def bucket_count_text(bucket):
            count = bucket["count"]
            return f"{count} webs" if count != 1 else "1 web"

        def update_web_split_label(label_widget, base_profile, group):
            buckets = web_assignment_buckets(base_profile, group)
            if not buckets:
                failure = (getattr(self, "web_split_failures", {}) or {}).get(group)
                if failure:
                    base_ratio, _ = self.web_group_single_size_ratio(group, base_profile)
                    if not self.design_utilization_ok(base_ratio):
                        label_widget.setText(failure)
                        label_widget.setStyleSheet("font-weight: bold; color: #b42318;")
                        return
                    (getattr(self, "web_split_failures", {}) or {}).pop(group, None)
                demand_items = (self.design_envelope_actions.get("web_member_demands", {}) or {}).get(group, {})
                output_demands = [demand for demand in demand_items.values() if self.is_output_web_demand(demand)]
                if base_profile is not None and output_demands:
                    label_widget.setText(f"{base_profile.name}: {self.web_member_count_text(output_demands)}")
                else:
                    label_widget.setText("Single size")
                label_widget.setStyleSheet("font-weight: bold;")
                return
            parts = [f"{bucket['profile'].name}: {bucket_count_text(bucket)}" for bucket in buckets]
            label_widget.setText("Adopted split: " + "; ".join(parts))
            label_widget.setStyleSheet("font-weight: bold; color: #1b7f3a;")

        def format_web_assignment_check_set(title, base_profile, group):
            buckets = web_assignment_buckets(base_profile, group)
            if not buckets:
                return format_check_set(title, base_profile, envelopes[group], group)
            overall = max(buckets, key=lambda item: item["worst_ratio"])
            overall_status = "OK" if self.design_utilization_ok(overall["worst_ratio"]) else "NOT OK"
            lines = [
                title,
                f"Auto-sized split active: {len(buckets)} assigned web sizes | governing {overall['profile'].name} | {overall['worst_check']} {overall['worst_ratio']:.3f} | {overall_status}",
            ]
            for bucket in buckets:
                lines.append("")
                lines.extend(format_check_set(
                    f"{bucket['profile'].name} ({bucket_count_text(bucket)})",
                    bucket["profile"],
                    bucket["envelope"],
                    group,
                ))
            return lines

        def section_result(profile, envelope, group):
            buckets = web_assignment_buckets(profile, group) if group in {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"} else []
            if buckets:
                overall = max(buckets, key=lambda item: item["worst_ratio"])
                notes = [f"Auto-sized split active across {len(buckets)} assigned web sizes."]
                return overall["profile"], notes, overall["worst_ratio"], overall["worst_check"]
            result, _, _, notes = resolved_section_check(profile, envelope, group)
            extra_ratio = 0.0
            extra_label = ""
            doubled_ratio, doubled_label = self.doubled_chord_zone_ratio(profile, envelope, group)
            if doubled_ratio > extra_ratio:
                extra_ratio = doubled_ratio
                extra_label = doubled_label
            if group in {
                "TOP", "BOTTOM", "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM",
                "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM",
            }:
                length_ratio = self.doubled_chord_length_fraction(group) / MAX_DOUBLED_CHORD_LENGTH_FRACTION
                if length_ratio > extra_ratio:
                    extra_ratio = length_ratio
                    extra_label = "Doubled chord length limit"
                web_joint_ratio, web_joint_label = self.web_assignment_chord_joint_ratio(profile, group)
                if web_joint_ratio > extra_ratio:
                    extra_ratio = web_joint_ratio
                    extra_label = web_joint_label
            if group in {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}:
                connection = self.web_connection_check(profile, envelope, group)
                extra_ratio = connection["ratio"]
                extra_label = connection["label"]
            return result, notes, extra_ratio, extra_label

        def update_status_label(label, result_info):
            result, notes, extra_ratio, extra_label = result_info
            if not hasattr(result, "governing_ratio"):
                governing_ratio = extra_ratio
                governing_check = extra_label
                status = "OK" if self.design_utilization_ok(governing_ratio) else "NOT OK"
                color = "#1b7f3a" if governing_ratio < 0.90 else "#a87900" if governing_ratio < 0.95 else "#c45a00" if self.design_utilization_ok(governing_ratio) else "#b42318"
                label.setText(f"{status} - auto-sized split active, max utilisation {governing_ratio:.3f} ({governing_check})")
                label.setStyleSheet(f"font-weight: bold; color: {color};")
                return
            governing_ratio = max(result.governing_ratio, extra_ratio)
            governing_check = extra_label if extra_ratio > result.governing_ratio else result.governing_check
            status = "OK" if self.design_utilization_ok(governing_ratio) else "NOT OK"
            if notes and self.design_utilization_ok(governing_ratio):
                color = "#a87900"
            elif not self.design_utilization_ok(governing_ratio):
                color = "#b42318"
            elif governing_ratio >= 0.95:
                color = "#c45a00"
            elif governing_ratio >= 0.90:
                color = "#a87900"
            else:
                color = "#1b7f3a"
            suffix = " - additional flybracing required" if notes else ""
            label.setText(f"{status}{suffix} - max utilisation {governing_ratio:.3f} ({governing_check})")
            label.setStyleSheet(f"font-weight: bold; color: {color};")

        def column_deflection_check():
            item = self.design_envelope_actions.get("sls_column_top_deflection", {})
            height = item.get("height_mm") or self.serviceability_height_mm or 0.0
            divisor = self.eave_deflection_divisor()
            limit = height / divisor if height > 0.0 else 0.0
            demand = max(item.get("value", -1.0), 0.0)
            ratio = demand / limit if limit > 1e-12 else float("inf")
            status = "OK" if ratio <= 1.0 else "NOT OK"
            return demand, limit, ratio, status, divisor, item

        def crane_rail_deflection_check():
            item = self.design_envelope_actions.get("sls_crane_rail_deflection", {})
            height = item.get("height_mm") or 0.0
            divisor = self.serviceability_limit_value("crane_rail_height")
            limit = height / divisor if height > 0.0 else 0.0
            demand = max(item.get("value", -1.0), 0.0)
            ratio = demand / limit if limit > 1e-12 else float("inf")
            status = "OK" if ratio <= 1.0 else "NOT OK"
            return demand, limit, ratio, status, divisor, item

        def crane_gauge_change_check():
            item = self.design_envelope_actions.get("sls_crane_gauge_change", {})
            limit = item.get("limit_mm") or CRANE_GAUGE_CHANGE_LIMIT_MM
            demand = max(item.get("value", -1.0), 0.0)
            ratio = demand / limit if limit > 1e-12 else float("inf")
            return demand, limit, ratio, "OK" if ratio <= 1.0 else "NOT OK", item

        def column_midspan_ws_deflection_check(group=None):
            if group is None:
                item = self.design_envelope_actions.get("sls_column_midspan_ws_deflection", {})
            else:
                item = self.design_envelope_actions.get("sls_column_midspan_ws_deflections", {}).get(group, {})
            height = item.get("height_mm") or self.serviceability_height_mm or 0.0
            divisor = self.column_deflection_divisor()
            limit = height / divisor if height > 0.0 else 0.0
            demand = max(item.get("value", -1.0), 0.0)
            ratio = demand / limit if limit > 1e-12 else float("inf")
            status = "OK" if ratio <= 1.0 else "NOT OK"
            return demand, limit, ratio, status, divisor, item

        def column_midspan_ws_deflection_line(group):
            demand, limit, ratio, status, divisor, item = column_midspan_ws_deflection_check(group)
            if item.get("value", -1.0) < 0.0:
                return ""
            return (
                f"Column midspan deflection Ws: {demand:.3f} mm <= {limit:.3f} mm "
                f"(H/{divisor:.0f}) | Member {item.get('member', '-')} | "
                f"{item.get('combo', '-')} | utilisation {ratio:.3f} | {status}"
            )

        def truss_deflection_checks():
            span = self.serviceability_span_mm or 0.0
            checks = {}
            items = self.design_envelope_actions.get("sls_truss_midspan_deflections", {})
            for component, divisor in self.midspan_component_deflection_divisors().items():
                item = items.get(component, {})
                demand = max(item.get("value", -1.0), 0.0)
                checks[component] = self.midspan_component_deflection_check(
                    component, demand, span, divisor, item,
                )
            combined_divisor = self.midspan_deflection_divisor()
            combined_demand = sum(checks.get(component, {}).get("demand", 0.0) for component in ("G", "Q"))
            combined_limit = span / combined_divisor if span > 0.0 else 0.0
            combined_ratio = combined_demand / combined_limit if combined_limit > 1e-12 else float("inf")
            checks["G+Q"] = {
                "demand": combined_demand,
                "limit": combined_limit,
                "ratio": combined_ratio,
                "status": "REFERENCE",
                "divisor": combined_divisor,
                "item": {"combo": "G + Q component envelope"},
                "component": "G+Q",
                "governing": False,
                "precamber_required_mm": 0.0,
            }
            return checks

        def side_midspan_deflection_checks():
            checks = {}
            items = self.design_envelope_actions.get("sls_side_midspan_deflections", {})
            for key, side_result in items.items():
                span = side_result.get("length_mm") or 0.0
                components = side_result.get("components", {})
                label = side_result.get("label", key)
                for component, divisor in self.midspan_component_deflection_divisors().items():
                    item = components.get(component, {})
                    demand = max(item.get("value", -1.0), 0.0)
                    checks[f"{key} {component}"] = {
                        **self.midspan_component_deflection_check(component, demand, span, divisor, item),
                        "label": f"{label} {component}",
                    }
                combined_divisor = self.midspan_deflection_divisor()
                combined_demand = sum(max(components.get(component, {}).get("value", -1.0), 0.0) for component in ("G", "Q"))
                combined_limit = span / combined_divisor if span > 0.0 else 0.0
                combined_ratio = combined_demand / combined_limit if combined_limit > 1e-12 else float("inf")
                checks[f"{key} G+Q"] = {
                    "label": f"{label} G+Q",
                    "demand": combined_demand,
                    "limit": combined_limit,
                    "ratio": combined_ratio,
                    "status": "REFERENCE",
                    "divisor": combined_divisor,
                    "item": {"combo": "G + Q component envelope"},
                    "component": "G+Q",
                    "governing": False,
                    "precamber_required_mm": 0.0,
                }
            return checks

        def canopy_tip_deflection_checks():
            checks = {}
            divisor = self.canopy_tip_deflection_divisor()
            items = self.design_envelope_actions.get("sls_canopy_tip_deflections", {})
            for key, item in items.items():
                length = item.get("length_mm") or 0.0
                limit = length / divisor if length > 0.0 else 0.0
                demand = max(item.get("value", -1.0), 0.0)
                ratio = demand / limit if limit > 1e-12 else float("inf")
                status = "OK" if ratio <= 1.0 else "NOT OK"
                checks[key] = {
                    "demand": demand,
                    "limit": limit,
                    "ratio": ratio,
                    "status": status,
                    "divisor": divisor,
                    "item": item,
                }
            return checks

        envelopes = self.design_envelope_actions
        is_rafter = getattr(self, "frame_system_combo", None) is not None and self.frame_system_combo.currentText() == "Rafter"
        update_web_split_label(self.web_split_label, web_profile, "WEB")
        if left_canopy_web_profile is not None:
            update_web_split_label(self.left_canopy_web_split_label, left_canopy_web_profile, "LEFT_CANOPY_WEB")
        if right_canopy_web_profile is not None:
            update_web_split_label(self.right_canopy_web_split_label, right_canopy_web_profile, "RIGHT_CANOPY_WEB")
        update_status_label(self.left_column_check_label, section_result(left_column_profile, envelopes["LEFT_COLUMN"], "LEFT_COLUMN"))
        update_status_label(self.right_column_check_label, section_result(right_column_profile, envelopes["RIGHT_COLUMN"], "RIGHT_COLUMN"))
        if envelopes["INTERNAL_COLUMN"]["compression_kn"]["value"] >= 0.0:
            update_status_label(self.internal_column_check_label, section_result(internal_column_profile, envelopes["INTERNAL_COLUMN"], "INTERNAL_COLUMN"))
        else:
            self.internal_column_check_label.setText("Not checked")
            self.internal_column_check_label.setStyleSheet("font-weight: bold;")
        update_status_label(self.top_chord_check_label, section_result(top_profile, envelopes["TOP"], "TOP"))
        if is_rafter:
            self.bottom_chord_check_label.setText("Not checked")
            self.bottom_chord_check_label.setStyleSheet("font-weight: bold;")
            self.web_check_label.setText("Not checked")
            self.web_check_label.setStyleSheet("font-weight: bold;")
        else:
            update_status_label(self.bottom_chord_check_label, section_result(bottom_profile, envelopes["BOTTOM"], "BOTTOM"))
        purlin_check = self.purlin_check or {}
        if purlin_check:
            match = purlin_check.get("match", {})
            local_match = purlin_check.get("local_match", {})
            purlin_status = match.get("status", "NOT CHECKED")
            local_status = local_match.get("status", "NOT CHECKED")
            combined_status = "OK" if purlin_status == "OK" and local_status == "OK" else "NOT OK"
            purlin_color = "#1b7f3a" if combined_status == "OK" else "#b42318"
            if combined_status == "OK":
                general_rows = int(match.get("bridging_rows", 0) or 0)
                local_rows = int(local_match.get("bridging_rows", 0) or 0)
                edge_rows = int(purlin_check.get("edge_rows", 0) or 0)
                edge_text = (
                    f"; first {edge_rows} edge rows "
                    f"{self.metroll_section_display(local_match.get('section', '-'), purlin_check.get('span_type', ''))} "
                    f"({local_rows}-row)"
                    if local_match.get("section") != match.get("section") or local_rows != general_rows
                    else ""
                )
                self.purlin_check_label.setText(
                    f"OK - {self.metroll_section_display(match.get('section', '-'), purlin_check.get('span_type', ''))} "
                    f"({general_rows}-row) @ {purlin_check.get('spacing_mm', 0.0):.0f} mm, "
                    f"{purlin_check.get('purlin_rows', 0)} rows{edge_text}"
                )
            else:
                self.purlin_check_label.setText(
                    f"NOT OK - general "
                    f"{self.metroll_section_display(match.get('section'), purlin_check.get('span_type', '')) or match.get('message', '-')}, "
                    f"edge {self.metroll_section_display(local_match.get('section'), purlin_check.get('span_type', '')) or local_match.get('message', '-')}"
                )
            self.purlin_check_label.setStyleSheet(f"font-weight: bold; color: {purlin_color};")
        else:
            self.purlin_check_label.setText("Not checked")
            self.purlin_check_label.setStyleSheet("font-weight: bold;")
        if getattr(self, "purlin_bridging_summary_label", None):
            self.purlin_bridging_summary_label.setText(self.purlin_bridging_summary_text(purlin_check))
        wall_girt_check = self.wall_girt_check or {}
        if wall_girt_check:
            match = wall_girt_check.get("match", {})
            girt_status = match.get("status", "NOT CHECKED")
            girt_color = "#1b7f3a" if girt_status == "OK" else "#b42318"
            if girt_status == "OK":
                self.wall_girt_check_label.setText(
                    f"OK - {self.metroll_section_display(match.get('section', '-'), wall_girt_check.get('span_type', ''))} "
                    f"({match.get('tables', '-')})"
                )
            else:
                self.wall_girt_check_label.setText(f"{girt_status} - {match.get('message', 'No match')}")
            self.wall_girt_check_label.setStyleSheet(f"font-weight: bold; color: {girt_color};")
        else:
            self.wall_girt_check_label.setText("Not checked")
            self.wall_girt_check_label.setStyleSheet("font-weight: bold;")
        try:
            if not self.crane_rating_active_from_ui():
                raise ValueError("No crane rating")
            crane_summary = self.current_crane_beam_summary()
            crane_status = "OK" if max(crane_summary["capacity_utilization"], crane_summary["deflection_utilization"]) <= 1.0 else "NOT OK"
            crane_color = "#1b7f3a" if crane_status == "OK" else "#b42318"
            self.crane_beam_check_label.setText(
                f"{crane_status} - {crane_summary['section']} "
                f"({crane_summary['weight_kg_m']:.1f} kg/m, span {crane_summary['span_mm']:.0f} mm)"
            )
            self.crane_beam_check_label.setStyleSheet(f"font-weight: bold; color: {crane_color};")
        except (KeyError, ValueError, ZeroDivisionError):
            crane_summary = None
            self.crane_beam_check_label.setText("Not required - no crane rating")
            self.crane_beam_check_label.setStyleSheet("font-weight: bold;")
        storage_results = getattr(self, "storage_results", None)
        storage_rail_design = getattr(self, "storage_rail_design", None)
        storage_stub_design = getattr(self, "storage_stub_design", None)
        storage_panel_design = getattr(self, "storage_panel_design", None)
        if storage_results is not None and storage_results.inputs.enabled:
            if storage_rail_design:
                rail_status = "OK" if storage_rail_design["passes"] else "NOT OK"
                rail_color = "#1b7f3a" if rail_status == "OK" else "#b42318"
                self.storage_rail_check_label.setText(
                    f"{rail_status} - {storage_rail_design['profile'].name}, "
                    f"cap {storage_rail_design['capacity_utilization']:.2f}, defl {storage_rail_design['deflection_utilization']:.2f}"
                )
                self.storage_rail_check_label.setStyleSheet(f"font-weight: bold; color: {rail_color};")
            else:
                self.storage_rail_check_label.setText("NOT OK - no rail section found")
                self.storage_rail_check_label.setStyleSheet("font-weight: bold; color: #b42318;")
            if is_centre_stub_storage_mode(storage_results.inputs):
                if storage_stub_design:
                    stub_status = "OK" if storage_stub_design["passes"] else "NOT OK"
                    stub_color = "#1b7f3a" if stub_status == "OK" else "#b42318"
                    self.storage_stub_check_label.setText(
                        f"{stub_status} - {storage_stub_design['profile'].name}, "
                        f"cap {storage_stub_design['capacity_utilization']:.2f}, defl {storage_stub_design['deflection_utilization']:.2f}"
                    )
                    self.storage_stub_check_label.setStyleSheet(f"font-weight: bold; color: {stub_color};")
                else:
                    self.storage_stub_check_label.setText("NOT OK - no stub section found")
                    self.storage_stub_check_label.setStyleSheet("font-weight: bold; color: #b42318;")
            else:
                self.storage_stub_check_label.setText("Not required - current storage mode")
                self.storage_stub_check_label.setStyleSheet("font-weight: bold;")
            if storage_panel_design:
                panel_status = "OK" if storage_panel_design.utilization <= 1.0 else "NOT OK"
                panel_color = "#1b7f3a" if panel_status == "OK" else "#b42318"
                self.storage_panel_check_label.setText(
                    f"{panel_status} - {storage_panel_design.thickness_mm:.0f} mm, "
                    f"+M {storage_panel_design.positive_face.reinforcement_label}, "
                    f"-M {storage_panel_design.negative_face.reinforcement_label}"
                )
                self.storage_panel_check_label.setStyleSheet(f"font-weight: bold; color: {panel_color};")
            else:
                self.storage_panel_check_label.setText("NOT OK - no passing panel option")
                self.storage_panel_check_label.setStyleSheet("font-weight: bold; color: #b42318;")
        else:
            self.storage_rail_check_label.setText("Not required - storage disabled")
            self.storage_rail_check_label.setStyleSheet("font-weight: bold;")
            self.storage_stub_check_label.setText("Not required - storage disabled")
            self.storage_stub_check_label.setStyleSheet("font-weight: bold;")
            self.storage_panel_check_label.setText("Not required - storage disabled")
            self.storage_panel_check_label.setStyleSheet("font-weight: bold;")
        if not is_rafter and envelopes["WEB"]["compression_kn"]["value"] >= 0.0:
            update_status_label(self.web_check_label, section_result(web_profile, envelopes["WEB"], "WEB"))
        elif not is_rafter:
            self.web_check_label.setText("Not checked")
            self.web_check_label.setStyleSheet("font-weight: bold;")
        if left_canopy_top_profile is not None and envelopes["LEFT_CANOPY_TOP"]["compression_kn"]["value"] >= 0.0:
            update_status_label(self.left_canopy_top_check_label, section_result(left_canopy_top_profile, envelopes["LEFT_CANOPY_TOP"], "LEFT_CANOPY_TOP"))
        if left_canopy_bottom_profile is not None and envelopes["LEFT_CANOPY_BOTTOM"]["compression_kn"]["value"] >= 0.0:
            update_status_label(self.left_canopy_bottom_check_label, section_result(left_canopy_bottom_profile, envelopes["LEFT_CANOPY_BOTTOM"], "LEFT_CANOPY_BOTTOM"))
        if left_canopy_web_profile is not None and envelopes["LEFT_CANOPY_WEB"]["compression_kn"]["value"] >= 0.0:
            update_status_label(self.left_canopy_web_check_label, section_result(left_canopy_web_profile, envelopes["LEFT_CANOPY_WEB"], "LEFT_CANOPY_WEB"))
        if left_lean_column_profile is not None and envelopes["LEFT_LEAN_COLUMN"]["compression_kn"]["value"] >= 0.0:
            update_status_label(self.left_lean_column_check_label, section_result(left_lean_column_profile, envelopes["LEFT_LEAN_COLUMN"], "LEFT_LEAN_COLUMN"))
        if right_canopy_top_profile is not None and envelopes["RIGHT_CANOPY_TOP"]["compression_kn"]["value"] >= 0.0:
            update_status_label(self.right_canopy_top_check_label, section_result(right_canopy_top_profile, envelopes["RIGHT_CANOPY_TOP"], "RIGHT_CANOPY_TOP"))
        if right_canopy_bottom_profile is not None and envelopes["RIGHT_CANOPY_BOTTOM"]["compression_kn"]["value"] >= 0.0:
            update_status_label(self.right_canopy_bottom_check_label, section_result(right_canopy_bottom_profile, envelopes["RIGHT_CANOPY_BOTTOM"], "RIGHT_CANOPY_BOTTOM"))
        if right_canopy_web_profile is not None and envelopes["RIGHT_CANOPY_WEB"]["compression_kn"]["value"] >= 0.0:
            update_status_label(self.right_canopy_web_check_label, section_result(right_canopy_web_profile, envelopes["RIGHT_CANOPY_WEB"], "RIGHT_CANOPY_WEB"))
        if right_lean_column_profile is not None and envelopes["RIGHT_LEAN_COLUMN"]["compression_kn"]["value"] >= 0.0:
            update_status_label(self.right_lean_column_check_label, section_result(right_lean_column_profile, envelopes["RIGHT_LEAN_COLUMN"], "RIGHT_LEAN_COLUMN"))
        deflection_demand, deflection_limit, deflection_ratio, deflection_status, deflection_divisor, deflection_item = column_deflection_check()
        crane_deflection_demand, crane_deflection_limit, crane_deflection_ratio, crane_deflection_status, crane_deflection_divisor, crane_deflection_item = crane_rail_deflection_check()
        mid_column_demand, mid_column_limit, mid_column_ratio, mid_column_status, mid_column_divisor, mid_column_item = column_midspan_ws_deflection_check()
        column_deflection_statuses = [deflection_status]
        column_deflection_parts = [
            f"Top {deflection_status} {deflection_ratio:.3f} (H/{deflection_divisor:.0f})",
        ]
        # Report only (greyed, not in the pass/fail): the frame alone gets no
        # help from its neighbours; crane rail racking is a 3D check.
        crane_racking_note = ""
        if crane_deflection_item.get("value", -1.0) >= 0.0:
            crane_racking_note = (
                f"crane racking, frame alone {crane_deflection_demand:.2f}/{crane_deflection_limit:.2f} mm = "
                f"{crane_deflection_ratio:.3f} (H/{crane_deflection_divisor:.0f}) - report only, checked in 3D"
            )
        gauge_demand, gauge_limit, gauge_ratio, gauge_status, gauge_item = crane_gauge_change_check()
        if gauge_item.get("value", -1.0) >= 0.0:
            column_deflection_statuses.append(gauge_status)
            column_deflection_parts.append(
                f"crane gauge {gauge_status} {gauge_demand:.2f}/{gauge_limit:.0f} mm = {gauge_ratio:.3f}"
            )
        if mid_column_item.get("value", -1.0) >= 0.0:
            column_deflection_statuses.append(mid_column_status)
            column_deflection_parts.append(f"mid Ws {mid_column_status} {mid_column_ratio:.3f} (H/{mid_column_divisor:.0f})")

        def deflection_overlay_candidate(kind, item, demand, limit, ratio, status, divisor, direction="horizontal"):
            return {
                "kind": kind,
                "item": dict(item),
                "value_mm": float(demand),
                "signed_value_mm": float(item.get("signed_value", demand)),
                "limit_mm": float(limit),
                "ratio": float(ratio),
                "status": status,
                "divisor": float(divisor),
                "combo": item.get("combo", "-"),
                "group": item.get("group", ""),
                "direction": direction,
            }

        def register_deflection_hover(label, overlay, limit_name):
            if label is None or not overlay or overlay.get("item", {}).get("value", -1.0) < 0.0:
                return
            self.deflection_hover_overlays[label] = overlay
            item = overlay.get("item", {})
            location = (
                f"Node {item.get('node')}" if item.get("node") not in (None, "-")
                else f"Member {item.get('member', '-')}"
            )
            label.setToolTip("\n".join([
                "Governing deflection",
                f"Check: {overlay['kind']}",
                f"Location: {location}",
                f"Demand: {overlay['value_mm']:.3f} mm",
                f"Limit: {overlay['limit_mm']:.3f} mm ({limit_name}/{overlay['divisor']:.0f})",
                f"Utilisation: {overlay['ratio']:.3f} - {overlay['status']}",
                f"Load case: {overlay['combo']}",
                "",
                "Keep hovering to preview the exaggerated governing shape on the Geometry view.",
            ]))
            label.setToolTipDuration(30000)

        deflection_candidates = [
            deflection_overlay_candidate(
                "Top eave sway", deflection_item, deflection_demand, deflection_limit,
                deflection_ratio, deflection_status, deflection_divisor,
            )
        ]
        if mid_column_item.get("value", -1.0) >= 0.0:
            deflection_candidates.append(
                deflection_overlay_candidate(
                    "Column midspan sway", mid_column_item, mid_column_demand,
                    mid_column_limit, mid_column_ratio, mid_column_status, mid_column_divisor,
                )
            )
        self.governing_column_deflection_overlay = max(
            deflection_candidates,
            key=lambda candidate: candidate["ratio"],
        )
        governing_deflection = self.governing_column_deflection_overlay
        register_deflection_hover(self.column_deflection_check_label, governing_deflection, "H")
        governing_item = governing_deflection["item"]
        governing_group = str(governing_deflection.get("group") or "").replace("_", " ").title()
        governing_location = governing_group or (
            f"Node {governing_item.get('node')}" if governing_item.get("node") not in (None, "-")
            else f"Member {governing_item.get('member', '-')}"
        )
        self.column_deflection_check_label.setToolTip("\n".join([
            "Governing frame deflection",
            f"Check: {governing_deflection['kind']}",
            f"Location: {governing_location}",
            f"Demand: {governing_deflection['value_mm']:.3f} mm",
            f"Limit: {governing_deflection['limit_mm']:.3f} mm (H/{governing_deflection['divisor']:.0f})",
            f"Utilisation: {governing_deflection['ratio']:.3f} - {governing_deflection['status']}",
            f"Load case: {governing_deflection['combo']}",
            "",
            "Keep hovering to preview the exaggerated governing shape on the Geometry view.",
        ]))
        self.column_deflection_check_label.setToolTipDuration(30000)
        status_colour = '#b42318' if 'NOT OK' in column_deflection_statuses else '#1b7f3a'
        if crane_racking_note:
            self.set_label_rich_text(self.column_deflection_check_label, True)
            self.column_deflection_check_label.setText(
                f"<span style='color: {status_colour};'>{html.escape('; '.join(column_deflection_parts))}</span>"
                f"<span style='color: #8a8f98; font-weight: normal;'>; {html.escape(crane_racking_note)}</span>"
            )
        else:
            self.set_label_rich_text(self.column_deflection_check_label, False)
            self.column_deflection_check_label.setText("; ".join(column_deflection_parts))
        self.column_deflection_check_label.setStyleSheet(f"font-weight: bold; color: {status_colour};")
        truss_deflection_checks_by_component = truss_deflection_checks()
        side_deflection_checks_by_component = side_midspan_deflection_checks()
        all_truss_deflection_checks = {
            **truss_deflection_checks_by_component,
            **side_deflection_checks_by_component,
        }
        governing_truss_deflection_checks = {
            key: item
            for key, item in all_truss_deflection_checks.items()
            if item.get("governing", True)
        }
        governing_truss_deflection = max(
            governing_truss_deflection_checks.items(),
            default=("-", {"ratio": 0.0, "status": "OK", "demand": 0.0, "limit": 0.0, "divisor": 1.0}),
            key=lambda entry: entry[1]["ratio"],
        )
        precamber_advisories = [
            (key, item)
            for key, item in all_truss_deflection_checks.items()
            if item.get("component") == "G" and item.get("precamber_required_mm", 0.0) > 1e-9
        ]
        truss_deflection_status = "NOT OK" if any(item["status"] == "NOT OK" for item in governing_truss_deflection_checks.values()) else "OK"
        truss_deflection_color = "#b42318" if truss_deflection_status == "NOT OK" else "#a87900" if precamber_advisories else "#1b7f3a"
        governing_component, governing_item = governing_truss_deflection
        truss_deflection_label = (
            f"{truss_deflection_status} - gov {governing_component} {governing_item['demand']:.2f} / {governing_item['limit']:.2f} mm = {governing_item['ratio']:.3f} (Span/{governing_item['divisor']:.0f})"
        )
        if precamber_advisories:
            precamber_key, precamber_item = max(
                precamber_advisories,
                key=lambda entry: entry[1]["precamber_required_mm"],
            )
            truss_deflection_label += (
                f" | PRECAMBER REQUIRED: min {precamber_item['precamber_required_mm']:.1f} mm ({precamber_key})"
            )
        self.truss_deflection_check_label.setText(truss_deflection_label)
        self.truss_deflection_check_label.setStyleSheet(f"font-weight: bold; color: {truss_deflection_color};")
        register_deflection_hover(
            self.truss_deflection_check_label,
            deflection_overlay_candidate(
                f"Truss midspan {governing_component}", governing_item.get("item", {}),
                governing_item["demand"], governing_item["limit"], governing_item["ratio"],
                governing_item["status"], governing_item["divisor"], "vertical",
            ),
            "Span",
        )

        def truss_serviceability_line(component):
            item = truss_deflection_checks_by_component[component]
            combo_item = item.get("item", {})
            if component == "Ws":
                label = "Midspan wind deflection Ws"
            elif component == "G+Q":
                label = "Combined midspan sag G+Q"
            else:
                label = f"Midspan sag {component}"
            line = (
                f"{label}: {item['demand']:.3f} mm <= {item['limit']:.3f} mm "
                f"(Span/{item['divisor']:.0f}) | {combo_item.get('combo', '-')} | "
                f"utilisation {item['ratio']:.3f} | {item['status']}"
            )
            if component == "G":
                if item.get("precamber_required_mm", 0.0) > 1e-9:
                    line += f" | minimum precamber {item['precamber_required_mm']:.3f} mm"
                line += " | fabrication advisory; excluded from governing sizing"
            elif component == "G+Q":
                line += " | reference only; G excluded from governing sizing"
            return line

        side_midspan_serviceability_lines = []
        for key, item in side_deflection_checks_by_component.items():
            combo_item = item.get("item", {})
            line = (
                f"{item.get('label', key)} deflection: {item['demand']:.3f} mm <= {item['limit']:.3f} mm "
                f"(Span/{item['divisor']:.0f}) | {combo_item.get('combo', '-')} | "
                f"utilisation {item['ratio']:.3f} | {item['status']}"
            )
            if item.get("component") == "G":
                if item.get("precamber_required_mm", 0.0) > 1e-9:
                    line += f" | minimum precamber {item['precamber_required_mm']:.3f} mm"
                line += " | fabrication advisory; excluded from governing sizing"
            elif item.get("component") == "G+Q":
                line += " | reference only; G excluded from governing sizing"
            side_midspan_serviceability_lines.append(line)

        canopy_tip_checks_by_side = canopy_tip_deflection_checks()
        canopy_tip_serviceability_lines = []
        for item in canopy_tip_checks_by_side.values():
            combo_item = item.get("item", {})
            reference_text = combo_item.get("reference", "anchor vertical")
            canopy_tip_serviceability_lines.append(
                f"{combo_item.get('label', 'Canopy tip deflection')}: {item['demand']:.3f} mm <= {item['limit']:.3f} mm "
                f"(L/{item['divisor']:.0f}) | {combo_item.get('combo', '-')} | ref {reference_text} | "
                f"utilisation {item['ratio']:.3f} | {item['status']}"
            )

        def update_serviceability_status_label(label, item, prefix, limit_label="Span"):
            if item is None:
                label.setText("Not checked")
                label.setStyleSheet("font-weight: bold;")
                return
            status = item.get("status", "NOT OK")
            color = "#1b7f3a" if status == "OK" else "#b42318"
            label.setText(
                f"{status} - {prefix} {item['demand']:.2f} / {item['limit']:.2f} mm = "
                f"{item['ratio']:.3f} ({limit_label}/{item['divisor']:.0f})"
            )
            label.setStyleSheet(f"font-weight: bold; color: {color};")

        def governing_side_midspan_check(side):
            side_prefix = f"{side} "
            side_items = [
                (key, item)
                for key, item in side_deflection_checks_by_component.items()
                if str(key).startswith(side_prefix) and item.get("governing", True)
            ]
            if not side_items:
                return None
            key, item = max(side_items, key=lambda entry: entry[1]["ratio"])
            component = key.replace(side_prefix, "", 1)
            return {**item, "component": component}

        def lean_column_midheight_check(group):
            demand, limit, ratio, status, divisor, item = column_midspan_ws_deflection_check(group)
            if item.get("value", -1.0) < 0.0:
                return None
            return {
                "demand": demand,
                "limit": limit,
                "ratio": ratio,
                "status": status,
                "divisor": divisor,
                "item": item,
                "component": "Ws",
            }

        update_serviceability_status_label(
            self.left_canopy_deflection_check_label,
            canopy_tip_checks_by_side.get("LEFT"),
            "tip",
            "L",
        )
        update_serviceability_status_label(
            self.right_canopy_deflection_check_label,
            canopy_tip_checks_by_side.get("RIGHT"),
            "tip",
            "L",
        )
        left_lean_governing = governing_side_midspan_check("LEFT")
        right_lean_governing = governing_side_midspan_check("RIGHT")
        left_lean_column_midheight = lean_column_midheight_check("LEFT_LEAN_COLUMN")
        right_lean_column_midheight = lean_column_midheight_check("RIGHT_LEAN_COLUMN")
        update_serviceability_status_label(
            self.left_lean_deflection_check_label,
            left_lean_governing,
            f"gov {left_lean_governing.get('component', '-')}" if left_lean_governing else "midspan",
            "Span",
        )
        update_serviceability_status_label(
            self.right_lean_deflection_check_label,
            right_lean_governing,
            f"gov {right_lean_governing.get('component', '-')}" if right_lean_governing else "midspan",
            "Span",
        )
        update_serviceability_status_label(
            self.left_lean_column_deflection_check_label,
            left_lean_column_midheight,
            "mid Ws",
            "H",
        )
        update_serviceability_status_label(
            self.right_lean_column_deflection_check_label,
            right_lean_column_midheight,
            "mid Ws",
            "H",
        )
        for label, check, kind, limit_name in (
            (self.left_canopy_deflection_check_label, canopy_tip_checks_by_side.get("LEFT"), "Left canopy tip", "L"),
            (self.right_canopy_deflection_check_label, canopy_tip_checks_by_side.get("RIGHT"), "Right canopy tip", "L"),
            (self.left_lean_deflection_check_label, left_lean_governing, "Left lean-to midspan", "Span"),
            (self.right_lean_deflection_check_label, right_lean_governing, "Right lean-to midspan", "Span"),
            (self.left_lean_column_deflection_check_label, left_lean_column_midheight, "Left lean-to column mid-height sway", "H"),
            (self.right_lean_column_deflection_check_label, right_lean_column_midheight, "Right lean-to column mid-height sway", "H"),
        ):
            if check is not None:
                register_deflection_hover(
                    label,
                    deflection_overlay_candidate(
                        f"{kind} {check.get('component', '')}".strip(), check.get("item", {}),
                        check["demand"], check["limit"], check["ratio"], check["status"],
                        check["divisor"],
                        "horizontal" if "column mid-height" in kind.lower() else "vertical",
                    ),
                    limit_name,
                )

        serviceability_lines = [
            f"{deflection_item.get('label', 'Column top deflection SLS')}: {deflection_demand:.3f} mm <= {deflection_limit:.3f} mm (H/{deflection_divisor:.0f}) | Node {deflection_item.get('node', '-')} | {deflection_item.get('combo', '-')} | utilisation {deflection_ratio:.3f} | {deflection_status}",
        ]
        if crane_deflection_item.get("value", -1.0) >= 0.0:
            serviceability_lines.append(
                f"{crane_deflection_item.get('label', 'Crane rail racking SLS')} (mean of both rails, frame alone): {crane_deflection_demand:.3f} mm vs {crane_deflection_limit:.3f} mm (H/{crane_deflection_divisor:.0f}) | Nodes {crane_deflection_item.get('node', '-')} | {crane_deflection_item.get('combo', '-')} | utilisation {crane_deflection_ratio:.3f} | REPORT ONLY - not a 2D check; neighbouring frames share crane loads through the roof bracing (3D check)"
            )
        if gauge_item.get("value", -1.0) >= 0.0:
            serviceability_lines.append(
                f"{gauge_item.get('label', 'Crane rail gauge change SLS')} (right minus left): {gauge_demand:.3f} mm <= {gauge_limit:.3f} mm | Nodes {gauge_item.get('node', '-')} | {gauge_item.get('combo', '-')} | utilisation {gauge_ratio:.3f} | {gauge_status}"
            )
        if mid_column_item.get("value", -1.0) >= 0.0:
            serviceability_lines.append(
                f"Column midspan deflection Ws: {mid_column_demand:.3f} mm <= {mid_column_limit:.3f} mm (H/{mid_column_divisor:.0f}) | {mid_column_item.get('group', '-')} | {mid_column_item.get('combo', '-')} | utilisation {mid_column_ratio:.3f} | {mid_column_status}"
            )
        serviceability_lines.extend([
            truss_serviceability_line("G"),
            truss_serviceability_line("Q"),
            truss_serviceability_line("G+Q"),
            truss_serviceability_line("Ws"),
            *side_midspan_serviceability_lines,
            *canopy_tip_serviceability_lines,
        ])

        lines = [
            "SECTION CHECKS",
            "Uses ULS envelope demands from the Results tab.",
            "Column restraints: clad main/lean-to columns automatically select girt flybraces, starting near three-quarter height and adding braces to governing segments when needed. am is calculated from each segment's same-case quarter-point moments. Flybraces provide assumed weak-axis lateral/twist restraint; the in-plane compression length remains base to roof connection (ke=1.00). LTB uses the physical segment length separately. Unclad columns retain ke=0.85 and am=1.75. Crane/storage columns retain their existing dedicated restraint checks.",
            "Storage rail nodes restrain side columns at the rail level and cap side-column restraint length to the longest segment between base, rail and column top. When crane loads are active, side-column minor-axis length also considers base to crane height, using the column's adopted ke (1.00 clad; 0.85 unclad).",
            f"{'Rafter' if is_rafter else 'Chord'} restraints: major length uses node spacing; top/rafter minor length uses purlin spacing {((self.member_check_restraints or {}).get('TOP', {}).get('minor_mm') or 0.0):.0f} mm; bottom minor length defaults to every second girt/flybrace {((self.member_check_restraints or {}).get('BOTTOM', {}).get('minor_mm') or 0.0):.0f} mm, with automatic every-purlin retry if required.",
            "Checks include axial, Clause 5.11-style shear, member bending, and axial plus bending interaction.",
            "Compactness/effective section modulus refinements are still to be verified against worked examples.",
            "",
            "SERVICEABILITY CHECKS",
            "Rafter/truss midspan sizing is governed by Q and Ws deflection. Permanent-load G sag is a fabrication advisory; where it exceeds its limit, the reported minimum precamber equals G sag minus the allowable sag.",
            *serviceability_lines,
            "",
            "CRANE RUNWAY BEAM",
        ]
        if crane_summary:
            lines.extend([
                f"Auto-selected/current section: {crane_summary['section']}",
                f"Span linked to bay size: {crane_summary['span_mm']:.0f} mm",
                f"Runway allowance: 2 beams x full building length for costing",
                f"Self weight: {crane_summary['weight_kg_m']:.1f} kg/m",
                f"Capacity utilisation: {crane_summary['capacity_utilization']:.3f}",
                f"Deflection utilisation: {crane_summary['deflection_utilization']:.3f}",
            ])
        else:
            lines.append("Not required - no crane rating entered.")
        lines.extend([
            "",
            "STORAGE PANEL / HORIZONTAL RAIL",
        ])
        if storage_results is not None and storage_results.inputs.enabled:
            storage_load_lines = [line for line in storage_summary_lines(storage_results, factor=1.0) if line]
            lines.extend(storage_load_lines)
            lines.append(
                f"Applied walls: left {'yes' if storage_results.inputs.left_wall else 'no'}, "
                f"right {'yes' if storage_results.inputs.right_wall else 'no'}"
            )
            if storage_rail_design:
                lines.extend([
                    f"Auto-selected rail: {storage_rail_design['profile'].name}",
                    f"Rail span: {storage_rail_design['span_mm']:.0f} mm; service deflection {storage_rail_design['deflection_mm']:.3f} / {storage_rail_design['deflection_limit_mm']:.3f} mm",
                    f"Rail utilisation: capacity {storage_rail_design['capacity_utilization']:.3f}, deflection {storage_rail_design['deflection_utilization']:.3f}",
                ])
            else:
                lines.append("No passing/current storage rail design available.")
            if storage_panel_design:
                lines.extend([
                    f"Panel thickness: {storage_panel_design.thickness_mm:.0f} mm",
                    f"+M face reo: {storage_panel_design.positive_face.reinforcement_label}, As {storage_panel_design.positive_face.as_provided_mm2_m:.0f} mm2/m, fs {storage_panel_design.positive_face.service_steel_stress_mpa:.1f} MPa, w {storage_panel_design.positive_face.crack_width_mm:.3f} mm",
                    f"-M face reo: {storage_panel_design.negative_face.reinforcement_label}, As {storage_panel_design.negative_face.as_provided_mm2_m:.0f} mm2/m, fs {storage_panel_design.negative_face.service_steel_stress_mpa:.1f} MPa, w {storage_panel_design.negative_face.crack_width_mm:.3f} mm",
                    f"Panel service deflection: {storage_panel_design.deflection_mm:.3f} / {storage_panel_design.deflection_limit_mm:.3f} mm",
                ])
            else:
                lines.append("No passing storage panel thickness/reinforcement option available.")
        else:
            lines.append("Not required - storage loads are disabled.")
        lines.extend([
            "",
            "STRENGTH CHECKS",
        ])
        auto_size_report = getattr(self, "auto_size_report_text", "")
        if auto_size_report:
            lines.extend(["AUTO-SIZE REPORT", auto_size_report, ""])
        lines.extend(format_check_set("LEFT COLUMN", left_column_profile, envelopes["LEFT_COLUMN"], "LEFT_COLUMN"))
        lines.append("")
        lines.extend(format_check_set("RIGHT COLUMN", right_column_profile, envelopes["RIGHT_COLUMN"], "RIGHT_COLUMN"))
        if envelopes["INTERNAL_COLUMN"]["compression_kn"]["value"] >= 0.0:
            lines.append("")
            lines.extend(format_check_set("INTERNAL COLUMN", internal_column_profile, envelopes["INTERNAL_COLUMN"], "INTERNAL_COLUMN"))
        lines.append("")
        lines.extend(format_check_set("RAFTER" if is_rafter else "TOP CHORD - DESIGN ENVELOPE", top_profile, envelopes["TOP"], "TOP"))
        if not is_rafter:
            lines.append("")
            lines.extend(format_check_set("BOTTOM CHORD - DESIGN ENVELOPE", bottom_profile, envelopes["BOTTOM"], "BOTTOM"))
        if purlin_check:
            match = purlin_check.get("match", {})
            local_match = purlin_check.get("local_match", {})
            top_minor = ((self.member_check_restraints or {}).get("TOP", {}) or {}).get("minor_mm") or purlin_check.get("spacing_mm", 0.0)
            bridging_summary = self.purlin_bridging_summary_text(purlin_check)
            lines.append("")
            lines.extend([
                "PURLIN CHECK",
                f"Span type: {purlin_check['span_type']} | table span {match.get('span_row_mm', 0):.0f} mm{' interpolated' if match.get('interpolated') else ''} | spacing {purlin_check['spacing_mm']:.0f} mm",
                f"Adopted purlin layout: {purlin_check.get('purlin_rows', 0)} rows total | extra rows per slope {purlin_check.get('extra_rows_per_slope', 0)} | top chord minor restraint {top_minor:.0f} mm",
                f"Adopted bridging: {bridging_summary}",
                f"Line loads: outward ULS {purlin_check['outward_kn_m']:.3f} kN/m, inward ULS {purlin_check['inward_strength_kn_m']:.3f} kN/m, service Ws {purlin_check['inward_service_kn_m']:.3f} kN/m",
                f"Area loads: G {purlin_check['permanent_kpa']:.3f} kPa, Q {purlin_check['live_kpa']:.3f} kPa, wind ULS up/down {purlin_check['wind_upward_kpa']:.3f}/{purlin_check['wind_downward_kpa']:.3f} kPa, Ws up/down {purlin_check['wind_service_upward_kpa']:.3f}/{purlin_check['wind_service_downward_kpa']:.3f} kPa",
                f"Inward ULS basis: max(1.2G+1.5Q = {purlin_check['inward_gravity_uls_kpa']:.3f} kPa, 1.2G+Wd = {purlin_check['inward_wind_uls_kpa']:.3f} kPa)",
            ])
            if match.get("status") == "OK":
                lines.extend([
                    f"General matched section: {self.metroll_section_display(match['section'], purlin_check['span_type'])} "
                    f"from Metroll tables {match['tables']}",
                    f"Table bridging basis: outward {self.bridging_label_text(match.get('outward_bridging'))}, inward {self.bridging_label_text(match.get('inward_bridging'))}",
                    f"Selection basis: {purlin_check.get('profile_selection', {}).get('basis', 'No compatible roof-zone pair selected')} General area {match.get('area_mm2', 0.0):.0f} mm2; edge area {local_match.get('area_mm2', 0.0):.0f} mm2.",
                    f"Capacities: outward {match['outward_capacity_kn_m']:.3f} kN/m, inward strength {match['inward_strength_capacity_kn_m']:.3f} kN/m, inward L/150 {match['inward_service_capacity_kn_m']:.3f} kN/m",
                ])
                if match.get("capped_by_double_span"):
                    lines.append("Single-span capacities capped at corresponding double-span values where extracted table values were higher.")
                alternatives = purlin_check.get('profile_selection', {}).get('profile_alternatives', [])
                if alternatives:
                    lines.append("Passing common-profile comparison (general / edge; total roof-row weight per metre of building, before common lap factor):")
                    for option in alternatives:
                        general_name = self.metroll_section_display(option['general_section'], purlin_check['span_type'])
                        edge_name = self.metroll_section_display(option['edge_section'], purlin_check['span_type'])
                        label = "selected" if option['profile'] == purlin_check['profile_selection']['common_profile'] else f"+{option['extra_material_percent']:.1f}% material"
                        lines.append(f"- {general_name} / {edge_name}: {option['weight_kg_per_m_building']:.2f} kg/m ({label})")
                if match.get("near_misses"):
                    lines.append("Nearest failing sections:")
                    for miss in match["near_misses"]:
                        lines.append(
                            f"- {self.metroll_section_display(miss['section'], purlin_check['span_type'])}: "
                            f"outward {miss['outward_ratio']:.3f}, "
                            f"inward strength {miss['inward_strength_ratio']:.3f}, "
                            f"inward L/150 {miss['inward_service_ratio']:.3f}"
                        )
            else:
                lines.append(f"General matched section: {match.get('status', 'NOT CHECKED')} - {match.get('message', 'No match')}")
            lines.extend([
                f"Local edge rows: first {purlin_check['edge_rows']} rows within a={purlin_check.get('local_dimension_a_m', 0.0):.2f} m | tributary area {purlin_check['local_area_m2']:.2f} m2 | Kl {purlin_check.get('local_pressure_factor', 1.0):.1f}",
                f"Local line loads: outward ULS {purlin_check['local_outward_kn_m']:.3f} kN/m, inward ULS {purlin_check['local_inward_strength_kn_m']:.3f} kN/m, service Ws {purlin_check['local_inward_service_kn_m']:.3f} kN/m",
                f"Local wind ULS up/down {purlin_check['local_wind_upward_kpa']:.3f}/{purlin_check['local_wind_downward_kpa']:.3f} kPa, Ws up/down {purlin_check['local_wind_service_upward_kpa']:.3f}/{purlin_check['local_wind_service_downward_kpa']:.3f} kPa",
                f"Local inward ULS basis: max(1.2G+1.5Q = {purlin_check['inward_gravity_uls_kpa']:.3f} kPa, 1.2G+Wd = {purlin_check['local_inward_wind_uls_kpa']:.3f} kPa)",
            ])
            if local_match.get("status") == "OK":
                lines.extend([
                    f"Local matched section: "
                    f"{self.metroll_section_display(local_match['section'], purlin_check['span_type'])} "
                    f"from Metroll tables {local_match['tables']}",
                    f"Local table bridging basis: outward {self.bridging_label_text(local_match.get('outward_bridging'))}, inward {self.bridging_label_text(local_match.get('inward_bridging'))}",
                    f"Local capacities: outward {local_match['outward_capacity_kn_m']:.3f} kN/m, inward strength {local_match['inward_strength_capacity_kn_m']:.3f} kN/m, inward L/150 {local_match['inward_service_capacity_kn_m']:.3f} kN/m",
                ])
                if local_match.get("capped_by_double_span"):
                    lines.append("Local single-span capacities capped at corresponding double-span values where extracted table values were higher.")
            else:
                lines.append(f"Local matched section: {local_match.get('status', 'NOT CHECKED')} - {local_match.get('message', 'No match')}")
            lines.append("Note: Metroll table capacities are limit-state kN/m values and do not include member self-weight.")
        wall_girt_check = self.wall_girt_check or {}
        if wall_girt_check:
            match = wall_girt_check.get("match", {})
            lines.append("")
            lines.extend([
                "WALL GIRT CHECK",
                f"Span type: {wall_girt_check['span_type']} | table span {match.get('span_row_mm', 0):.0f} mm{' interpolated' if match.get('interpolated') else ''} | spacing {wall_girt_check['spacing_mm']:.0f} mm (max {wall_girt_check['max_spacing_mm']:.0f} mm)",
                f"Line loads: outward ULS {wall_girt_check['outward_kn_m']:.3f} kN/m, inward ULS {wall_girt_check['inward_strength_kn_m']:.3f} kN/m, SLS {wall_girt_check['inward_service_kn_m']:.3f} kN/m",
                f"Wall pressures: outward {wall_girt_check['wind_outward_kpa']:.3f} kPa, inward {wall_girt_check['wind_inward_kpa']:.3f} kPa, service {wall_girt_check['wind_service_kpa']:.3f} kPa",
            ])
            if match.get("status") == "OK":
                lines.extend([
                    f"Matched section: "
                    f"{self.metroll_section_display(match['section'], wall_girt_check['span_type'])} "
                    f"from Metroll tables {match['tables']}",
                    f"Selection basis: deepest section within 5% of the lightest passing area; selected area {match.get('area_mm2', 0.0):.0f} mm2.",
                    f"Capacities: outward {match['outward_capacity_kn_m']:.3f} kN/m, inward strength {match['inward_strength_capacity_kn_m']:.3f} kN/m, inward L/150 {match['inward_service_capacity_kn_m']:.3f} kN/m",
                ])
                if match.get("capped_by_double_span"):
                    lines.append("Single-span capacities capped at corresponding double-span values where extracted table values were higher.")
                if match.get("near_misses"):
                    lines.append("Nearest failing sections:")
                    for miss in match["near_misses"]:
                        lines.append(
                            f"- {self.metroll_section_display(miss['section'], wall_girt_check['span_type'])}: "
                            f"outward {miss['outward_ratio']:.3f}, "
                            f"inward strength {miss['inward_strength_ratio']:.3f}, "
                            f"inward L/150 {miss['inward_service_ratio']:.3f}"
                        )
            else:
                lines.append(f"Matched section: {match.get('status', 'NOT CHECKED')} - {match.get('message', 'No match')}")
            lines.append("Note: wall girt spacing is capped at 1700 mm, or 1100 mm for wind Region C.")
        end_wall_design = self.end_wall_mullion_design or {}
        if end_wall_design and end_wall_design.get("end_wall_count", 0) > 0:
            selected = end_wall_design.get("selected") or {}
            governing = end_wall_design.get("governing_mullion") or {}
            spacing_override = end_wall_design.get("spacing_override_mm")
            spacing_basis = (
                f"manual target {spacing_override:.0f} mm"
                if spacing_override else "automatic maximum 8000 mm"
            )
            lines.append("")
            lines.extend([
                "END WALL MULLION CHECK",
                f"Layout: {end_wall_design.get('spaces', 0)} equal spaces @ {end_wall_design.get('actual_spacing_mm', 0.0):.0f} mm | {end_wall_design.get('mullion_count', 0)} intermediate mullion(s) per clad end",
                f"Spacing basis: {spacing_basis}",
                "Support/restraint: pinned roof and ground; one mid-height flybrace; effective lengths H/2",
                "Deflection basis: full-height simply supported wind deflection at midspan, H/150",
                f"Governing geometry: x {governing.get('station_mm', 0.0):.0f} mm | H {governing.get('height_mm', 0.0):.0f} mm | tributary width {governing.get('tributary_width_mm', 0.0):.0f} mm",
                f"Pressures: outward ULS {end_wall_design.get('outward_uls_kpa', 0.0):.3f} kPa | inward ULS {end_wall_design.get('inward_uls_kpa', 0.0):.3f} kPa | service {end_wall_design.get('service_kpa', 0.0):.3f} kPa",
            ])
            if spacing_override and spacing_override > end_wall_design.get("automatic_max_spacing_mm", 8000.0) + 1e-9:
                lines.append("Warning: manual mullion spacing target exceeds the 8000 mm automatic limit.")
            if selected:
                member_check = selected.get("member_check")
                lines.extend([
                    f"Auto-selected section: {selected.get('section', '-')} ({selected.get('mass_kg_m', 0.0):.1f} kg/m) | status {end_wall_design.get('status', 'NOT CHECKED')}",
                    f"Actions: M* {selected.get('moment_knm', 0.0):.3f} kNm | V* {selected.get('shear_kn', 0.0):.3f} kN | self-weight N* {selected.get('self_weight_compression_kn', 0.0):.3f} kN",
                    f"Strength: utilization {selected.get('strength_ratio', 0.0):.3f} | governing {member_check.governing_check if member_check else '-'}",
                    f"Deflection: {selected.get('deflection_mm', 0.0):.3f} mm <= {selected.get('deflection_limit_mm', 0.0):.3f} mm | utilization {selected.get('deflection_ratio', 0.0):.3f}",
                ])
            else:
                lines.append(f"Status: {end_wall_design.get('status', 'NOT CHECKED')} - {end_wall_design.get('message', 'No intermediate mullion required')}")
            end_girt = self.end_wall_girt_check or {}
            end_girt_match = end_girt.get("match", {})
            lines.extend([
                "",
                "END WALL GIRT CHECK",
                f"Span type: {end_girt.get('span_type', '-')} | horizontal span {end_girt.get('span_mm', 0.0):.0f} mm | vertical spacing {end_girt.get('spacing_mm', 0.0):.0f} mm",
                f"Line loads: outward ULS {end_girt.get('outward_kn_m', 0.0):.3f} kN/m | inward ULS {end_girt.get('inward_strength_kn_m', 0.0):.3f} kN/m | service {end_girt.get('inward_service_kn_m', 0.0):.3f} kN/m",
                f"Matched section: {end_girt_match.get('status', 'NOT CHECKED')} - "
                f"{self.metroll_section_display(end_girt_match.get('section'), end_girt.get('span_type', '')) or end_girt_match.get('message', '-')}",
            ])
            if end_girt_match.get("status") == "OK":
                lines.append(
                    f"Capacities: outward {end_girt_match.get('outward_capacity_kn_m', 0.0):.3f} kN/m | inward strength {end_girt_match.get('inward_strength_capacity_kn_m', 0.0):.3f} kN/m | inward L/150 {end_girt_match.get('inward_service_capacity_kn_m', 0.0):.3f} kN/m"
                )
        bracing_design = getattr(self, "longitudinal_bracing_design", None) or {}
        if bracing_design:
            wall_uls = bracing_design.get("wall_uls", {})
            wall_sls = bracing_design.get("wall_sls", {})
            roof_uls = bracing_design.get("roof_uls", {})
            roof_sls = bracing_design.get("roof_sls", {})
            lines.extend([
                "",
                "LONGITUDINAL BRACING - PRELIMINARY CHECK",
                f"Status: {bracing_design.get('status', 'NOT CHECKED')} | {bracing_design.get('method', '-')}",
                f"Layout: {bracing_design.get('roof_panel_count', 0)} roof-bracing panel(s) between mullion lines @ {bracing_design.get('roof_panel_width_mm', 0.0):.0f} mm | {bracing_design.get('wall_side_count', 0)} side wall(s) | {bracing_design.get('braced_bays_per_side', 0)} wall-braced bay(s) per side",
                f"End wall area: {bracing_design.get('projected_end_wall_area_m2', 0.0):.2f} m2",
                f"ULS end wind: {bracing_design.get('ultimate_pressure_kpa', 0.0):.3f} kPa -> {bracing_design.get('ultimate_resultant_kn', 0.0):.2f} kN total -> roof reaction {bracing_design.get('roof_reaction_uls_kn', 0.0):.2f} kN | {bracing_design.get('ultimate_case', '-')}",
                f"Maximum roof panel area: {bracing_design.get('max_roof_panel_area_m2', 0.0):.2f} m2",
                f"Roof-plane brace ({bracing_design.get('roof_brace_label', '-')}): panel shear {roof_uls.get('shear_kn', 0.0):.2f} kN | N* {roof_uls.get('brace_force_kn', 0.0):.2f} / phiNt {roof_uls.get('capacity_kn', 0.0):.2f} kN | utilization {roof_uls.get('capacity_ratio', 0.0):.3f}",
                f"Side-wall brace ({bracing_design.get('wall_brace_label', '-')}): shear {wall_uls.get('shear_kn', 0.0):.2f} kN/path | N* {wall_uls.get('brace_force_kn', 0.0):.2f} / phiNt {wall_uls.get('capacity_kn', 0.0):.2f} kN | utilization {wall_uls.get('capacity_ratio', 0.0):.3f}",
                f"Tension-only SLS drift: wall {wall_sls.get('horizontal_drift_mm', 0.0):.3f} mm + roof plane {roof_sls.get('horizontal_drift_mm', 0.0):.3f} mm per load path",
            ])
            for heading, key in (
                ("EAVE STRUTS", "eave_struts"),
                ("MULLION TIES", "mullion_ties"),
                ("RIDGE STRUTS", "ridge_struts"),
            ):
                items = bracing_design.get(key, []) or []
                if not items:
                    continue
                lines.append(heading)
                for item in items:
                    selection = item.get("selection", {})
                    lines.append(
                        f"- {item.get('label', '-')} | x {item.get('station_mm', 0.0):.0f} mm | roof height {item.get('height_mm', 0.0):.0f} mm | "
                        f"tributary area {item.get('tributary_area_m2', 0.0):.2f} m2 | N* {item.get('demand_kn', 0.0):.2f} kN | "
                        f"{selection.get('label', '-')} | phiNc {selection.get('capacity_kn', 0.0):.2f} kN | utilization {selection.get('ratio', 0.0):.3f}"
                    )
            lines.append("Strut hierarchy: existing purlin at 50% nominal axial reserve, additional purlin, back-to-back purlins, then the lightest passing SHS/RHS.")
            lines.append("Limitations: brace gross yielding and preliminary Rankine strut buckling only; connections, net section, block shear and diaphragm distribution are not yet checked.")
        if not is_rafter and envelopes["WEB"]["compression_kn"]["value"] >= 0.0:
            lines.append("")
            lines.extend(format_web_assignment_check_set("WEB", web_profile, "WEB"))
        if left_canopy_top_profile is not None and envelopes["LEFT_CANOPY_TOP"]["compression_kn"]["value"] >= 0.0:
            lines.append("")
            lines.extend(format_check_set("LEFT CANOPY / LEAN-TO TOP CHORD", left_canopy_top_profile, envelopes["LEFT_CANOPY_TOP"], "LEFT_CANOPY_TOP"))
        if left_canopy_bottom_profile is not None and envelopes["LEFT_CANOPY_BOTTOM"]["compression_kn"]["value"] >= 0.0:
            lines.append("")
            lines.extend(format_check_set("LEFT CANOPY / LEAN-TO BOTTOM CHORD", left_canopy_bottom_profile, envelopes["LEFT_CANOPY_BOTTOM"], "LEFT_CANOPY_BOTTOM"))
        if left_canopy_web_profile is not None and envelopes["LEFT_CANOPY_WEB"]["compression_kn"]["value"] >= 0.0:
            lines.append("")
            lines.extend(format_web_assignment_check_set("LEFT CANOPY / LEAN-TO WEB", left_canopy_web_profile, "LEFT_CANOPY_WEB"))
        if left_lean_column_profile is not None and envelopes["LEFT_LEAN_COLUMN"]["compression_kn"]["value"] >= 0.0:
            lines.append("")
            lines.extend(format_check_set("LEFT LEAN-TO OUTER COLUMN", left_lean_column_profile, envelopes["LEFT_LEAN_COLUMN"], "LEFT_LEAN_COLUMN"))
            lines.append(column_midspan_ws_deflection_line("LEFT_LEAN_COLUMN"))
        if right_canopy_top_profile is not None and envelopes["RIGHT_CANOPY_TOP"]["compression_kn"]["value"] >= 0.0:
            lines.append("")
            lines.extend(format_check_set("RIGHT CANOPY / LEAN-TO TOP CHORD", right_canopy_top_profile, envelopes["RIGHT_CANOPY_TOP"], "RIGHT_CANOPY_TOP"))
        if right_canopy_bottom_profile is not None and envelopes["RIGHT_CANOPY_BOTTOM"]["compression_kn"]["value"] >= 0.0:
            lines.append("")
            lines.extend(format_check_set("RIGHT CANOPY / LEAN-TO BOTTOM CHORD", right_canopy_bottom_profile, envelopes["RIGHT_CANOPY_BOTTOM"], "RIGHT_CANOPY_BOTTOM"))
        if right_canopy_web_profile is not None and envelopes["RIGHT_CANOPY_WEB"]["compression_kn"]["value"] >= 0.0:
            lines.append("")
            lines.extend(format_web_assignment_check_set("RIGHT CANOPY / LEAN-TO WEB", right_canopy_web_profile, "RIGHT_CANOPY_WEB"))
        if right_lean_column_profile is not None and envelopes["RIGHT_LEAN_COLUMN"]["compression_kn"]["value"] >= 0.0:
            lines.append("")
            lines.extend(format_check_set("RIGHT LEAN-TO OUTER COLUMN", right_lean_column_profile, envelopes["RIGHT_LEAN_COLUMN"], "RIGHT_LEAN_COLUMN"))
            lines.append(column_midspan_ws_deflection_line("RIGHT_LEAN_COLUMN"))

        self.section_checks_box.setText("\n".join(lines))

    def update_costing_text(self):
        return costing.update_costing_text(self)

    def update_foundation_text(self):
        if not getattr(self, "foundation_box", None):
            return
        summary = self.foundation_summary
        if not summary:
            self.foundation_box.setText("Generate and solve the frame to view foundation design.")
            return
        lines = [
            "FOUNDATION DESIGN",
            f"Allowable bearing: {summary['bearing_kpa']:.1f} kPa",
            f"Skin friction: {summary['skin_friction_kpa']:.1f} kPa from 1.0 m depth",
            f"Skin friction used for bearing: {'Yes' if summary.get('use_skin_friction_for_bearing') else 'No'}",
            "Standard pier diameters: " + ", ".join(f"{diameter:.0f} mm" for diameter in summary["diameters_mm"]),
            "Pier selection increases diameter before exceeding 4.0 m depth.",
            "",
            "SELECTED FOOTINGS",
        ]
        for item in summary["items"]:
            lines.append(
                f"{item['label']}: {item['diameter_mm']:.0f} mm dia x {item['depth_m']:.2f} m deep ({item.get('status', '-')})"
            )
        lines.extend([
            "",
            "DETAILS",
            "Column              | Demand envelope                         | Pier design",
            "--------------------|-----------------------------------------|-------------------------------",
        ])
        for item in summary["items"]:
            status = item.get("status", "-")
            extra = ""
            if item.get("required_bearing_diameter_mm", 0.0) > item["diameter_mm"]:
                extra = f" | bearing needs {item['required_bearing_diameter_mm']:.0f} mm dia"
            lines.append(
                f"{item['label']:<20}| "
                f"SLS C {item['compression_kn']:>7.1f} kN ({item['compression_combo']}), "
                f"ULS U {item['uplift_kn']:>7.1f} kN ({item['uplift_combo']}) | "
                f"{item['diameter_mm']:.0f} dia x {item['depth_m']:.2f} m deep | "
                f"bearing {item['bearing_capacity_kn']:.1f} kN, skin {item['skin_capacity_kn']:.1f} kN | {status}{extra}"
            )
            lines.append(
                f"{'':<20}| "
                f"H {item['shear_kn']:>7.1f} kN ({item['shear_combo']}), "
                f"M {item['moment_knm']:>7.1f} kNm ({item['moment_combo']}) | "
                "horizontal/moment shown for reference"
            )
        lines.extend([
            "",
            "Note: this is a preliminary circular pier sizing using vertical bearing and uplift skin friction only. It does not check lateral soil pressure, pier bending/shear, reinforcement, settlement, edge distance, group effects, or geotechnical reduction factors.",
        ])
        self.foundation_box.setText("\n".join(lines))

    def update_results_text(self, num_panels, panel, web_angle, left_support, right_support,
                            left_base, right_base, left_column_profile, right_column_profile, top_profile, bottom_profile, web_profile,
                            load_summary_lines, design_check_lines,
                            outside_span=None, centreline_span=None, left_column_offset=0.0, right_column_offset=0.0,
                            left_canopy_length=0.0, right_canopy_length=0.0,
                            left_canopy_pitch=0.0, left_canopy_shallow_depth=0.0,
                            right_canopy_pitch=0.0, right_canopy_shallow_depth=0.0,
                            left_canopy_type="Canopy", right_canopy_type="Canopy",
                            left_canopy_eave_height=None, right_canopy_eave_height=None,
                            left_canopy_frame_system="Truss", right_canopy_frame_system="Truss",
                            eave_x_restraint="None", eave_x_spring_kn_mm=0.0,
                            base_moment_spring_knm_rad=0.0,
                            eave_bracing_info=None,
                            roof_shape="Gable",
                            rafter_haunch_length=0.0,
                            panel_width_range=None,
                            truss_external_depth=None,
                            truss_centreline_depth=None):
        d = self.displacements
        R = self.reactions
        structure = self.structure

        results = []
        results.append("=== ANALYSIS RESULTS ===\n")
        if outside_span is not None and centreline_span is not None:
            results.append(f"Outside width: {outside_span:.1f} mm")
            results.append(f"Column centreline span: {centreline_span:.1f} mm")
        if self.internal_column_count_combo.currentText() == "Custom positions":
            results.append(
                f"Internal column positions from left column centreline (m): {self.internal_column_positions_input.text()}"
            )
            results.append(f"Column offsets: left {left_column_offset:.1f} mm, right {right_column_offset:.1f} mm")
        results.append(f"Roof shape: {roof_shape}")
        basis = getattr(self, "eave_node_basis_record", None)
        if basis:
            results.append(
                f"Eave height: {basis['eave_mm']:.1f} mm at the cladding pitching point (outside of girts)")
            results.append(
                f"Top chord node: {basis['node_mm']:.1f} mm at the column centrelines = eave "
                f"+ {basis['inset_mm']:.1f} x tan(pitch) - {basis['drop_mm']:.1f} "
                f"(girt {basis['girt_mm']:.0f} mm: {basis['girt_source']}; purlin {basis['purlin_mm']:.0f} mm: "
                f"{basis['purlin_source']}; top chord {basis['top_chord_depth_mm']:.0f} mm)")
            strips = basis.get("strips") or []
            if strips:
                results.append("Eave strips to the eave nodes: " + "; ".join(
                    f"{s['side'].lower()} wall {s['wall_mm']:.0f} mm, roof {s['roof_mm']:.0f} mm" for s in strips))
        if truss_external_depth is not None and truss_centreline_depth is not None:
            results.append(
                f"Truss depth: {truss_external_depth:.1f} mm outside-to-outside "
                f"({truss_centreline_depth:.1f} mm chord centreline depth)"
            )
        if left_canopy_length > 0.0 or right_canopy_length > 0.0:
            left_height_text = "top column node" if left_canopy_eave_height is None else f"{left_canopy_eave_height:.1f} mm"
            right_height_text = "top column node" if right_canopy_eave_height is None else f"{right_canopy_eave_height:.1f} mm"
            results.append(
                f"Canopies: left {left_canopy_length:.1f} mm ({left_canopy_type}, {left_canopy_frame_system}, eave {left_height_text}, pitch {left_canopy_pitch:.2f} deg, depth {left_canopy_shallow_depth:.1f} mm), "
                f"right {right_canopy_length:.1f} mm ({right_canopy_type}, {right_canopy_frame_system}, eave {right_height_text}, pitch {right_canopy_pitch:.2f} deg, depth {right_canopy_shallow_depth:.1f} mm)"
            )
        results.append(f"Panels: {num_panels}")
        if panel_width_range and panel_width_range[1] - panel_width_range[0] > 1.0:
            results.append(
                f"Panel lengths: {panel_width_range[0]:.1f}-{panel_width_range[1]:.1f} mm "
                "(alternating, roof-pitch compensated)"
            )
        else:
            results.append(f"Panel length: {panel:.1f} mm")
        results.append(f"Approx web angle: {web_angle:.1f}°")
        if self.purlin_layout is not None:
            results.append(
                f"Purlins: max spacing {self.purlin_layout.max_spacing_mm:.0f} mm "
                f"(end {self.purlin_layout.end_spacing_limit_mm:.0f}, mid limit {self.purlin_layout.mid_spacing_limit_mm:.0f})"
            )
        results.append("Bottom chord connection: directly connected to split column nodes")
        if rafter_haunch_length > 0.0:
            results.append(
                f"Rafter haunch: tapered added depth from full rafter depth at columns to zero over {rafter_haunch_length:.0f} mm each side (stiffness approximation)"
            )
        results.append(f"Left support: {left_support}")
        results.append(f"Right support: {right_support}")
        if "Spring" in {left_support, right_support}:
            results.append(f"Base moment spring kR: {base_moment_spring_knm_rad:.3f} kNm/rad")
        eave_restraint_text = eave_x_restraint
        if eave_x_restraint in {"One end", "Both ends"}:
            sides_braced = self.end_wall_braced_sides_combo.currentText().lower()
            eave_restraint_text = f"End wall bracing - {eave_x_restraint.lower()}, {sides_braced}"
        restraint_status = self.bracing_3d_restraint_status if eave_x_restraint == BRACING_3D_RESTRAINT_OPTION else None
        if restraint_status is not None:
            eave_restraint_text += (f" sway spring {eave_x_spring_kn_mm:.3f} kN/mm on mean eave movement"
                                    if eave_x_spring_kn_mm > 0.0 else " - no restraint applied")
        elif eave_x_restraint != "None" and eave_x_spring_kn_mm > 0.0:
            eave_restraint_text += f" spring {eave_x_spring_kn_mm:.3f} kN/mm"
        elif eave_x_restraint != "None":
            eave_restraint_text += " rigid"
        results.append(f"Eave X restraint: {eave_restraint_text}\n")
        if restraint_status is not None:
            label = {"applied": "", "recheck": "RECHECK 3D: ", "stale": "STALE: ", "missing": "NOT ADOPTED: "}[restraint_status["state"]]
            results.append(f"3D bracing restraint: {label}{restraint_status['message']}\n")
        if eave_bracing_info is not None and "springs" in eave_bracing_info:
            results.append(
                "Propped end frame restraint (end-wall bracing panels, k = E·A·cos²θ/L): "
                f"{eave_bracing_info['brace_label']} (A={eave_bracing_info['brace_area_mm2']:.0f} mm2); "
                + "; ".join(f"{side} eave: panel {s['panel_width_mm']:.0f} mm, L={s['diagonal_mm']:.0f} mm, "
                            f"{s['spring_kn_mm']:.3f} kN/mm" for side, s in eave_bracing_info["springs"].items())
                + "\n"
            )
        elif eave_bracing_info is not None:
            results.append(
                "End-wall portal restraint model: "
                f"{eave_bracing_info['end_count']} end(s), "
                f"path {eave_bracing_info['path_m']:.2f} m ({eave_bracing_info['squares_to_end']} bay squares), "
                f"{eave_bracing_info['brace_label']} "
                f"(A={eave_bracing_info['brace_area_mm2']:.0f} mm2, L={eave_bracing_info['diagonal_length_mm']:.0f} mm), "
                f"rope-only one-square spring {eave_bracing_info['base_spring_kn_mm']:.3f} kN/mm, "
                f"SpaceGASS path calibration x {eave_bracing_info['calibration_factor']:.3f}, "
                f"one end -> {eave_bracing_info['one_end_spring_kn_mm']:.3f} kN/mm, "
                f"adopted -> {eave_bracing_info['effective_spring_kn_mm']:.3f} kN/mm per eave\n"
            )
        bracing_design = getattr(self, "longitudinal_bracing_design", None) or {}
        if bracing_design:
            results.append(
                f"Longitudinal bracing: {bracing_design.get('status', 'NOT CHECKED')}, "
                f"roof reaction V*={bracing_design.get('roof_reaction_uls_kn', 0.0):.2f} kN, "
                f"governing utilization={bracing_design.get('governing_capacity_ratio', 0.0):.3f}; "
                "see the Bracing tab and Section Checks.\n"
            )

        analysis_info = self.analysis_info or {"type": "first_order", "converged": True, "iterations": 1, "displacement_amplification": 1.0}
        if analysis_info.get("type") == "second_order":
            status = "converged" if analysis_info.get("converged") else "not converged"
            results.append("ANALYSIS METHOD")
            results.append(
                f"Second-order geometric stiffness iteration: {status} in "
                f"{analysis_info.get('iterations', 0)} / {analysis_info.get('max_iterations', 0)} iterations"
            )
            results.append(
                f"Max displacement amplification: {analysis_info.get('displacement_amplification', 1.0):.3f} "
                f"({analysis_info.get('first_order_max_abs_mm', 0.0):.3f} mm -> "
                f"{analysis_info.get('second_order_max_abs_mm', 0.0):.3f} mm)\n"
            )
        else:
            results.append("ANALYSIS METHOD")
            results.append("First-order linear elastic analysis\n")

        results.extend(load_summary_lines)
        results.append("")
        results.extend(self.building_load_summary_lines())

        results.append("SELECTED PROFILES")
        for label, p in [
            ("Left column", left_column_profile),
            ("Right column", right_column_profile),
            ("Top chord", top_profile),
            ("Bottom chord", bottom_profile),
            ("Webs", web_profile),
        ]:
            results.append(f"{label:<13}: {p.name} | A={p.A:.0f} mm² | I={p.I_analysis:.3e} mm⁴")
        results.append("")

        results.append("SUPPORT REACTIONS")
        results.append(f"Left RX  = {R[left_base.ux] / 1000:.2f} kN")
        results.append(f"Left RY  = {R[left_base.uy] / 1000:.2f} kN")
        results.append(f"Left MZ  = {R[left_base.rz] / 1_000_000:.2f} kNm")
        results.append(f"Right RX = {R[right_base.ux] / 1000:.2f} kN")
        results.append(f"Right RY = {R[right_base.uy] / 1000:.2f} kN")
        results.append(f"Right MZ = {R[right_base.rz] / 1_000_000:.2f} kNm\n")

        min_uy_node = min(structure.nodes, key=lambda n: d[n.uy])
        max_uy_node = max(structure.nodes, key=lambda n: d[n.uy])
        min_ux_node = min(structure.nodes, key=lambda n: d[n.ux])
        max_ux_node = max(structure.nodes, key=lambda n: d[n.ux])
        max_abs_ux_node = max(structure.nodes, key=lambda n: abs(d[n.ux]))
        results.append("MAX DISPLACEMENTS")
        results.append(f"Max upward UY    = {d[max_uy_node.uy]:+.3f} mm at Node {max_uy_node.id}")
        results.append(f"Max downward UY  = {d[min_uy_node.uy]:+.3f} mm at Node {min_uy_node.id}")
        results.append(f"Max +UX          = {d[max_ux_node.ux]:+.3f} mm at Node {max_ux_node.id}")
        results.append(f"Max -UX          = {d[min_ux_node.ux]:+.3f} mm at Node {min_ux_node.id}")
        results.append(f"Max abs UX       = {d[max_abs_ux_node.ux]:+.3f} mm at Node {max_abs_ux_node.id}\n")

        results.extend(design_check_lines)
        results.append("")

        results.append("MEMBER MAX VALUES")
        results.append("Member | Group  | Profile            | Compression kN | Shear kN | Moment kNm")
        for element in structure.elements:
            s = structure.element_force_summary(element, d)
            results.append(
                f"{element.id:>6} | {element.group:<6} | {element.profile.name[:18]:<18} | "
                f"{s['compression_kn']:>14.2f} | {s['shear_kn']:>8.2f} | {s['moment_knm']:>10.2f}"
            )

        self.results_box.setText("\n".join(results))
