"""Scoped member checks and cost-ranked sizing of the fixed 3D brace layout.

Uses the application's existing preliminary AS 4100-style member engine.
Connection acceptance, geometric stability and production portal sizing remain
separate. Catalogue search is grouped/iterative, not a global layout optimum.
"""
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import re

from bracing_analysis import AnalysisError
from bracing_analysis_desktop import analyse_context
from costing import END_WALL_ROD_TURNBUCKLES, UB_STOCK_LENGTHS_M, section_cost_bucket
from member_checks import (MemberActions, SectionCheckProperties, check_member,
                           tension_capacity_kn)
from bracing_components import (StrutAssembly, StrutPlan, assembly_assessment,
                                assembly_properties, wire_check, wire_price)
from bracing_schedule import rod_check


GROUPS = {"strut": "longitudinal_strut", "end_post": "end_post",
          "roof_brace": "roof_brace", "wall_brace": "wall_brace", "end_brace": "end_brace"}


@dataclass(frozen=True)
class DesignCriteria:
    brace_fy_mpa: float = 250.
    brace_fu_mpa: float = 410.
    net_area_factor: float = .70
    tension_distribution_factor: float = .85
    strut_ke: float = 1.
    post_ke: float = 1.
    sls_divisor: float = 150.

    def validate(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise AnalysisError(f"Invalid section-check criterion: {name}")
        if self.net_area_factor > 1 or self.tension_distribution_factor > 1:
            raise AnalysisError("Net area and force distribution factors must not exceed 1")
        if self.brace_fu_mpa < self.brace_fy_mpa:
            raise AnalysisError("Brace ultimate stress must be at least its yield stress")


def candidate_name(candidate):
    return candidate["label"] if isinstance(candidate, dict) else candidate.name


def candidate_area(candidate):
    return float(candidate["area_mm2"] if isinstance(candidate, dict) else candidate.A)


def candidate_stiffness(candidate):
    if isinstance(candidate, dict) and candidate.get("component_type") == "wire_rope":
        try:
            return float(candidate.get("rating", {}).get("ea_kn", 0))*1000
        except (TypeError, ValueError):
            return 0.
    return 200000*candidate_area(candidate)


def candidate_properties(candidate, criteria):
    if isinstance(candidate, dict) and candidate.get("component_type") == "scheduled_rod":
        return dict(candidate)
    if isinstance(candidate, StrutPlan):
        return {"type": "strut_plan", "members": {key: candidate_properties(value, criteria) for key, value in candidate.members.items()}}
    if isinstance(candidate, StrutAssembly):
        props = assembly_properties(candidate)
        for record, component in zip(props["components"], candidate.components):
            if component["type"] == "steel":
                record["section_properties"] = asdict(section_properties(component["profile"], criteria))
        return props
    if isinstance(candidate, dict) and candidate.get("component_type") == "wire_rope":
        return {"type": "wire_rope", "diameter_mm": candidate["diameter_mm"], "rating": candidate.get("rating", {}),
                "reference_area_mm2": candidate["area_mm2"], "area_basis": "Reference for equivalent E only, not solid steel or strength area"}
    return asdict(section_properties(candidate, criteria))


def selected_key(selections):
    return tuple(candidate_name(selections[group]) for group in GROUPS)


def group_members(result, group):
    return [member for member in result["cases"][0]["members"] if member["kind"] == GROUPS[group]]


def section_properties(candidate, criteria):
    # Legacy tension_capacity_kn omits the separate 0.85 fracture multiplier.
    # Apply it through effective kt here. If that helper is corrected globally,
    # remove this adapter factor as well; the independent capacity test guards it.
    if isinstance(candidate, dict):
        area = candidate_area(candidate)
        return SectionCheckProperties(candidate_name(candidate), "TENSION BRACE", "",
                                      area, 0., 1., 1., fy_mpa=criteria.brace_fy_mpa,
                                      fu_mpa=criteria.brace_fu_mpa, net_area_mm2=area*criteria.net_area_factor,
                                      kt=.85*criteria.tension_distribution_factor)
    return replace(candidate.check_properties(), net_area_mm2=candidate.A*criteria.net_area_factor,
                   kt=.85*criteria.tension_distribution_factor)


def member_assessment(result, member_index, candidate, criteria):
    """Candidate screening uses a frozen demand envelope; final adoption re-solves."""
    criteria.validate()
    if isinstance(candidate, StrutAssembly):
        return assembly_assessment(result, member_index, candidate, criteria, member_assessment)
    member = result["cases"][0]["members"][member_index]
    if isinstance(candidate, dict) and candidate.get("component_type") in {"wire_rope", "scheduled_rod"}:
        check = wire_check if candidate["component_type"] == "wire_rope" else rod_check
        rows = [check(candidate, case["members"][member_index]["axial_n"]/1000, case["case"]["name"])
                for case in result["cases"] if case["case"]["level"] == "ULS"]
        governing = next((r for r in rows if r["ratio"] is None), None) or max(rows, key=lambda row: row["ratio"])
        ratio = governing["ratio"]
        return {"id": member["id"], "section": candidate_name(candidate), "ratio": ratio,
                "mode": governing["mode"], "case": governing["case"], "case_checks": rows,
                "passes": ratio is not None and ratio <= 1}
    section = section_properties(candidate, criteria)
    length = member["length_mm"]
    group = member["kind"]
    is_brace = member["tension_only"]
    local_weight = candidate_area(candidate) * 7.85e-6 * 9.80665  # N/mm
    if not is_brace and (min(section.iy_mm4, section.iz_mm4) <= 0 or not section.analysis_s_mm3):
        return {"id": member["id"], "section": candidate_name(candidate), "ratio": None,
                "mode": "Missing section properties", "case": "-", "case_checks": [], "passes": False}
    rows = []
    for case in result["cases"]:
        force = case["members"][member_index]["axial_n"] / 1000.
        name = case["case"]["name"]
        if is_brace:
            if case["case"]["level"] != "ULS":
                continue
            capacity = tension_capacity_kn(section)
            ratio = max(force, 0.) / capacity if capacity > 0 else math.inf
            rows.append({"case": name, "mode": "Tension (gross yield / configured net fracture)",
                         "ratio": ratio, "tension_kn": max(force, 0.), "capacity_kn": capacity,
                         "gross_yield_kn": .9*section.area_mm2*section.fy_mpa/1000,
                         "fracture_factor": .85, "configured_kt": criteria.tension_distribution_factor,
                         "net_fracture_kn": .9*section.kt*section.design_net_area_mm2*section.fu_mpa/1000})
            continue
        pressure_load = 0.
        if group == "end_post":
            a = result["nodes"][member["start"]]["point_mm"]
            stations = result["analysis_layout"]["geometry"]["end_wall_stations_mm"]
            index = min(range(len(stations)), key=lambda i: abs(stations[i]-a[0]))
            tributary = (stations[index+1]-stations[index-1]) / 2
            pressure = case["case"]["front_kpa"] if abs(a[1]) < 1e-5 else case["case"]["back_kpa"]
            pressure_load = abs(pressure) / 1000 * tributary  # N/mm
        line_load = local_weight if group == "longitudinal_strut" else pressure_load
        if case["case"]["level"] == "ULS":
            # Strut gravity is a separate local simply-supported beam check.
            # Post self-weight is conservatively added to compression without
            # taking beneficial gravity credit against wind tension.
            moment = line_load*length**2/8/1e6 * (1.2 if group == "longitudinal_strut" else 1.)
            shear = line_load*length/2/1000 * (1.2 if group == "longitudinal_strut" else 1.)
            gravity_n = 1.2*local_weight*length if group == "end_post" else 0.
            ke = criteria.strut_ke if group == "longitudinal_strut" else criteria.post_ke
            actions = MemberActions(compression_kn=max(-force, 0.)+gravity_n/1000,
                                    tension_kn=max(force, 0.), moment_knm=moment, shear_kn=shear,
                                    effective_length_y_mm=length*ke, effective_length_z_mm=length*ke)
            checked = check_member(section, actions)
            governing = max(checked.checks.values(), key=lambda item: item.ratio)
            rows.append({"case": name, "mode": governing.label, "ratio": governing.ratio,
                         "actions": asdict(actions), "checks": {key: asdict(value) for key, value in checked.checks.items()}})
        else:
            deflection = 5*line_load*length**4/(384*200000*section.analysis_i_mm4)
            limit = length/criteria.sls_divisor
            rows.append({"case": name, "mode": "Local simply-supported deflection",
                         "ratio": deflection/limit, "deflection_mm": deflection, "limit_mm": limit})
    governing = max(rows, key=lambda item: item["ratio"])
    finite = math.isfinite(governing["ratio"])
    # JSON must never silently encode non-finite engineering results.
    for row in rows:
        if not math.isfinite(row["ratio"]):
            row["ratio"] = None
        for check in row.get("checks", {}).values():
            if not math.isfinite(check["ratio"]):
                check["ratio"] = None
    return {"id": member["id"], "section": candidate_name(candidate),
            "ratio": governing["ratio"] if finite else None, "mode": governing["mode"],
            "case": governing["case"], "case_checks": rows,
            "passes": finite and governing["ratio"] <= 1.}


def group_assessments(result, group, candidate, criteria):
    return [member_assessment(result, i, candidate.members[member["id"]] if isinstance(candidate, StrutPlan) else candidate, criteria)
            for i, member in enumerate(result["cases"][0]["members"]) if member["kind"] == GROUPS[group]]


def group_cost(result, group, candidate, rates):
    members = group_members(result, group)
    if isinstance(candidate, StrutPlan):
        items = []
        for member in members:
            option = candidate.members[member["id"]]
            single = {**result, "cases": [{**result["cases"][0], "members": [member]}]}
            length = member["length_mm"]/1000
            if not isinstance(option, StrutAssembly):
                items.append(group_cost(single, group, option, rates))
                continue
            for component in option.components:
                if component["type"] == "steel":
                    items.append({**group_cost(single, group, component["profile"], rates), "member": member["id"]})
                else:
                    area = component["data"]["area_mm2"]
                    kg = area*length*.00785
                    purchased = 0. if component["existing"] else kg*1.07
                    rate = float(rates.get("purlin", rates["ub"]))
                    if not math.isfinite(rate) or rate < 0:
                        raise AnalysisError("Purlin cost rate must be finite and non-negative")
                    items.append({"member": member["id"], "section": component["data"]["section"],
                                  "existing": component["existing"], "installed_kg": kg, "purchased_kg": purchased,
                                  "installed_length_m": length, "purchased_length_m": 0. if component["existing"] else length*1.07,
                                  "material_cost": purchased*rate, "listed_fittings_cost": 0., "total": purchased*rate})
        return {"group": group, "section": candidate.name, "member_count": len(members),
                **{key: sum(item.get(key, 0) for item in items) for key in ("installed_kg", "purchased_kg", "installed_length_m", "purchased_length_m", "material_cost", "listed_fittings_cost", "total")},
                "material_rate_per_kg": 0., "allowance": "Incremental additions only; existing roof purlins already costed in the building",
                "component_costs": items, "incremental_only": True}
    if isinstance(candidate, StrutAssembly):
        return group_cost(result, group, StrutPlan({m["id"]: candidate for m in members}, candidate), rates)
    if isinstance(candidate, dict) and candidate.get("component_type") == "wire_rope":
        length = sum(m["length_mm"] for m in members)/1000
        priced = wire_price(candidate, length, len(members)) if members else {"material_cost": 0., "listed_fittings_cost": 0., "total": 0., "allowance": "Not installed"}
        return {"group": group, "section": candidate_name(candidate), "member_count": len(members),
                "installed_length_m": length, "purchased_length_m": length, "installed_kg": 0., "purchased_kg": 0.,
                "material_rate_per_kg": 0., **priced, "mass_basis": "Not inferred from nominal diameter"}
    area = candidate_area(candidate)
    if isinstance(candidate, dict):
        bucket = "ub"
    else:
        bucket = section_cost_bucket(candidate)
    rate = float(rates[bucket])
    if not math.isfinite(rate) or rate < 0:
        raise AnalysisError("Costing rates must be finite and non-negative")
    length = sum(m["length_mm"] for m in members) / 1000
    if isinstance(candidate, dict):
        costed_length = length
        allowance = "Exact brace length"
    elif bucket == "shs":
        costed_length = length*1.07
        allowance = "7% hollow-section waste"
    else:
        cuts = [m["length_mm"]/1000 for m in members]
        if any(cut > UB_STOCK_LENGTHS_M[-1]+1e-9 for cut in cuts):
            raise AnalysisError("A rolled-section cut exceeds priced stock length; splice design/cost is unavailable")
        costed_length = sum(next(stock for stock in UB_STOCK_LENGTHS_M if stock >= cut-1e-9) for cut in cuts)
        allowance = "Individual cuts rounded to existing UB stock lengths"
    installed_kg = area*length*.00785
    purchased_kg = area*costed_length*.00785
    fitting_cost = 0.
    if isinstance(candidate, dict) and candidate.get("component_type") == "scheduled_rod" and members:
        price = candidate.get("fitting_unit_cost")
        if price is None or not math.isfinite(float(price)) or float(price) < 0:
            raise AnalysisError(f"Enter a fitting price for {candidate['forged_turnbuckle']} on {candidate_name(candidate)}")
        fitting_cost = len(members)*float(price)
        allowance += f"; one {candidate['forged_turnbuckle']} forged-rated turnbuckle per diagonal (connection capacity unchecked)"
    elif isinstance(candidate, dict) and candidate.get("component_type") != "scheduled_rod" and "ROD" in candidate_name(candidate).upper():
        diameter = int(re.search(r"\d+", candidate_name(candidate))[0])
        if diameter not in END_WALL_ROD_TURNBUCKLES:
            raise AnalysisError(f"No turnbuckle price for {candidate_name(candidate)}")
        fitting_cost = len(members)*END_WALL_ROD_TURNBUCKLES[diameter]["unit_cost"]
    return {"group": group, "section": candidate_name(candidate), "member_count": len(members),
            "installed_length_m": length, "purchased_length_m": costed_length,
            "installed_kg": installed_kg, "purchased_kg": purchased_kg, "material_rate_per_kg": rate,
            "material_cost": purchased_kg*rate, "listed_fittings_cost": fitting_cost,
            "total": purchased_kg*rate+fitting_cost, "allowance": allowance}


def attach_design(result, selections, criteria, rates):
    criteria.validate()
    if isinstance(selections["strut"], StrutPlan):
        from purlin_beam_design import with_beam_demands
        result = with_beam_demands(result, selections["strut"])
    checks = {}
    costs = []
    for group, candidate in selections.items():
        checks.update((row["id"], row) for row in group_assessments(result, group, candidate, criteria))
        costs.append(group_cost(result, group, candidate, rates))
    movements = []
    for case in result["cases"]:
        if case["case"]["level"] != "SLS":
            continue
        for node, displacement in zip(result["nodes"], case["displacements_mm"]):
            height = node["point_mm"][2]
            if height > 0:
                limit = height/criteria.sls_divisor
                movements.append({"ratio": abs(displacement[1])/limit, "node": node["index"],
                                  "case": case["case"]["name"], "movement_mm": abs(displacement[1]), "limit_mm": limit})
    drift = max(movements, key=lambda row: row["ratio"])
    stable = all(case["tangent_status"] == "positive_with_taut_rods" for case in result["cases"])
    passes = all(row["passes"] for row in checks.values()) and drift["ratio"] <= 1 and stable
    request = {"source_hash": result["source_hash"], "sections": {g: candidate_name(c) for g, c in selections.items()},
               "section_properties": {g: candidate_properties(c, criteria) for g, c in selections.items()},
               "net_fracture_multiplier": .85, "criteria": asdict(criteria), "cost_rates": rates}
    result["design_request"] = request
    result["design_request_hash"] = hashlib.sha256(json.dumps(request, sort_keys=True, allow_nan=False).encode()).hexdigest()
    result["section_checks"] = checks
    preliminary = any(c.get('preliminary') for check in checks.values() for c in check.get('case_checks', []))
    result["design"] = {"passes": passes, 'preliminary_reference_checks': preliminary,
                        "member_checks_pass": all(r["passes"] for r in checks.values()),
                        "global_sls": drift, "taut_tangent_verified": stable,
                        "estimated_cost": sum(row["total"] for row in costs), "cost_items": costs,
                        "cost_model": {"stock_lengths_m": UB_STOCK_LENGTHS_M, "hollow_waste_factor": 1.07,
                                       "legacy_fitting_price_catalogue": END_WALL_ROD_TURNBUCKLES,
                                       "selected_scheduled_fittings": {g: {key: c.get(key) for key in
                                           ("forged_turnbuckle", "grade_s_turnbuckle", "fitting_unit_cost")}
                                           for g, c in selections.items() if isinstance(c, dict) and c.get("component_type") == "scheduled_rod"}},
                        "basis": "Project-schedule rods/ropes use supplied factored brace strengths directly. Other members use the preliminary member engine with explicit 0.85 net-fracture multiplier and configured net-area/kt/effective lengths. One section per steel strut/post/brace group. Strong-axis bending orientation. Wind plus local member self-weight checks only; connections and full load combinations remain separate.",
                        "cost_basis": "Current Costing rates, exact brace lengths, 7% hollow waste, rolled stock rounding, one listed turnbuckle per rod. Angle fittings, other connections, labour and supplier availability/quotes are not priced."}
    result["limitations"] = [line for line in result["limitations"] if not line.startswith(("Forces are analysis demands only.", "Fixed pinned-panel layout;"))]
    result["limitations"] += [result["design"]["basis"], result["design"]["cost_basis"]]
    if isinstance(selections["strut"], StrutPlan):
        old_basis, old_cost = result["design"]["basis"], result["design"]["cost_basis"]
        result["design"]["basis"] = "Per-location common-end purlin/added-member assemblies. Installed compression uses the project-adopted Lysaght May 1999 p20 simplified method with Metroll 2023 p7 Ag/web-axis radius, or declared Nc/Nex ratings as selected. Purlin member tension uses 0.9 min(Ag fy, 0.85 kt An fu), G450 lower-bound fy450/fu480 and configured net-area/kt assumptions, or declared Nt/source as selected. Independent bending/axial envelopes. Added unsheeted cold-formed plies use separate residual ratings. Back-to-back roof sharing only when declared. Common-end/collector force transfer is assumed; its connection capacity is unchecked. No composite bending I."
        result["design"]["cost_basis"] = "Incremental added struts/purlins only; existing roof purlins already costed. New purlins use 7% waste at the captured purlin rate. Other connections/labour excluded."
        result["limitations"] = [line for line in result["limitations"] if not line.startswith("Dedicated axial struts") and line not in (old_basis, old_cost)]
        result["limitations"] += [result["design"]["basis"], result["design"]["cost_basis"]]
        if any(not run.get('preliminary') for run in result.get('purlin_beams', {}).get('runs', {}).values()):
            result["limitations"].append("Sheeted additions use a separate first-order roof-run beam model with entered finite cladding/fastener stiffness and independent single-span rotations. No global diaphragm/portal bending credit. Redistributed M/V member ratings and cladding transfer connection capacities remain explicit; missing stiffness or member capacities cannot pass.")
        if preliminary:
            from purlin_shared_design import BASIS
            result['design']['basis'] += ' '+BASIS
            result['limitations'].append(BASIS)
    return result


def solve_selection(context, selections, criteria, rates):
    local = {**context, **{g: selections[g] for g in ("roof_brace", "wall_brace", "end_brace")}}
    result = analyse_context(local, selections["strut"], selections["end_post"])
    return attach_design(result, selections, criteria, rates)


def auto_size(context, selections, candidates, criteria, rates, progress=None, max_solves=18):
    """Cheapest candidates for successive demand envelopes; always reanalyse.

    Returns the lowest-cost passing reanalysed candidate found. Stiffness floors
    may rise to meet global SLS. No claim of exhaustive/global optimality.
    """
    if isinstance(selections["strut"], StrutPlan):
        from bracing_mixed_sizing import auto_size_mixed
        return auto_size_mixed(context, selections, candidates, criteria, rates, progress, max_solves)
    current = dict(selections)
    result = solve_selection(context, current, criteria, rates)
    best = (result, dict(current)) if result["design"]["passes"] else None
    trace, visited, floors = [], set(), {group: 0. for group in GROUPS}
    screens = 0
    for iteration in range(max_solves-1):
        if progress:
            progress(iteration, max_solves, f"Sizing round {iteration+1}; best verified cost: " + (f"${best[0]['design']['estimated_cost']:,.2f}" if best else "none yet"))
        key = selected_key(current)
        visited.add(key)
        trace.append({"sections": dict(zip(GROUPS, key)), "passes": result["design"]["passes"],
                      "estimated_cost": result["design"]["estimated_cost"], "global_sls_ratio": result["design"]["global_sls"]["ratio"]})
        if result["design"]["passes"] and (best is None or result["design"]["estimated_cost"] < best[0]["design"]["estimated_cost"]):
            best = (result, dict(current))
        if not result["design"]["taut_tangent_verified"]:
            raise AnalysisError("Auto-sizing requires verified taut-rod tangent stiffness for every case; review the layout/neutral rods")
        if result["design"]["global_sls"]["ratio"] > 1:
            factor = min(max(result["design"]["global_sls"]["ratio"], 1.12), 2.)
            for group in GROUPS:
                floors[group] = max(floors[group], candidate_stiffness(current[group])*factor)
        proposed = dict(current)
        for group in GROUPS:
            if not group_members(result, group):
                continue
            ranked = []
            for candidate in candidates[group]:
                if candidate_stiffness(candidate) < floors[group]-1e-9:
                    continue
                screens += 1
                if all(row["passes"] for row in group_assessments(result, group, candidate, criteria)):
                    try:
                        ranked.append((group_cost(result, group, candidate, rates)["total"], candidate_name(candidate), candidate))
                    except AnalysisError:
                        continue
            if not ranked:
                if best:
                    break
                raise AnalysisError(f"No priced catalogue candidate passes the current {group} demands and stiffness floor")
            proposed[group] = min(ranked, key=lambda item: (item[0], item[1]))[2]
        proposed_key = selected_key(proposed)
        if proposed_key == key or proposed_key in visited:
            if best:
                break
            raise AnalysisError("Sizing did not reach a verified candidate; demands cycle or the SLS constraint needs a different layout")
        current = proposed
        try:
            result = solve_selection(context, current, criteria, rates)
        except AnalysisError:
            if best:
                break
            raise
    if result["design"]["passes"] and (best is None or result["design"]["estimated_cost"] < best[0]["design"]["estimated_cost"]):
        best = (result, dict(current))
    if best is None:
        raise AnalysisError("No verified member/SLS sizing found within the search limit")
    best[0]["sizing_search"] = {"method": "Cost-ranked grouped demand-envelope iteration with exact reanalysis",
                                "scope": "Lowest-cost passing candidate found; not a global catalogue/layout optimum",
                                "candidate_screens": screens, "rounds": trace, "max_solves": max_solves}
    return best
