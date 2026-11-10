"""Prototype: per-frame eave restraint extracted from the coupled 3D model.

Study script, not production. Builds factory defaults offscreen, solves the
fixed-layout 3D bracing model for several practical layouts and, for every
frame line and case, reports:

* the secant roof-level restraint k = -sum(Rx)/u_eave the bracing network
  actually provides to that portal (Rx = interface X forces on the portal);
* the frame's unrestrained 2D sway under the same portal loads;
* sway and peak end moments from a 2D portal with eave springs, both the
  3D-derived secant spring and the production hand formula
  (``MainWindow.end_wall_bracing_eave_spring``), against the 3D recovery.

Run:  $env:QT_QPA_PLATFORM='offscreen'; python restraint_feedback_prototype.py
Writes output/restraint_feedback_prototype.md and .json.
"""
import copy
import json
import os
import sys
import time

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")


def eave_nodes(structure):
    top = [e for e in structure.elements if e.group == "TOP"]
    nodes = [n for e in top for n in (e.start, e.end)]
    return min(nodes, key=lambda n: n.x), max(nodes, key=lambda n: n.x)


def solve_2d(portal, loads, springs):
    """Linear portal solve with extra X springs {dof: N/mm}."""
    k = portal.k.copy()
    for dof, value in springs.items():
        k[dof, dof] += value
    free = portal.free
    d = np.zeros(len(k))
    d[free] = np.linalg.solve(k[np.ix_(free, free)], np.asarray(loads)[free])
    return d


def peak_moments(structure, d, member_equivalent_loads):
    """Peak end moments (kNm) for columns and rafters."""
    out = {"column": 0., "rafter": 0.}
    for e in structure.elements:
        meq = np.asarray(member_equivalent_loads.get(str(e.id), member_equivalent_loads.get(e.id, np.zeros(6))))
        f = e.local_end_forces(d) - meq
        m = max(abs(f[2]), abs(f[5])) / 1e6
        key = "rafter" if e.group == "TOP" else "column" if "COL" in e.group.upper() else None
        if key:
            out[key] = max(out[key], m)
    return out


def recovered_peak_moments(structure, recovered):
    groups = {e.id: e.group for e in structure.elements}
    out = {"column": 0., "rafter": 0.}
    for item in recovered["member_end_forces"]:
        f = item["local_n_n_nmm"]
        m = max(abs(f[2]), abs(f[5])) / 1e6
        group = groups[item["element"]]
        key = "rafter" if group == "TOP" else "column" if "COL" in group.upper() else None
        if key:
            out[key] = max(out[key], m)
    return out


def frame_class(index, count, braced_lines):
    if index in (0, count-1):
        return "end"
    if index in braced_lines:
        return "braced-bay"
    return "interior"


def main():
    from PySide6.QtWidgets import QApplication
    from main import MainWindow
    from bracing_analysis import AnalysisError, solve_building_case
    from bracing_analysis_desktop import prepare_context, prepare_analysis_models
    from bracing_layout import BracingLayout
    from bracing_layout_search import practical_seeds

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.confirm_on_close = False
    with open("presets/factory_defaults.json", encoding="utf-8") as handle:
        window.apply_preset_data(json.load(handle))
    window.generate_and_solve()
    context = prepare_context(window, allow_unconfigured=True)
    portal = context["portal"]
    structure = portal.structure
    left, right = eave_nodes(structure)
    g = context["layout"].geometry
    catalog = window.catalog
    struts = sorted([*catalog.shs_profiles(), *catalog.rhs_profiles()], key=lambda p: (p.A, p.name))
    posts = sorted(catalog.end_wall_mullion_profiles(), key=lambda p: (p.A, p.name))
    strut = min(struts, key=lambda p: abs(p.A-1500))
    post = catalog.profile_by_name.get(context.get("default_end_post")) or min(posts, key=lambda p: abs(p.A-2500))

    frames = len(g.bay_stations_mm)
    bay_m = (g.bay_stations_mm[1]-g.bay_stations_mm[0]) / 1000
    hand = window.end_wall_bracing_eave_spring(g.length_mm/1000, bay_m, context["end_brace"]["area_mm2"],
                                               end_count=2, brace_label=context["end_brace"].get("label", "Brace"))
    both_sides = window.end_wall_braced_sides_combo.currentText() == "Both sides"
    hand_springs = {left.ux: hand["effective_spring_kn_mm"]*1000}
    if both_sides:
        hand_springs[right.ux] = hand["effective_spring_kn_mm"]*1000
    hand_total = sum(hand_springs.values()) / 1000

    wanted = ("End roof/wall bays", "End roof/wall bays with end-wall twist restraint",
              "End bays with distributed end-wall restraint")
    seeds = {}
    for reason, selected in practical_seeds(context, set()):
        if reason in wanted or "continuous supported" in reason and "girder" in reason and not seeds.get("girder"):
            seeds["girder" if "girder" in reason else reason] = (reason, selected)

    report = {"building": {"span_m": g.span_mm/1000, "length_m": g.length_mm/1000, "eave_m": g.eave_mm/1000,
                           "pitch_deg": g.pitch_deg, "frames": frames, "bay_m": bay_m},
              "sections": {"strut": strut.name, "end_post": post.name,
                           **{k: context[k].get("label") for k in ("roof_brace", "wall_brace", "end_brace")}},
              "production_eave_restraint": {"setting": window.eave_x_restraint_combo.currentText(),
                                            "active": window.active_eave_x_restraint,
                                            "active_spring_kn_mm": window.active_eave_x_spring_kn_mm},
              "hand_spring": {**hand, "applied_eaves": "both" if both_sides else "left",
                              "frame_total_kn_mm": hand_total},
              "layouts": []}

    for key, (reason, selected) in seeds.items():
        layout = BracingLayout(g, sorted(context["layout"].blocked), sorted(selected))
        ctx = {**context, "layout": layout}
        start = time.perf_counter()
        try:
            model, _, roof_grid, portal_dofs, point_nodes, _ = prepare_analysis_models(ctx, strut, post)
        except AnalysisError as error:
            report["layouts"].append({"name": reason, "error": str(error)})
            continue
        braced_lines = set()
        for p in selected:
            if p.startswith("roof/"):
                bay = int(p.split("/")[2])
                braced_lines |= {bay, bay+1}
        rows, failures = [], []
        for case in ctx["cases"]:
            if case.get("portal_loads") is None:
                continue
            try:
                solved = solve_building_case(model, layout, portal, portal_dofs, point_nodes, case)
            except AnalysisError as error:
                failures.append(f"{case['name']}: {error}")
                continue
            for index, recovered in enumerate(solved["portals"]):
                state = case["portal_loads"][index]
                loads = np.asarray(state["loads_n"])
                meq = state.get("member_equivalent_loads") or {}
                rx = sum(f[0] for f in recovered["interface_forces_n"])
                disp = {n["node"]: n["ux_mm"] for n in recovered["node_displacements"]}
                u3d = .5*(disp[left.id]+disp[right.id])
                free = solve_2d(portal, loads, {})
                ufree = .5*(free[left.ux]+free[right.ux])
                if abs(ufree) < .5:
                    continue  # no meaningful sway in this case/frame
                k3d = -rx/u3d if abs(u3d) > 1e-6 else float("inf")
                secant = solve_2d(portal, loads, {left.ux: k3d/2, right.ux: k3d/2}) if np.isfinite(k3d) and k3d > 0 else None
                hand_d = solve_2d(portal, loads, hand_springs)
                rows.append({
                    "case": case["name"], "level": case["level"], "frame": index,
                    "class": frame_class(index, frames, braced_lines),
                    "rx_kn": rx/1000, "u_free_mm": ufree, "u_3d_mm": u3d,
                    "u_hand_mm": .5*(hand_d[left.ux]+hand_d[right.ux]),
                    "u_secant_mm": None if secant is None else .5*(secant[left.ux]+secant[right.ux]),
                    "k3d_kn_mm": k3d/1000,
                    "m_free": peak_moments(structure, free, meq),
                    "m_3d": recovered_peak_moments(structure, recovered),
                    "m_hand": peak_moments(structure, hand_d, meq),
                    "m_secant": None if secant is None else peak_moments(structure, secant, meq),
                })
        report["layouts"].append({"name": reason, "pinned_panels": len(selected), "braced_frame_lines": sorted(braced_lines),
                                  "elapsed_s": time.perf_counter()-start, "failures": failures, "rows": rows})
        print(f"{reason}: {len(rows)} frame-case rows, {len(failures)} failed cases, {time.perf_counter()-start:.1f}s", flush=True)

    os.makedirs("output", exist_ok=True)
    with open("output/restraint_feedback_prototype.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, default=float)
    with open("output/restraint_feedback_prototype.md", "w", encoding="utf-8") as handle:
        handle.write(markdown(report))
    window.close()
    print("Wrote output/restraint_feedback_prototype.md")


def governing(rows, key):
    """Row with the largest free sway for each frame class."""
    best = {}
    for row in rows:
        if row["level"] != "ULS":
            continue
        current = best.get(row[key])
        if current is None or abs(row["u_free_mm"]) > abs(current["u_free_mm"]):
            best[row[key]] = row
    return best


def markdown(report):
    b, h = report["building"], report["hand_spring"]
    lines = ["# Prototype: 3D-derived eave restraint vs hand spring", "",
             f"Factory defaults: {b['span_m']:.2f} m span x {b['length_m']:g} m long, {b['eave_m']:.2f} m eave, "
             f"{b['pitch_deg']:g} deg, {b['frames']} frames at {b['bay_m']:g} m.", "",
             f"Sections: {report['sections']}", "",
             f"Production setting: {report['production_eave_restraint']}", "",
             f"Hand spring (both ends, {h['applied_eaves']} eave(s)): {h['effective_spring_kn_mm']:.3f} kN/mm per eave, "
             f"frame total {h['frame_total_kn_mm']:.3f} kN/mm (calibration factor {h['calibration_factor']:g}, "
             f"{h['squares_to_end']} squares to end).", "",
             "Columns: free = unrestrained 2D; 3D = coupled model; hand = 2D with production spring; "
             "secant = 2D with the 3D-derived spring. Sway is mean eave X (mm). Moments are peak end moments (kNm). "
             "Each frame class shows its ULS case with the largest unrestrained sway.", ""]
    for layout in report["layouts"]:
        lines.append(f"## {layout['name']}")
        if "error" in layout:
            lines += ["", f"Not analysed: {layout['error']}", ""]
            continue
        lines += ["", f"{layout['pinned_panels']} pinned panels; roof-braced frame lines {layout['braced_frame_lines']}; "
                      f"{layout['elapsed_s']:.1f} s; {len(layout['failures'])} failed cases.", ""]
        lines += ["| Frame class | Case | k3D kN/mm | Sway free / 3D / hand / secant | Col M free / 3D / hand / secant | Rafter M free / 3D / hand |",
                  "|---|---|---:|---|---|---|"]
        for cls, r in sorted(governing(layout["rows"], "class").items()):
            sec_u = "-" if r["u_secant_mm"] is None else f"{r['u_secant_mm']:.1f}"
            sec_m = "-" if r["m_secant"] is None else f"{r['m_secant']['column']:.1f}"
            lines.append(f"| {cls} | {r['case']} | {r['k3d_kn_mm']:.3f} | "
                         f"{r['u_free_mm']:.1f} / {r['u_3d_mm']:.1f} / {r['u_hand_mm']:.1f} / {sec_u} | "
                         f"{r['m_free']['column']:.1f} / {r['m_3d']['column']:.1f} / {r['m_hand']['column']:.1f} / {sec_m} | "
                         f"{r['m_free']['rafter']:.1f} / {r['m_3d']['rafter']:.1f} / {r['m_hand']['rafter']:.1f} |")
        uls = [r for r in layout["rows"] if r["level"] == "ULS"]
        if uls:
            lines += ["", "Per-frame secant k (kN/mm), ULS side-wind cases with the largest free sway:", ""]
            side = [r for r in uls if "Left" in r["case"] or "Right" in r["case"]] or uls
            case = max(side, key=lambda r: abs(r["u_free_mm"]))["case"]
            per = sorted((r for r in uls if r["case"] == case), key=lambda r: r["frame"])
            lines.append(f"Case `{case}`:")
            lines.append("")
            lines.append("| Frame | Class | k3D | k3D range, all side-wind ULS | Sway free / 3D | Reduction | Col M free / 3D / secant |")
            lines.append("|---:|---|---:|---|---|---:|---|")
            for r in per:
                red = 1-r["u_3d_mm"]/r["u_free_mm"]
                ks = [x["k3d_kn_mm"] for x in side if x["frame"] == r["frame"]
                      and abs(x["u_free_mm"]) > .25*abs(r["u_free_mm"]) and np.isfinite(x["k3d_kn_mm"])]
                sec_m = "-" if r["m_secant"] is None else f"{r['m_secant']['column']:.1f}"
                lines.append(f"| {r['frame']+1} | {r['class']} | {r['k3d_kn_mm']:.3f} | {min(ks):.3f} to {max(ks):.3f} ({len(ks)} cases) | "
                             f"{r['u_free_mm']:.1f} / {r['u_3d_mm']:.1f} | {red:.0%} | "
                             f"{r['m_free']['column']:.1f} / {r['m_3d']['column']:.1f} / {sec_m} |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
