"""One physical primary-steel design checked before and after canopy installation.

Doublers follow PortalCalc's existing design-only modelling policy. Retained
zones add fabrication/cost allowances; they never mask a failed state check.
"""
from __future__ import annotations

import copy
import math


def geometry_basis(window):
    basis = [window.span_input.text(), window.eave_input.text(), window.pitch_input.text(),
            window.depth_input.text(), window.roof_shape_combo.currentText(),
            window.frame_system_combo.currentText(), window.internal_column_count_combo.currentText(),
            window.max_truss_depth_input.text()]
    for name in ("internal_column_offset_input", "internal_column_positions_input", "internal_column_connection_combo"):
        field = getattr(window, name, None)
        basis.append((field.currentText() if hasattr(field, "currentText") else field.text()) if field is not None else "")
    for side in ("left", "right"):
        for suffix in ("canopy_length_input", "canopy_pitch_input", "canopy_shallow_depth_input"):
            field = getattr(window, f"{side}_{suffix}", None)
            basis.append(field.text() if field is not None else "")
    return basis


def choose_profile(window, combo, group, names):
    """Catalogue envelope as a starting point; detailed checks remain mandatory."""
    profiles = [window.catalog.get(name) for name in set(names)]
    profiles = [profile for profile in profiles if profile is not None]
    if not profiles:
        raise ValueError(f"No catalogue sections to combine for {group}")
    if len(profiles) == 1:
        return profiles[0].name
    def measures(profile):
        return (profile.A, profile.I_analysis, min(profile.Iyp, profile.Izp),
                profile.A * profile.fy, max(profile.Syp, profile.Szp) * profile.fy)
    required = tuple(max(values) for values in zip(*(measures(p) for p in profiles)))
    candidates = [window.catalog.get(combo.itemText(index)) for index in range(combo.count())]
    candidates = [p for p in candidates if p is not None
                  and all(value >= minimum - 1e-6 for value, minimum in zip(measures(p), required))]
    if not candidates:
        raise ValueError(f"No common catalogue section envelopes {', '.join(names)} for {group}")
    return min(candidates, key=lambda p: (window.auto_size_profile_cost_key(p), p.name)).name


def capture(window):
    active = window.active_auto_size_group_specs()
    return {"state": window.auto_size_capture_state(active),
            "sections": {group: combo.currentText() for _, combo, group in active},
            "zones": copy.deepcopy((window.design_envelope_actions or {}).get("doubled_chord_zones", []))}


def merge(window, first, second):
    from PySide6.QtCore import QSignalBlocker
    specs = {group: combo for _, combo, group in window.auto_size_group_specs()}
    sections = dict(first["sections"])
    for group, name in second["sections"].items():
        sections[group] = choose_profile(window, specs[group], group, [sections.get(group, name), name])
    # Main/canopy top and bottom selectors can be linked. Envelope the pair once.
    for _, combo, _ in window.auto_size_group_specs():
        linked = [group for group, item in specs.items() if item is combo and group in sections]
        if linked:
            name = choose_profile(window, combo, linked[0], [sections[group] for group in linked])
            for group in linked:
                sections[group] = name
            blocker = QSignalBlocker(combo) if hasattr(combo, "blockSignals") else None
            window.set_combo_text_if_present(combo, name)
            del blocker
    assignments = copy.deepcopy(first["state"].get("web_assignments", {}))
    for group, members in second["state"].get("web_assignments", {}).items():
        target = assignments.setdefault(group, {})
        for member, name in members.items():
            target[member] = choose_profile(window, specs[group], group, [target.get(member, name), name])
    sides = copy.deepcopy(first["state"].get("web_joint_doubler_sides", {}))
    for group, required in second["state"].get("web_joint_doubler_sides", {}).items():
        sides[group] = set(sides.get(group, set())) | set(required)
    window.auto_web_profile_assignments = assignments
    window.auto_web_joint_doubler_sides = sides
    window.auto_web_assignment_basis = {group: window.web_assignment_basis_signature(group) for group in assignments}
    zones = {(zone["group"], int(zone["member"])): copy.deepcopy(zone)
             for zone in [*first["zones"], *second["zones"]]}
    return {"state": window.auto_size_capture_state(window.auto_size_group_specs()),
            "sections": sections, "zones": list(zones.values())}


def apply_retained_zones(window):
    retained = getattr(window, "future_canopy_design", None) or {}
    if not retained or retained.get("geometry_basis") != geometry_basis(window):
        return
    zones = (window.design_envelope_actions or {}).get("doubled_chord_zones", [])
    selected = {(z["group"], int(z["member"])): z for z in zones}
    elements = {(element.group, element.id): element for element in window.structure.elements}
    for item in retained.get("doubled_chord_members", []):
        key = (item[0], int(item[1]))
        if key in selected:
            continue
        element = elements.get(key)
        if element is None:
            if key[0] in {"TOP", "BOTTOM"}:
                raise ValueError("Retained main chord panel is absent; rerun Future Canopy Optimise")
            continue  # Canopy members are absent in the initial state.
        selected[key] = {"group": element.group, "member": element.id,
                         "x1": element.start.x, "y1": element.start.y,
                         "x2": element.end.x, "y2": element.end.y,
                         "length_mm": element.length(), "combo": "Initial/future fabrication envelope",
                         "single_ratio": 0.0, "doubled_ratio": 0.0,
                         "single_check": "Retained reinforcement", "doubled_check": "Retained reinforcement",
                         "future_canopy_retained": True}
    window.design_envelope_actions["doubled_chord_zones"] = sorted(selected.values(), key=lambda z: (z["group"], z["member"]))


def normalise_addition_depths(window):
    from PySide6.QtCore import QSignalBlocker
    for side in ("left", "right"):
        bounds = window.addition_depth_bounds(side)
        if getattr(window, f"{side}_canopy_type_combo").currentText() == "None" or bounds is None:
            continue
        minimum, maximum = bounds
        first, last = math.ceil(minimum / 50) * 50, math.floor(maximum / 50) * 50
        field = getattr(window, f"{side}_canopy_shallow_depth_input")
        blocker = QSignalBlocker(field)
        field.setText(f"{min(max(math.ceil(float(field.text()) / 50) * 50, first), last):.0f}")
        del blocker


def secondary_checks(window):
    """Record the real automatic roof/wall capacity-table selections."""
    checks = {}
    def add(label, data, key="match"):
        match = (data or {}).get(key) or {}
        span_type = (data or {}).get("span_type", "")
        checks[label] = {"status": match.get("status", "NOT CHECKED"), "span_type": span_type,
                         "section": window.metroll_section_display(match.get("section", ""), span_type),
                         "tables": match.get("tables", ""), "message": match.get("message", "")}
    add("roof_general", window.purlin_check)
    add("roof_edge", window.purlin_check, "local_match")
    walls = window.envelope_plan_selector.wall_states()
    if walls.get("left") or walls.get("right"):
        add("long_wall_girts", window.wall_girt_check)
    if walls.get("front") or walls.get("back"):
        add("end_wall_girts", window.end_wall_girt_check)
        design = window.end_wall_mullion_design or {}
        checks["end_wall_mullions"] = {"status": design.get("status", "NOT CHECKED")}
    return checks


def refresh_report(window, report):
    window.auto_size_report_text = report
    window.preserve_auto_size_report = True
    try:
        window.update_section_checks_text(*[window.selected_profile(getattr(window, name)) for name in (
            "left_column_profile_combo", "right_column_profile_combo", "internal_column_profile_combo",
            "top_profile_combo", "bottom_profile_combo", "web_profile_combo",
            "left_canopy_top_profile_combo", "left_canopy_bottom_profile_combo", "left_canopy_web_profile_combo",
            "left_lean_column_profile_combo", "right_canopy_top_profile_combo", "right_canopy_bottom_profile_combo",
            "right_canopy_web_profile_combo", "right_lean_column_profile_combo")])
    finally:
        window.preserve_auto_size_report = False


def run(window, show_completion=True, max_repairs=3):
    from PySide6.QtCore import QSignalBlocker
    from PySide6.QtWidgets import QMessageBox
    from portalcalc_service import json_safe
    configured = {side: getattr(window, f"{side}_canopy_type_combo").currentText() for side in ("left", "right")}
    if not any(kind == "Canopy" for kind in configured.values()) or any(kind not in {"None", "Canopy"} for kind in configured.values()):
        error = "Configure at least one cantilever canopy; this mode supports None/Canopy additions."
        if show_completion:
            QMessageBox.warning(window, "Future Canopy Optimise", error)
        return {"verified": False, "error": error, "algorithm": "physics hybrid"}
    original = window.collect_preset_data()
    if window.frame_system_combo.currentText() != "Truss":
        error = "Future Canopy Optimise currently requires a main truss frame."
        if show_completion:
            QMessageBox.warning(window, "Future Canopy Optimise", error)
        return {"verified": False, "error": error, "algorithm": "physics hybrid"}
    old_flags = (window._suppress_panel_topology_search, window._preserve_web_assignments_on_solve)
    prior = copy.deepcopy(getattr(window, "future_canopy_design", None))
    reports, solves = [], 0
    button = getattr(window, "future_canopy_optimise_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("Checking both stages...")
    def set_state(future):
        for side, kind in configured.items():
            combo = getattr(window, f"{side}_canopy_type_combo")
            blocker = QSignalBlocker(combo)
            combo.setCurrentText(kind if future else "None")
            del blocker
    def solve(hybrid, future):
        nonlocal solves
        set_state(future)
        # Physics Optimise temporarily changes this flag during final refinement.
        # Every hybrid check must restore the exact physical web schedule.
        window._preserve_web_assignments_on_solve = True
        window._suppress_panel_topology_search = True
        window.auto_size_restore_state(hybrid["state"])
        window._panel_count_override = common_panels
        window.future_canopy_design = {"geometry_basis": geometry_basis(window),
                                      "main_truss_panel_count": common_panels,
                                      "doubled_chord_members": [[z["group"], z["member"]] for z in hybrid["zones"]]}
        window.generate_and_solve()
        solves += 1
        if window.structure is None or int(window.panel_count_label.text()) != common_panels:
            raise ValueError("Hybrid check did not retain the common truss topology")
        if window.auto_web_profile_assignments != hybrid["state"]["web_assignments"]:
            raise ValueError("Hybrid check changed the frozen web schedule")
        specs = window.active_auto_size_group_specs()
        ratio, group, check = window.auto_size_overall_ratio(specs)
        secondary = secondary_checks(window)
        failed = {name: item for name, item in secondary.items() if item["status"] != "OK"}
        if failed:
            raise ValueError("No passing automatic secondary section: " + "; ".join(f"{name}: {item['status']} {item.get('message', '')}" for name, item in failed.items()))
        return {"verified": window.auto_size_design_acceptable(specs, ratio),
                "ratio": ratio, "group": group, "check": check,
                "secondary_checks": secondary,
                "costing": copy.deepcopy(window.costing_summary)}
    try:
        window.future_canopy_design = None
        window._preserve_web_assignments_on_solve = False
        if not window.max_truss_depth_input.text().strip():
            window.max_truss_depth_input.setText(f"{float(window.span_input.text()) * 1000 / 20:g}")
        normalise_addition_depths(window)
        future_result = window.run_physics_structure_optimisation(show_completion=False)
        reports.append("WITH CANOPY\n" + future_result.get("report", future_result.get("error", "")))
        solves += future_result.get("exact_solves", 0)
        if not future_result.get("verified"):
            raise ValueError("Future-state physics optimisation failed: " + future_result.get("error", "see report"))
        future = capture(window)
        common_depth = float(window.depth_input.text())
        common_panels = int(window.panel_count_label.text())
        window._future_canopy_fixed_main_depth = common_depth
        window._suppress_panel_topology_search = True
        window._panel_count_override = common_panels
        set_state(False)
        # Independent section search at the common physical geometry.
        for combo, index in future["state"]["combos"]:
            if combo in {window.left_column_profile_combo, window.right_column_profile_combo,
                         window.top_profile_combo, window.web_profile_combo, window.internal_column_profile_combo}:
                # Start from the original catalogue sections, not the future winner.
                key = {window.left_column_profile_combo: "left_column", window.right_column_profile_combo: "right_column",
                       window.top_profile_combo: "top_chord", window.web_profile_combo: "webs_posts",
                       window.internal_column_profile_combo: "internal_column"}[combo]
                name = original["sections"].get(key)
                if name is not None:
                    window.set_combo_text_if_present(combo, name)
        initial_result = window.run_physics_structure_optimisation(show_completion=False)
        reports.append("WITHOUT CANOPY\n" + initial_result.get("report", initial_result.get("error", "")))
        solves += initial_result.get("exact_solves", 0)
        if not initial_result.get("verified"):
            raise ValueError("Initial-state physics optimisation failed: " + initial_result.get("error", "see report"))
        initial = capture(window)
        window.auto_size_restore_state(future["state"])
        hybrid = merge(window, future, initial)
        window._preserve_web_assignments_on_solve = True
        phase_checks = {}
        for attempt in range(max_repairs + 1):
            phase_checks = {"initial": solve(hybrid, False), "future": solve(hybrid, True)}
            reports.append(f"HYBRID CHECK {attempt + 1}: initial {phase_checks['initial']['ratio']:.3f}; future {phase_checks['future']['ratio']:.3f}")
            if all(item["verified"] for item in phase_checks.values()):
                break
            if attempt == max_repairs:
                raise ValueError("Combined design did not pass both detailed state checks within the repair limit")
            for phase, check in phase_checks.items():
                if check["verified"]:
                    continue
                set_state(phase == "future")
                window.auto_size_restore_state(hybrid["state"])
                window.future_canopy_design = None
                repair = window.run_physics_structure_optimisation(show_completion=False)
                solves += repair.get("exact_solves", 0)
                reports.append(f"{phase.upper()} HYBRID REPAIR\n" + repair.get("report", repair.get("error", "")))
                if not repair.get("verified"):
                    raise ValueError(f"Physics repair failed for {phase} hybrid state")
                proposal = capture(window)
                hybrid = merge(window, hybrid, proposal)
        costs = phase_checks["future"]["costing"]["costs"]
        paired_costs = {key: max(value, phase_checks["initial"]["costing"]["costs"].get(key, 0)) for key, value in costs.items()}
        worst = max(phase_checks.values(), key=lambda item: item["ratio"])
        window.future_canopy_design.update(verified=True, phases=json_safe(phase_checks),
                                          independent_designs={"initial": initial["sections"], "future": future["sections"]},
                                          sections=hybrid["sections"], paired_costs=paired_costs,
                                          paired_cost=sum(paired_costs.values()))
        reports.extend(["One common depth/panel layout; catalogue section and reinforcement envelope; both states verified.",
                        "PRIMARY SECTION ENVELOPE"])
        for group in ("LEFT_COLUMN", "RIGHT_COLUMN", "TOP", "BOTTOM", "WEB"):
            reports.append(f"{group}: initial {initial['sections'].get(group, '-')}; "
                           f"future {future['sections'].get(group, '-')}; combined {hybrid['sections'].get(group, '-')}")
        reports.append(f"Retained doubled-chord panels: {len(hybrid['zones'])}; "
                       f"main depth {common_depth:g} mm; {common_panels} panels.")
        scope = phase_checks["future"]["costing"].get("cost_scope", "Main shed plus configured future canopy frame")
        reports.append(f"Paired steel allowance: ${sum(paired_costs.values()):,.0f}. {scope}.")
        result = {"verified": True, "algorithm": "physics hybrid", "ratio": worst["ratio"],
                  "governing_group": worst["group"], "governing_check": worst["check"],
                  "cost": sum(paired_costs.values()), "exact_solves": solves,
                  "phases": json_safe(phase_checks), "report": "\n\n".join(reports)}
        refresh_report(window, result["report"])
        if show_completion:
            QMessageBox.information(window, "Future Canopy Optimise", result["report"])
        return result
    except Exception as error:
        window._future_canopy_fixed_main_depth = None
        window.future_canopy_design = prior
        window.apply_preset_data(original)
        set_state(True)
        window.generate_and_solve()
        report = "\n\n".join([*reports, "HYBRID NOT VERIFIED: " + str(error)])
        refresh_report(window, report)
        if show_completion:
            QMessageBox.warning(window, "Future Canopy Optimise", report)
        return {"verified": False, "error": str(error), "exact_solves": solves,
                "algorithm": "physics hybrid", "report": report}
    finally:
        window._future_canopy_fixed_main_depth = None
        if getattr(window, "future_canopy_design", None):
            window._suppress_panel_topology_search = True
            window._preserve_web_assignments_on_solve = True
        else:
            window._suppress_panel_topology_search, window._preserve_web_assignments_on_solve = old_flags
        if button is not None:
            button.setText("Future Canopy Optimise")
            button.setEnabled(True)
