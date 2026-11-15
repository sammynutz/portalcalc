"""Adopting the 3D bracing network's frame restraint in the 2D portal.

Qt-free. The 3D analysis reports the secant roof-level sway restraint the
bracing gives each frame (``bracing_analysis_desktop.frame_restraint_summary``).
The 2D portal analyses one representative frame, so it adopts the least
interior-frame value as one spring on the mean eave movement.

A record is valid only for the bracing it came from. The hard fingerprint
covers layout geometry, pinned panels, brace/strut/post selections and
declared rope EA: any change drops the credit. Portal sections, loads, wind
and canopies change the restraint less directly. When they differ the credit
is kept but flagged for a 3D recheck (soft fingerprint), which lets the 2D
frame be re-sized with the restraint it was given.
"""
import copy
import hashlib
import json

SCHEMA = 'portalcalc.bracing-3d-restraint.v1'
OPTION = '3D bracing model'
HARD_SETTING_KEYS = ('roof_brace_label', 'wall_brace_label', 'end_brace_label', 'strut_section',
                     'end_post_section', 'mixed_struts', 'strut_members')
# The restraint selection itself must not make its own record stale.
RESTRAINT_GEOMETRY_KEYS = ('end_wall_bracing', 'end_wall_braced_sides', 'end_wall_brace_size')
GROUPS = ('roof_brace', 'wall_brace', 'end_brace')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def hard_fingerprint(layout, settings):
    layout, settings = layout or {}, settings or {}
    wires = {d: (rating or {}).get('ea_kn') for d, rating in (settings.get('wire_rope_ratings') or {}).items()}
    return _digest({'geometry': layout.get('geometry'), 'pinned': sorted(layout.get('pinned_panels') or []),
                    **{key: settings.get(key) for key in HARD_SETTING_KEYS}, 'wire_ea_kn': wires})


def soft_fingerprint(preset):
    preset = preset or {}
    geometry = {key: value for key, value in (preset.get('geometry') or {}).items()
                if key not in RESTRAINT_GEOMETRY_KEYS}
    return _digest({'geometry': geometry, **{key: preset.get(key) for key in ('sections', 'loads', 'wind', 'canopies')}})


def brace_ea_n(candidate):
    """Axial stiffness EA (N) of a brace option, as the 3D model uses it."""
    from portalcalc.bracing.design import candidate_stiffness
    return float(candidate_stiffness(candidate))


def adoption_record(result, layout, settings, preset):
    """Restraint record from a checked 3D result, or None with a reason."""
    summary = result.get('frame_restraint')
    if not summary:
        return None, 'The 3D result has no transverse cases to derive frame restraint from'
    if not result.get('design', {}).get('passes'):
        return None, 'The 3D bracing design does not pass its scoped checks'
    if not result.get('design', {}).get('taut_tangent_verified', True):
        return None, 'The 3D bracing stability is unverified (neutral rods)'
    if not summary['adoptable']:
        return None, summary['reason']
    sections = result.get('design_request', {}).get('section_properties', {})
    floors = {}
    for group in GROUPS:
        props = sections.get(group) or {}
        if props.get('type') == 'wire_rope':
            ea = float((props.get('rating') or {}).get('ea_kn') or 0.)*1000
        elif 'area_mm2' in props:
            ea = 200000.*float(props['area_mm2'])
        else:
            ea = None
        floors[group] = {'section': result['design_request']['sections'].get(group), 'ea_n': ea}
    governing = summary['governing']
    return {'schema': SCHEMA, 'stiffness_kn_mm': summary['adoptable_kn_mm'],
            'governing_frame': governing['frame']+1, 'governing_case': governing['case'],
            'governing_level': governing.get('level'),
            'hard_fingerprint': hard_fingerprint(layout, settings),
            'soft_fingerprint': soft_fingerprint(preset),
            'brace_ea_floors': floors,
            'portal_sections': copy.deepcopy((preset or {}).get('sections')),
            'basis': summary['basis']}, None


def restraint_status(record, layout, settings, preset):
    """('applied'|'recheck'|'stale'|'missing', stiffness kN/mm, message)."""
    if not record or record.get('schema') != SCHEMA:
        return 'missing', 0., 'No adopted 3D bracing restraint. Analyse and Save and close in the 3D bracing workspace.'
    if record.get('hard_fingerprint') != hard_fingerprint(layout, settings):
        return 'stale', 0., ('Adopted 3D restraint no longer matches the bracing layout or sections; '
                             'no restraint applied. Re-run the 3D bracing analysis.')
    k = float(record.get('stiffness_kn_mm') or 0.)
    if record.get('soft_fingerprint') != soft_fingerprint(preset):
        return 'recheck', k, (f'3D restraint {k:.3f} kN/mm applied, but portal sections, loads or wind have changed '
                              'since it was derived. Recheck the 3D bracing analysis before relying on this result.')
    return 'applied', k, (f"3D restraint {k:.3f} kN/mm applied on the mean eave movement "
                          f"(frame {record.get('governing_frame')}, {record.get('governing_case')}).")


def floor_candidates(candidates, record):
    """Restrict brace options to at least the stiffness the 2D frame was credited with."""
    if not record:
        return candidates
    restricted = dict(candidates)
    for group, floor in (record.get('brace_ea_floors') or {}).items():
        ea = (floor or {}).get('ea_n')
        if ea and group in restricted:
            restricted[group] = [c for c in restricted[group] if brace_ea_n(c) >= ea*(1-1e-9)]
    return restricted
