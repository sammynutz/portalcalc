"""Combined 2D portal and 3D bracing sizing, ranked by total estimated cost.

Headless orchestration over ``MainWindow``, like the other study backends:

1. Size the 2D portal with no bracing restraint. This is the upper bound.
2. Find and size a practical 3D bracing layout for those portal sections.
3. Build bracing stiffness options: the practical design with its roof and
   end-wall braces stepped to stiffer catalogue rods, and continuous eave
   roof-girder layouts, which reach every frame. Each option is checked in 3D
   and reports the frame restraint it gives (``frame_restraint``).
4. For each option that restrains every interior frame: adopt that restraint,
   re-size the 2D portal from the unrestrained sections, then recheck the same
   bracing with the re-sized portal. If the rechecked restraint is lower, adopt
   it and recheck the 2D design. Bracing that no longer passes is rejected.
5. Rank by 2D building cost plus 3D bracing cost and leave the window on the
   cheapest verified option.

Costs are the existing estimates: the main building costing total and the 3D
bracing materials, fittings and panel installation. The option set is bounded;
this is not a global optimum.
"""
import copy
import time

from bracing_analysis import AnalysisError
from bracing_analysis_desktop import prepare_context
from bracing_design import DesignCriteria, auto_size, solve_selection
from bracing_layout import BracingLayout
from bracing_layout_search import find_layout, practical_seeds
from bracing_restraint import OPTION, floor_candidates
from bracing_spacing import max_bay_spacing

BRACE_GROUPS = ('roof_brace', 'wall_brace', 'end_brace')
# Groups whose stiffness restrains frame sway: roof girders and end-wall bracing.
RESTRAINT_GROUPS = ('roof_brace', 'end_brace')
RECHECK_TOLERANCE = 1e-3


def _sections(window):
    return {group: combo.currentText() for _, combo, group in window.active_auto_size_group_specs()}


def _frame_state(window):
    specs = window.active_auto_size_group_specs()
    ratio, _, _ = window.auto_size_overall_ratio(specs)
    return {'cost': (window.costing_summary or {}).get('total_cost'), 'ratio': ratio,
            'acceptable': window.auto_size_design_acceptable(specs, ratio), 'sections': _sections(window)}


WEB_STATE = ('auto_web_profile_assignments', 'auto_web_joint_doubler_sides', 'auto_web_assignment_basis')


TOPOLOGY_STATE = ('_panel_count_override', '_panel_topology_cache_signature', '_panel_topology_cache_count')


def capture_state(window):
    """Preset plus what the auto-size holds outside it: the truss web
    assignments and the main truss panel count (topology) it chose."""
    return {'preset': copy.deepcopy(window.collect_preset_data()),
            **{key: copy.deepcopy(getattr(window, key, {})) for key in WEB_STATE},
            'panel_topology': {key: copy.deepcopy(getattr(window, key, None)) for key in TOPOLOGY_STATE}}


def restore_state(window, state, exact=False):
    """Re-apply a captured state and solve.

    ``exact=True`` also restores the main truss panel count, so the window
    holds exactly the captured frame (with its web assignments, which are per
    panel member). Otherwise apply_preset_data clears the panel count and the
    solve uses the default count for the restored truss depth: a warm start
    of sections for a new auto-size, which re-chooses the panel count.
    """
    # apply_preset_data resets the topology suppression (it sets it only for a
    # future-canopy design); keep a caller's hold, e.g. the optimiser's.
    held = getattr(window, '_suppress_panel_topology_search', False)
    window.apply_preset_data(copy.deepcopy(state['preset']))
    window._suppress_panel_topology_search = bool(held or getattr(window, '_suppress_panel_topology_search', False))
    for key in WEB_STATE:
        setattr(window, key, copy.deepcopy(state[key]))
    topology = state.get('panel_topology') if exact else None
    if topology is not None:
        for key, value in topology.items():
            setattr(window, key, copy.deepcopy(value))
    previous = getattr(window, '_preserve_web_assignments_on_solve', False)
    previous_topology = window._suppress_panel_topology_search
    window._preserve_web_assignments_on_solve = bool(state['auto_web_profile_assignments'])
    if topology is not None:
        window._suppress_panel_topology_search = True   # hold the restored count
    try:
        window.generate_and_solve()
    finally:
        window._preserve_web_assignments_on_solve = previous
        window._suppress_panel_topology_search = previous_topology


def _size_frame(window):
    # The same per-auto-size load-vector cache as Optimise Structure
    # (run_structure_optimisation): combination loads are applied once per
    # geometry/spring/case signature instead of on every trial solve.
    previous = getattr(window, 'optimisation_load_vector_cache', None)
    window.optimisation_load_vector_cache = {}
    try:
        outcome = window.auto_size_current_preset(show_completion=False)
    finally:
        window.optimisation_load_vector_cache = previous
    return {**_frame_state(window), 'verified': bool(outcome.get('verified'))}


def _workspace(window, context):
    """The 3D bracing workspace's own setup, not shown: the user's saved ratings,
    prices, brace policy and per-location strut plans."""
    from bracing_analysis_ui import BracingAnalysisDialog
    return BracingAnalysisDialog(context, window.catalog, saved=copy.deepcopy(window.bracing_3d_settings or {}))


def _candidates(window, context):
    dialog = _workspace(window, context)
    try:
        return {'strut': dialog.strut_profiles, 'end_post': dialog.post_profiles,
                **{group: dialog.component_editor.brace_candidates() for group in BRACE_GROUPS},
                # Portal frame brace columns/beams: catalogue UBs (used only with portal braces).
                **{group: list(dialog.portal_profiles) for group in ('portal_brace_column', 'portal_brace_beam')},
                # Open canopy roof braces: the same brace catalogue, sized as their own group.
                **({'canopy_brace': dialog.component_editor.brace_candidates()} if context.get('canopy_brace') else {})}
    finally:
        dialog.deleteLater()


def _start_selections(window, context, candidates):
    dialog = _workspace(window, context)
    try:
        return dialog.selections(context)
    finally:
        dialog.deleteLater()


def _criteria(window, context):
    """The workspace's saved design criteria, so a search checks what the workspace checks."""
    dialog = _workspace(window, context)
    try:
        return dialog.criteria()
    finally:
        dialog.deleteLater()


def store_calculation(window):
    """Run the workspace's own Analyse and check on the stored bracing and save it.

    The 3D workspace then reopens on this result (utilisation view, members
    clickable) instead of an unanalysed layout. Returns the result, or raises
    AnalysisError if the workspace check could not run.
    """
    window.generate_and_solve()
    context = prepare_context(window, allow_unconfigured=True)
    dialog = _workspace(window, context)
    try:
        dialog.run_analysis()
        if dialog.result is None:
            raise AnalysisError(f"3D workspace check failed: {dialog.status_label.text()}")
        window.bracing_3d_calculation_cache = dialog.calculation_cache()
        return dialog.result
    finally:
        dialog.deleteLater()


def check_saved_locations(window):
    """Refuse to run on bracing locations saved for different building dimensions.

    The 3D context then drops the saved blocked panels, so a search could brace
    a panel that cannot be braced (and fail when saving it to the old layout).
    """
    saved = window.bracing_location_layout
    if saved is None:
        return
    from bracing_layout import carry_over
    layout, current = BracingLayout.from_dict(saved), window.current_bracing_layout_geometry()
    # A canopy-only change keeps the main building's saved panels (carry_over).
    if layout.geometry != current and carry_over(layout, current) is None:
        raise AnalysisError(
            "The building dimensions have changed since the bracing locations were saved, so the blocked "
            "panels no longer line up with the building. Open the 3D bracing workspace, click 'Use current "
            "dimensions and clear saved locations', block the panels that can't be braced again and save, "
            "then run the optimiser again. Nothing has been changed.")


def _with_layout(context, pinned):
    from bracing_layout import canopy_bracing
    layout = context['layout']
    # Open canopies are braced in the end roof-braced bays (plus spacing-rule bays).
    # ``pinned`` is the whole selection (X panels and any portal braces).
    pinned = set(pinned) | canopy_bracing(layout.geometry, layout.blocked, pinned, max_bay_spacing(context))
    return {**context, 'layout': BracingLayout(layout.geometry, sorted(layout.all_blocked), sorted(pinned))}


def _store_bracing(window, context, pinned, selections):
    """Save the bracing as the workspace's Save and close would."""
    from bracing_layout import canopy_bracing
    pinned = set(pinned) | canopy_bracing(context['layout'].geometry, context['layout'].blocked, pinned,
                                          max_bay_spacing(context))
    dialog = _workspace(window, context)
    try:
        editor = dialog.layout_editor
        editor.model = BracingLayout(editor.model.geometry, sorted(editor.model.all_blocked), sorted(pinned))
        editor.remember()
        dialog.component_editor.refresh_bracing(set(pinned))
        dialog.adopt_sections(selections)
        window.bracing_3d_settings = dialog.snapshot()
        window.bracing_location_layout = dialog.layout_snapshot()
    finally:
        dialog.deleteLater()


def _stepped(selections, candidates, steps, context=None):
    """Selections with brace groups ``{group: count}`` sizes stiffer within their family.

    Stays within the brace family (rod, wire, angle) and the project brace
    policy, so e.g. rods required on unclad walls remain rods. None if a group
    has no stiffer option.
    """
    from bracing_design import candidate_name, candidate_stiffness
    from bracing_policy import eligible, family
    out = dict(selections)
    for group, count in steps.items():
        if not count:
            continue
        current = selections[group]
        ordered = sorted((o for o in candidates[group] if family(o) == family(current)
                          and (context is None or eligible(context, group, o))), key=candidate_stiffness)
        names = [candidate_name(o) for o in ordered]
        if candidate_name(current) not in names or names.index(candidate_name(current))+count >= len(ordered):
            return None
        out[group] = ordered[names.index(candidate_name(current))+count]
    return out


def _check(context, pinned, selections, criteria):
    local = _with_layout(context, pinned)
    try:
        result = solve_selection(local, selections, criteria, local['cost_rates'])
    except AnalysisError as error:
        return None, {'passes': False, 'bracing_cost': None, 'restraint_kn_mm': 0., 'adoptable': False,
                      'restraint_reason': None, 'analysis_error': str(error)}
    restraint = result.get('frame_restraint') or {}
    return result, {'passes': result['design']['passes'], 'bracing_cost': result['design']['estimated_cost'],
                    'restraint_kn_mm': restraint.get('adoptable_kn_mm', 0.), 'adoptable': bool(restraint.get('adoptable')),
                    'restraint_reason': restraint.get('reason')}


def bracing_options(window, context, candidates, criteria, base, max_options):
    """(name, pinned panels, selections) options, cheapest load path first."""
    layout, chosen = base
    options = [('Practical layout', set(layout.pinned), chosen)]
    for steps in (1, 2):
        stepped = _stepped(chosen, candidates, {group: steps for group in RESTRAINT_GROUPS}, context)
        if stepped is not None:
            options.append((f'Practical layout, roof/end braces +{steps} rod size', set(layout.pinned), stepped))
    for reason, selected in practical_seeds(_with_layout(context, ()), set()):
        if 'girder' not in reason or len(options) >= max_options:
            continue
        local = _with_layout(context, selected)
        try:
            result, sized = auto_size(local, chosen, candidates, criteria, local['cost_rates'], fast=True)
        except AnalysisError:
            continue
        if result['design']['passes']:
            options.append((reason, set(selected), sized))
            stepped = _stepped(sized, candidates, {group: 1 for group in RESTRAINT_GROUPS}, local)
            if stepped is not None and len(options) < max_options:
                options.append((reason+', roof/end braces +1 rod size', set(selected), stepped))
    return options[:max_options]


def run(window, *, max_options=6, progress=None):
    """Run the combined sizing on the window's current preset; returns the report."""
    started = time.perf_counter()
    note = progress or (lambda message: None)
    check_saved_locations(window)
    criteria = DesignCriteria()
    window.eave_x_restraint_combo.setCurrentText('None')
    note('Sizing the unrestrained 2D portal')
    unrestrained = _size_frame(window)
    if not unrestrained['verified']:
        raise AnalysisError('The unrestrained 2D portal could not be sized')
    baseline = capture_state(window)

    note('Finding practical 3D bracing for the unrestrained portal')
    context = prepare_context(window, allow_unconfigured=True)
    candidates = _candidates(window, context)
    start = _start_selections(window, context, candidates)
    result, chosen, layout = find_layout(_with_layout(context, ()), start, candidates, criteria, context['cost_rates'])
    options = bracing_options(window, context, candidates, criteria, (layout, chosen), max_options)

    rows = []
    for name, pinned, selections in options:
        note(f'Option: {name}')
        # Every option starts from the unrestrained sizing (a warm start).
        restore_state(window, baseline)
        context = prepare_context(window, allow_unconfigured=True)
        row = {'option': name, 'panels': len(pinned), 'sections': {g: getattr(s, 'name', None) or s['label'] for g, s in selections.items()}}
        result, check = _check(context, pinned, selections, criteria)
        row.update(check)
        if not check['passes']:
            row.update(status=('Analysis failed: '+check['analysis_error']) if check.get('analysis_error')
                       else 'Bracing does not pass with the unrestrained portal')
            rows.append(row)
            continue
        if not check['adoptable']:
            # No interior-frame restraint: the unrestrained portal stands.
            row.update(status='No frame restraint credit', frame=unrestrained, total_cost=unrestrained['cost']+check['bracing_cost'])
            rows.append(row)
            continue
        _store_bracing(window, context, pinned, selections)
        record, reason = window.adopt_bracing_3d_restraint(result)
        if record is None:
            row.update(status=f'Restraint not adoptable: {reason}')
            rows.append(row)
            continue
        window.eave_x_restraint_combo.setCurrentText(OPTION)
        frame = _size_frame(window)
        recheck, verified = [], False
        for _ in range(3):
            # The re-sized portal changes how load is shared with the bracing:
            # recheck the same bracing and credit only what it now provides.
            local = prepare_context(window, allow_unconfigured=True)
            rechecked, again = _check(local, pinned, selections, criteria)
            recheck.append({'restraint_kn_mm': again['restraint_kn_mm'], 'passes': again['passes'],
                            'bracing_cost': again['bracing_cost']})
            if not again['passes'] or not again['adoptable']:
                break
            record, _ = window.adopt_bracing_3d_restraint(rechecked)
            if record is None:
                break
            window.generate_and_solve()
            state = _frame_state(window)
            if state['acceptable']:
                verified = (window.bracing_3d_restraint_status or {}).get('state') == 'applied'
                break
            frame = _size_frame(window)
        frame = {**frame, **_frame_state(window)}
        final = recheck[-1]
        row.update(frame=frame, recheck=recheck, adopted_kn_mm=(record or {}).get('stiffness_kn_mm'),
                   bracing_cost=final['bracing_cost'],
                   status='Verified' if verified else 'Not verified after recheck',
                   total_cost=(frame['cost']+final['bracing_cost']) if verified else None,
                   state=capture_state(window) if verified else None)
        rows.append(row)

    unrestrained_total = min((r['total_cost'] for r in rows if r.get('status') == 'No frame restraint credit'), default=None)
    verified = [r for r in rows if r.get('total_cost') is not None]
    best = min(verified, key=lambda r: r['total_cost'], default=None)
    restore_state(window, best['state'] if best and best.get('state') else baseline)
    for r in rows:
        r.pop('state', None)
    return {'schema': 'portalcalc.combined-sizing.v1', 'elapsed_seconds': time.perf_counter()-started,
            'unrestrained_frame': unrestrained, 'unrestrained_total_cost': unrestrained_total,
            'options': rows, 'best': None if best is None else best['option'],
            'basis': __doc__.strip().split('\n\n')[0]}


def report_text(report):
    lines = ['COMBINED 2D PORTAL + 3D BRACING SIZING', report['basis'], '',
             f"Unrestrained 2D portal: ${report['unrestrained_frame']['cost']:,.2f}; "
             f"sections {report['unrestrained_frame']['sections']}", '',
             'Option | panels | bracing $ | adopted kN/mm | frame $ | total $ | status']
    def money(value):
        return '-' if value is None else format(value, ',.2f')

    for row in report['options']:
        frame = row.get('frame') or {}
        adopted = row.get('adopted_kn_mm')
        lines.append(f"{row['option']} | {row['panels']} | {money(row.get('bracing_cost'))} | "
                     f"{'-' if adopted is None else format(adopted, '.4f')} | {money(frame.get('cost'))} | "
                     f"{money(row.get('total_cost'))} | {row['status']}")
    lines += ['', f"Best: {report['best'] or 'none verified'}; {report['elapsed_seconds']:.0f} s."]
    return '\n'.join(lines)
