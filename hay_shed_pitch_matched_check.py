"""New 7.5deg checks at the new 10deg leaders; does not rerun the completed search."""
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from hay_shed_search import atomic_json, hay_inputs, digest, calculate_candidate, initialize_worker
from hay_shed_pitch_bale_study import ROOT, OUT
import hay_shed_pitch_bale_study as study


def main():
    study.DEPTH_ALLOWANCE = 21 - 17*1.2192
    saved = json.loads((OUT / "comparison.json").read_text(encoding="utf-8"))
    if saved["completed"] != saved["planned"]:
        raise ValueError("Wait for geometry stage to finish")
    ranked = sorted((study.packing(r) for r in saved["rows"] if r.get("verified") and r["pitch_deg"] == 10),
                    key=lambda r:r["cost_per_bale"])
    base = json.loads((ROOT / "presets/factory_defaults.json").read_text(encoding="utf-8"))
    directory = OUT / "matched_7_5deg"
    (directory / "calculations").mkdir(parents=True, exist_ok=True)
    candidates = []
    for area in (576,840):
        row = next(r for r in ranked if r["target_area_m2"] == area)
        geometry = {k:row[k] for k in ("width_m","length_m","bay_m","bay_count","area_m2","target_area_m2")}
        inputs = hay_inputs(base, geometry, "A-ALL", "63", 1,2,7.5,0,"Both",6)
        candidates.append({**geometry,"pitch_deg":7.5,"region":"A-ALL","open_side_faces":"63",
                           "id":digest(inputs),"inputs":inputs,"matching_10deg_id":row["id"]})
    cache = directory / "comparison.json"
    if cache.exists():
        result = json.loads(cache.read_text(encoding="utf-8"))
        if result["candidate_ids"] == [c["id"] for c in candidates] and result["complete"]:
            print("Matched pitch checks already saved")
            return
    rows = []
    with ProcessPoolExecutor(max_workers=2, initializer=initialize_worker, initargs=(str(directory),)) as pool:
        futures = [pool.submit(calculate_candidate,c,str(directory)) for c in candidates]
        for future in as_completed(futures):
            row = {**future.result(),"source_directory":str(directory)}
            rows.append(row)
            atomic_json(cache,dict(complete=len(rows)==len(candidates),candidate_ids=[c["id"] for c in candidates],rows=rows))
            print(f"Matched 7.5deg {row['width_m']:.3f} / {row['bay_m']:.3f}: {row['verified']} ${row.get('cost',0):,.0f}",flush=True)


if __name__ == "__main__":
    main()
