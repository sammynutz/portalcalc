"""Desktop adapter and evidence/report generation for fixed-layout 3D analysis."""
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import time

import numpy as np

from bracing_layout import BracingLayout, LayoutGeometry
from bracing_analysis import (AnalysisError, PortalReduction, build_braced_building,
                              solve_building_case)
from wind import (CPI_CASE_OPTIONS, active_cpi_for_case, calculate_wind,
                  direction_bearing_degrees, external_pressure_factor,
                  internal_pressure_factor, wind_pressure_scale)


def end_wind_cases(inputs):
    """Simultaneous signed front/back pressures, both directions, ULS/SLS.

    Reversing wind swaps windward/leeward end identities for the existing Cpi
    model. End +/- are roof coefficient branches, not opposite end directions.
    """
    cases = []
    area = inputs.building_width_m * inputs.eave_height_m
    rise = inputs.building_width_m * math.tan(math.radians(inputs.roof_pitch_deg))
    area += inputs.building_width_m * rise * (.25 if inputs.roof_shape == "Gable" else .5)
    for reverse in (False, True):
        source = "Any" if inputs.orientation == "Any" else (
            (180. if reverse else 0.) - direction_bearing_degrees(inputs.orientation)) % 360.
        directional = replace(inputs, orientation=source,
                              front_wall_clad=inputs.back_wall_clad if reverse else inputs.front_wall_clad,
                              back_wall_clad=inputs.front_wall_clad if reverse else inputs.back_wall_clad)
        wind = calculate_wind(directional)
        for cpi_case in CPI_CASE_OPTIONS:
            cpi = active_cpi_for_case(wind, cpi_case, "End +")
            for level, pressure_type in (("ULS", "ultimate"), ("SLS", "serviceability")):
                scale = wind_pressure_scale(wind, pressure_type)
                inward = []
                components = []
                for clad, cpe, surface in ((directional.front_wall_clad, .7, "windward_wall"),
                                           (directional.back_wall_clad, wind.leeward_wall_cpe, "leeward_wall")):
                    external_factor = external_pressure_factor(wind, surface, max(area, .01))
                    internal_factor = internal_pressure_factor(wind, cpi)
                    external = cpe * wind.wu_kn_m2 * scale * external_factor
                    internal = cpi * wind.wu_kn_m2 * scale * internal_factor
                    inward.append(external-internal if clad else 0.)
                    components.append({"surface": surface, "clad": bool(clad), "cpe": cpe,
                                       "external_factor": external_factor, "internal_factor": internal_factor,
                                       "external_kpa": external, "internal_kpa": internal,
                                       "inward_net_kpa": inward[-1]})
                # +Y points front to back. Back inward pressure therefore acts -Y.
                front, back = (inward[1], -inward[0]) if reverse else (inward[0], -inward[1])
                cases.append({"name": f"{level} / {'Back to front' if reverse else 'Front to back'} / {cpi_case}",
                              "level": level, "front_kpa": front, "back_kpa": back,
                              "source_direction": source, "cpi_case": cpi_case, "cpi": cpi,
                              "wind_pressure_scale": scale, "wu_kpa": wind.wu_kn_m2,
                              "end_wall_area_m2": area, "components_windward_leeward": components})
    if not any(abs(case["front_kpa"]) + abs(case["back_kpa"]) > 1e-12 for case in cases):
        raise AnalysisError("No end-wall pressure loads: this stage requires at least one clad end wall; roof drag is not included")
    return cases


def prepare_context(window, allow_unconfigured=False):
    nominal = window.current_bracing_layout_geometry()
    if window.bracing_location_layout is None and not allow_unconfigured:
        raise AnalysisError("Save a bracing layout and pin the brace panels to analyse first")
    saved = BracingLayout.from_dict(window.bracing_location_layout) if window.bracing_location_layout is not None else BracingLayout(nominal)
    if saved.geometry != nominal and not allow_unconfigured:
        raise AnalysisError("Building dimensions changed. Review and save the bracing locations before analysis")
    if not saved.pinned and not allow_unconfigured:
        raise AnalysisError("Pin the brace panels to analyse in the 3D location editor first. Available panels are not installed braces.")
    if window.structure is None:
        raise AnalysisError("A current 2D portal model is required")
    if any(e.group.startswith(("LEFT_CANOPY", "RIGHT_CANOPY", "LEFT_LEAN", "RIGHT_LEAN")) for e in window.structure.elements):
        raise AnalysisError("The first 3D analysis supports the main building only; remove canopies/lean-tos for this analysis")
    structure = copy.deepcopy(window.structure)
    structure.clear_loads()
    top = [e for e in structure.elements if e.group == "TOP"]
    if not top:
        raise AnalysisError("No main portal roof elements are available")
    nodes = [n for e in top for n in (e.start, e.end)]
    left, right = min(nodes, key=lambda n: n.x), max(nodes, key=lambda n: n.x)
    # The editor stores nominal building dimensions. Analysis must use actual
    # column/chord centrelines, including the desktop's section offsets.
    span = right.x-left.x
    g = LayoutGeometry.create(span, nominal.length_mm, left.y, nominal.pitch_deg, nominal.roof_shape,
                              bay_stations_mm=nominal.bay_stations_mm,
                              end_wall_stations_mm=[x*span/nominal.span_mm for x in nominal.end_wall_stations_mm])
    layout = BracingLayout(g, saved.blocked, saved.pinned) if saved.geometry == nominal else BracingLayout(g)
    # Remove legacy eave restraints in the private copy. Otherwise the 3D
    # network would inherit restraint credit from the system it is analysing.
    for node in (left, right):
        structure.springs.pop(node.ux, None)
        structure.restrained_dofs = [d for d in structure.restrained_dofs if d != node.ux]
    points = [(left.x+x, g.roof_z(x)) for x in g.roof_stations]
    portal = PortalReduction(structure, points)
    wind_inputs = window.wind_inputs_from_ui(g.span_mm, g.pitch_deg, g.eave_mm)
    cases = end_wind_cases(wind_inputs)
    source = copy.deepcopy(window.collect_preset_data())
    profile = (window.end_wall_mullion_design or {}).get("selected") or {}
    installed = {}
    purlin_check = window.purlin_check or {}
    for line, x in enumerate(g.roof_stations):
        points = (window.purlin_layout.points if window.purlin_layout else [])
        if not points:
            continue
        px, pz = min(points, key=lambda p: abs(p[0]-(left.x+x)))
        proximity = min(math.hypot(px-left.x, pz-left.y), math.hypot(px-right.x, pz-right.y))
        edge = proximity < float(purlin_check.get("local_dimension_a_m", 0))*1000
        match = purlin_check.get("local_match" if edge else "match", {})
        if match.get("status") != "OK":
            continue
        prefix = "local_" if edge else ""
        area = match["area_mm2"]
        w = area*7.85e-6*9.80665
        installed[str(line)] = {"section": match["section"], "area_mm2": area,
            "bending_ratio": max(purlin_check[prefix+"outward_kn_m"]/match["outward_capacity_kn_m"],
                                 purlin_check[prefix+"inward_strength_kn_m"]/match["inward_strength_capacity_kn_m"]),
            "service_ratio": purlin_check[prefix+"inward_service_kn_m"]/match["inward_service_capacity_kn_m"],
            "added_bending_ratio": 1.2*w/match["inward_strength_capacity_kn_m"],
            "added_service_ratio": w/match["inward_service_capacity_kn_m"],
            "basis_key": f"{match['section']}|{purlin_check['span_type']}|{match.get('tables')}|{match.get('outward_bridging')}|{match.get('inward_bridging')}",
            "zone": "edge" if edge else "general", "nearest_installed_x_mm": px-left.x,
            "node_offset_mm": px-left.x-x, "bending_snapshot": copy.deepcopy(match),
            "source": "Existing Metroll bending-table check; axial member compression/tension calculated or separately declared in the 3D workspace"}
        installed[str(line)]["bending_span_mm"] = purlin_check["span_mm"]
        installed[str(line)]["beam_inputs"] = {"span_type": purlin_check["span_type"],
            "outward_kn_m": purlin_check[prefix+"outward_kn_m"],
            "inward_strength_kn_m": purlin_check[prefix+"inward_strength_kn_m"],
            "service_kn_m": purlin_check[prefix+"inward_service_kn_m"],
            "service_outward_kn_m": purlin_check[prefix+"wind_service_upward_kpa"]*purlin_check['spacing_mm']/1000,
            "service_inward_kn_m": purlin_check[prefix+"wind_service_downward_kpa"]*purlin_check['spacing_mm']/1000,
            "permanent_kn_m": purlin_check["permanent_kpa"]*purlin_check["spacing_mm"]/1000,
            "gravity_service_kn_m": (purlin_check["permanent_kpa"]+purlin_check["live_kpa"])*purlin_check["spacing_mm"]/1000}
        installed[str(line)]["bending_key"] = hashlib.sha256(json.dumps({
            key: installed[str(line)][key] for key in ("section", "area_mm2", "bending_ratio", "service_ratio",
                "added_bending_ratio", "added_service_ratio", "basis_key", "bending_span_mm", "bending_snapshot", "beam_inputs")
        }, sort_keys=True).encode()).hexdigest()
    from purlin_strut_tables import snapshot as strut_table_snapshot
    return {
        "layout": layout, "nominal_layout": saved.to_dict(), "nominal_geometry": nominal, "portal": portal, "cases": cases,
        "roof_brace": copy.deepcopy(window.selected_brace_size(window.roof_brace_size_combo)),
        "wall_brace": copy.deepcopy(window.selected_brace_size(window.wall_brace_size_combo)),
        "end_brace": copy.deepcopy(window.selected_end_wall_brace_size()),
        "end_brace_options": [copy.deepcopy(window.end_wall_brace_size_combo.itemData(i))
                              for i in range(window.end_wall_brace_size_combo.count())],
        "source_preset": source,
        "source_hash": hashlib.sha256(json.dumps(source, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "default_end_post": profile.get("section"),
        "front_clad": wind_inputs.front_wall_clad, "back_clad": wind_inputs.back_wall_clad,
        "cost_rates": {"shs": window.costing_rate_from_input(window.shs_cost_input, "shs_rate_per_kg"),
                       "ub": window.costing_rate_from_input(window.ub_cost_input, "ub_rate_per_kg"),
                       "wb": window.costing_rate_from_input(window.wb_cost_input, "wb_rate_per_kg"),
                       "purlin": window.costing_rate_from_input(window.purlin_cost_input, "purlin_rate_per_kg")},
        "installed_purlins": installed,
        "purlin_strut_tables": strut_table_snapshot(window.metroll_tables or {}),
    }


LIMITATIONS = [
    "Fixed pinned-panel layout; unmarked available panels are not installed braces. No automatic layout or section optimisation.",
    "First-order small-displacement elastic analysis, with tension-only rod slackening; no P-delta, buckling, prestress or connection slip.",
    "If zero-force rods are needed for the elastic tangent, equilibrium is reported with stability unverified. A positive taut-rod tangent is not a buckling or code stability check.",
    "End-wall wind only: simultaneous windward/leeward pressures, both directions and both Cpi branches, ULS and SLS. No G/Q, transverse wind, roof drag or full design combinations.",
    "End-wall pressure uses simply supported vertical strips: half of each strip resultant goes to roof and half directly to ground. Open ends receive no cladding pressure.",
    "Identical actual 2D portal sections/supports repeat at every frame line. Legacy eave restraints are removed in a private copy. Portal attachment translations use work-conjugate beam interpolation.",
    "Dedicated axial struts of the selected section run along every roof station. Intermediate end posts are included at clad or end-braced ends, are axial-only and use the selected post section. Their bending design is separate.",
    "Brace base translations are fixed. Foundation flexibility, column weak-axis flexure and connection eccentricities are not modelled. No diaphragm or sheeting stiffness.",
    "Forces are analysis demands only. No brace net-section/connection checks, strut buckling or combined purlin bending/axial capacity checks; a solved model is not a passing design.",
    "Results do not replace Phase 1 checks, alter the 2D portal springs, section selections, costing or the full-computations export.",
]


def analyse_context(context, strut_profile, end_post_profile):
    start = time.perf_counter()
    layout, portal = context["layout"], context["portal"]
    post_faces = {face for face, clad in (("front_end", context["front_clad"]), ("back_end", context["back_clad"]))
                  if clad or any(layout.panels[p].face == face for p in layout.pinned)}
    from bracing_components import StrutPlan, assembly_properties, wire_modulus
    if isinstance(strut_profile, StrutPlan):
        expected = {f"strut/bay/{i}/line/{j}" for i in range(len(layout.geometry.bay_stations_mm)-1)
                    for j in range(len(layout.geometry.roof_stations))}
        if set(strut_profile.members) != expected:
            raise AnalysisError("Strut plan must specify every current bay and roof line exactly once")
    used = {"roof_brace" if layout.panels[p].face.startswith("roof") else "end_brace" if layout.panels[p].face in {"front_end", "back_end"} else "wall_brace" for p in layout.pinned}
    moduli = {group: wire_modulus(context[group]) if group in used and context[group].get("component_type") == "wire_rope" else 200000.
              for group in ("roof_brace", "wall_brace", "end_brace")}
    model, roof_grid, portal_dofs, point_nodes = build_braced_building(
        layout, portal, strut_profile.A, context["roof_brace"]["area_mm2"],
        context["wall_brace"]["area_mm2"], context["end_brace"]["area_mm2"], end_post_profile.A,
        end_post_faces=post_faces,
        strut_overrides=strut_profile.overrides() if isinstance(strut_profile, StrutPlan) else None,
        brace_moduli=moduli)
    cases = []
    for case in context["cases"]:
        try:
            cases.append(solve_building_case(model, layout, portal, portal_dofs, point_nodes, case))
        except AnalysisError as error:
            raise AnalysisError(f"{case['name']}: {error}") from None
    envelopes = []
    for i, member in enumerate(model.members):
        strength = [(case["members"][i]["axial_n"], case["case"]["name"]) for case in cases if case["case"]["level"] == "ULS"]
        tension = max(strength, key=lambda item: item[0])
        compression = min(strength, key=lambda item: item[0])
        envelopes.append({"id": member.id, "kind": member.kind, "panel_id": member.panel_id,
                          "tension_n": max(0., tension[0]), "tension_case": tension[1],
                          "compression_n": max(0., -compression[0]), "compression_case": compression[1]})
    roof_nodes = {n for row in roof_grid for n in row}
    sls = [(float(np.linalg.norm(displacement)), case["case"]["name"], i, displacement)
           for case in cases if case["case"]["level"] == "SLS"
           for i, displacement in enumerate(case["displacements_mm"]) if i in roof_nodes]
    governing = max(sls, key=lambda item: item[0])
    strut_record = {"section": strut_profile.name, "area_mm2": strut_profile.A}
    if isinstance(strut_profile, StrutPlan):
        strut_record.pop("area_mm2")
        strut_record["per_member"] = {key: assembly_properties(value) for key, value in strut_profile.members.items()}
    return {
        "schema": "portalcalc.fixed-bracing-analysis.v1", "status": "SOLVED_DEMANDS_ONLY",
        "created_utc": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.perf_counter()-start,
        "source_hash": context["source_hash"], "source_preset": context["source_preset"],
        "nominal_layout": context["nominal_layout"], "analysis_layout": layout.to_dict(),
        "sections": {"strut": strut_record,
                     "end_post": {"section": end_post_profile.name, "area_mm2": end_post_profile.A},
                     **{kind: context[kind] for kind in ("roof_brace", "wall_brace", "end_brace")}},
        "nodes": [{"index": i, "label": model.node_labels[i], "point_mm": p} for i, p in enumerate(model.nodes)],
        "portal_full_dofs": len(portal.k), "portal_interface_dofs": len(portal.stiffness),
        "member_envelopes": envelopes, "cases": cases,
        "governing_sls_displacement": {"resultant_mm": governing[0], "case": governing[1],
                                       "node": governing[2], "components_mm": governing[3]},
        "limitations": LIMITATIONS,
    }


def text_report(result):
    g = result["analysis_layout"]["geometry"]
    checked = "design" in result
    lines = ["FIXED-LAYOUT 3D BRACING ANALYSIS", ("Scoped member checks attached; connection acceptance and full combinations remain separate." if checked else "Solved demands only - section and connection checks remain separate."),
             f"Created: {result['created_utc']}", f"Source SHA256: {result['source_hash']}", "",
             "MODEL AND INPUTS", f"Actual portal centreline span: {g['span_mm']/1000:.4f} m; eave: {g['eave_mm']/1000:.4f} m; pitch: {g['pitch_deg']:g} deg",
             f"Length: {g['length_mm']/1000:g} m; portals: {len(g['bay_stations_mm'])}; pinned X panels: {len(result['analysis_layout']['pinned_panels'])}",
             f"Nodes: {len(result['nodes'])}; free DOFs: {result['cases'][0]['free_dofs']}; each portal reduced {result['portal_full_dofs']} -> {result['portal_interface_dofs']} DOFs",
             f"Analysis time: {result['elapsed_seconds']:.3f} s", ""]
    for kind, section in result["sections"].items():
        if "per_member" in section:
            lines.append(f"{kind.title()}: per-location arrangements ({len(section['per_member'])} paths); see frozen component properties")
        elif section.get("component_type") == "wire_rope":
            lines.append(f"{kind}: {section['label']}; declared rope EA / design capacity: {section.get('rating', {})}")
        elif section.get("component_type") == "scheduled_rod":
            lines.append(f"{kind}: {section['label']}; Grade {section['grade_mpa']:g}; supplied factored tension {section['design_tension_kn']:g} kN; forged-rated {section['forged_turnbuckle']} / Grade S {section['grade_s_turnbuckle']}; {section['capacity_source']}")
        else:
            lines.append(f"{kind.replace('_', ' ').title()}: {section.get('section', section.get('label', '-'))}; A = {section['area_mm2']:.1f} mm2; E = 200000 MPa")
    lines += ["", "SCOPE AND ASSUMPTIONS"] + [f"- {item}" for item in result["limitations"]]
    lines += ["", "ULS MEMBER DEMAND ENVELOPES (positive magnitudes, kN)", "Member | Type | Tension | Compression | Governing source"]
    for item in result["member_envelopes"]:
        source = item["tension_case"] if item["tension_n"] >= item["compression_n"] else item["compression_case"]
        lines.append(f"{item['id']} | {item['kind']} | {item['tension_n']/1000:.3f} | {item['compression_n']/1000:.3f} | {source}")
    sls = result["governing_sls_displacement"]
    lines += ["", "SERVICEABILITY MOVEMENT (no acceptance limit applied)",
              f"Maximum resultant: {sls['resultant_mm']:.4f} mm at node {sls['node']+1}; {sls['case']}", ""]
    for case in result["cases"]:
        lines += ["="*75, case["case"]["name"], f"Source direction: {case['case']['source_direction']}; Cpi: {case['case']['cpi']:.4f}",
                  f"Signed front/back pressure (+Y): {case['case']['front_kpa']:.5f} / {case['case']['back_kpa']:.5f} kPa",
                  f"Nonlinear iterations: {case['iterations']}; free-DOF residual: {case['residual_n']:.3g} N",
                  f"Elastic tangent status: {case['tangent_status']}",
                  f"Global force residual [N]: {case['balance_force_error_n']}",
                  f"Global moment residual [Nmm]: {case['balance_moment_error_nmm']}"]
        for work in case["load_workings"]:
            lines.append(f"{work['face']}: roof {work['roof_resultant_n']/1000:.3f} kN; direct ground {work['direct_ground_resultant_n']/1000:.3f} kN")
        lines += ["", "MEMBER FORCES | N kN (+tension) | Extension mm | State"]
        lines += [f"{m['id']} | {m['axial_n']/1000:.4f} | {m['extension_mm']:.5f} | {m['state']}" for m in case["members"]]
        lines += ["", "NODAL MOVEMENTS | X / Y / Z mm"]
        lines += [f"{n['index']+1}: {n['label']} | " + " / ".join(f"{v:.5f}" for v in d)
                  for n, d in zip(result["nodes"], case["displacements_mm"])]
        lines += ["", "BRACE/END-POST GROUND REACTIONS | X / Y / Z kN"]
        lines += [f"{r['label']} | " + " / ".join(f"{v/1000:.5f}" for v in r["force_n"]) for r in case["support_reactions_n"]]
        for portal in case["portals"]:
            lines += ["", f"PORTAL {portal['index']+1} at Y = {portal['y_mm']/1000:g} m: support reactions | X kN / Z kN / Mxz kNm"]
            lines += [f"Node {r['node']} | {r['fx_n']/1000:.5f} / {r['fz_n']/1000:.5f} / {r['m_xz_nmm']/1e6:.5f}" for r in portal["support_reactions"]]
    lines += ["", "The JSON evidence additionally contains every recovered portal nodal movement, interface force and signed local member end-force vector (N, N, Nmm at each end)."]
    return "\n".join(lines)
