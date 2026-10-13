"""Separate, resumable 10-degree study; existing 7.5-degree results are read only."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import json
import math

from hay_shed_search import ROOT, atomic_json, digest, hay_inputs, initialize_worker, calculate_candidate
from hay_shed_fixed_bearing import selected_inputs, verify_in_actual_regions
from portalcalc_service import calculation_source_provenance

OUT = ROOT / "hay_shed_10deg_bale_study_v2"
DEPTH_ALLOWANCE = .3
BAY_ALLOWANCE = .5


def packing(row):
    across = math.floor((row["bay_m"] - BAY_ALLOWANCE + 1e-8) / 2.4384)
    deep = math.floor((row["width_m"] - DEPTH_ALLOWANCE + 1e-8) / 1.2192)
    total = across * deep * row["bay_count"] * 6
    return {**row, "bales_per_bay": across, "rows_deep": deep, "capacity": total,
            "cost_per_bale": row["cost"] / total if total and row.get("cost") else None}


def candidates():
    base = json.loads((ROOT / "presets/factory_defaults.json").read_text(encoding="utf-8"))
    geometries = []
    def add(width, bay, count, area, pitch=10, label="alternative"):
        width = round(float(width), 3)
        bay = round(float(bay), 3)
        length = math.nextafter(count * bay, 0)
        geometries.append(dict(width_m=width, length_m=length, bay_m=bay,
                               bay_count=count, area_m2=width * length,
                               target_area_m2=area, pitch_deg=pitch, label=label))
    for area in (576, 840):
        for bay in (5.5, 8, 10.3):
            for count in range(2, 11):
                width = area / (bay * count)
                if not 15 <= width <= 30:
                    continue
                add(round(width, 6), bay, count, area)
                # Round depth upward to the next whole nominal bale row plus clearance.
                deep = math.ceil((width - DEPTH_ALLOWANCE) / 1.2192)
                aligned = round(deep * 1.2192 + DEPTH_ALLOWANCE + .01, 4)
                aligned_bay = round(area / aligned / count, 3)
                if aligned <= 30 and abs(aligned_bay - bay) <= .4 and aligned_bay >= math.floor((bay-.5)/2.4384)*2.4384 + .5:
                    add(aligned, aligned_bay, count, area, label="whole bale rows + clearance")
    for pitch in (7.5, 10):
        add(18, 8, 4, 576, pitch, "existing 18 x 32 offering")
    # Exact 21 x 40 at 10 degrees is already in the target grid.
    for width, bay, count in ((17.25, 8.116, 6), (20, 8.4, 5), (16.75, 5.572, 9),
                              (17, 5.49, 9), (16, 5.833, 9), (17, 6.176, 8)):
        add(width, bay, count, 840, label="saved promising geometry at new pitch")
    unique = {}
    for geometry in geometries:
        inputs = hay_inputs(base, geometry, "A-ALL", "63", 1, 2, geometry["pitch_deg"], 0, "Both", 6)
        candidate = {**geometry, "region": "A-ALL", "open_side_faces": "63", "inputs": inputs, "id": digest(inputs)}
        unique[candidate["id"]] = candidate
    return sorted(unique.values(), key=lambda c: (c["label"] != "existing 18 x 32 offering", abs(c["width_m"]-21), abs(c["bay_m"]-8)))


def save(rows, planned, complete=False, proofs=None):
    old = json.loads((ROOT / "hay_shed_63_all_a_v2/comparison.json").read_text(encoding="utf-8"))
    baseline = [packing({**r, "pitch_deg": 7.5, "target_area_m2": 840})
                for r in old["rows"]["no_solar"] if r.get("verified")]
    ranked = sorted([packing(r) for r in rows if r.get("verified")], key=lambda r: r["cost_per_bale"])
    atomic_json(OUT / "comparison.json", dict(complete=complete, planned=planned, completed=len(rows),
                scope="No-solar, main steel now, future 6 m canopy, A0-A5, bearing 63; six layers; 300 mm depth / 500 mm bay nominal allowance",
                rows=rows, ranked=ranked, saved_7_5deg_ranked=sorted(baseline, key=lambda r:r["cost_per_bale"]),
                regional_proofs=proofs or {}))
    lines = ["# 10-degree hay shed study: steel cost per bale", "",
             f"Completed {len(rows)}/{planned} new trials. Study complete: {complete}.", "",
             "Six layers; 300 mm total nominal depth allowance and 500 mm total nominal bay allowance. "
             "Allowance is before any additional deductions for column projections, aisles or bale variation. "
             "Main steel purchases only; future canopy frame supply and installed-shed costs excluded. "
             "All-A envelope, bearing 63°, eaves 6 m, truss depth capped at span/20. "
             "Prior 7.5° search is read only. New results are heuristic best-found designs, not proof of an absolute minimum.", "",
             "| Pitch | Width × length m | Bays × bay m | Bale rows × bales/bay | Six layers | Main steel | $/bale |",
             "|---|---|---|---|---:|---:|---:|"]
    for r in ranked:
        lines.append(f"| {r['pitch_deg']:g}° | {r['width_m']:.3f} × {r['length_m']:.3f} | {r['bay_count']} × {r['bay_m']:.3f} | "
                     f"{r['rows_deep']} × {r['bales_per_bay']} | {r['capacity']} | ${r['cost']:,.0f} | ${r['cost_per_bale']:.2f} |")
    lines += ["", "Saved 7.5° comparisons (300 mm depth allowance):", ""]
    for r in sorted(baseline, key=lambda r:r["cost_per_bale"])[:5]:
        lines.append(f"- {r['width_m']:g} × {r['length_m']:.3f} m; {r['bay_count']} × {r['bay_m']:.3f} m bays: "
                     f"{r['capacity']} bales, ${r['cost']:,.0f}, ${r['cost_per_bale']:.2f}/bale.")
    lines += ["", "Regional proofs are recorded in comparison.json. Passing trials have saved paired initial/future-canopy envelope checks. "
              "Failed optimiser trials do not establish structural infeasibility. Optional future solar has not been priced in this study.", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / "calculations").mkdir(exist_ok=True)
    trials = candidates()
    manifest = {"candidates": [{k:v for k,v in c.items() if k != "inputs"} for c in trials],
                "provenance": calculation_source_provenance(), "driver_sha256": __import__("hashlib").sha256(Path(__file__).read_bytes()).hexdigest()}
    manifest_path = OUT / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
        raise ValueError("Study inputs or engine changed; use a new output directory")
    atomic_json(manifest_path, manifest)
    journal = OUT / "results.jsonl"
    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()] if journal.exists() else []
    done = {r["id"] for r in rows}
    print(f"{len(trials)} trials, {len(rows)} saved; no solar; 10deg plus missing 18x32 reference", flush=True)
    save(rows, len(trials))
    with ProcessPoolExecutor(max_workers=8, initializer=initialize_worker, initargs=(str(OUT),)) as pool:
        futures = {pool.submit(calculate_candidate, c, str(OUT)): c for c in trials if c["id"] not in done}
        for future in as_completed(futures):
            row = {**future.result(), "source_directory": str(OUT)}
            with journal.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, allow_nan=False)+"\n")
                handle.flush()
            rows.append(row)
            save(rows, len(trials))
            print(f"{len(rows)}/{len(trials)}: {row['pitch_deg']}deg {row['width_m']:.3f}m / {row['bay_count']}x{row['bay_m']:.3f} "
                  f"pass={row['verified']} cost={row.get('cost')} {row.get('error', '')}", flush=True)
    # Regional proof for each area winner and both current offerings at 10 degrees.
    ranked = sorted((packing(r) for r in rows if r.get("verified") and r["pitch_deg"] == 10), key=lambda r:r["cost_per_bale"])
    finalists = []
    for area in (576, 840):
        finalists.extend(next(r for r in ranked if r["target_area_m2"] == area) for _ in range(1))
    finalists.extend(r for r in ranked if (r["width_m"], r["bay_m"], r["bay_count"]) in ((18,8,4),(21,8,5)))
    proofs = {}
    initialize_worker(str(OUT / "proof_runtime"))
    for row in {r["id"]:r for r in finalists}.values():
        proof = verify_in_actual_regions(selected_inputs(row), OUT / "verification" / row["id"])
        proofs[row["id"]] = proof
        atomic_json(OUT / f"verified_{row['id']}_inputs.json", selected_inputs(row))
        save(rows, len(trials), proofs=proofs)
        print(f"Regional proof {row['width_m']:.3f} / {row['bay_m']:.3f}: {proof['verified']}", flush=True)
    save(rows, len(trials), complete=True, proofs=proofs)
    print("Study complete", flush=True)


if __name__ == "__main__":
    main()
