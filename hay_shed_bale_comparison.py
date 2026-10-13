"""Rank saved hay-shed results by storage capacity; never run structural searches."""
from pathlib import Path
import json
import math

ROOT = Path(__file__).resolve().parent / "hay_shed_63_all_a_v2"
BALE_LENGTH = 2.4384
BALE_DEPTH = 1.2192
BALE_HEIGHT = 0.9144
LAYERS = 6
BAY_ALLOWANCE = 0.5  # Illustrative total allowance per bay, before column deductions.


def capacity(row, bay_allowance=BAY_ALLOWANCE, depth_allowance=0.0):
    across = max(0, math.floor((row["bay_m"] - bay_allowance + 1e-9) / BALE_LENGTH))
    deep = max(0, math.floor((row["width_m"] - depth_allowance + 1e-9) / BALE_DEPTH))
    layer = row["bay_count"] * across * deep
    total = layer * LAYERS
    return {
        **row, "bales_per_bay": across, "rows_into_shed": deep,
        "bales_per_layer": layer, "bales_six_layers": total,
        "steel_cost_per_bale_capacity": row["cost"] / total if total else None,
        "nominal_bay_spare_m": row["bay_m"] - across * BALE_LENGTH,
        "nominal_depth_spare_m": row["width_m"] - deep * BALE_DEPTH,
    }


def rank(rows, bay_allowance=BAY_ALLOWANCE, depth_allowance=0.0):
    calculated = [capacity(r, bay_allowance, depth_allowance) for r in rows if r.get("verified")]
    return sorted((r for r in calculated if r["bales_six_layers"]),
                  key=lambda r: (r["steel_cost_per_bale_capacity"], r["cost"], r["id"]))


def table(rows):
    lines = ["| Width × length (m) | Bays × bay (m) | Bales/bay × rows deep | Bales/layer | Six layers | Main steel | $/bale capacity | Bay/depth spare (mm) |",
             "|---|---|---|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['width_m']:g} × {r['length_m']:.3f} | {r['bay_count']} × {r['bay_m']:.3f} | "
                     f"{r['bales_per_bay']} × {r['rows_into_shed']} | {r['bales_per_layer']} | "
                     f"{r['bales_six_layers']:,} | ${r['cost']:,.0f} | ${r['steel_cost_per_bale_capacity']:.2f} | "
                     f"{r['nominal_bay_spare_m']*1000:.0f} / {r['nominal_depth_spare_m']*1000:.0f} |")
    return lines


def main():
    geometry = json.loads((ROOT / "comparison.json").read_text(encoding="utf-8"))
    final = json.loads((ROOT / "FINAL_COMPARISON.json").read_text(encoding="utf-8"))
    optional = json.loads((ROOT / "future_solar" / "comparison.json").read_text(encoding="utf-8"))
    no_solar = rank(geometry["rows"]["no_solar"])
    optional_solar = rank(optional["rows"])
    tight = rank(geometry["rows"]["no_solar"], bay_allowance=0)
    reference = next(r for r in no_solar if r["width_m"] == 21 and r["bay_count"] == 5)
    original_ten = next(r for r in tight if r["width_m"] == 21 and r["bay_count"] == 4)
    near_five = next(r for r in no_solar if r["width_m"] == 17 and r["bay_count"] == 9)
    best_two = next(r for r in no_solar if r["bales_per_bay"] == 2)
    best_three = next(r for r in no_solar if r["bales_per_bay"] == 3)
    best_tight_four = next(r for r in tight if r["bales_per_bay"] == 4)
    current = capacity(final["winners"]["no_solar"])
    selected = [near_five, best_two, best_three, reference, current]
    solar_selected = [next(r for r in optional_solar if r["bales_per_bay"] == n) for n in (2, 3)]
    solar_near_five = next(r for r in optional_solar if r["width_m"] == 17 and r["bay_count"] == 9)
    if solar_near_five["id"] not in {r["id"] for r in solar_selected}:
        solar_selected.append(solar_near_five)
    solar_near_eight = next(r for r in optional_solar if r["width_m"] == 17 and r["bay_count"] == 6)
    if solar_near_eight["id"] not in {r["id"] for r in solar_selected}:
        solar_selected.append(solar_near_eight)
    sensitivity = {str(a): rank(geometry["rows"]["no_solar"], depth_allowance=a)[:5]
                   for a in (0, 0.3, 0.5)}
    output = {
        "method": "Arithmetic reranking of saved passing trials only; no structural calculations or searches run",
        "inputs": ["comparison.json", "FINAL_COMPARISON.json", "future_solar/comparison.json"],
        "bale_dimensions_m": [BALE_LENGTH, BALE_DEPTH, BALE_HEIGHT], "layers": LAYERS,
        "bay_allowance_m": BAY_ALLOWANCE, "depth_allowance_m": 0,
        "requested_bay_targets_m": [5.5, 8, 10.3],
        "largest_saved_bay_m": max(r["bay_m"] for r in geometry["rows"]["no_solar"]),
        "no_solar_ranked": no_solar, "optional_solar_ranked": optional_solar,
        "solar_panels_present_only_ranked": rank(geometry["rows"]["solar"]),
        "tight_nominal_four_bale_proxies": [best_tight_four, original_ten],
        "depth_allowance_sensitivity_m": sensitivity,
    }
    lines = ["# Hay shed: main steel cost per bale of capacity", "",
             "Computed solely from saved results. No search, sizing or new structural checks were run.", "",
             "Bales: 8 ft along each bay, 4 ft into the shed, 3 ft vertically. Six layers are 5.486 m high. "
             "Capacity = bays × bales per bay × floor(nominal width / 1.2192 m) × layers. "
             "Cost per bale is a one-time main-steel allowance per storage position, not a recurring storage charge.", "",
             "## Packing assumptions", "",
             "At the requested bay sizes, two bales in 5.5 m leave 623 mm, three in 8 m leave 685 mm, "
             "and four in 10.3 m leave 546 mm. The ranking uses an illustrative 500 mm total allowance per nominal bay. "
             "No aisle or depth clearance is deducted in the main table. Column projections and bale variation can reduce capacity. "
             "Spare dimensions are nominal, before columns/walls; six-layer figures do not verify handling or stacking feasibility.", "",
             "## No solar: selected saved comparisons", ""]
    lines += table(selected)
    lines += ["", "The 5.833 m row is the best saved two-bale layout by cost per bale under these assumptions; "
              "the 5.490 m row is a closer match to the proposed 5.5 m bay. "
              "The 8.116 m row is the best saved three-bale layout. These are reranked saved trials, not newly optimised designs. "
              "Their saved all-A-envelope paired canopy checks passed; the final no-solar winner additionally has the saved 12 regional proofs.", "",
              "## Four-bale proxy: tighter than the requested bay", ""]
    lines += table([best_tight_four, original_ten])
    lines += ["", "The completed search capped bays at 10 m. There is no saved 10.3 m cost. "
              "These proxies fit four nominal bales only if the 500 mm bay allowance is removed. "
              "The 9.882 m bay leaves only 128 mm before columns, and 10 m leaves 246 mm. "
              "Do not treat either cost as a verified price for 10.3 m bays or as proof of practical four-bale fit.", "",
              "## Optional future solar: completed four-state gate only", ""]
    lines += table(solar_selected)
    lines += ["", "These entries passed the saved four-state gate (bare/canopy/solar/solar+canopy) under the all-A envelope. "
              "The 16 m / 5.833 m final solar winner also has 24 saved regional proofs. "
              "The larger geometry-stage solar dataset assumes panels present and is retained separately in JSON; "
              "its costs must not be substituted for an optional-solar design.", "",
              "## Sensitivity to total depth allowance", "",
              "The nominal packing winner at 17.25 m has just 181 mm spare across fourteen rows. "
              "The 16 m option has just 150 mm across thirteen rows; the original 21 m shed has 274 mm across seventeen rows. "
              "Small clearance deductions therefore change the ranking.", ""]
    for allowance, rows in sensitivity.items():
        lines += [f"### Deduct {float(allowance)*1000:.0f} mm from nominal shed depth", ""] + table(rows[:3]) + [""]
    lines += ["## Cost scope", "",
              "Saved modelled main-steel purchases now, reserving the future 6 m canopy. "
              "Future canopy frame supply, cladding, foundations, full connection fabrication, freight, erection and solar equipment are excluded. "
              "Existing 7% SHS waste and 15% roof Z laps are retained. SHS stock-length nesting remains deferred. "
              "All-A means A0–A5, opening bearing 63°, 6 m eaves and 7.5° roof. "
              "No new design approval or construction verification is implied.", ""]
    (ROOT / "BALE_COMPARISON.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    report = ROOT / "BALE_COMPARISON.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(report)
    print("Best no-solar $/bale:", round(best_three["steel_cost_per_bale_capacity"], 2))
    print("Best no-solar with 300 mm depth allowance:",
          sensitivity["0.3"][0]["width_m"], sensitivity["0.3"][0]["bay_m"],
          round(sensitivity["0.3"][0]["steel_cost_per_bale_capacity"], 2))


if __name__ == "__main__":
    main()
