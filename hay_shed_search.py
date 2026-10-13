"""Resumable, process-parallel geometry search using the real PortalCalc optimiser.

Run --plan first. Results are cheapest *found* structural-steel estimates,
not a proof of global optimality or an installed building price.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIRECTIONS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
REGIONS = tuple(f"A{i}" for i in range(6))
SEARCH_SCHEMA = "portalcalc.hay_shed_search.v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def atomic_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def north_selector(open_direction):
    # The right long wall faces screen E; MainWindow maps source = screen - north.
    if open_direction in DIRECTIONS:
        return DIRECTIONS[(2 - DIRECTIONS.index(open_direction)) % 8]
    from wind import direction_bearing_degrees
    return f"{(90 - direction_bearing_degrees(open_direction)) % 360:g}°"


def secondary_verified(dossier):
    checks = dossier.get("secondary_steel_checks") or {}
    return bool(checks) and all(item.get("status") == "OK" for item in checks.values())


def geometry_grid(area, widths, min_bay, max_bay):
    """Whole, equal bays; millimetre rounding keeps area within about 0.3 m²."""
    for width in widths:
        ideal_length = area / width
        for count in range(math.ceil(ideal_length / max_bay - 1e-9),
                           math.floor(ideal_length / min_bay + 1e-9) + 1):
            bay = round(ideal_length / count, 3)
            if not min_bay <= bay <= max_bay:
                continue
            # Costing uses ceil(length/bay). Avoid a floating-point extra portal.
            length = math.nextafter(count * bay, 0.0)
            yield {"width_m": width, "length_m": length, "bay_m": bay,
                   "bay_count": count, "area_m2": width * length}


def hay_inputs(base, geometry, region, facing, importance, terrain, pitch, solar_kpa=0, solar_side="Both", future_canopy_m=0):
    inputs = copy.deepcopy(base)
    inputs.pop("calculation_service", None)  # Saved web IDs belong to the old geometry.
    inputs.pop("future_canopy_design", None)
    inputs["sections"].update(purlin_span_type="Continuous lapped", wall_girt_span_type="Single span")
    inputs["geometry"].update(span_mm=geometry["width_m"] * 1000,
                              eave_height_mm=6000, roof_pitch_deg=pitch,
                              frame_system="Truss", roof_shape="Gable",
                              internal_column_count="None", eave_x_restraint="None",
                              max_truss_depth_mm=geometry["width_m"] * 1000 / 20,
                              truss_depth_mm=round(geometry["width_m"] * 40 / 50) * 50)
    inputs["loads"].update(bay_size_mm=geometry["bay_m"] * 1000,
                           bay_size_m=geometry["bay_m"], solar_kpa=solar_kpa,
                           fire_service_kpa=0, hvac_kpa=0, other_kpa=0,
                           crane_rating_t=0, left_wall_kn_m=0, right_wall_kn_m=0)
    inputs["loads"].setdefault("roof_load_placements", {})["solar"] = {
        "side": solar_side, "from_percent": "0", "to_percent": "100"}
    inputs["wind"].update(region=region, orientation=north_selector(facing),
                          importance_level=importance, terrain_category=terrain,
                          building_length_m=geometry["length_m"], frame_type="3 Sided",
                          wall_enclosure={"left": True, "right": False,
                                          "front": True, "back": True})
    inputs["canopies"].update(left_type="None", right_type="None",
                              left_length_mm=0, right_length_mm=0)
    inputs["hay_shed_search"] = {"optimiser": "physics", "max_main_depth_span_ratio": 0.05}
    from wind import direction_bearing_degrees
    inputs["hay_shed_search"]["open_side_true_bearing_deg"] = direction_bearing_degrees(facing)
    if future_canopy_m > 0:
        inputs["canopies"].update(right_type="Canopy", right_length_mm=future_canopy_m * 1000,
                                  right_frame_system="Truss", right_solar_kpa=0,
                                  right_pitch_deg="5", right_depth_mm="800",
                                  right_g_kpa="0.15", right_q_kpa="0.25",
                                  right_wind_model="Main roof Cpe + Cpi")
        inputs["hay_shed_search"].update(future_canopy_m=future_canopy_m, cost_scope="main_now")
    inputs.setdefault("storage_loads", {})["enabled"] = False
    inputs.setdefault("display", {})["second_order_analysis"] = False
    return inputs


def make_candidates(args, base):
    widths = [float(item) for item in args.widths.split(",")]
    if not widths or any(not math.isfinite(w) or w <= 0 for w in widths):
        raise ValueError("widths must be positive finite metres")
    candidates = []
    for geometry in geometry_grid(args.area, sorted(set(widths)), args.min_bay, args.max_bay):
        for region in args.regions.split(","):
            if region not in (*REGIONS, "A-ALL"):
                raise ValueError("regions must be A0 through A5 or A-ALL")
            for facing in args.facings.split(","):
                from wind import direction_bearing_degrees
                direction_bearing_degrees(facing)
                inputs = hay_inputs(base, geometry, region, facing,
                                    args.importance, args.terrain, args.pitch,
                                    getattr(args, "solar_kpa", 0) or 0,
                                    getattr(args, "solar_side", "Both"),
                                    getattr(args, "future_canopy_m", 0))
                candidates.append({**geometry, "region": region, "open_side_faces": facing,
                                   "inputs": inputs, "id": digest(inputs)})
    # Put the existing 21m/8m concept first, then explore the rest of the grid.
    candidates.sort(key=lambda c: (abs(c["width_m"] - 21), abs(c["bay_m"] - 8),
                                    c["region"], c["open_side_faces"]))
    return candidates


def initialize_worker(output):
    # Keep desktop settings and optimiser learning isolated from the user's app.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["APPDATA"] = str(Path(output) / "runtime" / str(os.getpid()))
    # Avoid competing BLAS thread pools when several engines run together.
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[name] = "1"
    from PySide6.QtWidgets import QApplication, QMessageBox
    global _APP
    _APP = QApplication.instance() or QApplication([])

    def fail_dialog(parent, title, message, *args, **kwargs):
        raise RuntimeError(f"{title}: {message}")

    QMessageBox.warning = fail_dialog
    QMessageBox.critical = fail_dialog


def calculate_candidate(candidate, output):
    from hay_shed_backend import PhysicsSearchBackend
    from portalcalc_service import PortalCalcCalculationService
    started = time.monotonic()
    row = {k: v for k, v in candidate.items() if k != "inputs"}
    try:
        future_canopy = (candidate["inputs"].get("hay_shed_search") or {}).get("future_canopy_m", 0)
        if future_canopy:
            from hay_shed_backend import FutureCanopyBackend
            backend = FutureCanopyBackend()
        else:
            backend = PhysicsSearchBackend()
        service = PortalCalcCalculationService(backend)
        result = service.run({"request_id": candidate["id"], "mode": "autoSize",
                              "inputs": candidate["inputs"]})
        dossier = result["dossier"]
        cost = dossier["costing_summary"]
        selected_inputs = dossier["inputs"]
        row["secondary_checks"] = dossier.get("secondary_steel_checks", {})
        row["wind_direction_checks"] = dossier["wind_direction_checks"]
        # Check the actual geometry consumed by the desktop adapter, including rounding.
        if cost["bay_count"] != candidate["bay_count"]:
            raise ValueError(f"Engine counted {cost['bay_count']} bays, expected {candidate['bay_count']}")
        actual_wind = selected_inputs["wind"]
        actual_geometry = selected_inputs["geometry"]
        if actual_wind["region"] != candidate["region"]:
            raise ValueError("Engine wind region differs from the requested envelope")
        from wind import direction_bearing_degrees, design_direction_multiplier
        facing = direction_bearing_degrees(candidate["open_side_faces"])
        expected_md = {
            "Right +": design_direction_multiplier(candidate["region"], facing),
            "Left +": design_direction_multiplier(candidate["region"], facing + 180),
            "End +": max(design_direction_multiplier(candidate["region"], facing - 90),
                         design_direction_multiplier(candidate["region"], facing + 90)),
        }
        for case, md in expected_md.items():
            if not math.isclose(dossier["wind_direction_checks"][case]["design_md"], md, abs_tol=1e-10):
                raise ValueError(f"Engine {case} Md differs from the requested physical bearing")
        if selected_inputs["sections"].get("purlin_span_type") != "Continuous lapped" or selected_inputs["sections"].get("wall_girt_span_type") != "Single span":
            raise ValueError("Engine changed the requested lapped Z roof / single-span C wall arrangement")
        if actual_wind["wall_enclosure"] != candidate["inputs"]["wind"]["wall_enclosure"]:
            raise ValueError("Engine enclosure differs from the requested open long side")
        if actual_wind["orientation"] != candidate["inputs"]["wind"]["orientation"]:
            raise ValueError("Engine orientation differs from requested open-side direction")
        if not math.isclose(float(actual_geometry["span_mm"]), candidate["width_m"] * 1000, abs_tol=0.1):
            raise ValueError("Engine width differs from requested width")
        depth_cap = candidate["width_m"] * 1000 / 20
        if float(actual_geometry["truss_depth_mm"]) > depth_cap + 1e-6:
            raise ValueError(f"Main truss depth exceeds span/20 ({depth_cap:g} mm)")
        expected_algorithm = "physics hybrid" if future_canopy else "physics"
        if dossier.get("optimisation", {}).get("algorithm") != expected_algorithm:
            raise ValueError("Candidate was not sized with the physics optimiser")
        total = float(cost["total_cost"])
        if not math.isfinite(total) or total <= 0:
            raise ValueError("Engine did not return a positive finite cost")
        if result["verified"] and secondary_verified(dossier):
            checked = service.run({"request_id": candidate["id"] + "-reload",
                                   "mode": "checkSpecified", "inputs": selected_inputs})
            checked_cost = float(checked["dossier"]["costing_summary"]["total_cost"])
            if not checked["verified"] or not secondary_verified(checked["dossier"]) or not math.isclose(total, checked_cost, rel_tol=1e-8, abs_tol=0.01):
                atomic_json(Path(output) / "calculations" / f"{candidate['id']}.unreproduced.json", result)
                atomic_json(Path(output) / "calculations" / f"{candidate['id']}.failed-reload.json", checked)
                raise ValueError(f"Saved design reload mismatch: passing={checked['verified']}, cost {total:.2f} -> {checked_cost:.2f}")
            atomic_json(Path(output) / "calculations" / f"{candidate['id']}.checked.json", checked)
        verified = result["verified"] and secondary_verified(dossier)
        if result["verified"] and not verified:
            row["error"] = "Automatic roof/wall secondary-steel checks did not all pass"
        if not verified and dossier.get("optimisation", {}).get("error"):
            row["error"] = dossier["optimisation"]["error"]
        if future_canopy and verified:
            current_inputs = copy.deepcopy(selected_inputs)
            current_inputs["canopies"]["right_type"] = "None"  # Retain future geometry for the physical schedule.
            selections = current_inputs.get("calculation_service", {})
            for key in ("auto_web_profile_assignments", "auto_web_joint_doubler_sides"):
                selections[key] = {group: value for group, value in selections.get(key, {}).items() if group == "WEB"}
            current = service.run({"request_id": candidate["id"] + "-as-built",
                                   "mode": "checkSpecified", "inputs": current_inputs})
            atomic_json(Path(output) / "calculations" / f"{candidate['id']}.as-built.json", current)
            row["as_built_verified"] = current["verified"] and secondary_verified(current["dossier"])
            row["future_state_verified"] = verified
            verified = verified and row["as_built_verified"]
            row["future_main_cost"] = total
            row["future_canopy_frame_cost"] = cost["future_canopy_frame_cost"]
            current_costs = current["dossier"]["costing_summary"]["costs"]
            paired_costs = {key: max(value, current_costs.get(key, 0)) for key, value in cost["costs"].items()}
            total = sum(paired_costs.values())
            row["paired_main_costs"] = paired_costs
            result["paired_as_built_verification"] = current["provenance"]
            result["paired_main_cost"] = total
            if not verified:
                row["error"] = "Future canopy state passed; the main shed before canopy installation failed"
        row.update(status="completed", verified=verified, cost=total,
                   cost_per_m2=total / candidate["area_m2"],
                   truss_depth_mm=float(actual_geometry["truss_depth_mm"]),
                   main_truss_panel_count=selected_inputs.get("calculation_service", {}).get("main_truss_panel_count"),
                   ratio=result["checks"]["overall_governing"].get("ratio"),
                   sections=result["sizing"], costs=cost["costs"],
                   exact_solves=dossier.get("optimisation", {}).get("exact_solves"))
        atomic_json(Path(output) / "calculations" / f"{candidate['id']}.json", result)
    except Exception as exc:
        row.update(status="error", verified=False, error=f"{type(exc).__name__}: {exc}")
    row["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return row


def write_summary(output, manifest, rows):
    feasible = sorted((r for r in rows if r.get("verified") and r.get("cost", 0) > 0),
                      key=lambda r: (r["cost_per_m2"], r["cost"], r["id"]))
    by_region = {region: next((r for r in feasible if r["region"] == region), None)
                 for region in manifest["regions"]}
    summary = {"schema": SEARCH_SCHEMA, "planned": manifest["candidate_count"],
               "completed": len(rows), "errors": sum(r["status"] == "error" for r in rows),
               "passing": len(feasible), "best_by_region": by_region,
               "top_20": feasible[:20], "scope": manifest["scope"]}
    atomic_json(output / "summary.json", summary)
    fields = ["region", "open_side_faces", "width_m", "length_m", "bay_m", "bay_count",
              "area_m2", "verified", "cost", "cost_per_m2", "truss_depth_mm", "ratio",
              "elapsed_seconds", "status", "error", "id"]
    temp = output / "ranking.csv.tmp"
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(feasible + [r for r in rows if not r.get("verified")])
    temp.replace(output / "ranking.csv")
    for region, row in by_region.items():
        if row:
            result = json.loads((output / "calculations" / f"{row['id']}.json").read_text(encoding="utf-8"))
            atomic_json(output / f"best_{region}_preset.json", result["dossier"]["inputs"])
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", type=Path, default=ROOT / "presets" / "factory_defaults.json")
    parser.add_argument("--output", type=Path, default=ROOT / "hay_shed_results")
    parser.add_argument("--widths", default=",".join(str(w) for w in range(15, 31)))
    parser.add_argument("--area", type=float, default=840)
    parser.add_argument("--min-bay", type=float, default=5)
    parser.add_argument("--max-bay", type=float, default=10)
    parser.add_argument("--regions", default=",".join(REGIONS))
    parser.add_argument("--facings", default=",".join(DIRECTIONS),
                        help="Open-side compass headings or numeric true bearings, e.g. 63")
    parser.add_argument("--importance", type=int, choices=(1, 2, 3, 4), default=1)
    parser.add_argument("--terrain", type=float, choices=(1, 2, 2.5, 3, 4), default=2)
    parser.add_argument("--pitch", type=float, default=7.5)
    parser.add_argument("--solar-kpa", type=float, default=0,
                        help="Solar dead load; 0 for no solar, preset allowance is 0.15 kPa")
    parser.add_argument("--solar-side", choices=("Both", "Left", "Right"), default="Both")
    parser.add_argument("--future-canopy-m", type=float, default=0,
                        help="Cantilever on open side; rank main steel now, check before/after canopy")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, help="Run at most this many unfinished candidates (pilot)")
    parser.add_argument("--plan", action="store_true", help="List size and assumptions without calculating")
    args = parser.parse_args()
    if (not all(math.isfinite(v) and v > 0 for v in (args.area, args.min_bay, args.max_bay))
            or args.min_bay > args.max_bay or not math.isfinite(args.pitch)
            or not math.isfinite(args.solar_kpa) or args.solar_kpa < 0
            or not math.isfinite(args.future_canopy_m) or args.future_canopy_m < 0
            or not 0 <= args.pitch <= 45 or args.workers < 1
            or (args.limit is not None and args.limit < 1)):
        parser.error("Invalid area, bay limits, pitch, worker count or run limit")
    from portalcalc_service import calculation_source_provenance
    base = json.loads(args.preset.read_text(encoding="utf-8"))
    candidates = make_candidates(args, base)
    if not candidates:
        parser.error("No equal-bay layouts satisfy these constraints")
    provenance = calculation_source_provenance()
    for name in ("hay_shed_search.py", "hay_shed_backend.py", "future_canopy_design.py", "costing.py", "Shape_Catalog.xlsx",
                 "metroll_megaspan_capacity_tables.json", "reduced_optimizer.py",
                 "bracing.py", "end_wall.py", "storage_loads.py", "crane_beam.py"):
        provenance["source_file_sha256"][name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    manifest = {"schema": SEARCH_SCHEMA, "candidate_count": len(candidates),
                "regions": args.regions.split(","), "engine": provenance,
                "candidate_ids": [c["id"] for c in candidates],
                "scope": {"target_area_m2": args.area, "eave_height_m": 6,
                          "optimiser": "physics hybrid" if args.future_canopy_m else "physics",
                          "max_main_truss_depth": "span/20; checked again in saved geometry",
                          "roof_purlins": "Continuous lapped Z; tables 5A/5B; existing 15% lap allowance",
                          "wall_girts": "Single span C, including end-wall girts; tables 1A/1B",
                          "importance_level": args.importance, "terrain_category": args.terrain,
                          "roof_pitch_deg": args.pitch, "open_wall": "right long side",
                          "open_side_facings": args.facings.split(","),
                          "wind_envelope": "A0–A5 maximum of individually interpolated direction windows" if args.regions == "A-ALL" else args.regions,
                          "roof": "Gable truss, clear span, pinned bases",
                          "live_load_kpa": base["loads"]["q_kpa"],
                          "dead_load_kpa": base["loads"]["g_kpa"],
                          "solar_kpa": args.solar_kpa,
                          "solar_coverage": args.solar_side + " roof slopes, 0–100%",
                          "other_roof_services": "none", "rates": base.get("costing", {}),
                          "future_canopy_m": args.future_canopy_m,
                          "future_canopy": "Cantilever on open side, 5° pitch, 0.15 G / 0.25 Q kPa, no solar" if args.future_canopy_m else "none",
                          "price_scope": "Main shed now, future canopy frame purchase excluded" if args.future_canopy_m else "Main shed",
                          "secondary_steel": "Maximum cost per category across present/future states; secondary selections remain in each dossier" if args.future_canopy_m else "current model",
                          "objective": "structural-steel cost per actual covered m²",
                          "area_policy": "whole equal bays, millimetre rounding",
                          "excluded": "installed-price completeness, cladding, foundations, transport, erection, SHS cut nesting",
                          "optimality": "best found on listed width/bay/direction/region grid; member sizing is heuristic",
                          "shs_allowance": "existing engine 7% wastage"}}
    print(f"{len(candidates)} candidates; {len(set((c['width_m'], c['bay_count']) for c in candidates))} equal-bay layouts", flush=True)
    print(json.dumps(manifest["scope"], indent=2), flush=True)
    if args.plan:
        return
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "calculations").mkdir(exist_ok=True)
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        if digest(old) != digest(manifest):
            parser.error("Inputs, search grid or engine changed: use a new output directory")
    else:
        atomic_json(manifest_path, manifest)
    journal = output / "results.jsonl"
    rows = []
    if journal.exists():
        # A truncated final line from an interrupted write is safe to discard.
        lines = journal.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                if index != len(lines) - 1:
                    raise
        journal.write_text("".join(json.dumps(r, allow_nan=False) + "\n" for r in rows), encoding="utf-8")
    done = {r["id"] for r in rows}
    pending = [c for c in candidates if c["id"] not in done]
    if args.limit:
        pending = pending[:args.limit]
    print(f"{len(done)} saved; running {len(pending)} with {args.workers} process(es)", flush=True)
    iterator = iter(pending)
    interrupted = False
    with ProcessPoolExecutor(max_workers=args.workers, initializer=initialize_worker,
                             initargs=(str(output),)) as pool:
        futures = {}

        def submit_next():
            candidate = next(iterator, None)
            if candidate is not None:
                futures[pool.submit(calculate_candidate, candidate, str(output))] = candidate

        for _ in range(args.workers):
            submit_next()
        while futures:
            try:
                ready, _ = wait(futures, timeout=1, return_when=FIRST_COMPLETED)
            except KeyboardInterrupt:
                interrupted = True
                print("Stopping after active calculations; completed results will be saved.", flush=True)
                continue
            for future in ready:
                futures.pop(future)
                row = future.result()
                with journal.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, allow_nan=False) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                rows.append(row)
                write_summary(output, manifest, rows)
                detail = f"${row['cost']:,.0f}, ${row['cost_per_m2']:.2f}/m²" if "cost" in row else row.get("error")
                print(f"[{len(rows)}/{len(candidates)}] {row['region']} open {row['open_side_faces']} "
                      f"{row['width_m']:g}m / {row['bay_m']:g}m bays: "
                      f"passing={row['verified']} {detail} ({row['elapsed_seconds']:.1f}s)", flush=True)
                if not interrupted:
                    submit_next()
    summary = write_summary(output, manifest, rows)
    print(f"Saved {summary['completed']}/{summary['planned']}; {summary['passing']} passing. {output}", flush=True)


if __name__ == "__main__":
    main()
