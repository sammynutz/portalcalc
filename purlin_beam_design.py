"""Roof-run beam demands and scoped per-member checks for sheeted additions."""
import json
import math

from bracing_analysis import AnalysisError
from purlin_beam import cached_run
from purlin_combined import properties, compression_capacity, method


ARRANGEMENT = "sheeted_purlin"


def _positive(record, key):
    try:
        value = float(record.get(key) or 0)
    except (TypeError, ValueError):
        value = 0.
    if not math.isfinite(value) or value <= 0:
        raise AnalysisError("Enter a positive "+key.replace("_", " "))
    return value


def with_beam_demands(result, plan):
    """Solve every affected roof line, including adjoining bays without additions."""
    lines = sorted({a.location["line"] for a in plan.members.values() if a.arrangement == ARRANGEMENT})
    runs, by_member = {}, {}
    stations = result["analysis_layout"]["geometry"]["bay_stations_mm"]
    spans = [b-a for a, b in zip(stations, stations[1:])]
    for line in lines:
        assemblies = [plan.members[f"strut/bay/{i}/line/{line}"] for i in range(len(spans))]
        data = assemblies[0].location["purlin"]
        rating = data["axial_rating"]
        error = None
        try:
            if len({a.location["existing_count"] for a in assemblies}) != 1:
                raise AnalysisError("Sheeted beam model needs a consistent existing-purlin count along the run")
            if any(a.location["purlin"]["section"] != data["section"] for a in assemblies):
                raise AnalysisError("Variable roof-run profiles need an explicit run schedule")
            p = properties(data["section"], rating.get("purlin_shape", "Z"))
            if any(abs(a.location["purlin"]["area_mm2"]/p["area_mm2"]-1) > .005 or
                   a.location["purlin"]["axial_rating"].get("purlin_shape", "Z") != p["shape"] for a in assemblies):
                raise AnalysisError("Installed roof-run area/shape does not match the selected Metroll properties")
            if data.get("beam_inputs") is None:
                raise AnalysisError("Roof beam loading/span inputs are missing")
            inputs = data["beam_inputs"]
            span_type = {"Single span": "single", "Double span": "double", "Continuous lapped": "continuous_lapped"}.get(inputs["span_type"])
            if span_type is None:
                raise AnalysisError("Unknown installed purlin span configuration")
            if span_type == "continuous_lapped" and p["shape"] != "Z":
                raise AnalysisError("C sections cannot use the lapped roof-run model")
            release_text = str(rating.get("beam_roof_releases", "auto")).strip().lower()
            try:
                releases = None if release_text == "auto" else [] if release_text in ("", "none") else [int(v.strip()) for v in release_text.split(",")]
            except ValueError:
                raise AnalysisError("Roof releases must be auto, none, or comma-separated bay numbers") from None
            stiffness = rating.get("cladding_coupling_kn_m_per_mm")
            pickup = rating.get("beam_direct_roof_pickup_fraction")
            sourced = bool(str(rating.get("cladding_coupling_source", "")).strip())
            defined = stiffness is not None and pickup is not None and sourced
            k = float(stiffness) if stiffness is not None and sourced else 0.
            beta = float(pickup) if pickup is not None and sourced else 0.
            if not math.isfinite(k) or k < 0:
                raise AnalysisError("Cladding coupling must be finite and non-negative")
            if not math.isfinite(beta) or not 0 <= beta <= 1:
                raise AnalysisError("Direct roof-load pickup fraction must be between 0 and 1")
            # A line uses one cladding stiffness/basis. Refuse conflicting saved
            # per-bay entries instead of taking whichever appears first.
            for a in assemblies:
                r = a.location["purlin"]["axial_rating"]
                if (r.get("cladding_coupling_kn_m_per_mm"), r.get("cladding_coupling_source", ""), r.get("beam_direct_roof_pickup_fraction")) != (stiffness, rating.get("cladding_coupling_source", ""), pickup):
                    raise AnalysisError("Cladding coupling inputs differ along the same roof run")
                if str(r.get("beam_roof_releases", "auto")).strip().lower() != release_text or r.get("beam_lap_ratio", .15) != rating.get("beam_lap_ratio", .15):
                    raise AnalysisError("Continuity/lap inputs differ along the same roof run")
            own = p["area_mm2"]*7.85e-6*9.80665  # N/mm
            loads = [{"name": "ULS outward", "roof_n_mm": -inputs["outward_kn_m"], "added_n_mm": 0.},
                     {"name": "ULS inward", "roof_n_mm": inputs["inward_strength_kn_m"], "added_n_mm": 1.2*own},
                     {"name": "SLS outward", "roof_n_mm": -inputs["service_kn_m"], "added_n_mm": 0.},
                     {"name": "SLS inward G + wind envelope", "roof_n_mm": inputs["service_kn_m"]+inputs["permanent_kn_m"], "added_n_mm": own},
                     {"name": "SLS gravity G + Q", "roof_n_mm": inputs["gravity_service_kn_m"], "added_n_mm": own}]
            active_bays = [i for i, a in enumerate(assemblies) if a.arrangement == ARRANGEMENT]
            n = assemblies[0].location["existing_count"]
            for load in loads:
                load["roof_n_mm_by_bay"] = [load["roof_n_mm"]*(1-beta if i in active_bays else 1.) for i in range(len(spans))]
                load["added_n_mm_by_bay"] = [load["added_n_mm"]+load["roof_n_mm"]*n*beta if i in active_bays else 0. for i in range(len(spans))]
            payload = {"spans_mm": spans, "roof_i_mm4": p["web_ix_mm4"], "added_i_mm4": p["web_ix_mm4"],
                       "roof_count": assemblies[0].location["existing_count"],
                       "added_bays": active_bays,
                       "coupling": k, "span_type": span_type,
                       "roof_releases_after_bays": releases,
                       "lap_ratio": float(rating.get("beam_lap_ratio", .15)), "loads": loads}
            run = {**cached_run(json.dumps(payload, sort_keys=True, allow_nan=False)),
                   "line": line, "section_properties": p, "coupling_defined": bool(defined),
                   "direct_roof_pickup_fraction": beta,
                   "coupling_source": rating.get("cladding_coupling_source", ""),
                   "note": "Declared direct roof-load pickup is applied in added bays; finite cladding/fastener springs redistribute further load by relative deflection. Remaining bays retain full roof load. No rigid tie or 50/50 bending assumption."}
        except (AnalysisError, ValueError, TypeError) as problem:
            error = str(problem)
            run = {"line": line, "error": error, "cases": []}
        runs[str(line)] = run
        for bay, assembly in enumerate(assemblies):
            by_member[assembly.location["id"]] = {"line": str(line), "bay": bay, "error": error}
    return {**result, "purlin_beams": {"runs": runs, "members": by_member}}


def beam_check(result, identifier, component, force_kn, name, level, sls_divisor):
    """No UDL-table capacity is applied to redistributed moment diagrams.

    Entered member M+/M-/V capacities must cover the actual restraint/span/
    moment configuration. Without them demands remain visible but unverified.
    """
    entry = result.get("purlin_beams", {}).get("members", {}).get(identifier)
    if entry is None:
        return None
    data, existing = component["data"], component["existing"]
    run = result["purlin_beams"]["runs"][entry["line"]]
    base = {"case": name, "method": "coupled_purlin_beam", "section": data["section"],
            "axial_kn": force_kn, "beam_line": entry["line"], "beam_bay": entry["bay"],
            "beam_role": "roof" if existing else "added", "coupling_defined": run.get("coupling_defined", False)}
    if entry["error"]:
        return {**base, "mode": entry["error"], "ratio": None, "passes": False}
    role = base["beam_role"]
    envelopes = [(c["name"], c["bays"][entry["bay"]][role]) for c in run["cases"] if c["name"].startswith(level)]
    details = [{"case": case, **{k: v for k, v in d.items() if k != "samples"}} for case, d in envelopes]
    base.update(beam_demands=details, coupling_kn_m_per_mm=run["coupling_kn_m_per_mm"],
                source="Elastic roof-run beam demands; capacities entered for the actual member/restraint basis")
    if not run["coupling_defined"]:
        return {**base, "mode": "Cladding stiffness, direct pickup and source required; fallback demands shown", "ratio": None, "passes": False}
    if level == "SLS":
        demand = max(d["deflection_mm"] for _, d in envelopes)
        limit = data["analysis_span_mm"]/sls_divisor
        return {**base, "mode": "Roof-run beam local deflection", "ratio": demand/limit,
                "deflection_mm": demand, "limit_mm": limit}
    rating = data["axial_rating"]
    try:
        prefix = "beam_roof_" if existing else "beam_added_"
        mp = _positive(rating, prefix+"positive_moment_knm")
        mn = _positive(rating, prefix+"negative_moment_knm")
        v = _positive(rating, prefix+"shear_kn")
        if not str(rating.get("beam_capacity_source", "")).strip():
            raise AnalysisError("Beam member M/V capacity source is required for redistributed loading")
        if rating.get("beam_capacity_key") != data.get("bending_key"):
            raise AnalysisError("Beam member capacity basis changed; review M/V ratings")
        p = run["section_properties"]
        ns = compression_capacity(p["area_mm2"], p["web_rx_mm"], data["analysis_span_mm"])
        bending = max(max(d["positive_moment_knm"]/mp, d["negative_moment_knm"]/mn) for _, d in envelopes)
        shear = max(d["shear_kn"]/v for _, d in envelopes)
        if force_kn > 1e-8:
            nt = _positive(rating, "tension_kn" if existing else "beam_added_tension_kn")
            source = rating.get("source") if existing else rating.get("beam_capacity_source")
            if not str(source or "").strip():
                raise AnalysisError("Purlin tension source is required")
            axial = force_kn/nt
        else:
            nt, axial = None, max(-force_kn, 0.)/ns
        amplification = 1.
        if existing and method(rating) == "declared" and force_kn < -1e-8:
            ns = _positive(rating, "compression_kn")
            ne = _positive(rating, "elastic_buckling_kn")
            alpha = 1-max(-force_kn, 0.)/ne
            if alpha <= 0:
                raise AnalysisError("Roof purlin compression reaches the declared elastic buckling load")
            if not str(rating.get("source", "")).strip():
                raise AnalysisError("Declared roof purlin compression source is required")
            axial, amplification = max(-force_kn, 0.)/ns, 1/alpha
    except AnalysisError as error:
        return {**base, "mode": str(error)+"; beam demands solved", "ratio": None, "passes": False}
    # Conservative independent envelopes, retaining the adopted linear axial/
    # bending screen. Connections, biaxial flexure and code M/V interaction are
    # outside this scoped screen and are explicitly retained in the evidence.
    return {**base, "mode": "Beam M/V + adopted purlin axial interaction", "ratio": max(axial+bending*amplification, math.hypot(bending, shear)),
            "bending_ratio": bending, "shear_ratio": shear, "compression_capacity_kn": ns,
            "bending_amplification": amplification,
            "tension_capacity_kn": nt, "positive_moment_capacity_knm": mp,
            "negative_moment_capacity_knm": mn, "shear_capacity_kn": v,
            "capacity_source": rating["beam_capacity_source"],
            "basis": "Redistributed beam demands, entered member M+/M-/V ratings; Ns=903Ag/(L/rx')². max(N/Ns+bending, sqrt(bending²+shear²)); independent axial/bending envelopes. No inferred capacity from a UDL-table moment ratio. Full cold-formed/biaxial and connection acceptance remain separate."}
