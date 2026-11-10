"""Frontier optimiser: the cheapest combined 2D portal and 3D bracing.

The frame and the bracing interact only through the restraint the bracing
gives each frame (k, kN/mm on the mean eave movement).

1. Bracing side. Check bracing options in 3D with the unrestrained frame: the
   practical layout and the eave-girder layouts, each with stiffer roof and
   end-wall rod steps. Keep the frontier: the cheapest bracing for each level
   of restraint delivered.
2. Frame side. The cheapest frame for a restraint k comes from the 2D
   auto-size with that restraint (``trial_sway_restraint_kn_mm``), warm-started
   from the unrestrained sizing. It is needed because restraint changes more
   than member sizes: on factory defaults the restrained optimum also deepens
   the truss and re-sizes the webs, which a quick check of lighter members
   cannot reproduce. Each frame is sized for the bracing's restraint less a
   margin, which allows for the recheck with the re-sized frame.
3. Bound. Frame cost cannot rise with more restraint, so sizing the frame once
   at the stiffest frontier bracing gives a floor. A bracing option whose cost
   plus that floor cannot beat the best verified total is skipped without
   sizing a frame for it. Otherwise the floor frame is checked with the option
   first: if it verifies, it meets the bound, so no 2D re-size is needed.
4. Verify. Set the sized frame, recheck its bracing in 3D with it, adopt the
   restraint it now delivers and run the exact 2D check. The unrestrained
   frame with the cheapest passing bracing needs no restraint and is the
   starting incumbent.

Costs are the existing estimates: the main building costing total and the 3D
bracing materials, fittings and panel installation. Bracing options are
bounded and the 2D auto-size is itself a bounded search; this is not a global
optimum.
"""
import time

from bracing_analysis_desktop import prepare_context
from bracing_analysis import AnalysisError
from bracing_design import auto_size
from bracing_layout_search import find_layout, practical_seeds
from bracing_restraint import OPTION
import bracing_parallel as parallel
import combined_sizing as shared

# (roof brace, end-wall brace) size steps tried on every bracing layout, each
# within its brace family and the project brace policy.
ROD_STEPS = ((0, 0), (1, 0), (0, 1), (1, 1), (2, 2))
SIZING_MARGIN = .05   # frames are sized for the delivered restraint / (1 + margin)
MAX_GIRDER_LAYOUTS = 4


def bracing_points(context, candidates, criteria, practical, progress=None):
    """3D-checked bracing options with the unrestrained frame.

    The girder-layout sizings and the stepped-rod checks are independent, so
    each batch runs in worker processes (``bracing_parallel``); the result is
    the same as checking them one after another.
    """
    note = progress or (lambda message: None)
    layout, chosen = practical
    layouts = [('Practical layout', set(layout.pinned), chosen)]
    seeds = [(reason, set(selected)) for reason, selected in practical_seeds(shared._with_layout(context, ()), set())
             if 'girder' in reason]
    with parallel.Runner(context, max(len(seeds), len(ROD_STEPS)*(MAX_GIRDER_LAYOUTS+1))) as runner:
        sized = runner.map(parallel.size_task, [(selected, chosen, candidates, criteria) for _, selected in seeds],
                           lambda done, total: note(f'Sizing girder bracing layouts in 3D ({done}/{total})'))
        for (reason, selected), selections in zip(seeds, sized):
            if len(layouts) > MAX_GIRDER_LAYOUTS:
                break
            if selections is not None:
                layouts.append((reason, selected, selections))
        options = []
        for name, pinned, selections in layouts:
            for roof, end in ROD_STEPS:
                stepped = shared._stepped(selections, candidates, {'roof_brace': roof, 'end_brace': end},
                                          shared._with_layout(context, pinned))
                if stepped is not None:
                    label = name if not (roof or end) else f'{name}; roof/end braces +{roof}/+{end} sizes'
                    options.append((label, pinned, stepped))
        checks = runner.map(parallel.check_task, [(pinned, stepped, criteria) for _, pinned, stepped in options],
                            lambda done, total: note(f'Checking bracing options in 3D ({done}/{total})'))
    return [{'option': label, 'pinned': pinned, 'selections': stepped, **check,
             'restraint': check['restraint_kn_mm'] if check['adoptable'] else 0.}
            for (label, pinned, stepped), check in zip(options, checks)]


def frontier(points):
    """Cheapest passing bracing for each increasing level of restraint."""
    out, best = [], -1.
    for point in sorted((p for p in points if p['passes']), key=lambda p: p['bracing_cost']):
        if point['restraint'] > best+1e-9:
            out.append(point)
            best = point['restraint']
    return out


def same_frame(a, b):
    """True when two sized frames are the same design (so verify gives the same answer)."""
    return a is b or (a.get('state') is not None and a.get('state') == b.get('state'))


def search(options, frame_for, verify, incumbent):
    """Branch-and-bound over the frontier (cheapest first).

    ``frame_for(k)`` returns the sized frame for restraint k (cost, ...);
    ``verify(frame, option)`` returns a row with ``verified``/``total``;
    ``incumbent`` is the verified unrestrained row. Returns (best, rows).

    The floor frame (sized for the stiffest bracing) is tried first with each
    option: frame cost cannot fall with less restraint, so if it verifies, no
    frame sized for that option can be cheaper and its 2D auto-size is skipped.
    """
    best, rows = incumbent, []
    restrained = [p for p in options if p['restraint'] > 0.]
    if not restrained:
        return best, rows
    floor = frame_for(restrained[-1]['restraint'])
    for option in restrained:
        if floor is None or option['bracing_cost']+floor['cost'] >= best['total']:
            rows.append({'bracing': option['option'], 'status': 'Skipped: cannot beat the best total even with the least frame cost'})
            continue
        floor_row = verify(floor, option)
        floor_row['predicted_total'] = floor['cost']+option['bracing_cost']
        if floor_row['verified']:
            if floor_row['total'] < best['total']:
                best = floor_row
            if not floor_row.get('bracing_resized'):
                # Least frame cost with the option's own bracing: exact for this option.
                floor_row['status'] += ' (least-cost frame; no re-size needed)'
                rows.append(floor_row)
                continue
        frame = frame_for(option['restraint'])
        if frame is None:
            rows.append(floor_row if floor_row['verified'] else
                        {'bracing': option['option'], 'status': 'Frame could not be sized'})
            continue
        predicted = frame['cost']+option['bracing_cost']
        if same_frame(frame, floor):
            rows.append(floor_row)   # same design, already checked
            continue
        if predicted >= best['total']:
            if floor_row['verified']:
                rows.append(floor_row)
            rows.append({'bracing': option['option'], 'frame_cost': frame['cost'], 'predicted_total': predicted,
                         'status': 'Not cheaper than the best total'})
            continue
        if floor_row['verified']:
            rows.append(floor_row)
        row = verify(frame, option)
        row['predicted_total'] = predicted
        rows.append(row)
        if row['verified'] and row['total'] < best['total']:
            best = row
    return best, rows


def optimise(window, *, progress=None):
    """Run the frontier optimiser on the window's current preset; returns the report."""
    parallel.check_not_bootstrapping()
    suspended = getattr(window, 'suspended_plotting', None)
    if suspended is None:
        return _optimise(window, progress=progress)
    with suspended():
        return _optimise(window, progress=progress)


def _optimise(window, *, progress=None):
    started = time.perf_counter()
    note = progress or (lambda message: None)
    shared.check_saved_locations(window)
    counts = {'frame_auto_size': 0, 'bracing_3d': 0}
    window.trial_sway_restraint_kn_mm = None
    window.eave_x_restraint_combo.setCurrentText('None')
    try:
        note('Sizing the unrestrained 2D frame')
        unrestrained = shared._size_frame(window)
        counts['frame_auto_size'] += 1
        if not unrestrained['verified']:
            raise AnalysisError('The unrestrained 2D frame could not be sized')
        baseline = shared.capture_state(window)

        note('Bracing options and the restraint each gives (3D)')
        context = prepare_context(window, allow_unconfigured=True)
        candidates = shared._candidates(window, context)
        start = shared._start_selections(window, context, candidates)
        # The workspace's saved criteria (slack-play limit, serviceability), so the
        # adopted design is the one the workspace's Analyse and check confirms.
        criteria = shared._criteria(window, context)
        # These analyses only rank options (their results are discarded), so
        # skip the report-only portal member end forces.
        search_context = {**context, 'search_only': True}
        _, chosen, layout = find_layout(shared._with_layout(search_context, ()), start, candidates, criteria,
                                        context['cost_rates'])
        points = bracing_points(search_context, candidates, criteria, (layout, chosen), progress=note)
        counts['bracing_3d'] += len(points)
        options = frontier(points)
        if not options:
            raise AnalysisError('No bracing option passes with the unrestrained frame')

        # The cheapest passing bracing with the unrestrained frame needs no restraint.
        cheapest = options[0]
        shared._store_bracing(window, context, cheapest['pinned'], cheapest['selections'])
        # The unrestrained frame takes no restraint credit; drop any earlier record.
        window.bracing_3d_restraint, window.bracing_3d_restraint_note = None, None
        incumbent = {'frame': 'Unrestrained sizing', 'bracing': cheapest['option'], 'verified': True,
                     'status': 'Verified, no restraint needed', 'frame_cost': unrestrained['cost'],
                     'bracing_cost': cheapest['bracing_cost'], 'total': unrestrained['cost']+cheapest['bracing_cost'],
                     'state': shared.capture_state(window), 'restraint_kn_mm': 0.}

        frames = {}

        def frame_for(restraint):
            target = restraint/(1+SIZING_MARGIN)
            key = round(target, 6)
            if key not in frames:
                note(f'Sizing the 2D frame for {target:.3f} kN/mm')
                shared.restore_state(window, baseline)
                window.trial_sway_restraint_kn_mm = target
                sized = shared._size_frame(window)
                counts['frame_auto_size'] += 1
                frames[key] = None if not (sized['verified'] and sized['acceptable']) else {
                    **sized, 'sized_for_kn_mm': target, 'state': shared.capture_state(window)}
                window.trial_sway_restraint_kn_mm = None
            return frames[key]

        def verify(frame, option):
            counts['bracing_3d'] += 1
            return verify_pair(window, frame, option, criteria, candidates)

        best, rows = search(options, frame_for, verify, incumbent)
        shared.restore_state(window, best['state'])
        note('Checking the adopted bracing in the 3D workspace')
        workspace = shared.store_calculation(window)
        design = workspace['design']
        best = {**best, 'workspace_check': {
            'passes': bool(design['passes']), 'estimated_cost': design['estimated_cost'],
            'matches': bool(design['passes']) and abs(design['estimated_cost']-best['bracing_cost']) <= .005}}
        public = lambda row: {k: v for k, v in row.items() if k != 'state'}
        return {'schema': 'portalcalc.frontier-sizing.v2', 'elapsed_seconds': time.perf_counter()-started,
                'solves': counts, 'unrestrained_frame': unrestrained,
                'bracing_points': [_public(p) for p in points], 'frontier': [_public(p) for p in options],
                'frames': [{k: v for k, v in f.items() if k != 'state'} for f in frames.values() if f],
                'incumbent': public(incumbent), 'searched': [public(r) for r in rows], 'best': public(best),
                'basis': __doc__.strip().split('\n\n')[0]}
    finally:
        window.trial_sway_restraint_kn_mm = None


def _public(point):
    return {k: v for k, v in point.items() if k not in ('pinned', 'selections')} | {
        'panels': len(point['pinned']),
        'sections': {g: getattr(s, 'name', None) or s['label'] for g, s in point['selections'].items()}}


def verify_pair(window, frame, option, criteria, candidates=None):
    """Exact check of a sized frame with one bracing option; returns a report row.

    A lighter frame sheds more load into the bracing. If the option's bracing
    no longer passes, it is re-sized upwards only (brace stiffness never below
    the option's, so its restraint is kept) before the pair is rejected.
    """
    row = {'frame': f"Sized for {frame['sized_for_kn_mm']:.3f} kN/mm", 'bracing': option['option'],
           'frame_sections': frame['sections'], 'verified': False}
    shared.restore_state(window, frame['state'])
    window.eave_x_restraint_combo.setCurrentText('None')
    window.generate_and_solve()
    context = prepare_context(window, allow_unconfigured=True)
    selections = option['selections']
    result, check = shared._check(context, option['pinned'], selections, criteria)
    if result is not None and not result['design']['taut_tangent_verified']:
        row['stability'] = result['design'].get('slack_play')
    if not check['passes'] and candidates is not None:
        local = shared._with_layout(context, option['pinned'])
        try:
            resized, selections = auto_size(local, selections, candidates, criteria, local['cost_rates'],
                                            fast=True, retain_brace_stiffness=True)
        except AnalysisError as error:
            row['resize_error'] = str(error)
        else:
            result, check = shared._check(context, option['pinned'], selections, criteria)
            row['bracing_resized'] = True
    row.update(bracing_cost=check['bracing_cost'], restraint_kn_mm=check['restraint_kn_mm'])
    if not check['passes'] or not check['adoptable']:
        row['status'] = (check.get('analysis_error') or check.get('restraint_reason') or row.get('resize_error')
                         or 'Bracing does not pass with this frame')
        return row
    shared._store_bracing(window, context, option['pinned'], selections)
    record, reason = window.adopt_bracing_3d_restraint(result)
    if record is None:
        row['status'] = f'Not adoptable: {reason}'
        return row
    window.eave_x_restraint_combo.setCurrentText(OPTION)
    window.generate_and_solve()
    state = shared._frame_state(window)
    applied = (window.bracing_3d_restraint_status or {}).get('state') == 'applied'
    row.update(frame_cost=state['cost'])
    if state['acceptable'] and applied:
        row.update(verified=True, status='Verified' + (' (bracing re-sized for this frame)' if row.get('bracing_resized') else ''),
                   total=state['cost']+check['bracing_cost'], state=shared.capture_state(window))
    else:
        row['status'] = 'Frame fails with the restraint delivered' if applied else 'Restraint not applied'
    return row


def report_text(report):
    def money(value):
        return '-' if value is None else format(value, ',.2f')
    lines = ['FRONTIER 2D PORTAL + 3D BRACING OPTIMISER', report['basis'], '',
             f"Unrestrained frame ${money(report['unrestrained_frame']['cost'])}: {report['unrestrained_frame']['sections']}",
             f"Analyses: {report['solves']['frame_auto_size']} 2D auto-sizes, {report['solves']['bracing_3d']} 3D bracing checks; "
             f"{report['elapsed_seconds']:.0f} s.", '',
             'BRACING FRONTIER | bracing $ | restraint kN/mm | panels']
    lines += [f"{p['option']} | {money(p['bracing_cost'])} | {p['restraint']:.4f} | {p['panels']}" for p in report['frontier']]
    lines += ['', 'FRAMES SIZED | for kN/mm | frame $ | sections']
    lines += [f"{f['sized_for_kn_mm']:.4f} | {money(f['cost'])} | {f['sections']}" for f in report['frames']]
    incumbent = report['incumbent']
    lines += ['', f"Incumbent: unrestrained frame + {incumbent['bracing']}, ${money(incumbent['total'])}",
              'SEARCHED | frame $ | predicted $ | verified $ | status']
    lines += [f"{r['bracing']} | {money(r.get('frame_cost'))} | {money(r.get('predicted_total'))} | "
              f"{money(r.get('total'))} | {r['status']}" for r in report['searched']]
    best = report['best']
    lines += ['', f"BEST: {best['frame']} + {best['bracing']}, ${money(best['total'])}"]
    check = best.get('workspace_check')
    if check:
        lines.append('3D workspace Analyse and check: '
                     + ('same result, saved; reopen the workspace to inspect members.' if check['matches'] else
                        f"{'PASS' if check['passes'] else 'FAIL / UNVERIFIED'}, bracing ${money(check['estimated_cost'])} "
                        f"(optimiser ${money(best['bracing_cost'])}). Review in the 3D workspace."))
    return '\n'.join(lines)
