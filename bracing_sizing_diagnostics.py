"""Actionable sizing stops, with unchanged-input analysis retained for review."""
from bracing_analysis import AnalysisError


def governing_check_row(check):
    rows = [r for r in check.get('case_checks', []) if r.get('case') == check.get('case')]
    exact = [r for r in rows if r.get('mode') == check.get('mode') and r.get('ratio') == check.get('ratio')]
    return next(iter(exact), None) or max(rows, key=lambda r: float('inf') if r.get('ratio') is None else r['ratio'], default={})


def check_type(result, row):
    if row.get('ratio') is None:
        return 'Missing check'
    level = row.get('level') or next((c['case']['level'] for c in result['cases'] if c['case']['name'] == row.get('case')), None)
    return {'ULS': 'Strength', 'SLS': 'Local deflection'}.get(level, 'Member check')


def result_summary(result):
    """Separate global movement from every member's ULS/SLS/missing checks."""
    checks = result.get('section_checks', {})
    counts = {'Strength': 0, 'Local deflection': 0, 'Missing check': 0, 'Member check': 0}
    failed = 0
    for check in checks.values():
        kinds = {check_type(result, row) for row in check.get('case_checks', [])
                 if row.get('included_in_design',True) and (row.get('ratio') is None or row['ratio'] > 1)}
        if check.get('ratio') is None or check['ratio'] > 1:
            failed += 1
            if not kinds:
                kinds.add(check_type(result, check))
        for kind in kinds:
            counts[kind] += 1  # A member can fail strength and local deflection.
    sls = result['design']['global_sls']
    node = result['nodes'][sls['node']]
    movement = (f"Overall longitudinal movement: {'FAIL' if sls['ratio'] > 1 else 'PASS'} - "
                f"{sls['movement_mm']:.1f} mm / {sls['limit_mm']:.1f} mm limit "
                f"({sls['ratio']:.3f} x limit); {node['label']}.")
    if not result['design'].get('include_serviceability',True):
        movement = (f"Overall longitudinal movement: REPORT ONLY - {sls['movement_mm']:.1f} mm / "
                    f"{sls['limit_mm']:.1f} mm reference ({sls['ratio']:.3f} x reference); "
                    f"{'exceeds reference' if sls['ratio']>1 else 'within reference'}; serviceability excluded.")
    members = (f"Members: {failed} failing/unverified of {len(checks)}; "
               f"{counts['Strength']} strength failures; {counts['Local deflection']} local deflection failures; "
               f"{counts['Missing check']} missing checks.")
    if counts['Member check']:
        members += f" {counts['Member check']} other member failures."
    if not result['design']['taut_tangent_verified']:
        members += ' Taut-brace stability is unverified.'
    faces = {face: sum(p.startswith(face+'/') for p in result['analysis_layout']['pinned_panels'])
             for face in ('left_wall', 'right_wall', 'front_end', 'back_end')}
    layout = 'Wall X panels: '+', '.join(f"{label} {faces[face]}" for face, label in
        (('left_wall', 'left'), ('right_wall', 'right'), ('front_end', 'front'), ('back_end', 'back')))+'.'
    retained = bool(result.get('sizing_failure'))
    lines = (["AUTO-SIZING STOPPED - original input selections shown; no sizing changes adopted."] if retained else [])
    lines += [movement, members, layout]
    diaphragm = result.get('stressed_skin', {})
    if diaphragm.get('settings', {}).get('enabled'):
        settings = diaphragm['settings']
        lines.append(f"PRELIMINARY SLS stressed skin: c {settings['flexibility_mm_per_kn']:g} mm/kN ({settings['shear_direction']}); "
                     f"bare/with-sheet maximum longitudinal movement {diaphragm['bare_max_longitudinal_mm']:.1f}/{diaphragm['enhanced_max_longitudinal_mm']:.1f} mm. "
                     'ULS uses conventional bracing only; diaphragm connections remain unchecked.')
    if not result['design'].get('include_serviceability',True):
        outside = sum(any(row.get('level')=='SLS' and not row.get('included_in_design',True) and (row['ratio'] is None or row['ratio']>1)
                          for row in check['case_checks']) for check in checks.values())
        lines.insert(0,'STRENGTH ONLY - bracing serviceability is reported, not accepted. Main mullion prerequisites still apply.' if result.get('mullion_basis') else 'STRENGTH ONLY - serviceability is reported, not accepted.')
        lines.append(f'Reported local SLS: {outside} members outside reference limits or unverified; excluded from design.')
    peaks = {}
    for case in result['cases']:
        if case['case']['level']=='ULS':
            for kind,value in case.get('load_path',{}).get('peak_brace_tension_n',{}).items():
                peaks[kind] = max(peaks.get(kind,0.),value)
    if peaks:
        lines += ['ULS brace peak tension: '+', '.join(f"{label} {peaks.get(kind,0.)/1000:.1f} kN"
                  for kind,label in (('roof_brace','roof'),('wall_brace','side-wall'),('end_brace','front/back end-wall')))+'.']
    drags=[c['case']['roof_drag'] for c in result['cases'] if c['case']['level']=='ULS' and c['case'].get('roof_drag')]
    if drags:
        peak=max(drags,key=lambda d:abs(d['signed_y_force_kn']))
        lines += [f"External roof drag included: up to {abs(peak['signed_y_force_kn']):.2f} kN; Cf {peak['cf']:g}, projected loaded area {peak['projected_area_m2']:.1f} m2. See full results for the load strip and basis."]
        if any(d['preliminary_open_sided'] for d in drags):
            lines.append('PRELIMINARY open-side assumption: enclosed-building external-roof friction method; open-roof underside/exposed-member drag is not assessed.')
    lines.append('Demand scope: end-wall pressure'+(' plus external roof friction' if drags else ' only; roof friction excluded')+'; normal roof/side-wall pressures, wall friction and transverse-wind cases are outside this 3D solve.')
    return {'text': '\n'.join(lines), 'movement_text': movement, 'member_counts': counts,
            'failed_members': failed, 'wall_panel_counts': faces, 'retained_inputs': retained}


class SizingStopped(AnalysisError):
    def __init__(self, message, result, diagnostic):
        super().__init__(message)
        self.result = {**result, "sizing_failure": diagnostic}
        self.diagnostic = diagnostic


def stop_for_search(initial, screening, reason, group=None, floor=None, ceiling=None):
    """An exhausted bounded search is not a declaration of infeasibility."""
    sls = screening['design']['global_sls']
    retained = initial['design']['global_sls']
    movement_failure = sls.get('included_in_design',True) and sls['ratio'] > 1
    names = {'strut': 'Struts', 'end_post': 'End posts', 'roof_brace': 'Roof braces',
             'wall_brace': 'Side-wall braces', 'end_brace': 'End-wall braces'}
    label = names.get(group, 'Bracing system')
    kind = 'longitudinal_strut' if group == 'strut' else group
    identifiers = {m['id'] for m in screening['cases'][0]['members'] if m['kind'] == kind}
    checks = [c for k,c in screening['section_checks'].items() if k in identifiers]
    worst = max(checks, key=lambda c: float('inf') if c.get('ratio') is None else c['ratio'], default={})
    title = (f"global longitudinal serviceability still exceeds the limit (screening UC {sls['ratio']:.3f}); "
             if movement_failure else '')+reason
    diagnostic = {'status': 'STOPPED_NO_ADOPTION', 'member': 'group/'+(group or 'system'),
                  'location': label, 'section': initial['design_request']['sections'].get(group, 'Original selections'),
                  'screening_section': screening['design_request']['sections'].get(group, 'Screening selections'),
                  'reason': title, 'case': sls['case'] if movement_failure else worst.get('case', sls['case']), 'axial_kn': None,
                  'input_target': 'layout' if movement_failure or group is None else 'group', 'group': group,
                  'input_column': None, 'rejected_alternatives': [], 'stiffness_floor_rejections': 0,
                  'stiffness_floor_n': floor, 'catalogue_stiffness_ceiling_n': ceiling,
                  'action': ('The bounded search found no verified design. This is not proof that the layout cannot work. '
                             'Review the retained input checks, screening movement, available section sizes and missing rating/price data. '
                             'No failed screening selections have been adopted.'),
                  'retained_analysis': 'Original input selections; no failed sizing candidate adopted'}
    diagnostic.update(global_sls=dict(retained), screening_global_sls=dict(sls),
                      governing_node=initial['nodes'][retained['node']])
    return SizingStopped(f'{label}: {title}', initial, diagnostic)


def describe_location(location, geometry):
    # The geometry schema exposes roof stations as a derived property, so use
    # the actual member x/span to name eave/ridge positions without raw IDs.
    x, span = location["x_mm"], geometry["span_mm"]
    face = "left eave" if abs(x) < 1e-4 else "right eave" if abs(x-span) < 1e-4 else "ridge" if abs(x-span/2) < 1e-4 else f"roof line {location['line']+1}"
    return f"Bay {location['bay']+1}, {face}"


def failure_record(option, checked):
    failures = [r for r in checked["case_checks"] if r.get('included_in_design',True) and (r["ratio"] is None or r["ratio"] > 1)]
    # Keep one record per reason/component/state; many wind branches can have
    # the same missing datum. Prefer the largest physical demand for that row.
    records = {}
    for row in failures:
        kind = "missing_data" if row["ratio"] is None else "capacity"
        key = row["mode"], row.get("component"), kind
        force = row.get("axial_kn", row.get("tension_kn"))
        record = {"arrangement": option.arrangement, "section": option.name, "mode": row["mode"],
                  "case": row["case"], "component": row.get("component"), "state": kind,
                  "ratio": row["ratio"], "axial_kn": force}
        metric = abs(force) if force is not None else row["ratio"] or 0.
        previous = records.get(key)
        if previous is None or metric > previous[0]:
            records[key] = metric, record
    return [record for _, record in records.values()]


def stop_for_strut(initial_result, current_result, assembly, failures, floor_rejections):
    identifier = assembly.location["id"]
    label = describe_location(assembly.location, initial_result["analysis_layout"]["geometry"])
    # Diagnose the failed screening state, while retaining the original input
    # analysis for review; no locally changed sizing candidate is adopted.
    original = initial_result["section_checks"].get(identifier, {})
    evaluation = current_result["section_checks"].get(identifier, original)
    rows = [r for r in evaluation.get("case_checks", []) if r.get('included_in_design',True)]
    primary = next((r for r in rows if r["ratio"] is None), None)
    primary = primary or max((r for r in rows if r["ratio"] is not None), key=lambda r:r["ratio"], default={})
    reason = primary.get("mode", "No candidate satisfies the current capacity/stiffness constraints")
    force = primary.get("axial_kn", primary.get("tension_kn"))
    if "tension rating required" in reason.lower():
        target, column = "purlin", 3
        title = "purlin tensile capacity Nt / source is missing or does not match"
        action = "Components / ratings > Purlin axial ratings: select Calculated member capacity, or enter valid Declared Nt kN and its source for the current basis."
    elif primary.get("method") == "coupled_purlin_beam":
        target, column = "beam", 1 if not primary.get("coupling_defined") else 3
        title = reason
        action = "Components / ratings > Sheeted-purlin beam: review the cladding/load-pickup inputs and member M+/M-/V ratings."
    else:
        target, column = "purlin", 7
        title = reason
        action = "Review the member's case checks and rejected alternatives below; revise the applicable ratings, arrangement or layout."
    if floor_rejections and not failures:
        title = "all strut alternatives are below the current SLS stiffness requirement"
        action = "Review global SLS movement, available strut sizes and the pinned bracing layout."
    if primary.get("ratio") is not None and primary["ratio"] > 1:
        title += f" (UC {primary['ratio']:.3f})"
    suffix = f" ({abs(force):.3f} kN {'tension' if force >= 0 else 'compression'})" if force is not None else ""
    screening_sls = current_result.get('design', {}).get('global_sls', {})
    global_stop = (floor_rejections > 0 and screening_sls.get('included_in_design',True) and screening_sls.get('ratio', 0) > 1
                   and not any(r['state'] == 'missing_data' for r in failures))
    retained_sls = initial_result.get('design', {}).get('global_sls', {})
    if global_stop:
        target, column = 'layout', None
        title = f"global longitudinal serviceability exceeds the limit (screening SLS UC {screening_sls['ratio']:.3f})"
        action = ('Review the deformed shape and the pinned roof/wall bracing load path. '
                  'The search exhausted eligible strut alternatives while enforcing its SLS stiffness floor. '
                  'This is not proof that a larger purlin will resolve the system movement; '
                  'check the bracing arrangement and available stiffness before further section sizing.')
        suffix = f"; original inputs move {retained_sls['movement_mm']:.1f} mm against {retained_sls['limit_mm']:.1f} mm"
    diagnostic = {"status": "STOPPED_NO_ADOPTION", "member": identifier, "location": label,
                  "section": original.get("section", assembly.name), "reason": title,
                  "screening_section": evaluation.get("section", assembly.name),
                  "case": primary.get("case", "-"), "axial_kn": force, "action": action,
                  "input_target": target, "input_column": column,
                  "rating_key": assembly.location["purlin"]["basis_key"],
                  "rejected_alternatives": failures, "stiffness_floor_rejections": floor_rejections,
                  "retained_analysis": "Original input selections; no failed sizing candidate adopted"}
    if global_stop:
        node = retained_sls['node']
        diagnostic.update(global_sls=dict(retained_sls), screening_global_sls=dict(screening_sls),
                          governing_node=initial_result['nodes'][node], case=retained_sls['case'])
    return SizingStopped(f"{'Bracing system' if global_stop else label}: {title}{suffix}", initial_result, diagnostic)


def diagnostic_text(diagnostic):
    d = diagnostic
    lines = ["AUTO-SIZING STOPPED - INPUT SELECTIONS RETAINED", f"Member: {d['location']} | {d['member']}",
             f"Section: {d['section']}", f"Reason: {d['reason']}", f"Source case: {d['case']}"]
    if d["axial_kn"] is not None:
        lines.append(f"Axial demand: {d['axial_kn']:.3f} kN (+ tension)")
    if d["screening_section"] != d["section"]:
        lines.append(f"Screening section at the stop: {d['screening_section']}")
    if d.get('stiffness_floor_n') is not None:
        lines.append(f"Screening EA target {d['stiffness_floor_n']/1000:.1f} kN; largest eligible priced catalogue EA {d['catalogue_stiffness_ceiling_n']/1000:.1f} kN.")
    if d.get('global_sls'):
        sls = d['global_sls']
        node = d['governing_node']
        lines += [f"Governing system movement: {node.get('label', 'Node '+str(sls['node']+1))}",
                  f"Original-input longitudinal SLS movement {sls['movement_mm']:.3f} mm / limit {sls['limit_mm']:.3f} mm; UC {sls['ratio']:.3f}.",
                  f"Screening SLS UC at the stop: {d['screening_global_sls']['ratio']:.3f}. Layout has not been changed."]
    lines += [d["action"], "The results below are the original input selection's analysis, not an optimised design.", "", "REJECTED ALTERNATIVES"]
    grouped = {}
    for row in d["rejected_alternatives"]:
        key = row["arrangement"], row["mode"], row["state"]
        record = grouped.setdefault(key, {"sections": set(), "example": row})
        record["sections"].add(row["section"])
    for (arrangement, reason, state), record in grouped.items():
        row = record["example"]
        uc = "UNVERIFIED" if row["ratio"] is None else f"UC {row['ratio']:.3f}"
        lines.append(f"{arrangement} ({len(record['sections'])} section option(s)): {reason}; {uc}; {row['case']}")
    if d["stiffness_floor_rejections"]:
        lines.append(f"{d['stiffness_floor_rejections']} alternatives below the current SLS stiffness requirement.")
    return "\n".join(lines)
