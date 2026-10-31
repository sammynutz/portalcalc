"""Manufacturer UDL table access for independent single-span strut checks.

This module never converts tabulated line-load capacities into general moment
or shear capacities. The caller must keep the manufacturer's loading/restraint
configuration and explicitly supply the design line loads.
"""
import hashlib
import json
import math
import re

from bracing_analysis import AnalysisError


def table_row(table, section, span_mm):
    """Interpolate common bridging columns only, within the published range."""
    for profile in table.get('profiles', []):
        if profile.get('section') != section:
            continue
        rows = profile.get('rows', [])
        if not rows or span_mm < rows[0]['span_mm'] or span_mm > rows[-1]['span_mm']:
            return None
        for row in rows:
            if abs(span_mm-row['span_mm']) < 1e-7:
                return {**row, 'interpolated': False}
        for lower, upper in zip(rows, rows[1:]):
            if lower['span_mm'] < span_mm < upper['span_mm']:
                t = (span_mm-lower['span_mm'])/(upper['span_mm']-lower['span_mm'])
                values = {key: lower['strength_by_bridging_kn_m'][key]*(1-t)+upper['strength_by_bridging_kn_m'][key]*t
                          for key in lower['strength_by_bridging_kn_m'].keys() & upper['strength_by_bridging_kn_m'].keys()}
                result = {'span_mm': span_mm, 'interpolated': True, 'strength_by_bridging_kn_m': values,
                          'interpolation_spans_mm': [lower['span_mm'], upper['span_mm']]}
                if all('service_l150_capacity_kn_m' in r for r in (lower, upper)):
                    result['service_l150_capacity_kn_m'] = lower['service_l150_capacity_kn_m']*(1-t)+upper['service_l150_capacity_kn_m']*t
                return result
    return None


def snapshot(tables):
    """Freeze the actual cache entries, including identity, in the design input."""
    selected = {'source': tables.get('source', 'Metroll MEGASPAN capacity cache'),
                'tables': {key: tables.get('tables', {}).get(key, {}) for key in ('1A', '1B', '2A', '2B', '5A', '5B')}}
    selected['sha256'] = hashlib.sha256(json.dumps(selected, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return selected


def span_capacities(bundle, section, span_mm, bridging_rows, span_type='Single span'):
    if not math.isfinite(span_mm) or span_mm <= 0:
        raise AnalysisError('Positive added-purlin span is required')
    if isinstance(bridging_rows, bool) or not isinstance(bridging_rows, int) or bridging_rows < 0:
        raise AnalysisError('Added-purlin bridging rows must be a non-negative integer')
    match = re.fullmatch(r'(?:C/Z|C|Z)\s*(\d+)\s+(\d+)', section)
    if not match:
        raise AnalysisError('Unknown added-purlin section designation')
    canonical = f'C/Z {int(match[1])} {int(match[2])}'
    max_spacing = 3000. if int(match[1]) <= 150 else 4000.
    minimum = max(0, math.ceil(span_mm/max_spacing)-1)
    if bridging_rows < minimum:
        raise AnalysisError(f'Added purlin requires at least {minimum} bridging rows at this span')
    ids = {'Single span': ('1A', '1B'), 'Double span': ('2A', '2B'), 'Continuous lapped': ('5A', '5B')}.get(span_type)
    if ids is None:
        raise AnalysisError('Unsupported reference span arrangement')
    caps, evidence = {}, {}
    for key, direction in zip(ids, ('outward', 'inward')):
        table = bundle.get('tables', {}).get(key, {})
        row = table_row(table, canonical, span_mm)
        if row is None:
            raise AnalysisError(f'No single-span {key} data for {canonical} at {span_mm:g} mm')
        options = [(int(m[1]), label, value) for label, value in row.get('strength_by_bridging_kn_m', {}).items()
                   if (m := re.match(r'B(\d+)', label)) and int(m[1]) <= bridging_rows and '/SB' not in label]
        if not options:
            raise AnalysisError(f'No {key} capacity for the declared added-purlin bridging')
        # Use an actually tabulated restraint level no greater than installed.
        _, label, value = max(options, key=lambda item:(item[0], -item[2]))
        if not math.isfinite(value) or value <= 0:
            raise AnalysisError('Invalid added-purlin table capacity')
        caps[direction+'_capacity_kn_m'] = value
        evidence[key] = {'page': table.get('page'), 'bridging': label, 'row': row}
        if direction == 'inward':
            service = float(row.get('service_l150_capacity_kn_m') or 0.)
            if not math.isfinite(service) or service <= 0:
                raise AnalysisError('Added-purlin L/150 table capacity is missing')
            caps['service_capacity_kn_m'] = service
    return {**caps, 'span_mm': span_mm, 'section': canonical, 'tables': ' / '.join(ids),
            'span_type': span_type, 'bridging_rows': bridging_rows,
            'minimum_bridging_rows': minimum, 'max_bridging_spacing_mm': max_spacing,
            'source': bundle.get('source'), 'source_sha256': bundle.get('sha256'),
            'table_evidence': evidence,
            'restraint_basis': 'Flange screw-fixed to cladding providing the manufacturer minimum rotational restraint, with the stated bridging installed. UDL only; no redistributed beam-diagram capacity inferred.'}


def single_span_capacities(bundle, section, span_mm, bridging_rows):
    return span_capacities(bundle, section, span_mm, bridging_rows)
