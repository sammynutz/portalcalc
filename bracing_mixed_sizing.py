"""Reanalysed, incremental-cost sizing of individual longitudinal strut paths."""
from bracing_analysis import AnalysisError
from bracing_components import StrutPlan, location_options
from bracing_sizing_diagnostics import failure_record, stop_for_strut, stop_for_search
from bracing_policy import candidate_rank, eligible as policy_eligible, selection_score


def reduce_strut_brace_cost(context, best, candidates, criteria, rates, budget, progress=None):
    """Bounded coupled trials: cheaper cold-formed paths and stiffer braces.

    A group-at-a-time polish cannot trade steel-strut cost for brace stiffness.
    These are proposals only; the unchanged pinned layout and every check must
    pass a new exact analysis before any plan replaces the verified result.
    """
    from bracing_design import (candidate_name, candidate_stiffness, group_members,
                               group_cost, solve_selection)
    result, chosen = best
    brace_groups = [g for g in ('roof_brace', 'wall_brace', 'end_brace') if group_members(result, g)]
    stiff = dict(chosen)
    for group in brace_groups:
        priced = []
        for option in candidates[group]:
            if not policy_eligible(context, group, option):
                continue
            try:
                cost = group_cost(result, group, option, rates)['total']
            except AnalysisError:
                continue
            ea = candidate_stiffness(option)
            if ea > 0:
                priced.append((-ea, candidate_rank(context, group, option), cost, candidate_name(option), option))
        if priced:
            stiff[group] = min(priced, key=lambda row: row[:4])[-1]
    plans = []
    for arrangement in ('shared_purlin', 'shared_purlin_pair'):
        members = {}
        for identifier, old in chosen['strut'].members.items():
            options = [o for o in location_options(old.location, []) if o.arrangement == arrangement]
            if not options:
                break
            members[identifier] = max(options, key=lambda o: o.A)
        if len(members) == len(chosen['strut'].members):
            plan = StrutPlan(members, chosen['strut'].fallback)
            for label, base in (('current braces', chosen), ('stiffer eligible braces', stiff)):
                trial = {**base, 'strut': plan}
                try:
                    cost = sum(group_cost(result, group, trial[group], rates)['total']
                               for group in candidates if group_members(result, group))
                except AnalysisError:
                    continue
                score = selection_score(context, trial, {'design': {'estimated_cost': cost}})
                if score < selection_score(context, chosen, result):
                    plans.append((score, arrangement+' / '+label, trial))
    records, used, seen = [], 0, set()
    for _, label, trial in sorted(plans, key=lambda row: row[:2]):
        if used >= budget:
            break
        key = (tuple((k, a.name) for k,a in sorted(trial['strut'].members.items())),
               tuple(candidate_name(trial[g]) for g in brace_groups))
        if key in seen:
            continue
        seen.add(key)
        if progress:
            progress(used, budget, 'Checking purlin/brace cost trade: '+label)
        used += 1
        record = {'group': 'strut and braces', 'section': label, 'adopted': False}
        try:
            checked = solve_selection(context, trial, criteria, rates)
        except AnalysisError as error:
            record['analysis_error'] = str(error)
        else:
            record.update(passes=checked['design']['passes'], estimated_cost=checked['design']['estimated_cost'],
                          global_sls_ratio=checked['design']['global_sls']['ratio'])
            if checked['design']['passes'] and selection_score(context, trial, checked) < selection_score(context, best[1], best[0]):
                best = checked, trial
                record['adopted'] = True
        records.append(record)
    return best, records, used


def auto_size_mixed(context, selections, candidates, criteria, rates, progress=None, max_solves=18):
    from bracing_design import (GROUPS, candidate_name, candidate_stiffness, group_assessments,
                               group_cost, member_assessment, solve_selection, stiffness_ceiling,
                               bounded_stiffness_floor, reduce_verified_cost)
    current = dict(selections)
    result = solve_selection(context, current, criteria, rates)
    initial_result = result
    solves = 1
    ceilings = {g: stiffness_ceiling(context, result, g, candidates[g], rates) for g in GROUPS if g != 'strut'}
    best = (result, dict(current)) if result["design"]["passes"] else None
    floors, visited, trace, screens = {}, set(), [], 0
    floor_limits = []

    def key_of(choice):
        return (tuple((key, value.name) for key, value in sorted(choice["strut"].members.items())),
                tuple(candidate_name(choice[g]) for g in GROUPS if g != "strut"))

    for iteration in range(max_solves-1):
        if progress:
            progress(iteration, max_solves, f"Per-location sizing round {iteration+1}; best: " +
                     (f"${best[0]['design']['estimated_cost']:,.2f}" if best else "not yet verified"))
        key = key_of(current)
        visited.add(key)
        trace.append({"passes": result["design"]["passes"], "estimated_cost": result["design"]["estimated_cost"],
                      "strut_arrangements": {k: a.name for k, a in current["strut"].members.items()}})
        if result["design"]["passes"] and (best is None or selection_score(context, current, result) < selection_score(context, best[1], best[0])):
            best = result, dict(current)
        if not result["design"]["taut_tangent_verified"]:
            raise stop_for_search(initial_result, result, 'Per-location sizing needs a verified taut-rod tangent for every case')
        factor = min(max(result["design"]["global_sls"]["ratio"], 1.12), 2.) if criteria.include_serviceability and result["design"]["global_sls"]["ratio"] > 1 else 1.
        proposed = dict(current)
        members = {}
        incomplete = None
        for index, member in enumerate(result["cases"][0]["members"]):
            if member["kind"] != "longitudinal_strut":
                continue
            old = current["strut"].members[member["id"]]
            options = location_options(old.location, candidates['strut'])
            ceiling = max(option.A*200000 for option in options)
            if factor > 1:
                floors[member["id"]] = bounded_stiffness_floor(floors.get(member['id'], 0), old.A*200000, factor, ceiling)
            priced = []
            rejected, below_floor = [], []
            # A stiffness floor guides the search; it is not a member capacity
            # requirement. If no locally checked arrangement meets that target,
            # try the strongest checked alternative and let other groups grow.
            # Otherwise a large steel option that fails the roof purlin's local
            # bending can stop the entire search before braces are stiffened.
            above = [o for o in options if o.A*200000 >= floors.get(member['id'], 0)-1e-9]
            below_floor = [o for o in options if o.A*200000 < floors.get(member['id'], 0)-1e-9]
            pending = list(above)
            retry_below = bool(below_floor)
            while pending or (retry_below and not priced):
                if not pending:
                    pending = list(below_floor)
                    retry_below = False
                option = pending.pop(0)
                screens += 1
                from purlin_beam_design import with_beam_demands, ARRANGEMENT
                line = old.location["line"]
                trial = {**current["strut"].members, member["id"]: option}
                affected = any(a.location["line"] == line and a.arrangement in {ARRANGEMENT, 'shared_purlin', 'shared_purlin_pair'} for a in trial.values()) or any(a.location["line"] == line and a.arrangement in {ARRANGEMENT, 'shared_purlin', 'shared_purlin_pair'} for a in current["strut"].members.values())
                screened = with_beam_demands(result, StrutPlan(trial, current["strut"].fallback)) if affected else result
                checked = member_assessment(screened, index, option, criteria)
                if not checked["passes"]:
                    rejected.extend(failure_record(option, checked))
                # Trial demands cover the entire roof run. A currently failing
                # neighbour may need its own addition in this same sizing round,
                # so do not reject a local improvement solely for that failure.
                # Exact global reanalysis/checking gates adoption of the combined
                # proposal, including every adjoining member on affected runs.
                if checked["passes"]:
                    single = {**result, "cases": [{**result["cases"][0], "members": [member]}]}
                    try:
                        cost = group_cost(single, "strut", StrutPlan({member["id"]: option}, current["strut"].fallback), rates)["total"]
                    except AnalysisError:
                        continue
                    priced.append((cost, option.name, option))
            if priced and not retry_below and below_floor:
                checked_ceiling = max(o.A*200000 for _, _, o in priced)
                previous = floors.get(member['id'], 0)
                if checked_ceiling < previous-1e-9:
                    floors[member['id']] = checked_ceiling
                    priced = [row for row in priced if row[2].A*200000 >= checked_ceiling-1e-9]
                    floor_limits.append({'member': member['id'], 'requested_ea_n': previous,
                                         'checked_ea_ceiling_n': checked_ceiling})
            if not priced:
                incomplete = stop_for_strut(initial_result, result, old, rejected, len(below_floor))
                break
            members[member["id"]] = min(priced, key=lambda item: (item[0], item[1]))[2]
        if incomplete:
            if best:
                break
            raise incomplete
        proposed["strut"] = StrutPlan(members, current["strut"].fallback)
        for group in GROUPS:
            if group == "strut" or not any(m["kind"] == GROUPS[group] for m in result["cases"][0]["members"]):
                continue
            if factor > 1:
                floors[group] = bounded_stiffness_floor(floors.get(group, 0), candidate_stiffness(current[group]), factor, ceilings[group])
            priced = []
            for option in candidates[group]:
                if not policy_eligible(context, group, option):
                    continue
                if candidate_stiffness(option) < floors.get(group, 0)-1e-9:
                    continue
                screens += 1
                if all(row["passes"] for row in group_assessments(result, group, option, criteria)):
                    try:
                        priced.append((candidate_rank(context, group, option), group_cost(result, group, option, rates)["total"], candidate_name(option), option))
                    except AnalysisError:
                        continue
            if not priced:
                if best:
                    incomplete = True
                    break
                raise stop_for_search(initial_result, result, f'No eligible rated and priced {group} candidate passes the current demands and bounded stiffness floor', group, floors.get(group, 0), ceilings[group])
            proposed[group] = min(priced, key=lambda item: item[:3])[3]
        if incomplete:
            break
        if key_of(proposed) == key or key_of(proposed) in visited:
            if best:
                break
            raise stop_for_search(initial_result, result, 'Per-location candidate selections repeat before a verified design is found')
        solves += 1
        try:
            next_result = solve_selection(context, proposed, criteria, rates)
        except AnalysisError as error:
            if best:
                break
            raise stop_for_search(initial_result, result, 'Candidate reanalysis failed: '+str(error)) from error
        current, result = proposed, next_result
    if result["design"]["passes"] and (best is None or selection_score(context, current, result) < selection_score(context, best[1], best[0])):
        best = result, dict(current)
    if best is None:
        raise stop_for_search(initial_result, result, 'No verified per-location sizing within the search limit')
    best, coupled_trials, extra = reduce_strut_brace_cost(context, best, candidates, criteria, rates,
                                                        min(4, max_solves-solves), progress)
    solves += extra
    best, reductions, extra, reduction_screens = reduce_verified_cost(context, best, candidates, criteria, rates, max_solves-solves, progress)
    solves += extra
    best[0]["sizing_search"] = {"method": "Per-location incremental-cost demand iteration with exact reanalysis",
                                "scope": "Passing candidate found, ranked by project brace-family preferences then estimated cost; no forced purlin/SHS layout or global optimum claim",
                                "candidate_screens": screens+reduction_screens, "rounds": trace, "max_solves": max_solves,
                                'exact_solves': solves, 'stiffness_ceilings_n': ceilings,
                                'cost_reduction_trials': coupled_trials+reductions}
    best[0]['sizing_search']['locally_checked_strut_floor_limits'] = floor_limits
    return best
