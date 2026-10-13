"""Render the completed staged physics search into a report and a static plot."""
import argparse
import json
from pathlib import Path

from hay_shed_search import ROOT, atomic_json


def describe(row):
    if row is None:
        return "No verified candidate"
    return (f"{row['width_m']:g} × {row['length_m']:.3f} m; "
            f"{row['bay_count']} × {row['bay_m']:.3f} m bays; opening {row['open_side_faces']}; "
            f"${row['cost']:,.0f} (${row['cost_per_m2']:.2f}/m²)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=ROOT / "hay_shed_physics_staged")
    args = parser.parse_args()
    root = args.directory.resolve()
    comparison = json.loads((root / "comparison.json").read_text(encoding="utf-8"))
    if not comparison["complete"]:
        raise ValueError("Staged search is incomplete")
    datasets = comparison["rows"]
    winners = comparison["winners"]
    labels = {"no_solar": "No solar", "solar": "Solar 0.15 kPa"}
    best = {case: min((r for r in rows if r.get("verified")), key=lambda r: r["cost_per_m2"])
            for case, rows in datasets.items()}
    lines = ["# Hay shed — physics search", "",
             "Cheapest verified designs found with Physics Optimise and a maximum main truss depth of span/20. Prices use PortalCalc's current steel rates and cover the main shed built now.", "",
             "Approximately 840 m² main covered area; 6 m eave height; 7.5° gable; clear span; pinned bases; IL1; TC2; one open long side. Main roof G/Q are 0.15/0.25 kPa. The solar case adds 0.15 kPa across both full main roof slopes. Roof purlins are continuous lapped Z (15% lap allowance); side and end-wall girts are single-span C. All automatic secondary capacity checks must pass in both phases.", "",
             "Both cases carry a future 6 m cantilever on the open side, with no outer columns and no canopy solar. Canopy pitch is 5°, G/Q are 0.15/0.25 kPa, with the existing main-roof Cpe + Cpi wind model. Future canopy frame purchases are excluded. Every ranked design passed an exact saved-design reload and a separate initial-state check with the canopy removed.", "",
             "| Case | Cheapest found anywhere in A0–A5 | Subregion | Main truss depth |",
             "|---|---|---|---:|"]
    for case, row in best.items():
        lines.append(f"| {labels[case]} | {describe(row)} | {row['region']} | {row['truss_depth_mm']:g} mm |")
        source = Path(row["source_directory"]) / "calculations" / f"{row['id']}.json"
        result = json.loads(source.read_text(encoding="utf-8"))
        atomic_json(root / f"best_overall_{case}_inputs.json", result["dossier"]["inputs"])
        atomic_json(root / f"best_overall_{case}_result.json", result)
    lines += ["", "Region is a site assumption. The table below keeps each subregion separate; changing subregion is not a saving available at the same site.", "",
              "| Subregion | No solar | Solar 0.15 kPa |", "|---|---|---|"]
    for region in winners["no_solar"]:
        lines.append(f"| {region} | {describe(winners['no_solar'][region])} | {describe(winners['solar'][region])} |")
    lines += ["", "## Original 21 × 40 m concept", ""]
    for case, rows in datasets.items():
        original = [r for r in rows if r.get("verified") and abs(r["width_m"] - 21) < 1e-9
                    and r["bay_count"] == 5 and r["region"] == "A5" and r["open_side_faces"] == "E"]
        if original:
            row = min(original, key=lambda r: r["cost_per_m2"])
            saving = row["cost"] - winners[case]["A5"]["cost"] if winners[case]["A5"] else 0
            lines.append(f"- {labels[case]}, A5/east: {describe(row)}. Best A5 trial saves ${saving:,.0f} in the modelled main steel allowance.")
    lines += ["", "## Coverage and limits", "",
              "Coarse search: all 65 equal-bay layouts at widths 15–30 m in 1 m steps, with bays 5–10 m, in A5 facing east. Direction stage: all eight directions in all six A subregions at the shortlisted distinct layouts. Width refinement: ±0.25 and ±0.5 m around the leading overall and A5 layouts, trying every allowed whole bay count. Opposite solar-case winners are also used as fresh sizing seeds.", ""]
    for case, rows in datasets.items():
        lines.append(f"- {labels[case]}: {len(rows)} trials, {sum(bool(r.get('verified')) for r in rows)} passing, {sum(r.get('status') == 'error' for r in rows)} calculation errors; {len(comparison['stages'][case])} completed stages.")
    lines += ["", "This is the best found in a staged, discrete search. It does not exhaust the full 3,120 combinations per solar scenario or prove the global minimum; member sizing and paired-state repairs remain heuristic. Failed trials are excluded rather than treated as proof that a geometry is infeasible.", "",
              "The objective is the current steel allowance. Cladding, foundations, connections, freight, erection, solar equipment and SHS stock-length nesting are outside the price comparison. SHS retains the engine's 7% waste allowance. Automatically selected secondary members can differ between initial and future phases; costing takes the higher allowance per category, and a common secondary schedule still needs to be selected. These results are a preliminary engineering shortlist.", "",
              "![Passing geometry trials](geometry_comparison.png)", "",
              "Exact reload preserves the main truss panel count and web assignments. Use `hay_shed_check.py` on the saved `best_*_inputs.json`; the ordinary desktop preset loader does not load service web assignments."]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    points = {}
    for case, rows in datasets.items():
        collapsed = {}
        for row in rows:
            if not row.get("verified"):
                continue
            key = row["width_m"], row["bay_m"]
            if key not in collapsed or row["cost_per_m2"] < collapsed[key]["cost_per_m2"]:
                collapsed[key] = row
        points[case] = list(collapsed.values())
    prices = [r["cost_per_m2"] for rows in points.values() for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
    for ax, (case, rows) in zip(axes, points.items()):
        scatter = ax.scatter([r["width_m"] for r in rows], [r["bay_m"] for r in rows],
                             c=[r["cost_per_m2"] for r in rows], cmap="viridis_r", s=65,
                             vmin=min(prices), vmax=max(prices), edgecolors="white", linewidths=0.5)
        row = best[case]
        ax.scatter(row["width_m"], row["bay_m"], marker="*", s=300,
                   facecolors="none", edgecolors="#dc2626", linewidths=2)
        ax.set(title=labels[case], xlabel="Main shed width (m)", ylabel="Equal bay spacing (m)",
               xlim=(14.5, 30.5), ylim=(4.7, 10.3))
        ax.grid(alpha=0.2)
    fig.colorbar(scatter, ax=axes, label="Main steel allowance ($ / main covered m²)")
    fig.suptitle("Physics trials: cheapest passing orientation/subregion at each geometry; span/20 cap")
    fig.savefig(root / "geometry_comparison.png", dpi=160)
    plt.close(fig)
    print(root / "REPORT.md")


if __name__ == "__main__":
    main()
