"""Calculation evidence for the full report; no PDF or Qt dependencies.

Capture case records inside the existing solver, then freeze the final design
after the normal member and web checks have completed. Report rendering never
re-solves or selects a section.
"""
from dataclasses import asdict
import copy
from datetime import datetime
import hashlib
import json
import math

import member_checks as steel


WEB_GROUPS = {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}
CHORD_GROUPS = {"TOP", "BOTTOM", "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM", "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM"}


def case_record(structure, combination, name, displacement, reactions, forces, analysis, load_lines):
    """Copy one actual solved case, retaining signed global forces and rotations."""
    return {
        "name": name,
        "combination": asdict(combination),
        "analysis": copy.deepcopy(analysis or {"type": "first_order", "converged": True, "iterations": 1}),
        "load_lines": list(load_lines),
        "nodes": [{
            "id": node.id, "x_mm": node.x, "y_mm": node.y,
            "ux_mm": float(displacement[node.ux]), "uy_mm": float(displacement[node.uy]),
            "rz_rad": float(displacement[node.rz]),
            "fx_kn": float(forces[node.native_ux]) / 1000.0,
            "fy_kn": float(forces[node.native_uy]) / 1000.0,
            "mz_knm": float(forces[node.native_rz]) / 1e6,
        } for node in structure.nodes],
        "reactions": [{
            "node": node.id, "rx_kn": float(reactions[node.ux]) / 1000.0,
            "ry_kn": float(reactions[node.uy]) / 1000.0,
            "mz_knm": float(reactions[node.rz]) / 1e6,
        } for node in structure.nodes
            if any(dof in structure.restrained_dofs or dof in structure.springs for dof in (node.ux, node.uy, node.rz))],
        "members": [{"member": element.id, "group": element.group,
                     **structure.element_force_summary(element, displacement),
                     "diagram": [list(element.section_forces_at(displacement, t / 8,
                                      structure.uniform_load_for_element(element))) for t in range(9)]}
                    for element in structure.elements],
        "member_loads": [{"member": element.id, "local_load": list(structure.uniform_load_for_element(element))}
                         for element in structure.elements
                         if any(abs(v) > 1e-12 for v in structure.uniform_load_for_element(element))],
    }


def engineering_inputs(preset):
    """Exclude presentation state from the solved-input identity."""
    result = {key: value for key, value in preset.items() if key not in {"display", "finishes", "version"}}
    display = preset.get("display") or {}
    result["analysis_settings"] = {key: display[key] for key in (
        "second_order_analysis", "eave_deflection_limit_override", "serviceability_limit_overrides"
    ) if key in display}
    return result


def input_digest(preset):
    return hashlib.sha256(json.dumps(engineering_inputs(preset), sort_keys=True, default=str).encode()).hexdigest()


def capacity_working(properties, actions):
    """Use the production capacity functions for the worked intermediate values."""
    rows = [
        ("Design section compression", "phi Ns = phi x kf x An x fy / 1000", steel.compression_section_capacity_kn(properties), "kN"),
        ("Gross tension yield", "Ag x fy / 1000", properties.area_mm2 * properties.fy_mpa / 1000, "kN"),
        ("Net tension fracture", "kt x An x fu / 1000", properties.kt * properties.design_net_area_mm2 * properties.fu_mpa / 1000, "kN"),
    ]
    local = steel.hollow_local_compression_capacity_info(properties)
    if local:
        rows.extend([
            ("Hollow-wall slenderness", "lambda_e = flat width / wall thickness x sqrt(fy/250)", local["slenderness"], ""),
            ("Hollow-wall effective-area factor", "min[1, slenderness limit / lambda_e]", local["effective_area_factor"], ""),
            ("Hollow-wall local capacity", "phi x effective-area factor x Ag x fy / 1000", local["capacity_kn"], "kN"),
        ])
    for axis, length, radius in (("y", actions.effective_length_y_mm, properties.radius_y_mm),
                                 ("z", actions.effective_length_z_mm, properties.radius_z_mm)):
        if length and radius > 0:
            slenderness = length / radius * math.sqrt(properties.kf * properties.fy_mpa / 250)
            rows.extend([
                (f"Normalised slenderness ({axis})", f"({length:.1f} / {radius:.3f}) x sqrt({properties.kf:.3f} x {properties.fy_mpa:.1f} / 250)", slenderness, ""),
                (f"Buckling reduction ({axis})", "Production compression-slenderness reduction, alpha_c", steel.compression_slenderness_reduction(slenderness, properties.alpha_b), ""),
            ])
    ms = steel.section_moment_capacity_knm(properties)
    mo = steel.reference_buckling_moment_knm(properties, actions)
    rows.extend([
        ("Design section moment", "phi Ms = phi x fy x S / 10^6", ms, "kNm"),
        ("Reference buckling moment", "Mo from effective length, E, Iminor, GJ and warping Iw", mo, "kNm"),
        ("Bending reduction", "alpha_s = min[1, 0.6(sqrt((Ms/Mo)^2 + 3) - Ms/Mo)]", steel.bending_slenderness_reduction(ms, mo), ""),
        ("Shear area", "Aw from the selected section geometry", steel.shear_area_mm2(properties), "mm2"),
        ("Unfactored uniform shear", "Vu from web slenderness and shear yield", steel.uniform_shear_capacity_kn(properties), "kN"),
        ("Shear stress distribution", "Adopted section-type shear distribution", steel.shear_stress_distribution(properties), ""),
    ])
    if properties.web_thickness_mm and properties.depth_mm and "CHS" not in properties.name.upper():
        depth = steel.clear_web_depth_mm(properties) or properties.depth_mm
        slenderness = depth / properties.web_thickness_mm
        limit = 82 / math.sqrt(properties.design_fyw_mpa / 250)
        rows.extend([
            ("Web slenderness", f"dp/tw = {depth:.3f} / {properties.web_thickness_mm:.3f}", slenderness, ""),
            ("Shear slenderness limit", f"82 / sqrt({properties.design_fyw_mpa:g} / 250)", limit, ""),
            ("Uniform web shear factor", "alpha_v = min[1, (limit / (dp/tw))^2]", min(1, (limit / slenderness) ** 2), ""),
        ])
    return rows


def source_for_check(key, envelope, group):
    source_key = {"compression": "compression_kn", "local_compression": "compression_kn",
                  "tension": "tension_kn", "shear": "shear_kn", "bending": "moment_knm"}.get(key)
    if key == "bending" and group in CHORD_GROUPS:
        item = envelope.get("compression_kn", {})
        return {"combo": item.get("combo", "-"), "member": item.get("coincident_moment_member", f"{group} average"),
                "basis": "Chord-average bending in the compression-governing case"}
    if source_key:
        return copy.deepcopy(envelope.get(source_key, {}))
    return {"combo": "Envelope interaction", "member": envelope.get("compression_kn", {}).get("member", "-") if group in WEB_GROUPS else "Group envelope",
            "basis": "Uses the governing axial and bending envelopes; these may originate in different cases."}


def member_record(window, label, profile, envelope, group):
    result, actions, restraint, notes = window.resolved_member_check(profile, envelope, group)
    properties = profile.check_properties()
    checks = [{"key": key, **asdict(check), "source": source_for_check(key, envelope, group)}
              for key, check in result.checks.items()]
    ratio, governing = result.governing_ratio, result.governing_check
    if group in CHORD_GROUPS:
        from main import MAX_DOUBLED_CHORD_LENGTH_FRACTION
        extras = [window.doubled_chord_zone_ratio(profile, envelope, group),
                  (window.doubled_chord_length_fraction(group) / MAX_DOUBLED_CHORD_LENGTH_FRACTION, "Doubled chord length limit"),
                  window.web_assignment_chord_joint_ratio(profile, group)]
        for extra_ratio, extra_label in extras:
            if extra_ratio > ratio:
                ratio, governing = extra_ratio, extra_label
    return {"label": label, "group": group, "profile": profile.name,
            "properties": asdict(properties), "actions": asdict(actions), "restraint": copy.deepcopy(restraint),
            "notes": notes, "checks": checks, "envelope": copy.deepcopy(envelope),
            "ratio": ratio, "governing": governing,
            "working": capacity_working(properties, actions)}


def connection_working(window, profile, chord_profile, context, check):
    """Document the implemented connection expression beside its returned value."""
    web, chord = window.hollow_connection_dimensions(profile), window.hollow_connection_dimensions(chord_profile)
    if not web or not chord:
        return []
    from main import WEB_CONNECTION_CFW_LEG_MM
    theta = min(max(float(context.get("branch_angle_deg") or 90), 15), 90)
    axial_ratio = max(float(context.get("chord_axial_ratio") or 0), 0)
    capacity_factor = max(.50, 1 - .35 * min(axial_ratio, 1))
    fu = min(web["fu"], chord["fu"], 490)
    throat = .707 * WEB_CONNECTION_CFW_LEG_MM
    perimeter = web["perimeter"]
    effective = 2 * min(web["width"], chord["width"]) + 2 * min(web["depth"], chord["depth"])
    label = check["label"]
    formula = {
        "Weld throat": f"phi Vw = 0.8 x 0.6 x fu x throat x perimeter / 1000 = 0.8 x 0.6 x {fu:g} x {throat:g} x {perimeter:g} / 1000",
        "Web wall yield": f"phi Ny = 0.9 x fy,web x t,web x perimeter / 1000 = 0.9 x {web['fy']:g} x {web['wall']:g} x {perimeter:g} / 1000",
        "Web wall fracture": f"phi Nf = 0.9 x fu,web x t,web x perimeter / 1000 = 0.9 x {web['fu']:g} x {web['wall']:g} x {perimeter:g} / 1000",
        "Chord wall shear": f"Capacity = capacity factor x 0.9 x 0.6 x fy,chord x t,chord x Leff / 1000 = {capacity_factor:.6g} x 0.9 x 0.6 x {chord['fy']:g} x {chord['wall']:g} x {effective:g} / 1000",
        "Chord punching": f"Capacity = capacity factor x 0.9 x 0.6 x fy,chord x t,chord x perimeter / 1000 = {capacity_factor:.6g} x 0.9 x 0.6 x {chord['fy']:g} x {chord['wall']:g} x {perimeter:g} / 1000",
        "Chord face yield": f"Capacity = capacity factor x 0.9 x fy,chord x t,chord x Leff / 1000 = {capacity_factor:.6g} x 0.9 x {chord['fy']:g} x {chord['wall']:g} x {effective:g} / 1000",
    }.get(label)
    if label == "K face plast":
        peer_width = float((context.get("k_joint") or {}).get("peer_width_mm") or web["width"])
        beta = min((web["width"] + peer_width) / (2 * chord["width"]), 1.5)
        qu = max(4, min(35, 19.16 * (max(beta, .25) / .60) ** .75))
        qf = max(.50, (1 - min(axial_ratio, .95)) ** .10)
        formula = f"Screen capacity = Qu x Qf x fy,chord x t,chord^2 / (sin(theta) x 1000) = {qu:.6g} x {qf:.6g} x {chord['fy']:g} x {chord['wall']:g}^2 / (sin({theta:g} deg) x 1000)"
    if not formula:
        return []
    return [formula + f" = {check['capacity']:.6g} {check['unit']}",
            f"Joint inputs: branch angle {theta:.3f} deg; chord axial ratio {axial_ratio:.6g}; chord capacity factor {capacity_factor:.6g}; "
            f"chord demand share {'0.5 (paired chords)' if context.get('doubled_chord') else '1.0'}; returned demand {check['demand']:.6g} {check['unit']}."]


def web_records(window):
    records = []
    for label, combo, group in window.active_auto_size_group_specs():
        if group not in WEB_GROUPS:
            continue
        assignments = window.auto_web_profile_assignments.get(group, {})
        doubled_sides = window.auto_web_joint_doubler_sides.get(group, set())
        for member_id, demand in sorted((window.design_envelope_actions.get("web_member_demands", {}).get(group) or {}).items(), key=lambda item: int(item[0])):
            profile_name = assignments.get(member_id) or assignments.get(str(member_id))
            profile = window.catalog.get(profile_name) if profile_name else window.selected_profile(combo)
            envelope = window.web_design_envelope(demand, assignments, doubled_sides)
            result = window.member_check_result(profile, envelope, group)
            ratio, governing, _ = window.member_and_connection_ratio(profile, envelope, group)
            connections = []
            for chord_label, chord in zip(("top chord", "bottom chord"), window.web_connection_group_profiles(group)):
                if chord is None:
                    continue
                context = (envelope.get("connection") or {}).get(chord_label, {})
                for check in window.welded_chord_web_connection_checks(profile, chord, envelope["connection_demand_kn"], context):
                    connections.append({**copy.deepcopy(check), "chord": chord_label, "chord_profile": chord.name,
                                        "combo": demand.get("connection_combo", demand.get("combo", "-")),
                                        "working": connection_working(window, profile, chord, context, check),
                                        "context": copy.deepcopy(context)})
            records.append({"group": group, "member": int(member_id), "profile": profile.name,
                            "length_mm": demand["length_mm"], "ratio": ratio, "governing": governing,
                            "compression_kn": demand["compression_kn"], "tension_kn": demand["tension_kn"],
                            "compression_combo": demand.get("compression_combo", demand.get("combo")),
                            "tension_combo": demand.get("tension_combo", demand.get("combo")),
                            "checks": [{"key": key, **asdict(check), "source": source_for_check(key, envelope, group)}
                                       for key, check in result.checks.items()],
                            "properties": asdict(profile.check_properties()),
                            "actions": asdict(window.member_actions_from_envelope(envelope, group)),
                            "working": capacity_working(profile.check_properties(), window.member_actions_from_envelope(envelope, group)),
                            "connections": connections})
    return records


def build_report_data(window, metadata=None):
    if window.structure is None or not window.design_envelope_actions:
        raise ValueError("Generate and solve the frame before exporting computations.")
    cases = copy.deepcopy(window.design_envelope_actions.get("computation_cases") or [])
    if not cases:
        raise ValueError("Computation case evidence was not captured. Export the report again to run a fresh analysis.")
    preset = copy.deepcopy(window.collect_preset_data())
    members = []
    for label, combo, group in window.active_auto_size_group_specs():
        envelope = window.design_envelope_actions.get(group)
        if group in WEB_GROUPS or not window.member_envelope_has_demands(envelope):
            continue
        members.append(member_record(window, label, window.selected_profile(combo), envelope, group))
    wind = copy.deepcopy(window.design_envelope_actions.get("computation_wind") or [])
    if not wind:
        raise ValueError("The adopted wind basis was not captured for this report. Export again to run a fresh analysis.")
    structure = window.structure
    model = {
        "nodes": [{"id": n.id, "x_mm": n.x, "y_mm": n.y,
                   "dofs": [n.ux + 1, n.uy + 1, n.rz + 1],
                   "restraints": [axis for axis, dof in (("Ux", n.ux), ("Uy", n.uy), ("Rz", n.rz)) if dof in structure.restrained_dofs],
                   "springs": {axis: structure.springs[dof] for axis, dof in (("Ux", n.ux), ("Uy", n.uy), ("Rz", n.rz)) if dof in structure.springs}}
                  for n in structure.nodes],
        "elements": [{"id": e.id, "start": e.start.id, "end": e.end.id, "group": e.group,
                      "analysis_profile": e.profile.name, "length_mm": e.length(), "E_mpa": e.E,
                      "area_mm2": e.A, "I_mm4": e.I} for e in structure.elements],
    }
    ratio, group, check = window.auto_size_overall_ratio(window.active_auto_size_group_specs())
    from app_metadata import APP_VERSION
    result = {
        "schema": "portalcalc.computation-report.v1", "software_version": APP_VERSION,
        "issued_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "metadata": dict(metadata or {}), "input_digest": input_digest(preset),
        "inputs": engineering_inputs(preset), "model": model, "cases": cases,
        "wind": wind, "members": members, "webs": web_records(window),
        "overall": {"ratio": ratio, "group": group, "check": check},
        "envelopes": copy.deepcopy({k: v for k, v in window.design_envelope_actions.items() if k not in {"computation_cases", "computation_wind"}}),
        "analysis": copy.deepcopy(window.analysis_info),
        "native_reports": {name: getattr(window, field).toPlainText() for name, field in (
            ("Section checks", "section_checks_box"), ("Analysis output", "results_box"),
            ("Storage loads", "storage_results_box"), ("Foundations", "foundation_box")) if hasattr(window, field)},
    }
    return result
