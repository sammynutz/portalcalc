"""Project limit on roof transfer between complete, wall-anchored roof bays.

This is an installation/load-path rule, not a wind or member capacity formula.
No five consecutive building bays may lack a full roof-braced bay with an
aligned side-wall brace. Narrow continuous eave strips alone are not anchors.
"""
from bracing_analysis import AnalysisError

DEFAULT_MAX_BAY_SPACING=5


def max_bay_spacing(context):
    settings=(context.get('source_preset',{}).get('bracing',{}).get('analysis_3d') or {}).get('layout_search') or {}
    value=context.get('max_braced_bay_spacing',settings.get('max_braced_bay_spacing',DEFAULT_MAX_BAY_SPACING))
    if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=50:
        raise AnalysisError('Maximum braced-bay spacing must be a whole number from 1 to 50')
    return value


def bay_panels(layout,bay):
    roof={f'roof/bay/{bay}/strip/{i}' for i in range(len(layout.geometry.roof_stations)-1)}
    walls={f'{face}/bay/{bay}' for face in ('left_wall','right_wall')}
    return roof,walls


def spacing_assessment(layout, maximum=DEFAULT_MAX_BAY_SPACING, selected=None):
    selected=set(layout.pinned if selected is None else selected)
    count=len(layout.geometry.bay_stations_mm)-1
    anchors=[]
    for bay in range(count):
        roof,walls=bay_panels(layout,bay)
        if roof<=selected and walls & selected:
            anchors.append(bay)
    runs=[]
    for left,right in zip([-1]+anchors,anchors+[count]):
        if right-left>1:
            runs.append({'first_bay':left+2,'last_bay':right,'unbraced_bays':right-left-1})
    violations=[r for r in runs if r['unbraced_bays']>=maximum]
    return {'passes':not violations,'enforced':True,'max_braced_bay_spacing':maximum,
        'anchor_bays':[b+1 for b in anchors],
        'maximum_unbraced_run':max((r['unbraced_bays'] for r in runs),default=0),
        'violations':violations,
        'basis':f'Project load-path rule: at least one complete roof-braced bay aligned with side-wall bracing in every {maximum} consecutive building bays. A continuous eave strip alone does not count. Blocked panels remain excluded.'}


def repair_spacing(context, selected):
    """Add a short compliant chain using O(bays × allowed gap) planning.

    Preserve selected/required panels. Minimise added anchor bays, then added
    panels; prefer later intermediate anchors for a regular maximum-gap chain.
    No structural analysis or brute-force panel combinations are used here.
    """
    layout=context['layout'];selected=set(selected);maximum=max_bay_spacing(context)
    check=spacing_assessment(layout,maximum,selected)
    if check['passes']:
        return selected
    count=len(layout.geometry.bay_stations_mm)-1
    preferred={p.split('/')[0] for p in selected if p.startswith(('left_wall/','right_wall/'))}
    options={};available_panels=layout.available
    for bay in range(count):
        roof,walls=bay_panels(layout,bay)
        if not roof<=available_panels:
            continue
        available=walls & available_panels
        if not available:
            continue
        aligned=available & selected
        aligned=aligned or {p for p in available if p.split('/')[0] in preferred} or available
        options[bay]=roof|aligned
    best={}
    for bay,panels in options.items():
        added=panels-selected
        cost=(int(bool(added)),len(added),-bay)
        candidates=[]
        if bay<maximum:
            candidates.append((cost,(bay,)))
        for previous in range(max(0,bay-maximum),bay):
            if previous in best:
                score,path=best[previous]
                candidates.append((tuple(a+b for a,b in zip(score,cost)),path+(bay,)))
        if candidates:
            best[bay]=min(candidates)
    finishes=[value for bay,value in best.items() if count-1-bay<maximum]
    if not finishes:
        raise AnalysisError(f'Blocked panels prevent a complete roof/side-wall braced bay within every {maximum} bays. Review the blocked locations or spacing limit.')
    _,path=min(finishes)
    for bay in path:
        selected |= options[bay]
    if not spacing_assessment(layout,maximum,selected)['passes']:
        raise AnalysisError('Braced-bay spacing plan did not satisfy the project limit')
    return selected


def spacing_text(check):
    bays=', '.join(map(str,check['anchor_bays'])) or 'none'
    text=f"Braced-bay spacing: {'PASS' if check['passes'] else 'FAIL'}; maximum {check['max_braced_bay_spacing']} bays; anchored roof bays {bays}."
    if check['violations']:
        text+=' Add intermediate aligned roof/wall bracing: '+', '.join(f"bays {r['first_bay']}–{r['last_bay']} ({r['unbraced_bays']} consecutive unbraced bays)" for r in check['violations'])+'.'
    return text
