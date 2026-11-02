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
                  direction_bearing_degrees, area_reduction_factor,
                  leeward_wall_cpe, wind_pressure_scale)


END_WIND_PRESSURE_SOURCE = 'AS/NZS 1170.2:2021 (Amendment 2), Table 5.2(B) end-wind branch and clause 5.4.3'
END_WIND_PRESSURE_URL = 'https://webstore.ansi.org/preview-pages/sai/preview_00-2666920554-primary.pdf'


def end_wind_combination_factors(inputs,cpi):
    """Count applied end-wall/internal surfaces; no absent roof contribution."""
    external=int(bool(inputs.front_wall_clad))+int(bool(inputs.back_wall_clad))
    internal=external>0 and abs(cpi)>=.4
    count=external+int(internal)
    kce=.8 if count>=3 else .9 if count==2 else 1.
    kci=kce if internal else 1.
    return kce,kci,count


def roof_friction_drag(inputs, q_kpa, mean_height_m, reverse=False, roof_profile='5-Rib (Trimdek)'):
    """External roof component of clause 5.5; ribs run down the roof slope.

    Open-sided buildings retain the same external-roof calculation as an
    explicit preliminary extension of the enclosed-building method. This is
    not a free-roof/underside or exposed-member drag assessment.
    """
    coefficients = {'5-Rib (Trimdek)': .04, 'Corry (Corodek)': .02}
    if roof_profile not in coefficients:
        raise AnalysisError(f'Roof friction coefficient is not configured for {roof_profile!r}')
    b, d, h = inputs.building_width_m, inputs.building_length_m, mean_height_m
    cutoff = 4*min(h,b)
    start, end = (0., max(0.,d-cutoff)) if reverse else (min(cutoff,d),d)
    area = b*max(0.,d-cutoff)
    cf = coefficients[roof_profile]
    enclosed = all((inputs.front_wall_clad,inputs.back_wall_clad,inputs.left_wall_clad,inputs.right_wall_clad))
    return {'profile':roof_profile,'cf':cf,'surface':'External roof; sheet ribs/corrugations across longitudinal wind',
            'q_kpa':q_kpa,'signed_y_pressure_kpa':(-1 if reverse else 1)*q_kpa*cf,
            'mean_roof_height_m':h,'breadth_m':b,'depth_m':d,'d_over_h':d/h,'d_over_b':d/b,
            'windward_exclusion_m':cutoff,'y_start_m':start,'y_end_m':end,
            'projected_area_m2':area,'signed_y_force_kn':(-1 if reverse else 1)*q_kpa*cf*area,
            'application':'Enclosed building' if enclosed else 'PRELIMINARY: enclosed-building external-roof method extended to an open-sided building',
            'preliminary_open_sided':not enclosed,'source':'AS/NZS 1170.2:2021 (Amendment 2), clauses 2.4.2, 5.5 and Table 5.9',
            'url':END_WIND_PRESSURE_URL,
            'basis':'Projected roof area b * max(d - 4*min(h,b), 0); full force delivered to roof nodes. No Ka/Kce/Kci reduction. No wall, underside or exposed-member drag.'}


def end_wind_cases(inputs, roof_profile='5-Rib (Trimdek)'):
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
        # Wind runs along the building length, parallel to the gable ridge.
        # Use d=length, b=span and the all-pitches end-wind coefficient branch,
        # not the main transverse frame's span/length and roof-pitch lookup.
        end_ratio=directional.building_length_m/directional.building_width_m
        end_cpe=leeward_wall_cpe(end_ratio,0.)
        wind=replace(wind,leeward_wall_cpe=end_cpe)
        for cpi_case in CPI_CASE_OPTIONS:
            cpi = active_cpi_for_case(wind, cpi_case, "End +")
            kce,kci,surface_count=end_wind_combination_factors(directional,cpi)
            for level, pressure_type in (("ULS", "ultimate"), ("SLS", "serviceability")):
                scale = wind_pressure_scale(wind, pressure_type)
                inward = []
                components = []
                for clad, cpe, surface in ((directional.front_wall_clad, .7, "windward_wall"),
                                           (directional.back_wall_clad, end_cpe, "leeward_wall")):
                    ka=area_reduction_factor(wind,surface,max(area,.01))
                    external_factor = max(ka*kce,.8)
                    internal_factor = kci
                    external = cpe * wind.wu_kn_m2 * scale * external_factor
                    internal = cpi * wind.wu_kn_m2 * scale * internal_factor
                    inward.append(external-internal if clad else 0.)
                    physical_face=(('back_end' if reverse else 'front_end') if surface=='windward_wall'
                                   else ('front_end' if reverse else 'back_end'))
                    components.append({"surface": surface, "clad": bool(clad), "cpe": cpe,
                                       'face':physical_face,
                                       "external_factor": external_factor, "internal_factor": internal_factor,
                                       'area_reduction_factor':ka,'kce':kce,'kci':kci,
                                       "external_kpa": external, "internal_kpa": internal,
                                       "inward_net_kpa": inward[-1]})
                # +Y points front to back. Back inward pressure therefore acts -Y.
                front, back = (inward[1], -inward[0]) if reverse else (inward[0], -inward[1])
                cases.append({"name": f"{level} / {'Back to front' if reverse else 'Front to back'} / {cpi_case}",
                              "level": level, "front_kpa": front, "back_kpa": back,
                              "source_direction": source, "cpi_case": cpi_case, "cpi": cpi,
                              "wind_pressure_scale": scale, "wu_kpa": wind.wu_kn_m2,
                              "end_wall_area_m2": area, "components_windward_leeward": components,
                              'roof_drag':roof_friction_drag(directional,wind.wu_kn_m2*scale,wind.h_m,reverse,roof_profile)})
                cases[-1]['pressure_basis']={'alongwind_depth_m':directional.building_length_m,
                    'crosswind_breadth_m':directional.building_width_m,'d_over_b':end_ratio,
                    'leeward_cpe':end_cpe,'effective_surface_count':surface_count,
                    'kce':kce,'kci':kci,'source':END_WIND_PRESSURE_SOURCE,'url':END_WIND_PRESSURE_URL,
                    'scope':'Normal end-wall pressures plus external roof friction; wall/underside/exposed-member drag and transverse-wind cases excluded'}
    if not any(abs(case["front_kpa"]) + abs(case["back_kpa"]) > 1e-12 for case in cases):
        raise AnalysisError("No end-wall pressure loads: this model requires at least one clad end wall; a free-roof wind model is not included")
    return cases


def apex_tributary_widths(points, apex):
    """Half the adjacent along-roof sheet span, independently on each slope."""
    widths = []
    for side in (-1, 1):
        distances = [math.hypot(x-apex[0], z-apex[1]) for x,z in points if side*(x-apex[0]) > 1e-5]
        if not distances:
            return []  # Do not credit a reduction without actual adjacent rows.
        widths.append(min(distances)/2)
    return widths


def eave_tributary_width(points, eave, roof_end):
    """Half the first actual sheet span plus support inset from the roof edge."""
    sign = 1 if roof_end[0]>eave[0] else -1
    rows = sorted({(float(x),float(z)) for x,z in points
                   if sign*(x-eave[0])>=-1e-5 and sign*(roof_end[0]-x)>=-1e-5},
                  key=lambda p:sign*(p[0]-eave[0]))
    if len(rows)<2:
        return None
    span = math.dist(rows[0],rows[1])
    inset = math.dist(eave,rows[0])
    return {'width_mm':span/2+inset,'adjacent_sheet_span_mm':span,'sheet_edge_offset_mm':inset}


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
    source = copy.deepcopy(window.collect_preset_data())
    cases = end_wind_cases(wind_inputs,source['finishes']['roof_profile'])
    profile = (window.end_wall_mullion_design or {}).get("selected") or {}
    main_mullions = window.end_wall_mullion_design or {}
    governing = main_mullions.get('governing_mullion') or {}
    mullion_basis = ({'strength_pressure_kpa':main_mullions['strength_pressure_kpa'],
        'service_pressure_kpa':main_mullions['service_kpa'],'deflection_divisor':main_mullions['deflection_divisor'],
        'effective_length_factor':profile['effective_length_mm']/governing['height_mm'],
        'moment_modifier':profile['actions'].moment_modifier,'restraint':main_mullions['restraint'],
        'governing_height_mm':governing['height_mm'],'governing_width_mm':governing['tributary_width_mm'],
        'mullions':copy.deepcopy(main_mullions['mullions']),
        'basis':'Main calculation wind envelope, catalogue section properties and mid-height flybrace basis. Main baseline strength/serviceability remain prerequisites; 3D case axial demand is added to the local wind-bending envelope.'}
        if profile and governing else None)
    installed = {}
    purlin_check = window.purlin_check or {}
    for line, x in enumerate(g.roof_stations):
        points = (window.purlin_layout.points if window.purlin_layout else [])
        if not points:
            continue
        px, pz = min(points, key=lambda p: abs(p[0]-(left.x+x)))
        is_eave_strut = abs(x)<1e-4 or abs(x-g.span_mm)<1e-4
        if is_eave_strut:
            # The end roof line is an idealised interface for the strut at
            # the first inboard purlin. Pick that physical purlin's section
            # and wind zone, rather than the sheet-edge eave purlin's.
            inward = 1 if abs(x)<1e-4 else -1
            slope_end = g.span_mm/2 if g.roof_shape=='Gable' else (g.span_mm if inward==1 else 0.)
            candidates = [p for p in points if inward*(p[0]-left.x-x)>1e-4
                          and inward*(left.x+slope_end-p[0])>=-1e-4]
            if candidates:
                px,pz = min(candidates,key=lambda p:abs(p[0]-left.x-x))
        proximity = min(math.hypot(px-left.x, pz-left.y), math.hypot(px-right.x, pz-right.y))
        edge = proximity < float(purlin_check.get("local_dimension_a_m", 0))*1000
        match = purlin_check.get("local_match" if edge else "match", {})
        if match.get("status") != "OK":
            continue
        prefix = "local_" if edge else ""
        area = match["area_mm2"]
        w = area*7.85e-6*9.80665
        apex_pair = g.roof_shape == 'Gable' and g.pitch_deg > 0 and abs(x-g.span_mm/2) < 1e-4
        original_width = float(purlin_check['spacing_mm'])
        apex_widths = apex_tributary_widths(points, (px,pz)) if apex_pair else []
        eave_data = None
        if is_eave_strut:
            roof_end_x = g.span_mm/2 if g.roof_shape=='Gable' else (g.span_mm if abs(x)<1e-4 else 0.)
            eave_data = eave_tributary_width(points,(left.x+x,g.roof_z(x)),(left.x+roof_end_x,g.roof_z(roof_end_x)))
            if eave_data:
                # This axial strut aligns with the first inboard roof purlin,
                # not the sheet-edge eave purlin. Retain edge geometry only as
                # evidence; its half tributary is not the strut's roof duty.
                eave_data['sheet_edge_tributary_width_mm'] = eave_data['width_mm']
                eave_data['width_mm'] = original_width
                eave_data['strut_alignment'] = 'First inboard purlin; full roof spacing'
        tributary_width = eave_data['width_mm'] if eave_data else max(apex_widths) if apex_widths else original_width
        width_factor = tributary_width/original_width
        installed[str(line)] = {"section": match["section"], "area_mm2": area,
            'installed_count': 2 if apex_pair else 1,
            'installed_count_source': ('User-confirmed apex pair: one purlin from each roof slope'
                if apex_pair else 'Single installed roof-purlin line'),
            'tributary_width_mm': tributary_width, 'original_roof_spacing_mm': original_width,
            'apex_tributary_widths_mm': apex_widths,
            'eave_tributary':eave_data,
            'tributary_width_source': ('Half the actual adjacent sheet end span on each slope; larger per-ply width used if unequal'
                if apex_widths else 'First inboard purlin alignment: full roof-check spacing; sheet-edge half tributary does not apply to the strut' if eave_data else 'Full installed roof-check spacing envelope'),
            "bending_ratio": max(purlin_check[prefix+"outward_kn_m"]/match["outward_capacity_kn_m"],
                                 purlin_check[prefix+"inward_strength_kn_m"]/match["inward_strength_capacity_kn_m"]),
            "service_ratio": purlin_check[prefix+"inward_service_kn_m"]/match["inward_service_capacity_kn_m"],
            "added_bending_ratio": 1.2*w/match["inward_strength_capacity_kn_m"],
            "added_service_ratio": w/match["inward_service_capacity_kn_m"],
            "basis_key": f"{match['section']}|{purlin_check['span_type']}|{match.get('tables')}|{match.get('outward_bridging')}|{match.get('inward_bridging')}",
            "zone": "edge" if edge else "general", "nearest_installed_x_mm": px-left.x,
            "node_offset_mm": px-left.x-x, "bending_snapshot": copy.deepcopy(match),
            "source": "Existing Metroll bending-table check; axial member compression/tension calculated or separately declared in the 3D workspace"}
        installed[str(line)]['bending_ratio'] *= width_factor
        installed[str(line)]['service_ratio'] *= width_factor
        installed[str(line)]["bending_span_mm"] = purlin_check["span_mm"]
        installed[str(line)]["beam_inputs"] = {"span_type": purlin_check["span_type"],
            "outward_kn_m": purlin_check[prefix+"outward_kn_m"],
            "inward_strength_kn_m": purlin_check[prefix+"inward_strength_kn_m"],
            "service_kn_m": purlin_check[prefix+"inward_service_kn_m"],
            "service_outward_kn_m": purlin_check[prefix+"wind_service_upward_kpa"]*purlin_check['spacing_mm']/1000,
            "service_inward_kn_m": purlin_check[prefix+"wind_service_downward_kpa"]*purlin_check['spacing_mm']/1000,
            "permanent_kn_m": purlin_check["permanent_kpa"]*purlin_check["spacing_mm"]/1000,
            "gravity_service_kn_m": (purlin_check["permanent_kpa"]+purlin_check["live_kpa"])*purlin_check["spacing_mm"]/1000}
        for key in installed[str(line)]['beam_inputs']:
            if key != 'span_type':
                installed[str(line)]['beam_inputs'][key] *= width_factor
        if eave_data:
            # Use the strut's full loaded area for pressure factors as well.
            area_m2 = tributary_width*purlin_check['span_mm']/1e6
            up,down = window.purlin_design_pressures(wind_inputs,tributary_area_m2=area_m2,edge_zone=edge)
            su,sd = window.purlin_design_pressures(wind_inputs,tributary_area_m2=area_m2,edge_zone=edge,wind_pressure='serviceability')
            loads = installed[str(line)]['beam_inputs'];width_m=tributary_width/1000
            loads.update(outward_kn_m=up*width_m,
                         inward_strength_kn_m=max(1.2*purlin_check['permanent_kpa']+1.5*purlin_check['live_kpa'],1.2*purlin_check['permanent_kpa']+down)*width_m,
                         service_kn_m=max(su,sd)*width_m,service_outward_kn_m=su*width_m,service_inward_kn_m=sd*width_m)
            installed[str(line)]['bending_ratio'] = max(loads['outward_kn_m']/match['outward_capacity_kn_m'],loads['inward_strength_kn_m']/match['inward_strength_capacity_kn_m'])
            installed[str(line)]['service_ratio'] = loads['service_kn_m']/match['inward_service_capacity_kn_m']
            eave_data.update(loaded_area_m2=area_m2,wind_upward_kpa=up,wind_downward_kpa=down)
        installed[str(line)]["bending_key"] = hashlib.sha256(json.dumps({
            key: installed[str(line)][key] for key in ("section", "area_mm2", "bending_ratio", "service_ratio",
                "added_bending_ratio", "added_service_ratio", "basis_key", "bending_span_mm", "bending_snapshot", "beam_inputs",
                'tributary_width_mm', 'original_roof_spacing_mm', 'apex_tributary_widths_mm','eave_tributary')
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
        'global_sls_basis':{'divisor':window.eave_deflection_divisor(),
            'importance_level':wind_inputs.importance_level,
            'source':'Main in-plane eave sway criterion; project default IL1 H/100, IL2+ H/150; main overrides retained'},
        'mullion_basis':mullion_basis,
        "front_clad": wind_inputs.front_wall_clad, "back_clad": wind_inputs.back_wall_clad,
        'face_cladding': {'left_wall': wind_inputs.left_wall_clad, 'right_wall': wind_inputs.right_wall_clad,
                          'front_end': wind_inputs.front_wall_clad, 'back_end': wind_inputs.back_wall_clad},
        "cost_rates": {"shs": window.costing_rate_from_input(window.shs_cost_input, "shs_rate_per_kg"),
                       "ub": window.costing_rate_from_input(window.ub_cost_input, "ub_rate_per_kg"),
                       "wb": window.costing_rate_from_input(window.wb_cost_input, "wb_rate_per_kg"),
                       "purlin": window.costing_rate_from_input(window.purlin_cost_input, "purlin_rate_per_kg"),
                       'braced_panel_installation': (window.bracing_3d_settings or {}).get('installation_cost_per_panel', 50.)},
        "installed_purlins": installed,
        'stressed_skin': copy.deepcopy((window.bracing_3d_settings or {}).get('stressed_skin')),
        "purlin_strut_tables": strut_table_snapshot(window.metroll_tables or {}),
    }


LIMITATIONS = [
    "Fixed pinned-panel layout; unmarked available panels are not installed braces. No automatic layout or section optimisation.",
    "First-order small-displacement elastic analysis, with tension-only rod slackening; no P-delta, buckling, prestress or connection slip.",
    "If zero-force rods are needed for the elastic tangent, equilibrium is reported with stability unverified. A positive taut-rod tangent is not a buckling or code stability check.",
    "Longitudinal wind: simultaneous end-wall pressures and external roof friction, both directions and both Cpi branches, ULS and SLS. No G/Q, transverse wind, wall/underside/exposed-member drag or full design combinations.",
    "Normal roof/side-wall pressures are not applied to the reduced 3D frame. Purlin bending uses a separate roof-load envelope; it is not a simultaneous whole-building pressure pattern.",
    "Roof friction uses the projected external-roof portion of clause 5.5/Table 5.9, with sheeting running down-slope. For open-sided buildings this is a preliminary extension of the enclosed-building method, not a complete open-roof aerodynamic assessment.",
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
    from bracing_diaphragm import DiaphragmSettings, stressed_skin_model, align_fixed_nodes, recover_diaphragm
    diaphragm_settings = DiaphragmSettings.from_dict(context.get('stressed_skin'))
    diaphragm = {'settings': vars(diaphragm_settings), 'active':diaphragm_settings.active}
    sls_model = model
    if diaphragm_settings.active:
        cladding = {'front_end':context['front_clad'], 'back_end':context['back_clad'],
                    **context.get('face_cladding',{})}
        sls_model, diaphragm = stressed_skin_model(model, layout, point_nodes, diaphragm_settings, cladding)
        align_fixed_nodes(model,sls_model)
    cases = []
    for case in context["cases"]:
        try:
            solved = solve_building_case(model, layout, portal, portal_dofs, point_nodes, case)
            if diaphragm_settings.active and case['level'] == 'SLS':
                enhanced = solve_building_case(sls_model, layout, portal, portal_dofs, point_nodes, case)
                solved = recover_diaphragm(enhanced, diaphragm['panels'], len(model.members), solved)
            cases.append(solved)
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
    if diaphragm_settings.active:
        bare = [(float(np.linalg.norm(d)), case['case']['name'], i)
                for case in cases if 'bare_sls' in case
                for i,d in enumerate(case['bare_sls']['displacements_mm']) if i in roof_nodes]
        movement, name, node = max(bare)
        diaphragm['bare_governing_sls'] = {'resultant_mm': movement, 'case': name, 'node': node}
        diaphragm['bare_max_longitudinal_mm'] = max(abs(d[1]) for case in cases if 'bare_sls' in case
            for i,d in enumerate(case['bare_sls']['displacements_mm']) if i in roof_nodes)
        diaphragm['enhanced_max_longitudinal_mm'] = max(abs(d[1]) for case in cases if 'bare_sls' in case
            for i,d in enumerate(case['displacements_mm']) if i in roof_nodes)
    strut_record = {"section": strut_profile.name, "area_mm2": strut_profile.A}
    if isinstance(strut_profile, StrutPlan):
        strut_record.pop("area_mm2")
        strut_record["per_member"] = {key: assembly_properties(value) for key, value in strut_profile.members.items()}
    return {
        'face_cladding': dict(context.get('face_cladding', {})),
        'mullion_basis':copy.deepcopy(context.get('mullion_basis')),
        'global_sls_basis':copy.deepcopy(context.get('global_sls_basis')),
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
        'stressed_skin': diaphragm,
        "governing_sls_displacement": {"resultant_mm": governing[0], "case": governing[1],
                                       "node": governing[2], "components_mm": governing[3]},
        "limitations": ([line.replace('No diaphragm or sheeting stiffness.',
                         'Enabled roof/wall diaphragm stiffness is included in SLS only.') for line in LIMITATIONS]
                        + [diaphragm['basis']] if diaphragm_settings.active else list(LIMITATIONS)),
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
            lines.append(f"{kind}: {section['label']}; adopted rope EA / design capacity: {section.get('rating', {})}")
        elif section.get("component_type") == "scheduled_rod":
            lines.append(f"{kind}: {section['label']}; Grade {section['grade_mpa']:g}; supplied factored tension {section['design_tension_kn']:g} kN; forged-rated {section['forged_turnbuckle']} / Grade S {section['grade_s_turnbuckle']}; {section['capacity_source']}")
        else:
            lines.append(f"{kind.replace('_', ' ').title()}: {section.get('section', section.get('label', '-'))}; A = {section['area_mm2']:.1f} mm2; E = 200000 MPa")
    lines += ["", "SCOPE AND ASSUMPTIONS"] + [f"- {item}" for item in result["limitations"]]
    diaphragm = result.get('stressed_skin', {})
    if diaphragm.get('active',diaphragm.get('settings', {}).get('enabled')):
        settings = diaphragm['settings']
        bare = diaphragm['bare_governing_sls']
        walls = settings.get('side_walls_enabled') or settings.get('end_walls_enabled')
        title = 'SLS STRESSED-SKIN ROOF AND WALL CONTRIBUTION' if walls else 'SLS STRESSED-SKIN ROOF CONTRIBUTION'
        geometry_mode = settings.get('calibration_mode')=='rev_b_geometry'
        c_label = 'reference c' if geometry_mode else 'trial c'
        lines += ['', title, diaphragm['source'], diaphragm['basis'],
                  'Calibration mode: '+('Rev B empirical reference geometry; a across ribs / b along ribs; c_panel = c_reference * (a/b^2)/(a_ref/b_ref^2)' if geometry_mode else 'Direct panel c override; no geometry scaling'),
                  f"Roof {'ON' if settings['enabled'] else 'OFF'}: {c_label} = {settings['flexibility_mm_per_kn']:g} mm/kN; direction = {settings['shear_direction']}.",
                  f"Side walls {'ON' if settings.get('side_walls_enabled') else 'OFF'}: {c_label} = {settings.get('side_wall_flexibility_mm_per_kn',6.):g} mm/kN, longitudinal shear.",
                  f"End walls {'ON' if settings.get('end_walls_enabled') else 'OFF'}: {c_label} = {settings.get('end_wall_flexibility_mm_per_kn',6.):g} mm/kN, transverse shear.",
                  f"Included panels by family: {diaphragm.get('panel_counts',{})}; excluded open faces: {diaphragm.get('excluded_open_faces',[])}.",
                  'Aeq = Ld^3 / (E c s^2), E = 200 kN/mm2; Vpanel = Ndiag s/Ld. Equivalent diameters are stiffness surrogates, not installed rods.',
                  f"Bare conventional maximum SLS resultant: {bare['resultant_mm']:.4f} mm; {bare['case']}; node {bare['node']+1}.",
                  f"Maximum longitudinal SLS movement: bare {diaphragm['bare_max_longitudinal_mm']:.4f} mm; with stressed skin {diaphragm['enhanced_max_longitudinal_mm']:.4f} mm.",
                  'REV B REFERENCE CALIBRATION RECORDS (measured benchmarks separated from judgement factors)']
        for family,reference in diaphragm.get('reference_calibrations',{}).items():
            lines.append(family+': '+json.dumps(reference,sort_keys=True,ensure_ascii=False))
        lines.append('Panel | adopted c mm/kN | s mm | n mm | Ld mm | reference Aeq mm2 | equivalent diameter mm | implied longitudinal c mm/kN')
        for panel in diaphragm['panels']:
            longitudinal = panel['implied_longitudinal_flexibility_mm_per_kn']
            longitudinal_text = f'{longitudinal:.4f}' if longitudinal is not None else 'not in the wall plane'
            lines.append(f"{panel['id']} | {panel['flexibility_mm_per_kn']:.5f} | {panel['s_mm']:.3f} | {panel['n_mm']:.3f} | {panel['length_mm']:.3f} | {panel['area_mm2']:.4f} | {panel['equivalent_diameter_mm']:.3f} | {longitudinal_text}")
            calibration=panel.get('calibration',{})
            if calibration:
                lines.append(f"  {calibration['mode']}: a across ribs {calibration['a_perpendicular_to_ribs_mm']:.3f} mm; b along ribs {calibration['b_parallel_to_ribs_mm']:.3f} mm; geometry factor {calibration['geometry_factor']:.6f}; c input {calibration['reference_c_input_mm_per_kn']:g} -> adopted panel c {calibration['adopted_panel_c_mm_per_kn']:.5f} mm/kN. {calibration['status']}")
            for member_id,props in panel.get('diagonal_properties',{}).items():
                lines.append(f"  Actual {member_id}: s {props['s_mm']:.3f}; height {props['n_mm']:.3f}; Ld {props['length_mm']:.3f} mm; Aeq {props['area_mm2']:.4f} mm2; EA {props['ea_kn']:.3f} kN; c {props['flexibility_mm_per_kn']:g} mm/kN. Panel row uses mean-height reference geometry.")
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
        if case['case'].get('pressure_basis'):
            b=case['case']['pressure_basis']
            lines += [f"End-wind coefficient basis: d/b={b['d_over_b']:.5f} (length/span); leeward Cp,e={b['leeward_cpe']:.5f}; effective surfaces {b['effective_surface_count']}; Kce/Kci={b['kce']:g}/{b['kci']:g}.",b['source'],b['scope']]
        if case['case'].get('components_windward_leeward'):
            lines += ['SIMULTANEOUS END-WALL PRESSURES (kPa; inward positive)',
                      'Common Cpi on both ends; net inward = external minus internal. Front inward acts +Y; back inward acts -Y.']
            for component in case['case']['components_windward_leeward']:
                lines.append(f"{component.get('face','end')} / {component['surface']} / {'clad' if component['clad'] else 'open, no applied pressure'}: external {component['external_kpa']:.5f}, internal {component['internal_kpa']:.5f}, applied net inward {component['inward_net_kpa']:.5f}.")
        if case['case'].get('roof_drag'):
            drag=case['case']['roof_drag']
            lines += [f"External roof friction: {drag['signed_y_force_kn']:.3f} kN along Y; Cf={drag['cf']:g}, q={drag['q_kpa']:.5f} kPa; projected area={drag['projected_area_m2']:.3f} m2.",
                      f"Mean roof height {drag['mean_roof_height_m']:.3f} m; windward exclusion {drag['windward_exclusion_m']:.3f} m; loaded Y={drag['y_start_m']:.3f} to {drag['y_end_m']:.3f} m; {drag['profile']}.",
                      drag['application'],drag['basis'],drag['source']]
        for work in case["load_workings"]:
            lines.append(f"{work['face']}: roof {work['roof_resultant_n']/1000:.3f} kN; direct ground {work['direct_ground_resultant_n']/1000:.3f} kN")
        if case.get('load_path'):
            path = case['load_path']
            lines += ['', 'LOAD PATH / BRACE DIRECTIONS',path['axes'],path['basis'],
                      f"Roof-applied Y load {path['applied_roof_y_n']/1000:.3f} kN; direct-to-base end-wall load {path['direct_ground_y_n']/1000:.3f} kN (separate from bracing)."]
            for face,value in path['member_base_reactions_n'].items():
                lines.append(f"{face}: member-to-ground reaction X/Y/Z kN = "+' / '.join(f'{v/1000:.3f}' for v in value))
            for face,value in path.get('diaphragm_base_reactions_n',{}).items():
                lines.append(f"  SLS diaphragm share at {face} anchors X/Y/Z kN = "+' / '.join(f'{v/1000:.3f}' for v in value))
            lines += [f"Roof Y + member base Y residual {path['roof_to_member_base_y_residual_n']:.6g} N.",
                      'Peak brace tension kN: '+', '.join(f"{g} {v/1000:.3f}" for g,v in path['peak_brace_tension_n'].items())]
        lines += ["", "MEMBER FORCES | N kN (+tension) | Extension mm | State"]
        lines += [f"{m['id']} | {m['axial_n']/1000:.4f} | {m['extension_mm']:.5f} | {m['state']}" for m in case["members"]]
        if case.get('diaphragm_members'):
            lines += ['', 'SLS EQUIVALENT DIAPHRAGMS | Ndiag kN | diagonal shear component kN (adopted direction) | component flow kN/m | longitudinal component kN | State']
            lines += [f"{m['id']} | {m['axial_n']/1000:.4f} | {m['panel_shear_kn']:.4f} | {m['shear_flow_kn_per_m']:.4f} | {m['longitudinal_shear_kn']:.4f} | {m['state']}" for m in case['diaphragm_members']]
            lines += ['NET PANEL SHEAR | adopted direction kN | average boundary flow kN/m | longitudinal kN',
                      'Signed shear sums opposite diagonal contributions; individual diagonal forces must also be checked for boundary/collector actions.']
            lines += [f"{p['id']} | {p['net_shear_kn']:.4f} | {p['net_shear_flow_kn_per_m']:.4f} | {p['net_longitudinal_shear_kn']:.4f}" for p in case['diaphragm_panels']]
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
