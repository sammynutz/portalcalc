"""Coarse geometry search and local refinement at 63°, checking every A subregion.

Run two solar cases, with one physical main shed sized for both canopy states.
The orientation stays fixed; there is no regional/direction optimisation sweep.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import subprocess
import sys
from pathlib import Path

from hay_shed_search import ROOT, atomic_json, initialize_worker, secondary_verified
from hay_shed_staged import read_rows

CASES = (("no_solar", 0), ("solar", .15))


def stage(output, workers, solar, *options):
    subprocess.run([sys.executable, str(ROOT / "hay_shed_search.py"),
                    "--output", str(output), "--workers", str(workers),
                    "--regions", "A-ALL", "--facings", "63",
                    "--future-canopy-m", "6", "--solar-kpa", str(solar), *options], check=True)
    return read_rows(output)


def best(rows):
    feasible = [r for r in rows if r.get("verified")]
    if not feasible:
        raise ValueError("No passing layout; inspect the saved calculation reports")
    return min(feasible, key=lambda r: (r["cost_per_m2"], r["cost"], r["id"]))


def selected_inputs(row):
    source = Path(row["source_directory"]) / "calculations" / f"{row['id']}.json"
    return json.loads(source.read_text(encoding="utf-8"))["dossier"]["inputs"]


def progress(output, datasets, stages, complete=False, verification=None):
    winners = {case: best(rows) for case, rows in datasets.items() if any(r.get("verified") for r in rows)}
    atomic_json(output / "comparison.json", {
        "complete": complete, "method": "fixed 63° opening; all-A envelope; physics hybrid",
        "stages": stages, "rows": datasets, "winners": winners,
        "universal_verification": verification or {}})
    return winners


def verify_in_actual_regions(inputs, directory):
    """Check the same primary sections, topology, webs and retained chord zones.

    Only site region and canopy presence change. No sizing is requested.
    """
    from hay_shed_backend import FutureCanopyBackend
    from portalcalc_service import PortalCalcCalculationService
    from wind import A_SUBREGIONS
    directory.mkdir(parents=True, exist_ok=True)
    initialize_worker(str(directory / "runtime"))
    class ScheduleProofBackend(FutureCanopyBackend):
        @staticmethod
        def create_window(window_class):
            window = FutureCanopyBackend.create_window(window_class)
            original = window.assistant_design_dossier
            def capture_schedule():
                dossier = original()
                main_groups = {"TOP", "BOTTOM", "WEB", "POST", "APEX"}
                columns = {}
                for group in ("LEFT_COLUMN", "RIGHT_COLUMN", "INTERNAL_COLUMN"):
                    elements = [e for e in window.structure.elements if e.group == group]
                    if elements:
                        points = [node for e in elements for node in (e.start, e.end)]
                        columns[group] = [round(min(n.x for n in points), 6), round(max(n.x for n in points), 6),
                                          round(min(n.y for n in points), 6), round(max(n.y for n in points), 6)]
                dossier["physical_main_schedule"] = {
                    # Canopy attachment nodes split the analysis column into
                    # more elements. Compare the same continuous physical column.
                    "column_extents": columns,
                    "members": sorted([e.group, e.id, round(e.start.x, 6), round(e.start.y, 6),
                                       round(e.end.x, 6), round(e.end.y, 6)]
                                      for e in window.structure.elements if e.group in main_groups),
                    "doubled_chords": sorted([z["group"], int(z["member"]), round(z["length_mm"], 6),
                                              round(z["x1"], 6), round(z["y1"], 6),
                                              round(z["x2"], 6), round(z["y2"], 6)]
                                             for z in window.design_envelope_actions.get("doubled_chord_zones", [])
                                             if z["group"] in {"TOP", "BOTTOM"}),
                }
                return dossier
            window.assistant_design_dossier = capture_schedule
            return window
    service = PortalCalcCalculationService(ScheduleProofBackend())
    reference = service.run({"request_id": "fixed-63-physical-reference", "mode": "checkSpecified", "inputs": inputs})
    reference_schedule = reference["dossier"]["physical_main_schedule"]
    atomic_json(directory / "A-ALL_reference.json", reference)
    rows = []
    for region in A_SUBREGIONS:
        for state in ("future", "initial"):
            data = copy.deepcopy(inputs)
            data["wind"]["region"] = region
            if state == "initial":
                data["canopies"]["right_type"] = "None"
                selections = data["calculation_service"]
                for key in ("auto_web_profile_assignments", "auto_web_joint_doubler_sides"):
                    selections[key] = {group: value for group, value in selections.get(key, {}).items() if group == "WEB"}
            result = service.run({"request_id": f"fixed-63-{region}-{state}",
                                  "mode": "checkSpecified", "inputs": data})
            dossier = result["dossier"]
            actual = dossier["inputs"]
            unchanged = (actual["sections"] == data["sections"]
                         and actual["wind"]["orientation"] == data["wind"]["orientation"]
                         and actual["wind"]["region"] == region
                         and actual["calculation_service"]["main_truss_panel_count"] == data["calculation_service"]["main_truss_panel_count"]
                         and actual["calculation_service"]["auto_web_profile_assignments"] == data["calculation_service"]["auto_web_profile_assignments"]
                         and dossier["physical_main_schedule"] == reference_schedule
                         and actual["future_canopy_design"]["doubled_chord_members"] == data["future_canopy_design"]["doubled_chord_members"])
            verified = result["verified"] and secondary_verified(dossier) and unchanged
            row = {"region": region, "state": state, "verified": verified,
                   "physical_schedule_preserved": unchanged,
                   "governing": result["checks"]["overall_governing"],
                   "secondary": dossier["secondary_steel_checks"],
                   "wind": dossier["wind_direction_checks"]}
            rows.append(row)
            atomic_json(directory / f"{region}_{state}.json", result)
            atomic_json(directory / "summary.json", {"verified": all(r["verified"] for r in rows),
                                                       "completed": len(rows), "planned": 12, "rows": rows})
            print(f"Actual-region check {region} {state}: passing={verified}", flush=True)
    return {"verified": reference["verified"] and len(rows) == 12 and all(r["verified"] for r in rows), "rows": rows}


def report(output, datasets, winners, verification):
    lines = ["# Hay shed search — opening at 63°, covering all A subregions", "",
             "These are the cheapest passing layouts found in this bounded geometry search. Both roof cases reserve a future 6 m cantilever canopy without outer columns or canopy solar. Cost is the main shed's modelled steel purchased now.", "",
             "| Main roof case | Width × length | Equal bays | Main truss depth | Main steel allowance | $/m² |",
             "|---|---|---|---:|---:|---:|"]
    labels = {"no_solar": "No solar", "solar": "Solar 0.15 kPa"}
    for case, row in winners.items():
        lines.append(f"| {labels[case]} | {row['width_m']:g} × {row['length_m']:.3f} m | {row['bay_count']} × {row['bay_m']:.3f} m | {row['truss_depth_mm']:g} mm | ${row['cost']:,.0f} | ${row['cost_per_m2']:.2f} |")
        atomic_json(output / f"best_{case}_inputs.json", selected_inputs(row))
    lines += ["", "The opening faces true bearing 63° (27° north of East). The native north selector uses custom 27° in the section view. The all-A design factors are opening 0.89, opposite side 1.00 and governing end 1.00.", "",
              "## Coverage and validation", "",
              "The coarse search checks every whole-bay layout at widths 15–30 m in 1 m steps and equal bays 5–10 m, holding about 840 m². Nearby widths ±0.25/0.5 m around each case's coarse winner are then tested with all allowable bay counts. The opposite solar winner supplies one additional sizing seed per case. Orientation stays fixed.", ""]
    for case, rows in datasets.items():
        count = sum(bool(r.get("verified")) for r in rows)
        original = [r for r in rows if r.get("verified") and r["width_m"] == 21 and r["bay_count"] == 5]
        text = f"{labels[case]}: {len(rows)} trials, {count} passing."
        if original:
            original_best = min(original, key=lambda r: r["cost"])
            text += f" The 21 × 40 m / 8 m bay reference costs ${original_best['cost']:,.0f}; the selected layout saves ${original_best['cost']-winners[case]['cost']:,.0f} in modelled main steel."
        lines.append("- " + text)
        proof = verification[case]
        lines.append(f"- {labels[case]} final design: {sum(r['verified'] for r in proof['rows'])}/12 checks pass across A0–A5 before and after canopy installation, retaining the same main section, web and reinforcement schedule.")
    lines += ["", "The physics optimiser sizes the current and future states independently, then combines larger required members and the union of doubled chord locations. It verifies the actual combined design in both states. Main truss depth is capped at span/20. Roof purlins are continuous lapped Z; all wall girts are single-span C.", "",
              "The search uses the corrected web-force envelope: both maximum compression and maximum tension are retained, with their source load combinations. The earlier false-pass run was invalidated and not reused in this ranking.", "",
              "## Price and search limits", "",
              "Rates are those saved in the factory input preset: SHS $5.12/kg, doubled chord $6.12/kg, UB $3.70/kg, WB $4.48/kg and secondary steel $1.90/kg. SHS retains the existing 7% waste allowance; roof Z laps retain 15%. Future canopy frame purchase is excluded. Cladding, foundations, full connection fabrication, freight, erection, solar equipment and stock-length nesting are not priced.", "",
              "Member sizing is heuristic and widths are discretised. These are best-found designs, not a proof of the absolute global cost minimum. Failed searches are excluded and do not establish structural infeasibility. Secondary steel is selected automatically per phase; pricing retains the higher allowance per category. Final purchase schedules require consolidation of those selections.", "",
              "Saved best_*_inputs.json files load through the native desktop preset loader, including custom orientation, panel count, web profiles and retained doubled chord locations. The regional verification files contain the fresh checks and source provenance."]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "hay_shed_63_all_a_v2")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--no-refine", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    datasets, stages = {}, {}
    for case, solar in CASES:
        directory = output / "coarse" / case
        datasets[case] = stage(directory, args.workers, solar)
        stages[case] = [str(directory)]
        progress(output, datasets, stages)
    coarse_winners = {case: best(rows) for case, rows in datasets.items()}
    if not args.no_refine:
        for case, solar in CASES:
            winner = coarse_winners[case]
            widths = [winner["width_m"] + d for d in (-.5, -.25, .25, .5)
                      if 15 <= winner["width_m"] + d <= 30]
            seed = output / f"{case}_refinement_seed.json"
            atomic_json(seed, selected_inputs(winner))
            directory = output / "refinement" / case
            datasets[case].extend(stage(directory, args.workers, solar,
                                        "--preset", str(seed), "--widths", ",".join(map(str, widths))))
            stages[case].append(str(directory))
            progress(output, datasets, stages)
    # A seed is useful even where the geometry was already tested; physics is heuristic.
    for case, solar in CASES:
        other = "solar" if case == "no_solar" else "no_solar"
        winner = coarse_winners[other]
        seed = output / f"{case}_cross_seed.json"
        atomic_json(seed, selected_inputs(winner))
        directory = output / "cross_seed" / case
        datasets[case].extend(stage(directory, 1, solar, "--preset", str(seed),
                                    "--widths", str(winner["width_m"]),
                                    "--min-bay", str(winner["bay_m"] - .0005),
                                    "--max-bay", str(winner["bay_m"] + .0005)))
        stages[case].append(str(directory))
        progress(output, datasets, stages)
    verification, winners = {}, {}
    for case, _ in CASES:
        for row in sorted((r for r in datasets[case] if r.get("verified")), key=lambda r: r["cost_per_m2"]):
            proof = verify_in_actual_regions(selected_inputs(row), output / "verification" / case / row["id"])
            if proof["verified"]:
                verification[case], winners[case] = proof, row
                break
            row["verified"] = False
            row["error"] = "Fixed physical schedule failed a fresh actual-subregion check"
            progress(output, datasets, stages)
        if case not in winners:
            raise ValueError(f"No {case} design passed every actual A subregion")
    progress(output, datasets, stages, complete=True, verification=verification)
    report(output, datasets, winners, verification)
    print(f"Completed fixed-bearing search: {output / 'REPORT.md'}", flush=True)


if __name__ == "__main__":
    main()
