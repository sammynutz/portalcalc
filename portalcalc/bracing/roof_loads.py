"""Simultaneous longitudinal-wind and roof-gravity loads on actual portals.

N, mm, MPa. Roof area gravity loads use horizontal projection. Wind pressure
acts normal to the roof. Piecewise end-wind zones are integrated along the
building using simply supported bay reaction shape functions (not a worst
uniform roof pressure applied to every frame).
"""
import copy
import hashlib
import json

import numpy as np

from portalcalc.frame.solver import apply_eave_strip_loads
from portalcalc.loads import (BasicRoofLoads, RoofLoadPlacement, apply_roof_vertical_loads_in_zones,
                   apply_structure_self_weight)
from portalcalc.wind import (low_pitch_cpe_values, SIDE_WALL_CPE_BANDS, area_reduction_factor, action_combination_factors,
                  open_canopy_cpi, transverse_free_roof_cpn)

CANOPY_TOP_GROUPS = {'left': 'LEFT_CANOPY_TOP', 'right': 'RIGHT_CANOPY_TOP'}


def canopy_parts(structure, source=None):
    """Open canopies on a portal: roof members, nodes, area loads and plan width.

    ``source`` is the preset (its 'canopies' section gives the canopy G/Q/solar
    kPa, pitch and underside). Plan width is measured from the main eave.
    """
    settings = (source or {}).get('canopies', {}) or {}
    xs = [n.x for e in structure.elements if e.group == 'TOP' for n in (e.start, e.end)]
    parts = []
    for side, group in CANOPY_TOP_GROUPS.items():
        elements = [e for e in structure.elements if e.group == group]
        if not elements or not xs:
            continue
        nodes = sorted({n.id: n for e in elements for n in (e.start, e.end)}.values(), key=lambda n: n.x)

        def kpa(key):
            try:
                return -abs(float(settings.get(f'{side}_{key}', 0) or 0))
            except (TypeError, ValueError):
                return 0.

        try:
            pitch = float(settings.get(f'{side}_pitch_deg', 0) or 0)
        except (TypeError, ValueError):
            pitch = 0.
        parts.append({'side': side, 'group': group, 'elements': elements, 'nodes': nodes,
                      'plan_mm': (min(xs)-nodes[0].x) if side == 'left' else (nodes[-1].x-max(xs)),
                      'slope_mm': sum(e.length() for e in elements), 'pitch_deg': pitch,
                      'underside': 'blocked' if str(settings.get(f'{side}_underside', '')).startswith('Blocked') else 'empty',
                      'loads': BasicRoofLoads(g=kpa('g_kpa'), q=kpa('q_kpa'), solar=kpa('solar_kpa'))})
    return parts


def fully_open(inputs):
    """No clad wall: the 2D frame treats the roof (and canopies) as free roofs."""
    return not any((inputs.left_wall_clad, inputs.right_wall_clad, inputs.front_wall_clad, inputs.back_wall_clad))


def add_normal_line_load(structure, element, outward_n_mm):
    """Uniform load normal to a roof member, positive outward (upward side)."""
    dx, dz = element.end.x-element.start.x, element.end.y-element.start.y
    sign = 1. if dx >= 0 else -1.
    structure.add_uniform_global_line_load(element, -sign*dz/element.length()*outward_n_mm,
                                           sign*dx/element.length()*outward_n_mm)


def apply_eave_strips(structure, eave_strips, walls_only=False):
    """Envelope outside the frame lines, carried to the eave nodes as the 2D frame does.

    The portal's top chord nodes sit below the eave pitching point and inside
    the wall cladding line. ``eave_strips`` (the 2D frame's
    ``eave_node_basis_record['strips']``) gives, per side, the wall height above
    the node and the roof length beyond the column centreline; the line loads
    already on the column top and the eave end of the roof are extended over
    them (frame.solver.apply_eave_strip_loads).

    ``walls_only`` is for wind: the 2D frame applies roof wind as point loads,
    which its strips do not extend, so only the wall strip applies.
    """
    if not eave_strips:
        return
    loads = dict(structure.element_uniform_loads)
    if walls_only:
        columns = {e.id for e in structure.elements if 'COLUMN' in e.group.upper()}
        loads = {key: value for key, value in loads.items() if key in columns}
    apply_eave_strip_loads(structure, eave_strips, [(loads, 1.0)])


def frame_strip_integrals(stations, segments, reverse=False):
    """Integrate piecewise value × bay support shape; conserve force and moment."""
    result = np.zeros(len(stations))
    length = stations[-1]
    for lo, hi, value in segments:
        if reverse:
            lo, hi = length-hi, length-lo
        for i, (a,b) in enumerate(zip(stations, stations[1:])):
            start, end = max(a,lo), min(b,hi)
            if end <= start:
                continue
            total = (end-start)*value
            right = ((start+end)/2-a)/(b-a)
            result[i] += total*(1-right)
            result[i+1] += total*right
    return result


def _snapshot(structure, extra_member_loads=None):
    member_loads={str(e.id):e.equivalent_nodal_load_local(load)
        for e in structure.elements if (load := structure.element_uniform_loads.get(e.id)) is not None}
    for key,value in (extra_member_loads or {}).items():
        member_loads[str(key)]=member_loads.get(str(key),np.zeros(6))+np.asarray(value)
    return {'loads_n': structure.load_vector().tolist(),
            'member_equivalent_loads': {key:value.tolist() for key,value in member_loads.items()}}


def _combine(*terms):
    loads = np.zeros(len(terms[0][1]['loads_n']))
    members = {}
    for factor, state in terms:
        if not factor:
            continue
        loads += factor*np.asarray(state['loads_n'])
        for key, values in state['member_equivalent_loads'].items():
            members.setdefault(key,np.zeros(6))
            members[key] += factor*np.asarray(values)
    return {'loads_n': loads.tolist(), 'member_equivalent_loads': {k:v.tolist() for k,v in members.items()}}


def crane_cases(structure, crane, gravity, live):
    """3D crane cases: the crane on one frame (``crane['frame_index']``), G/Q on every frame.

    Crab left/right x lateral +/-X (loads.CRANE_CASES), with the 2D frame's
    crane actions (crane_beam.apply_crane_actions; the runway self weight is
    not added):
    - SLS "G + Crane" and "G + 0.7Q + Crane": racking is measured against the
      "SLS / G" shape (rails aligned under dead load). G is kept in the solve so
      the tension-only rods are in their dead-load state.
    - ULS "1.2G + 1.5Q + Crane": the bracing members are checked for the crane.
    """
    from portalcalc.crane_beam import apply_crane_actions
    from portalcalc.loads import CRANE_CASES
    nodes = {n.id: n for n in structure.nodes}
    crane_nodes = {side: nodes[crane['node_ids'][side]] for side in ('left', 'right')}
    frame = crane['frame_index']
    cases = []
    for crane_label, position in CRANE_CASES:
        states = {}
        for level, serviceability in (('SLS', True), ('ULS', False)):
            structure.clear_loads()
            apply_crane_actions(structure, crane_nodes, crane['portal_actions'], position, serviceability, 0.)
            states[level] = _snapshot(structure)
        for level, gf, qf, label in (('SLS', 1., 0., 'G + Crane'), ('SLS', 1., .7, 'G + 0.7Q + Crane'),
                                     ('ULS', 1.2, 1.5, '1.2G + 1.5Q + Crane')):
            cases.append({
                'name': f"{level} / {label} / frame {frame+1}: {crane_label}", 'level': level,
                'front_kpa': 0., 'back_kpa': 0., 'source_direction': None, 'cpi': 0.,
                'g_factor': gf, 'q_factor': qf, 'wind_family': 'crane',
                'crane_position': position, 'crane_frame': frame, 'crane_label': crane_label,
                **({'crane_baseline': 'SLS / G'} if level == 'SLS' else {}),
                'portal_loads': [_combine((gf, a), (qf, b), (1. if i == frame else 0., states[level]))
                                 for i, (a, b) in enumerate(zip(gravity, live))]})
    cases += buffer_cases(crane, gravity)
    return cases


def buffer_cases(crane, gravity):
    """ULS crane buffer impact on the end stops, front and back separately.

    The factored buffer force (``crane['buffer']``: force per rail x ULS
    factor) acts along the building on BOTH runways at the impacted end frame
    (front stops -Y at frame 1, back stops +Y at the last frame), at runway
    level; the runway beam carries it to the braced bays. With 1.2G.
    """
    buffer = crane.get('buffer') or {}
    force = float(buffer.get('force_kn', 0.) or 0.)*float(buffer.get('uls_factor', 1.))*1000.
    if force <= 0.:
        return []
    last = len(gravity)-1
    return [{'name': f'ULS / 1.2G + Crane buffer / {label}', 'level': 'ULS',
             'front_kpa': 0., 'back_kpa': 0., 'source_direction': None, 'cpi': 0.,
             'g_factor': 1.2, 'q_factor': 0., 'wind_family': 'crane', 'crane_buffer': label,
             'wall_tier_loads': [{'face': face, 'frame': frame, 'fy_n': sign*force}
                                 for face in ('left_wall', 'right_wall')],
             'portal_loads': [_combine((1.2, a)) for a in gravity]}
            for label, frame, sign in (('front stops', 0, -1.), ('back stops', last, 1.))]


def building_roof_cases(base_cases, portal, geometry, wind_inputs, source, snow_service_kpa=0., crane=None,
                        eave_strips=None):
    """Expand end-wind cases to G/Q and coincident G + normal-wind cases.

    The existing end-wall pressures and friction are retained. The two roof
    external branches use Table 5.3(A) crosswind (R) bands, measured from the
    physical windward building end, with d=building length. Local Table 5.6
    patches remain separate purlin bending checks. ``eave_strips`` adds the
    roof and wall outside the frame lines to every portal load (see
    ``apply_eave_strips``).
    """
    g = geometry
    structure = copy.deepcopy(portal.structure)
    tops = sorted({n.id:n for e in structure.elements if e.group=='TOP'
                   for n in (e.start,e.end)}.values(),key=lambda n:n.x)
    settings = source['loads']
    placements = {name:RoofLoadPlacement(**settings.get('roof_load_placements',{}).get(key,{}))
                  for name,key in [('G','g'),('Q','q'),('Solar','solar'),('Other','other')]}
    roof_loads = BasicRoofLoads(g=-abs(float(settings.get('g_kpa',0))),
        q=-max(abs(float(settings.get('q_kpa',0))),snow_service_kpa),
        solar=-abs(float(settings.get('solar_kpa',0))),
        fire_service=-abs(float(settings.get('fire_service_kpa',0))),
        hvac=-abs(float(settings.get('hvac_kpa',0))),other=-abs(float(settings.get('other_kpa',0))),
        placements=placements)
    widths = frame_strip_integrals(g.bay_stations_mm,[(0.,g.length_mm,1.)])
    canopies = canopy_parts(structure, source)
    free_roof = fully_open(wind_inputs)
    gravity, live = [], []
    for width in widths:
        for gf,qf,destination in [(1.,0.,gravity),(0.,1.,live)]:
            structure.clear_loads()
            for _,kpa,placement in roof_loads.factored_components(gf,qf):
                apply_roof_vertical_loads_in_zones(structure,tops,kpa*width/1000,
                    placement.side,placement.from_percent,placement.to_percent,element_groups={'TOP'})
            # Canopy G/Q/solar as the 2D frame applies them (full canopy, no
            # snow), on the canopy roof members only.
            for part in canopies:
                for _,kpa,_ in part['loads'].factored_components(gf,qf):
                    if kpa:
                        apply_roof_vertical_loads_in_zones(structure,part['nodes'],kpa*width/1000,'Both',0.,100.,
                                                           element_groups={part['group']})
            apply_eave_strips(structure,eave_strips)
            if gf:
                apply_structure_self_weight(structure,structure.elements,1.)
            destination.append(_snapshot(structure))

    cases = []
    # Pure gravity checks are necessary: their roof-brace/tie axial effects can
    # govern even when there is no longitudinal end-wall wind resultant.
    for level,gf,qf,label in [('ULS',1.35,0.,'1.35G'),('ULS',1.2,1.5,'1.2G + 1.5Q'),
                              ('SLS',1.,0.,'G'),('SLS',1.,1.,'G + Q')]:
        cases.append({'name':f'{level} / {label}','level':level,'front_kpa':0.,'back_kpa':0.,
            'source_direction':None,'cpi':0.,'g_factor':gf,'q_factor':qf,'gravity_only':True,'wind_family':'gravity',
            'portal_loads':[_combine((gf,a),(qf,b)) for a,b in zip(gravity,live)]})

    for base in base_cases:
        reverse = 'Back to front' in base['name']
        q = base['wu_kpa']*base['wind_pressure_scale']
        h = base['roof_drag']['mean_roof_height_m']
        for branch in ('External max','External min'):
            roof_segments = [(max(0.,a*h*1000),min(g.length_mm,b*h*1000),cpmax if branch=='External max' else cpmin)
                             for _,a,b,cpmax,cpmin in low_pitch_cpe_values(h/(g.length_mm/1000))]
            side_segments = [(a*h*1000,min(g.length_mm,b*h*1000),cp)
                             for a,b,cp in SIDE_WALL_CPE_BANDS]
            roof_cp = frame_strip_integrals(g.bay_stations_mm,roof_segments,reverse)
            side_cp = frame_strip_integrals(g.bay_stations_mm,side_segments,reverse)
            normal = []
            for i,width in enumerate(widths):
                structure.clear_loads()
                roof_area = sum(e.length() for e in structure.elements if e.group=='TOP')*width/1e6
                roof_factor = max(area_reduction_factor(wind_inputs,'roof',roof_area)*base['pressure_basis']['kce'],.8)
                internal = base['cpi']*base['pressure_basis']['kci']*width
                # Integrated pressure × tributary width, N/mm of sloping roof.
                roof_line = q*(-roof_factor*roof_cp[i]+internal)/1000
                side_factor = max(area_reduction_factor(wind_inputs,'side_wall',g.eave_mm*width/1e6)*base['pressure_basis']['kce'],.8)
                side_line = q*(-side_factor*side_cp[i]+internal)/1000
                if free_roof:
                    # All walls open: the 2D free-roof net coefficient for
                    # longitudinal wind (B.4(A), empty under), no Cpi, as 2D.
                    # 'External max' is 2D 'End +', which 2D takes as uplift.
                    use_uplift = branch=='External max'
                    roof_line = q*(-transverse_free_roof_cpn('empty',use_uplift))*width/1000
                for part in canopies:
                    if free_roof:
                        outward = q*(-transverse_free_roof_cpn(part['underside'],use_uplift))*width/1000
                    else:
                        # Same per-frame roof bands as the main roof on top; the
                        # open underside takes the shared canopy Cpi rule with
                        # this frame's average side-wall Cpe.
                        canopy_factor = max(area_reduction_factor(wind_inputs,'roof',part['slope_mm']*width/1e6)
                                            *base['pressure_basis']['kce'],.8)
                        wall_cpe = side_cp[i]/width if width>1e-9 else 0.
                        cpi_c,_,_ = open_canopy_cpi(wind_inputs,'End +',part['side'],wind_inputs.building_length_m,
                                                    g.eave_mm/1000,wall_cpe=wall_cpe)
                        kci_c = action_combination_factors(wind_inputs,cpi_c)[1]
                        outward = q*(-canopy_factor*roof_cp[i]+cpi_c*kci_c*width)/1000
                    for e in part['elements']:
                        add_normal_line_load(structure,e,outward)
                for e in structure.elements:
                    if e.group=='TOP':
                        dx, dz = e.end.x-e.start.x,e.end.y-e.start.y
                        sign = 1. if dx>=0 else -1.
                        structure.add_uniform_global_line_load(e,-sign*dz/e.length()*roof_line,sign*dx/e.length()*roof_line)
                    elif 'COLUMN' in e.group.upper():
                        x = (e.start.x+e.end.x)/2
                        if abs(x-tops[0].x)<1. and wind_inputs.left_wall_clad:
                            structure.add_uniform_global_line_load(e,-side_line,0.)
                        elif abs(x-tops[-1].x)<1. and wind_inputs.right_wall_clad:
                            structure.add_uniform_global_line_load(e,side_line,0.)
                apply_eave_strips(structure,eave_strips,walls_only=True)
                normal.append(_snapshot(structure))
            for gf in ((.9,1.2) if base['level']=='ULS' else (0.,1.)):
                case = copy.deepcopy(base)
                label = f'{gf:g}G + W' if gf else 'W'
                case.update(name=f"{base['name']} / {branch} / {label}",g_factor=gf,q_factor=0.,
                    cpe_case=branch,wind_family='longitudinal',portal_loads=[_combine((gf,a),(1.,b)) for a,b in zip(gravity,normal)])
                case['pressure_basis']['scope'] = ('Simultaneous end-wall pressures, external roof friction, '
                    'normal roof/clad side-wall pressures and factored G. Transverse wind and wall/underside/exposed-member drag excluded.')
                cases.append(case)

    from portalcalc.bracing.side_wind import side_wind_cases
    transverse_cases=side_wind_cases(portal,g,wind_inputs,gravity,widths,source=source,eave_strips=eave_strips)
    cases.extend(transverse_cases)
    if crane:
        cases.extend(crane_cases(structure, crane, gravity, live))
    # Some dominant-opening presets have identical Cpi branches. Preserve
    # source labels but solve each distinct physical load combination once.
    unique = {}
    for case in cases[4:]+cases[:4]:
        signature = hashlib.sha256(json.dumps({k:case.get(k) for k in
            ('level','front_kpa','back_kpa','portal_loads','roof_drag','end_wall_pressure_segments',
             'wall_tier_loads')},sort_keys=True).encode()).hexdigest()
        if signature in unique:
            unique[signature].setdefault('equivalent_case_names',[]).append(case['name'])
        else:
            unique[signature] = case
    basis = {'source':'AS/NZS 1170.2:2021 Amd 2, Tables 5.2(A/B/C), 5.3(A/B/C), 5.9; existing project G/Q combinations',
        'frame_tributary_widths_mm':widths.tolist(),
        'permanent_roof_kpa':-roof_loads.permanent_vertical_kpa,'live_or_snow_service_kpa':-roof_loads.q,
        'placements':copy.deepcopy(settings.get('roof_load_placements',{})),
        'transverse_wind':{'enabled':True,'levels':['ULS','SLS'],'directions':['Left to right','Right to left'],
            'basis':'Simultaneous roof/wall external and internal pressures and both roof alternatives: ULS with 0.9G/1.2G, SLS at serviceability pressure as Ws and G + Ws. Conventional bare bracing only. End-wall suction varies across the span. Roof friction parallel to sheet ribs is included where applicable. Transverse SLS reports per-frame eave sway only; it does not govern 3D sizing.'},
        'basis':'Full portal distributed loads and member self-weight, condensed/recovered with the original frame stiffness. G includes solar/fire/HVAC/other. End frames have half adjacent-bay roof tributaries; wind zones use bay support shape functions. Longitudinal wind includes roof/clad side-wall normal pressures and roof friction. Transverse ULS/SLS cases include roof/side-wall normal pressures, roof friction and varying end-wall suction. Local Kl patches are excluded from the global model.',
        'crane':(None if not crane else {'frame': crane['frame_index']+1, 'frame_y_mm': crane['frame_y_mm'],
            'basis': 'Crane on one frame (crab left/right x lateral +/-X): SLS G + Crane and G + 0.7Q + Crane '
                     '(racking from the G-only shape), ULS 1.2G + 1.5Q + Crane (brace strength). Runway self weight '
                     'not added. ULS 1.2G + buffer impact on the end stops (front and back separately): '
                     'the factored buffer force on both runways at runway level, carried by the runway beam '
                     'to the side-wall bracing.',
            'buffer': copy.deepcopy(crane.get('buffer'))}),
        'excluded':'Transverse SLS diaphragm credit, storage actions, wall/underside/exposed-member friction and purlin continuity reaction redistribution are outside this model.'+('' if crane else ' No crane.')}
    return list(unique.values()), basis
