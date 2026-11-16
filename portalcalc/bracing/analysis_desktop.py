"""Desktop adapter and evidence/report generation for fixed-layout 3D analysis."""
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import time

import numpy as np

from portalcalc.bracing.layout import BracingLayout, LayoutGeometry, brace_group, carry_over, layout_panels
from portalcalc.bracing.analysis import (AnalysisError, PortalReduction, build_braced_building,
                              solve_building_case)
from portalcalc.bracing.restraint import OPTION as RESTRAINT_OPTION
from portalcalc.bracing.diaphragm import default_settings as default_diaphragm_settings
from portalcalc.wind import (CPI_CASE_OPTIONS, active_cpi_for_case, calculate_wind,
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
    sign = -1 if reverse else 1
    enclosed = all((inputs.front_wall_clad,inputs.back_wall_clad,inputs.left_wall_clad,inputs.right_wall_clad))
    # Wind passes under the main roof only when both end walls are open; an
    # open canopy always has its underside exposed. Each exposed surface
    # carries the same friction on the same projected area.
    both_ends_open = not inputs.front_wall_clad and not inputs.back_wall_clad
    surfaces = 2 if both_ends_open else 1
    drag = {'profile':roof_profile,'cf':cf,'surface':'External roof; sheet ribs/corrugations across longitudinal wind',
            'q_kpa':q_kpa,'signed_y_pressure_kpa':sign*q_kpa*cf*surfaces,
            'mean_roof_height_m':h,'breadth_m':b,'depth_m':d,'d_over_h':d/h,'d_over_b':d/b,
            'windward_exclusion_m':cutoff,'y_start_m':start,'y_end_m':end,
            'projected_area_m2':area,'signed_y_force_kn':sign*q_kpa*cf*area*surfaces,
            'application':'Enclosed building' if enclosed else 'PRELIMINARY: enclosed-building external-roof method extended to an open-sided building',
            'preliminary_open_sided':not enclosed,'source':'AS/NZS 1170.2:2021 (Amendment 2), clauses 2.4.2, 5.5 and Table 5.9',
            'url':END_WIND_PRESSURE_URL,
            'basis':'Projected roof area b * max(d - 4*min(h,b), 0); full force delivered to roof nodes. No Ka/Kce/Kci reduction. No wall, underside or exposed-member drag.'}
    if both_ends_open:
        drag.update(surface='External roof top and underside (both end walls open); sheet ribs/corrugations across longitudinal wind',
                    surfaces=['top','underside'],
                    basis=('Projected roof area b * max(d - 4*min(h,b), 0) on each of the top and underside (both end walls '
                           'open, so wind passes under the roof); full force delivered to roof nodes. No Ka/Kce/Kci reduction. '
                           'PRELIMINARY: enclosed-building friction method applied to both surfaces. No wall or exposed-member drag.'))
    canopies = []
    for side in ('left','right'):
        width = float(getattr(inputs, f'{side}_canopy_length_m', 0.) or 0.)
        if width <= 1e-9:
            continue
        canopy_area = width*max(0.,d-cutoff)
        canopies.append({'side':side,'width_m':width,'surfaces':['top','underside'],
                         'signed_y_pressure_kpa':sign*q_kpa*cf*2,'y_start_m':start,'y_end_m':end,
                         'projected_area_m2':canopy_area,'signed_y_force_kn':sign*q_kpa*cf*canopy_area*2,
                         'basis':('Open canopy: projected canopy plan area width * max(d - 4*min(h,b), 0) on the top and '
                                  'the open underside, same Cf and windward exclusion as the main roof. PRELIMINARY.')})
    if canopies:
        drag['canopies'] = canopies
    return drag


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
    def along_building(case):
        drag = case['roof_drag']
        friction = abs(drag['signed_y_force_kn'])+sum(abs(c['signed_y_force_kn']) for c in drag.get('canopies',()))
        return abs(case["front_kpa"]) + abs(case["back_kpa"]) + friction
    if not any(along_building(case) > 1e-12 for case in cases):
        raise AnalysisError("No along-building wind load: there is no clad end wall, and the building is too short for "
                            "roof friction (length must exceed 4 x min(h, b))")
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


def canopy_edge_purlin(window, g, side):
    """Installed-purlin record for a canopy edge strut line, or None.

    Same fields as a main roof line, from the 2D canopy edge purlin check
    (canopy loads and the open underside). None when that check does not
    pass; the strut location then reports the missing edge purlin.
    """
    check = (getattr(window, 'canopy_purlin_checks', None) or {}).get(side) or {}
    match = check.get('match') or {}
    if match.get('status') != 'OK':
        return None
    area = match['area_mm2']
    weight = area*7.85e-6*9.80665
    width_m = check['tributary_width_mm']/1000
    span_type = check['span_type']
    entry = {'section': match['section'], 'area_mm2': area, 'installed_count': 1,
             'installed_count_source': 'Canopy edge purlin',
             'tributary_width_mm': check['tributary_width_mm'], 'original_roof_spacing_mm': check['spacing_mm'],
             'apex_tributary_widths_mm': [], 'eave_tributary': None,
             'tributary_width_source': 'Canopy edge purlin: half the canopy purlin spacing',
             'bending_ratio': max(check['outward_kn_m']/match['outward_capacity_kn_m'],
                                  check['inward_strength_kn_m']/match['inward_strength_capacity_kn_m']),
             'service_ratio': check['inward_service_kn_m']/match['inward_service_capacity_kn_m'],
             'added_bending_ratio': 1.2*weight/match['inward_strength_capacity_kn_m'],
             'added_service_ratio': weight/match['inward_service_capacity_kn_m'],
             'basis_key': (f"{match['section']}|{span_type}|{match.get('tables')}|{match.get('outward_bridging')}|"
                           f"{match.get('inward_bridging')}|canopy_{side}_edge"),
             'zone': 'edge', 'nearest_installed_x_mm': g.canopy_tip(side)[0], 'node_offset_mm': 0.,
             'bending_snapshot': copy.deepcopy(match), 'canopy_side': side, 'canopy_check': copy.deepcopy(check),
             'source': ('Canopy edge purlin: Metroll bending-table check with canopy loads and the open underside; '
                        'axial member compression/tension calculated or separately declared in the 3D workspace'),
             'bending_span_mm': check['span_mm'],
             'beam_inputs': {'span_type': span_type, 'outward_kn_m': check['outward_kn_m'],
                             'inward_strength_kn_m': check['inward_strength_kn_m'],
                             'service_kn_m': check['inward_service_kn_m'],
                             'service_outward_kn_m': check['wind_service_upward_kpa']*width_m,
                             'service_inward_kn_m': check['wind_service_downward_kpa']*width_m,
                             'permanent_kn_m': check['permanent_kpa']*width_m,
                             'gravity_service_kn_m': (check['permanent_kpa']+check['live_kpa'])*width_m,
                             'end_roof_local_cases': []}}
    entry['bending_key'] = hashlib.sha256(json.dumps({
        key: entry[key] for key in ("section", "area_mm2", "bending_ratio", "service_ratio",
            "added_bending_ratio", "added_service_ratio", "basis_key", "bending_span_mm", "bending_snapshot", "beam_inputs",
            'tributary_width_mm', 'original_roof_spacing_mm', 'apex_tributary_widths_mm', 'eave_tributary')
    }, sort_keys=True).encode()).hexdigest()
    return entry


def actual_canopies(structure, left, right, nominal):
    """Open canopies on the 2D portal: (side, plan width, tip drop, tip node).

    Widths and the fall are taken from the actual 2D canopy roof members,
    measured from the main eaves. Canopies must attach at the main eave on a
    gable roof (the braced eave line), and must be in the bracing layout.
    """
    result = []
    for side, eave in (('left', left), ('right', right)):
        members = [e for e in structure.elements if e.group == f'{side.upper()}_CANOPY_TOP']
        if not members:
            continue
        if nominal.roof_shape != 'Gable':
            raise AnalysisError('The 3D bracing analysis supports open canopies on gable roofs only')
        if nominal.canopy(side) is None:
            raise AnalysisError(f'The {side} canopy is not in the bracing layout geometry')
        nodes = [n for e in members for n in (e.start, e.end)]
        root = min(nodes, key=lambda n: abs(n.x-eave.x))
        if abs(root.x-eave.x) > 1. or abs(root.y-eave.y) > 1.:
            raise AnalysisError(f'The {side} canopy must attach at the main eave for the 3D bracing analysis; '
                                'clear its eave height override')
        tip = (min if side == 'left' else max)(nodes, key=lambda n: n.x)
        result.append((side, abs(tip.x-eave.x), eave.y-tip.y, tip))
    return result


def crane_wall_tier_mm(window):
    """Crane runway level (mid depth of the runway beam, mm), or None without a crane.

    Crane side walls are braced as two X's split at this level, with the
    runway beam as the continuous strut between them.
    """
    actions = getattr(window, 'crane_portal_actions', None)
    if not actions or not getattr(window, 'serviceability_crane_active', False):
        return None
    level = actions.get('runway_mid_height_mm')
    return round(float(level), 1) if level and level > 0 else None


def crane_racking_setup(window, structure, geometry):
    """The 3D crane racking set-up, or None without a crane (or interior frame).

    The crane goes on the interior frame nearest the middle of the building,
    with the 2D frame's crane actions (crane_beam.apply_crane_actions) at its
    crane stub nodes; racking = mean rail UX against H/n (the 2D frame's
    "Portal crane-rail racking" setting).
    """
    actions = getattr(window, 'crane_portal_actions', None)
    if not actions or not getattr(window, 'serviceability_crane_active', False):
        return None
    node_ids = {side: (actions.get('nodes', {}).get(side) or {}).get('node_id') for side in ('left', 'right')}
    nodes = {n.id: n for n in structure.nodes}
    if any(node_id not in nodes for node_id in node_ids.values()):
        return None
    stations = list(geometry.bay_stations_mm)
    interior = range(1, len(stations)-1)
    if not interior:
        return None
    frame = min(interior, key=lambda i: (abs(stations[i]-geometry.length_mm/2), i))
    from portalcalc.crane_beam import BUFFER_ULS_FACTOR
    return {'portal_actions': copy.deepcopy(actions), 'node_ids': node_ids, 'frame_index': frame,
            'buffer': {'force_kn': float(actions.get('buffer_kn', 0.) or 0.), 'uls_factor': BUFFER_ULS_FACTOR,
                       'source': actions.get('buffer_source', 'rule'),
                       'runway_level_mm': geometry.wall_tier_z_mm,
                       'basis': ('Longitudinal buffer impact per rail (entered, else the rule 9 + 2.8 x capacity t '
                                 '+ 1.33 x crane span m, an upper bound of supplier data) x '
                                 f'{BUFFER_ULS_FACTOR:g} on both runways at once, at runway level; front and back '
                                 'stops separately, with 1.2G.')},
            'runway_area_mm2': float(actions.get('runway_area_mm2', 0.) or 0.),
            'frame_y_mm': stations[frame],
            'heights_mm': {side: nodes[node_id].y for side, node_id in node_ids.items()},
            'divisor': float(window.serviceability_limit_value('crane_rail_height')),
            'basis': ('Crane on the interior frame nearest the middle of the building, at its crane stub nodes, '
                      'with the 2D frame crane actions. Racking = mean of the two rails\' UX, measured from the '
                      'G-only shape (rails aligned under dead load), against H/n with H the mean rail height. '
                      'Bare bracing network (no stressed-skin credit).')}


def prepare_context(window, allow_unconfigured=False):
    nominal = window.current_bracing_layout_geometry()
    if window.bracing_location_layout is None and not allow_unconfigured:
        raise AnalysisError("Save a bracing layout and pin the brace panels to analyse first")
    saved = BracingLayout.from_dict(window.bracing_location_layout) if window.bracing_location_layout is not None else BracingLayout(nominal)
    if saved.geometry != nominal:
        # Adding or changing a canopy keeps the main building's saved panels.
        saved = carry_over(saved, nominal) or saved
    if saved.geometry != nominal and not allow_unconfigured:
        raise AnalysisError("Building dimensions changed. Review and save the bracing locations before analysis")
    if not saved.selection and not allow_unconfigured:
        raise AnalysisError("Pin the brace panels to analyse in the 3D location editor first. Available panels are not installed braces.")
    if window.structure is None:
        raise AnalysisError("A current 2D portal model is required")
    groups = {e.group for e in window.structure.elements}
    if any(group.startswith(("LEFT_LEAN", "RIGHT_LEAN")) for group in groups):
        raise AnalysisError("The 3D bracing analysis does not support lean-tos yet (open canopies are supported); "
                            "remove the lean-to for this analysis")
    structure = copy.deepcopy(window.structure)
    structure.clear_loads()
    top = [e for e in structure.elements if e.group == "TOP"]
    if not top:
        raise AnalysisError("No main portal roof elements are available")
    nodes = [n for e in top for n in (e.start, e.end)]
    left, right = min(nodes, key=lambda n: n.x), max(nodes, key=lambda n: n.x)
    canopies = actual_canopies(structure, left, right, nominal)
    # The editor stores nominal building dimensions. Analysis must use actual
    # column/chord centrelines, including the desktop's section offsets.
    span = right.x-left.x
    g = LayoutGeometry.create(span, nominal.length_mm, left.y, nominal.pitch_deg, nominal.roof_shape,
                              bay_stations_mm=nominal.bay_stations_mm,
                              end_wall_stations_mm=[x*span/nominal.span_mm for x in nominal.end_wall_stations_mm],
                              canopies=[{'side':side,'width_mm':width,'drop_mm':drop} for side,width,drop,_ in canopies],
                              wall_tier_z_mm=crane_wall_tier_mm(window),
                              # The editor's two-tier decisions, so the analysis braces what it shows.
                              tiered_panels=[p.id for p in layout_panels(nominal) if p.tier == 'tall'])
    layout = BracingLayout(g, sorted(saved.all_blocked), sorted(saved.selection)) if saved.geometry == nominal else BracingLayout(g)
    # Remove legacy eave restraints in the private copy. Otherwise the 3D
    # network would inherit restraint credit from the system it is analysing.
    for node in (left, right):
        structure.springs.pop(node.ux, None)
        structure.restrained_dofs = [d for d in structure.restrained_dofs if d != node.ux]
    # Likewise any adopted 3D sway restraint: the network itself provides it.
    structure.sway_springs = []
    # Roof stations, then each canopy's outer edge (the canopy-edge strut line).
    points = [(left.x+x, g.roof_z(x)) for x in g.roof_stations] + [(tip.x, tip.y) for _,_,_,tip in canopies]
    portal = PortalReduction(structure, points)
    # Wind acts on the building envelope, as in the 2D frame: its outside width and the
    # eave pitching point, not the column centrelines and the (lower) top chord node.
    # The roof and wall outside the frame lines reach the portals as eave strips.
    eave_basis = getattr(window, "eave_node_basis_record", None) or {}
    try:
        envelope_width_mm = float(window.span_input.text())*1000.
    except (AttributeError, TypeError, ValueError):
        envelope_width_mm = g.span_mm
    wind_inputs = window.wind_inputs_from_ui(envelope_width_mm, g.pitch_deg, eave_basis.get("eave_mm", g.eave_mm))
    eave_strips = copy.deepcopy(eave_basis.get("strips") or [])
    source = copy.deepcopy(window.collect_preset_data())
    from portalcalc.bracing.spacing import max_bay_spacing
    spacing_limit=max_bay_spacing({'source_preset':source})
    cases = end_wind_cases(wind_inputs,source['finishes']['roof_profile'])
    from portalcalc.bracing.roof_loads import building_roof_cases
    crane = crane_racking_setup(window, structure, g)
    cases, roof_load_basis = building_roof_cases(cases,portal,g,wind_inputs,source,
                                                window.snow_loads_from_ui()['service_kpa'], crane=crane,
                                                eave_strips=eave_strips)
    crane_basis = None if crane is None else {key: copy.deepcopy(value) for key, value in crane.items()
                                              if key != 'portal_actions'}
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
        from portalcalc.purlins.end_local import end_roof_local_cases
        installed[str(line)]['beam_inputs']['end_roof_local_cases'] = end_roof_local_cases(wind_inputs,g,installed[str(line)])
        installed[str(line)]["bending_key"] = hashlib.sha256(json.dumps({
            key: installed[str(line)][key] for key in ("section", "area_mm2", "bending_ratio", "service_ratio",
                "added_bending_ratio", "added_service_ratio", "basis_key", "bending_span_mm", "bending_snapshot", "beam_inputs",
                'tributary_width_mm', 'original_roof_spacing_mm', 'apex_tributary_widths_mm','eave_tributary')
        }, sort_keys=True).encode()).hexdigest()
    for side,_,_,_ in canopies:
        entry = canopy_edge_purlin(window, g, side)
        if entry is not None:
            installed[str(g.canopy_line(side))] = entry
    from portalcalc.purlins.strut_tables import snapshot as strut_table_snapshot
    return {
        "layout": layout, "nominal_layout": saved.to_dict(), "nominal_geometry": nominal, "portal": portal, "cases": cases,
        'roof_load_basis': roof_load_basis,
        'max_braced_bay_spacing':spacing_limit,
        "roof_brace": copy.deepcopy(window.selected_brace_size(window.roof_brace_size_combo)),
        "wall_brace": copy.deepcopy(window.selected_brace_size(window.wall_brace_size_combo)),
        "end_brace": copy.deepcopy(window.selected_end_wall_brace_size()),
        "end_brace_options": [copy.deepcopy(window.end_wall_brace_size_combo.itemData(i))
                              for i in range(window.end_wall_brace_size_combo.count())],
        # Canopy roof X-braces: their own group, starting at the roof brace size.
        **({"canopy_brace": copy.deepcopy(window.selected_brace_size(window.roof_brace_size_combo))} if canopies else {}),
        "source_preset": source,
        "source_hash": hashlib.sha256(json.dumps(source, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        "default_end_post": profile.get("section"),
        'global_sls_basis':{'divisor':window.eave_deflection_divisor(),
            'importance_level':wind_inputs.importance_level,
            'source':'Main in-plane eave sway criterion; project default IL1 H/80, IL2+ H/150; main overrides retained'},
        # Crane rail racking (3D): None without a crane. See crane_racking_setup.
        'crane_racking_basis': crane_basis,
        # Crane walls: the runway beam is the continuous wall strut at runway level.
        'crane_runway_area_mm2': (float((window.crane_portal_actions or {}).get('runway_area_mm2', 0.) or 0.)
                                  if g.wall_tier_z_mm is not None else None),
        'crane_runway_section': ((window.crane_portal_actions or {}).get('runway_section')
                                 if g.wall_tier_z_mm is not None else None),
        # Tall side-wall bays: two X's with a mid-height SHS strut (its own design group).
        **({'wall_tier_strut': default_wall_tier_strut(window)}
           if any(panel.tier == 'tall' for panel in layout.panels.values()) else {}),
        'mullion_basis':mullion_basis,
        "front_clad": wind_inputs.front_wall_clad, "back_clad": wind_inputs.back_wall_clad,
        'face_cladding': {'left_wall': wind_inputs.left_wall_clad, 'right_wall': wind_inputs.right_wall_clad,
                          'front_end': wind_inputs.front_wall_clad, 'back_end': wind_inputs.back_wall_clad},
        "cost_rates": {"shs": window.costing_rate_from_input(window.shs_cost_input, "shs_rate_per_kg"),
                       "ub": window.costing_rate_from_input(window.ub_cost_input, "ub_rate_per_kg"),
                       "wb": window.costing_rate_from_input(window.wb_cost_input, "wb_rate_per_kg"),
                       "purlin": window.costing_rate_from_input(window.purlin_cost_input, "purlin_rate_per_kg"),
                       'braced_panel_installation': (window.bracing_3d_settings or {}).get('installation_cost_per_panel', 50.),
                       'portal_brace_allowance': (window.bracing_3d_settings or {}).get('portal_brace_allowance', 0.)},
        **default_portal_brace_sections(window),
        "installed_purlins": installed,
        # The 2D portal's credited restraint: 3D sizing keeps at least its brace stiffness.
        'adopted_restraint': (copy.deepcopy(getattr(window, 'bracing_3d_restraint', None))
                              if window.eave_x_restraint_combo.currentText() == RESTRAINT_OPTION else None),
        # Saved choice wins; an unsaved project defaults by importance level (IL1 on, IL2+ off).
        'stressed_skin': copy.deepcopy((window.bracing_3d_settings or {}).get('stressed_skin')
                                       or default_diaphragm_settings(window.importance_combo.currentText())),
        "purlin_strut_tables": strut_table_snapshot(window.metroll_tables or {}),
    }


def default_wall_tier_strut(window):
    """Mid-height wall strut section: the saved workspace choice, else a light SHS."""
    profiles = sorted(window.catalog.shs_profiles(), key=lambda p: (p.A, p.name))
    if not profiles:
        raise AnalysisError('No SHS sections are available for the mid-height wall struts of tall side-wall bays')
    name = (window.bracing_3d_settings or {}).get('wall_tier_strut_section')
    saved = next((p for p in profiles if p.name == name), None)
    return saved or min(profiles, key=lambda p: (abs(p.A-500.), p.name))


DEFAULT_PORTAL_BRACE_SECTIONS = {'portal_brace_column': '310 UB 40.4', 'portal_brace_beam': '250 UB 31.4'}


def default_portal_brace_sections(window):
    """Portal brace column/beam candidates: the saved workspace choices, else defaults."""
    from portalcalc.bracing.portal_brace import portal_candidates
    candidates = portal_candidates(window.catalog.ub_profiles())
    if not candidates:
        return {}
    saved = window.bracing_3d_settings or {}
    by_name = {c.name: c for c in candidates}
    return {group: by_name.get(saved.get(f'{group}_section')) or by_name.get(name) or candidates[0]
            for group, name in DEFAULT_PORTAL_BRACE_SECTIONS.items()}


PORTAL_BRACE_LIMITATION = (
    "Portal frame braces are UB moment frames in side-wall bays: a column stitch-welded inside each portal column and a "
    "beam moment-connected across the top at eave level, with pinned bases on their own base plates. Each is a 2D "
    "frame in the wall plane, condensed onto the two eave Y translations: the columns carry their own axial forces to "
    "their own bases, the stitch-weld offset and the portal columns' weak-axis stiffness are ignored, and the brace "
    "carries no cladding or self-weight load. Members are checked segment by segment between lateral restraints, "
    "with each segment's peak moment and AS 4100 Table 5.6.1 moment modifier from its end moments (2.5 for the "
    "beam in double curvature). Columns: in-plane (sway) effective length 2.0 h; minor-axis and LTB segments at "
    "most the stitch-weld/stiffener restraint spacing. Beam: in-plane length = span; minor-axis and LTB segments = "
    "the beam restraint spacing (default the bay) with the entered kr (default 0.85). Connections, base plates, "
    "stitch welds and stiffeners are not designed; they are covered by the per-brace allowance only.")


LIMITATIONS = [
    "Fixed pinned-panel layout; unmarked available panels are not installed braces. No automatic layout or section optimisation.",
    "First-order small-displacement elastic analysis, with tension-only rod slackening; no P-delta, buckling, prestress or connection slip.",
    "If zero-force rods are needed for the elastic tangent, equilibrium is reported with stability unverified. A positive taut-rod tangent is not a buckling or code stability check.",
    "ULS/SLS gravity and longitudinal wind combinations include G (solar/fire/HVAC/other and actual portal self-weight), Q/snow, simultaneous end-wall pressures, external roof friction and normal roof/clad side-wall pressures. Side winds include both directions, roof alternatives, internal pressure and end-wall suction: ULS with 0.9G/1.2G and SLS as Ws and G + Ws on the bare network. Transverse SLS frame sway is reported only and does not govern 3D sizing. With a crane, the 2D frame's crane actions load the interior frame nearest the middle: SLS G + Crane and G + 0.7Q + Crane check crane rail racking (from the G-only shape, bare network, H/n), and ULS 1.2G + 1.5Q + Crane enters the member envelopes. Storage actions and wall/underside/exposed-member drag remain outside scope.",
    "Normal roof/side-wall loads act through full portal load condensation/recovery. Roof load tributaries use simply supported bay reactions with half tributaries at end frames; purlin continuity redistribution is excluded. Local Table 5.6 patches remain purlin bending checks only.",
    "Roof friction uses the projected external-roof portion of clause 5.5/Table 5.9, with sheeting running down-slope. For open-sided buildings this is a preliminary extension of the enclosed-building method, not a complete open-roof aerodynamic assessment.",
    "End-wall pressure uses simply supported vertical strips: half of each strip resultant goes to roof and half directly to ground. Open ends receive no cladding pressure.",
    "Identical actual 2D portal sections/supports repeat at every frame line. Legacy eave restraints are removed in a private copy. Portal attachment translations use work-conjugate beam interpolation.",
    "Dedicated axial struts of the selected section run along every roof station. Intermediate end posts are included at clad or end-braced ends, are axial-only and use the selected post section. Their bending design is separate.",
    "Brace base translations are fixed. Foundation flexibility, column weak-axis flexure and connection eccentricities are not modelled. No diaphragm or sheeting stiffness.",
    "Forces are analysis demands only. No brace net-section/connection checks, strut buckling or combined purlin bending/axial capacity checks; a solved model is not a passing design.",
    "Results do not replace Phase 1 checks, alter the 2D portal springs, section selections, costing or the full-computations export.",
    "Open canopies (not lean-tos) are part of each portal (stiffness, self-weight, canopy G/Q/solar and canopy wind) and add a braceable canopy roof face: one strip per bay from the main eave line to the canopy edge, with the canopy edge purlin as the edge strut. Canopy roof friction acts on the top and the open underside and is shared between the eave line and the canopy edge. Canopies must attach at the main eave of a gable roof. No canopy diaphragm credit; intermediate canopy purlin lines are not in the network.",
]


def transverse_sls(case):
    """Side-wind serviceability case: reported frame sway, not 3D acceptance."""
    return case.get('level') == 'SLS' and case.get('wind_family') == 'transverse'


def crane_case_3d(case):
    """A crane case: the crane loads one frame (bracing_roof_loads.crane_cases)."""
    return case.get('wind_family') == 'crane'


def crane_racking_summary(portal, cases, basis):
    """Crane rail racking of the crane frame in 3D, against H/n. None without a crane.

    Racking is the mean of the two crane rails' UX, measured from the
    "SLS / G" shape (the rails are aligned under dead load), for every SLS
    crane case. The frame-alone value solves the same crane (and 0.7Q) loads
    on that portal without the 3D network, for comparison with 2D.
    """
    if not basis:
        return None
    frame, ids = basis['frame_index'], basis['node_ids']
    selected = [c for c in cases if crane_case_3d(c['case']) and c['case']['level'] == 'SLS']
    baseline = next((c for c in cases if c['case']['name'] == 'SLS / G'), None)
    if not selected or baseline is None:
        return None
    structure = portal.structure
    nodes = {n.id: n for n in structure.nodes}
    free = portal.free
    kff = portal.k[np.ix_(free, free)]

    def rails(solved):
        moved = {n['node']: n['ux_mm'] for n in solved['portals'][frame]['node_displacements']}
        return moved[ids['left']], moved[ids['right']]

    base = rails(baseline)
    base_loads = np.asarray(baseline['case']['portal_loads'][frame]['loads_n'])
    height = .5*(basis['heights_mm']['left']+basis['heights_mm']['right'])
    limit = height/basis['divisor']
    rows = []
    for solved in selected:
        ux = rails(solved)
        left, right = ux[0]-base[0], ux[1]-base[1]
        racking = .5*(left+right)
        alone = np.zeros(len(base_loads))
        alone[free] = np.linalg.solve(kff, (np.asarray(solved['case']['portal_loads'][frame]['loads_n'])-base_loads)[free])
        alone_racking = .5*(alone[nodes[ids['left']].ux]+alone[nodes[ids['right']].ux])
        rows.append({'case': solved['case']['name'], 'left_mm': left, 'right_mm': right, 'racking_mm': racking,
                     'frame_alone_mm': alone_racking, 'limit_mm': limit,
                     'ratio': abs(racking)/limit if limit > 1e-9 else float('inf')})
    governing = max(rows, key=lambda row: row['ratio'])
    return {'passes': governing['ratio'] <= 1., 'status': 'OK' if governing['ratio'] <= 1. else 'NOT OK',
            'frame': frame, 'frame_y_mm': basis['frame_y_mm'], 'divisor': basis['divisor'],
            'height_mm': height, 'limit_mm': limit, 'ratio': governing['ratio'],
            'governing': governing, 'rows': rows, 'basis': basis['basis']}


def portal_eave_nodes(structure):
    nodes = [n for e in structure.elements if e.group == 'TOP' for n in (e.start, e.end)]
    return min(nodes, key=lambda n: n.x), max(nodes, key=lambda n: n.x)


# Cases whose loads barely sway the unrestrained frame give an ill-conditioned
# secant restraint. Judge on the unrestrained frame, not the restrained
# movement, so well-restrained frames are kept.
SECANT_MIN_SWAY_MM = 1.


def frame_sway_rows(portal, cases):
    """Per solved case and frame: eave movement, portal-alone sway and restraint.

    Yields (case, frame index, recovered portal, row, portal-alone mean eave X).
    The secant restraint is -sum(roof interface X)/mean eave X.
    """
    left, right = portal_eave_nodes(portal.structure)
    free = portal.free
    kff = portal.k[np.ix_(free, free)]
    for case in cases:
        loads = np.array([state['loads_n'] for state in case['case']['portal_loads']])
        alone = np.zeros_like(loads)
        alone[:, free] = np.linalg.solve(kff, loads[:, free].T).T
        for index, recovered in enumerate(case['portals']):
            displacement = {n['node']: n['ux_mm'] for n in recovered['node_displacements']}
            ux = (displacement[left.id], displacement[right.id])
            mean = .5*(ux[0]+ux[1])
            interface_x = sum(force[0] for force in recovered['interface_forces_n'])
            row = {'case': case['case']['name'], 'left_eave_mm': ux[0], 'right_eave_mm': ux[1],
                   'movement_mm': max(abs(v) for v in ux),
                   'portal_alone_mm': max(abs(alone[index, left.ux]), abs(alone[index, right.ux])),
                   'roof_interface_x_kn': interface_x/1000,
                   'secant_restraint_kn_mm': -interface_x/mean/1000 if abs(mean) > 1e-6 else None}
            yield case, index, recovered, row, .5*(alone[index, left.ux]+alone[index, right.ux])


def frame_restraint_summary(portal, cases):
    """Roof-level sway restraint the 3D network gives each frame, for 2D adoption.

    Uses every transverse case (ULS and SLS). The adoptable value is the least
    secant restraint over interior frames and cases: the 2D model analyses one
    representative frame with identical sections, so the least-restrained
    interior frame governs. End frames are excluded because end posts and
    end-wall bracing support them directly and they carry half the roof load;
    a single eave spring cannot represent that. If any interior frame receives
    zero or negative restraint, no credit is adoptable.
    """
    selected = [case for case in cases if case['case'].get('wind_family') == 'transverse']
    if not selected:
        return None
    frames = {}
    for case, index, recovered, row, alone_mean in frame_sway_rows(portal, selected):
        frame = frames.setdefault(index, {'index': index, 'y_mm': recovered['y_mm'], 'least': None, 'greatest': None})
        k = row['secant_restraint_kn_mm']
        if k is None or abs(alone_mean) <= SECANT_MIN_SWAY_MM:
            continue
        if frame['least'] is None or k < frame['least']['secant_restraint_kn_mm']:
            frame['least'] = {'level': case['case']['level'], **row}
        if frame['greatest'] is None or k > frame['greatest']['secant_restraint_kn_mm']:
            frame['greatest'] = {'level': case['case']['level'], **row}
    ordered = [frames[i] for i in sorted(frames)]
    interior = [f for f in ordered if 0 < f['index'] < len(ordered)-1]
    reason = None
    if not interior:
        reason = 'No interior frames: end frames are not represented by an eave spring'
    elif any(f['least'] is None for f in interior):
        reason = 'An interior frame had no transverse case with measurable sway'
    governing = None if reason else min(interior, key=lambda f: f['least']['secant_restraint_kn_mm'])
    k = None if governing is None else governing['least']['secant_restraint_kn_mm']
    if governing is not None and k <= 1e-6:
        reason = (f"frame {governing['index']+1} is not restrained, as no roof bracing reaches it"
                  if k > -1e-6 else f"frame {governing['index']+1} is loaded, not restrained, by the bracing (negative restraint)")
    return {'frames': ordered, 'adoptable': reason is None,
            'adoptable_kn_mm': k if reason is None else 0.,
            'governing': None if governing is None else {'frame': governing['index'], **governing['least']},
            'reason': reason,
            'basis': 'Least secant roof-level sway restraint over interior frames and all transverse ULS/SLS cases, '
                     'applied in 2D as one spring on the mean eave movement (frame sway only; eave spread is not resisted). '
                     'End frames are excluded. Valid only with the bracing layout and sections it was derived from.'}


def transverse_sls_summary(portal, cases, sls_basis):
    """Per-frame eave sway under transverse SLS, with the bracing's restraint.

    The governing ratio uses Ws-only cases, matching the main portal's column
    top check. The portal-alone sway solves the same frame loads without the
    3D network. The secant restraint is -sum(interface X)/mean eave X: the
    roof-level stiffness the network actually provided to that frame in that
    case. It is negative where roof bracing loads a frame by tying it to a
    softer neighbour. REPORT ONLY: the production 2D check remains acceptance.
    """
    selected = [case for case in cases if transverse_sls(case['case'])]
    if not selected:
        return None
    left, right = portal_eave_nodes(portal.structure)
    divisor = (sls_basis or {}).get('divisor')
    frames = {}
    for case, index, recovered, row, alone_mean in frame_sway_rows(portal, selected):
        ws_only = not case['case'].get('g_factor')
        row['combination'] = 'Ws' if ws_only else 'G + Ws'
        ux = (row['left_eave_mm'], row['right_eave_mm'])
        if divisor:
            limits = (left.y/divisor, right.y/divisor)
            row.update(limit_mm=min(limits), ratio=max(abs(u)/limit for u, limit in zip(ux, limits)))
        frame = frames.setdefault(index, {'index': index, 'y_mm': recovered['y_mm'],
                                          'governing_ws': None, 'max_g_plus_ws_mm': 0.,
                                          'secant_restraint_range_kn_mm': None})
        if ws_only:
            key = 'ratio' if divisor else 'movement_mm'
            if frame['governing_ws'] is None or row[key] > frame['governing_ws'][key]:
                frame['governing_ws'] = row
        else:
            frame['max_g_plus_ws_mm'] = max(frame['max_g_plus_ws_mm'], row['movement_mm'])
        k = row['secant_restraint_kn_mm']
        if k is not None and abs(alone_mean) > SECANT_MIN_SWAY_MM:
            low, high = frame['secant_restraint_range_kn_mm'] or (k, k)
            frame['secant_restraint_range_kn_mm'] = (min(low, k), max(high, k))
    ordered = [frames[i] for i in sorted(frames)]
    governing = max((f for f in ordered if f['governing_ws']),
                    key=lambda f: f['governing_ws']['ratio' if divisor else 'movement_mm'], default=None)
    return {'status': 'REPORT_ONLY', 'divisor': divisor, 'criterion_source': (sls_basis or {}).get('source'),
            'eave_heights_mm': [left.y, right.y], 'case_count': len(selected), 'frames': ordered,
            'governing': None if governing is None else {'frame': governing['index'], **governing['governing_ws']},
            'basis': 'Transverse SLS at serviceability pressure (Ws and G + Ws), conventional bare bracing, no diaphragm credit. '
                     'Eave sway is the larger column-top X movement; the ratio uses Ws-only cases against the main in-plane eave criterion. '
                     'Portal-alone sway is the same frame and loads without the 3D network. Secant restraint is -sum(roof interface X)/mean eave X. '
                     'Report only: the production 2D portal check remains the serviceability acceptance.'}


PORTAL_BRACE_GROUPS = ('portal_brace_column', 'portal_brace_beam')


def portal_brace_section_record(profile):
    from portalcalc.bracing.portal_brace import section_of
    section = section_of(profile)
    return {'section': section.name, 'area_mm2': section.area_mm2, 'inertia_mm4': section.inertia_mm4}


def portal_brace_envelope(cases):
    """ULS peak actions per portal brace member (``{brace id}/{member}``)."""
    envelopes = {}
    for case in cases:
        if case['case']['level'] != 'ULS':
            continue
        name = case['case']['name']
        for brace in case.get('portal_braces', ()):
            for member in brace['members']:
                key = f"{brace['id']}/{member['member']}"
                row = envelopes.setdefault(key, {'id': key, 'brace': brace['id'], 'member': member['member'],
                                                 'section': member['section'], 'length_mm': member['length_mm'],
                                                 'tension_n': 0., 'compression_n': 0., 'shear_n': 0., 'moment_nmm': 0.})
                for field, case_key, value in (('tension_n', 'tension_case', max(member['axial_n'], 0.)),
                                               ('compression_n', 'compression_case', max(-member['axial_n'], 0.)),
                                               ('shear_n', 'shear_case', member['shear_n']),
                                               ('moment_nmm', 'moment_case', member['moment_nmm'])):
                    if value > row[field]:
                        row[field], row[case_key] = value, name
    return list(envelopes.values())


def prepare_analysis_models(context, strut_profile, end_post_profile):
    """Shared physical assembly for full checks and lightweight SLS trials."""
    layout, portal = context["layout"], context["portal"]
    for side, _, _ in layout.geometry.canopies:
        if layout.pinned and not any(p.startswith(f"canopy_{side}/") for p in layout.pinned):
            raise AnalysisError(f"The {side} canopy has no roof bracing, so its outer edge has no support along the building. "
                                f"Pin at least one {side} canopy roof panel in the bracing locations (usually in the main "
                                "roof's braced bays), or use Find layout and size, which adds it.")
    post_faces = {face for face, clad in (("front_end", context["front_clad"]), ("back_end", context["back_clad"]))
                  if clad or any(layout.panels[p].face == face for p in layout.pinned)}
    from portalcalc.bracing.components import StrutPlan, assembly_properties, wire_modulus
    if isinstance(strut_profile, StrutPlan):
        expected = {f"strut/bay/{i}/line/{j}" for i in range(len(layout.geometry.bay_stations_mm)-1)
                    for j in range(len(layout.geometry.strut_lines))}
        if set(strut_profile.members) != expected:
            raise AnalysisError("Strut plan must specify every current bay and roof line exactly once")
    used = {brace_group(layout.panels[p].face) for p in layout.pinned}
    groups = ("roof_brace", "wall_brace", "end_brace") + (("canopy_brace",) if context.get("canopy_brace") else ())
    moduli = {group: wire_modulus(context[group]) if group in used and context[group].get("component_type") == "wire_rope" else 200000.
              for group in groups}
    model, roof_grid, portal_dofs, point_nodes = build_braced_building(
        layout, portal, strut_profile.A, context["roof_brace"]["area_mm2"],
        context["wall_brace"]["area_mm2"], context["end_brace"]["area_mm2"], end_post_profile.A,
        end_post_faces=post_faces,
        strut_overrides=strut_profile.overrides() if isinstance(strut_profile, StrutPlan) else None,
        brace_moduli=moduli,
        canopy_brace_area_mm2=(context.get("canopy_brace") or {}).get("area_mm2"),
        portal_brace_sections={'column': context.get('portal_brace_column'), 'beam': context.get('portal_brace_beam')}
        if layout.portal_braces else None,
        wall_tier_strut_area_mm2=getattr(context.get('wall_tier_strut'), 'A', None),
        runway_area_mm2=context.get('crane_runway_area_mm2'))
    from portalcalc.bracing.diaphragm import DiaphragmSettings, stressed_skin_model, align_fixed_nodes, recover_diaphragm
    diaphragm_settings = DiaphragmSettings.from_dict(context.get('stressed_skin'))
    diaphragm = {'settings': vars(diaphragm_settings), 'active':diaphragm_settings.active}
    sls_model = model
    if diaphragm_settings.active:
        cladding = {'front_end':context['front_clad'], 'back_end':context['back_clad'],
                    **context.get('face_cladding',{})}
        sls_model, diaphragm = stressed_skin_model(model, layout, point_nodes, diaphragm_settings, cladding)
        align_fixed_nodes(model,sls_model)
    return model, sls_model, roof_grid, portal_dofs, point_nodes, diaphragm


def analyse_context(context, strut_profile, end_post_profile, reuse_cases=None):
    """Solve every case. ``reuse_cases`` maps case names to solved results for
    this same model and sections, which are used instead of re-solving.

    ``context['search_only']`` (set by optimiser searches whose results are
    discarded) omits the per-member portal end-force evidence, which only the
    report/export reads; design and restraint checks are unchanged."""
    start = time.perf_counter()
    layout, portal = context['layout'], context['portal']
    member_forces = not context.get('search_only')
    model, sls_model, roof_grid, portal_dofs, point_nodes, diaphragm = prepare_analysis_models(
        context, strut_profile, end_post_profile)
    from portalcalc.bracing.components import StrutPlan, assembly_properties
    from portalcalc.bracing.diaphragm import recover_diaphragm
    cases = []
    for case in context["cases"]:
        reused = (reuse_cases or {}).get(case['name'])
        if reused is not None and reused['case'] is case:
            cases.append(reused)
            continue
        try:
            solved = solve_building_case(model, layout, portal, portal_dofs, point_nodes, case, member_forces)
            # Transverse SLS and crane racking use the bare network: no diaphragm sway credit.
            if diaphragm['active'] and case['level'] == 'SLS' and not transverse_sls(case) and not crane_case_3d(case):
                enhanced = solve_building_case(sls_model, layout, portal, portal_dofs, point_nodes, case, member_forces)
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
    portal_brace_envelopes = portal_brace_envelope(cases)
    roof_nodes = {n for row in roof_grid for n in row}
    sls = [(float(np.linalg.norm(displacement)), case["case"]["name"], i, displacement)
           for case in cases if case["case"]["level"] == "SLS" and not transverse_sls(case["case"])
           and not crane_case_3d(case["case"])
           for i, displacement in enumerate(case["displacements_mm"]) if i in roof_nodes]
    governing = max(sls, key=lambda item: item[0])
    if diaphragm['active']:
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
        'roof_load_basis':copy.deepcopy(context.get('roof_load_basis')),
        "schema": "portalcalc.fixed-bracing-analysis.v1", "status": "SOLVED_DEMANDS_ONLY",
        "created_utc": datetime.now(timezone.utc).isoformat(), "elapsed_seconds": time.perf_counter()-start,
        "source_hash": context["source_hash"], "source_preset": context["source_preset"],
        "nominal_layout": context["nominal_layout"], "analysis_layout": layout.to_dict(),
        "sections": {"strut": strut_record,
                     "end_post": {"section": end_post_profile.name, "area_mm2": end_post_profile.A},
                     **{kind: context[kind] for kind in ("roof_brace", "wall_brace", "end_brace")},
                     **({"canopy_brace": context["canopy_brace"]} if context.get("canopy_brace") else {}),
                     **({"wall_tier_strut": {"section": context["wall_tier_strut"].name,
                                             "area_mm2": context["wall_tier_strut"].A}}
                        if context.get("wall_tier_strut") is not None else {}),
                     **({"crane_runway": {"section": context.get("crane_runway_section"),
                                          "area_mm2": context.get("crane_runway_area_mm2"),
                                          "level_mm": layout.geometry.wall_tier_z_mm,
                                          "basis": "Runway beam as the continuous side-wall strut at runway level; "
                                                   "axial stiffness only, its strength is checked in the crane beam calculation."}}
                        if layout.geometry.wall_tier_z_mm is not None else {}),
                     **({group: portal_brace_section_record(context[group]) for group in PORTAL_BRACE_GROUPS}
                        if layout.portal_braces else {})},
        **({'portal_brace_envelopes': portal_brace_envelopes} if portal_brace_envelopes else {}),
        "nodes": [{"index": i, "label": model.node_labels[i], "point_mm": p} for i, p in enumerate(model.nodes)],
        "portal_full_dofs": len(portal.k), "portal_interface_dofs": len(portal.stiffness),
        "member_envelopes": envelopes, "cases": cases,
        'stressed_skin': diaphragm,
        "governing_sls_displacement": {"resultant_mm": governing[0], "case": governing[1],
                                       "node": governing[2], "components_mm": governing[3]},
        'transverse_sls': transverse_sls_summary(portal, cases, context.get('global_sls_basis')),
        'frame_restraint': frame_restraint_summary(portal, cases),
        'crane_racking_basis': copy.deepcopy(context.get('crane_racking_basis')),
        'crane_racking': crane_racking_summary(portal, cases, context.get('crane_racking_basis')),
        "limitations": ([line.replace('No diaphragm or sheeting stiffness.',
                         'Enabled roof/wall diaphragm stiffness is included in SLS only.') for line in LIMITATIONS]
                        + [diaphragm['basis']] if diaphragm['active'] else list(LIMITATIONS))
                       + ([PORTAL_BRACE_LIMITATION] if layout.portal_braces else []),
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
    spacing=result.get('design',{}).get('layout_spacing',{})
    if 'anchor_bays' in spacing:
        from portalcalc.bracing.spacing import spacing_text
        lines += ['', 'PROJECT BRACED-BAY SPACING',spacing_text(spacing),spacing['basis']]
    roof_basis = result.get('roof_load_basis')
    if roof_basis:
        lines += ['', 'SIMULTANEOUS ROOF LOAD BASIS',roof_basis['basis'],
                  f"Permanent roof load {roof_basis['permanent_roof_kpa']:g} kPa; live/snow service load {roof_basis['live_or_snow_service_kpa']:g} kPa.",
                  'Frame roof tributary widths (m): '+', '.join(f'{v/1000:g}' for v in roof_basis['frame_tributary_widths_mm']),
                  'Roof load extents: '+json.dumps(roof_basis['placements'],sort_keys=True),roof_basis['source'],
                  'Excluded: '+roof_basis['excluded']]
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
                  *([f"Open canopy roofs {'ON' if settings['enabled'] else 'OFF'} (follow the roof): {c_label} = {settings['flexibility_mm_per_kn']:g} mm/kN; "
                     f"{diaphragm['panel_counts']['canopy']} canopy bay panel(s); direction = {settings['shear_direction']}."]
                    if 'canopy' in diaphragm.get('panel_counts',{}) else []),
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
              f"Maximum resultant, gravity/longitudinal SLS: {sls['resultant_mm']:.4f} mm at node {sls['node']+1}; {sls['case']}", ""]
    transverse = result.get('transverse_sls')
    if transverse:
        criterion = f"H/{transverse['divisor']:g}" if transverse['divisor'] else 'no criterion'
        lines += ['TRANSVERSE SLS FRAME SWAY (REPORT ONLY)', transverse['basis'],
                  f"Criterion: {criterion}; {transverse.get('criterion_source') or '-'}. {transverse['case_count']} transverse SLS cases.",
                  'Frame | Y m | Ws eave sway mm | limit mm | ratio | portal alone mm | secant restraint kN/mm | range kN/mm | max G + Ws mm | governing Ws case']
        for frame in transverse['frames']:
            row = frame['governing_ws'] or {}
            k = row.get('secant_restraint_kn_mm')
            span = frame['secant_restraint_range_kn_mm']
            lines.append(f"{frame['index']+1} | {frame['y_mm']/1000:g} | {row.get('movement_mm', 0):.3f} | "
                         f"{row.get('limit_mm', float('nan')):.1f} | {row.get('ratio', float('nan')):.3f} | "
                         f"{row.get('portal_alone_mm', 0):.3f} | {'-' if k is None else f'{k:.4f}'} | "
                         f"{'-' if span is None else f'{span[0]:.4f} to {span[1]:.4f}'} | "
                         f"{frame['max_g_plus_ws_mm']:.3f} | {row.get('case', '-')}")
        lines.append('')
    crane = result.get('crane_racking')
    if crane:
        governing = crane['governing']
        lines += ['CRANE RAIL RACKING (3D)', crane['basis'],
                  f"Crane frame {crane['frame']+1} (Y {crane['frame_y_mm']/1000:g} m); mean rail height "
                  f"{crane['height_mm']:.0f} mm; limit H/{crane['divisor']:g} = {crane['limit_mm']:.2f} mm.",
                  f"Governing: {abs(governing['racking_mm']):.3f} mm, ratio {crane['ratio']:.3f} {crane['status']} "
                  f"({governing['case']}); the frame alone: {abs(governing['frame_alone_mm']):.3f} mm.",
                  'Case | left rail mm | right rail mm | racking mm | frame alone mm | ratio']
        for row in crane['rows']:
            lines.append(f"{row['case']} | {row['left_mm']:.3f} | {row['right_mm']:.3f} | {row['racking_mm']:.3f} | "
                         f"{row['frame_alone_mm']:.3f} | {row['ratio']:.3f}")
        lines.append('')
    restraint = result.get('frame_restraint')
    if restraint:
        lines += ['FRAME RESTRAINT FOR THE 2D PORTAL', restraint['basis'],
                  (f"Adoptable: {restraint['adoptable_kn_mm']:.4f} kN/mm (frame {restraint['governing']['frame']+1}, "
                   f"{restraint['governing']['case']})" if restraint['adoptable'] else f"Not adoptable: {restraint['reason']}"),
                  'Frame | least kN/mm | case | greatest kN/mm']
        for frame in restraint['frames']:
            least, greatest = frame['least'] or {}, frame['greatest'] or {}
            low = '-' if not least else '%.4f' % least['secant_restraint_kn_mm']
            high = '-' if not greatest else '%.4f' % greatest['secant_restraint_kn_mm']
            lines.append(f"{frame['index']+1} | {low} | {least.get('case', '-')} | {high}")
        lines.append('')
    for case in result["cases"]:
        lines += ["="*75, case["case"]["name"], f"Source direction: {case['case']['source_direction']}; Cpi: {case['case']['cpi']:.4f}",
                  f"Signed front/back pressure (+Y): {case['case']['front_kpa']:.5f} / {case['case']['back_kpa']:.5f} kPa",
                  f"Nonlinear iterations: {case['iterations']}; free-DOF residual: {case['residual_n']:.3g} N",
                  f"Elastic tangent status: {case['tangent_status']}"
                  + (f"; slack play {case['slack_play']['play_mm']:.3f} mm over {case['slack_play']['modes']} mode(s), "
                     f"taken up by {', '.join(case['slack_play']['rods'])}" if case.get('slack_play') else ''),
                  f"Global force residual [N]: {case['balance_force_error_n']}",
                  f"Global moment residual [Nmm]: {case['balance_moment_error_nmm']}"]
        if case['case'].get('pressure_basis'):
            b=case['case']['pressure_basis']
            lines += [f"Wind coefficient basis: d/b={b['d_over_b']:.5f} (along-wind depth/crosswind breadth); leeward Cp,e={b['leeward_cpe']:.5f}; effective surfaces {b['effective_surface_count']}; Kce/Kci={b['kce']:g}/{b['kci']:g}.",b['source'],b['scope']]
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
            if work.get('pressure_segments_mm_kpa'):
                lines.append('Signed Y pressure bands [X start mm, X end mm, kPa]: '+json.dumps(work['pressure_segments_mm_kpa']))
        if case['case'].get('transverse_pressure_pattern'):
            pattern=case['case']['transverse_pressure_pattern']
            lines += [f"{case['case']['level']} TRANSVERSE PRESSURE PATTERN",pattern['source'],
                      'Roof Cpe bands [X start mm, X end mm, Cp,e]: '+json.dumps(pattern['roof_cpe_segments_mm']),
                      'End-wall side-suction Cpe bands [X start mm, X end mm, Cp,e]: '+json.dumps(pattern['end_wall_cpe_segments_mm']),
                      'Conventional bare bracing only; no transverse diaphragm credit. Scalar front/back pressure above is the peak; the applied bands are shown here.']
            drag=case['case']['transverse_roof_drag']
            lines.append(f"Transverse roof friction X {drag['signed_x_force_kn']:.3f} kN; Cf={drag['cf']:g}, X={drag['x_start_mm']/1000:g} to {drag['x_end_mm']/1000:g} m. "+drag['basis'])
        if case.get('portal_load_workings'):
            lines += ['SIMULTANEOUS PORTAL LOADS (physical applied X/Y/Z, kN)',
                      f"G/Q factors: {case['case'].get('g_factor',0):g} / {case['case'].get('q_factor',0):g}"]
            for work in case['portal_load_workings']:
                lines.append(f"Portal {work['index']+1}: "+' / '.join(f'{v/1000:.3f}' for v in work['applied_force_n']))
            lines.append('Whole-building applied resultant X/Y/Z kN: '+' / '.join(f'{v/1000:.3f}' for v in case['applied_resultant_n']))
        if case['case'].get('equivalent_case_names'):
            lines += ['Identical physical combination also represents: '+name for name in case['case']['equivalent_case_names']]
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
