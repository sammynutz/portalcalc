"""Bounded, exactly checked layout search over the unblocked panel set.

Constraints and trial contexts are private copies. A diaphragm never substitutes
for the bare-model stability/ULS checks performed by the section design engine.
"""
import copy
from dataclasses import replace
import hashlib
import json
from time import perf_counter

from bracing_analysis import AnalysisError
from bracing_design import (auto_size, solve_selection, candidate_name, panel_installation_rate,
                           candidate_stiffness, balance_compact_braces, group_cost)
from bracing_components import StrutPlan, StrutAssembly
from bracing_layout import BracingLayout
from bracing_policy import selection_score, family, eligible as policy_eligible
from bracing_spacing import max_bay_spacing,spacing_assessment,repair_spacing,spacing_text


class LayoutSearchCancelled(AnalysisError):
    """Cancellation must escape candidate failure handling."""


class LayoutSearchStopped(AnalysisError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def layout_context(context, selected):
    """Keep actual and nominal grids aligned by panel ID, without mutating input."""
    original = context['layout']
    layout = BracingLayout(original.geometry, original.blocked, selected)
    nominal = copy.deepcopy(context.get('nominal_layout', original.to_dict()))
    nominal.update(blocked_panels=sorted(layout.blocked), pinned_panels=sorted(layout.pinned))
    source = copy.deepcopy(context.get('source_preset', {}))
    source.setdefault('bracing', {})['layout'] = nominal
    return {**context, 'layout': layout, 'nominal_layout': nominal, 'source_preset': source,
            'source_hash': hashlib.sha256(json.dumps(source, sort_keys=True, allow_nan=False).encode()).hexdigest()}


def removal_groups(layout, selected, required):
    """Coherent bay/face removals first, then individual panels; deterministic."""
    groups = []
    for face in ('front_end', 'back_end'):
        groups.append({p for p in selected if layout.panels[p].face == face})
    # Remove interior bays first, retaining economical end load paths when possible.
    bays = range(len(layout.geometry.bay_stations_mm)-1)
    middle = (len(layout.geometry.bay_stations_mm)-2)/2
    for bay in sorted(bays, key=lambda b: (abs(b-middle), b)):
        groups.append({p for p in selected if '/bay/' in p and int(p.split('/')[2]) == bay})
        groups.append({p for p in selected if p.startswith(f'roof/bay/{bay}/')})
    for face in ('left_wall', 'right_wall'):
        groups.append({p for p in selected if layout.panels[p].face == face})
    groups.extend({p} for p in sorted(selected))
    seen = set()
    for group in groups:
        group = frozenset(group-required)
        if group and group not in seen and selected-set(group):
            seen.add(group)
            yield group


def structured_seeds(layout, required):
    """A small set of engineering arrangements, rather than panel combinations.

    Whole roof bays and available wall faces are proposed together. Exclusions
    may interrupt a proposal; only the exact solver can decide if it works.
    """
    available = layout.available
    count = len(layout.geometry.bay_stations_mm)-1
    if layout.pinned:
        yield 'Re-size input arrangement', layout.pinned | required
    pairs = [(0, count-1), (0,), (count-1,), (count//2,),
             (count//4, min(count-1, 3*count//4))]
    pairs += [(bay,) for bay in range(count)]
    faces = [face for face in ('left_wall', 'right_wall')
             if any(p.startswith(face+'/') for p in available)]
    patterns = [tuple(faces)]
    if len(faces) == 2:
        patterns.extend((face,) for face in faces)
    seen = set()
    # First diversify bay positions, then compare single-wall arrangements.
    for wall_faces in patterns:
        for bays in pairs:
            bays = set(bays)
            selected = {p for p in available if '/bay/' in p and int(p.split('/')[2]) in bays
                        and (p.startswith('roof/') or layout.panels[p].face in wall_faces)} | required
            key = frozenset(selected)
            if key and key not in seen:
                seen.add(key)
                yield ('Complete roof/wall bays '+', '.join(str(b+1) for b in sorted(bays))+
                       ' ('+', '.join(wall_faces)+')'), selected


def coherent_changes(layout, selected, required):
    """Align, complete, remove and relocate braced bays, preserving constraints."""
    available = layout.available
    wall_bays = {int(p.split('/')[2]) for p in selected if p.startswith(('left_wall/', 'right_wall/'))}
    roof_bays = {int(p.split('/')[2]) for p in selected if p.startswith('roof/')}
    roof_at = lambda bays: {p for p in available if p.startswith('roof/') and int(p.split('/')[2]) in bays}
    if wall_bays:
        yield 'Align roof bracing with wall bays', ({p for p in selected if not p.startswith('roof/')} | roof_at(wall_bays) | required)
    yield 'Complete occupied roof bays', selected | roof_at(roof_bays) | required
    count = len(layout.geometry.bay_stations_mm)-1
    for bay in sorted(wall_bays | roof_bays):
        items = {p for p in selected if '/bay/' in p and int(p.split('/')[2]) == bay}
        for target in sorted(range(count), key=lambda b: (abs(b-bay), b)):
            if target == bay:
                continue
            moved = set()
            for panel in items:
                parts = panel.split('/')
                parts[2] = str(target)
                moved.add('/'.join(parts))
            if moved <= available:
                yield f'Move bay {bay+1} bracing to bay {target+1}', (selected-items) | moved | required
    for removal in removal_groups(layout, selected, required):
        if len(removal) > 1:
            yield f'Remove coherent group of {len(removal)} panels', selected-set(removal)


def section_signature(chosen):
    return tuple((group, tuple((key, candidate_name(member)) for key, member in sorted(option.members.items()))
                  if isinstance(option, StrutPlan) else candidate_name(option))
                 for group, option in sorted(chosen.items()))


def find_cost_layout(context, selections, candidates, criteria, rates, *, required=(),
                progress=None, max_trials=48, max_section_solves=12):
    """Compare resized engineering seeds, explore bay changes, then prune/polish.

    Each outer trial performs either one exact analysis or a bounded section
    search. This is a heuristic with a declared budget, not proof of an optimum
    or of infeasibility. Returns (checked result, sections, actual layout).
    """
    if isinstance(max_trials, bool) or not isinstance(max_trials, int) or max_trials < 1:
        raise ValueError('Layout trial budget must be a positive integer')
    if isinstance(max_section_solves, bool) or not isinstance(max_section_solves, int) or max_section_solves < 2:
        raise ValueError('Section solve budget must be at least two')
    original = context['layout']
    required = set(required)
    if not required <= original.available:
        raise AnalysisError('Required panels must be known, unblocked panels')
    evidence = {'method': 'Resized engineering seeds, two-start bay relocation/completion and exact deletion/polish',
                'scope': 'Bounded heuristic search; no proof of global layout/cost optimum or infeasibility. Estimated materials, listed fittings and panel installation allowance.',
                'blocked_panels': sorted(original.blocked), 'required_panels': sorted(required),
                'max_layout_trials': max_trials, 'max_section_solves_per_trial': max_section_solves,
                'trials': []}
    evidence.update(max_braced_bay_spacing=max_bay_spacing(context),spacing_rejections=[])
    trials = evidence['trials']
    if not original.available:
        raise LayoutSearchStopped('All panels are blocked; no layout can be tried', evidence)

    def update(message):
        if progress:
            progress(len(trials), max_trials, message)

    evaluated = set()
    def evaluate(selected, chosen, reason, sizing=False):
        spacing=spacing_assessment(original,max_bay_spacing(context),selected)
        if not spacing['passes']:
            evidence['spacing_rejections'].append({'reason':reason,'spacing':spacing})
            return None
        key = (frozenset(selected), section_signature(chosen), sizing)
        if key in evaluated:
            evidence['duplicate_trials_skipped'] += 1
            return None
        evaluated.add(key)
        update(f'Layout trial {len(trials)+1}/{max_trials}: {reason} ({len(selected)} panels)')
        local = layout_context(context, selected)
        chosen = dict(chosen)
        if isinstance(chosen['strut'], StrutPlan):
            plan = chosen['strut']
            bays = {int(p.split('/')[2]) for p in selected if '/bay/' in p}
            chosen['strut'] = replace(plan, members={key: replace(member, location={**member.location,
                'braced_bay': member.location['bay'] in bays}) if isinstance(member, StrutAssembly) else member
                for key, member in plan.members.items()})
        record = {'trial': len(trials)+1, 'reason': reason, 'selected_panels': sorted(selected), 'section_sizing': sizing,
                  'passes': False, 'adopted': False}
        trials.append(record)
        try:
            if sizing:
                def section_progress(step, total, message):
                    update(f'Layout {len(trials)}/{max_trials}: {message}')
                result, chosen = auto_size(local, chosen, candidates, criteria, rates,
                                           section_progress, max_solves=max_section_solves)
            else:
                result = solve_selection(local, chosen, criteria, rates)
            record.update(passes=result['design']['passes'], estimated_cost=result['design']['estimated_cost'],
                          global_sls_ratio=result['design']['global_sls']['ratio'],
                          taut_tangent_verified=result['design']['taut_tangent_verified'],
                          section_labels={g: candidate_name(c) for g, c in chosen.items()})
            return result, dict(chosen), local
        except LayoutSearchCancelled:
            raise
        except AnalysisError as error:
            record['analysis_error'] = str(error)
            return None

    best, winning_trial, beam = None, None, []
    evidence.update(duplicate_trials_skipped=0, beam_width=2, structured_seed_trials=0)

    def score(trial):
        result, chosen, local = trial
        return (*selection_score(local, chosen, result), len(local['layout'].pinned))

    def consider(trial):
        nonlocal best, winning_trial
        if trial is None or not trial[0]['design']['passes']:
            return False
        if best is None or score(trial) < score(best):
            best = trial
            winning_trial = trials[-1]['trial']
            trials[-1]['adopted'] = True
        return True

    # Compare several independently resized arrangements even after one passes.
    # Keep two different verified starts, rather than following one deletion path.
    seed_limit = min(6, max(1, max_trials//6))
    for reason, selected in structured_seeds(original, required):
        if evidence['structured_seed_trials'] >= seed_limit or len(trials) >= max_trials:
            break
        try:
            repaired=repair_spacing(context,selected)
        except AnalysisError as error:
            evidence['spacing_rejections'].append({'reason':reason,'error':str(error)})
            continue
        if repaired!=selected:
            reason+=f'; intermediate roof/wall bays (maximum {max_bay_spacing(context)})'
        selected=repaired
        before = len(trials)
        trial = evaluate(set(selected), selections, reason, True)
        if len(trials) == before:
            continue
        evidence['structured_seed_trials'] += 1
        if consider(trial):
            beam.append(trial)
            beam.sort(key=score)
            del beam[2:]
    # Dense fallback is for establishing feasibility, not the only search start.
    if best is None and len(trials) < max_trials:
        trial = evaluate(original.available, selections, 'Dense feasibility fallback', True)
        if consider(trial):
            beam.append(trial)
    if best is None:
        evidence['layout_trials'] = len(trials)
        if not trials and evidence['spacing_rejections']:
            raise LayoutSearchStopped(f"Blocked panels prevent a compliant braced bay within every {max_bay_spacing(context)} bays. Review the blocked locations or maximum braced-bay spacing.",evidence)
        raise LayoutSearchStopped('No passing layout found within the search budget. Review blocked locations, sections and criteria; this is not proof that no feasible layout exists.', evidence)

    # Give two promising starts a bounded neighbourhood budget. Full bay moves
    # can cross a cost/feasibility barrier that single-panel deletions cannot.
    # Reserve time for panel-level refinement and final sizing.
    resized = 0
    local_budget = max(0, (max_trials-len(trials)-1)*2//3)
    local_end = len(trials)+local_budget
    for index, start in enumerate(beam):
        end = local_end if index == len(beam)-1 else len(trials)+local_budget//len(beam)
        current = start
        explored = set()
        while len(trials) < end:
            selected = set(current[2]['layout'].pinned)
            state = (frozenset(selected), section_signature(current[1]))
            if state in explored:
                break
            explored.add(state)
            improved = current
            for reason, candidate in coherent_changes(original, selected, required):
                if len(trials) >= end:
                    break
                if candidate == selected or not candidate:
                    continue
                trial = evaluate(candidate, current[1], reason)
                # Completing a bay adds brace cost at the current sizes, but
                # may allow cheaper struts/braces after redistribution. Compare
                # that coupled cost even when the unchanged sections pass.
                needs_sizing = trial and (not trial[0]['design']['passes'] or reason == 'Complete occupied roof bays')
                if (needs_sizing and resized < 3
                        and trial[0]['design']['taut_tangent_verified'] and len(trials) < end):
                    resized += 1
                    trial = evaluate(candidate, current[1], 'Re-size bay alternative', True)
                if consider(trial) and score(trial) < score(improved):
                    improved = trial
            if improved is current:
                break
            current = improved
    evidence['neighbourhood_resizes'] = resized

    # Only now test individual deletions, using the cheapest verified topology.
    # A panel-count reduction never outweighs a higher fully checked cost.
    while len(trials) < max_trials-1:
        result, chosen, local = best
        selected = set(local['layout'].pinned)
        adopted = False
        for panel in sorted(selected-required):
            if len(trials) >= max_trials-1:
                break
            candidate = selected-{panel}
            if not candidate:
                continue
            previous = best
            consider(evaluate(candidate, chosen, 'Remove panel '+panel))
            if best is not previous:
                adopted = True
                break
        if not adopted:
            break
    if len(trials) < max_trials:
        result, chosen, local = best
        trial = evaluate(set(local['layout'].pinned), chosen, 'Re-size surviving layout', True)
        consider(trial)
    result, chosen, local = best
    evidence.update(layout_trials=len(trials), winning_trial=winning_trial, selected_panels=sorted(local['layout'].pinned),
                    budget_exhausted=len(trials) >= max_trials)
    result['layout_search'] = evidence
    result['limitations'].append(evidence['scope'])
    update(f'Passing layout: {len(local["layout"].pinned)} X panels; ${result["design"]["estimated_cost"]:,.2f}')
    return result, chosen, local['layout']


def practical_seeds(context, required):
    """Short end load paths first; add transverse restraint for eccentric plans.

    Span/eave >= 4 is a search-order heuristic for one-sided sheds, not a code
    limit or acceptance rule. End-wall braces act in X/Z; only the full 3D solve
    can verify their indirect effect on Y sway/plan twist.
    """
    layout = context['layout']
    available, g = layout.available, layout.geometry
    if layout.pinned:
        yield 'Verify current layout before compacting', layout.pinned | required
    count = len(g.bay_stations_mm)-1
    def panels(bay, roof=False):
        return {p for p in available if '/bay/' in p and int(p.split('/')[2]) == bay
                and (p.startswith('roof/') if roof else p.startswith(('left_wall/', 'right_wall/')))}
    viable = [bay for bay in range(count) if panels(bay, True) and panels(bay)]
    if not viable:
        viable = [bay for bay in range(count) if panels(bay, True)]
    if not viable:
        yield 'Available load-path fallback', available | required
        return
    full = [bay for bay in viable if len(panels(bay, True)) == len(g.roof_stations)-1]
    ends = {min(full or viable), max(full or viable)}
    base = set(required)
    for bay in ends:
        base |= panels(bay, True) | panels(bay)
    faces = {layout.panels[p].face for p in base if p.startswith(('left_wall/', 'right_wall/'))}
    one_sided = len(faces) == 1
    open_edge = 'right' if 'left_wall' in faces else 'left'
    end_braces = set()
    for face in ('front_end', 'back_end'):
        options = sorted((p for p in available if p.startswith(face+'/')), key=lambda p: int(p.split('/')[-1]))
        if options:
            end_braces.add(options[-1] if one_sided and open_edge == 'right' else options[0])
    with_ends = base | end_braces
    nominal_span = context.get('nominal_layout', {}).get('geometry', {}).get('span_mm', g.span_mm)
    all_end = {p for p in available if p.startswith(('front_end/', 'back_end/'))}
    paired_ends = set()
    for face in ('front_end', 'back_end'):
        options = sorted((p for p in available if p.startswith(face+'/')), key=lambda p: int(p.split('/')[-1]))
        if options:
            paired_ends.update((options[0], options[-1]))
    wide_one_sided = one_sided and nominal_span/g.eave_mm >= 4. and bool(all_end)
    sequence = [('End bays with distributed end-wall restraint', base | all_end),
                ('End roof/wall bays with end-wall twist restraint', with_ends),
                ('End roof/wall bays', base)] if wide_one_sided else [
                ('End roof/wall bays', base), ('End roof/wall bays with end-wall twist restraint', with_ends)]
    sequence.append(('End bays with distributed end-wall restraint', base | all_end))
    # A longitudinal roof wind girder is a different load path from repeating
    # whole transverse braced bays. Try the eave attached to the vertical wall
    # bracing first, then the opposite eave/both. Never label an interrupted
    # blocked strip as a continuous girder.
    eave_strips = [('left', 0, 'left_wall'), ('right', len(g.roof_stations)-2, 'right_wall')]
    eave_strips.sort(key=lambda row: (row[2] not in faces, row[0]))
    rows = []
    primary_girder = None
    primary_variants = []
    for side, strip, face in eave_strips:
        row = {f'roof/bay/{bay}/strip/{strip}' for bay in range(count)}
        if row <= available:
            rows.append(row)
            role = 'supported' if face in faces else 'opposite'
            sequence.append((f'End bays with continuous {role} {side}-eave roof girder', base | end_braces | row))
            if role == 'supported' and primary_girder is None:
                primary_girder = sequence[-1]
                # Scale the same load path progressively: close the girder's
                # end restraint, then deepen its continuous band. A single
                # narrow strip is not a complete search of this family.
                sequence.append((f'End bays with continuous supported {side}-eave roof girder and paired end anchors', base | paired_ends | row))
                sequence.append((f'End bays with continuous supported {side}-eave roof girder and full end restraint', base | all_end | row))
                second = strip+1 if side == 'left' else strip-1
                if 0 <= second < len(g.roof_stations)-1:
                    extra = {f'roof/bay/{bay}/strip/{second}' for bay in range(count)}
                    if extra <= available:
                        sequence.append((f'End bays with continuous two-strip {side}-eave roof girder', base | end_braces | row | extra))
                        sequence.append((f'End bays with continuous two-strip {side}-eave roof girder and paired end anchors', base | paired_ends | row | extra))
                # A changing mullion/purlin grid changes a single strip's
                # relative width. Start wider when necessary, rather than
                # treating one strip as the same girder at every shed width.
                ordered = range(len(g.roof_stations)-1) if side == 'left' else range(len(g.roof_stations)-2,-1,-1)
                band, band_rows = [], set()
                for cell in ordered:
                    cells = {f'roof/bay/{bay}/strip/{cell}' for bay in range(count)}
                    if not cells <= available:
                        break
                    band.append(cell)
                    band_rows |= cells
                    width = sum(g.roof_stations[j+1]-g.roof_stations[j] for j in band)
                    if width >= .25*g.span_mm-1e-6:
                        if len(band) > 1:
                            primary_girder = (f'End bays with continuous {len(band)}-strip supported {side}-eave roof girder and paired end anchors',
                                              base | paired_ends | band_rows)
                            primary_variants = [primary_girder,
                                (f'End bays with continuous {len(band)}-strip supported {side}-eave roof girder and full end restraint', base | all_end | band_rows),
                                (f'End bays with continuous {len(band)}-strip supported {side}-eave roof girder', base | end_braces | band_rows)]
                            sequence.extend(primary_variants)
                        break
    if len(rows) == 2:
        sequence.append(('End bays with continuous roof girders at both eaves', base | end_braces | rows[0] | rows[1]))
    if wide_one_sided and primary_girder is not None:
        for pattern in reversed(primary_variants or [primary_girder]):
            sequence.remove(pattern)
            sequence.insert(0, pattern)
    interior = [bay for bay in viable if bay not in ends]
    if interior:
        middle = min(interior, key=lambda bay: (abs((g.bay_stations_mm[bay]+g.bay_stations_mm[bay+1])/2-g.length_mm/2), bay))
        sequence.append(('End bays with central roof collector bracing', base | end_braces | panels(middle, True)))
        sequence.append(('End bays with one central roof/wall bay', base | end_braces | panels(middle, True) | panels(middle)))
    expanded = set(base | end_braces)
    for end in sorted(ends):
        neighbours = [bay for bay in viable if bay not in ends]
        if neighbours:
            bay = min(neighbours, key=lambda b: (abs(b-end), b))
            expanded |= panels(bay, True) | panels(bay)
    sequence.append(('Extend end bracing into neighbouring bays', expanded))
    if layout.pinned:
        sequence.append(('Retained input load-path fallback', layout.pinned | required | end_braces))
    sequence.append(('Available load-path fallback', available | required))
    seen = {frozenset(layout.pinned | required)} if layout.pinned else set()
    for reason, selected in sequence:
        key = frozenset(selected)
        if key and key not in seen:
            seen.add(key)
            yield reason, selected


def wall_rod_expansion_seeds(context, selected):
    """One extra wall anchor, or its full roof collector, before heavy wall steel.

    Fill the largest gap between selected side-wall bracing bays first. A wall
    panel alone is proposed only where a complete eave girder already connects
    it; otherwise include the complete available roof collector bay. Exact
    coupled analysis, not this ordering, determines whether it helps.
    """
    layout = context['layout']
    g = layout.geometry
    bay_count = len(g.bay_stations_mm)-1
    strip_count = len(g.roof_stations)-1
    options = []
    for face, strip in (('left_wall', 0), ('right_wall', strip_count-1)):
        existing = [int(p.split('/')[-1]) for p in selected if p.startswith(face+'/bay/')]
        if not existing:
            continue
        continuous = all(f'roof/bay/{bay}/strip/{strip}' in selected for bay in range(bay_count))
        for bay in range(bay_count):
            wall = f'{face}/bay/{bay}'
            roof = {f'roof/bay/{bay}/strip/{s}' for s in range(strip_count)}
            if wall in selected or wall not in layout.available or (not continuous and not roof <= layout.available):
                continue
            centre = (g.bay_stations_mm[bay]+g.bay_stations_mm[bay+1])/2
            distance = min(abs(centre-(g.bay_stations_mm[b]+g.bay_stations_mm[b+1])/2) for b in existing)
            options.append((-distance, abs(centre-g.length_mm/2), face, bay, wall, roof, continuous))
    if not options:
        return
    _, _, face, bay, wall, roof, continuous = min(options)
    expanded = selected | {wall}
    if continuous:
        yield f'Extra {face} bay {bay+1} anchor for rod bracing', expanded
    if roof <= layout.available and (not continuous or not roof <= expanded):
        yield f'Extra {face} bay {bay+1} with full roof collector for rod bracing', expanded | roof


def _find_practical_layout(context, selections, candidates, criteria, rates, required, progress,
                           max_trials, max_section_solves, *, seed_override=None, compare_compact=True):
    started = perf_counter()
    original = context['layout']
    evidence = {'strategy': 'practical', 'method': 'End load paths, eccentric-plan restraint and bounded compact-layout comparison',
                'scope': 'Practical passing arrangement with up to two extra-bay rod alternatives for heavy wall steel and up to four compact load-path patterns within the layout budget, stopping at the first preferred/lower-cost passing option. Includes materials, fittings and panel installation. No global optimum claim.',
                'blocked_panels': sorted(original.blocked), 'required_panels': sorted(required),
                'max_layout_trials': max_trials, 'max_section_solves_per_trial': max_section_solves,
                'trials': [], 'section_polish': False,
                'ordering_basis': 'One-sided nominal span/eave >= 4 prioritises a supported-eave roof girder with end-wall restraint; a search heuristic, not a design limit.'}
    evidence.update(max_braced_bay_spacing=max_bay_spacing(context),spacing_rejections=[])
    if not original.available:
        raise LayoutSearchStopped('All panels are blocked; no layout can be tried', evidence)
    seeds = practical_seeds(context, required) if seed_override is None else seed_override
    seen_spacing=set()
    for reason, selected in seeds:
        if len(evidence['trials']) >= max_trials:
            break
        try:
            if seed_override is None:
                repaired=repair_spacing(context,selected)
                if repaired!=selected:
                    reason+=f'; intermediate roof/wall bays (maximum {max_bay_spacing(context)})'
                selected=repaired
            spacing=spacing_assessment(original,max_bay_spacing(context),selected)
            if not spacing['passes']:
                evidence['spacing_rejections'].append({'reason':reason,'spacing':spacing})
                continue
        except AnalysisError as error:
            evidence['spacing_rejections'].append({'reason':reason,'error':str(error)})
            continue
        signature=frozenset(selected)
        if signature in seen_spacing:
            continue
        seen_spacing.add(signature)
        if progress:
            progress(len(evidence['trials']), max_trials, reason)
        local = layout_context(context, selected)
        chosen = dict(selections)
        if isinstance(chosen['strut'], StrutPlan):
            bays = {int(p.split('/')[2]) for p in selected if '/bay/' in p}
            plan = chosen['strut']
            chosen['strut'] = replace(plan, members={key: replace(member, location={**member.location,
                'braced_bay': member.location['bay'] in bays}) if isinstance(member, StrutAssembly) else member
                for key, member in plan.members.items()})
        record = {'trial': len(evidence['trials'])+1, 'reason': reason, 'selected_panels': sorted(selected),
                  'section_sizing': False, 'passes': False, 'adopted': False}
        evidence['trials'].append(record)
        try:
            screen = solve_selection(local, chosen, criteria, rates)
            record.update(screen_global_sls_ratio=screen['design']['global_sls']['ratio'],
                          screen_passes=screen['design']['passes'])
            # Do not try to cure severe one-sided sway by a long sequence of
            # member upgrades when an untried end-wall restraint path is next.
            faces = {original.panels[p].face for p in selected if p.startswith(('left_wall/', 'right_wall/'))}
            if (criteria.include_serviceability and len(faces) == 1
                    and not any(p.startswith(('front_end/', 'back_end/')) for p in selected)
                    and any(p.startswith(('front_end/', 'back_end/')) for p in original.available)
                    and screen['design']['global_sls']['ratio'] > 1.25
                    and len(evidence['trials']) < max_trials):
                record['decision'] = 'Try end-wall restraint before repeated SLS upsizing'
                continue
            def sizing_progress(step, total, message):
                if progress:
                    progress(len(evidence['trials'])-1, max_trials, message)
            record['section_sizing'] = True
            brace_floors = {g: candidate_stiffness(chosen[g]) for g in ('roof_brace','wall_brace','end_brace')}
            # Feasibility has first use of the solve budget. Reserving two
            # polish checks previously discarded near-passing girders early.
            sizing_budget = max_section_solves
            retain = seed_override is not None or reason.startswith('Verify current')
            same_roof_wall = {p for p in selected if not p.startswith(('front_end/', 'back_end/'))} == {
                p for p in original.pinned if not p.startswith(('front_end/', 'back_end/'))}
            if not same_roof_wall and 'girder' in reason and any(not isinstance(chosen[g],dict) or chosen[g].get('component_type') != 'wire_rope'
                                          for g in ('roof_brace','wall_brace','end_brace')):
                # A new girder load path need not inherit large angle/rod EA
                # from a previous topology. Let local demand sizing reconsider
                # those groups, with full exact acceptance as usual.
                retain = False
            if reason.startswith('Verify current') and screen['design']['passes']:
                # Establish the checked incumbent without repeating catalogue
                # sizing that was already done for the saved input layout.
                result = screen
                record['section_sizing'] = False
                result['sizing_search'] = {'method': 'Verified input incumbent',
                    'scope': 'Current sections checked once before compact layout trials; no repeated catalogue sizing.',
                    'candidate_screens': 0, 'rounds': [], 'exact_solves': 1,
                    'max_solves': max_section_solves, 'cost_reduction_trials': [], 'fast': True}
            else:
                result, chosen = auto_size(local, chosen, candidates, criteria, rates, sizing_progress,
                                           max_solves=sizing_budget, fast=True, initial_result=screen,
                                           retain_brace_stiffness=retain)
            if seed_override is not None and max_section_solves >= 12:
                search = dict(result.get('sizing_search', {}))
                used = search.get('exact_solves', sizing_budget)
                best, balancing, extra = balance_compact_braces(local, (result,chosen), candidates, criteria, rates,
                    brace_floors, max_section_solves-used, sizing_progress)
                result, chosen = best
                used += extra
                if any(t['adopted'] for t in balancing) and used < max_section_solves:
                    result, chosen = auto_size(local, chosen, candidates, criteria, rates, sizing_progress,
                        max_solves=max_section_solves-used+1, fast=True, initial_result=result, retain_brace_stiffness=True)
                    used += result.get('sizing_search', {}).get('exact_solves', 1)-1
                search.update(exact_solves=used, max_solves=max_section_solves, brace_balance_trials=balancing)
                result['sizing_search'] = search
            record.update(passes=result['design']['passes'], estimated_cost=result['design']['estimated_cost'],
                          global_sls_ratio=result['design']['global_sls']['ratio'],
                          taut_tangent_verified=result['design']['taut_tangent_verified'],
                          section_labels={g: candidate_name(c) for g, c in chosen.items()},
                          exact_solves=result.get('sizing_search', {}).get('exact_solves'))
            if not result['design']['passes']:
                continue
            record['adopted'] = True
            evidence.update(layout_trials=len(evidence['trials']), winning_trial=record['trial'],
                            selected_panels=sorted(selected), elapsed_seconds=perf_counter()-started,
                            stopped_after_pass=True, budget_exhausted=False)
            # A passing angle layout is not evidence that another rod-braced
            # wall bay is inefficient. Keep the proven roof load path and try
            # at most two expansions, sharing the overall topology/solve caps.
            wall_rod_adopted = False
            if compare_compact and family(chosen['wall_brace']) == 'other' and len(evidence['trials']) < max_trials:
                rods = []
                for option in candidates['wall_brace']:
                    if family(option) != 'rod' or not policy_eligible(local, 'wall_brace', option):
                        continue
                    try:
                        group_cost(result, 'wall_brace', option, rates)
                    except AnalysisError:
                        continue  # Missing fittings/rates never become free options.
                    rods.append(option)
                expansions = list(wall_rod_expansion_seeds(local, selected)) if rods else []
                if expansions:
                    evidence['wall_rod_comparison'] = {'attempted': True, 'adopted': False,
                        'pattern_limit': min(2, max_trials-len(evidence['trials'])), 'patterns_checked': 0}
                for label, panels in expansions:
                    if len(evidence['trials']) >= max_trials:
                        break
                    offset = len(evidence['trials'])
                    def rod_progress(step, total, message):
                        if progress:
                            progress(offset+step, max_trials, 'Extra-bay rod layout: '+message)
                    evidence['wall_rod_comparison']['patterns_checked'] += 1
                    rod_start = {**chosen, 'wall_brace': max(rods, key=candidate_stiffness)}
                    rod_pool = {**candidates, 'wall_brace': rods}
                    try:
                        alternative = _find_practical_layout(local, rod_start, rod_pool, criteria, rates, required,
                            rod_progress, 1, max_section_solves,
                            seed_override=[('Extra-bay rod comparison: '+label, panels)], compare_compact=False)
                    except LayoutSearchCancelled:
                        raise
                    except LayoutSearchStopped as error:
                        alternative, extra_trials = None, error.evidence['trials']
                    else:
                        extra_trials = alternative[0]['layout_search']['trials']
                    for trial in extra_trials:
                        trial['trial'] += offset
                        trial['adopted'] = False
                    evidence['trials'].extend(extra_trials)
                    evidence.update(layout_trials=len(evidence['trials']), stopped_after_pass=False,
                                    stopped_after_wall_rod_comparison=True)
                    if alternative is None:
                        continue
                    checked, resized, expanded_layout = alternative
                    expanded_context = layout_context(local, expanded_layout.pinned)
                    if selection_score(expanded_context, resized, checked) < selection_score(local, chosen, result):
                        result, chosen, local = checked, resized, expanded_context
                        wall_rod_adopted = True
                        evidence['wall_rod_comparison']['adopted'] = True
                        evidence['trials'][-1]['adopted'] = True
                        evidence.update(winning_trial=evidence['trials'][-1]['trial'], selected_panels=sorted(expanded_layout.pinned))
                        break
            # A flat installation allowance must influence layout choice, not
            # just the displayed total. Progress through a few compact girder
            # patterns, warm-started from the verified sections. No panel
            # deletion sweep or restarted broad cost search.
            if compare_compact and not wall_rod_adopted and panel_installation_rate(rates) > 0 and len(evidence['trials']) < max_trials:
                alternatives = [(label, panels) for label, panels in practical_seeds(context, required)
                                if label.startswith('End ') and len(panels) < len(selected)]
                g = original.geometry
                bay_count = len(g.bay_stations_mm)-1
                strip_count = len(g.roof_stations)-1
                full_bays = {b for b in range(bay_count) if all(f'roof/bay/{b}/strip/{s}' in selected for s in range(strip_count))}
                girder_cells = {f'roof/bay/{b}/strip/{s}' for s in range(strip_count)
                                if all(f'roof/bay/{y}/strip/{s}' in selected for y in range(bay_count)) for b in range(bay_count)}
                if girder_cells and len(full_bays) < bay_count:
                    # Keep an established wind girder continuous and at its
                    # proven depth when reducing end restraints. Dense roofs
                    # are not mistaken for a girder that cannot be compacted.
                    alternatives = [(label,p) for label,p in alternatives if girder_cells <= p]
                restrained = [(label, panels) for label, panels in alternatives
                              if any(p.startswith(('front_end/', 'back_end/')) for p in panels)]
                roof_bays = {int(p.split('/')[2]) for p in selected if p.startswith('roof/')}
                # Remove a single interior braced bay before attempting a jump
                # from four bays to two. Retain the bay nearest the governing
                # movement node; both wind directions still receive exact checks.
                contractions = []
                continuous = any(all(f'roof/bay/{bay}/strip/{strip}' in selected for bay in range(len(original.geometry.bay_stations_mm)-1))
                                 for strip in (0, len(original.geometry.roof_stations)-2))
                if len(roof_bays) > 2 and not continuous:
                    node_y = result.get('nodes', [{}])[result['design']['global_sls'].get('node', 0)].get('point_mm', [0,0,0])[1]
                    interior_bays = roof_bays-{min(roof_bays), max(roof_bays)}
                    for bay in sorted(interior_bays, key=lambda b: (-abs((original.geometry.bay_stations_mm[b]+original.geometry.bay_stations_mm[b+1])/2-node_y), b)):
                        removal = {p for p in selected if '/bay/' in p and int(p.split('/')[2]) == bay}
                        if not removal & required:
                            contractions.append((f'Remove interior bracing bay {bay+1}', selected-removal))
                girder_options = [(label, panels) for label, panels in alternatives if 'continuous' in label and 'girder' in label]
                if girder_options:
                    compact_patterns = girder_options[:4]
                elif contractions:
                    compact_patterns = contractions[:1]
                else:
                    compact_patterns = [min(restrained or alternatives, key=lambda item: (len(item[1]), item[0]))] if alternatives else []
                if compact_patterns:
                    evidence['compact_comparison'] = {'attempted': True, 'adopted': False,
                        'installation_rate_per_panel': panel_installation_rate(rates),
                        'pattern_limit': min(4, max_trials-len(evidence['trials'])), 'patterns_checked': 0}
                for label, panels in compact_patterns:
                    if len(evidence['trials']) >= max_trials:
                        break
                    offset = len(evidence['trials'])
                    def compact_progress(step, total, message):
                        if progress:
                            progress(offset+step, max_trials, 'Compact layout: '+message)
                    evidence['compact_comparison']['patterns_checked'] += 1
                    try:
                        compact = _find_practical_layout(context, chosen, candidates, criteria, rates, required,
                            compact_progress, 1, max_section_solves,
                            seed_override=[('Compact installation comparison: '+label, panels)], compare_compact=False)
                    except LayoutSearchCancelled:
                        raise
                    except LayoutSearchStopped as error:
                        compact = None
                        extra_trials = error.evidence['trials']
                    else:
                        extra_trials = compact[0]['layout_search']['trials']
                    for trial in extra_trials:
                        trial['trial'] += offset
                        trial['adopted'] = False
                    evidence['trials'].extend(extra_trials)
                    evidence.update(layout_trials=len(evidence['trials']), stopped_after_pass=False,
                                    stopped_after_compact_comparison=True)
                    if compact is not None:
                        checked, resized, compact_layout = compact
                        compact_context = layout_context(context, compact_layout.pinned)
                        old_score = (*selection_score(local, chosen, result), len(selected))
                        new_score = (*selection_score(compact_context, resized, checked), len(compact_layout.pinned))
                        if new_score < old_score:
                            result, chosen, local = checked, resized, compact_context
                            evidence['compact_comparison']['adopted'] = True
                            evidence['trials'][-1]['adopted'] = True
                            evidence.update(winning_trial=evidence['trials'][-1]['trial'],
                                            selected_panels=sorted(compact_layout.pinned))
                            # First verified improvement is enough; do not
                            # continue a wider least-cost search to the cent.
                            break
            evidence['elapsed_seconds'] = perf_counter()-started
            result['layout_search'] = evidence
            if evidence['scope'] not in result['limitations']:
                result['limitations'].append(evidence['scope'])
            return result, chosen, local['layout']
        except LayoutSearchCancelled:
            raise
        except AnalysisError as error:
            record['analysis_error'] = str(error)
    evidence.update(layout_trials=len(evidence['trials']), elapsed_seconds=perf_counter()-started)
    if not evidence['trials'] and evidence['spacing_rejections']:
        raise LayoutSearchStopped(f"Blocked panels prevent a compliant braced bay within every {max_bay_spacing(context)} bays. Review the blocked locations or maximum braced-bay spacing. Layout and sections unchanged.",evidence)
    raise LayoutSearchStopped('No passing practical layout found within the short search. Review blocked paths/ratings or use Detailed cost search for a broader search. Layout and sections unchanged.', evidence)


def find_layout(context, selections, candidates, criteria, rates, *, required=(), progress=None,
                max_trials=None, max_section_solves=None, strategy='practical'):
    """Default to a short load-path search; broader cost search is explicit."""
    if strategy not in ('practical', 'cost'):
        raise ValueError('Unknown layout search strategy')
    panel_installation_rate(rates)
    max_trials = (6 if strategy == 'practical' else 48) if max_trials is None else max_trials
    max_section_solves = 12 if max_section_solves is None else max_section_solves
    for value, minimum, label in ((max_trials, 1, 'Layout trial'), (max_section_solves, 2, 'Section solve')):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(label+' budget is invalid')
    required = set(required)
    if not required <= context['layout'].available:
        raise AnalysisError('Required panels must be known, unblocked panels')
    if strategy == 'cost':
        result, chosen, layout = find_cost_layout(context, selections, candidates, criteria, rates,
            required=required, progress=progress, max_trials=max_trials, max_section_solves=max_section_solves)
        result['layout_search']['strategy'] = 'cost'
        return result, chosen, layout
    return _find_practical_layout(context, selections, candidates, criteria, rates, required, progress,
                                  max_trials, max_section_solves)
