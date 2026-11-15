"""User-adopted brace family preferences and mandatory unclad-wall rods."""
from portalcalc.bracing.analysis import AnalysisError
from portalcalc.bracing.layout import BRACE_GROUPS, brace_group

PROJECT_USES = ('unspecified', 'agricultural', 'commercial_industrial')


def policy(context):
    value = context.get('brace_policy', {}).get('project_use', 'unspecified')
    if value not in PROJECT_USES:
        raise AnalysisError('Unknown bracing project use: '+str(value))
    return {'schema': 'portalcalc.brace-policy.v1', 'project_use': value,
            'unclad_walls_require_rod': True}


def family(candidate):
    if isinstance(candidate, dict):
        if candidate.get('component_type') == 'wire_rope':
            return 'wire'
        if candidate.get('component_type') == 'scheduled_rod' or 'ROD' in candidate.get('label', '').upper():
            return 'rod'
    return 'other'


def group_faces(context, group):
    if group not in BRACE_GROUPS:
        return []
    layout = context['layout']
    return sorted({layout.panels[key].face for key in layout.pinned if brace_group(layout.panels[key].face) == group})


def rod_faces(context, group):
    # Unknown cladding does not imply an open wall. Desktop contexts explicitly
    # provide all four faces; numerical fixtures may have only front/back flags.
    clad = {'front_end': context.get('front_clad'), 'back_end': context.get('back_clad'),
            **context.get('face_cladding', {})}
    return [face for face in group_faces(context, group) if not face.startswith('roof') and clad.get(face) is False]


def crane_rods(context, group):
    """Crane buildings: rod bracing in every brace group (impact and dynamic crane loads)."""
    return bool(context.get('crane_racking_basis')) and group in BRACE_GROUPS


def eligible(context, group, candidate):
    return (not rod_faces(context, group) and not crane_rods(context, group)) or family(candidate) == 'rod'


def candidate_rank(context, group, candidate):
    if group not in BRACE_GROUPS or not group_faces(context, group) or rod_faces(context, group) or crane_rods(context, group):
        return 0
    use = policy(context)['project_use']
    if use == 'unspecified':
        return 0
    preferred = 'wire' if use == 'agricultural' else 'rod'
    return 0 if family(candidate) == preferred else 1 if family(candidate) in {'wire', 'rod'} else 2


def selection_score(context, selections, result):
    return (sum(candidate_rank(context, group, selections[group]) for group in BRACE_GROUPS if group in selections),
            result['design']['estimated_cost'])


def apply_group_rule(context, group, candidate, rows):
    faces = rod_faces(context, group)
    crane = crane_rods(context, group)
    if (not faces and not crane) or family(candidate) == 'rod':
        return rows
    reason = ('Project rule: rod required on unclad wall(s): '+', '.join(faces) if faces
              else 'Project rule: rod bracing required in a crane building')
    return [{**row, 'passes': False, 'ratio': None, 'mode': reason, 'case': 'Material rule',
             'policy_violation': reason,
             'case_checks': [*row.get('case_checks', []), {'case': 'Material rule', 'ratio': None,
                                                        'passes': False, 'mode': reason}]} for row in rows]


def evidence(context, selections):
    result = policy(context)
    result['crane_building_rods_only'] = bool(context.get('crane_racking_basis'))
    result['groups'] = {group: {'faces': group_faces(context, group), 'rod_required_faces': rod_faces(context, group),
                               'rod_required_crane': crane_rods(context, group),
                               'selected_family': family(selections[group]),
                               'preference_rank': candidate_rank(context, group, selections[group])}
                        for group in BRACE_GROUPS if group in selections}
    result['basis'] = ('Agricultural: wire preferred; commercial/industrial: rod preferred; unspecified: cost only. '
                       'Preferences rank eligible, passing, priced candidates before estimated cost. '
                       'Rod is mandatory for a wall-brace group containing an installed brace on an unclad face, '
                       'and for every brace group in a crane building (impact and dynamic crane loads). '
                       'One section per roof/side-wall/end-wall group; the mandatory wall rule applies to the whole affected group. '
                       'Missing rope stiffness or prices never count as a passing, zero-cost option.')
    return result
