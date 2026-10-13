"""Run the physics hay-shed search in coarse, direction and local-width stages."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from hay_shed_search import ROOT, REGIONS, atomic_json


def read_rows(directory):
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    if summary["completed"] != summary["planned"]:
        raise ValueError(f"Incomplete stage: {directory}")
    rows = []
    for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        row["source_directory"] = str(directory.resolve())
        rows.append(row)
    return rows


def shortlist(rows, count):
    selected, seen = [], set()
    for row in sorted(rows, key=lambda r: r.get("cost_per_m2", float("inf"))):
        geometry = (row["width_m"], row["bay_count"])
        if row.get("verified") and geometry not in seen:
            selected.append(row)
            seen.add(geometry)
        if len(selected) == count:
            break
    return selected


def stage_command(output, workers, solar, *options):
    command = [sys.executable, str(ROOT / "hay_shed_search.py"),
               "--output", str(output), "--workers", str(workers),
               "--future-canopy-m", "6", "--solar-kpa", str(solar), *options]
    subprocess.run(command, check=True)
    return read_rows(output)


def save_progress(output, datasets, stages):
    winners = {case: {region: next((r for r in sorted(rows, key=lambda r: r.get("cost_per_m2", float("inf")))
                                   if r.get("verified") and r["region"] == region), None)
                      for region in REGIONS} for case, rows in datasets.items()}
    atomic_json(output / "comparison.json", {"method": "physics staged search; main truss cap span/20",
                                              "complete": False, "stages": stages,
                                              "winners": winners, "rows": datasets})
    return winners


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "hay_shed_physics_staged")
    parser.add_argument("--geometry-output", type=Path,
                        help="Reuse/resume a completed paired A5/east coarse geometry search")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--shortlist", type=int, default=2,
                        help="Distinct coarse layouts per scenario to try in all regions/directions")
    args = parser.parse_args()
    if args.workers < 1 or args.shortlist < 1:
        parser.error("workers and shortlist must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    geometry = (args.geometry_output or output / "geometry").resolve()
    subprocess.run([sys.executable, str(ROOT / "hay_shed_pair.py"), "--regions", "A5", "--facings", "E",
                    "--workers", str(args.workers), "--output", str(geometry)], check=True)
    datasets, stages = {}, {}
    for case in ("no_solar", "solar"):
        paths = [geometry / case, geometry / f"cross_{case}"]
        datasets[case] = [row for path in paths for row in read_rows(path)]
        stages[case] = [str(path) for path in paths]
    save_progress(output, datasets, stages)

    for case, solar in (("no_solar", 0), ("solar", 0.15)):
        for number, winner in enumerate(shortlist(datasets[case], args.shortlist), 1):
            source = Path(winner["source_directory"]) / "calculations" / f"{winner['id']}.json"
            seed = output / f"{case}_direction_seed_{number}.json"
            atomic_json(seed, json.loads(source.read_text(encoding="utf-8"))["dossier"]["inputs"])
            directory = output / f"{case}_directions_{number}"
            # Tight bounds retain this precise bay count/spacing while changing direction.
            rows = stage_command(directory, args.workers, solar, "--preset", str(seed),
                                 "--widths", str(winner["width_m"]),
                                 "--min-bay", str(winner["bay_m"] - 0.0005),
                                 "--max-bay", str(winner["bay_m"] + 0.0005))
            datasets[case].extend(rows)
            stages[case].append(str(directory))
            save_progress(output, datasets, stages)

    winners = save_progress(output, datasets, stages)
    for case, solar in (("no_solar", 0), ("solar", 0.15)):
        passing = [r for r in datasets[case] if r.get("verified")]
        if not passing:
            raise ValueError(f"No verified {case} layout; inspect failed candidate reports")
        seeds = [min(passing, key=lambda r: r["cost_per_m2"]), winners[case]["A5"]]
        seen = set()
        for number, winner in enumerate(seeds, 1):
            if winner is None:
                continue
            key = (winner["width_m"], winner["region"], winner["open_side_faces"])
            if key in seen:
                continue
            seen.add(key)
            widths = [winner["width_m"] + delta for delta in (-0.5, -0.25, 0.25, 0.5)
                      if 15 <= winner["width_m"] + delta <= 30]
            if not widths:
                continue
            source = Path(winner["source_directory"]) / "calculations" / f"{winner['id']}.json"
            seed = output / f"{case}_refinement_seed_{number}.json"
            atomic_json(seed, json.loads(source.read_text(encoding="utf-8"))["dossier"]["inputs"])
            directory = output / f"{case}_refinement_{number}"
            rows = stage_command(directory, args.workers, solar, "--preset", str(seed),
                                 "--widths", ",".join(map(str, widths)),
                                 "--regions", winner["region"], "--facings", winner["open_side_faces"])
            datasets[case].extend(rows)
            stages[case].append(str(directory))
            save_progress(output, datasets, stages)

    winners = save_progress(output, datasets, stages)
    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    comparison["complete"] = True
    atomic_json(output / "comparison.json", comparison)
    for case, regional in winners.items():
        for region, winner in regional.items():
            if winner:
                source = Path(winner["source_directory"]) / "calculations" / f"{winner['id']}.json"
                atomic_json(output / f"best_{region}_{case}_inputs.json",
                            json.loads(source.read_text(encoding="utf-8"))["dossier"]["inputs"])
    print(f"Completed staged comparison: {output / 'comparison.json'}", flush=True)


if __name__ == "__main__":
    main()
