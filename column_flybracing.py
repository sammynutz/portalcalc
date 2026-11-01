"""Automatic girt flybracing of clad columns, using already-solved ULS cases.

No structural solve or connection-stiffness model lives here. Girt flybraces
restrain weak-axis translation/twist; they never create an in-plane support.
The receiving girt and effective lateral/twist restraint remain design assumptions.
"""
from dataclasses import asdict
import math

from member_checks import MemberActions, MemberCheckResult, check_member


COLUMN_GROUPS = {'LEFT_COLUMN', 'RIGHT_COLUMN', 'LEFT_LEAN_COLUMN', 'RIGHT_LEAN_COLUMN'}


def girt_levels(base, top, maximum_spacing, bottom_offset=300.0):
    """Same equally-spaced wall layout used by the girt capacity check."""
    height = top - base
    if height <= bottom_offset:
        return []
    spaces = max(1, math.ceil((height-bottom_offset) / maximum_spacing))
    spacing = (height-bottom_offset) / spaces
    return [base + bottom_offset + i*spacing for i in range(spaces)]


def capture_case(structure, displacement, groups, name):
    """Exact N/V/M polynomials in local t, including linear member loads."""
    result = {group: {'name': name, 'pieces': []} for group in groups}
    for e in structure.elements:
        if e.group not in groups:
            continue
        load = structure.uniform_load_for_element(e)
        qx1, qy1, qx2, qy2 = e.load_components(load)
        f = e.local_end_forces(displacement, load)
        length = e.length()
        result[e.group]['pieces'].append({
            'member': e.id, 'y0': e.start.y, 'y1': e.end.y,
            'n': [-float(f[0]), -qx1*length, -(qx2-qx1)*length/2],
            'v': [float(f[1]), qy1*length, (qy2-qy1)*length/2],
            'm': [-float(f[2]), float(f[1])*length, qy1*length**2/2,
                  (qy2-qy1)*length**2/6],
        })
    return result


def value(coefficients, t):
    answer = 0.0
    for c in reversed(coefficients):
        answer = answer*t + c
    return answer


def extrema(coefficients, low, high):
    """Analytic interior extrema; no station grid or numerical polynomial fit."""
    c = list(coefficients) + [0.0] * (4-len(coefficients))
    a, b, d = 3*c[3], 2*c[2], c[1]
    stations = [low, high]
    if abs(a) > 1e-12:
        discriminant = b*b - 4*a*d
        if discriminant >= 0:
            root = math.sqrt(discriminant)
            # Stable quadratic roots, including a root close to zero.
            q = -.5*(b + math.copysign(root, b))
            roots = [q/a, d/q] if abs(q) > 1e-30 else [-b/(2*a)]
            stations.extend(t for t in roots if low < t < high)
    elif abs(b) > 1e-12:
        root = -d/b
        if low < root < high:
            stations.append(root)
    values = [value(c, t) for t in stations]
    return min(values), max(values)


def segment_demand(case, low, high):
    n_low = n_high = v_abs = m_abs = 0.0
    pieces = []
    covered = 0.0
    for piece in case['pieces']:
        y0, y1 = piece['y0'], piece['y1']
        a, b = max(low, min(y0, y1)), min(high, max(y0, y1))
        if b-a < 1e-7:
            continue
        covered += b-a
        ta, tb = sorted(((a-y0)/(y1-y0), (b-y0)/(y1-y0)))
        nl, nh = extrema(piece['n'], ta, tb)
        vl, vh = extrema(piece['v'], ta, tb)
        ml, mh = extrema(piece['m'], ta, tb)
        n_low, n_high = min(n_low, nl), max(n_high, nh)
        v_abs, m_abs = max(v_abs, abs(vl), abs(vh)), max(m_abs, abs(ml), abs(mh))
        pieces.append(piece)
    if abs(covered-(high-low)) > 1e-4:
        raise ValueError('Column segment is not covered exactly once by the solved members')
    quarters = []
    for fraction in (.25, .5, .75):
        y = low + fraction*(high-low)
        moments = [value(p['m'], (y-p['y0'])/(p['y1']-p['y0'])) for p in pieces
                   if min(p['y0'], p['y1'])-1e-7 <= y <= max(p['y0'], p['y1'])+1e-7]
        quarters.append(max(moments, key=abs))
    denominator = math.sqrt(sum(m*m for m in quarters))
    # AS 4100 5.6.1.1: this expression can be slightly below 1.0 for
    # constant moments. Do not silently clamp it to 1.0.
    am = min(2.5, 1.7*m_abs/denominator) if denominator > 1e-9 else 1.0
    return {'compression_kn': -n_low/1000, 'tension_kn': n_high/1000,
            'shear_kn': v_abs/1000, 'moment_knm': m_abs/1e6,
            'moment_modifier': am, 'quarter_moments_knm': [m/1e6 for m in quarters]}


class FlybraceColumn:
    """A per-solve cache of interval demands and section/layout checks."""
    def __init__(self, basis):
        self.basis = basis
        self.base, self.top = basis['base_mm'], basis['top_mm']
        self.fixed = sorted(set([self.base, self.top] + basis['fixed_levels_mm']))
        self.candidates = sorted(set(y for y in basis['girt_levels_mm']
                                     if self.base+1e-6 < y < basis['roof_node_mm']-1e-6
                                     and all(abs(y-f) > 1e-6 for f in self.fixed)))
        self.demands = {}
        self.intervals = {}
        self.plans = {}

    def interval(self, section, low, high):
        key = (tuple(asdict(section).values()), low, high)
        if key in self.intervals:
            return self.intervals[key]
        demand_key = (low, high)
        if demand_key not in self.demands:
            self.demands[demand_key] = [(case['name'], segment_demand(case, low, high))
                                         for case in self.basis['cases']]
        checks = []
        for name, demand in self.demands[demand_key]:
            actions = MemberActions(**{k: v for k, v in demand.items() if k != 'quarter_moments_knm'},
                                    effective_length_y_mm=high-low,
                                    effective_length_z_mm=self.basis['in_plane_length_mm'],
                                    ltb_length_mm=high-low)
            result = check_member(section, actions)
            checks.append((result, actions, {'combo': name, 'from_mm': low, 'to_mm': high,
                                            'am': actions.moment_modifier,
                                            'quarter_moments_knm': demand['quarter_moments_knm']}))
        worst = max(checks, key=lambda row: row[0].governing_ratio)
        self.intervals[key] = (checks, worst)
        return checks, worst

    def layout(self, section, positions):
        cuts = sorted(set(self.fixed + list(positions)))
        intervals = [self.interval(section, a, b) for a, b in zip(cuts[:-1], cuts[1:])]
        worst = max((row[1] for row in intervals), key=lambda row: row[0].governing_ratio)
        return intervals, worst

    def choose(self, section):
        section_key = tuple(asdict(section).values())
        if section_key in self.plans:
            return self.plans[section_key]
        # Upper-quarter prediction, followed by its two neighbouring girts.
        prediction = self.base + .75*(self.basis['roof_node_mm']-self.base)
        positions = []
        if self.candidates:
            nearest = min(range(len(self.candidates)), key=lambda i: abs(self.candidates[i]-prediction))
            seeds = self.candidates[max(0, nearest-1):nearest+2]
            positions = [min(seeds, key=lambda y: (self.layout(section, [y])[1][0].governing_ratio,
                                                  abs(y-prediction)))]
        intervals, worst = self.layout(section, positions)
        # At most one addition per available girt: bounded, no subset search.
        # A section/in-plane floor prevents futile extra braces when those govern.
        while worst[0].governing_ratio > 1.0+1e-9:
            floor = check_member(section, MemberActions(
                compression_kn=worst[1].compression_kn, tension_kn=worst[1].tension_kn,
                moment_knm=worst[1].moment_knm, shear_kn=worst[1].shear_kn,
                effective_length_y_mm=1.0, ltb_length_mm=1.0,
                effective_length_z_mm=self.basis['in_plane_length_mm']))
            if floor.governing_ratio > 1.0+1e-9:
                break
            low, high = worst[2]['from_mm'], worst[2]['to_mm']
            options = [y for y in self.candidates if low+1e-6 < y < high-1e-6 and y not in positions]
            if not options:
                break
            # Reuse cached unaffected intervals; evaluate each possible split only.
            split = min(options, key=lambda y: (
                max(self.interval(section, low, y)[1][0].governing_ratio,
                    self.interval(section, y, high)[1][0].governing_ratio), abs(y-(low+high)/2)))
            positions.append(split)
            intervals, worst = self.layout(section, positions)
        aggregate = MemberCheckResult(section_name=section.name)
        sources = {}
        for checks, _ in intervals:
            for result, actions, source in checks:
                for key, check in result.checks.items():
                    if key not in aggregate.checks or check.ratio > aggregate.checks[key].ratio:
                        aggregate.checks[key] = check
                        sources[key] = {**source, 'actions': asdict(actions),
                                        'basis': 'Same load case and physical restraint segment'}
        plan = {
            'positions_mm': sorted(positions),
            'heights_above_base_mm': sorted(y-self.base for y in positions),
            'below_roof_node_mm': sorted(self.basis['roof_node_mm']-y for y in positions),
            'braces': [{'elevation_mm': y, 'height_above_base_mm': y-self.base,
                        'below_roof_node_mm': self.basis['roof_node_mm']-y,
                        'restraint_requirement': 'Weak-axis lateral and twist restraint for both moment signs'}
                       for y in sorted(positions)],
            'in_plane_length_mm': self.basis['in_plane_length_mm'],
            'governing_ratio': aggregate.governing_ratio,
            'passes': aggregate.governing_ratio <= 1.0+1e-9,
            'sources': sources,
            'segments': [{'from_mm': row[1][2]['from_mm'], 'to_mm': row[1][2]['to_mm'],
                          'ratio': row[1][0].governing_ratio, 'combo': row[1][2]['combo'],
                          'actions': asdict(row[1][1])} for row in intervals],
            'restraint': 'Effective weak-axis lateral and twist restraint at scheduled girt flybraces; no in-plane support added.',
            'selection': 'Upper-quarter seed, adjacent girts, then cached splits of governing segments.',
        }
        self.plans[section_key] = aggregate, worst[1], plan
        return self.plans[section_key]
