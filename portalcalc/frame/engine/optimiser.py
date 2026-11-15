"""The structure optimiser: depth sweep, physics-screened search and building-load screening."""

import math
import time

from portalcalc.frame.common import MAIN_TRUSS_DEPTH_MIN_RATIO
from portalcalc.frame.reduced_optimizer import build_screen_from_portalcalc


class OptimiserMixin:
    """Top-level auto-size / optimise entry points."""

    def optimisation_progress_update(self, step=None, label=None, exact_solves=None, busy=False):
        # A caller running the auto-size inside a larger job (the frame +
        # bracing optimiser) can follow its steps 1-12 on its own progress bar.
        listener = getattr(self, "optimisation_progress_listener", None)
        if listener is not None:
            listener(step, label, exact_solves)
        progress = getattr(self, "optimisation_progress_dialog", None)
        if progress is None:
            return
        if busy:
            progress.setRange(0, 0)
        else:
            progress.setRange(0, 12)
            if step is not None:
                progress.setValue(max(0, min(int(step), 12)))
        if label:
            if exact_solves is not None:
                label = f"{label}\nExact solves: {exact_solves}"
            progress.setLabelText(label)
        self.process_ui_events()

    def optimisation_progress_finish(self):
        progress = getattr(self, "optimisation_progress_dialog", None)
        self.optimisation_progress_dialog = None
        if progress is None:
            return
        progress.setRange(0, 12)
        progress.setValue(12)
        progress.close()
        progress.deleteLater()
        self.process_ui_events()

    def run_future_canopy_optimisation(self, show_completion=True):
        from portalcalc.frame.future_canopy_design import run
        return run(self, show_completion=show_completion)

    def auto_size_depth_sweep(self, seed, exact_solves, coarse_step=100.0, descent_moves=4):
        """Coupled truss depth and section search around a verified design.

        The stiffness screen ranks by serviceability, so it favours deep
        trusses. A strength-governed frame can be cheaper shallower with
        lighter columns (factory defaults: 1000 mm with 250UB31.4 columns
        against 1300 mm with 360UB44.7), and a sway-governed one deeper with
        lighter columns and chords. A single depth step can't reach either,
        because the sections have to change with the depth.

        Each trial depth gets the cheapest sections its own solved envelopes
        allow (auto_size_frozen_candidate, all groups at once), then member and
        serviceability repairs to an exact pass (the column sway repair stiffens
        both main columns together: auto_size_repair_column_pair), then one
        exact step lighter per column decision, kept if it passes and saves.
        Keeping the seed's columns instead of stripping them was tried and was
        worse or equal (harness preset $49,062 against $44,719; factory
        defaults $35,216 against $35,036; PortalTest2 equal). Depths are tried every
        ``coarse_step`` over the permitted range, then the 50 mm neighbours of
        the cheapest, and the cheapest of all gets a short cheaper-neighbour
        descent. Only exactly verified states are compared, by full cost.
        Returns (best state or None, exact_solves, report lines).
        """
        if self.frame_system_combo.currentText() != "Truss" or seed is None:
            return None, exact_solves, []
        try:
            minimum = self.round_up_to_increment(self.truss_depth_from_span(MAIN_TRUSS_DEPTH_MIN_RATIO), 50.0)
            maximum = math.floor(self.max_main_truss_depth_mm() / 50.0) * 50.0
        except (TypeError, ValueError):
            return None, exact_solves, []
        if maximum < minimum:
            return None, exact_solves, []
        results = {}
        solves = [exact_solves]
        column_groups = {"LEFT_COLUMN", "RIGHT_COLUMN", "INTERNAL_COLUMN", "LEFT_LEAN_COLUMN", "RIGHT_LEAN_COLUMN"}

        def solve():
            self.generate_and_solve()
            self.process_ui_events()
            solves[0] += 1

        def evaluate(depth):
            depth = float(depth)
            if depth in results:
                return results[depth]
            self.auto_size_restore_state(seed)
            self.depth_input.setText(f"{depth:.0f}")
            # One web size while sizing; the two-size split is made at the end.
            self.auto_web_profile_assignments = {}
            self.auto_web_assignment_basis = {}
            self.auto_web_joint_doubler_sides = {}
            solve()
            specs = self.active_auto_size_group_specs()
            if not self.design_envelope_actions or not specs:
                results[depth] = None
                return None
            decisions = {}
            for spec in specs:
                decisions.setdefault(id(spec[1]), {"combo": spec[1], "specs": []})["specs"].append(spec)
            cache = {}
            for decision in decisions.values():   # columns, then chords, then webs (to fit the chords)
                candidate = self.auto_size_frozen_candidate(decision["combo"], decision["specs"], cache)
                if candidate is not None:
                    decision["combo"].setCurrentIndex(candidate[3])
            solve()
            for _ in range(4):
                specs = self.active_auto_size_group_specs()
                ratio, _, _ = self.auto_size_overall_ratio(specs)
                if self.auto_size_design_acceptable(specs, ratio):
                    break
                if self.auto_size_serviceability_ratio()[0] >= ratio - 1e-9:
                    _, _, sls_solves = self.auto_size_repair_serviceability(specs, {}, max_passes=2)
                    solves[0] += sls_solves
                    continue
                if not self.auto_size_repair_failed_members(specs, {}):
                    break
                solve()
            specs = self.active_auto_size_group_specs()
            ratio, _, _ = self.auto_size_overall_ratio(specs)
            if not self.auto_size_design_acceptable(specs, ratio):
                results[depth] = None
                return None
            cost = float((self.costing_summary or {}).get("total_cost", float("inf")))
            results[depth] = lighten_columns(self.auto_size_capture_state(specs, ratio, cost))
            return results[depth]

        def lighten_columns(state):
            """One exact step lighter for each column decision, kept only if it passes and saves."""
            specs = self.active_auto_size_group_specs()
            combos = {}
            for _, combo, group in specs:
                if group in column_groups:
                    combos.setdefault(id(combo), (combo, group))
            for combo, group in combos.values():
                current = self.selected_profile(combo)
                if current is None:
                    continue
                current_cost = self.auto_size_profile_cost_key(current)
                envelope_groups = [g for _, c, g in specs if c is combo]
                lighter = []
                for index in self.auto_size_candidate_indices(combo, group, min_metric_step=0.0):
                    profile = self.catalog.get(combo.itemText(index))
                    if profile is None or self.auto_size_profile_cost_key(profile) >= current_cost - 1e-9:
                        continue
                    if any(self.member_and_connection_ratio(
                            profile, (self.design_envelope_actions or {}).get(g), g)[0] > 1.0 for g in envelope_groups):
                        continue
                    lighter.append((self.auto_size_profile_cost_key(profile), profile.I_analysis, index))
                if not lighter:
                    continue
                combo.setCurrentIndex(max(lighter)[2])      # the next step down
                solve()
                ratio, _, _ = self.auto_size_overall_ratio(specs)
                cost = float((self.costing_summary or {}).get("total_cost", float("inf")))
                if self.auto_size_design_acceptable(specs, ratio) and cost < state["cost"] - 1e-6:
                    state = self.auto_size_capture_state(specs, ratio, cost)
                else:
                    self.auto_size_restore_state(state)
                    solve()
            return state

        depths = []
        depth = minimum
        while depth <= maximum + 1e-9:
            depths.append(depth)
            depth += coarse_step
        if depths[-1] < maximum - 1e-9:
            depths.append(maximum)
        for number, depth in enumerate(depths, start=1):
            self.optimisation_progress_update(
                10, f"Depth sweep: {depth:.0f} mm ({number} of {len(depths)})...", solves[0])
            evaluate(depth)
        passing = {d: s for d, s in results.items() if s is not None}
        if passing:
            best_depth = min(passing, key=lambda d: passing[d]["cost"])
            for depth in (best_depth - 50.0, best_depth + 50.0):
                if minimum - 1e-9 <= depth <= maximum + 1e-9:
                    evaluate(depth)
        passing = {d: s for d, s in results.items() if s is not None}
        lines = [f"  {d:.0f} mm: " + (f"${s['cost']:,.0f} (exact {float(s['ratio']):.3f})" if s else "no verified design")
                 for d, s in sorted(results.items())]
        if not passing:
            return None, solves[0], lines
        best = min(passing.values(), key=lambda s: s["cost"])
        # Short cheaper-neighbour descent on the cheapest depth only.
        self.auto_size_restore_state(best)
        solve()
        specs = self.active_auto_size_group_specs()
        rejected = set()
        for _ in range(descent_moves):
            neighbour = self.auto_size_exact_neighbour_candidate(
                specs, {}, max_frozen_ratio=1.08, rejected=rejected,
                min_estimated_saving=max(100.0, best["cost"] * 0.0015))
            if neighbour is None:
                break
            _, _, candidate_name, combo, candidate_index, previous_name, _, move_key = neighbour
            combo.setCurrentIndex(candidate_index)
            solve()
            ratio, _, _ = self.auto_size_overall_ratio(specs)
            cost = float((self.costing_summary or {}).get("total_cost", float("inf")))
            if self.auto_size_design_acceptable(specs, ratio) and cost < best["cost"] - 1e-6:
                lines.append(f"  descent {previous_name} -> {candidate_name}, exact {ratio:.3f}, "
                             f"saving ${best['cost'] - cost:,.0f}")
                best = self.auto_size_capture_state(specs, ratio, cost)
            else:
                rejected.add(move_key)
                self.auto_size_restore_state(best)
                solve()
        return best, solves[0], lines

    def truss_depth_outside_range_note(self):
        """A warning when the entered truss depth is outside Optimise's permitted range, else ""."""
        if self.frame_system_combo.currentText() != "Truss":
            return ""
        try:
            current = float(self.depth_input.text())
            minimum = self.truss_depth_from_span(MAIN_TRUSS_DEPTH_MIN_RATIO)
            maximum = math.floor(self.max_main_truss_depth_mm() / 50.0) * 50.0
        except (TypeError, ValueError):
            return ""
        if minimum - 1e-9 <= self.round_up_to_increment(current, 50.0) <= maximum + 1e-9:
            return ""
        used = min(max(self.round_up_to_increment(current, 50.0), minimum), maximum)
        reason = "the Max truss depth" if current > maximum else "the minimum (3% of span)"
        return (f"WARNING: the entered truss depth ({current:.0f} mm) is outside the permitted range "
                f"{minimum:.0f}-{maximum:.0f} mm, set by {reason}. Optimise started from {used:.0f} mm and "
                "searched only that range, so the starting design shown is at that depth. "
                "Change Max truss depth to let it search deeper.")

    def run_physics_structure_optimisation(self, show_completion=True):
        """Optimise: the stiffness-first search with exact verification.

        A fast reduced elastic screen finds a low-cost stiffness distribution,
        which is projected to catalogue sections; the leading designs are
        verified exactly, repaired and locally refined. This is the only frame
        optimiser (the Optimise button, Optimise frame + bracing, the
        calculation service, Configurator Review and the hay-shed search).
        Progress is reported as steps 1-12 (optimisation_progress_update).
        """
        button = getattr(self, "optimise_structure_button", None)
        button_text = button.text() if button is not None else ""
        if button is not None:
            button.setEnabled(False)
            button.setText("Optimising...")
        self.process_ui_events()
        self.optimisation_progress_update(1, "Preparing the starting design...")
        started = time.perf_counter()
        exact_solves = 0
        starting_state = None
        adopted = False
        depth_clamp_note = ""
        starting_state_for_failure = None   # the user's own columns, when Optimise matched them
        try:
            self.auto_web_profile_assignments = {}
            self.auto_web_assignment_basis = {}
            self.auto_web_joint_doubler_sides = {}
            # Physics Optimise captures its starting state as a viable
            # candidate.  Apply the same depth bounds used by the production
            # optimiser before that capture; otherwise a depth selected before
            # a user lowers the optional cap can be screened and re-adopted.
            if self.frame_system_combo.currentText() == "Truss":
                try:
                    minimum_depth = self.truss_depth_from_span(MAIN_TRUSS_DEPTH_MIN_RATIO)
                    entered_maximum = self.max_main_truss_depth_mm()
                    maximum_depth = math.floor(entered_maximum / 50.0) * 50.0
                    if maximum_depth < minimum_depth:
                        raise ValueError(
                            f"The maximum truss depth ({entered_maximum:.0f} mm) is below "
                            f"the minimum permitted depth ({minimum_depth:.0f} mm)."
                        )
                    current_depth = float(self.depth_input.text())
                    bounded_depth = self.round_up_to_increment(current_depth, 50.0)
                    bounded_depth = min(max(bounded_depth, minimum_depth), maximum_depth)
                    # Outside the permitted range (not just rounding): say so at the end.
                    depth_clamp_note = self.truss_depth_outside_range_note()
                    if abs(bounded_depth - current_depth) > 1e-9:
                        self.depth_input.setText(f"{bounded_depth:.0f}")
                except ValueError:
                    raise
            self.generate_and_solve()
            if self.structure is None or not self.design_envelope_actions:
                raise ValueError("The current frame could not be generated and solved.")
            matched_note = ""
            if self.columns_should_match():
                # Symmetric building: one column section both sides. An unequal
                # start takes the stiffer of the two on both (sway can't get worse).
                original_state = self.auto_size_capture_state(self.active_auto_size_group_specs())
                left, right = (self.selected_profile(c) for c in (self.left_column_profile_combo,
                                                                   self.right_column_profile_combo))
                self._matched_columns = True
                matched_note = "Columns matched left and right (symmetric building)."
                if left is not None and right is not None and left.name != right.name:
                    stiffer = max((left, right), key=lambda p: (p.I_analysis, p.A))
                    if stiffer is right:
                        self.left_column_profile_combo.setCurrentIndex(
                            self.left_column_profile_combo.findText(self.right_column_profile_combo.currentText()))
                    self._sync_matched_columns()
                    matched_note += f" The start's {left.name} / {right.name} became {stiffer.name} both sides."
                    self.generate_and_solve()
                starting_state_for_failure = original_state
            active_specs = self.active_auto_size_group_specs()
            start_ratio, start_group, start_check = self.auto_size_overall_ratio(active_specs)
            start_cost = float((self.costing_summary or {}).get("total_cost", float("inf")))
            starting_state = self.auto_size_capture_state(active_specs, start_ratio, start_cost)

            self.optimisation_progress_update(2, "Stiffness screen...", exact_solves)
            screen_started = time.perf_counter()
            screen, bindings, diagnostics = build_screen_from_portalcalc(self)
            screening = screen.run(candidate_count=20)
            screen_seconds = time.perf_counter() - screen_started
            candidate_lines = []
            repair_lines = []
            passing_seeds = []
            failed_seeds = []
            candidate_total = max(len(screening.candidates), 1)
            for number, candidate in enumerate(screening.candidates, start=1):
                self.optimisation_progress_update(
                    3 + 4 * (number - 1) // candidate_total,
                    f"Checking catalogue candidate {number} of {candidate_total}...", exact_solves)
                self.auto_size_restore_state(starting_state)
                for variable_name, profile_name in candidate.selected_sections.items():
                    binding = bindings[variable_name]
                    index = binding["indices"].get(profile_name)
                    if index is not None:
                        binding["combo"].setCurrentIndex(index)
                    depth = binding.get("depths", {}).get(profile_name)
                    if depth is not None:
                        binding["depth_input"].setText(f"{depth:.0f}")
                self.generate_and_solve()
                self.process_ui_events()
                exact_solves += 1
                trial_specs = self.active_auto_size_group_specs()
                ratio, group, check = self.auto_size_overall_ratio(trial_specs)
                cost = float((self.costing_summary or {}).get("total_cost", float("inf")))
                state = self.auto_size_capture_state(trial_specs, ratio, cost)
                if self.auto_size_design_acceptable(trial_specs, ratio):
                    passing_seeds.append(state)
                else:
                    failed_seeds.append((candidate.score, cost, state))
                selection = ", ".join(
                    f"{name}={profile}" for name, profile in candidate.selected_sections.items()
                )
                candidate_lines.append(
                    f"  {number}. predicted SLS {candidate.governing_ratio:.3f} "
                    f"({candidate.governing_constraint}); exact {ratio:.3f} {group} {check}; "
                    f"${cost:,.0f} | {selection}"
                )

            if self.auto_size_design_acceptable(active_specs, start_ratio):
                passing_seeds.append(starting_state)
            if not passing_seeds and failed_seeds:
                self.optimisation_progress_update(7, "Repairing the nearest candidates...", exact_solves)
                def promote_doubled_chord(group):
                    combo = self.chord_group_combo(group)
                    current_profile = self.selected_profile(combo) if combo is not None else None
                    if combo is None or current_profile is None:
                        return []
                    current_metric = self.auto_size_profile_metric(current_profile, group)
                    related_web_group = {
                        "TOP": "WEB", "BOTTOM": "WEB",
                        "LEFT_CANOPY_TOP": "LEFT_CANOPY_WEB",
                        "LEFT_CANOPY_BOTTOM": "LEFT_CANOPY_WEB",
                        "RIGHT_CANOPY_TOP": "RIGHT_CANOPY_WEB",
                        "RIGHT_CANOPY_BOTTOM": "RIGHT_CANOPY_WEB",
                    }.get(group)
                    web_combo = self.web_group_combo(related_web_group) if related_web_group else None
                    web_profile = self.selected_profile(web_combo) if web_combo is not None else None
                    options = []
                    for index in self.auto_size_candidate_indices(combo, group, min_metric_step=0.0):
                        profile = self.catalog.get(combo.itemText(index))
                        if profile is None:
                            continue
                        if self.profile_family_name(profile) not in {"SHS", "RHS", "CHS"}:
                            continue
                        metric = self.auto_size_profile_metric(profile, group)
                        if metric <= current_metric * 1.001:
                            continue
                        if (
                            web_profile is not None
                            and related_web_group is not None
                            and not self.web_profile_fits_chords(
                                web_profile,
                                related_web_group,
                                chord_profiles=(profile, profile),
                            )
                        ):
                            continue
                        options.append((metric, self.auto_size_search_cost_key(profile, group), profile.name, index))
                    if not options:
                        return []
                    _, _, candidate_name, candidate_index = min(options)
                    before = combo.currentText()
                    combo.setCurrentIndex(candidate_index)
                    return [f"{before} -> {candidate_name} | targeted doubled-chord length step"]

                def state_right_to_left_stiffness(item):
                    state = item[2]
                    metrics = {}
                    for combo, index in state["combos"]:
                        if combo not in {self.left_column_profile_combo, self.right_column_profile_combo}:
                            continue
                        profile = self.catalog.get(combo.itemText(index))
                        if profile is not None:
                            metrics[combo] = max(float(profile.I_analysis), 1e-9)
                    return metrics.get(self.right_column_profile_combo, 1e-9) / metrics.get(
                        self.left_column_profile_combo, 1e-9
                    )

                repair_rankings = (
                    sorted(failed_seeds, key=lambda item: (item[0], item[1])),
                    sorted(failed_seeds, key=lambda item: (item[1], item[0])),
                    sorted(failed_seeds, key=lambda item: (item[2]["ratio"], item[1], item[0])),
                    sorted(
                        failed_seeds,
                        key=lambda item: (
                            item[0] + max(float(item[2]["ratio"]) - 1.0, 0.0) ** 2,
                            item[1],
                        ),
                    ),
                    sorted(
                        failed_seeds,
                        key=lambda item: (-state_right_to_left_stiffness(item), item[1], item[0]),
                    ),
                )
                repair_seeds = []
                repair_signatures = set()
                for ranking in repair_rankings:
                    for repair_seed in ranking:
                        state = repair_seed[2]
                        signature = (
                            state["depth"], state.get("left_depth"), state.get("right_depth"),
                            tuple((id(combo), index) for combo, index in state["combos"]),
                        )
                        if signature in repair_signatures:
                            continue
                        repair_signatures.add(signature)
                        repair_seeds.append(repair_seed)
                        break
                    if len(repair_seeds) >= 5:
                        break
                for seed_number, (_, _, repair_seed) in enumerate(repair_seeds, start=1):
                    self.auto_size_restore_state(repair_seed)
                    basin_passed = False
                    basin_ratio_cache = {}
                    for repair_pass in range(1, 5):
                        trial_specs = self.active_auto_size_group_specs()
                        stalled_ratio, stalled_group, stalled_check = self.auto_size_overall_ratio(trial_specs)
                        stalled_serviceability = self.auto_size_serviceability_ratio()
                        if (
                            stalled_ratio > 1.0
                            and stalled_serviceability[0] >= stalled_ratio - 1e-9
                        ):
                            repair_lines.append(
                                f"  Basin {seed_number}, pass {repair_pass}: "
                                f"deferred {stalled_group} {stalled_check} {stalled_ratio:.3f} "
                                "to bounded serviceability closure"
                            )
                            break
                        changes = []
                        if stalled_ratio > 1.0 and "doubled chord length" in str(stalled_check).lower():
                            changes = promote_doubled_chord(stalled_group)
                        if not changes:
                            changes = self.auto_size_repair_failed_members(
                                trial_specs, basin_ratio_cache,
                            )
                        if not changes:
                            stalled_ratio, stalled_group, _ = self.auto_size_overall_ratio(trial_specs)
                            coupled_groups = {
                                "WEB", "TOP", "BOTTOM",
                                "LEFT_CANOPY_WEB", "LEFT_CANOPY_TOP", "LEFT_CANOPY_BOTTOM",
                                "RIGHT_CANOPY_WEB", "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM",
                            }
                            if stalled_ratio > 1.0 and stalled_group in coupled_groups:
                                changes = self.auto_size_repair_coupled_web_chords(
                                    trial_specs, basin_ratio_cache,
                                )
                        if not changes:
                            repair_lines.append(
                                f"  Basin {seed_number}, pass {repair_pass}: no frozen-envelope repair available"
                            )
                            break
                        self.generate_and_solve()
                        self.process_ui_events()
                        exact_solves += 1
                        ratio, group, check = self.auto_size_overall_ratio(trial_specs)
                        repair_lines.append(
                            f"  Basin {seed_number}, pass {repair_pass}: exact {ratio:.3f} "
                            f"{group} {check} | {'; '.join(changes)}"
                        )
                        if self.auto_size_design_acceptable(trial_specs, ratio):
                            passing_seeds.append(self.auto_size_capture_state(trial_specs, ratio))
                            basin_passed = True
                            break
                    if not basin_passed:
                        trial_specs = self.active_auto_size_group_specs()
                        ratio, _, _ = self.auto_size_overall_ratio(trial_specs)
                        serviceability = self.auto_size_serviceability_ratio()
                        if (
                            not self.auto_size_design_acceptable(trial_specs, ratio)
                            and serviceability[0] >= ratio - 1e-9
                        ):
                            sls_lines, _, sls_solves = self.auto_size_repair_serviceability(
                                trial_specs, basin_ratio_cache, max_passes=2,
                            )
                            exact_solves += sls_solves
                            repair_lines.extend(
                                f"  Basin {seed_number}, serviceability: {line.strip()}"
                                for line in sls_lines
                            )
                            ratio, group, check = self.auto_size_overall_ratio(trial_specs)
                            if self.auto_size_design_acceptable(trial_specs, ratio):
                                passing_seeds.append(
                                    self.auto_size_capture_state(trial_specs, ratio)
                                )
                                repair_lines.append(
                                    f"  Basin {seed_number}: closed at exact {ratio:.3f} {group} {check}"
                                )
            if not passing_seeds:
                failure_report = [
                    "OPTIMISE — NO VERIFIED CANDIDATE",
                    "",
                    f"Reduced model: {diagnostics['variables']} stiffness variable(s), "
                    f"{diagnostics['constraints']} captured SLS constraint(s), "
                    f"{diagnostics['full_free_dofs']} -> {diagnostics['reduced_dofs']} response DOFs",
                    f"Continuous search: {screening.optimum.evaluations} reduced solve(s) in {screen_seconds:.3f} s",
                    "",
                    "Projected catalogue candidates",
                    *candidate_lines,
                ]
                if repair_lines:
                    failure_report.extend(["", "Exact basin repair", *repair_lines])
                failure_report.extend([
                    "",
                    *([depth_clamp_note] if depth_clamp_note else []),
                    "No reduced candidate reached a verified detailed design; the starting design was restored.",
                ])
                self.auto_size_report_text = "\n".join(failure_report)
                raise ValueError(self.auto_size_report_text)

            unique_seeds = {}
            for seed in passing_seeds:
                signature = (
                    seed["depth"], seed.get("left_depth"), seed.get("right_depth"),
                    tuple((id(combo), index) for combo, index in seed["combos"]),
                )
                previous = unique_seeds.get(signature)
                if previous is None or seed["cost"] < previous["cost"]:
                    unique_seeds[signature] = seed
            leading = sorted(unique_seeds.values(), key=lambda state: state["cost"])[:2]
            refined_states = []
            refinement_lines = []
            for number, seed in enumerate(leading, start=1):
                self.optimisation_progress_update(
                    8 + number, f"Refining leading design {number} of {len(leading)}...", exact_solves)
                self.auto_size_restore_state(seed)
                self.generate_and_solve()
                exact_solves += 1
                trial_specs = self.active_auto_size_group_specs()
                current = seed
                changes = []
                rejected = set()
                column_pair = []
                for combo, group in (
                    (self.left_column_profile_combo, "LEFT_COLUMN"),
                    (self.right_column_profile_combo, "RIGHT_COLUMN"),
                ):
                    current_profile = self.selected_profile(combo)
                    envelope = (self.design_envelope_actions or {}).get(group)
                    alternatives = []
                    for index in self.auto_size_candidate_indices(combo, group, min_metric_step=0.0):
                        profile = self.catalog.get(combo.itemText(index))
                        if profile is None or current_profile is None:
                            continue
                        if self.auto_size_profile_cost_key(profile) >= self.auto_size_profile_cost_key(current_profile) - 1e-9:
                            continue
                        if profile.I_analysis >= current_profile.I_analysis - 1e-9:
                            continue
                        frozen_ratio = self.member_and_connection_ratio(profile, envelope, group)[0]
                        if frozen_ratio <= 1.15:
                            alternatives.append((
                                abs(math.log(max(profile.I_analysis, 1e-9) / max(current_profile.I_analysis, 1e-9))),
                                -self.auto_size_profile_cost_key(profile),
                                index,
                                profile.name,
                            ))
                    if alternatives:
                        column_pair.append((combo, min(alternatives)[2], current_profile.name, min(alternatives)[3]))
                if len(column_pair) == 2:
                    for combo, index, _, _ in column_pair:
                        combo.setCurrentIndex(index)
                    self.generate_and_solve()
                    exact_solves += 1
                    ratio, _, _ = self.auto_size_overall_ratio(trial_specs)
                    cost = float((self.costing_summary or {}).get("total_cost", float("inf")))
                    if self.auto_size_design_acceptable(trial_specs, ratio) and cost < current["cost"] - 1e-6:
                        changes.append(
                            f"paired columns {column_pair[0][2]} / {column_pair[1][2]} -> "
                            f"{column_pair[0][3]} / {column_pair[1][3]}, exact {ratio:.3f}, "
                            f"saving ${current['cost'] - cost:,.0f}"
                        )
                        current = self.auto_size_capture_state(trial_specs, ratio, cost)
                    else:
                        self.auto_size_restore_state(current)
                        self.generate_and_solve()
                        exact_solves += 1
                for _ in range(8):
                    neighbour = self.auto_size_exact_neighbour_candidate(
                        trial_specs,
                        {},
                        max_frozen_ratio=1.08,
                        rejected=rejected,
                        min_estimated_saving=max(100.0, current["cost"] * 0.0015),
                    )
                    if neighbour is None:
                        break
                    frozen_ratio, _, candidate_name, combo, candidate_index, previous_name, _, move_key = neighbour
                    combo.setCurrentIndex(candidate_index)
                    self.generate_and_solve()
                    exact_solves += 1
                    ratio, _, _ = self.auto_size_overall_ratio(trial_specs)
                    cost = float((self.costing_summary or {}).get("total_cost", float("inf")))
                    if self.auto_size_design_acceptable(trial_specs, ratio) and cost < current["cost"] - 1e-6:
                        changes.append(
                            f"{previous_name} -> {candidate_name}, frozen {frozen_ratio:.3f}, "
                            f"exact {ratio:.3f}, saving ${current['cost'] - cost:,.0f}"
                        )
                        current = self.auto_size_capture_state(trial_specs, ratio, cost)
                    else:
                        rejected.add(move_key)
                        self.auto_size_restore_state(current)
                        self.generate_and_solve()
                        exact_solves += 1

                if self.frame_system_combo.currentText() == "Truss":
                    try:
                        current_depth = float(current["depth"])
                        minimum_depth = self.truss_depth_from_span(MAIN_TRUSS_DEPTH_MIN_RATIO)
                        maximum_depth = self.max_main_truss_depth_mm()
                    except (TypeError, ValueError):
                        current_depth = minimum_depth = maximum_depth = 0.0
                    depth_states = [current]
                    for trial_depth in (current_depth - 50.0, current_depth + 50.0):
                        if trial_depth < minimum_depth - 1e-9 or trial_depth > maximum_depth + 1e-9:
                            continue
                        self.auto_size_restore_state(current)
                        self.depth_input.setText(f"{trial_depth:.0f}")
                        self.generate_and_solve()
                        exact_solves += 1
                        ratio, _, _ = self.auto_size_overall_ratio(trial_specs)
                        cost = float((self.costing_summary or {}).get("total_cost", float("inf")))
                        if self.auto_size_design_acceptable(trial_specs, ratio):
                            depth_states.append(self.auto_size_capture_state(trial_specs, ratio, cost))
                    depth_best = min(depth_states, key=lambda state: state["cost"])
                    if depth_best["cost"] < current["cost"] - 1e-6:
                        changes.append(
                            f"depth {current['depth']} -> {depth_best['depth']}, "
                            f"saving ${current['cost'] - depth_best['cost']:,.0f}"
                        )
                    current = depth_best

                self.auto_size_restore_state(current)
                self.generate_and_solve()
                exact_solves += 1
                refined_states.append(current)
                refinement_lines.append(
                    f"  Seed {number}: ${seed['cost']:,.0f} -> ${current['cost']:,.0f}; "
                    f"{len(changes)} local improvement(s)"
                )

            best_state = min([*leading, *refined_states], key=lambda state: state["cost"])
            swept_state, exact_solves, sweep_lines = self.auto_size_depth_sweep(best_state, exact_solves)
            if swept_state is not None and swept_state["cost"] < best_state["cost"] - 1e-6:
                sweep_lines.append(f"  Adopted {swept_state['depth']} mm: ${best_state['cost']:,.0f} -> "
                                   f"${swept_state['cost']:,.0f}")
                best_state = swept_state
            elif sweep_lines:
                sweep_lines.append(f"  Kept {best_state['depth']} mm (${best_state['cost']:,.0f})")
            self.auto_size_restore_state(best_state)
            self.generate_and_solve()
            exact_solves += 1
            final_specs = self.active_auto_size_group_specs()
            self.optimisation_progress_update(11, "Two-size web assignment...", exact_solves)
            web_lines = self.auto_size_two_web_assignments(final_specs)
            if web_lines:
                pre_web_state = self.auto_size_capture_state(final_specs)
                self._preserve_web_assignments_on_solve = True
                try:
                    self.generate_and_solve()
                finally:
                    self._preserve_web_assignments_on_solve = False
                exact_solves += 1
                web_ratio, _, _ = self.auto_size_overall_ratio(final_specs)
                web_cost = float((self.costing_summary or {}).get("total_cost", float("inf")))
                if self.auto_size_design_acceptable(final_specs, web_ratio) and web_cost < best_state["cost"]:
                    best_state = self.auto_size_capture_state(final_specs, web_ratio, web_cost)
                else:
                    self.auto_size_restore_state(pre_web_state)
                    self.generate_and_solve()
                    exact_solves += 1
                    web_lines = []

            self.optimisation_progress_update(12, "Final verification...", exact_solves)
            self.auto_size_restore_state(best_state)
            self._preserve_web_assignments_on_solve = True
            try:
                self.generate_and_solve()
            finally:
                self._preserve_web_assignments_on_solve = False
            exact_solves += 1
            final_specs = self.active_auto_size_group_specs()
            final_ratio, final_group, final_check = self.auto_size_overall_ratio(final_specs)
            final_cost = float((self.costing_summary or {}).get("total_cost", float("inf")))
            final_verified = self.auto_size_design_acceptable(final_specs, final_ratio)
            elapsed = time.perf_counter() - started
            report = [
                "OPTIMISE",
                "",
                f"Reduced model: {diagnostics['variables']} stiffness variable(s), "
                f"{diagnostics['constraints']} captured SLS constraint(s), "
                f"{diagnostics['full_free_dofs']} -> {diagnostics['reduced_dofs']} response DOFs",
                f"Baseline stiffness reproduction error: {diagnostics['baseline_matrix_error']:.2e}",
                f"Continuous search: {screening.optimum.evaluations} reduced solve(s) in {screen_seconds:.3f} s",
                f"Relaxed optimum: predicted SLS {screening.optimum.governing_ratio:.3f} "
                f"({screening.optimum.governing_constraint})",
                "",
                "Projected catalogue candidates",
                *candidate_lines,
            ]
            if repair_lines:
                report.extend(["", "Exact basin repair", *repair_lines])
            if refinement_lines:
                report.extend(["", "Detailed local refinement", *refinement_lines])
            if sweep_lines:
                report.extend(["", "Depth sweep (sections sized at each depth)", *sweep_lines])
            if web_lines:
                report.extend(["", "Detailed web refinement", *[f"  {line}" for line in web_lines]])
            report.extend([
                "",
                *([depth_clamp_note] if depth_clamp_note else []),
                *([matched_note] if matched_note else []),
                f"Starting design: ${start_cost:,.0f}, {start_group} {start_check} {start_ratio:.3f}",
                f"Final design: ${final_cost:,.0f}, {final_group} {final_check} {final_ratio:.3f}",
                (
                    f"Change: ${final_cost - start_cost:,.0f} "
                    f"({(final_cost / start_cost - 1.0) * 100.0:+.1f}%)"
                    if math.isfinite(start_cost) and start_cost > 0.0 else "Change: unavailable"
                ),
                f"Detailed verification solves: {exact_solves}",
                f"Total elapsed: {elapsed:.2f} s",
                f"FINAL VERIFICATION: {'OK' if final_verified else 'NOT OK'}",
            ])
            if diagnostics["second_order_screened_as_first_order"]:
                report.append(
                    "Screening note: ranking was first-order; every projected candidate used exact second-order verification."
                )
            self.auto_size_report_text = "\n".join(report)
            self.preserve_auto_size_report = True
            try:
                self.update_section_checks_text(
                    *[
                        self.selected_profile(combo)
                        for combo in (
                            self.left_column_profile_combo, self.right_column_profile_combo,
                            self.internal_column_profile_combo, self.top_profile_combo,
                            self.bottom_profile_combo, self.web_profile_combo,
                            self.left_canopy_top_profile_combo, self.left_canopy_bottom_profile_combo,
                            self.left_canopy_web_profile_combo, self.left_lean_column_profile_combo,
                            self.right_canopy_top_profile_combo, self.right_canopy_bottom_profile_combo,
                            self.right_canopy_web_profile_combo, self.right_lean_column_profile_combo,
                        )
                    ]
                )
            finally:
                self.preserve_auto_size_report = False
            adopted = final_verified
            if final_verified and show_completion:
                self.notify("information", "Optimise", self.auto_size_report_text)
            elif show_completion:
                self.notify("warning", "Optimise", self.auto_size_report_text)
            return {
                "verified": final_verified,
                "ratio": final_ratio,
                "governing_group": final_group,
                "governing_check": final_check,
                "cost": final_cost,
                "exact_solves": exact_solves,
                "screen_seconds": screen_seconds,
                "elapsed_seconds": elapsed,
                "report": self.auto_size_report_text,
                **({"depth_clamp_note": depth_clamp_note} if depth_clamp_note else {}),
            }
        except Exception as error:
            if starting_state is not None and not adopted:
                # With matched columns, put back the user's own (possibly unequal) columns.
                self._matched_columns = False
                self.auto_size_restore_state(starting_state_for_failure or starting_state)
                self.generate_and_solve()
            if show_completion:
                self.notify("critical", "Optimise failed", str(error))
            return {"verified": False, "error": str(error), "exact_solves": exact_solves}
        finally:
            self._matched_columns = False
            if button is not None:
                button.setText(button_text)
                button.setEnabled(True)

    def auto_size_current_preset(self, *, show_completion=True):
        """Size the frame with Optimise (run_physics_structure_optimisation).

        With building loads it sizes on the governing combinations and accepts
        only on a check of all of them (_auto_size_with_building_screening).
        """
        if not getattr(self, "building_load_mode", None) or getattr(self, "building_screening_disabled", False):
            return self.run_physics_structure_optimisation(show_completion=show_completion)
        return self._auto_size_with_building_screening(show_completion=show_completion)

    def _auto_size_with_building_screening(self, *, show_completion=True, max_rounds=3):
        """Phase 5: optimise against the governing building combinations, then check all of them.

        Each round sizes against the active set (combinations governing the
        last full check, plus every gravity combination), then solves the full
        set. A design is accepted only on the full check. If it fails and new
        combinations govern, they join the active set and the next round
        continues from the current design.
        """
        from portalcalc.frame.frame_loading import governing_report
        self._building_active_set = None
        self.building_full_governing_names = set()
        self.generate_and_solve()
        if not self.design_envelope_actions:
            return self.run_physics_structure_optimisation(show_completion=show_completion)
        total = len(getattr(self, "building_combination_names", []) or [])
        active = set(self.building_full_governing_names) | set(self.building_gravity_combination_names)
        lines, result, verified = [], {}, False
        ratio, group, check = 0.0, "-", "-"
        for round_index in range(1, max_rounds + 1):
            self._building_active_set = frozenset(active)
            try:
                result = self.run_physics_structure_optimisation(show_completion=False) or {}
            finally:
                self._building_active_set = None
            if result.get("error"):
                break
            self._preserve_web_assignments_on_solve = True
            try:
                self.generate_and_solve()
            finally:
                self._preserve_web_assignments_on_solve = False
            specs = self.active_auto_size_group_specs()
            ratio, group, check = self.auto_size_overall_ratio(specs)
            verified = self.auto_size_design_acceptable(specs, ratio)
            newly = set(self.building_full_governing_names) - active
            lines.append(
                f"Round {round_index}: sized on {len(active)} of {total} combinations; full check "
                f"{group} {check} {ratio:.3f} {'OK' if verified else 'NOT OK'}; {len(newly)} newly governing")
            if verified or not newly:
                break
            active |= newly
        report = [self.auto_size_report_text or "", "", "BUILDING LOAD ACCEPTANCE GATE", *lines,
                  f"FULL-SET VERIFICATION: {'OK' if verified else 'NOT OK'} | {group} {check} {ratio:.3f}",
                  "", *governing_report(self.design_envelope_actions)]
        self.auto_size_report_text = "\n".join(report).strip()
        if show_completion:
            self.notify(
                "information" if verified else "warning",
                "Optimise" if verified else "Optimisation did not find a passing design",
                self.auto_size_report_text)
        result = dict(result)
        result.update(verified=verified, ratio=ratio, governing_group=group, governing_check=check,
                      cost=(self.costing_summary or {}).get("total_cost"), report=self.auto_size_report_text,
                      building_screening_rounds=len(lines))
        return result

    def run_structure_optimisation(self, *, show_completion=True):
        """The Optimise button: auto-size with a progress dialog and one undo step for the result."""
        previous_history_state = self._history_suspended
        self._history_suspended = True
        self.optimisation_progress_begin()
        # Combination loads are applied once per geometry/spring/case signature.
        self.optimisation_load_vector_cache = {}
        try:
            return self.auto_size_current_preset(show_completion=show_completion)
        finally:
            self.optimisation_load_vector_cache = None
            self.optimisation_progress_finish()
            self._history_suspended = previous_history_state
            self.record_model_snapshot()
