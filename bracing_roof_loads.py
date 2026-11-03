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

from loads import (BasicRoofLoads, RoofLoadPlacement, apply_roof_vertical_loads_in_zones,
                   apply_structure_self_weight)
from wind import low_pitch_cpe_values, SIDE_WALL_CPE_BANDS, area_reduction_factor


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


def building_roof_cases(base_cases, portal, geometry, wind_inputs, source, snow_service_kpa=0.):
    """Expand end-wind cases to G/Q and coincident G + normal-wind cases.

    The existing end-wall pressures and friction are retained. The two roof
    external branches use Table 5.3(A) crosswind (R) bands, measured from the
    physical windward building end, with d=building length. Local Table 5.6
    patches remain separate purlin bending checks.
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
    gravity, live = [], []
    for width in widths:
        for gf,qf,destination in [(1.,0.,gravity),(0.,1.,live)]:
            structure.clear_loads()
            for _,kpa,placement in roof_loads.factored_components(gf,qf):
                apply_roof_vertical_loads_in_zones(structure,tops,kpa*width/1000,
                    placement.side,placement.from_percent,placement.to_percent,element_groups={'TOP'})
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
                normal.append(_snapshot(structure))
            for gf in ((.9,1.2) if base['level']=='ULS' else (0.,1.)):
                case = copy.deepcopy(base)
                label = f'{gf:g}G + W' if gf else 'W'
                case.update(name=f"{base['name']} / {branch} / {label}",g_factor=gf,q_factor=0.,
                    cpe_case=branch,wind_family='longitudinal',portal_loads=[_combine((gf,a),(1.,b)) for a,b in zip(gravity,normal)])
                case['pressure_basis']['scope'] = ('Simultaneous end-wall pressures, external roof friction, '
                    'normal roof/clad side-wall pressures and factored G. Transverse wind and wall/underside/exposed-member drag excluded.')
                cases.append(case)

    from bracing_side_wind import side_wind_cases
    transverse_cases=side_wind_cases(portal,g,wind_inputs,gravity,widths)
    cases.extend(transverse_cases)
    # Some dominant-opening presets have identical Cpi branches. Preserve
    # source labels but solve each distinct physical load combination once.
    unique = {}
    for case in cases[4:]+cases[:4]:
        signature = hashlib.sha256(json.dumps({k:case.get(k) for k in
            ('level','front_kpa','back_kpa','portal_loads','roof_drag','end_wall_pressure_segments')},sort_keys=True).encode()).hexdigest()
        if signature in unique:
            unique[signature].setdefault('equivalent_case_names',[]).append(case['name'])
        else:
            unique[signature] = case
    basis = {'source':'AS/NZS 1170.2:2021 Amd 2, Tables 5.2(A/B/C), 5.3(A/B/C), 5.9; existing project G/Q combinations',
        'frame_tributary_widths_mm':widths.tolist(),
        'permanent_roof_kpa':-roof_loads.permanent_vertical_kpa,'live_or_snow_service_kpa':-roof_loads.q,
        'placements':copy.deepcopy(settings.get('roof_load_placements',{})),
        'transverse_wind':{'enabled':True,'level':'ULS','directions':['Left to right','Right to left'],
            'basis':'Simultaneous roof/wall external and internal pressures, both roof alternatives and 0.9G/1.2G. Conventional bare bracing only. End-wall suction varies across the span. Roof friction parallel to sheet ribs is included where applicable.'},
        'basis':'Full portal distributed loads and member self-weight, condensed/recovered with the original frame stiffness. G includes solar/fire/HVAC/other. End frames have half adjacent-bay roof tributaries; wind zones use bay support shape functions. Longitudinal wind includes roof/clad side-wall normal pressures and roof friction. ULS transverse cases include roof/side-wall normal pressures, roof friction and varying end-wall suction. Local Kl patches are excluded from the global model.',
        'excluded':'Transverse SLS, crane/storage actions, wall/underside/exposed-member friction and purlin continuity reaction redistribution are outside this model.'}
    return list(unique.values()), basis
