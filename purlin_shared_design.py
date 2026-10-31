"""Project-adopted 50/50 load allocation and PRELIMINARY table-reference check.

Reference actions are elastic responses at a published UDL rating. They are
not independently verified member M/V resistances for an arbitrary diagram.
The approximation is explicit in every affected check and overall result.
"""
import json
import math
from functools import lru_cache

from bracing_analysis import AnalysisError
from purlin_beam import solve_run
from purlin_combined import PROPERTIES, properties, compression_capacity, tension_capacity, method
from purlin_strut_tables import span_capacities, single_span_capacities

ARRANGEMENT = 'shared_purlin'
METHOD = 'assigned_half_reference'
BASIS = ('PRELIMINARY table-reference screen: 50/50 APPLIED roof load in added bays; '
         'existing continuity and adjoining bays analysed, added purlin single span. '
         'UC = axial interaction + max(moment/reference moment, shear/reference shear). '
         'Reference actions come from the published UDL-rated arrangement; they are NOT '
         'general moment/shear resistances or complete AS/NZS 4600 acceptance. '
         'The 50/50 allocation and cladding/collector force transfer are design assumptions; connections unchecked.')


def location_data(context, data, span):
    """Freeze supported candidates and source rows without runtime file access."""
    bundle = context.get('purlin_strut_tables', {})
    rating = data['axial_rating']
    bridge = rating.get('shared_bridging_rows', data.get('bending_snapshot', {}).get('bridging_rows', 0))
    fixed = rating.get('shared_cladding_fixed', True)
    references, candidates = {}, []
    for span_type in ('Single span', 'Double span', 'Continuous lapped'):
        try:
            references[span_type] = span_capacities(bundle, data['section'], span, bridge, span_type)
        except AnalysisError as error:
            references[span_type] = {'error': str(error)}
    for depth, gauge in sorted(PROPERTIES['C']):
        section = f'C/Z {depth} {gauge}'
        try:
            p = properties(section, 'C')
            cap = single_span_capacities(bundle, section, span, bridge)
        except AnalysisError:
            continue
        candidates.append({'section': section, 'area_mm2': p['area_mm2'], 'section_properties': p,
                           'analysis_span_mm': span, 'basis_key': f'{section}|shared single span|{span:g}|B{bridge}',
                           'axial_rating': {'purlin_shape': 'C', 'tension_method': 'calculated'},
                           'table_capacity': cap, 'shared_cladding_fixed': fixed})
    return references, candidates


def _solve(payload):
    return _cached_shared(json.dumps(payload, sort_keys=True, allow_nan=False))


@lru_cache(maxsize=64)
def _cached_shared(payload):
    # Keep the repeated roof-pattern/reference solves separate from the finite
    # coupling cache. Added profiles do not change roof bending in this model.
    return solve_run(**json.loads(payload))


def _added_actions(q, length, inertia, offset):
    samples = []
    for i in range(13):
        x = length*i/12
        samples.append({'x_mm': offset+x, 'moment_knm': q*x*(length-x)/2/1e6,
                        'shear_kn': q*(length/2-x)/1000,
                        'deflection_mm': q*x*(length**3-2*length*x*x+x**3)/(24*200000*inertia), 'plies': 1})
    return {'samples': samples, 'positive_moment_knm': max(q, 0)*length**2/8/1e6,
            'negative_moment_knm': max(-q, 0)*length**2/8/1e6,
            'shear_kn': abs(q)*length/2/1000, 'deflection_mm': 5*abs(q)*length**4/(384*200000*inertia)}


def solve_shared(assemblies, spans, line):
    data = assemblies[0].location['purlin']
    rating, inputs = data['axial_rating'], data.get('beam_inputs')
    if not inputs:
        raise AnalysisError('Roof beam inputs missing for 50/50 purlin check')
    p = properties(data['section'], rating.get('purlin_shape', 'Z'))
    n = assemblies[0].location['existing_count']
    if any(a.location['existing_count'] != n or a.location['purlin']['section'] != data['section'] or
           a.location['purlin']['axial_rating'].get('purlin_shape', 'Z') != p['shape'] or
           abs(a.location['purlin']['area_mm2']/p['area_mm2']-1) > .005 or
           a.location['purlin'].get('beam_inputs') != inputs for a in assemblies):
        raise AnalysisError('50/50 run requires consistent installed profile, linked count, shape and roof inputs')
    if any(abs(a.location['purlin'].get('bending_span_mm', span)-span) > 1e-3 for a,span in zip(assemblies,spans)):
        raise AnalysisError('Installed roof bending basis does not match the actual bay span')
    if any(a.arrangement == 'sheeted_purlin' for a in assemblies):
        raise AnalysisError('Do not mix finite cladding coupling and assigned 50/50 sharing on one roof run')
    kind = {'Single span': 'single', 'Double span': 'double', 'Continuous lapped': 'continuous_lapped'}[inputs['span_type']]
    text = str(rating.get('beam_roof_releases', 'auto')).strip().lower()
    releases = None if text == 'auto' else [] if text in ('none', '') else [int(v.strip()) for v in text.split(',')]
    actual_releases = list(range(1, len(spans))) if kind == 'single' else list(range(2, len(spans), 2)) if kind == 'double' else []
    if releases is not None and releases != actual_releases:
        raise AnalysisError('Nonstandard release schedule requires declared member capacities, not the preliminary reference check')
    if max(spans)-min(spans) > 1e-4:
        raise AnalysisError('Unequal spans require declared member capacities, not the preliminary reference check')
    lap = float(rating.get('beam_lap_ratio', .15))
    if kind == 'continuous_lapped' and (len(spans) != 5 or p['shape'] != 'Z' or abs(lap-.15) > 1e-8):
        raise AnalysisError('The 5A/5B preliminary reference requires five Z spans with 15% laps')
    active = {i: next(c['data'] for c in a.components if c.get('shared_beam'))
              for i, a in enumerate(assemblies) if a.arrangement == ARRANGEMENT}
    if any(not c.get('shared_cladding_fixed') for c in active.values()):
        raise AnalysisError('50/50 purlins must be screw-fixed to cladding and have the stated bridging installed')
    loads = [dict(name='ULS outward', roof_n_mm=-inputs['outward_kn_m'], own_factor=0.),
             dict(name='ULS inward', roof_n_mm=inputs['inward_strength_kn_m'], own_factor=1.2),
             dict(name='SLS outward', roof_n_mm=-inputs.get('service_outward_kn_m', inputs['service_kn_m']), own_factor=0.),
             dict(name='SLS inward G + wind', roof_n_mm=inputs.get('service_inward_kn_m', inputs['service_kn_m'])+inputs['permanent_kn_m'], own_factor=1.),
             dict(name='SLS gravity G + Q', roof_n_mm=inputs['gravity_service_kn_m'], own_factor=1.)]
    for load in loads:
        load['roof_n_mm_by_bay'] = [load['roof_n_mm']*(.5 if i in active else 1.) for i in range(len(spans))]
        load['added_n_mm'] = 0.
    roof = _solve({'spans_mm': spans, 'roof_i_mm4': p['web_ix_mm4'], 'added_i_mm4': p['web_ix_mm4'],
                   'roof_count': n, 'span_type': kind, 'lap_ratio': lap, 'loads': loads})
    cases = []
    for base, load in zip(roof['cases'], loads):
        bays, added_total, offset = [], 0., 0.
        added_loads = []
        for i, (bay, length) in enumerate(zip(base['bays'], spans)):
            q = 0.
            if i in active:
                c = active[i]
                q = load['roof_n_mm']*n*.5 + load['own_factor']*c['area_mm2']*7.85e-6*9.80665
                added = _added_actions(q, length, c['section_properties']['web_ix_mm4'], offset)
                added_total += q*length
            else:
                added = bay['added']
            bays.append({**bay, 'added': added})
            added_loads.append(q)
            offset += length
        cases.append({**base, 'bays': bays, 'loads': {**load, 'added_n_mm_by_bay': added_loads},
                      'applied_load_n': base['applied_load_n']+added_total,
                      'support_reaction_n': base['support_reaction_n']-added_total})
    references = {}
    for i, a in enumerate(assemblies):
        span_type = inputs['span_type']
        count = 1 if kind == 'single' or (kind == 'double' and i == len(spans)-1 and len(spans)%2) else 2 if kind == 'double' else 5
        if count == 1:
            span_type = 'Single span'
        caps = a.location['shared_reference_caps'][span_type]
        if 'error' in caps:
            raise AnalysisError(caps['error'])
        ref = _solve({'spans_mm': [spans[i]]*count, 'roof_i_mm4': p['web_ix_mm4'], 'added_i_mm4': p['web_ix_mm4'],
                      'span_type': 'single' if count == 1 else 'double' if count == 2 else 'continuous_lapped',
                      'lap_ratio': lap, 'loads': [{'name': 'unit', 'roof_n_mm': 1., 'added_n_mm': 0.}]})
        reference = {'moment_per_kn_m': max(max(b['roof']['positive_moment_knm'], b['roof']['negative_moment_knm']) for b in ref['cases'][0]['bays']),
                     'deflection_per_kn_m': max(b['roof']['deflection_mm'] for b in ref['cases'][0]['bays']),
                     'shear_per_kn_m': max(b['roof']['shear_kn'] for b in ref['cases'][0]['bays']), 'capacity': caps}
        references[str(i)] = reference
    return {**roof, 'cases': cases, 'line': line, 'added_bays': list(active),
            'section_properties': p, 'added_sections': {str(i): c for i,c in active.items()},
            'references': references, 'design_method': METHOD, 'preliminary': True,
            'coupling_defined': True, 'coupling_source': 'Project-adopted 50/50 applied roof-load allocation; no stiffness-derived sharing',
            'direct_roof_pickup_fraction': .5, 'basis': BASIS,
            'note': '50% of the original line load goes to each line in added bays; adjoining bays retain full applied load. Axial force shares by EA. Added self-weight is separate. No equal-moment or rigid cladding-tie assumption.'}


def shared_check(run, entry, component, force, name, level, divisor, criteria):
    existing = component['existing']
    data, bay = component['data'], entry['bay']
    base = {'case': name, 'method': METHOD, 'preliminary': True, 'section': data['section'],
            'beam_role': 'roof' if existing else 'added', 'beam_line': entry['line'], 'beam_bay': bay,
            'axial_kn': force, 'coupling_defined': True, 'roof_load_fraction': .5 if bay in run['added_bays'] else 1., 'basis': BASIS}
    if entry['error']:
        return {**base, 'mode': entry['error'], 'ratio': None, 'passes': False}
    role = base['beam_role']
    demands = [(c['name'], c['bays'][bay][role]) for c in run['cases'] if c['name'].startswith(level)]
    base['beam_demands'] = [{'case': c, **{k:v for k,v in d.items() if k != 'samples'}} for c,d in demands]
    if existing:
        reference = run['references'][str(bay)]
    else:
        l = data['analysis_span_mm']
        reference = {'moment_per_kn_m': l*l/8/1e6, 'shear_per_kn_m': l/2/1000,
                     'deflection_per_kn_m': 5*l**4/(384*200000*data['section_properties']['web_ix_mm4']),
                     'capacity': data['table_capacity']}
    if level == 'SLS':
        d = max(d['deflection_mm'] for _,d in demands)
        rated = reference['deflection_per_kn_m']*reference['capacity']['service_capacity_kn_m']
        limit = data['analysis_span_mm']/divisor
        adjusted = d/rated*(data['analysis_span_mm']/150.)
        return {**base, 'mode': 'PRELIMINARY 50/50 table-reference serviceability',
                'ratio': max(d, adjusted)/limit, 'elastic_deflection_mm': d,
                'table_adjusted_deflection_mm': adjusted, 'deflection_mm': max(d, adjusted),
                'limit_mm': limit, 'table_reference': reference}
    try:
        p = properties(data['section'], data['axial_rating'].get('purlin_shape', 'Z'))
        ns = compression_capacity(p['area_mm2'], p['web_rx_mm'], data['analysis_span_mm'])
        if existing and method(data['axial_rating']) == 'declared':
            raise AnalysisError('Declared Nc/Nex purlins require the declared beam-capacity option for shared-load checks')
        comparisons = []
        for case, d in demands:
            direction = 'outward' if 'outward' in case else 'inward'
            cap = reference['capacity'][direction+'_capacity_kn_m']
            mr, vr = reference['moment_per_kn_m']*cap, reference['shear_per_kn_m']*cap
            bm = max(d['positive_moment_knm'], d['negative_moment_knm'])/mr
            shear = d['shear_kn']/vr
            comparisons.append({'case': case, 'reference_line_load_kn_m': cap, 'reference_moment_knm': mr,
                                'reference_shear_kn': vr, 'moment_ratio': bm, 'shear_ratio': shear, 'ratio': max(bm, shear)})
        envelope = max(r['ratio'] for r in comparisons)
        tension = tension_capacity(data, criteria) if force > 1e-8 else {}
        axial = force/tension['tension_capacity_kn'] if force > 1e-8 else max(-force,0.)/ns
    except AnalysisError as error:
        return {**base, 'mode': str(error), 'ratio': None, 'passes': False}
    return {**base, **tension, 'mode': 'PRELIMINARY 50/50 axial + table-reference envelope',
            'ratio': axial+envelope, 'bending_ratio': max(c['moment_ratio'] for c in comparisons),
            'reference_envelope_ratio': envelope, 'axial_ratio': axial, 'compression_capacity_kn': ns,
            'table_reference': reference, 'reference_comparisons': comparisons}
