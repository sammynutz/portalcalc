"""Apply the user's original 21m/17-row tolerance to saved pitch-study results."""
import json
from hay_shed_pitch_bale_study import OUT, ROOT, packing
import hay_shed_pitch_bale_study as study


def main():
    # Exactly the residual in the user's existing 21m offering with 17 nominal rows.
    study.DEPTH_ALLOWANCE = 21 - 17 * 1.2192
    data = json.loads((OUT / "comparison.json").read_text(encoding="utf-8"))
    old = json.loads((ROOT / "hay_shed_63_all_a_v2/comparison.json").read_text(encoding="utf-8"))
    new = sorted((packing(r) for r in data["rows"] if r.get("verified")), key=lambda r:r["cost_per_bale"])
    matched_path = OUT / "matched_7_5deg/comparison.json"
    matched = json.loads(matched_path.read_text(encoding="utf-8")) if matched_path.exists() else {}
    matched_rows = [packing(r) for r in matched.get("rows", []) if r.get("verified")]
    baseline = sorted((packing({**r, "pitch_deg":7.5, "target_area_m2":840})
                       for r in old["rows"]["no_solar"] if r.get("verified")), key=lambda r:r["cost_per_bale"])
    result = dict(complete=data["complete"], completed=data["completed"], planned=data["planned"],
                  depth_allowance_m=study.DEPTH_ALLOWANCE, bay_allowance_m=study.BAY_ALLOWANCE,
                  layers=6, new_ranked=new, saved_7_5deg_ranked=baseline,
                  matched_pitch_checks=matched_rows, matched_pitch_checks_complete=matched.get("complete", False),
                  regional_proofs=data.get("regional_proofs", {}))
    (OUT / "BALE_COMPARISON.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    lines = ["# Pitch and bale-capacity comparison: original offering tolerance", "",
             "This report supersedes REPORT.md's initial 300mm capacity assumption. "
             f"Study complete: {data['complete']}; {data['completed']}/{data['planned']} new structural trials saved.", "",
             "User-selected packing rule: preserve the original 21m offering's 17 nominal four-foot rows. "
             "That gives a total depth allowance of 273.6mm. Apply the same allowance to every width. "
             "Nominal bales: 2.4384m along the bay, 1.2192m into the shed and 0.9144m high; six layers. "
             "Bay allowance remains 500mm total. No additional aisle, column or bale-variation deduction is made. "
             "These are arithmetic capacities, not verified handling or bale-clearance dimensions.", ""]
    def table(rows):
        output = ["| Pitch | Width × length m | Bays × bay m | Rows deep | Bales, six layers | Main steel | $/bale |",
                  "|---|---|---|---:|---:|---:|---:|"]
        for r in rows:
            output.append(f"| {r['pitch_deg']:g}° | {r['width_m']:.3f} × {r['length_m']:.3f} | {r['bay_count']} × {r['bay_m']:.3f} | "
                          f"{r['rows_deep']} | {r['capacity']} | ${r['cost']:,.0f} | ${r['cost_per_bale']:.2f} |")
        return output
    lines += ["## Existing offerings", ""]
    offerings = [r for r in new+baseline if (r["width_m"], r["bay_m"], r["bay_count"]) in ((18,8,4),(21,8,5))]
    lines += table(sorted(offerings, key=lambda r:(r["width_m"],r["pitch_deg"])))
    lines += ["", "## Matched pitch checks at the two new leaders", ""]
    pairs = []
    for r in matched_rows:
        pairs += [r, next(n for n in new if n["id"] == r["matching_10deg_id"])]
    lines += table(pairs)
    for area in (576,840):
        lines += ["", f"## New 10° alternatives: about {area}m²", ""]
        relevant = [r for r in new if r["target_area_m2"] == area and r["pitch_deg"] == 10]
        lines += table(relevant)
    lines += ["", "## Saved 7.5° alternatives: about 840m²", ""] + table(baseline[:10])
    lines += ["", "## Interpretation and evidence", "",
              "All new structural trials use the saved engine's all-A envelope and paired initial/future-canopy check. "
              "Separate regional proofs, when completed, are recorded in BALE_COMPARISON.json. "
              "The original 7.5° final winner has its existing saved regional proof; other saved alternatives remain envelope-stage results. "
              "No solar allowance was priced in the new pitch study. Future-solar proposals would require the four-state gate.", "",
              "Pitch comparison is direct only for matching geometries. Most alternatives were newly tested at 10° only; "
              "differences against other 7.5° layouts combine pitch, geometry and heuristic member sizing. "
              "Whole-row aligned widths were generated using the initial 300mm allowance and then reranked using 273.6mm. "
              "A ranking difference does not prove that roof pitch caused the cost difference.", "",
              "Cost scope: modelled main steel purchases now, provision for future 6m canopy; excludes future canopy frame supply, "
              "cladding, foundations, full connection fabrication, freight, erection and solar equipment. "
              "SHS stock nesting remains deferred. Best-found bounded trials, not global optimality.", ""]
    report = OUT / "BALE_COMPARISON.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(report)
    for area in (576,840):
        rows = [r for r in new if r["target_area_m2"] == area and r["pitch_deg"] == 10]
        if rows:
            r=rows[0]
            print(area, r["width_m"],r["bay_m"],r["capacity"],r["cost_per_bale"])


if __name__ == "__main__":
    main()
