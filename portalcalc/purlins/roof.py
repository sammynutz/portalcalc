"""Roof purlin end zones: Table 5.6 gable-end local pressure on the actual run.

Step 3 of docs/SECONDARY_MEMBERS_PLAN.md. The general (uniform) roof purlin
cases keep the direct manufacturer-table check in ``frame/engine/secondary.py``.
This module adds the gable-end patches (RA1, RA2 and RC1/RC2, built by
``purlins/end_local.py``, the same cases the 3D bracing check uses) for every
physical purlin row, analysed along the actual bays and laps
(``purlins/beam.solve_run``), and chooses a thicker end-zone section of the
same profile where the patches need it.

Check, per bay and per row, as an equivalent table line load (PRELIMINARY,
as the 3D check): the bay's own-ply moment and shear divided by the moment
and shear of the table's reference arrangement under 1 kN/m (1, 2 or 5 equal
spans of the design bay), against the bay section's table capacity, using
the lower of outward and inward because a patch case can be mixed-sign.
Serviceability compares the deflection with L/150 and with the reference
deflection at the table's L/150 capacity. Bridging is chosen for the demand.
"""
import copy
from dataclasses import asdict
import json
import math
from types import SimpleNamespace

from portalcalc.bracing.analysis import AnalysisError
from portalcalc.frame.purlin_selection import profile_key
from portalcalc.purlins import tables
from portalcalc.purlins.beam import solve_run
from portalcalc.purlins.combined import properties
from portalcalc.purlins.end_local import end_roof_local_cases

KIND = {"Single span": "single", "Double span": "double", "Continuous lapped": "continuous_lapped"}
REFERENCE_SPANS = {"single": 1, "double": 2, "continuous_lapped": 5}
LAP_RATIO = .15
MAX_END_BAYS = 2
MAX_RUN_BAYS = 20          # beam.solve_run limit; longer buildings check each end on 10 bays
END_RUN_BAYS = 10
STEEL_KG_PER_MM3 = 7.85e-6
BASIS = ("PRELIMINARY end-zone check: AS/NZS 1170.2 Table 5.6 gable-end local pressure patches on each purlin "
         "row, analysed along the actual bays and laps (first-order beam, gross web-axis I, lap EI the sum of the "
         "plies). Each bay's own-ply moment/shear is converted to an equivalent line load through the table's "
         "reference arrangement (1, 2 or 5 equal spans of the design bay) and compared with that bay section's "
         "table capacity (lower of outward and inward); deflection against L/150 and the table L/150 reference. "
         "End zones keep the profile (same depth, laps still work) and may be thicker.")

_SOLVES = {}
_DESIGNS = {}


def _cached(store, key, build, limit=512):
    if key not in store:
        if len(store) >= limit:
            store.pop(next(iter(store)))
        store[key] = build()
    return store[key]


def _solve(spans, inertia, kind, loads):
    payload = json.dumps({'spans_mm': list(spans), 'roof_i_mm4': inertia, 'added_i_mm4': min(inertia)
                          if isinstance(inertia, list) else inertia, 'span_type': kind, 'lap_ratio': LAP_RATIO,
                          'loads': loads}, sort_keys=True, allow_nan=False)
    return _cached(_SOLVES, payload, lambda: solve_run(**json.loads(payload)))


def roof_rows(points, edge_rows):
    """Physical purlin rows across the roof: plan x from the left eave, tributary
    width along the slope (half of each adjacent spacing) and zone. Edge rows are
    the first ``edge_rows`` from each eave, as the 2D selection and costing count them."""
    pts = sorted((float(x), float(y)) for x, y in points)
    if not pts:
        return [], 0.
    x0, n = pts[0][0], len(pts)
    rows = []
    width_mm = pts[-1][0]-x0
    for i, (x, y) in enumerate(pts):
        width = 0.
        if i > 0:
            width += math.hypot(x-pts[i-1][0], y-pts[i-1][1])/2
        if i < n-1:
            width += math.hypot(pts[i+1][0]-x, pts[i+1][1]-y)/2
        rows.append({'index': i, 'x_mm': x-x0, 'y_mm': y, 'tributary_width_mm': width,
                     'eave_distance_mm': min(x-x0, width_mm-(x-x0)),
                     'zone': 'edge' if min(i, n-1-i) < edge_rows else 'general'})
    return rows, width_mm


MAX_INFILLS_PER_CORNER = 6
INFILLS_PER_GAP = 1          # 1 halves the corner-zone spacing


def corner_infills(side_rows, count):
    """Infill purlins between the corner rows of one eave (rows ordered from the eave).

    Each infill splits the currently widest gap (ties: nearest the eave), so the
    corner rows' tributary widths even out. Returns (new widths by row index,
    infills as {x_mm, y_mm, tributary_width_mm}). Gaps are along the slope.
    """
    gaps = [{'i': i, 'length': math.hypot(b['x_mm']-a['x_mm'], b['y_mm']-a['y_mm']), 'pieces': 1}
            for i, (a, b) in enumerate(zip(side_rows, side_rows[1:]))]
    if not gaps:
        return None
    for _ in range(count):
        widest = max(gaps, key=lambda g: (g['length']/g['pieces'], -g['i']))
        widest['pieces'] += 1
    widths = {}
    for i, row in enumerate(side_rows):
        width = row['tributary_width_mm']
        for gap in (gaps[i-1] if i > 0 else None, gaps[i] if i < len(gaps) else None):
            if gap is not None and gap['pieces'] > 1:
                width += (gap['length']/gap['pieces']-gap['length'])/2
        widths[row['index']] = width
    infills = []
    for gap in gaps:
        a, b = side_rows[gap['i']], side_rows[gap['i']+1]
        for j in range(1, gap['pieces']):
            t = j/gap['pieces']
            infills.append({'x_mm': a['x_mm']+(b['x_mm']-a['x_mm'])*t, 'y_mm': a['y_mm']+(b['y_mm']-a['y_mm'])*t,
                            'tributary_width_mm': gap['length']/gap['pieces']})
    return widths, infills


def _reference(kind, span_mm, inertia):
    count = REFERENCE_SPANS[kind]
    run = _solve([span_mm]*count, inertia, kind, [{'name': 'unit', 'roof_n_mm': 1., 'added_n_mm': 0.}])
    bays = run['cases'][0]['bays']
    return {'moment_knm_per_kn_m': max(max(b['roof']['positive_moment_knm'], b['roof']['negative_moment_knm']) for b in bays),
            'shear_kn_per_kn_m': max(b['roof']['shear_kn'] for b in bays),
            'deflection_mm_per_kn_m': max(b['roof']['deflection_mm'] for b in bays),
            'reference_spans': count, 'reference_span_mm': span_mm}


def _row_loads(wind_inputs, geometry, row):
    cases = end_roof_local_cases(wind_inputs, geometry, {'tributary_width_mm': row['tributary_width_mm'],
                                                         'nearest_installed_x_mm': row['x_mm']})
    return [{'name': c['name'], 'roof_n_mm': 0., 'added_n_mm': 0., 'roof_patches': c['segments'],
             'region': c['local_pressure']['region'],
             'patch_reach_mm': 1000*(c['local_pressure']['patch_start_from_end_m']+c['local_pressure']['patch_length_m'])}
            for c in cases]


def _runs(spans, loads):
    """(bay offset, spans, loads) runs to analyse; a building longer than the beam
    model's limit is checked at each end on its first/last END_RUN_BAYS bays."""
    if len(spans) <= MAX_RUN_BAYS:
        return [(0, list(spans), loads)]
    total = sum(spans)
    front, back = list(spans[:END_RUN_BAYS]), list(spans[-END_RUN_BAYS:])
    front_len, back_start = sum(front), total-sum(back)
    def clip(load, lo, hi):
        patches = [[max(a, lo)-lo, min(b, hi)-lo, q] for a, b, q in load['roof_patches'] if min(b, hi) > max(a, lo)]
        return {**load, 'roof_patches': patches}
    return [(0, front, [clip(l, 0., front_len) for l in loads if '/ Front /' in l['name']]),
            (len(spans)-END_RUN_BAYS, back, [clip(l, back_start, total) for l in loads if '/ Back /' in l['name']])]


class _Sections:
    """Per-section stiffness, reference actions and table capacities (cached)."""
    def __init__(self, metroll_tables, span_type, design_span_mm):
        self.tables, self.span_type, self.span = metroll_tables, span_type, design_span_mm
        self.kind = KIND[span_type]
        self.shape = 'Z' if self.kind == 'continuous_lapped' else 'C'
        self._props, self._refs = {}, {}

    def inertia(self, section):
        if section not in self._props:
            self._props[section] = properties(section, self.shape)['web_ix_mm4']
        return self._props[section]

    def reference(self, section):
        if section not in self._refs:
            self._refs[section] = _reference(self.kind, self.span, self.inertia(section))
        return self._refs[section]

    def capacity(self, section, demand_kn_m):
        candidate = tables.section_candidate(self.tables, self.span_type, self.span, section,
                                             demand_kn_m, demand_kn_m, 0.)
        if candidate is None:
            raise AnalysisError(f'No table capacity for {section} at {self.span:.0f} mm')
        candidate.pop('_rows', None)
        return candidate


def _check(sections_by_bay, spans, loads, info):
    """Governing ULS and SLS ratios over every bay of one row's run."""
    worst = {'ratio': 0., 'uls_ratio': 0., 'sls_ratio': 0., 'governing': None, 'bridging_rows': {}}
    for offset, run_spans, run_loads in _runs(spans, loads):
        if not run_loads:
            continue
        run_sections = sections_by_bay[offset:offset+len(run_spans)]
        inertia = [info.inertia(s) for s in run_sections]
        solve_loads = [{k: l[k] for k in ('name', 'roof_n_mm', 'added_n_mm', 'roof_patches')} for l in run_loads]
        run = _solve(run_spans, inertia if len(set(inertia)) > 1 else inertia[0], info.kind, solve_loads)
        for b, section in enumerate(run_sections):
            ref = info.reference(section)
            demand, uls_case, deflection, sls_case = 0., None, 0., None
            for case, load in zip(run['cases'], run_loads):
                roof = case['bays'][b]['roof']
                if case['name'].startswith('ULS'):
                    w = max(max(roof['positive_moment_knm'], roof['negative_moment_knm'])/ref['moment_knm_per_kn_m'],
                            roof['shear_kn']/ref['shear_kn_per_kn_m'])
                    if w > demand:
                        demand, uls_case = w, load
                elif roof['deflection_mm'] > deflection:
                    deflection, sls_case = roof['deflection_mm'], load
            bay = offset+b
            if uls_case is not None:
                cap = info.capacity(section, demand)
                capacity = min(cap['outward_capacity_kn_m'], cap['inward_strength_capacity_kn_m'])
                ratio = demand/capacity if capacity > 1e-12 else float('inf')
                worst['bridging_rows'][bay] = cap['bridging_rows']
                if ratio > worst['uls_ratio']:
                    worst['uls_ratio'] = ratio
                if ratio > worst['ratio']:
                    worst.update(ratio=ratio, governing={'bay': bay, 'level': 'ULS', 'case': uls_case['name'],
                        'region': uls_case['region'], 'section': section, 'equivalent_line_load_kn_m': demand,
                        'capacity_kn_m': capacity})
            if sls_case is not None:
                service = info.capacity(section, 0.)['inward_service_capacity_kn_m']
                rated = ref['deflection_mm_per_kn_m']*service
                ratio = max(deflection/(info.span/150.), deflection/rated if rated > 1e-12 else float('inf'))
                worst['sls_ratio'] = max(worst['sls_ratio'], ratio)
                if ratio > worst['ratio']:
                    worst.update(ratio=ratio, governing={'bay': bay, 'level': 'SLS', 'case': sls_case['name'],
                        'region': sls_case['region'], 'section': section, 'deflection_mm': deflection,
                        'limit_mm': min(info.span/150., rated)})
    return worst


def _zone(rows, spans, info, profile_sections):
    """Lightest end zone passing every row: 0, 1 or 2 bays at each end, each row
    stepped up the same number of gauges from its own 2D section (same profile),
    and keeping its own section in the other bays."""
    unique = {}
    for row in rows:
        if row['loads']:
            key = json.dumps([row['base_section'], row['loads']], sort_keys=True)
            unique.setdefault(key, {'loads': row['loads'], 'base': row['base_section'], 'rows': []})['rows'].append(row)
    bases = sorted({row['base_section'] for row in rows}, key=tables.section_area_mm2)
    counts = {}
    for row in rows:
        counts[row['zone']] = counts.get(row['zone'], 0)+1
    record = {'base_sections': bases, 'end_sections': {b: b for b in bases}, 'gauge_steps': 0, 'end_bays': 0,
              'end_length_mm': 0., 'rows': len(rows), 'rows_by_2d_zone': counts, 'extra_kg': 0., 'status': 'OK',
              'ratio': 0., 'base_ratio': 0., 'governing': None}
    if not unique:
        record['note'] = 'No gable-end local pressure patches on these rows (regions switched off).'
        return record
    n = len(spans)
    ladder = sorted(profile_sections, key=tables.section_area_mm2)
    def stepped(section, steps):
        index = ladder.index(section) if section in ladder else len(ladder)-1
        return ladder[min(index+steps, len(ladder)-1)]
    max_steps = max(len(ladder)-1-(ladder.index(b) if b in ladder else len(ladder)-1) for b in bases)
    options = [(0, 0, 0., 0.)]
    for k in range(1, MAX_END_BAYS+1):
        bays = sorted(set(range(min(k, n))) | set(range(max(n-k, 0), n)))
        length = sum(spans[b] for b in bays)
        for steps in range(1, max_steps+1):
            extra = sum(length*(tables.section_area_mm2(stepped(r['base_section'], steps))
                                -tables.section_area_mm2(r['base_section']))*STEEL_KG_PER_MM3 for r in rows)
            options.append((k, steps, length, extra))
        if len(bays) == n:
            break
    options.sort(key=lambda o: (o[3], o[0]))
    best_fail = None
    # Rows nearest an eave (corner patches) usually govern: check them first so a
    # failing option stops early.
    groups = sorted(unique.values(), key=lambda g: min(r['eave_distance_mm'] for r in g['rows']))
    tried = []
    record['options_tried'] = tried
    for k, steps, length, extra in options:
        governing, failed = {'ratio': 0.}, False
        bridging = {}
        for group in groups:
            end = stepped(group['base'], steps)
            sections = [end if (b < k or b >= n-k) else group['base'] for b in range(n)]
            result = _check(sections, spans, group['loads'], info)
            for bay, value in result['bridging_rows'].items():
                bridging[bay] = max(bridging.get(bay, 0), value)
            if result['ratio'] > governing['ratio']:
                governing = {**result, 'row_x_mm': group['rows'][0]['x_mm'],
                             'row_tributary_width_mm': group['rows'][0]['tributary_width_mm']}
            if result['ratio'] > 1+1e-9:
                failed = True
                if k:   # the plain check (k = 0) runs every group, for a true base ratio
                    break
        if k == 0:
            record['base_ratio'] = governing['ratio']
        summary = {'ratio': governing['ratio'], 'governing': {**(governing.get('governing') or {}),
                   'row_x_mm': governing.get('row_x_mm'), 'row_tributary_width_mm': governing.get('row_tributary_width_mm')}}
        chosen = {'end_sections': {b: stepped(b, steps) if k else b for b in bases}, 'gauge_steps': steps,
                  'end_bays': k, 'end_length_mm': length, 'extra_kg': extra}
        tried.append({'end_bays': k, 'gauge_steps': steps, 'extra_kg': extra, 'ratio': governing['ratio'],
                      'bay': (governing.get('governing') or {}).get('bay'), 'passes': not failed,
                      'case': (governing.get('governing') or {}).get('case'), 'row_x_mm': governing.get('row_x_mm')})
        if not failed:
            end_bridging = max((v for b, v in bridging.items() if b < k or b >= n-k), default=None)
            record.update(**chosen, end_bridging_rows=end_bridging, **summary)
            return record
        if best_fail is None or governing['ratio'] < best_fail[0]['ratio']:
            best_fail = (summary, chosen)
    summary, chosen = best_fail
    record.update(status='NOT OK', **chosen, **summary,
                  message='No end-zone gauge of this profile carries the gable-end local pressure.')
    return record


def _scaled_outside(loads, factor, ranges):
    """Loads with every segment outside ``ranges`` multiplied by ``factor`` (split at the range ends)."""
    cuts = sorted({x for lo, hi in ranges for x in (lo, hi)})
    out = []
    for load in loads:
        patches = []
        for a, b, q in load['roof_patches']:
            points = [a, *[c for c in cuts if a < c < b], b]
            for lo, hi in zip(points, points[1:]):
                mid = (lo+hi)/2
                inside = any(r0 <= mid <= r1 for r0, r1 in ranges)
                patches.append([lo, hi, q if inside else q*factor])
        out.append({**load, 'roof_patches': patches})
    return out


def _clipped(loads, lo, hi):
    """Loads clipped to [lo, hi] and shifted to start at 0 (cases with nothing there dropped)."""
    out = []
    for load in loads:
        patches = [[max(a, lo)-lo, min(b, hi)-lo, q] for a, b, q in load['roof_patches'] if min(b, hi) > max(a, lo)]
        if patches:
            out.append({**load, 'roof_patches': patches})
    return out


def _infill_design(infills, wind_inputs, geometry, stations, end_bays, metroll_tables, profile_sections, uniform):
    """Lightest section of the profile for the single-span infills in the corner end bays:
    the uniform edge-row line loads for their own width, and their share of the corner patches."""
    checks = []   # (bay length, width, loads)
    for infill in infills:
        loads = _row_loads(wind_inputs, geometry, infill)
        for bay in end_bays:
            lo, hi = stations[bay], stations[bay+1]
            checks.append((hi-lo, infill['tributary_width_mm'], _clipped(loads, lo, hi)))
    infos = {}
    worst = None
    for section in sorted(profile_sections, key=tables.section_area_mm2):
        ratio, governing = 0., None
        for length, width, loads in checks:
            factor = width/uniform['spacing_mm']
            demand = (uniform['outward_kn_m']*factor, uniform['inward_strength_kn_m']*factor,
                      uniform['inward_service_kn_m']*factor)
            cap = tables.section_candidate(metroll_tables, 'Single span', length, section, *demand)
            if cap is None:
                ratio = float('inf')
                break
            uniform_ratio = max(cap['outward_ratio'], cap['inward_strength_ratio'], cap['inward_service_ratio'])
            if uniform_ratio > ratio:
                ratio, governing = uniform_ratio, {'level': 'ULS/SLS uniform', 'span_mm': length}
            if loads:
                info = infos.setdefault(length, _Sections(metroll_tables, 'Single span', length))
                result = _check([section], [length], loads, info)
                if result['ratio'] > ratio:
                    ratio, governing = result['ratio'], {**result['governing'], 'span_mm': length}
        record = {'section': section, 'ratio': ratio, 'governing': governing}
        if ratio <= 1+1e-9:
            return {**record, 'status': 'OK'}
        if worst is None or ratio < worst['ratio']:
            worst = record
    return {**(worst or {'section': None, 'ratio': float('inf'), 'governing': None}), 'status': 'NOT OK'}


def _corner(corner_rows, bays, info, profile_sections, wind_inputs, geometry, metroll_tables, uniform, all_rows=()):
    """Corner rows: a thicker end zone alone, or with single-span infill purlins between
    the RC1 rows of each eave in the end bays that cover the corner patch; lightest passing."""
    plain = _zone(corner_rows, bays, info, profile_sections)
    best = plain if plain['status'] == 'OK' else None
    eave_rows = [r for r in corner_rows if any(l['region'] == 'RC1' for l in r['loads'])]
    if not eave_rows or uniform is None:
        return plain
    stations = [0.]
    for bay in bays:
        stations.append(stations[-1]+bay)
    n, length = len(bays), stations[-1]
    width_mm = geometry.span_mm
    # Each eave's RC1 rows from the eave inward, plus the next row inward, so the
    # last corner row's inner gap can take an infill too.
    sides = []
    for left in (True, False):
        side = sorted([r for r in eave_rows if (r['x_mm'] <= width_mm/2) == left], key=lambda r: r['eave_distance_mm'])
        if side:
            beyond = sorted([r for r in all_rows if (r['x_mm'] <= width_mm/2) == left
                             and r['eave_distance_mm'] > side[-1]['eave_distance_mm']], key=lambda r: r['eave_distance_mm'])
            sides.append(side+beyond[:1])
    reach = max(l['patch_reach_mm'] for r in corner_rows for l in r['loads'])
    tried = []
    for k in range(1, min(MAX_END_BAYS, n//2)+1):
        if stations[k] < reach-1e-6 or length-stations[n-k] < reach-1e-6:
            continue   # the infills must cover every end patch
        end_bays = [*range(k), *range(n-k, n)]
        ranges = [(0., stations[k]), (stations[n-k], length)]
        end_length = stations[k]+length-stations[n-k]
        gaps = max((len(side)-1 for side in sides), default=0)
        for count in range(1, min(MAX_INFILLS_PER_CORNER, INFILLS_PER_GAP*gaps)+1):
            layouts = [corner_infills(side, count) for side in sides if len(side) > 1]
            if not layouts:
                continue
            widths = {i: w for layout, _ in layouts for i, w in layout.items()}
            infills = [f for _, side_infills in layouts for f in side_infills]
            thinnest = min(map(tables.section_area_mm2, profile_sections))
            infill_length = len(infills)*end_length
            if best is not None and infill_length*thinnest*STEEL_KG_PER_MM3 >= best['extra_kg']:
                continue
            rows = []
            for row in corner_rows:
                width = widths.get(row['index'], row['tributary_width_mm'])
                if abs(width-row['tributary_width_mm']) < 1e-6:
                    rows.append(row)
                    continue
                narrow = _row_loads(wind_inputs, geometry, {**row, 'tributary_width_mm': width})
                if max((l['patch_reach_mm'] for l in narrow), default=0.) > min(stations[k], length-stations[n-k])+1e-6:
                    rows = None
                    break
                rows.append({**row, 'loads': _scaled_outside(narrow, row['tributary_width_mm']/width, ranges),
                             'end_zone_tributary_width_mm': width})
            if rows is None:
                continue
            zone = _zone(rows, bays, info, profile_sections)
            infill = _infill_design(infills, wind_inputs, geometry, stations, end_bays, metroll_tables,
                                    profile_sections, uniform)
            infill_kg = infill_length*tables.section_area_mm2(infill['section'])*STEEL_KG_PER_MM3 \
                if infill['section'] else float('inf')
            record = {**zone, 'extra_kg': zone['extra_kg']+infill_kg,
                      'status': 'OK' if zone['status'] == 'OK' and infill['status'] == 'OK' else 'NOT OK',
                      'infill': {**infill, 'per_corner': count, 'bays': k, 'count': len(infills)*2,
                                 'length_each_mm': [stations[b+1]-stations[b] for b in end_bays],
                                 'total_length_mm': infill_length, 'kg': infill_kg,
                                 'positions': [{'x_mm': f['x_mm'], 'tributary_width_mm': f['tributary_width_mm']}
                                               for f in infills],
                                 'corner_row_widths_mm': {str(i): w for i, w in widths.items()}}}
            tried.append({'bays': k, 'per_corner': count, 'status': record['status'], 'extra_kg': record['extra_kg'],
                          'corner_rows_ratio': zone['ratio'], 'infill_ratio': infill['ratio'],
                          'infill_section': infill['section']})
            if record['status'] == 'OK' and (best is None or record['extra_kg'] < best['extra_kg']):
                best = record
    chosen = best or plain
    return {**chosen, 'infill_options_tried': tried}


def end_zone_design(metroll_tables, wind_inputs, points, bays_mm, span_type, edge_rows, general_section,
                    edge_section, design_span_mm, uniform=None):
    """End zones for one common profile: field rows (RA patches) and corner rows (RC
    patches, with corner infills if needed). ``uniform`` is the edge-row line loads at
    ``spacing_mm`` (outward, inward strength, inward service), for the infills.
    Cached on its inputs."""
    key = json.dumps({'tables': id(metroll_tables), 'wind': asdict(wind_inputs),
                      'points': [[round(x, 3), round(y, 3)] for x, y in points], 'bays': [round(b, 3) for b in bays_mm],
                      'span_type': span_type, 'edge_rows': edge_rows, 'general': general_section,
                      'edge': edge_section, 'design_span_mm': round(design_span_mm, 3), 'uniform': uniform},
                     sort_keys=True, default=str)
    return copy.deepcopy(_cached(_DESIGNS, key, lambda: _design(
        metroll_tables, wind_inputs, points, list(bays_mm), span_type, edge_rows, general_section, edge_section,
        design_span_mm, uniform), limit=64))


def _design(metroll_tables, wind_inputs, points, bays_mm, span_type, edge_rows, general_section, edge_section,
            design_span_mm, uniform=None):
    rows, plan_width = roof_rows(points, edge_rows)
    stations = [0.]
    for bay in bays_mm:
        stations.append(stations[-1]+bay)
    geometry = SimpleNamespace(span_mm=plan_width, length_mm=stations[-1], pitch_deg=wind_inputs.roof_pitch_deg,
                               roof_shape=wind_inputs.roof_shape, bay_stations_mm=stations)
    info = _Sections(metroll_tables, span_type, design_span_mm)
    for row in rows:
        row['loads'] = _row_loads(wind_inputs, geometry, row)
    profile = profile_key(general_section)
    table = metroll_tables['tables'].get(tables.TABLE_IDS[span_type][0], {})
    profile_sections = [p['section'] for p in table.get('profiles', []) if profile_key(p['section']) == profile]
    # End-zone groups follow the patches, not the 2D row count: rows whose strip
    # reaches a roof corner (RC1/RC2 patches) and the rest of the field.
    for row in rows:
        row['base_section'] = edge_section if row['zone'] == 'edge' else general_section
        row['group'] = 'corner' if any(l['region'].startswith('RC') for l in row['loads']) else 'field'
    zones = {}
    field = [r for r in rows if r['group'] == 'field']
    corner = [r for r in rows if r['group'] == 'corner']
    if field:
        zones['field'] = _zone(field, bays_mm, info, profile_sections)
    if corner:
        zones['corner'] = _corner(corner, bays_mm, info, profile_sections, wind_inputs, geometry, metroll_tables,
                                  uniform, rows)
    return {'status': 'OK' if all(z['status'] == 'OK' for z in zones.values()) else 'NOT OK',
            'profile': profile, 'zones': zones, 'extra_kg': sum(z['extra_kg'] for z in zones.values()),
            'bays_mm': bays_mm, 'design_span_mm': design_span_mm,
            'regions': sorted({l['region'] for r in rows for l in r['loads']}),
            'reference': info.reference(general_section), 'basis': BASIS}
