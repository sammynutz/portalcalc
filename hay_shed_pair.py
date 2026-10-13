"""Run an identical hay-shed grid without/with solar, reserving a future canopy."""
import argparse
import copy
import json
import math
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from hay_shed_search import (ROOT, atomic_json, calculate_candidate, digest,
                             initialize_worker, write_summary)


def cross_seed(args, outputs):
    """Protect useful designs found under the opposite roof loading."""
    original = copy.deepcopy(outputs)
    for name, other, solar in (("no_solar", "solar", 0), ("solar", "no_solar", args.solar_allowance)):
        candidates = []
        for region, row in original[other]["best_by_region"].items():
            if row is None:
                continue
            source = args.output.resolve() / other / "calculations" / f"{row['id']}.json"
            inputs = json.loads(source.read_text(encoding="utf-8"))["dossier"]["inputs"]
            inputs["loads"]["solar_kpa"] = solar
            candidates.append({key: copy.deepcopy(row[key]) for key in
                               ("width_m", "length_m", "bay_m", "bay_count", "area_m2", "region", "open_side_faces")}
                              | {"inputs": inputs, "id": digest(inputs)})
        output = args.output.resolve() / f"cross_{name}"
        output.mkdir(exist_ok=True)
        (output / "calculations").mkdir(exist_ok=True)
        rows = []
        with ProcessPoolExecutor(max_workers=min(6, len(candidates) or 1), initializer=initialize_worker,
                                 initargs=(str(output),)) as pool:
            futures = [pool.submit(calculate_candidate, c, str(output)) for c in candidates]
            for future in as_completed(futures):
                rows.append(future.result())
        manifest = {"schema": "portalcalc.hay_shed_search.v1", "candidate_count": len(candidates),
                    "regions": list(original[other]["best_by_region"]),
                    "scope": {"method": "opposite-case winner as sizing seed", "solar_kpa": solar}}
        atomic_json(output / "manifest.json", manifest)
        (output / "results.jsonl").write_text("".join(json.dumps(r, allow_nan=False) + "\n" for r in rows), encoding="utf-8")
        cross = write_summary(output, manifest, rows)
        outputs[name]["cross_seed_trials"] = cross
        for region, row in cross["best_by_region"].items():
            incumbent = outputs[name]["best_by_region"].get(region)
            if row and (not incumbent or row["cost_per_m2"] < incumbent["cost_per_m2"]):
                row["source_directory"] = str(output)
                outputs[name]["best_by_region"][region] = row


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     epilog="Other options are forwarded to hay_shed_search.py (e.g. --plan, --workers, --regions, --widths).")
    parser.add_argument("--output", type=Path, default=ROOT / "hay_shed_pair")
    parser.add_argument("--solar-allowance", type=float, default=0.15)
    parser.add_argument("--future-canopy-m", type=float, default=6)
    args, extra = parser.parse_known_args()
    if not math.isfinite(args.solar_allowance) or args.solar_allowance <= 0:
        parser.error("solar allowance must be positive and finite")
    if "--solar-kpa" in extra:
        parser.error("use --solar-allowance; the paired run owns --solar-kpa")
    outputs = {}
    for name, solar in (("no_solar", 0), ("solar", args.solar_allowance)):
        output = args.output.resolve() / name
        command = [sys.executable, str(ROOT / "hay_shed_search.py"), *extra,
                   "--future-canopy-m", str(args.future_canopy_m),
                   "--solar-kpa", str(solar), "--output", str(output)]
        subprocess.run(command, check=True)
        if "--plan" not in extra:
            outputs[name] = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    if outputs:
        if all(s["completed"] == s["planned"] for s in outputs.values()):
            cross_seed(args, outputs)
        atomic_json(args.output.resolve() / "comparison.json", outputs)
        print(f"Paired summaries: {args.output.resolve() / 'comparison.json'}")


if __name__ == "__main__":
    main()
