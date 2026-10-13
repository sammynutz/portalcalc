"""Apply the optional-solar four-state requirement to geometry-search finalists."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from hay_shed_search import ROOT, atomic_json, digest, initialize_worker, secondary_verified
from hay_shed_staged import read_rows
from hay_shed_fixed_bearing import selected_inputs, verify_in_actual_regions


def calculate(row, directory):
    from future_solar_design import FutureSolarBackend
    from portalcalc_service import PortalCalcCalculationService, calculation_source_provenance
    started = time.monotonic()
    directory = Path(directory)
    inputs = selected_inputs(row)
    inputs["loads"]["solar_kpa"] = .15
    inputs["hay_shed_search"]["future_solar_allowance"] = True
    proposal = {**row, "seed_id": row["id"], "id": digest(inputs), "source_directory": str(directory)}
    result_file = directory / "calculations" / f"{proposal['id']}.json"
    row_file = directory / "rows" / f"{proposal['id']}.json"
    if row_file.exists():
        saved = json.loads(row_file.read_text(encoding="utf-8"))
        if saved.get("status") == "stopped_optional":
            return saved
    if result_file.exists():
        result = json.loads(result_file.read_text(encoding="utf-8"))
        actual = result["dossier"]["engine_provenance"]
        expected = calculation_source_provenance()
        if actual["source_file_sha256"] != expected["source_file_sha256"]:
            raise ValueError("Cached four-state result uses different calculation sources")
    else:
        service = PortalCalcCalculationService(FutureSolarBackend())
        result = service.run({"request_id": proposal["id"], "mode": "autoSize", "inputs": inputs})
        atomic_json(result_file, result)
    dossier = result["dossier"]
    optimisation = dossier.get("optimisation", {})
    phases = optimisation.get("phases", {})
    verified = result["verified"] and secondary_verified(dossier) and len(phases) == 4 and all(
        p["verified"] for p in phases.values())
    cost = float(optimisation.get("cost", dossier["costing_summary"]["total_cost"]))
    paired = dossier["inputs"].get("future_canopy_design", {}).get("paired_costs", {})
    for key in ("future_main_cost", "future_canopy_frame_cost", "as_built_verified", "future_state_verified"):
        proposal.pop(key, None)
    proposal.update(verified=verified, cost=cost, cost_per_m2=cost / row["area_m2"],
                    sections=result["sizing"], phases=phases,
                    seed_sizing_cost=row["cost"], costs=paired, paired_main_costs=paired,
                    ratio=max((p["ratio"] for p in phases.values()), default=None),
                    secondary_checks=dossier["secondary_steel_checks"],
                    exact_solves=optimisation.get("exact_solves"),
                    elapsed_seconds=round(time.monotonic() - started, 2),
                    error=optimisation.get("error"))
    atomic_json(directory / "rows" / f"{proposal['id']}.json", proposal)
    return proposal


def candidates(root, top, allow_partial=False):
    all_rows = {case: [] for case in ("solar", "no_solar")}
    for stage in ("coarse", "refinement", "cross_seed", "optional_refinement"):
        for case in all_rows:
            directory = root / stage / case
            if directory.exists():
                if allow_partial:
                    available = [dict(json.loads(line), source_directory=str(directory.resolve()))
                                 for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()]
                else:
                    available = read_rows(directory)
                all_rows[case].extend(r for r in available if r.get("verified"))
    # Refine all cheap solar proposals, plus cheap no-solar geometries as
    # different stiffness/topology seeds. Exact final checks decide feasibility.
    selected = []
    for case in all_rows:
        selected.extend(sorted(all_rows[case], key=lambda r: r["cost_per_m2"])[:top])
    return list({r["id"]: r for r in selected}.values())


def verify_final(inputs, directory):
    """Resume an unchanged completed proof; otherwise run fresh native checks."""
    from portalcalc_service import calculation_source_provenance
    from hay_shed_backend import SOURCE_SHA256 as backend_sha
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    signature = digest({"inputs": inputs, "engine": calculation_source_provenance(),
        "backend_sha256": backend_sha,
        "verifier_sha256": hashlib.sha256((ROOT / "hay_shed_fixed_bearing.py").read_bytes()).hexdigest()})
    cache = directory / "proof.json"
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if (saved.get("signature") == signature and saved["proof"]["verified"] and
            all((directory / name).exists() and hashlib.sha256((directory / name).read_bytes()).hexdigest() == value
                for name, value in saved.get("evidence_sha256", {}).items()) and
            len(saved.get("evidence_sha256", {})) >= 26):
            return saved["proof"]
    proofs = {}
    for solar in (.15, 0):
        current = copy.deepcopy(inputs)
        current["loads"]["solar_kpa"] = solar
        proofs[str(solar)] = verify_in_actual_regions(current, directory / str(solar))
    references = [json.loads((directory / str(load) / "A-ALL_reference.json").read_text(encoding="utf-8"))["dossier"]
                  for load in (.15, 0)]
    unchanged = (references[0]["physical_main_schedule"] == references[1]["physical_main_schedule"]
                 and references[0]["inputs"]["sections"] == references[1]["inputs"]["sections"]
                 and references[0]["inputs"]["calculation_service"] == references[1]["inputs"]["calculation_service"])
    proof = {"verified": all(p["verified"] for p in proofs.values()) and unchanged,
             "same_main_schedule_with_and_without_panels": unchanged, "roof_load_cases": proofs}
    evidence = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in directory.glob("*/*.json")}
    atomic_json(cache, {"signature": signature, "proof": proof, "evidence_sha256": evidence})
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search", type=Path, default=ROOT / "hay_shed_63_all_a_v2")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--skip-regional", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    output = (args.output or args.search / "future_solar").resolve()
    for name in ("calculations", "rows"):
        (output / name).mkdir(parents=True, exist_ok=True)
    source_sha = hashlib.sha256((ROOT / "future_solar_design.py").read_bytes()).hexdigest()
    from portalcalc_service import calculation_source_provenance
    from hay_shed_backend import SOURCE_SHA256 as backend_sha
    manifest = {"module_sha256": source_sha, "backend_sha256": backend_sha,
                "engine": calculation_source_provenance(), "top_seeds_per_case": args.top,
                "seed_ids": [r["id"] for r in candidates(args.search, args.top, args.allow_partial)]}
    old_manifest = output / "manifest.json"
    if old_manifest.exists():
        old = json.loads(old_manifest.read_text(encoding="utf-8"))
        if old["module_sha256"] != source_sha:
            raise ValueError("Four-state engine changed; use a new output directory")
        if old.get("engine", manifest["engine"]) != manifest["engine"] or old.get("backend_sha256", backend_sha) != backend_sha:
            raise ValueError("Calculation engine changed; use a new output directory")
    atomic_json(old_manifest, manifest)
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=initialize_worker,
                             initargs=(str(output / "runtime"),)) as pool:
        tasks = {pool.submit(calculate, row, str(output)): row for row in candidates(args.search, args.top, args.allow_partial)}
        for task in as_completed(tasks):
            seed = tasks[task]
            try:
                row = task.result()
            except Exception as error:
                row = {**seed, "verified": False, "error": str(error)}
            rows.append(row)
            atomic_json(output / "comparison.json", {"complete": False, "rows": rows})
            print(f"Four-state {len(rows)}/{len(tasks)}: {row['width_m']:g} m / {row['bay_m']:g} m bays, "
                  f"pass={row['verified']} ${row.get('cost') or 0:,.0f} {row.get('error') or ''}", flush=True)
    winners = sorted((r for r in rows if r.get("verified")), key=lambda r: r["cost_per_m2"])
    if not winners:
        raise ValueError("No common solar/canopy schedule passed all four states")
    proof = None
    winner = winners[0]
    if not args.skip_regional:
        for winner in winners:
            data = selected_inputs(winner)
            proof = verify_final(data, output / "verification" / winner["id"])
            if proof["verified"]:
                break
        if not proof["verified"]:
            raise ValueError("No four-state finalist passed every actual A subregion")
    atomic_json(output / "best_solar_inputs.json", selected_inputs(winner))
    atomic_json(output / "comparison.json", {"complete": not args.skip_regional, "rows": rows,
        "winner": winner, "actual_subregion_verification": proof})
    print(f"Solar provision winner: {winner['width_m']:g} × {winner['length_m']:.3f}, "
          f"{winner['bay_count']} × {winner['bay_m']:g} bays, ${winner['cost']:,.0f}", flush=True)


if __name__ == "__main__":
    main()
