"""Small first-order roof-run beam model with finite cladding coupling.

N, mm, MPa. Existing roof continuity and added single-span rotations remain
independent. Coupling is an entered distributed stiffness, NOT a diaphragm or
an assumed rigid tie. Capacity acceptance is separate from solved demands.
"""
from functools import lru_cache
import json
import math

import numpy as np
from numpy.polynomial import Polynomial as P

from bracing_analysis import AnalysisError


SHAPES = (P([1, 0, -3, 2]), P([0, 1, -2, 1]), P([0, 0, 3, -2]), P([0, 0, -1, 1]))


def _beam(ei, length):
    l = length
    return ei/l**3*np.array([[12,6*l,-12,6*l], [6*l,4*l*l,-6*l,2*l*l],
                             [-12,-6*l,12,-6*l], [6*l,2*l*l,-6*l,4*l*l]])


def _shape(length):
    return (SHAPES[0], SHAPES[1]*length, SHAPES[2], SHAPES[3]*length)


def _integral(poly):
    return float(poly.integ()(1)-poly.integ()(0))


def _roots(poly):
    return [float(r.real) for r in poly.roots() if abs(r.imag) < 1e-7 and 0 < r.real < 1]


def _q(load, role, bay):
    values = load.get(role+"_n_mm_by_bay")
    return float(values[bay] if values is not None else load[role+"_n_mm"])


def solve_run(spans_mm, roof_i_mm4, added_i_mm4, roof_count=1, added_bays=(),
              coupling=0., span_type="single", lap_ratio=.15, loads=(), divisions=12,
              roof_releases_after_bays=None):
    """One equivalent roof run plus independent added members in selected bays.

    Each load record is {name, roof_n_mm [per existing member], added_n_mm}.
    Coupling units kN/m per mm relative deflection = N/mm2. Its value applies
    to each added line against the equivalent existing run. Loads enter the
    original roof line, with transfer to the added member through the springs.
    """
    spans = tuple(float(s) for s in spans_mm)
    if not spans or any(not math.isfinite(s) or s <= 0 for s in spans):
        raise AnalysisError("Positive roof-run spans are required")
    if len(spans) > 20 or not 4 <= divisions <= 48:
        raise AnalysisError("Roof beam model supports 20 bays and 4-48 elements per bay")
    if span_type not in {"single", "double", "continuous_lapped"}:
        raise AnalysisError("Unknown roof-run continuity")
    releases = set(roof_releases_after_bays if roof_releases_after_bays is not None else
                   range(1, len(spans)) if span_type == "single" else
                   range(2, len(spans), 2) if span_type == "double" else ())
    if any(isinstance(b, bool) or not isinstance(b, int) or not 1 <= b < len(spans) for b in releases):
        raise AnalysisError("Roof rotation releases must be interior bay numbers (1-based)")
    if any(not math.isfinite(v) or v <= 0 for v in (roof_i_mm4, added_i_mm4)):
        raise AnalysisError("Positive web-axis I is required for both beam lines")
    if isinstance(roof_count, bool) or not isinstance(roof_count, int) or not 1 <= roof_count <= 8:
        raise AnalysisError("Existing roof count must be an integer from 1 to 8")
    if not math.isfinite(coupling) or coupling < 0:
        raise AnalysisError("Cladding coupling must be finite and non-negative")
    if span_type == "continuous_lapped" and (not math.isfinite(lap_ratio) or not .15 <= lap_ratio <= .5):
        raise AnalysisError("Lapped roof-run lap/span ratio must be between 0.15 and 0.5")
    added = set(added_bays)
    if any(not isinstance(b, int) or not 0 <= b < len(spans) for b in added):
        raise AnalysisError("Invalid added-purlin bay")
    if coupling and added:
        transfer_length = (200000*min(roof_i_mm4*roof_count, added_i_mm4)/coupling)**.25
        required = math.ceil(2*max(spans[b] for b in added)/transfer_length)
        if required > 48:
            raise AnalysisError("Cladding stiffness requires a finer beam mesh than this lightweight model supports")
        divisions = max(divisions, required)
    if not loads:
        raise AnalysisError("At least one roof beam load case is required")
    for load in loads:
        if any(values is not None and len(values) != len(spans) for values in (load.get("roof_n_mm_by_bay"), load.get("added_n_mm_by_bay"))):
            raise AnalysisError("Per-bay roof loads must cover the entire run")
        if any(not math.isfinite(_q(load, role, bay)) for role in ("roof", "added") for bay in range(len(spans))):
            raise AnalysisError("Roof beam loads must be finite")
    dofs, fixed, elements = {}, set(), []
    def dof(key):
        if key not in dofs:
            dofs[key] = len(dofs)
        return dofs[key]
    offset = 0.
    for bay, length in enumerate(spans):
        stations = {round(i/divisions, 12) for i in range(divisions+1)}
        if span_type == "continuous_lapped":
            # Equal-section lap stiffness = sum of ply EIs; no composite I.
            if bay > 0 and bay not in releases:
                stations.add(lap_ratio/2)
            if bay+1 < len(spans) and bay+1 not in releases:
                stations.add(1-lap_ratio/2)
        stations = sorted(stations)
        group = sum(b <= bay for b in releases)
        def roof_node(t):
            x = round(offset+t*length, 6)
            w, theta = dof(("roof_w", x)), dof(("roof_theta", group, x))
            if t in (0., 1.):
                fixed.add(w)
            return w, theta
        def added_node(t):
            w, theta = dof(("added_w", bay, t)), dof(("added_theta", bay, t))
            if t in (0., 1.):
                fixed.add(w)
            return w, theta
        for a, b in zip(stations, stations[1:]):
            midpoint = (a+b)/2
            lap = span_type == "continuous_lapped" and ((bay > 0 and bay not in releases and midpoint < lap_ratio/2) or
                   (bay+1 < len(spans) and bay+1 not in releases and midpoint > 1-lap_ratio/2))
            factor = roof_count*(2 if lap else 1)
            ids = (*roof_node(a), *roof_node(b))
            extra = (*added_node(a), *added_node(b)) if bay in added else None
            elements.append({"bay": bay, "x": offset+a*length, "length": (b-a)*length,
                             "roof": ids, "added": extra, "roof_plies": factor,
                             "roof_ei": 200000*roof_i_mm4*factor, "added_ei": 200000*added_i_mm4})
        offset += length
    size = len(dofs)
    if size > 1600:
        raise AnalysisError("Roof beam model exceeds 1600 DOFs")
    matrix = np.zeros((size, size))
    rhs = np.zeros((size, len(loads)))
    gauss, weights = np.polynomial.legendre.leggauss(4)
    for e in elements:
        length = e["length"]
        shape = _shape(length)
        for role, ei, count in (("roof", e["roof_ei"], roof_count), ("added", e["added_ei"], 1)):
            ids = e[role]
            if ids is None:
                continue
            matrix[np.ix_(ids, ids)] += _beam(ei, length)
            unit = length*np.array([_integral(n) for n in shape])
            for j, load in enumerate(loads):
                rhs[list(ids), j] += unit*_q(load, role, e["bay"])*count
        if e["added"] is not None and coupling:
            ids = (*e["roof"], *e["added"])
            for t, weight in zip((gauss+1)/2, weights/2):
                n = np.array([s(t) for s in shape])
                v = np.concatenate((n, -n))
                matrix[np.ix_(ids, ids)] += coupling*length*weight*np.outer(v, v)
    free = np.array(sorted(set(range(size))-fixed), dtype=int)
    reduced = matrix[np.ix_(free, free)]
    scale = np.sqrt(np.diag(reduced))
    try:
        solved = np.linalg.solve(reduced/np.outer(scale, scale), rhs[free]/scale[:, None])/scale[:, None]
    except np.linalg.LinAlgError:
        raise AnalysisError("Roof beam stiffness is singular") from None
    displacement = np.zeros_like(rhs)
    displacement[free] = solved
    residual = matrix@displacement-rhs
    cases = []
    for j, load in enumerate(loads):
        error = float(np.max(np.abs(residual[free, j]), initial=0))
        if error > max(1e-4, float(np.max(np.abs(rhs[:, j]), initial=0))*1e-7):
            raise AnalysisError("Roof beam equilibrium did not converge")
        bays = [{"roof": [], "added": [], "transfer_kn": 0., "peak_transfer_kn_m": 0., "transfer_samples": []} for _ in spans]
        for e in elements:
            length, shape = e["length"], _shape(e["length"])
            roof_w = sum((s*d for s, d in zip(shape, displacement[list(e["roof"]), j])), P([0.]))
            added_w = sum((s*d for s, d in zip(shape, displacement[list(e["added"]), j])), P([0.])) if e["added"] else P([0.])
            transfer = (roof_w-added_w)*coupling if e["added"] else P([0.])
            bay = bays[e["bay"]]
            bay["transfer_kn"] += _integral(transfer)*length/1000
            ts = [0., 1., *_roots(transfer.deriv())]
            bay["transfer_samples"].extend({"x_mm": e["x"]+t*length, "transfer_kn_m": float(transfer(t))} for t in sorted(set(ts+[.25, .5, .75])))
            bay["peak_transfer_kn_m"] = max(bay["peak_transfer_kn_m"], *(abs(float(transfer(t))) for t in ts))
            for role, ei, count in (("roof", e["roof_ei"], roof_count), ("added", e["added_ei"], 1)):
                ids = e[role]
                if ids is None:
                    continue
                q = P([_q(load, role, e["bay"])*count]) + transfer*(-1 if role == "roof" else 1)
                equivalent = length*np.array([_integral(s*q) for s in shape])
                ends = _beam(ei, length)@displacement[list(ids), j]-equivalent
                shear = P([-ends[0]])-q.integ()*length
                moment = P([ends[1], -ends[0]*length])-q.integ(2)*length**2
                # Exact local particular solution for the recovered distributed
                # polynomial load; zero end displacement/slope correction.
                particular = q.integ(4)*length**4/ei
                f1, fp1 = particular(1), particular.deriv()(1)
                particular += P([0, 0, -3*f1+fp1, 2*f1-fp1])
                w = sum((s*d for s, d in zip(shape, displacement[list(ids), j])), P([0.]))+particular
                positions = sorted({0., .25, .5, .75, 1., *_roots(shear), *_roots(q), *_roots(w.deriv())})
                factor = e["roof_plies"] if role == "roof" else 1
                for t in positions:
                    bay[role].append({"x_mm": e["x"]+t*length, "moment_knm": float(moment(t))/factor/1e6,
                                      "shear_kn": float(shear(t))/factor/1000, "deflection_mm": float(w(t)),
                                      "plies": factor})
        for bay in bays:
            for role in ("roof", "added"):
                samples = bay[role]
                bay[role] = {"samples": samples,
                             "positive_moment_knm": max((max(s["moment_knm"], 0.) for s in samples), default=0.),
                             "negative_moment_knm": max((max(-s["moment_knm"], 0.) for s in samples), default=0.),
                             "shear_kn": max((abs(s["shear_kn"]) for s in samples), default=0.),
                             "deflection_mm": max((abs(s["deflection_mm"]) for s in samples), default=0.)}
        vertical = [i for key, i in dofs.items() if key[0].endswith("_w")]
        applied = float(np.sum(rhs[vertical, j]))
        reaction = float(np.sum(residual[list(set(vertical)&fixed), j]))
        cases.append({"name": load["name"], "loads": dict(load), "bays": bays,
                      "free_dof_residual": error, "applied_load_n": applied,
                      "support_reaction_n": reaction, "vertical_balance_error_n": applied+reaction})
    return {"schema": "portalcalc.purlin-beam.v1", "spans_mm": spans, "span_type": span_type,
            "roof_releases_after_bays": sorted(releases),
            "lap_ratio": lap_ratio if span_type == "continuous_lapped" else None,
            "roof_count": roof_count, "added_bays": sorted(added), "coupling_kn_m_per_mm": coupling,
            "divisions_per_bay": divisions, "free_dofs": len(free), "cases": cases,
            "basis": "First-order Euler-Bernoulli beams; gross web-axis EI; finite distributed cladding/fastener springs. Existing roof rotation continuity retained; each added member simply supported. Lap EI is sum of ply EIs. Cladding stiffness is a declared input; connection capacity unchecked."}


@lru_cache(maxsize=24)
def cached_run(payload):
    return solve_run(**json.loads(payload))
