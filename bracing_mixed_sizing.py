"""Reanalysed, incremental-cost sizing of individual longitudinal strut paths."""
from bracing_analysis import AnalysisError
from bracing_components import StrutPlan, location_options
from bracing_sizing_diagnostics import failure_record, stop_for_strut


def auto_size_mixed(context, selections, candidates, criteria, rates, progress=None, max_solves=18):
    from bracing_design import (GROUPS, candidate_name, candidate_stiffness, group_assessments,
                               group_cost, member_assessment, solve_selection)
    current = dict(selections)
    result = solve_selection(context, current, criteria, rates)
    initial_result = result
    best = (result, dict(current)) if result["design"]["passes"] else None
    floors, visited, trace, screens = {}, set(), [], 0

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
        if result["design"]["passes"] and (best is None or result["design"]["estimated_cost"] < best[0]["design"]["estimated_cost"]):
            best = result, dict(current)
        if not result["design"]["taut_tangent_verified"]:
            raise AnalysisError("Per-location sizing needs a verified taut-rod tangent for every case")
        factor = min(max(result["design"]["global_sls"]["ratio"], 1.12), 2.) if result["design"]["global_sls"]["ratio"] > 1 else 1.
        proposed = dict(current)
        members = {}
        incomplete = None
        for index, member in enumerate(result["cases"][0]["members"]):
            if member["kind"] != "longitudinal_strut":
                continue
            old = current["strut"].members[member["id"]]
            if factor > 1:
                floors[member["id"]] = max(floors.get(member["id"], 0), old.A*200000*factor)
            priced = []
            rejected, below_floor = [], 0
            for option in location_options(old.location, candidates["strut"]):
                if option.A*200000 < floors.get(member["id"], 0)-1e-9:
                    below_floor += 1
                    continue
                screens += 1
                from purlin_beam_design import with_beam_demands, ARRANGEMENT
                line = old.location["line"]
                trial = {**current["strut"].members, member["id"]: option}
                affected = any(a.location["line"] == line and a.arrangement in {ARRANGEMENT, 'shared_purlin'} for a in trial.values()) or any(a.location["line"] == line and a.arrangement in {ARRANGEMENT, 'shared_purlin'} for a in current["strut"].members.values())
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
                    cost = group_cost(single, "strut", StrutPlan({member["id"]: option}, current["strut"].fallback), rates)["total"]
                    priced.append((cost, option.name, option))
            if not priced:
                incomplete = stop_for_strut(initial_result, result, old, rejected, below_floor)
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
                floors[group] = max(floors.get(group, 0), candidate_stiffness(current[group])*factor)
            priced = []
            for option in candidates[group]:
                if candidate_stiffness(option) < floors.get(group, 0)-1e-9:
                    continue
                screens += 1
                if all(row["passes"] for row in group_assessments(result, group, option, criteria)):
                    try:
                        priced.append((group_cost(result, group, option, rates)["total"], candidate_name(option), option))
                    except AnalysisError:
                        continue
            if not priced:
                if best:
                    incomplete = True
                    break
                raise AnalysisError(f"No rated and priced {group} candidate passes")
            proposed[group] = min(priced, key=lambda item: (item[0], item[1]))[2]
        if incomplete:
            break
        if key_of(proposed) == key or key_of(proposed) in visited:
            if best:
                break
            raise AnalysisError("Per-location sizing has not reached a passing candidate; review the constraints/data")
        current = proposed
        result = solve_selection(context, current, criteria, rates)
    if result["design"]["passes"] and (best is None or result["design"]["estimated_cost"] < best[0]["design"]["estimated_cost"]):
        best = result, dict(current)
    if best is None:
        raise AnalysisError("No verified per-location sizing within the search limit")
    best[0]["sizing_search"] = {"method": "Per-location incremental-cost demand iteration with exact reanalysis",
                                "scope": "Lowest-cost passing candidate found; no forced purlin/SHS layout and no global optimum claim",
                                "candidate_screens": screens, "rounds": trace, "max_solves": max_solves}
    return best
