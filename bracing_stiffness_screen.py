"""Response-guided catalogue screening; never a design acceptance check.

Portal geometry/sections and layout stay fixed. Captured simultaneous SLS loads
are reused; tension-only equilibrium is resolved for each shortlisted trial.
Only brace EA is varied in this first implementation.
"""
from dataclasses import dataclass, replace
from time import perf_counter

import numpy as np

from bracing_analysis import AnalysisError, _elastic_factor, _solve_factor


BRACE_GROUPS = ('roof_brace', 'wall_brace', 'end_brace')


@dataclass(frozen=True)
class MovementResponse:
    """Signed linear response: supports absolute and relative movements."""
    name: str
    terms: tuple  # (global translational DOF, coefficient)
    limit_mm: float

    def ratio(self, displacement):
        if not np.isfinite(self.limit_mm) or self.limit_mm <= 0:
            raise AnalysisError('Screen response limit must be finite and positive')
        return abs(sum(displacement[dof]*weight for dof, weight in self.terms))/self.limit_mm


class LightweightSlsScreen:
    """One physical assembly, captured loads, local derivatives, small shortlist.

    No portal recovery, load generation, ULS/member checks, costing or reporting
    is performed during trial solves. Active sets are NOT frozen for trial solves.
    """
    def __init__(self, model, cases, responses):
        self.model = model
        self.members = tuple(model.members)
        self.responses = tuple(responses)
        if not self.responses or not cases:
            raise AnalysisError('Stiffness screening needs SLS cases and movement responses')
        self.cases = tuple(cases)
        self.derivatives = []
        self.sls_solves = 0
        free, linear, rows, ks, rods, _, _, _, _, _, _ = model._prepare()
        self.free = free
        rod_mask = np.zeros(len(self.members), dtype=bool)
        rod_mask[rods] = True
        tangent_factors = {}
        for case in self.cases:
            if case.get('tangent_status') != 'positive_with_taut_rods':
                raise AnalysisError('Screening needs a verified taut-rod tangent')
            u = np.asarray(case['displacements_mm'], dtype=float).reshape(-1)
            force = np.asarray(case['nodal_loads_n'], dtype=float).reshape(-1)
            if (u.size != 3*len(model.nodes) or force.size != u.size
                    or not np.all(np.isfinite(u)) or not np.all(np.isfinite(force))):
                raise AnalysisError('Captured screen case does not match the physical model')
            active = ~rod_mask | (rows @ u > 1e-10)
            taut = rod_mask & active
            r = rows[:, free]
            tangent = linear[np.ix_(free, free)] + r[taut].T @ (ks[taut, None]*r[taut])
            residual = tangent @ u[free]-force[free]
            if np.max(np.abs(residual), initial=0) > max(1e-5, np.max(np.abs(force), initial=0)*1e-7):
                raise AnalysisError('Captured screen case failed baseline equilibrium')
            rhs = []
            for group in BRACE_GROUPS:
                indices = [i for i, m in enumerate(self.members) if m.kind == group and active[i]]
                rhs.append(-(r[indices].T @ (ks[indices]*(r[indices] @ u[free]))))
            key = taut.tobytes()
            if key not in tangent_factors:
                tangent_factors[key] = _elastic_factor(tangent)
            derivatives = _solve_factor(tangent_factors[key], np.column_stack(rhs))
            self.derivatives.append((u, derivatives))

    def ratio(self, displacement):
        return max(response.ratio(displacement) for response in self.responses)

    def predict(self, group, ea_ratio):
        """Local derivative wrt EA multiplier; large steps are ranking only."""
        index = BRACE_GROUPS.index(group)
        ratios = []
        for u, derivatives in self.derivatives:
            trial = u.copy()
            trial[self.free] += (ea_ratio-1)*derivatives[:, index]
            ratios.append(self.ratio(trial))
        return max(ratios)

    def evaluate(self, group, ea_ratio):
        self.model.members = [replace(m, area_mm2=m.area_mm2*ea_ratio)
                              if m.kind == group else m for m in self.members]
        ratios = []
        try:
            for case in self.cases:
                self.sls_solves += 1
                solved = self.model.solve(np.asarray(case['nodal_loads_n']).reshape(-1),
                                          allow_neutral_equilibrium=case['case'].get('gravity_only', False))
                if solved['tangent_status'] != 'positive_with_taut_rods':
                    raise AnalysisError('Trial has an unverified taut-rod tangent')
                ratios.append(self.ratio(np.asarray(solved['displacements_mm']).reshape(-1)))
            return max(ratios)
        finally:
            self.model.members = list(self.members)


def sway_proposal(context, result, chosen, candidates, criteria, rates, tried):
    """Return one proposed brace upgrade plus diagnostic evidence.

    Material rules and current member-demand screens precede response ranking.
    Failed predictions never reject a layout or establish infeasibility.
    """
    from bracing_analysis_desktop import prepare_analysis_models
    from bracing_design import (candidate_name, candidate_stiffness, group_members,
                                group_assessments, group_cost, global_sway_basis)
    from bracing_policy import eligible, candidate_rank

    started = perf_counter()
    evidence = {'method': 'SLS response derivatives and tension-only shortlist',
                'scope': 'Fixed portal sections/layout; brace EA upgrades only; full checks required',
                'catalogue_predictions': 0, 'lightweight_trials': [], 'sls_solves': 0}
    options = []
    for group in BRACE_GROUPS:
        if group in tried or not group_members(result, group):
            continue
        old_ea = candidate_stiffness(chosen[group])
        old_cost = group_cost(result, group, chosen[group], rates)['total']
        for option in candidates[group]:
            ea = candidate_stiffness(option)
            if old_ea <= 0 or ea <= old_ea+1e-9 or not eligible(context, group, option):
                continue
            try:
                cost = group_cost(result, group, option, rates)['total']
            except AnalysisError:
                continue
            if not all(row['passes'] for row in group_assessments(result, group, option, criteria)):
                continue
            options.append({'group': group, 'option': option, 'ea_ratio': ea/old_ea,
                            'cost_change': cost-old_cost,
                            'preference_change': candidate_rank(context, group, option)-candidate_rank(context, group, chosen[group])})
    if not options:
        evidence['fallback'] = 'No eligible untried upgrade'
        return None, evidence

    local = {**context, **{g: chosen[g] for g in BRACE_GROUPS}}
    _, model, _, _, _, _ = prepare_analysis_models(local, chosen['strut'], chosen['end_post'])
    divisor = global_sway_basis(result, criteria, context)['divisor']
    responses = [MovementResponse(f'Node {i+1} longitudinal sway', ((3*i+1, 1.),), p[2]/divisor)
                 for i, p in enumerate(model.nodes) if p[2] > 0]
    cases = [case for case in result['cases'] if case['case']['level'] == 'SLS']
    screen = LightweightSlsScreen(model, cases, responses)
    baseline = max(screen.ratio(u) for u, _ in screen.derivatives)
    evidence['baseline_ratio'] = baseline

    def rank(row, key):
        ratio = row[key]
        gain = max(baseline-ratio, 1e-12)
        return (row['preference_change'], ratio > 1.,
                row['cost_change'] if ratio <= 1. else max(row['cost_change'], 0.)/gain,
                ratio, row['group'], candidate_name(row['option']))

    for row in options:
        row['predicted_ratio'] = screen.predict(row['group'], row['ea_ratio'])
    options.sort(key=lambda row: rank(row, 'predicted_ratio'))
    evidence['catalogue_predictions'] = len(options)
    # Diversify groups, then fill the remaining slots by predicted response/cost.
    shortlist = []
    for group in BRACE_GROUPS:
        first = next((row for row in options if row['group'] == group), None)
        if first is not None:
            shortlist.append(first)
    for row in options:
        if len(shortlist) >= 4:
            break
        if not any(row is other for other in shortlist):
            shortlist.append(row)
    verified = []
    for row in shortlist:
        record = {k: row[k] for k in ('group', 'ea_ratio', 'cost_change', 'predicted_ratio')}
        record['section'] = candidate_name(row['option'])
        try:
            row['physical_sls_ratio'] = screen.evaluate(row['group'], row['ea_ratio'])
            record['physical_sls_ratio'] = row['physical_sls_ratio']
            if row['physical_sls_ratio'] < baseline-1e-6:
                verified.append(row)
        except AnalysisError as error:
            record['analysis_error'] = str(error)
        evidence['lightweight_trials'].append(record)
    evidence.update(sls_solves=screen.sls_solves, elapsed_seconds=perf_counter()-started)
    if not verified:
        evidence['fallback'] = 'Shortlist found no useful movement reduction'
        return None, evidence
    best = min(verified, key=lambda row: rank(row, 'physical_sls_ratio'))
    group = best['group']
    evidence['selected'] = {'group': group, 'section': candidate_name(best['option']),
                            'physical_sls_ratio': best['physical_sls_ratio']}
    tried.add(group)
    return ({**chosen, group: best['option']}, group), evidence
