"""Qt-free bridge from per-gridline building loads to the 2D frame checks.

Phase 4 of ``BUILDING_LOADS_3D_PLAN.md``. ``building_loads.py`` gives every
gridline a ``FrameLoadSet``. This module turns those into
``FrameLoadCombination`` objects that main.py's existing envelope machinery
checks like any other ``LoadCombination``. Each one carries the frame load
set it came from, so ``apply_combination_loads`` loads the 2D frame with that
gridline's loads instead of the single "frame anywhere" loads.

**Deduplication is per combination, not just per frame.** Two gridlines
share a combination when that combination sees identical loads on both:
- the same tributary and gravity, for gravity-only combinations;
- also the same wind case, for wind combinations.

Interior frames therefore share all their gravity and side-wind
combinations, and only the end-wind cases (whose bands vary along the
length) stay frame-specific.

Self weight, crane and storage are the same on every frame and still come
from the 2D model.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from loads import BasicRoofLoads, LoadCombination, RoofLoadPlacement, build_load_combinations
from wind import CPI_CASE_OPTIONS

GRAVITY_FIELDS = {"G": "g", "Q": "q", "Solar": "solar", "Fire service": "fire_service", "HVAC": "hvac", "Other": "other"}
PLACEMENT_LABELS = ("G", "Q", "Solar", "Other")


@dataclass(frozen=True)
class FrameLoadCombination(LoadCombination):
    """A load combination on the gridlines that share its loads.

    ``name`` is the template name with "@<gridlines>" appended, so envelope
    "combo" labels say where an action governs. ``base_name`` keeps the
    template name for logic that keys on it.
    """
    base_name: str = ""
    gridlines: tuple = ()
    frame_loads: Any = field(default=None, compare=False, repr=False)

    @property
    def frame_id(self) -> Optional[str]:
        return self.gridlines[0] if self.gridlines else None


def gridline_label(gridlines) -> str:
    """'GL3', 'GL2–GL5' for a consecutive run, else 'GL1,GL6'."""
    ids = list(gridlines)
    if len(ids) == 1:
        return ids[0]
    numbers = [_trailing_number(g) for g in ids]
    prefix = ids[0][:len(ids[0]) - len(str(numbers[0]))] if numbers[0] is not None else None
    if (None not in numbers and prefix is not None and all(g.startswith(prefix) for g in ids)
            and numbers == list(range(numbers[0], numbers[0] + len(numbers)))):
        return f"{ids[0]}–{ids[-1]}"
    return ",".join(ids)


def _trailing_number(text):
    digits = ""
    for ch in reversed(text):
        if not ch.isdigit():
            break
        digits = ch + digits
    return int(digits) if digits else None


def _canonical(value):
    """JSON-ready copy with floats rounded to 12 significant figures."""
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, float):
        return 0.0 if value == 0.0 else float(f"{value:.12g}")
    return value


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(_canonical(value), sort_keys=True).encode()).hexdigest()


def gravity_signature(frame) -> str:
    return _digest({"tributary_mm": frame.tributary_mm, "gravity": [g.to_dict() for g in frame.gravity],
                    "attachments": {side: [g.to_dict() for g in loads]
                                    for side, loads in (frame.attachment_gravity or {}).items()},
                    "attachment_tributary_mm": dict(getattr(frame, "attachment_tributary_mm", {}) or {})})


def wind_signature(frame, cpe_case: str) -> str:
    case = frame.wind_case(cpe_case).to_dict()
    case.pop("source_direction", None)  # a label; the pressures it implies are in the case
    case.pop("factors", None)           # evidence only; the factors are already in the line loads
    return _digest(case)


def wind_case_names(frames) -> list:
    names = []
    for case in frames[0].wind:
        if case.cpe_case not in names:
            names.append(case.cpe_case)
    return names


def frame_load_combinations(frames, gridlines=None) -> list:
    """Deduplicated ``FrameLoadCombination`` list for the given frames.

    ``frames`` are ``building_loads.FrameLoadSet`` objects. ``gridlines``
    optionally restricts them to one sizing group.
    """
    if gridlines is not None:
        wanted = set(gridlines)
        frames = [f for f in frames if f.gridline in wanted]
    if not frames:
        raise ValueError("no frames to combine")
    templates = build_load_combinations(wind_case_names(frames), CPI_CASE_OPTIONS)
    gravity_keys = {f.gridline: gravity_signature(f) for f in frames}
    wind_keys = {(f.gridline, c.cpe_case): wind_signature(f, c.cpe_case) for f in frames for c in f.wind}
    combinations = []
    for template in templates:
        groups: dict = {}
        for frame in frames:
            key = gravity_keys[frame.gridline]
            if template.includes_wind:
                key = (key, wind_keys[(frame.gridline, template.cpe_case)])
            groups.setdefault(key, []).append(frame)
        for members in groups.values():
            ids = tuple(f.gridline for f in members)
            combinations.append(FrameLoadCombination(
                name=f"{template.name} @{gridline_label(ids)}",
                g_factor=template.g_factor, q_factor=template.q_factor,
                crane_position=template.crane_position, cpe_case=template.cpe_case,
                cpi_case=template.cpi_case, wind_pressure=template.wind_pressure,
                serviceability_component=template.serviceability_component,
                base_name=template.name, gridlines=ids, frame_loads=members[0]))
    return combinations


def distinct_frames(frames) -> list:
    """Groups of gridlines whose complete load sets are identical."""
    groups: dict = {}
    for frame in frames:
        key = (gravity_signature(frame),) + tuple(wind_signature(frame, c.cpe_case) for c in frame.wind)
        groups.setdefault(key, []).append(frame.gridline)
    return list(groups.values())


def frame_roof_loads(frame) -> BasicRoofLoads:
    """The frame's roof gravity as main.py's ``BasicRoofLoads`` (tributary = bay size)."""
    values = {name: 0.0 for name in GRAVITY_FIELDS.values()}
    placements = {}
    for load in frame.gravity:
        values[GRAVITY_FIELDS[load.label]] = load.kpa
        if load.label in PLACEMENT_LABELS:
            placements[load.label] = RoofLoadPlacement(load.side, load.from_percent, load.to_percent)
    for label in PLACEMENT_LABELS:
        placements.setdefault(label, RoofLoadPlacement())
    return BasicRoofLoads(bay_size_m=frame.tributary_m, placements=placements, **values)


def frame_attachment_loads(frame, side) -> BasicRoofLoads:
    """A canopy/lean-to's G/Q/solar on this frame (tributary = bay size), as main.py's canopy loads."""
    values = {"g": 0.0, "q": 0.0, "solar": 0.0}
    for load in (frame.attachment_gravity or {}).get(side, ()):
        values[{"G": "g", "Q": "q", "Solar": "solar"}[load.label]] = load.kpa
    tributary_mm = (getattr(frame, "attachment_tributary_mm", None) or {}).get(side, frame.tributary_mm)
    return BasicRoofLoads(bay_size_m=tributary_mm / 1000.0, **values)


def roof_strips_on_nodes(case, nodes, canopy_bounds=None) -> list:
    """Strips (from_m, to_m, kN/m) along the actual roof node path for ``case``.

    "bands" strips are used as they are (main.py's nominal strip overlap).
    "slopes" splits at the actual apex node and "uniform" covers the whole
    path, as main.py does for >=10 degree gables, free roofs and end wind.

    ``canopy_bounds`` {side: (from_m, to_m)} marks the canopy/lean-to parts of
    the path. There, a part-length attachment's strips are scaled to its own
    tributary, or replaced by its end-wind value.
    """
    distances = [0.0]
    for n1, n2 in zip(nodes[:-1], nodes[1:]):
        distances.append(distances[-1] + math.hypot((n2.x - n1.x) / 1000.0, (n2.y - n1.y) / 1000.0))
    total = distances[-1]
    if case.roof_layout == "bands":
        strips = [tuple(strip) for strip in case.roof_strips]
    elif case.roof_layout == "uniform":
        strips = [(0.0, total, case.roof_strips[0][2])]
    else:
        apex = max(range(len(nodes)), key=lambda i: nodes[i].y)
        ridge = distances[apex]
        halves = ((0.0, ridge, case.roof_strips[0][2]), (ridge, total, case.roof_strips[1][2]))
        strips = [(a, b, q) for a, b, q in halves if b - a > 1e-9]
    scales = getattr(case, "canopy_roof_scale", None) or {}
    overrides = getattr(case, "canopy_roof_kn_m", None) or {}
    for side, (lo, hi) in (canopy_bounds or {}).items():
        if side in overrides:
            strips = _replace_interval(strips, lo, hi, lambda q, v=overrides[side]: v, (lo, hi, overrides[side]))
        elif side in scales:
            strips = _replace_interval(strips, lo, hi, lambda q, r=scales[side]: q * r)
    return strips


def _replace_interval(strips, lo, hi, transform, fill=None):
    """Split strips at [lo, hi] and transform the values inside (or fill it with one strip)."""
    out = []
    for a, b, q in strips:
        for s0, s1, inside in ((a, min(b, lo), False), (max(a, lo), min(b, hi), True), (max(a, hi), b, False)):
            if s1 - s0 > 1e-12:
                out.append((s0, s1, transform(q) if inside else q))
    if fill is not None:
        out = [s for s in out if not (s[0] >= lo - 1e-12 and s[1] <= hi + 1e-12)] + [fill]
    return sorted(out, key=lambda s: s[0])


def envelope_combo_labels(actions) -> set:
    """Every combination label recorded anywhere in a design envelope."""
    labels = set()

    def walk(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, str(k))
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v, key)
        elif isinstance(value, str) and "combo" in key and value not in ("-", ""):
            labels.add(value)

    walk(actions)
    return labels


def governing_combination_names(actions, names) -> set:
    """Names (from ``names``) of the combinations that govern any enveloped action.

    Envelope labels may carry a " [storage case]" suffix; it is stripped.
    """
    names = set(names)
    governing = set()
    for label in envelope_combo_labels(actions):
        if label in names:
            governing.add(label)
        elif " [" in label and label.rsplit(" [", 1)[0] in names:
            governing.add(label.rsplit(" [", 1)[0])
    return governing


def governing_actions(actions, gridlines_by_name) -> list:
    """Every enveloped action with its value, governing combination and gridlines.

    ``gridlines_by_name`` maps combination names to their gridlines; envelope
    labels with a " [storage case]" suffix are matched on the base name.
    """
    found = []

    def walk(value, path):
        if isinstance(value, dict):
            combo = value.get("combo")
            number = value.get("value")
            if (isinstance(combo, str) and combo not in ("", "-") and isinstance(number, (int, float))
                    and not isinstance(number, bool) and math.isfinite(float(number))):
                name = combo if combo in gridlines_by_name else combo.rsplit(" [", 1)[0]
                found.append({"path": path.lstrip("."), "value": float(number), "combo": combo,
                              "gridlines": list(gridlines_by_name.get(name, []))})
            for key, item in value.items():
                if key not in ("displacements",):
                    walk(item, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(actions or {}, "")
    return found


MEMBER_GROUPS = ("LEFT_COLUMN", "RIGHT_COLUMN", "INTERNAL_COLUMN", "TOP", "BOTTOM", "WEB")
ACTIONS = (("compression_kn", "kN"), ("tension_kn", "kN"), ("shear_kn", "kN"), ("moment_knm", "kNm"))


def governing_report(actions) -> list:
    """Which gridline and case govern each member action."""
    lines = ["Governing gridlines and cases (building loads):"]
    for group in MEMBER_GROUPS:
        envelope = (actions or {}).get(group) or {}
        parts = []
        for key, unit in ACTIONS:
            item = envelope.get(key) or {}
            if item.get("combo") in (None, "-") or not item.get("value"):
                continue
            parts.append(f"{key.split('_')[0]} {float(item['value']):.1f} {unit} [{item['combo']}]")
        if parts:
            lines.append(f"  {group}: " + "; ".join(parts))
    return lines


def summary_lines(combination) -> list:
    frame = combination.frame_loads
    lines = ["", f"BUILDING LOADS: {combination.base_name}",
             f"Gridlines        : {gridline_label(combination.gridlines)} ({frame.basis} basis, Kc {frame.kc_rule})",
             f"Tributary width  : {frame.tributary_m:.3f} m"]
    if combination.includes_wind:
        case = frame.wind_case(combination.cpe_case)
        lines += [f"Wind case        : {case.cpe_case} with {combination.cpi_case} ({combination.wind_pressure})",
                  f"Source direction : {case.source_direction}",
                  f"Wu / Ws          : {case.wu_kn_m2:.3f} / {case.ws_kn_m2:.3f} kPa",
                  f"Main chamber Cpi : {case.cpi[combination.cpi_case]:+.3f}",
                  f"Factors          : {case.factors}"]
    return lines
