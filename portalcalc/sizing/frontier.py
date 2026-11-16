"""Frontier optimiser: the cheapest combined 2D portal and 3D bracing.

The frame and the bracing interact only through the restraint the bracing
gives each frame (k, kN/mm on the mean eave movement).

1. Bracing side. Check bracing options in 3D with the unrestrained frame: the
   practical layout, the eave-girder layouts and full roof bracing in every
   bay, each with stiffer roof and end-wall rod steps. Each layout is sized
   from the practical sections and from the lightest rods (the cheaper pass
   is kept). Keep the frontier: the cheapest bracing for each level of
   restraint delivered.
2. Frame side. The cheapest frame for a restraint k comes from the 2D
   auto-size with that restraint (``trial_sway_restraint_kn_mm``), started
   from the unrestrained sizing's sections at the default truss panel count
   (the auto-size chooses the panel count again). It is needed because restraint changes more
   than member sizes: on factory defaults the restrained optimum also deepens
   the truss and re-sizes the webs, which a quick check of lighter members
   cannot reproduce. Each frame is sized for the bracing's restraint less a
   margin, which allows for the recheck with the re-sized frame. The frames
   for all the frontier's restraint levels are independent, so they are sized
   at once in worker processes (``parallel.frame_task``).
3. Bound. Frame cost should not rise with more restraint, so sizing the
   frame once at the stiffest frontier bracing gives a floor. A bracing
   option whose cost plus that floor cannot beat the best verified total is
   skipped without sizing a frame for it. Otherwise the floor frame is checked
   with the option first: if it verifies, it meets the bound, so no 2D re-size
   is needed. The 2D auto-size is a bounded search and can break the
   assumption: if the floor frame costs more than the unrestrained frame, the
   frame for the least restraint is sized as a probe. If it is no cheaper than
   the unrestrained frame either, restraint does not help and the search
   stops; otherwise it is the bound and each option's own frame is sized.
4. Verify. Set the sized frame exactly (sections, truss panel count and web
   assignments), recheck its bracing in 3D with it, adopt the restraint it
   now delivers and run the exact 2D check. The adopted frame is re-checked in
   2D and reported (``frame_check``). The unrestrained
   frame with the cheapest passing bracing needs no restraint and is the
   starting incumbent.

Costs are the existing estimates: the main building costing total and the 3D
bracing materials, fittings and panel installation. Bracing options are
bounded and the 2D auto-size is itself a bounded search; this is not a global
optimum.
"""
import time

from portalcalc.bracing.analysis_desktop import prepare_context
from portalcalc.bracing.analysis import AnalysisError
from portalcalc.bracing.design import auto_size
from portalcalc.bracing.layout import is_portal_brace
from portalcalc.bracing.layout_search import find_layout, full_roof_seeds, portal_brace_seeds, practical_seeds, section_signature
from portalcalc.bracing.portal_brace import PORTAL_BRACE_GROUPS
from portalcalc.bracing.restraint import OPTION
from portalcalc.bracing import parallel
from portalcalc.sizing import combined as shared

# (roof brace, end-wall brace) size steps tried on every bracing layout, each
# within its brace family and the project brace policy.
ROD_STEPS = ((0, 0), (1, 0), (0, 1), (1, 1), (2, 2))
SIZING_MARGIN = .05   # frames are sized for the delivered restraint / (1 + margin)
MAX_GIRDER_LAYOUTS = 4

# Frames for the frontier's restraint levels are sized at once, each in its own
# worker process on a windowless engine (parallel.frame_task): one per core, up to this many.
FRAME_WORKERS = 16

# Share of the progress bar for each step, from a factory-default run (2026-11-16, 32 cores):
# unrestrained 2D 35 s, 3D options 27 s, restrained frames (9 at once) 45 s, pairs + workspace 30 s.
PHASES = {
    'frame': (1, 'Sizing the unrestrained 2D frame', 0., .26),
    'bracing': (2, 'Checking bracing options in 3D', .26, .46),
    'floor': (3, 'Sizing the 2D frame for each bracing restraint', .46, .78),
    'pairs': (4, 'Checking frame + bracing pairs', .78, 1.),
}
STEP_COUNT = len(PHASES)
AUTO_SIZE_STEPS = 12   # Optimise (run_physics_structure_optimisation) reports steps 1-12


class Progress:
    """Overall optimiser progress for ``callback(message, fraction)``.

    ``fraction`` runs 0-1 over the whole optimisation; ``message`` is
    "Step n of 4: phase" plus a detail line. A callback taking only a message
    also works. The callback may raise to cancel. Reports from inside a 2D
    auto-size (``safe=False``) must not interrupt it, so an error raised there
    is held and re-raised at the next safe report.
    """

    def __init__(self, callback=None):
        import inspect
        self.callback = callback
        self.two_args = False
        if callback is not None:
            try:
                parameters = inspect.signature(callback).parameters.values()
                self.two_args = (sum(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) for p in parameters) >= 2
                                 or any(p.kind is p.VAR_POSITIONAL for p in parameters))
            except (TypeError, ValueError):
                pass
        self.title, self.start, self.end, self.fraction, self.held = '', 0., 0., 0., None

    def phase(self, key, detail=None):
        step, title, self.start, self.end = PHASES[key]
        self.title = f'Step {step} of {STEP_COUNT}: {title}'
        self(detail, 0.)

    def span(self, start, end):
        """Narrow the current phase to [start, end] of itself (fractions 0-1)."""
        width = self.end-self.start
        self.start, self.end = self.start+start*width, self.start+end*width

    def __call__(self, detail=None, within=0., safe=True):
        if self.held is not None and safe:
            held, self.held = self.held, None
            raise held
        fraction = self.start+max(0., min(1., within))*(self.end-self.start)
        self.fraction = max(self.fraction, fraction)
        if self.callback is None:
            return
        message = self.title+(f'\n{detail}' if detail else '')
        try:
            if self.two_args:
                self.callback(message, self.fraction)
            else:
                self.callback(message)
        except Exception as error:
            if safe:
                raise
            self.held = error

    def size_frame(self, window, label):
        """2D auto-size with its own 12 steps shown inside the current phase."""
        def listener(step, detail, exact_solves=None):
            if step is not None:
                state['step'] = step
            text = f'{label}: {detail}' if detail else label
            if exact_solves is not None:
                text += f' ({exact_solves} exact solves)'
            self(text, state['step']/AUTO_SIZE_STEPS, safe=False)
        state = {'step': 0}
        previous = getattr(window, 'optimisation_progress_listener', None)
        window.optimisation_progress_listener = listener
        try:
            self(label, 0.)
            return shared._size_frame(window)
        finally:
            window.optimisation_progress_listener = previous


def bracing_points(context, candidates, criteria, practical, progress=None, portal_braces=False):
    """3D-checked bracing options with the unrestrained frame.

    ``portal_braces`` also tries portal frame braces on a side wall whose end
    bays can't take X bracing (``portal_brace_seeds``). Off by default: on the
    test projects they were never cheaper than roof bracing. Portal braces the
    user pins are always kept, analysed and sized.

    The girder-layout sizings and the stepped-rod checks are independent, so
    each batch runs in worker processes (``bracing_parallel``); the result is
    the same as checking them one after another. ``progress(detail, within)``
    gets this step's share done (0-1) and may raise to cancel.
    """
    note = progress or (lambda detail, within: None)
    layout, chosen = practical
    layouts = [('Practical layout', set(layout.selection), chosen)]
    unpinned = shared._with_layout(context, ())
    seeds = [(reason, set(selected)) for reason, selected in practical_seeds(unpinned, set()) if 'girder' in reason]
    extra = [(reason, set(selected)) for reason, selected in full_roof_seeds(unpinned)]
    if portal_braces:
        extra += [(reason, set(selected)) for reason, selected in portal_brace_seeds(unpinned)]
    # Each layout is sized from the practical sections and from the lightest
    # rods; the fast auto-size does not come far down from heavy sections, so
    # a light start can find a much cheaper passing set. The cheaper pass is kept.
    light = light_start(chosen, candidates)
    starts = [chosen] + ([light] if light is not None else [])
    # Layouts with portal frame braces also need those groups: the saved or
    # default sections with the practical start, the lightest UBs with the light one.
    portal_starts = [{g: context[g] for g in PORTAL_BRACE_GROUPS if context.get(g) is not None},
                     {g: candidates[g][0] for g in PORTAL_BRACE_GROUPS if candidates.get(g)}]

    def start_for(selected, number):
        if not any(is_portal_brace(p) for p in selected):
            return starts[number]
        return {**starts[number], **portal_starts[min(number, 1)]}
    jobs = [(index, selected, start_for(selected, number))
            for index, (_, selected) in enumerate(seeds+extra) for number in range(len(starts))]
    if light is not None:
        jobs.append((None, set(layout.selection), light))   # the practical layout from light rods
    with parallel.Runner(context, max(len(jobs), len(ROD_STEPS)*(MAX_GIRDER_LAYOUTS+3))) as runner:
        sized = runner.map(parallel.size_cost_task, [(selected, start, candidates, criteria) for _, selected, start in jobs],
                           lambda done, total: note(f'Sizing girder bracing layouts ({done} of {total})', .75*done/total))
        best = {}
        for (index, _, _), outcome in zip(jobs, sized):
            if outcome is not None and (index not in best or outcome[0] < best[index][0]):
                best[index] = outcome
        if None in best and section_signature(best[None][1]) != section_signature(chosen):
            layouts.append(('Practical layout, sized from light rods', set(layout.selection), best[None][1]))
        girders = 0
        for index, (reason, selected) in enumerate(seeds+extra):
            if index < len(seeds) and girders >= MAX_GIRDER_LAYOUTS:
                continue
            if index in best:
                girders += index < len(seeds)
                # With the canopy bracing the analysis adds (end roof-braced bays);
                # the selection keeps any portal braces.
                layouts.append((reason, set(shared._with_layout(context, selected)['layout'].selection), best[index][1]))
        options = []
        for name, pinned, selections in layouts:
            for roof, end in ROD_STEPS:
                stepped = shared._stepped(selections, candidates, {'roof_brace': roof, 'end_brace': end},
                                          shared._with_layout(context, pinned))
                if stepped is not None:
                    label = name if not (roof or end) else f'{name}; roof/end braces +{roof}/+{end} sizes'
                    options.append((label, pinned, stepped))
        note(f'Checking {len(options)} bracing options', .75)
        checks = runner.map(parallel.check_task, [(pinned, stepped, criteria) for _, pinned, stepped in options],
                            lambda done, total: note(f'Checking bracing options ({done} of {total})', .75+.25*done/total))
    return [{'option': label, 'pinned': pinned, 'selections': stepped, **check,
             'restraint': check['restraint_kn_mm'] if check['adoptable'] else 0.}
            for (label, pinned, stepped), check in zip(options, checks)]


def light_start(selections, candidates):
    """``selections`` with every brace group at its lightest priced rod, or None.

    Rods are eligible in every brace group (the project policy only ever
    requires rod), so this is always a valid start for the auto-size.
    """
    from portalcalc.bracing.design import candidate_stiffness
    from portalcalc.bracing.policy import family
    out = dict(selections)
    for group in shared.BRACE_GROUPS + ('canopy_brace',):
        if group not in selections or group not in candidates:
            continue
        rods = [c for c in candidates[group]
                if isinstance(c, dict) and family(c) == 'rod' and c.get('fitting_unit_cost') is not None]
        if not rods:
            return None
        out[group] = min(rods, key=candidate_stiffness)
    return out


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
    if a is None or b is None:
        return a is b
    return a is b or (a.get('state') is not None and a.get('state') == b.get('state'))


def search(options, frame_for, verify, incumbent):
    """Branch-and-bound over the frontier (cheapest first).

    ``frame_for(k)`` returns the sized frame for restraint k (cost, ...);
    ``verify(frame, option)`` returns a row with ``verified``/``total``;
    ``incumbent`` is the verified unrestrained row. Returns (best, rows).

    The floor frame (sized for the stiffest bracing) is tried first with each
    option: frame cost should not fall with less restraint, so if it verifies,
    no frame sized for that option should be cheaper and its 2D auto-size is
    skipped.

    The 2D auto-size is a bounded search, so more restraint can occasionally
    give a dearer frame. A floor frame dearer than the unrestrained frame shows
    that here, so the floor is not a bound. The frame for the least restraint
    (the cheapest restraining option) is then sized as a probe: if it is not
    cheaper than the unrestrained frame either, restraint does not reduce the
    frame at either end and the search stops. Otherwise the probe's cost is the
    bound and each remaining option's own frame is sized (no floor checks or
    shortcut; the floor frame cannot win, being dearer than the incumbent's
    frame with dearer bracing).
    """
    best, rows = incumbent, []
    restrained = [p for p in options if p['restraint'] > 0.]
    if not restrained:
        return best, rows
    floor = frame_for(restrained[-1]['restraint'])
    unrestrained_cost = incumbent.get('frame_cost')
    trusted = floor is None or unrestrained_cost is None or floor['cost'] <= unrestrained_cost+.005
    least = floor and floor['cost']
    if not trusted:
        probe = frame_for(restrained[0]['restraint'])
        if probe is None or probe['cost'] >= unrestrained_cost-.005:
            rows.append({'bracing': '(all options)', 'frame_cost': floor['cost'], 'status': (
                f"Restraint does not reduce the frame: frames sized for the most (${floor['cost']:,.2f}) and least "
                + (f"(${probe['cost']:,.2f}) " if probe else "(could not be sized) ")
                + f"restraint both cost at least the unrestrained frame (${unrestrained_cost:,.2f})")})
            return best, rows
        least = probe['cost']
        rows.append({'bracing': '(all options)', 'frame_cost': floor['cost'], 'status': (
            f"Floor frame ${floor['cost']:,.2f} costs more than the unrestrained frame ${unrestrained_cost:,.2f}; "
            f"bound from the frame for the least restraint (${least:,.2f}) and each option's own frame is sized")})
    for option in restrained:
        if least is None or option['bracing_cost']+least >= best['total']:
            rows.append({'bracing': option['option'], 'status': 'Skipped: cannot beat the best total even with the least frame cost'})
            continue
        floor_row = {'verified': False}
        if trusted:
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
            if trusted:
                rows.append(floor_row)   # same design, already checked
            else:
                rows.append({'bracing': option['option'], 'frame_cost': frame['cost'], 'predicted_total': predicted,
                             'status': 'Floor frame: dearer than the unrestrained frame'})
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
    # No undo snapshots of intermediate trial designs (as Optimise Structure);
    # the caller records one snapshot of the adopted design.
    history = getattr(window, '_history_suspended', None)
    if history is not None:
        window._history_suspended = True
    # Hold the main truss panel count between solves (as Optimise Structure
    # does), so a restored frame keeps the count its 2D auto-size chose. Each
    # auto-size still chooses it (its own forced topology check).
    topology = getattr(window, '_suppress_panel_topology_search', None)
    if topology is not None:
        window._suppress_panel_topology_search = True
    # Read before any sizing: the 2D auto-size clamps the depth into range.
    note = getattr(window, 'truss_depth_outside_range_note', lambda: '')()
    try:
        suspended = getattr(window, 'suspended_plotting', None)
        if suspended is None:
            report = _optimise(window, progress=progress)
        else:
            with suspended():
                report = _optimise(window, progress=progress)
        if note:
            report['warnings'] = [*report.get('warnings', []), note]
        return report
    finally:
        if history is not None:
            window._history_suspended = history
        if topology is not None:
            window._suppress_panel_topology_search = topology


def _optimise(window, *, progress=None):
    started = time.perf_counter()
    report = Progress(progress)
    shared.check_saved_locations(window)
    counts = {'frame_auto_size': 0, 'bracing_3d': 0}
    window.trial_sway_restraint_kn_mm = None
    window.eave_x_restraint_combo.setCurrentText('None')
    try:
        report.phase('frame')
        unrestrained = report.size_frame(window, '2D auto-size')
        counts['frame_auto_size'] += 1
        if not unrestrained['verified']:
            raise AnalysisError('The unrestrained 2D frame could not be sized')
        baseline = shared.capture_state(window)

        report.phase('bracing', 'Finding a practical bracing layout')
        context = prepare_context(window, allow_unconfigured=True)
        candidates = shared._candidates(window, context)
        start = shared._start_selections(window, context, candidates)
        # The workspace's saved criteria (slack-play limit, serviceability), so the
        # adopted design is the one the workspace's Analyse and check confirms.
        criteria = shared._criteria(window, context)
        # These analyses only rank options (their results are discarded), so
        # skip the report-only portal member end forces.
        search_context = {**context, 'search_only': True}
        _, chosen, layout = find_layout(
            shared._with_layout(search_context, ()), start, candidates, criteria, context['cost_rates'],
            progress=lambda step, total, message: report(f'Finding a practical bracing layout: {message}',
                                                         .4*min(1., step/max(total, 1))))
        report('Sizing girder bracing layouts', .4)
        report.span(.4, 1.)
        # Portal frame brace options are opt-in (workspace setting
        # layout_search.try_portal_braces); pinned portal braces are always kept.
        try_portal_braces = bool(((window.bracing_3d_settings or {}).get('layout_search') or {})
                                 .get('try_portal_braces', False))
        points = bracing_points(search_context, candidates, criteria, (layout, chosen), progress=report,
                                portal_braces=try_portal_braces)
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
        # Step 4 has an unknown number of checks (and possibly re-sizes): each
        # takes a share of what is left, keeping the last 20% for the
        # workspace check, so the bar always moves forward.
        pairs = {'started': False, 'at': 0.}

        def in_pairs():
            if not pairs['started']:
                pairs['started'] = True
                report.phase('pairs')

        def pairs_share(share):
            begin = pairs['at']
            pairs['at'] = begin+(.8-begin)*share
            return begin, pairs['at']

        def frame_for(restraint):
            target = restraint/(1+SIZING_MARGIN)
            key = round(target, 6)
            if key not in frames:
                if not frames:
                    report.phase('floor')
                else:
                    in_pairs()
                    begin, end = pairs_share(.6)
                    report.phase('pairs')
                    report.span(begin, end)
                shared.restore_state(window, baseline)
                window.trial_sway_restraint_kn_mm = target
                sized = report.size_frame(window, f'2D auto-size for {target:.3f} kN/mm restraint')
                counts['frame_auto_size'] += 1
                frames[key] = None if not (sized['verified'] and sized['acceptable']) else {
                    **sized, 'sized_for_kn_mm': target, 'state': shared.capture_state(window)}
                window.trial_sway_restraint_kn_mm = None
            return frames[key]

        checked = {}   # (frame sized-for, option name) -> verify_pair row, checked ahead in worker processes

        def verify(frame, option):
            in_pairs()
            report.phase('pairs')
            report(f"Checking {option['option']}\nwith the frame sized for {frame['sized_for_kn_mm']:.3f} kN/mm",
                   pairs_share(.35)[0])
            counts['bracing_3d'] += 1
            ready = checked.get((frame['sized_for_kn_mm'], option['option']))
            if ready is not None:
                return dict(ready)
            return verify_pair(window, frame, option, criteria, candidates)

        # Each restraint level's frame is the 2D auto-size of the same baseline with
        # that restraint: independent of the others, so they are all sized now, at
        # once, in worker processes. The search below then finds them ready.
        targets = {}
        for option in options:
            if option['restraint'] > 0.:
                target = option['restraint']/(1+SIZING_MARGIN)
                targets.setdefault(round(target, 6), target)
        if len(targets) > 1:
            report.phase('floor')
            with parallel.Runner({'baseline': baseline}, len(targets), max_workers=FRAME_WORKERS) as runner:
                sized = runner.map(
                    parallel.frame_task, list(targets.values()),
                    lambda done, total: report(f'{total} frames, one per bracing restraint level: {done} sized', done/total))
            for (key, target), outcome in zip(targets.items(), sized):
                counts['frame_auto_size'] += 1
                state = outcome.pop('state')
                frames[key] = (None if not (outcome['verified'] and outcome['acceptable']) else
                               {**outcome, 'sized_for_kn_mm': target, 'state': state})

            # The pair checks the search may ask for are independent as well: each
            # restores its frame's captured state before checking. Check them ahead,
            # together; the search then makes the same decisions from ready results.
            restrained = [p for p in options if p['restraint'] > 0.]
            frame_of = lambda option: frames.get(round(option['restraint']/(1+SIZING_MARGIN), 6))
            floor = frame_of(restrained[-1])
            unrestrained_cost = incumbent.get('frame_cost')
            trusted = floor is None or unrestrained_cost is None or floor['cost'] <= unrestrained_cost+.005
            pairs_ahead = {}
            for option in restrained:
                for frame in ((floor,) if trusted else ()) + (frame_of(option),):
                    if frame is not None:
                        pairs_ahead.setdefault((frame['sized_for_kn_mm'], option['option']), (frame, option))
            if len(pairs_ahead) > 1:
                in_pairs()
                report.phase('pairs')
                with parallel.Runner({'criteria': criteria, 'candidates': candidates}, len(pairs_ahead),
                                     max_workers=FRAME_WORKERS) as runner:
                    rows_ahead = runner.map(
                        parallel.pair_task, list(pairs_ahead.values()),
                        lambda done, total: report(f'{total} frame + bracing pairs: {done} checked', .7*done/total))
                checked.update(zip(pairs_ahead, rows_ahead))
                pairs['at'] = .7

        best, rows = search(options, frame_for, verify, incumbent)
        report.phase('pairs')
        report('Restoring the best design', .8)
        shared.restore_state(window, best['state'], exact=True)
        report('Checking the adopted bracing in the 3D workspace', .85)
        workspace = shared.store_calculation(window)
        # The 2D frame as adopted (panel count included) must be the one
        # verified and costed; record it so the report shows any difference.
        adopted = shared._frame_state(window)
        _keep_panel_count(window)
        report('Done', 1.)
        design = workspace['design']
        best = {**best, 'workspace_check': {
            'passes': bool(design['passes']), 'estimated_cost': design['estimated_cost'],
            'matches': bool(design['passes']) and abs(design['estimated_cost']-best['bracing_cost']) <= .005},
            'frame_check': {'acceptable': bool(adopted['acceptable']), 'ratio': adopted['ratio'],
                            'cost': adopted['cost'], 'panel_count': _panel_count(window),
                            'matches': bool(adopted['acceptable']) and abs(adopted['cost']-best['frame_cost']) <= .005}}
        public = lambda row: {k: v for k, v in row.items() if k != 'state'}
        return {'schema': 'portalcalc.frontier-sizing.v2', 'elapsed_seconds': time.perf_counter()-started,
                'solves': counts, 'unrestrained_frame': unrestrained,
                'bracing_points': [_public(p) for p in points], 'frontier': [_public(p) for p in options],
                'frames': [{k: v for k, v in f.items() if k != 'state'} for f in frames.values() if f],
                'incumbent': public(incumbent), 'searched': [public(r) for r in rows], 'best': public(best),
                'basis': __doc__.strip().split('\n\n')[0]}
    finally:
        window.trial_sway_restraint_kn_mm = None


def _panel_count(window):
    try:
        return int(window.panel_count_label.text())
    except (AttributeError, TypeError, ValueError):
        return None


def _keep_panel_count(window):
    """Let ordinary solves after the run keep the adopted truss panel count
    while the inputs are unchanged (as a manually checked panel count is)."""
    count = _panel_count(window)
    if count is not None and getattr(window, 'frame_system_combo', None) is not None \
            and window.frame_system_combo.currentText() == 'Truss':
        window._panel_count_override = count
        window._panel_topology_cache_signature = window.panel_topology_input_signature()
        window._panel_topology_cache_count = count


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
    shared.restore_state(window, frame['state'], exact=True)   # the frame as sized, panel count included
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
    frame_check = best.get('frame_check')
    if frame_check:
        lines.append(f"2D frame as adopted ({frame_check['panel_count']} truss panels): "
                     + ('same frame as verified.' if frame_check['matches'] else
                        f"{'PASS' if frame_check['acceptable'] else 'FAIL'}, ratio {frame_check['ratio']:.3f}, "
                        f"frame ${money(frame_check['cost'])} (optimiser ${money(best.get('frame_cost'))}). "
                        "Review the 2D frame."))
    check = best.get('workspace_check')
    if check:
        lines.append('3D workspace Analyse and check: '
                     + ('same result, saved; reopen the workspace to inspect members.' if check['matches'] else
                        f"{'PASS' if check['passes'] else 'FAIL / UNVERIFIED'}, bracing ${money(check['estimated_cost'])} "
                        f"(optimiser ${money(best['bracing_cost'])}). Review in the 3D workspace."))
    if report.get('warnings'):
        lines += ['', *report['warnings']]
    return '\n'.join(lines)
