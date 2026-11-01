"""User-adopted brace family preferences and mandatory unclad-wall rods."""
from bracing_analysis import AnalysisError

PROJECT_USES = ('unspecified', 'agricultural', 'commercial_industrial')
BRACE_GROUPS = ('roof_brace', 'wall_brace', 'end_brace')


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
    return sorted({layout.panels[key].face for key in layout.pinned
                   if ('roof_brace' if layout.panels[key].face.startswith('roof') else
                       'end_brace' if layout.panels[key].face in {'front_end', 'back_end'} else 'wall_brace') == group})


def rod_faces(context, group):
    # Unknown cladding does not imply an open wall. Desktop contexts explicitly
    # provide all four faces; numerical fixtures may have only front/back flags.
    clad = {'front_end': context.get('front_clad'), 'back_end': context.get('back_clad'),
            **context.get('face_cladding', {})}
    return [face for face in group_faces(context, group) if not face.startswith('roof') and clad.get(face) is False]


def eligible(context, group, candidate):
    return not rod_faces(context, group) or family(candidate) == 'rod'


def candidate_rank(context, group, candidate):
    if group not in BRACE_GROUPS or not group_faces(context, group) or rod_faces(context, group):
        return 0
    use = policy(context)['project_use']
    if use == 'unspecified':
        return 0
    preferred = 'wire' if use == 'agricultural' else 'rod'
    return 0 if family(candidate) == preferred else 1 if family(candidate) in {'wire', 'rod'} else 2


def selection_score(context, selections, result):
    return (sum(candidate_rank(context, group, selections[group]) for group in BRACE_GROUPS),
            result['design']['estimated_cost'])


def apply_group_rule(context, group, candidate, rows):
    faces = rod_faces(context, group)
    if not faces or family(candidate) == 'rod':
        return rows
    reason = 'Project rule: rod required on unclad wall(s): '+', '.join(faces)
    return [{**row, 'passes': False, 'ratio': None, 'mode': reason, 'case': 'Material rule',
             'policy_violation': reason,
             'case_checks': [*row.get('case_checks', []), {'case': 'Material rule', 'ratio': None,
                                                        'passes': False, 'mode': reason}]} for row in rows]


def evidence(context, selections):
    result = policy(context)
    result['groups'] = {group: {'faces': group_faces(context, group), 'rod_required_faces': rod_faces(context, group),
                               'selected_family': family(selections[group]),
                               'preference_rank': candidate_rank(context, group, selections[group])}
                        for group in BRACE_GROUPS}
    result['basis'] = ('Agricultural: wire preferred; commercial/industrial: rod preferred; unspecified: cost only. '
                       'Preferences rank eligible, passing, priced candidates before estimated cost. '
                       'Rod is mandatory for a wall-brace group containing an installed brace on an unclad face. '
                       'One section per roof/side-wall/end-wall group; the mandatory wall rule applies to the whole affected group. '
                       'Missing rope stiffness or prices never count as a passing, zero-cost option.')
    return result
