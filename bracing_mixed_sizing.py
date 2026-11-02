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
    if budget <= 0:
        return best, [], 0
    from bracing_design import (candidate_name, candidate_stiffness, group_members,
                               group_cost, member_assessment, solve_selection)
    from purlin_beam_design import with_beam_demands
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
    indices={m['id']:i for i,m in enumerate(result['cases'][0]['members'])}
    for arrangement in ('shared_purlin', 'shared_purlin_pair'):
        members = {}
        for identifier, old in chosen['strut'].members.items():
            options = [o for o in location_options(old.location, [])
                       if o.arrangement in ('existing_purlins',arrangement)]
            member=result['cases'][0]['members'][indices[identifier]]
            single={**result,'cases':[{**result['cases'][0],'members':[member]}]}
            ranked=[]
            for option in options:
                try:
                    cost=group_cost(single,'strut',StrutPlan({identifier:option},chosen['strut'].fallback),rates)['total']
                except AnalysisError:
                    continue
                ranked.append((cost,option.name,option))
            picked=None
            for _,_,option in sorted(ranked,key=lambda row:row[:2]):
                trial={**chosen['strut'].members,**members,identifier:option}
                screened=with_beam_demands(result,StrutPlan(trial,chosen['strut'].fallback))
                if member_assessment(screened,indices[identifier],option,criteria)['passes']:
                    picked=option
                    break
            if picked is None:
                break
            # Existing purlins are retained wherever locally adequate. Do not
            # fill a whole roof run with the largest-EA addition for sway.
            members[identifier] = picked
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


def auto_size_mixed(context, selections, candidates, criteria, rates, progress=None, max_solves=18,
                    *, fast=False, initial_result=None, retain_brace_stiffness=False):
    from bracing_design import (GROUPS, candidate_name, candidate_stiffness, group_assessments,
                               group_cost, member_assessment, solve_selection, stiffness_ceiling,
                               bounded_stiffness_floor, reduce_verified_cost)
    from bracing_design import fast_sway_proposal
    current = dict(selections)
    result = initial_result if initial_result is not None else solve_selection(context, current, criteria, rates)
    initial_result = result
    solves = 1
    ceilings = {g: stiffness_ceiling(context, result, g, candidates[g], rates) for g in GROUPS if g != 'strut'}
    best = (result, dict(current)) if result["design"]["passes"] else None
    floors, visited, trace, screens = {}, set(), [], 0
    if retain_brace_stiffness:
        floors.update({g: candidate_stiffness(current[g]) for g in ('roof_brace', 'wall_brace', 'end_brace')})
    local_rounds, sway_tried, sway_adjustments = 0, set(), []

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
        if fast and iteration > 0 and result['design']['passes']:
            break
        if (fast and (local_rounds or retain_brace_stiffness) and criteria.include_serviceability and result['design']['member_checks_pass']
                and result['design']['taut_tangent_verified'] and result['design']['global_sls']['ratio'] > 1):
            adjustment = fast_sway_proposal(context, result, current, candidates, criteria, rates, sway_tried)
            if adjustment:
                proposed, group = adjustment
                floors[group] = max(floors.get(group, 0.), candidate_stiffness(proposed[group]))
                if progress:
                    progress(iteration, max_solves, 'Checking targeted SLS stiffness: '+group)
                solves += 1
                try:
                    result = solve_selection(context, proposed, criteria, rates)
                except AnalysisError as error:
                    if best:
                        break
                    raise stop_for_search(initial_result, result, 'Targeted brace check failed: '+str(error)) from error
                current = proposed
                sway_adjustments.append(group)
                continue
            if best:
                break
            raise stop_for_search(initial_result, result, 'Short targeted brace-stiffness checks exhausted; review the load path')
        if not result["design"]["taut_tangent_verified"]:
            raise stop_for_search(initial_result, result, 'Per-location sizing needs a verified taut-rod tangent for every case')
        factor = min(max(result["design"]["global_sls"]["ratio"], 1.12), 2.) if not fast and criteria.include_serviceability and result["design"]["global_sls"]["ratio"] > 1 else 1.
        proposed = dict(current)
        members = {}
        incomplete = None
        for index, member in enumerate(result["cases"][0]["members"]):
            if member["kind"] != "longitudinal_strut":
                continue
            old = current["strut"].members[member["id"]]
            options = location_options(old.location, candidates['strut'])
            priced = []
            rejected = []
            # Global building sway does not impose an EA/area floor on struts.
            # Choose the cheapest locally checked ULS/roof-SLS arrangement;
            # its real EA still participates in the next exact building solve.
            pending = list(options)
            if fast:
                # Cost is independent of the trial force distribution. Screen
                # cheapest options first and stop at the first local pass;
                # expensive beam rechecks of every larger steel section cannot
                # improve that local cost ranking. The combined proposal still
                # undergoes exact whole-building and adjoining-run acceptance.
                single = {**result, 'cases': [{**result['cases'][0], 'members': [member]}]}
                ranked = []
                for option in pending:
                    try:
                        cost = group_cost(single, 'strut', StrutPlan({member['id']: option}, current['strut'].fallback), rates)['total']
                    except AnalysisError:
                        continue
                    ranked.append((cost, option.name, option))
                pending = [row[-1] for row in sorted(ranked, key=lambda row: row[:2])]
            while pending:
                option = pending.pop(0)
                screens += 1
                from purlin_beam_design import with_beam_demands, ARRANGEMENT
                line = old.location["line"]
                trial = {**current["strut"].members, member["id"]: option}
                affected = any(a.location["line"] == line and a.arrangement in {ARRANGEMENT, 'shared_purlin', 'shared_purlin_pair'} for a in trial.values()) or any(a.location["line"] == line and a.arrangement in {ARRANGEMENT, 'shared_purlin', 'shared_purlin_pair'} for a in current["strut"].members.values())
                screened = with_beam_demands(result, StrutPlan(trial, current["strut"].fallback),
                    only_lines={line} if fast else None) if affected else result
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
                    if fast:
                        break
            if not priced:
                incomplete = stop_for_strut(initial_result, result, old, rejected)
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
        local_rounds += 1
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
                                                        0 if fast else min(4, max_solves-solves), progress)
    solves += extra
    best, reductions, extra, reduction_screens = reduce_verified_cost(context, best, candidates, criteria, rates, 0 if fast else max_solves-solves, progress)
    solves += extra
    best[0]["sizing_search"] = {"method": "Per-location incremental-cost demand iteration with exact reanalysis",
                                "scope": "Passing candidate found, ranked by project brace-family preferences then estimated cost; no forced purlin/SHS layout or global optimum claim",
                                "candidate_screens": screens+reduction_screens, "rounds": trace, "max_solves": max_solves,
                                'exact_solves': solves, 'stiffness_ceilings_n': ceilings,
                                'cost_reduction_trials': coupled_trials+reductions}
    best[0]['sizing_search']['locally_checked_strut_floor_limits'] = []  # legacy evidence field
    best[0]['sizing_search']['strut_sizing_basis'] = 'ULS interaction and required local member/roof serviceability; no global-sway stiffness floor'
    best[0]['sizing_search']['fast'] = fast
    best[0]['sizing_search']['targeted_sway_adjustments'] = sway_adjustments
    if fast:
        best[0]['sizing_search']['scope'] = 'Demand-ranked per-location sizing; stop after a verified resized pass; coupled/group cost-polish analyses omitted. No least-cost claim.'
    return best
