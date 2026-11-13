"""Scoped member checks and cost-ranked sizing of the fixed 3D brace layout.

Uses the application's existing preliminary AS 4100-style member engine.
Connection acceptance, geometric stability and production portal sizing remain
separate. Catalogue search is grouped/iterative, not a global layout optimum.
"""
from dataclasses import asdict, dataclass, replace
import functools
import hashlib
import json
import math
import re

from bracing_analysis import AnalysisError, building_sway_node, positive
from bracing_portal_brace import MEMBER_GROUP, PORTAL_BRACE_GROUPS, PortalBraceCandidate
from bracing_analysis_desktop import analyse_context
from costing import END_WALL_ROD_TURNBUCKLES, UB_STOCK_LENGTHS_M, section_cost_bucket
from member_checks import MemberActions, SectionCheckProperties, check_member
from bracing_components import (StrutAssembly, StrutPlan, assembly_assessment,
                                assembly_properties, wire_check, wire_price, scoped_case_checks)
from bracing_schedule import rod_check
from bracing_policy import (apply_group_rule, evidence as policy_evidence, eligible as policy_eligible,
                            candidate_rank, selection_score)
from bracing_sizing_diagnostics import stop_for_search


GROUPS = {"strut": "longitudinal_strut", "end_post": "end_post",
          "roof_brace": "roof_brace", "wall_brace": "wall_brace", "end_brace": "end_brace",
          # Only for buildings with an open canopy; absent from selections otherwise.
          "canopy_brace": "canopy_brace",
          # Portal frame braces (UB moment frames in side-wall bays); only when
          # the layout has them. Their members are not network axial members.
          "portal_brace_column": "portal_brace_column", "portal_brace_beam": "portal_brace_beam"}
SWAY_BRACE_GROUPS = ('end_brace', 'roof_brace', 'wall_brace', 'canopy_brace') + PORTAL_BRACE_GROUPS


def active_groups(selections):
    """Design groups present in ``selections`` (canopy_brace only with a canopy)."""
    return [group for group in GROUPS if group in selections]


@dataclass(frozen=True)
class DesignCriteria:
    brace_fy_mpa: float = 300.
    brace_fu_mpa: float = 410.
    net_area_factor: float = .70
    tension_distribution_factor: float = .85
    strut_ke: float = 1.
    post_ke: float = 1.
    sls_divisor: float = 150.
    include_serviceability: bool = True
    global_sls_divisor: float | None = None
    # Project-adopted: bounded slack play in a single zero-stiffness rod mode
    # up to this movement counts as stable (turnbuckle pretension takes it up).
    # 0 accepts none.
    slack_play_limit_mm: float = 1.
    # Portal frame brace columns: minor-axis buckling and lateral-torsional
    # segment length, from the stitch welds to the portal column (stiffeners
    # where required).
    portal_brace_restraint_mm: float = 1500.
    # Portal frame brace beam: LTB rotational restraint factor kr (0.85 one
    # end / partial, 0.7 both knees restraining the flanges in plan) and lateral
    # restraint spacing (0 = the bay length).
    portal_brace_beam_kr: float = .85
    portal_brace_beam_restraint_mm: float = 0.

    def validate(self):
        for name, value in asdict(self).items():
            if name=='global_sls_divisor' and value is None:
                continue
            if name=='include_serviceability':
                if not isinstance(value,bool):
                    raise AnalysisError('Include serviceability must be true or false')
                continue
            if name=='slack_play_limit_mm':
                if isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0:
                    raise AnalysisError('Accepted slack play must be zero or a positive movement')
                continue
            if name=='portal_brace_beam_restraint_mm':
                if isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0:
                    raise AnalysisError('Portal brace beam restraint spacing must be zero (the bay) or positive')
                continue
            if name=='portal_brace_beam_kr':
                if isinstance(value, bool) or not isinstance(value,(int,float)) or not .7 <= value <= 1.:
                    raise AnalysisError('Portal brace beam kr must be between 0.7 and 1.0')
                continue
            if isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0:
                raise AnalysisError(f"Invalid section-check criterion: {name}")
        if self.net_area_factor > 1 or self.tension_distribution_factor > 1:
            raise AnalysisError("Net area and force distribution factors must not exceed 1")
        if self.brace_fu_mpa < self.brace_fy_mpa:
            raise AnalysisError("Brace ultimate stress must be at least its yield stress")


def global_sway_basis(result,criteria,context=None):
    """Global H/n is independent of local member/purlin L/n.

    Desktop models carry the actual main in-plane eave criterion. Export-only
    rechecks recover the same defaults/overrides from the frozen source preset.
    Low-level fixtures without an importance level retain their legacy L/n
    reference unless they supply an explicit global criterion.
    """
    source=result.get('source_preset',{})
    importance=source.get('wind',{}).get('importance_level')
    if criteria.global_sls_divisor is not None:
        return {'divisor':criteria.global_sls_divisor,'source':'Explicit 3D global sway override',
                'importance_level':importance,'mode':'override'}
    basis=(context or {}).get('global_sls_basis') or result.get('global_sls_basis')
    if basis:
        return {**basis,'divisor':positive(basis.get('divisor'),'Global sway divisor'),'mode':'follow_in_plane'}
    if importance is not None:
        il1=str(importance).strip() in ('1','1.0')
        display=source.get('display',{})
        limits=display.get('serviceability_limit_overrides') or {}
        key='column_il1' if il1 else 'column_standard'
        divisor=limits.get(key,80. if il1 else 150.)
        if source.get('storage_loads',{}).get('enabled'):
            divisor=limits.get('storage_eave',100.)
        if display.get('eave_deflection_limit_override') is not None:
            divisor=display['eave_deflection_limit_override']
        return {'divisor':positive(divisor,'Global sway divisor'),'importance_level':importance,
                'source':'Main in-plane eave sway criterion from frozen preset; project IL1 H/80 default',
                'mode':'follow_in_plane'}
    return {'divisor':criteria.sls_divisor,'importance_level':None,
            'source':'Legacy low-level fixture reference; importance level unavailable','mode':'legacy_reference'}


def candidate_name(candidate):
    return candidate["label"] if isinstance(candidate, dict) else candidate.name


def candidate_area(candidate):
    return float(candidate["area_mm2"] if isinstance(candidate, dict) else candidate.A)


def candidate_stiffness(candidate):
    if isinstance(candidate, PortalBraceCandidate):
        return candidate.bending_stiffness   # E I: portal braces resist sway in bending
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
                "reference_area_mm2": candidate["area_mm2"], "area_basis": "Nominal circular reference area; adopted EA/stiffness basis is recorded in rating, independent of strength"}
    return asdict(section_properties(candidate, criteria))


def selected_key(selections):
    return tuple(candidate_name(selections[group]) for group in active_groups(selections))


def group_members(result, group):
    if group in PORTAL_BRACE_GROUPS:
        return portal_brace_members(result, group)
    return [member for member in result["cases"][0]["members"] if member["kind"] == GROUPS[group]]


def portal_brace_members(result, group):
    """Portal brace columns or beams as member records (id, length, brace)."""
    return [{'id': f"{brace['id']}/{member['member']}", 'kind': group, 'length_mm': member['length_mm'],
             'brace': brace['id'], 'member': member['member']}
            for brace in result['cases'][0].get('portal_braces', ())
            for member in brace['members'] if MEMBER_GROUP[member['member']] == group]


def linear_moment_modifier(ma, mb):
    """AS 4100 Table 5.6.1 alpha_m for a segment with linear moment from ma to mb.

    beta_m is the smaller/larger end moment ratio, positive in reverse
    (double) curvature: alpha_m = 1.75 + 1.05 beta_m + 0.3 beta_m^2 <= 2.5.
    Uniform single curvature gives 1.0; zero at one end 1.75.
    """
    larger = max(abs(ma), abs(mb))
    if larger <= 1e-9:
        return 1.
    beta = min(abs(ma), abs(mb))/larger*(1. if ma*mb < 0 else -1.)
    return min(1.75+1.05*beta+.3*beta*beta, 2.5)


def member_segments(local_forces, length, spacing):
    """Equal restraint segments (at most ``spacing`` long) with their end moments.

    ``local_forces`` is [N1, V1, M1, N2, V2, M2]; with no member loads the
    internal moment varies linearly from -M1 to M2.
    """
    m1, m2 = -float(local_forces[2]), float(local_forces[5])
    count = max(1, math.ceil(length/spacing-1e-9)) if spacing and spacing > 0 else 1
    return [(length/count, m1+(m2-m1)*k/count, m1+(m2-m1)*(k+1)/count) for k in range(count)]


def portal_brace_assessments(result, group, candidate, criteria):
    """Combined actions for each portal brace column/beam, case by case (ULS).

    The recovered N, V and M are for the analysed sections; screening another
    candidate against them is frozen-demand, as for every group, and adoption
    re-solves. Each member is checked segment by segment between lateral
    restraints, with the segment's own peak moment and AS 4100 Table 5.6.1
    moment modifier from its end moments; the governing segment is reported.
    Columns: major-axis (in-plane sway) effective length 2.0 h for a
    pinned-base sway frame; minor-axis and LTB segments at most the stitch-weld/
    stiffener restraint spacing. Beam: major-axis length = span; minor-axis and
    LTB segments = the beam restraint spacing (0 = the bay), LTB kr as entered.
    """
    section = replace(candidate.check_properties(), compression_area_mm2=candidate.A)
    rows_by_member = {}
    for case in result['cases']:
        if case['case']['level'] != 'ULS':
            continue
        for brace in case.get('portal_braces', ()):
            for member in brace['members']:
                if MEMBER_GROUP[member['member']] != group:
                    continue
                length = member['length_mm']
                column = group == 'portal_brace_column'
                spacing = criteria.portal_brace_restraint_mm if column else criteria.portal_brace_beam_restraint_mm
                kr = 1. if column else criteria.portal_brace_beam_kr
                governing_row = None
                segments = member_segments(member['local_n_n_nmm'], length, spacing)
                for index, (segment, ma, mb) in enumerate(segments):
                    modifier = linear_moment_modifier(ma, mb)
                    actions = MemberActions(compression_kn=max(-member['axial_n'], 0.)/1000,
                                            tension_kn=max(member['axial_n'], 0.)/1000,
                                            shear_kn=member['shear_n']/1000, moment_knm=max(abs(ma), abs(mb))/1e6,
                                            effective_length_y_mm=segment,
                                            effective_length_z_mm=2.*length if column else length,
                                            ltb_length_mm=segment, moment_modifier=modifier, kr=kr)
                    checked = check_member(section, actions)
                    governing = max(checked.checks.values(), key=lambda item: item.ratio)
                    row = {'case': case['case']['name'], 'mode': governing.label, 'ratio': governing.ratio,
                           'segment': index, 'segment_count': len(segments),
                           'segment_mm': segment, 'segment_end_moments_knm': [ma/1e6, mb/1e6],
                           'actions': asdict(actions), 'checks': {key: asdict(value) for key, value in checked.checks.items()}}
                    if governing_row is None or not math.isfinite(row['ratio']) or row['ratio'] > governing_row['ratio']:
                        governing_row = row
                        if not math.isfinite(row['ratio']):
                            break
                rows_by_member.setdefault(f"{brace['id']}/{member['member']}", []).append(governing_row)
    assessments = []
    for identifier, rows in rows_by_member.items():
        for row in rows:
            if not math.isfinite(row['ratio']):
                row['ratio'] = None
            for check in row['checks'].values():
                if not math.isfinite(check['ratio']):
                    check['ratio'] = None
        rows, governing = scoped_case_checks(result, rows, criteria)
        finite = governing['ratio'] is not None and math.isfinite(governing['ratio'])
        assessments.append({'id': identifier, 'section': candidate_name(candidate),
                            'ratio': governing['ratio'] if finite else None, 'mode': governing['mode'],
                            'case': governing['case'], 'case_checks': rows, 'passes': finite and governing['ratio'] <= 1.})
    return assessments


def section_properties(candidate, criteria):
    # Legacy tension_capacity_kn omits the separate 0.85 fracture multiplier.
    # Apply it through effective kt here. If that helper is corrected globally,
    # remove this adapter factor as well; the independent capacity test guards it.
    if isinstance(candidate, dict):
        area = candidate_area(candidate)
        return SectionCheckProperties(candidate_name(candidate), "TENSION BRACE", "",
                                      area, 0., 1., 1., fy_mpa=criteria.brace_fy_mpa,
                                      fu_mpa=criteria.brace_fu_mpa, net_area_mm2=area*criteria.net_area_factor,
                                      kt=.85*criteria.tension_distribution_factor, compression_area_mm2=area)
    return replace(candidate.check_properties(), net_area_mm2=candidate.A*criteria.net_area_factor,
                   kt=.85*criteria.tension_distribution_factor, compression_area_mm2=candidate.A)


def member_assessment(result, member_index, candidate, criteria):
    """Candidate screening uses a frozen demand envelope; final adoption re-solves."""
    criteria.validate()
    if isinstance(candidate, StrutAssembly):
        return assembly_assessment(result, member_index, candidate, criteria, member_assessment)
    member = result["cases"][0]["members"][member_index]
    if member['kind']=='end_post' and result.get('mullion_basis'):
        from bracing_mullion_design import mullion_assessment
        return mullion_assessment(result,member_index,candidate,criteria)
    if isinstance(candidate, dict) and candidate.get("component_type") in {"wire_rope", "scheduled_rod"}:
        check = wire_check if candidate["component_type"] == "wire_rope" else rod_check
        rows = [check(candidate, case["members"][member_index]["axial_n"]/1000, case["case"]["name"])
                for case in result["cases"] if case["case"]["level"] == "ULS"]
        rows,governing = scoped_case_checks(result,rows,criteria)
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
            capacity = .9*section.area_mm2*section.fy_mpa/1000
            ratio = max(force, 0.) / capacity if capacity > 0 else math.inf
            rows.append({"case": name, 'method': 'gross_yield_brace_tension', 'preliminary': True,
                         "mode": "Adopted brace tension: 0.9 Ag fy (connections separate)",
                         "ratio": ratio, "tension_kn": max(force, 0.), "capacity_kn": capacity,
                         "gross_yield_kn": capacity, 'capacity_factor': .9, 'gross_area_mm2': section.area_mm2,
                         'fy_mpa': section.fy_mpa, 'connections_checked': False,
                         'basis': 'User-adopted member-only gross-yield screen. Holes, threads, shear lag, net fracture, fittings and anchors require separate connection checks; not complete tension-member acceptance.'})
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
                         'compression_area_mm2': section.compression_area_mm2,
                         'compression_area_basis': 'Gross member-body area; connection holes/end details checked separately',
                         "actions": asdict(actions), "checks": {key: asdict(value) for key, value in checked.checks.items()}})
        else:
            deflection = 5*line_load*length**4/(384*200000*section.analysis_i_mm4)
            limit = length/criteria.sls_divisor
            rows.append({"case": name, "mode": "Local simply-supported deflection",
                         "ratio": deflection/limit, "deflection_mm": deflection, "limit_mm": limit})
    # JSON must never silently encode non-finite engineering results.
    for row in rows:
        if not math.isfinite(row["ratio"]):
            row["ratio"] = None
        for check in row.get("checks", {}).values():
            if not math.isfinite(check["ratio"]):
                check["ratio"] = None
    rows,governing = scoped_case_checks(result,rows,criteria)
    finite = governing['ratio'] is not None and math.isfinite(governing['ratio'])
    return {"id": member["id"], "section": candidate_name(candidate),
            "ratio": governing["ratio"] if finite else None, "mode": governing["mode"],
            "case": governing["case"], "case_checks": rows,
            "passes": finite and governing["ratio"] <= 1.}


def group_assessments(result, group, candidate, criteria):
    if group in PORTAL_BRACE_GROUPS:
        return portal_brace_assessments(result, group, candidate, criteria)
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


def panel_installation_rate(rates):
    value = rates.get('braced_panel_installation', 50.)
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise AnalysisError('Braced-panel installation allowance must be finite and non-negative') from None
    if isinstance(value, bool) or not math.isfinite(rate) or rate < 0:
        raise AnalysisError('Braced-panel installation allowance must be finite and non-negative')
    return rate


def panel_installation_cost(result, rates):
    """Once per physical X panel, independent of case count and rod slackness."""
    kinds = {'roof_brace', 'wall_brace', 'end_brace', 'canopy_brace'}
    panels = {m['id'][6:].rsplit('/', 1)[0] for m in result['cases'][0]['members']
              if m['kind'] in kinds and m['id'].startswith('brace/')}
    rate = panel_installation_rate(rates)
    return {'group': 'panel_installation', 'section': 'Installation allowance',
            'panel_count': len(panels), 'panels': sorted(panels), 'rate_per_panel': rate,
            'member_count': 0, 'installed_kg': 0., 'purchased_kg': 0., 'material_rate_per_kg': 0.,
            'installed_length_m': 0., 'purchased_length_m': 0.,
            'material_cost': 0., 'listed_fittings_cost': 0., 'installation_cost': len(panels)*rate,
            'total': len(panels)*rate,
            'allowance': 'Once per installed physical X panel, including both diagonals; roof, side and end walls. SLS diaphragm equivalents excluded.'}


def portal_brace_allowance_cost(result, rates):
    """Fabrication/connection allowance once per portal brace, or None without any."""
    braces = sorted({brace['id'] for brace in result['cases'][0].get('portal_braces', ())})
    if not braces:
        return None
    value = rates.get('portal_brace_allowance', 0.)
    try:
        rate = float(value)
    except (TypeError, ValueError):
        raise AnalysisError('Portal brace allowance must be finite and non-negative') from None
    if isinstance(value, bool) or not math.isfinite(rate) or rate < 0:
        raise AnalysisError('Portal brace allowance must be finite and non-negative')
    return {'group': 'portal_brace_allowance', 'section': 'Portal brace allowance',
            'brace_count': len(braces), 'braces': braces, 'rate_per_brace': rate,
            'member_count': 0, 'installed_kg': 0., 'purchased_kg': 0., 'material_rate_per_kg': 0.,
            'installed_length_m': 0., 'purchased_length_m': 0., 'material_cost': 0., 'listed_fittings_cost': 0.,
            'installation_cost': len(braces)*rate, 'total': len(braces)*rate,
            'allowance': 'Once per portal frame brace: two moment knees, two base plates and stitch welding (user allowance).'}


def slack_play_assessment(result, limit_mm):
    """Rod stability over every case: taut tangents, or accepted bounded play.

    Project-adopted: a case whose zero-stiffness modes are all taken up by
    zero-force or slack rods (every direction and combination verified) within
    ``limit_mm`` is accepted. Larger play or an escaping direction stays
    unverified.
    """
    accepted, unverified = [], []
    for case in result['cases']:
        for status, play, label in ((case['tangent_status'], case.get('slack_play'), case['case']['name']),
                                    (case.get('bare_sls', {}).get('tangent_status', 'positive_with_taut_rods'),
                                     case.get('bare_sls', {}).get('slack_play'), case['case']['name']+' (bare SLS)')):
            if status == 'positive_with_taut_rods':
                continue
            # Bounded play, including zero play at zero-force rods, with every
            # direction (or combination of modes) shown to engage a rod.
            if (limit_mm > 0 and status in ('slack_play_stability_unverified', 'neutral_rods_required_stability_unverified')
                    and play and play.get('directions_verified', play['modes'] == 1)
                    and play['play_mm'] <= limit_mm):
                accepted.append((label, play['play_mm']))
            else:
                unverified.append((label, status, (play or {}).get('play_mm')))
    evidence = None
    if accepted or unverified:
        evidence = {'limit_mm': limit_mm, 'accepted_cases': len(accepted),
                    'max_accepted_play_mm': max((p for _, p in accepted), default=None),
                    'unverified_cases': [{'case': c, 'status': s, 'play_mm': p} for c, s, p in unverified[:20]],
                    'unverified_count': len(unverified),
                    'basis': 'Project-adopted: zero-stiffness rod modes count as stable when every movement along them, including any combination of '
                             'modes, extends a zero-force or slack rod within the play limit; turnbuckle pretension, not modelled, takes up the play. '
                             'Larger play, or a movement that extends no rod, stays unverified.'}
    return {'stable': not unverified, 'evidence': evidence}


def attach_design(result, selections, criteria, rates, context=None):
    criteria.validate()
    if isinstance(selections["strut"], StrutPlan):
        from purlin_beam_design import with_beam_demands
        result = with_beam_demands(result, selections["strut"])
    checks = {}
    costs = []
    for group, candidate in selections.items():
        assessed = group_assessments(result, group, candidate, criteria)
        if context is not None:
            assessed = apply_group_rule(context, group, candidate, assessed)
        checks.update((row["id"], row) for row in assessed)
        costs.append(group_cost(result, group, candidate, rates))
    installation = panel_installation_cost(result, rates)
    materials_and_fittings = sum(row['total'] for row in costs)
    costs.append(installation)
    portal_allowance = portal_brace_allowance_cost(result, rates)
    if portal_allowance is not None:
        costs.append(portal_allowance)
    sway_basis=global_sway_basis(result,criteria,context)
    movements = []
    for case in result["cases"]:
        # Global longitudinal sway only. Transverse SLS frame sway is reported
        # separately against the main in-plane criterion and does not govern.
        if case["case"]["level"] != "SLS" or case["case"].get("wind_family") == "transverse":
            continue
        for node, displacement in zip(result["nodes"], case["displacements_mm"]):
            height = node["point_mm"][2]
            # Main building only; open canopy edges are not building sway.
            if building_sway_node(node["label"], height):
                limit = height/sway_basis['divisor']
                movements.append({"ratio": abs(displacement[1])/limit, "node": node["index"],
                                  "case": case["case"]["name"], "movement_mm": abs(displacement[1]), "limit_mm": limit})
    drift = max(movements, key=lambda row: row["ratio"])
    drift.update(included_in_design=criteria.include_serviceability,passes=drift['ratio']<=1)
    drift.update(divisor=sway_basis['divisor'],basis=sway_basis)
    play = slack_play_assessment(result, criteria.slack_play_limit_mm)
    stable = play['stable']
    member_sls = [row for check in checks.values() for row in check['case_checks'] if row.get('level')=='SLS']
    sls_pass = drift['passes'] and all(row['ratio'] is not None and row['ratio']<=1 for row in member_sls)
    from bracing_spacing import max_bay_spacing,spacing_assessment
    from bracing_layout import BracingLayout
    physical=(context or {}).get('layout')
    if physical is None and 'pinned_panels' in result.get('analysis_layout',{}):
        physical=BracingLayout.from_dict(result['analysis_layout'])
    maximum=max_bay_spacing(context or {'source_preset':result.get('source_preset',{})})
    spacing=spacing_assessment(physical,maximum) if physical is not None else {'passes':True,'enforced':False}
    passes = all(row["passes"] for row in checks.values()) and (not criteria.include_serviceability or drift["passes"]) and stable and spacing['passes']
    request = {"source_hash": result["source_hash"], "sections": {g: candidate_name(c) for g, c in selections.items()},
               "section_properties": {g: candidate_properties(c, criteria) for g, c in selections.items()},
               "net_fracture_multiplier": .85, "criteria": asdict(criteria),
               "cost_rates": {**rates, 'braced_panel_installation': installation['rate_per_panel']}}
    request['stressed_skin'] = result.get('stressed_skin', {}).get('settings', {})
    request['global_sls_basis'] = sway_basis
    request['max_braced_bay_spacing']=maximum
    if result.get('mullion_basis'):
        request['mullion_basis']=result['mullion_basis']
        request['section_properties']['end_post']=asdict(selections['end_post'].check_properties())
    if context is not None:
        result['brace_policy'] = policy_evidence(context, selections)
        request['brace_policy'] = result['brace_policy']
    result["design_request"] = request
    result["design_request_hash"] = hashlib.sha256(json.dumps(request, sort_keys=True, allow_nan=False).encode()).hexdigest()
    result["section_checks"] = checks
    preliminary = any(c.get('method') == 'assigned_half_reference' for check in checks.values() for c in check.get('case_checks', []))
    brace_screen = any(c.get('method') == 'gross_yield_brace_tension' for check in checks.values() for c in check.get('case_checks', []))
    result["design"] = {"passes": passes, 'preliminary_reference_checks': preliminary,
                        'layout_spacing':spacing,
                        'strut_sizing_basis':'ULS interaction and required local member/roof serviceability; no global-sway stiffness floor; actual EA retained in analysis',
                        'include_serviceability':criteria.include_serviceability,
                        'design_basis':'Strength + serviceability' if criteria.include_serviceability else 'Strength only',
                        'serviceability_passes':sls_pass,
                        'preliminary_brace_tension_checks': brace_screen,
                        "member_checks_pass": all(r["passes"] for r in checks.values()),
                        "global_sls": drift, "taut_tangent_verified": stable,
                        'slack_play': play['evidence'],
                        "estimated_cost": sum(row["total"] for row in costs), "cost_items": costs,
                        'materials_and_fittings_cost': materials_and_fittings,
                        'installation_cost': installation['total'],
                        'installed_braced_panel_count': installation['panel_count'],
                        "cost_model": {"stock_lengths_m": UB_STOCK_LENGTHS_M, "hollow_waste_factor": 1.07,
                                       'braced_panel_installation_rate': installation['rate_per_panel'],
                                       "legacy_fitting_price_catalogue": END_WALL_ROD_TURNBUCKLES,
                                       "selected_scheduled_fittings": {g: {key: c.get(key) for key in
                                           ("forged_turnbuckle", "grade_s_turnbuckle", "fitting_unit_cost")}
                                           for g, c in selections.items() if isinstance(c, dict) and c.get("component_type") == "scheduled_rod"}},
                        "basis": "Project-schedule rods/ropes use supplied factored brace strengths directly. Other members use the preliminary member engine with explicit 0.85 net-fracture multiplier and configured net-area/kt/effective lengths. One section per steel strut/post/brace group. Strong-axis bending orientation. Axial actions govern over the recorded 3D load combinations; local member self-weight and purlin bending use their scoped checks. Connections and the full production combination set remain separate.",
                        "cost_basis": "Current Costing rates, exact brace lengths, 7% hollow waste, rolled stock rounding, one listed turnbuckle per rod. Angle fittings, other connections and supplier availability/quotes are not priced."}
    result["limitations"] = [line for line in result["limitations"] if not line.startswith(("Forces are analysis demands only.", "Fixed pinned-panel layout;"))]
    result["limitations"] += [result["design"]["basis"], result["design"]["cost_basis"]]
    if result.get('mullion_basis'):
        result['limitations'].append(result['mullion_basis']['basis']+' Wind bending and bracing axial are independently enveloped conservatively, not assumed to be coincident governing cases. The assumed mid-height flybrace must be provided; open ends receive no such credit.')
        result['limitations']=[line for line in result['limitations'] if not line.startswith('Dedicated axial struts of the selected section run')]
        result['limitations'].append('Intermediate mullions remain axial-only in the global 3D network. The attached local combined checks use the main mullion calculation and native catalogue properties, independently of generic bracing tension connection deductions. They do not add post bending stiffness to the spatial analysis.')
    if not criteria.include_serviceability:
        result['limitations'].append('STRENGTH ONLY: global movement and local member/purlin deflections are calculated and reported, but excluded from member selection and design pass/fail. Serviceability acceptance is not established. Strength, required ratings, material rules and taut-brace stability still apply.')
    result['limitations'].append('Steel strut/post compression uses gross member-body area with local/global buckling and bending checks. Tension connection net-area/kt assumptions do not reduce body compression area; actual holes/end details remain separate.')
    if brace_screen:
        result['limitations'].append('Steel-brace tension uses the user-adopted PRELIMINARY member-only 0.9 Ag fy screen. Net fracture, connection eccentricity, holes/threads, fittings and anchors remain separate. Scheduled rod strengths already include 0.9 and are not factored twice.')
    if context is not None:
        result['limitations'].append(result['brace_policy']['basis'])
    from bracing_components import WIRE_ASSUMED_STIFFNESS, WIRE_ASSUMED_SOURCE
    assumed_groups = [g for g, c in selections.items() if isinstance(c, dict)
                      and c.get('component_type') == 'wire_rope'
                      and c.get('rating', {}).get('stiffness_basis') == WIRE_ASSUMED_STIFFNESS]
    result['design']['assumed_wire_stiffness_groups'] = assumed_groups
    if assumed_groups:
        result['limitations'].append(WIRE_ASSUMED_SOURCE+'. Groups: '+', '.join(assumed_groups)+
            '. May underpredict rope stretch; omitted cladding stiffness does not establish conservatism. '
            + ('Enabled roof/wall diaphragm contribution is included in SLS only on the separately recorded adopted flexibility basis.'
               if result.get('stressed_skin', {}).get('active') else 'Global diaphragm contribution remains excluded.'))
    if isinstance(selections["strut"], StrutPlan):
        old_basis, old_cost = result["design"]["basis"], result["design"]["cost_basis"]
        result["design"]["basis"] = "Per-location common-end purlin/added-member assemblies. Installed compression uses the project-adopted Lysaght May 1999 p20 simplified method with Metroll 2023 p7 Ag/web-axis radius, or declared Nc/Nex ratings as selected. Purlin member tension uses 0.9 min(Ag fy, 0.85 kt An fu), G450 lower-bound fy450/fu480 and configured net-area/kt assumptions, or declared Nt/source as selected. Independent bending/axial envelopes. Added unsheeted cold-formed plies use separate residual ratings. Back-to-back roof sharing only when declared. Common-end/collector force transfer is assumed; its connection capacity is unchecked. No composite bending I."
        result["design"]["cost_basis"] = "Incremental added struts/purlins only; existing roof purlins already costed. New purlins use 7% waste at the captured purlin rate. Other unlisted connections excluded."
        result["limitations"] = [line for line in result["limitations"] if not line.startswith("Dedicated axial struts") and line not in (old_basis, old_cost)]
        result["limitations"] += [result["design"]["basis"], result["design"]["cost_basis"]]
        if any(not run.get('preliminary') for run in result.get('purlin_beams', {}).get('runs', {}).values()):
            result["limitations"].append("Sheeted additions use a separate first-order roof-run beam model with entered finite cladding/fastener stiffness and independent single-span rotations. That local model adds no global diaphragm/portal bending credit; any SLS stressed-skin option is recorded separately. Redistributed M/V member ratings and cladding transfer connection capacities remain explicit; missing stiffness or member capacities cannot pass.")
        if preliminary:
            from purlin_shared_design import BASIS
            result['design']['basis'] += ' '+BASIS
            result['limitations'].append(BASIS)
    result['design']['cost_basis'] += (' Installation is a flat $'+f"{installation['rate_per_panel']:g}"+
        ' per physical X-braced panel, once for the crossed pair; other labour/connection costs remain excluded.')
    result['limitations'].append(installation['allowance'])
    return result


def solve_selection(context, selections, criteria, rates, reuse_cases=None):
    local = {**context, **{g: selections[g] for g in ("roof_brace", "wall_brace", "end_brace", "canopy_brace")
                           + PORTAL_BRACE_GROUPS if g in selections}}
    result = analyse_context(local, selections["strut"], selections["end_post"], reuse_cases=reuse_cases)
    return attach_design(result, selections, criteria, rates, context)


def stiffness_ceiling(context, result, group, candidates, rates):
    """Largest eligible, priced EA, not an ever-growing requested stiffness."""
    values = []
    for candidate in candidates:
        ea = candidate_stiffness(candidate)
        if not math.isfinite(ea) or ea <= 0 or not policy_eligible(context, group, candidate):
            continue
        try:
            group_cost(result, group, candidate, rates)
        except AnalysisError:
            continue
        values.append(ea)
    return max(values, default=0.)


def bounded_stiffness_floor(previous, current, factor, ceiling):
    return min(ceiling, max(previous, current*factor))


def fast_sway_proposal(context, result, chosen, candidates, criteria, rates, tried):
    """Response-guided upgrade with the legacy ranking as a fallback."""
    if context.get('stiffness_screening', True):
        from bracing_stiffness_screen import sway_proposal
        if all(g in tried or g not in chosen or not group_members(result, g)
               for g in SWAY_BRACE_GROUPS):
            tried.clear()
        try:
            proposal, evidence = sway_proposal(context, result, chosen, candidates, criteria, rates, tried)
        except AnalysisError as error:
            proposal, evidence = None, {'fallback': str(error)}
        result['stiffness_screening_trial'] = evidence
        if proposal:
            return proposal
    return legacy_sway_proposal(context, result, chosen, candidates, criteria, rates, tried)


def legacy_sway_proposal(context, result, chosen, candidates, criteria, rates, tried):
    """One cheap, targeted brace change after local strength sizing.

    Compare the cheapest stiffer choice in each brace group by incremental cost
    per relative EA gain. This is a ranking heuristic, not an assumed sway gain.
    Posts/struts are not inflated as a proxy for longitudinal sway. Every proposal
    requires an exact full-model strength/SLS/stability check.
    """
    # Round-robin sweeps may revisit a group at its new EA. A single increment
    # in each group is not catalogue exhaustion and must not force extra bays.
    # The caller's exact-solve budget bounds the number of successful proposals.
    for sweep in range(2):
        proposals = []
        for group in SWAY_BRACE_GROUPS:
            if group in tried or group not in chosen or not group_members(result, group):
                continue
            current_ea = candidate_stiffness(chosen[group])
            ranked = []
            for candidate in candidates[group]:
                ea = candidate_stiffness(candidate)
                if ea <= current_ea+1e-9 or not policy_eligible(context, group, candidate):
                    continue
                try:
                    cost = group_cost(result, group, candidate, rates)['total']
                except AnalysisError:
                    continue
                if all(row['passes'] for row in group_assessments(result, group, candidate, criteria)):
                    ranked.append((candidate_rank(context, group, candidate), cost, -ea, candidate_name(candidate), candidate))
            if ranked:
                rank, cost, neg_ea, name, candidate = min(ranked, key=lambda row: row[:4])
                old_cost = group_cost(result, group, chosen[group], rates)['total']
                gain = (-neg_ea/current_ea)-1.
                proposals.append((rank-candidate_rank(context, group, chosen[group]),
                                  max(0., cost-old_cost)/gain, cost-old_cost,
                                  SWAY_BRACE_GROUPS.index(group), group, candidate))
        if proposals:
            *_, group, candidate = min(proposals, key=lambda row: row[:4])
            tried.add(group)
            return {**chosen, group: candidate}, group
        if not sweep and tried:
            tried.clear()
        else:
            break
    return None


def reduce_verified_cost(context, best, candidates, criteria, rates, budget, progress=None):
    """Try cheaper/preferred groups with exact acceptance, within the solve cap."""
    trials, used, screens = [], 0, 0
    for group in active_groups(best[1]):
        result, chosen = best
        if used >= budget:
            break
        if (group == 'strut' and isinstance(chosen[group], StrutPlan)) or not group_members(result, group):
            continue
        current_rank = (candidate_rank(context, group, chosen[group]), group_cost(result, group, chosen[group], rates)['total'])
        ranked = []
        for candidate in candidates[group]:
            if candidate_name(candidate) == candidate_name(chosen[group]) or not policy_eligible(context, group, candidate):
                continue
            try:
                rank = (candidate_rank(context, group, candidate), group_cost(result, group, candidate, rates)['total'])
            except AnalysisError:
                continue
            if rank >= current_rank:
                continue
            screens += 1
            if all(row['passes'] for row in group_assessments(result, group, candidate, criteria)):
                ranked.append((*rank, candidate_name(candidate), candidate))
        for *_, candidate in sorted(ranked, key=lambda r: r[:3]):
            if used >= budget:
                break
            if progress:
                progress(used, budget, 'Checking lower-cost '+group+': '+candidate_name(candidate))
            trial = {**chosen, group: candidate}
            used += 1
            record = {'group': group, 'section': candidate_name(candidate), 'adopted': False}
            try:
                checked = solve_selection(context, trial, criteria, rates)
            except AnalysisError as error:
                record['analysis_error'] = str(error)
            else:
                record.update(passes=checked['design']['passes'], estimated_cost=checked['design']['estimated_cost'],
                              global_sls_ratio=checked['design']['global_sls']['ratio'])
                if checked['design']['passes'] and selection_score(context, trial, checked) < selection_score(context, chosen, result):
                    best = checked, trial
                    record['adopted'] = True
            trials.append(record)
            if record['adopted']:
                break
    return best, trials, used, screens


def balance_compact_braces(context, best, candidates, criteria, rates, floors, budget, progress=None):
    """At most two coupled checks: lighter roof, stronger wall, balanced ends.

    One-sided roofs can make roof-only stiffness growth expensive. This stencil
    moves stiffness to the vertical wall while reducing roof/end material. It
    does not assume a stiffness gain; every proposal gets a full exact check.
    """
    result, chosen = best
    groups = ('roof_brace', 'wall_brace', 'end_brace')
    if budget <= 0 or any(not group_members(result, group) for group in groups):
        return best, [], 0
    def priced(group, test):
        rows = []
        for option in candidates[group]:
            ea = candidate_stiffness(option)
            if ea < floors.get(group, 0)-1e-9 or not test(option, ea) or not policy_eligible(context, group, option):
                continue
            try:
                cost = group_cost(result, group, option, rates)['total']
            except AnalysisError:
                continue
            rows.append((candidate_rank(context, group, option), cost, -ea, candidate_name(option), option))
        return rows
    from bracing_policy import family
    roof_ea = candidate_stiffness(chosen['roof_brace'])
    roofs = priced('roof_brace', lambda option, ea: family(option) == family(chosen['roof_brace']) and ea < roof_ea-1e-9)
    walls = priced('wall_brace', lambda option, ea: ea > candidate_stiffness(chosen['wall_brace'])+1e-9)
    if not roofs or not walls:
        return best, [], 0
    # Nearest smaller roof section avoids a speculative large stiffness loss.
    roof = min(roofs, key=lambda row: (row[0], row[2], row[1], row[3]))[-1]
    wall = min(walls, key=lambda row: row[:4])[-1]
    ends = priced('end_brace', lambda option, ea: ea >= candidate_stiffness(roof)-1e-9)
    records, used = [], 0
    for *_, end in sorted(ends, key=lambda row: row[:4]):
        if used >= min(2, budget):
            break
        proposal = {**chosen, 'roof_brace': roof, 'wall_brace': wall, 'end_brace': end}
        group_cost_old = sum(group_cost(result, g, chosen[g], rates)['total'] for g in groups)
        expected = result['design']['estimated_cost']-group_cost_old+sum(group_cost(result,g,proposal[g],rates)['total'] for g in groups)
        if expected >= best[0]['design']['estimated_cost']-1e-9:
            continue
        if progress:
            progress(used, min(2,budget), 'Checking lighter roof / stronger wall brace balance')
        used += 1
        record = {'sections': {g: candidate_name(proposal[g]) for g in groups}, 'adopted': False}
        try:
            checked = solve_selection(context, proposal, criteria, rates)
        except AnalysisError as error:
            record['analysis_error'] = str(error)
        else:
            record.update(passes=checked['design']['passes'], estimated_cost=checked['design']['estimated_cost'],
                          global_sls_ratio=checked['design']['global_sls']['ratio'])
            if checked['design']['passes'] and selection_score(context, proposal, checked) < selection_score(context, best[1], best[0]):
                best = checked, proposal
                record['adopted'] = True
        records.append(record)
    return best, records, used


def deferred_transverse_sls(search=None, *, final_context=None):
    """Search without transverse SLS cases, then check the adopted design with them.

    Transverse SLS frame sway is report only. Within a search it can affect
    only member local-deflection rows and taut-rod stability, so it is solved
    once on the adopted design rather than on every trial. If that final
    check fails where the reduced search passed, the search is repeated with
    every case. The returned result always contains every case.
    ``final_context(context, *extra_outputs)`` rebuilds the context for the
    final check when the search also returns a layout.
    """
    if search is None:
        return lambda function: deferred_transverse_sls(function, final_context=final_context)

    @functools.wraps(search)
    def run(context, selections, candidates, criteria, rates, *args, **kwargs):
        from bracing_analysis_desktop import transverse_sls
        if context.get('transverse_sls_deferred') or not any(transverse_sls(c) for c in context['cases']):
            return search(context, selections, candidates, criteria, rates, *args, **kwargs)
        reduced = {**context, 'cases': [c for c in context['cases'] if not transverse_sls(c)],
                   'transverse_sls_deferred': True}
        outcome = search(reduced, selections, candidates, criteria, rates, *args, **kwargs)
        result, chosen, rest = outcome[0], outcome[1], tuple(outcome[2:])
        final = final_context(context, *rest) if final_context is not None else context
        # The search result is for these sections and layout: solve only the deferred cases.
        try:
            full = solve_selection(final, chosen, criteria, rates,
                                   reuse_cases={c['case']['name']: c for c in result['cases']})
            final_error = None
        except AnalysisError as error:
            # e.g. a Ws-only case with no gravity leaves an unstable rod set.
            full, final_error = None, str(error)
        deferral = {'deferred_cases': len(context['cases'])-len(reduced['cases']),
                    'final_check_passes': bool(full and full['design']['passes']),
                    **({'final_check_error': final_error} if final_error else {})}
        if full is None or (result['design']['passes'] and not full['design']['passes']):
            outcome = search({**context, 'transverse_sls_deferred': True}, selections, candidates, criteria, rates, *args, **kwargs)
            result, chosen, rest = outcome[0], outcome[1], tuple(outcome[2:])
            result['transverse_sls_search'] = {**deferral, 'repeated_with_all_cases': True}
            return (result, chosen, *rest)
        # Keep the search evidence; analysis and checks come from the full solve.
        for key, value in result.items():
            full.setdefault(key, value)
        full['transverse_sls_search'] = {**deferral, 'repeated_with_all_cases': False}
        return (full, chosen, *rest)
    return run


@deferred_transverse_sls
def auto_size(context, selections, candidates, criteria, rates, progress=None, max_solves=18,
              *, fast=False, initial_result=None, retain_brace_stiffness=False):
    """Cheapest candidates for successive demand envelopes; always reanalyse.

    Returns the lowest-cost passing reanalysed candidate found. Stiffness floors
    may rise for brace/post groups to meet global SLS. Struts have no global
    sway stiffness floor and retain their ULS/local checks. No global optimum claim.
    """
    if isinstance(selections["strut"], StrutPlan):
        from bracing_mixed_sizing import auto_size_mixed
        return auto_size_mixed(context, selections, candidates, criteria, rates, progress, max_solves,
                               fast=fast, initial_result=initial_result, retain_brace_stiffness=retain_brace_stiffness)
    current = dict(selections)
    result = initial_result if initial_result is not None else solve_selection(context, current, criteria, rates)
    initial = result
    from bracing_sizing_diagnostics import require_layout_spacing
    require_layout_spacing(result)
    solves = 1
    groups = active_groups(current)
    ceilings = {g: stiffness_ceiling(context, result, g, candidates[g], rates) for g in groups}
    best = (result, dict(current)) if result["design"]["passes"] else None
    trace, visited, floors = [], set(), {group: 0. for group in groups}
    if retain_brace_stiffness:
        floors.update({g: candidate_stiffness(current[g]) for g in ('roof_brace', 'wall_brace', 'end_brace')})
    screens = 0
    local_rounds, sway_tried, sway_adjustments = 0, set(), []
    stiffness_screen_trials = []
    for iteration in range(max_solves-1):
        if progress:
            progress(iteration, max_solves, f"Sizing round {iteration+1}; best verified cost: " + (f"${best[0]['design']['estimated_cost']:,.2f}" if best else "none yet"))
        key = selected_key(current)
        visited.add(key)
        trace.append({"sections": dict(zip(groups, key)), "passes": result["design"]["passes"],
                      "estimated_cost": result["design"]["estimated_cost"], "global_sls_ratio": result["design"]["global_sls"]["ratio"]})
        if result["design"]["passes"] and (best is None or selection_score(context, current, result) < selection_score(context, best[1], best[0])):
            best = (result, dict(current))
        if fast and iteration > 0 and result['design']['passes']:
            break
        if ((fast or context.get('stiffness_screening', True)) and (local_rounds or retain_brace_stiffness or not fast) and criteria.include_serviceability and result['design']['member_checks_pass']
                and result['design']['taut_tangent_verified'] and result['design']['global_sls']['ratio'] > 1):
            adjustment = fast_sway_proposal(context, result, current, candidates, criteria, rates, sway_tried)
            if 'stiffness_screening_trial' in result:
                stiffness_screen_trials.append(result.pop('stiffness_screening_trial'))
            if adjustment:
                proposed, group = adjustment
                floors[group] = max(floors[group], candidate_stiffness(proposed[group]))
                if progress:
                    progress(iteration, max_solves, 'Checking targeted SLS stiffness: '+group)
                solves += 1
                try:
                    result = solve_selection(context, proposed, criteria, rates)
                except AnalysisError as error:
                    if best:
                        break
                    raise stop_for_search(initial, result, 'Targeted brace check failed: '+str(error)) from error
                current = proposed
                sway_adjustments.append(group)
                continue
            if best:
                break
            if fast:
                raise stop_for_search(initial, result, 'Short targeted brace-stiffness checks exhausted; review the load path')
        if not result["design"]["taut_tangent_verified"]:
            # Slack rod sets return a result rather than raising; as with a
            # failed trial, keep the best verified design found so far.
            if best:
                break
            raise stop_for_search(initial, result, 'Auto-sizing requires verified taut-rod tangent stiffness for every case; review the layout/neutral rods')
        if not fast and criteria.include_serviceability and result["design"]["global_sls"]["ratio"] > 1:
            factor = min(max(result["design"]["global_sls"]["ratio"], 1.12), 2.)
            for group in groups:
                if group != 'strut':
                    floors[group] = bounded_stiffness_floor(floors[group], candidate_stiffness(current[group]), factor, ceilings[group])
        proposed = dict(current)
        for group in groups:
            if not group_members(result, group):
                continue
            ranked = []
            for candidate in candidates[group]:
                if not policy_eligible(context, group, candidate):
                    continue
                if candidate_stiffness(candidate) < floors[group]-1e-9:
                    continue
                screens += 1
                if all(row["passes"] for row in group_assessments(result, group, candidate, criteria)):
                    try:
                        ranked.append((candidate_rank(context, group, candidate), group_cost(result, group, candidate, rates)["total"], candidate_name(candidate), candidate))
                    except AnalysisError:
                        continue
            if not ranked:
                if best:
                    break
                raise stop_for_search(initial, result, f'No eligible priced candidate passes the current {group} demands and bounded stiffness floor', group, floors[group], ceilings[group])
            proposed[group] = min(ranked, key=lambda item: item[:3])[3]
        proposed_key = selected_key(proposed)
        if proposed_key == key or proposed_key in visited:
            if best:
                break
            raise stop_for_search(initial, result, 'Candidate selections repeat before a verified design is found')
        solves += 1
        local_rounds += 1
        try:
            next_result = solve_selection(context, proposed, criteria, rates)
        except AnalysisError as error:
            if best:
                break
            raise stop_for_search(initial, result, 'Candidate reanalysis failed: '+str(error)) from error
        current, result = proposed, next_result
    if result["design"]["passes"] and (best is None or selection_score(context, current, result) < selection_score(context, best[1], best[0])):
        best = (result, dict(current))
    if best is None:
        raise stop_for_search(initial, result, 'No verified member/SLS sizing found within the search limit')
    best, reductions, extra, reduction_screens = reduce_verified_cost(context, best, candidates, criteria, rates, 0 if fast else max_solves-solves, progress)
    solves += extra
    best[0]["sizing_search"] = {"method": "Cost-ranked grouped demand-envelope iteration with exact reanalysis",
                                "scope": "Passing candidate found, ranked by project brace-family preferences then estimated cost; not a global catalogue/layout optimum",
                                "candidate_screens": screens+reduction_screens, "rounds": trace, "max_solves": max_solves,
                                'exact_solves': solves, 'stiffness_ceilings_n': ceilings, 'cost_reduction_trials': reductions,
                                'strut_sizing_basis':'ULS interaction and required local member/roof serviceability; no global-sway stiffness floor'}
    best[0]['sizing_search']['fast'] = fast
    best[0]['sizing_search']['targeted_sway_adjustments'] = sway_adjustments
    best[0]['sizing_search']['stiffness_screening'] = stiffness_screen_trials
    if fast:
        best[0]['sizing_search']['scope'] = 'Demand-ranked local section sizing; stop after a verified resized pass; optional cost-polish analyses omitted. No least-cost claim.'
    return best
