"""Actionable sizing stops, with unchanged-input analysis retained for review."""
from bracing_analysis import AnalysisError


class SizingStopped(AnalysisError):
    def __init__(self, message, result, diagnostic):
        super().__init__(message)
        self.result = {**result, "sizing_failure": diagnostic}
        self.diagnostic = diagnostic


def describe_location(location, geometry):
    # The geometry schema exposes roof stations as a derived property, so use
    # the actual member x/span to name eave/ridge positions without raw IDs.
    x, span = location["x_mm"], geometry["span_mm"]
    face = "left eave" if abs(x) < 1e-4 else "right eave" if abs(x-span) < 1e-4 else "ridge" if abs(x-span/2) < 1e-4 else f"roof line {location['line']+1}"
    return f"Bay {location['bay']+1}, {face}"


def failure_record(option, checked):
    failures = [r for r in checked["case_checks"] if r["ratio"] is None or r["ratio"] > 1]
    # Keep one record per reason/component/state; many wind branches can have
    # the same missing datum. Prefer the largest physical demand for that row.
    records = {}
    for row in failures:
        kind = "missing_data" if row["ratio"] is None else "capacity"
        key = row["mode"], row.get("component"), kind
        force = row.get("axial_kn", row.get("tension_kn"))
        record = {"arrangement": option.arrangement, "section": option.name, "mode": row["mode"],
                  "case": row["case"], "component": row.get("component"), "state": kind,
                  "ratio": row["ratio"], "axial_kn": force}
        metric = abs(force) if force is not None else row["ratio"] or 0.
        previous = records.get(key)
        if previous is None or metric > previous[0]:
            records[key] = metric, record
    return [record for _, record in records.values()]


def stop_for_strut(initial_result, current_result, assembly, failures, floor_rejections):
    identifier = assembly.location["id"]
    label = describe_location(assembly.location, initial_result["analysis_layout"]["geometry"])
    # Diagnose the failed screening state, while retaining the original input
    # analysis for review; no locally changed sizing candidate is adopted.
    original = initial_result["section_checks"].get(identifier, {})
    evaluation = current_result["section_checks"].get(identifier, original)
    rows = evaluation.get("case_checks", [])
    primary = next((r for r in rows if r["ratio"] is None), None)
    primary = primary or max((r for r in rows if r["ratio"] is not None), key=lambda r:r["ratio"], default={})
    reason = primary.get("mode", "No candidate satisfies the current capacity/stiffness constraints")
    force = primary.get("axial_kn", primary.get("tension_kn"))
    if "tension rating required" in reason.lower():
        target, column = "purlin", 3
        title = "purlin tensile capacity Nt / source is missing or does not match"
        action = "Components / ratings > Purlin axial ratings: enter Declared Nt kN and its rating source. The adopted compression formula does not supply Nt."
    elif primary.get("method") == "coupled_purlin_beam":
        target, column = "beam", 1 if not primary.get("coupling_defined") else 3
        title = reason
        action = "Components / ratings > Sheeted-purlin beam: review the cladding/load-pickup inputs and member M+/M-/V ratings."
    else:
        target, column = "purlin", 7
        title = reason
        action = "Review the member's case checks and rejected alternatives below; revise the applicable ratings, arrangement or layout."
    if floor_rejections and not failures:
        title = "all strut alternatives are below the current SLS stiffness requirement"
        action = "Review global SLS movement, available strut sizes and the pinned bracing layout."
    if primary.get("ratio") is not None and primary["ratio"] > 1:
        title += f" (UC {primary['ratio']:.3f})"
    suffix = f" ({abs(force):.3f} kN {'tension' if force >= 0 else 'compression'})" if force is not None else ""
    diagnostic = {"status": "STOPPED_NO_ADOPTION", "member": identifier, "location": label,
                  "section": original.get("section", assembly.name), "reason": title,
                  "screening_section": evaluation.get("section", assembly.name),
                  "case": primary.get("case", "-"), "axial_kn": force, "action": action,
                  "input_target": target, "input_column": column,
                  "rating_key": assembly.location["purlin"]["basis_key"],
                  "rejected_alternatives": failures, "stiffness_floor_rejections": floor_rejections,
                  "retained_analysis": "Original input selections; no failed sizing candidate adopted"}
    return SizingStopped(f"{label}: {title}{suffix}", initial_result, diagnostic)


def diagnostic_text(diagnostic):
    d = diagnostic
    lines = ["AUTO-SIZING STOPPED - INPUT SELECTIONS RETAINED", f"Member: {d['location']} | {d['member']}",
             f"Section: {d['section']}", f"Reason: {d['reason']}", f"Source case: {d['case']}"]
    if d["axial_kn"] is not None:
        lines.append(f"Axial demand: {d['axial_kn']:.3f} kN (+ tension)")
    if d["screening_section"] != d["section"]:
        lines.append(f"Screening section at the stop: {d['screening_section']}")
    lines += [d["action"], "The results below are the original input selection's analysis, not an optimised design.", "", "REJECTED ALTERNATIVES"]
    grouped = {}
    for row in d["rejected_alternatives"]:
        key = row["arrangement"], row["mode"], row["state"]
        record = grouped.setdefault(key, {"sections": set(), "example": row})
        record["sections"].add(row["section"])
    for (arrangement, reason, state), record in grouped.items():
        row = record["example"]
        uc = "UNVERIFIED" if row["ratio"] is None else f"UC {row['ratio']:.3f}"
        lines.append(f"{arrangement} ({len(record['sections'])} section option(s)): {reason}; {uc}; {row['case']}")
    if d["stiffness_floor_rejections"]:
        lines.append(f"{d['stiffness_floor_rejections']} alternatives below the current SLS stiffness requirement.")
    return "\n".join(lines)
