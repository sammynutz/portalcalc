"""Freeze one main frame and verify all solar/cantilever installation states.

Used after the canopy geometry search. Repairs use the native physics optimiser
at the seed's physical geometry, then envelope sections and reinforcement.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from future_canopy_design import capture, geometry_basis, merge, refresh_report, secondary_checks
from hay_shed_backend import FutureCanopyBackend

SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
STATES = (("bare", False, False), ("canopy", False, True),
          ("solar", True, False), ("solar_canopy", True, True))


def main_schedule(window):
    groups = {"TOP", "BOTTOM", "WEB", "POST", "APEX"}
    columns = {}
    for group in ("LEFT_COLUMN", "RIGHT_COLUMN", "INTERNAL_COLUMN"):
        elements = [e for e in window.structure.elements if e.group == group]
        if elements:
            points = [n for e in elements for n in (e.start, e.end)]
            columns[group] = [round(min(n.x for n in points), 6), round(max(n.x for n in points), 6),
                              round(min(n.y for n in points), 6), round(max(n.y for n in points), 6)]
    return {"column_extents": columns,
            "members": sorted([e.group, e.id, round(e.start.x, 6), round(e.start.y, 6),
                               round(e.end.x, 6), round(e.end.y, 6)]
                              for e in window.structure.elements if e.group in groups)}


def zone_keys(proposal):
    return {(z["group"], int(z["member"])) for z in proposal["zones"]}


def run(window, max_repairs=3):
    from PySide6.QtCore import QSignalBlocker
    from portalcalc_service import json_safe
    original = window.collect_preset_data()
    configured = {side: getattr(window, f"{side}_canopy_type_combo").currentText()
                  for side in ("left", "right")}
    allowance = float(window.solar_load_input.text())
    if allowance <= 0 or not any(v == "Canopy" for v in configured.values()):
        raise ValueError("Requires a positive future solar allowance and configured cantilever canopy")
    fixed_depth = float(window.depth_input.text())
    panels = int(window.panel_count_label.text())
    basis = geometry_basis(window)
    reference_schedule = None
    hybrid = capture(window)
    previous_bounds = window.addition_depth_bounds
    depths = {side: float(getattr(window, f"{side}_canopy_shallow_depth_input").text())
              for side in configured}
    old_fixed = getattr(window, "_future_canopy_fixed_main_depth", None)
    window._future_canopy_fixed_main_depth = fixed_depth
    window.addition_depth_bounds = lambda side: ((depths[side], depths[side])
                                               if configured[side] == "Canopy" else previous_bounds(side))
    solves, reports = 0, []

    def set_phase(has_solar, has_canopy):
        field = window.solar_load_input
        blocker = QSignalBlocker(field)
        field.setText(f"{allowance if has_solar else 0:g}")
        del blocker
        for side, kind in configured.items():
            combo = getattr(window, f"{side}_canopy_type_combo")
            blocker = QSignalBlocker(combo)
            combo.setCurrentText(kind if has_canopy else "None")
            del blocker

    def frozen_check(proposal, has_solar, has_canopy):
        nonlocal solves, reference_schedule
        set_phase(has_solar, has_canopy)
        window._preserve_web_assignments_on_solve = True
        window._suppress_panel_topology_search = True
        window.auto_size_restore_state(proposal["state"])
        window._panel_count_override = panels
        window.future_canopy_design = {"geometry_basis": basis, "main_truss_panel_count": panels,
            "doubled_chord_members": [list(k) for k in sorted(zone_keys(proposal))]}
        window.generate_and_solve()
        solves += 1
        if window.structure is None or geometry_basis(window) != basis:
            raise ValueError("Solar-state check changed the common physical main geometry")
        actual_schedule = main_schedule(window)
        # Column section sizes affect centroid offsets within the fixed outside
        # footprint. A repair may change these; establish the common physical
        # schedule after enveloping sections, then hold it across all four states.
        if reference_schedule is None:
            reference_schedule = actual_schedule
        elif actual_schedule != reference_schedule:
            raise ValueError("Installation states do not retain the same physical main schedule")
        if int(window.panel_count_label.text()) != panels:
            raise ValueError("Solar-state check changed truss panel count")
        if window.auto_web_profile_assignments != proposal["state"]["web_assignments"]:
            raise ValueError("Solar-state check changed frozen web assignments")
        specs = window.active_auto_size_group_specs()
        ratio, group, check = window.auto_size_overall_ratio(specs)
        secondary = secondary_checks(window)
        return {"verified": window.auto_size_design_acceptable(specs, ratio)
                            and all(v["status"] == "OK" for v in secondary.values()),
                "ratio": ratio, "group": group, "check": check,
                "secondary_checks": secondary, "costing": copy.deepcopy(window.costing_summary)}

    try:
        for attempt in range(max_repairs + 2):
            reference_schedule = None
            checks, added_zones = {}, False
            for name, has_solar, has_canopy in STATES:
                checks[name] = frozen_check(hybrid, has_solar, has_canopy)
                proposal = capture(window)
                if not zone_keys(proposal) <= zone_keys(hybrid):
                    added_zones = True
                    hybrid = merge(window, hybrid, proposal)
            reports.append(f"Four-state check {attempt + 1}: " + "; ".join(
                f"{name} {item['ratio']:.3f} {'PASS' if item['verified'] else 'FAIL'}"
                for name, item in checks.items()))
            if all(item["verified"] for item in checks.values()) and not added_zones:
                break
            if attempt >= max_repairs:
                raise ValueError("Common solar/canopy design failed the bounded four-state repair")
            for name, has_solar, has_canopy in STATES:
                if checks[name]["verified"]:
                    continue
                set_phase(has_solar, has_canopy)
                window.auto_size_restore_state(hybrid["state"])
                window.future_canopy_design = None
                window._panel_count_override = panels
                window._suppress_panel_topology_search = True
                repair = window.run_physics_structure_optimisation(show_completion=False)
                solves += repair.get("exact_solves", 0)
                reports.append(name.upper() + " REPAIR\n" + repair.get("report", repair.get("error", "")))
                if not repair.get("verified"):
                    raise ValueError(f"Physics repair failed for {name}")
                if geometry_basis(window) != basis or int(window.panel_count_label.text()) != panels:
                    raise ValueError("Physics repair changed depth or topology; section schedules cannot be merged")
                hybrid = merge(window, hybrid, capture(window))
        else:
            raise ValueError("Reinforcement union did not converge")
        # Restore the published state, including all reinforcement accumulated
        # from every check; its cost is still the main shed purchased now.
        final = frozen_check(hybrid, True, True)
        if not final["verified"]:
            raise ValueError("Final frozen four-state design failed")
        paired = {key: max(item["costing"]["costs"].get(key, 0) for item in checks.values())
                  for key in final["costing"]["costs"]}
        window.future_canopy_design.update(verified=True, phases=json_safe(checks),
            sections=hybrid["sections"], paired_costs=paired, paired_cost=sum(paired.values()),
            future_solar_allowance_kpa=allowance, solar_design_states=[s[0] for s in STATES],
            future_solar_design_sha256=SOURCE_SHA256)
        worst = max(checks.values(), key=lambda item: item["ratio"])
        reports.append(f"One physical main schedule passes all four installation states. Main steel ${sum(paired.values()):,.0f}.")
        result = {"verified": True, "algorithm": "physics hybrid four-state",
                  "ratio": worst["ratio"], "cost": sum(paired.values()), "exact_solves": solves,
                  "phases": json_safe(checks), "report": "\n\n".join(reports)}
        refresh_report(window, result["report"])
        return result
    except Exception as error:
        window._future_canopy_fixed_main_depth = None
        window.apply_preset_data(original)
        window.generate_and_solve()
        return {"verified": False, "algorithm": "physics hybrid four-state", "error": str(error),
                "exact_solves": solves, "report": "\n\n".join([*reports, str(error)])}
    finally:
        window._future_canopy_fixed_main_depth = old_fixed
        window.addition_depth_bounds = previous_bounds
        window._preserve_web_assignments_on_solve = True
        window._suppress_panel_topology_search = True


class FutureSolarBackend(FutureCanopyBackend):
    @staticmethod
    def optimise_window(window):
        return run(window)

    def calculate(self, request):
        dossier = dict(super().calculate(request))
        dossier["engine_provenance"]["future_solar_design_sha256"] = SOURCE_SHA256
        return dossier
