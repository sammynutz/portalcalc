"""Publish the completed geometry search with its final four-state solar gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hay_shed_search import ROOT, atomic_json
from hay_shed_fixed_bearing import selected_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search", type=Path, default=ROOT / "hay_shed_63_all_a_v2")
    args = parser.parse_args()
    root = args.search.resolve()
    geometry = json.loads((root / "comparison.json").read_text(encoding="utf-8"))
    solar = json.loads((root / "future_solar" / "comparison.json").read_text(encoding="utf-8"))
    if not geometry["complete"] or not solar["complete"]:
        raise ValueError("Geometry search and four-state regional verification must finish first")
    no_solar = geometry["winners"]["no_solar"]
    future_solar = solar["winner"]
    proofs = {"no_solar": geometry["universal_verification"]["no_solar"],
              "solar": solar["actual_subregion_verification"]}
    if not all(p["verified"] for p in proofs.values()):
        raise ValueError("Final physical schedules have not passed every required region/state")
    winners = {"no_solar": no_solar, "solar": future_solar}
    final = {"complete": True, "open_side_true_bearing_deg": 63,
             "regions": [f"A{i}" for i in range(6)], "winners": winners,
             "verification": proofs, "scope": "Main shed steel purchased now; canopy frame excluded",
             "solar_is_future_allowance": True, "solar_kpa": .15,
             "canopy_m": 6, "canopy_solar_kpa": 0,
             "stopped_optional_trials": solar.get("stopped_optional_trials", [])}
    atomic_json(root / "FINAL_COMPARISON.json", final)
    labels = {"no_solar": "No solar", "solar": "Future solar 0.15 kPa"}
    lines = ["# Hay shed results — 63° opening, all A subregions", "",
             "These are the cheapest passing designs found in this bounded search. The main shed is built now and sized for a future 6 m cantilever canopy, without outer columns or canopy solar. The solar option must also pass before panels are installed.", "",
             "| Main roof allowance | Width × length | Equal bays | Main truss depth | Main steel now | $/m² |",
             "|---|---|---|---:|---:|---:|"]
    for case, row in winners.items():
        lines.append(f"| {labels[case]} | {row['width_m']:g} × {row['length_m']:.3f} m | {row['bay_count']} × {row['bay_m']:.3f} m | {row['truss_depth_mm']:g} mm | ${row['cost']:,.0f} | ${row['cost_per_m2']:.2f} |")
        atomic_json(root / f"best_{case}_inputs.json", selected_inputs(row))
    lines += ["", "Both layouts have about 840 m² main covered area, 6 m eaves, 7.5° gable roof, IL1 and TC2. The open long side faces true bearing 63°, or 27° north of East. A2 remains included. The native north selector is custom 27° in section view.", "",
              "Future canopy assumptions: 6 m cantilever, 5° pitch, 0.15 kPa dead load and 0.25 kPa live load, no solar, using the native Main roof Cpe + Cpi wind model. Its geometry and future member schedule are retained in the saved preset.", "",
              "## Physical design and verification", "",
              "One common main frame is used across every installation state. Larger required primary members and individual web assignments are combined with the union of doubled chord panels and web joint doublers. Every combined design is checked again, preserving its physical member geometry and reinforcement. Main truss depth stays at or below nominal span/20. Roof purlins use continuous lapped Z; wall and end girts use single-span C.", "",
              "- No-solar design: 12/12 fresh checks pass across A0–A5, before and after canopy installation.",
              "- Future-solar design: 24/24 fresh checks pass across A0–A5 in bare, canopy-only, solar-only and solar-plus-canopy states.", "",
              "The regional proof uses the saved final sections, truss panel count, web assignments and retained chord reinforcement. No member sizing is requested during these checks. The corrected web envelope retains compression and tension independently with their governing combinations; the earlier false-pass search is excluded.", ""]
    for case, row in winners.items():
        data = selected_inputs(row)
        sections = data["sections"]
        count = data["calculation_service"]["main_truss_panel_count"]
        doubled = [p for p in data["future_canopy_design"]["doubled_chord_members"] if p[0] in {"TOP", "BOTTOM"}]
        lines += [f"### {labels[case]} schedule", "",
                  f"Rear/closed-side column: **{sections['left_column']}**. Front/open-side column: **{sections['right_column']}**. Main top/bottom chord selectors: **{sections['top_chord']} / {sections['bottom_chord']}**. {count} truss panels; {len(doubled)} retained main doubled-chord panels.", "",
                  "Individual web sizes and exact reinforcement locations are saved in the native input preset and fresh verification dossiers.", ""]
        phases = data["future_canopy_design"]["phases"].values()
        common = {label: {p["secondary_checks"][label].get("section", "") for p in phases}
                  for label in ("roof_general", "roof_edge", "long_wall_girts", "end_wall_girts")}
        if all(len(names) == 1 for names in common.values()):
            names = {label: next(iter(values)) for label, values in common.items()}
            lines += [f"The same cold-formed selections pass every installation-state check under the all-A envelope: roof general **{names['roof_general']}**, roof edge **{names['roof_edge']}**, long-wall girts **{names['long_wall_girts']}**, end-wall girts **{names['end_wall_girts']}**.", ""]
    reference = [r for r in geometry["rows"]["no_solar"] if r.get("verified") and r["width_m"] == 21 and r["bay_count"] == 5]
    if reference:
        cost = min(r["cost"] for r in reference)
        lines += [f"The passing 21 × 40 m / 8 m bay no-solar reference costs ${cost:,.0f}. The selected no-solar layout saves ${cost-no_solar['cost']:,.0f} in modelled main steel.", ""]
    lines += ["## Search method", "",
              "The opening bearing is fixed using the continuous wind-direction calculation. The geometry search tests 65 whole-bay layouts per roof case at widths 15–30 m in 1 m steps, with bays 5–10 m. It then tests widths ±0.25/0.5 m around the coarse winners and uses the opposite roof case as an additional sizing seed. The native physics optimiser screens sections and performs detailed checks.", "",
              "Four additional nearby widths, 15.5, 15.75, 16.25 and 16.5 m, are tested at the provisional optional-solar winner's nine-bay layout. These supply further seeds to the final four-state gate.", "",
              f"The optional-solar gate completed {len(solar['rows'])-len(solar.get('stopped_optional_trials', []))} cheap seeds from both roof cases, repairing missing installation-state requirements at common geometry and ranking their four-state costs. One optional extra seed for the already-tested 16.5 m / 6.364 m bay geometry was stopped after prolonged runtime; the passing alternative at that geometry costs $34,176. The geometry-stage solar costs assume panels are present and are retained only as sizing seeds; FINAL_COMPARISON.json and best_solar_inputs.json contain the final future-solar proposal.", "",
              "Widths are discretised and member sizing is heuristic. These are best-found designs, not a proof of the absolute global minimum. Failed searches do not establish structural infeasibility.", "",
              "## Price scope", "",
              "Main steel allowance uses the saved factory rates: SHS $5.12/kg, doubled chord $6.12/kg, UB $3.70/kg, WB $4.48/kg and secondary steel $1.90/kg. SHS includes the existing 7% waste allowance; roof Z laps include 15%. Costs retain the higher allowance per category across installation states. Automatic secondary selections must be consolidated for the eventual purchase schedule.", "",
              "Future canopy frame steel, cladding, foundations, full connection fabrication, freight, erection and solar equipment are excluded. SHS stock-length nesting is deferred. The reported allowance is modelled steel purchase cost, not an installed shed quote.", "",
              "## Reproduction", "",
              "Run hay_shed_fixed_bearing.py, then hay_shed_future_solar.py, then hay_shed_optional_solar_report.py. The stages preserve source/data hashes and exact calculation dossiers. best_no_solar_inputs.json and best_solar_inputs.json load through the native desktop preset loader."]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(root / "REPORT.md")


if __name__ == "__main__":
    main()
