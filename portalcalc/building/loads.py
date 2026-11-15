"""Qt-free building load engine: per-gridline frame load sets from a 3D building.

Phases 2-3 and 7a of ``BUILDING_LOADS_3D_PLAN.md``: roof gravity and wind
from all four directions. It covers the main building plus full-length
canopies and lean-tos. Each gridline gets a ``FrameLoadSet`` of load
*descriptors* in the frame's own terms:
- roof strips along the continuous roof;
- canopy member lines;
- column line loads;
- Cpi coefficients.

These are the quantities main.py's 2D application consumes, so a frame can be
loaded exactly as it is today.

Two bases:

``legacy_2d``
    Today's 2D "frame anywhere" cases (Left/Right/End), scaled by each
    frame's tributary width. Coefficients come from the nominal bay exactly
    as main.py derives them:
    - the worst bay window of end-wind roof Cpe, applied uniformly;
    - the first-bay side-wall Cpe;
    - the larger-Md end when both ends are clad;
    - the lean-to and canopy chamber rules.

    An interior frame reproduces the 2D engine exactly. This basis exists for
    parity and comparison.

``building``
    Loads applied to the 3D building and resolved onto each gridline:
    - **Side wind (Left/Right):** each frame's own tributary and Ka.
    - **End wind (Front/Back), each direction with its own Md.**
      - Table 5.3(A) roof bands at every pitch, with d = building length
        (D4), applied across the continuous roof including canopies.
      - Table 5.2(C) bands on the main and lean-to side walls.
      - Both are measured from the true windward end and integrated onto
        each frame with simply supported bay shape functions.
    - **Cpi per chamber and direction** (main, connected main + lean-to,
      lean-to) from the open wall segments (Table 5.1(B)).
      - Each opening takes the Cpe of where it actually is: windward 0.7;
        leeward by d/b; side faces averaged over their Table 5.2(C) bands
        from the windward edge.
      - An open canopy's underside follows its open side. For end wind that
        uses this frame's side-wall band average, as the 3D bracing does.
    - **Ka:** from frame tributary areas.
    - **Kc:** ``kc_rule``. The default ``surface_count`` counts the effective
      surfaces on the frame (roof, each external clad column line, and
      internal pressure when |Cpi| ≥ 0.4) and maps ≥3 → 0.8, 2 → 0.9,
      1 → 1.0.

Doors and openings are closed (D7).

Not yet supported, raising ``BuildingLoadsScopeError``:
- part-length or offset attachments (they need several frame types);
- skillion roofs with attachments;
- partitions.

Sign conventions match main.py:
- roof, canopy and Cpi line loads are positive outward (uplift), normal to
  the roof;
- column line loads are signed global +X;
- gravity kPa is negative downward on horizontal projection.

Wind line loads are ultimate; multiply by ``sls_scale`` for serviceability.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from portalcalc.bracing.roof_loads import frame_strip_integrals
from portalcalc.building.model import MAIN_SIDE_WALLS, MAIN_WALLS, BuildingModel
from portalcalc.loads import RoofLoadPlacement
from portalcalc.service.contract import calculate_snow_loads
from portalcalc.wind import (CPI_BY_FRAME_TYPE, CPI_CASE_OPTIONS, SIDE_WALL_CPE_BANDS, WindInputs, action_combination_factors,
                  area_reduction_factor, calculate_wind, cyclonic_enclosed_cpi, free_roof_cpn, leeward_wall_cpe,
                  low_pitch_cpe_values, open_canopy_cpi, opening_area_reduction_factor,
                  symmetric_end_source_direction, transverse_free_roof_cpn, wall_cpe_for_surface,
                  wall_surface_for_cpe_case)

SCHEMA = "portalcalc.building-loads.v1"
BASES = ("legacy_2d", "building")
KC_RULES = ("legacy", "surface_count")
SIGNS = ("+", "-")
OPPOSITE = {"left": "right", "right": "left", "front": "back", "back": "front"}
GRAVITY_COMPONENTS = (
    # label, preset kPa key (None = Q/snow), placement key (None = full roof), factor
    ("G", "g_kpa", "g", "G"),
    ("Q", None, "q", "Q"),
    ("Solar", "solar_kpa", "solar", "G"),
    ("Fire service", "fire_service_kpa", None, "G"),
    ("HVAC", "hvac_kpa", None, "G"),
    ("Other", "other_kpa", "other", "G"),
)
ATTACHMENT_GRAVITY = (("G", "g_kpa", "G"), ("Q", "q_kpa", "Q"), ("Solar", "solar_kpa", "G"))


class BuildingLoadsScopeError(ValueError):
    """The building uses a feature this load engine does not handle yet."""


# --------------------------------------------------------------- results

@dataclass(frozen=True)
class GravityLoad:
    """A roof area load on horizontal projection; line load = kpa × tributary."""
    label: str
    factor: str            # "G" or "Q": which combination factor applies
    kpa: float             # negative = downward
    side: str = "Both"
    from_percent: float = 0.0
    to_percent: float = 100.0

    def placement(self) -> RoofLoadPlacement:
        return RoofLoadPlacement(self.side, self.from_percent, self.to_percent)

    def to_dict(self) -> dict:
        return {"label": self.label, "factor": self.factor, "kpa": self.kpa, "side": self.side,
                "from_percent": self.from_percent, "to_percent": self.to_percent}


@dataclass(frozen=True)
class WindCase:
    """One external pressure case on one frame, with the Cpi that goes with it.

    ``cpe_case`` names the case (Left/Right/End/Front/Back ±); ``legacy_cpe_case``
    is the 2D name it corresponds to (Front and Back both map to End).

    ``roof_strips`` run along the continuous roof (left canopy tip, main roof,
    right canopy tip) unless ``roof_basis`` is "free_roof_cpn" (main roof only).
    ``roof_layout`` says how they are applied to the frame's panels:

    - "bands": by overlap with each panel's distance along the actual roof
      from the left end (main.py's strip application);
    - "slopes": (left slope, right slope) values, split at the actual apex
      node;
    - "uniform": one value on every roof panel.

    Column keys are "left"/"right" (main columns) and "left_lean"/"right_lean"
    (lean-to outer columns).
    """
    wind_from: str
    cpe_case: str
    legacy_cpe_case: str
    source_direction: object
    wu_kn_m2: float
    ws_kn_m2: float
    roof_basis: str                 # "cpe" or "free_roof_cpn" (fully open, net coefficient)
    roof_layout: str
    roof_length_m: float
    roof_strips: tuple              # ((from_m, to_m, kn_m outward), ...)
    wall_cpe_kn_m: dict             # {column: signed +X kN/m}
    cpi: dict                       # {"Cpi +"|"Cpi -": main chamber coefficient}
    roof_cpi_kn_m: dict             # {cpi case: outward kN/m on the main roof}; empty for a free roof
    wall_cpi_kn_m: dict             # {cpi case: {column: signed +X kN/m}}
    factors: dict                   # Kc/Ka evidence for this frame and case
    canopy_cpe_kn_m: dict = field(default_factory=dict)   # {side: outward kN/m} free-roof canopy net Cpn
    canopy_cpi_kn_m: dict = field(default_factory=dict)   # {cpi case: {side: outward kN/m}} canopy/lean-to underside
    # Canopy/lean-to roof panels on the continuous roof: scale the strip value by
    # (attachment tributary / frame tributary), or use an explicit end-wind value.
    canopy_roof_scale: dict = field(default_factory=dict)  # {side: ratio}, only where != 1
    canopy_roof_kn_m: dict = field(default_factory=dict)   # {side: outward kN/m} replacing the strips

    @property
    def sls_scale(self) -> float:
        return 0.0 if abs(self.wu_kn_m2) < 1e-12 else self.ws_kn_m2 / self.wu_kn_m2

    def roof_value_at(self, s_m: float, ridge_m: float | None = None) -> float:
        """Outward roof line load at slope distance ``s_m`` (nominal geometry)."""
        if self.roof_layout == "uniform":
            return self.roof_strips[0][2]
        if self.roof_layout == "slopes":
            ridge = self.roof_length_m / 2.0 if ridge_m is None else ridge_m
            return self.roof_strips[0][2] if s_m <= ridge else self.roof_strips[1][2]
        return sum(q for a, b, q in self.roof_strips if a <= s_m < b)

    def roof_resultant_kn(self) -> float:
        """Integral of the outward roof line load over the nominal roof slope."""
        if self.roof_layout == "uniform":
            return self.roof_strips[0][2] * self.roof_length_m
        return sum((b - a) * q for a, b, q in self.roof_strips)

    def to_dict(self) -> dict:
        return {"wind_from": self.wind_from, "cpe_case": self.cpe_case, "legacy_cpe_case": self.legacy_cpe_case,
                "source_direction": self.source_direction, "wu_kn_m2": self.wu_kn_m2,
                "ws_kn_m2": self.ws_kn_m2, "roof_basis": self.roof_basis, "roof_layout": self.roof_layout,
                "roof_length_m": self.roof_length_m,
                "roof_strips": [list(strip) for strip in self.roof_strips],
                "wall_cpe_kn_m": dict(self.wall_cpe_kn_m), "cpi": dict(self.cpi),
                "roof_cpi_kn_m": dict(self.roof_cpi_kn_m),
                "wall_cpi_kn_m": {case: dict(lines) for case, lines in self.wall_cpi_kn_m.items()},
                "canopy_cpe_kn_m": dict(self.canopy_cpe_kn_m),
                "canopy_cpi_kn_m": {case: dict(lines) for case, lines in self.canopy_cpi_kn_m.items()},
                "canopy_roof_scale": dict(self.canopy_roof_scale), "canopy_roof_kn_m": dict(self.canopy_roof_kn_m),
                "factors": dict(self.factors)}


@dataclass(frozen=True)
class FrameLoadSet:
    gridline: str
    y_mm: float
    tributary_mm: float
    wall_tributary_mm: dict         # {column: clad tributary of that column line}
    gravity: tuple
    wind: tuple
    basis: str = "building"
    kc_rule: str = "surface_count"
    attachment_gravity: dict = field(default_factory=dict)   # {side: (GravityLoad, ...)} on canopy members
    attachment_tributary_mm: dict = field(default_factory=dict)  # {side: canopy/lean-to tributary at this gridline}

    @property
    def tributary_m(self) -> float:
        return self.tributary_mm / 1000.0

    def gravity_kn_m(self, label: str) -> float:
        load = next((item for item in self.gravity if item.label == label), None)
        return 0.0 if load is None else load.kpa * self.tributary_m

    def wind_case(self, cpe_case: str) -> WindCase:
        for case in self.wind:
            if case.cpe_case == cpe_case:
                return case
        raise KeyError(cpe_case)

    def to_dict(self) -> dict:
        return {"gridline": self.gridline, "y_mm": self.y_mm, "tributary_mm": self.tributary_mm,
                "basis": self.basis, "kc_rule": self.kc_rule,
                "wall_tributary_mm": dict(self.wall_tributary_mm),
                "gravity": [g.to_dict() for g in self.gravity],
                "attachment_gravity": {side: [g.to_dict() for g in loads]
                                       for side, loads in self.attachment_gravity.items()},
                "attachment_tributary_mm": dict(self.attachment_tributary_mm),
                "wind": [w.to_dict() for w in self.wind]}


# ------------------------------------------------------------------ scope

def scope_issues(model: BuildingModel) -> list:
    """Reasons this engine cannot load ``model`` (empty when it can)."""
    issues = []
    for attachment in model.attachments:
        if attachment.start_offset_mm or attachment.end_offset_mm:
            issues.append(f"{attachment.id} extends past a gridline on an eave beam")
    if model.attachments and model.main.roof_shape == "Skillion":
        issues.append("a skillion roof with a canopy or lean-to is not in the building load engine yet")
    if model.partitions:
        issues.append("internal partitions (separate chambers) are not in the building load engine yet")
    return issues


# ----------------------------------------------------------------- inputs

def _preset_number(value, default=0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _wall_has_cladding(model: BuildingModel) -> dict:
    """Whole-wall flags for ``WindInputs``: a wall counts as clad if any of it is.

    In ``wind.py`` these flags select Kce (Roof Only when nothing is clad), the
    enclosed Ka rule and the symmetric-end shortcut, so partial cladding still
    behaves as clad there.
    """
    return {face: not model.wall(face).fully_open for face in MAIN_WALLS}


def base_wind_inputs(model: BuildingModel, bay_size_m: float, canopy_lengths_m: dict | None = None) -> WindInputs:
    clad = _wall_has_cladding(model)
    frame_type = {4: "Enclosed", 3: "3 Sided", 2: "2 Sided", 1: "1 Sided", 0: "Roof Only"}[sum(clad.values())]
    lengths = canopy_lengths_m or {}
    return WindInputs(
        wind_region=model.site.region, importance_level=model.site.importance_level,
        terrain_category=model.site.terrain_category, eave_height_m=model.main.eave_mm / 1000.0,
        roof_pitch_deg=model.main.pitch_deg, roof_shape=model.main.roof_shape,
        building_width_m=model.main.span_mm / 1000.0, building_length_m=model.length_mm / 1000.0,
        bay_size_m=bay_size_m, orientation=model.site.north, frame_type=frame_type, cpi_case="Cpi +",
        left_wall_clad=clad["left"], right_wall_clad=clad["right"],
        front_wall_clad=clad["front"], back_wall_clad=clad["back"],
        left_canopy_length_m=lengths.get("left", 0.0), right_canopy_length_m=lengths.get("right", 0.0),
        reduction_factor=model.site.pressure_factor, internal_pressure_mode=model.site.internal_pressure_mode,
    )


def face_tributaries_mm(model: BuildingModel, face: str) -> tuple:
    """Clad length of a wall running along Y resolved onto each gridline column."""
    stations = [g.y_mm for g in model.gridlines]
    segments = [(a, b, 1.0) for a, b, clad in model.wall(face).segments if clad]
    return tuple(float(v) for v in frame_strip_integrals(stations, segments))


# Back-compatible name used by earlier tests and tools.
wall_tributaries_mm = face_tributaries_mm


def open_wall_areas_m2(model: BuildingModel) -> dict:
    """Open area of each main wall: open segment length × eave (as main.py)."""
    eave_m = model.main.eave_mm / 1000.0
    return {face: sum(b - a for a, b, clad in model.wall(face).segments if not clad) / 1000.0 * eave_m
            for face in MAIN_WALLS}


def gravity_loads(model: BuildingModel) -> tuple:
    """Roof area loads (kPa) with placements, as main.py's ``BasicRoofLoads``."""
    preset = model.frame_preset(model.gridlines[0].id)
    loads = preset.get("loads", {}) or {}
    snow = calculate_snow_loads(preset)
    effective_q = max(abs(_preset_number(loads.get("q_kpa"))), snow["service_kpa"])
    placements = loads.get("roof_load_placements", {}) or {}
    legacy = {"side": loads.get("roof_load_side"), "from_percent": loads.get("roof_load_from_percent"),
              "to_percent": loads.get("roof_load_to_percent")}
    result = []
    for label, key, placement_key, factor in GRAVITY_COMPONENTS:
        kpa = -effective_q if key is None else -abs(_preset_number(loads.get(key)))
        if abs(kpa) <= 1e-12:
            continue
        side, start, end = "Both", 0.0, 100.0
        if placement_key is not None:
            data = placements.get(placement_key) or legacy
            side = str(data.get("side") or "Both")
            start = _preset_number(data.get("from_percent"), 0.0)
            end = _preset_number(data.get("to_percent"), 100.0)
        result.append(GravityLoad(label, factor, kpa, side, start, end))
    return tuple(result)


def attachment_gravity_loads(attachment) -> tuple:
    """Canopy/lean-to G, Q and solar (kPa, no snow), as main.py's canopy ``BasicRoofLoads``."""
    loads = []
    for label, key, factor in ATTACHMENT_GRAVITY:
        kpa = -abs(_preset_number(attachment.properties.get(key)))
        if abs(kpa) > 1e-12:
            loads.append(GravityLoad(label, factor, kpa))
    return tuple(loads)


# -------------------------------------------------------------------- Cpi

def table_5_1b_cpi(cpi_case: str, surface: str, cpe: float, opening_area_m2: float, other_area_m2: float) -> float:
    """main.py's Table 5.1(B) rule for the dominant opening (``table_5_1b_cpi_for_opening``)."""
    ratio = float("inf") if other_area_m2 <= 1e-12 else opening_area_m2 / other_area_m2
    ka = opening_area_reduction_factor(surface, opening_area_m2)
    plus = str(cpi_case).strip() == "Cpi +"
    if ratio <= 0.5:
        return 0.0 if plus else -0.3
    if ratio < 2.0:
        if surface == "windward_wall":
            return 0.2 if plus else -0.1
        return 0.0 if plus else -0.3
    if surface == "windward_wall" and ratio < 3.0:
        multiplier = 0.7
    elif surface == "windward_wall" and ratio < 6.0:
        multiplier = 0.85
    else:
        multiplier = 1.0
    return multiplier * ka * 1.0 * cpe


def chamber_cpi(inputs, cpi_case: str, open_areas_m2: dict, surface_cpe) -> float:
    """Chamber Cpi (main.py ``chamber_cpi_from_openings``); ``surface_cpe(key)`` -> (surface, Cpe)."""
    openings = [(wall, area) for wall, area in open_areas_m2.items() if area > 1e-12]
    if not openings:
        cpi_max, cpi_min = cyclonic_enclosed_cpi(inputs, "Enclosed") or CPI_BY_FRAME_TYPE["Enclosed"]
        return cpi_max if str(cpi_case).strip() == "Cpi +" else cpi_min
    largest = max(area for _, area in openings)
    candidates = []
    for wall, area in openings:
        if abs(area - largest) > 1e-9:
            continue
        other = sum(a for w, a in openings if w != wall)
        surface, cpe = surface_cpe(wall)
        candidates.append(table_5_1b_cpi(cpi_case, surface, cpe, area, other))
    return (max if str(cpi_case).strip() == "Cpi +" else min)(candidates)


def side_wall_band_cpe(h_m: float, start_m: float, end_m: float) -> float:
    """Table 5.2(C) side-wall Cpe averaged over [start, end] from the windward edge."""
    if end_m - start_m <= 1e-12:
        return 0.0
    total = 0.0
    for from_h, to_h, cpe in SIDE_WALL_CPE_BANDS:
        a, b = from_h * h_m, (math.inf if math.isinf(to_h) else to_h * h_m)
        total += cpe * max(0.0, min(end_m, b) - max(start_m, a))
    return total / (end_m - start_m)


# ------------------------------------------------------------- factors

class Factors:
    """Ka/Kc for one frame and wind case (Kc rule signed off 2026-11-11)."""

    def __init__(self, rule: str, inputs, clad_columns: int, free_roof: bool, cpis: dict):
        self.rule = rule
        self.inputs = inputs
        self.free_roof = free_roof
        if rule == "legacy":
            self.kce = action_combination_factors(inputs)[0]
            self.surfaces = None
            self.internal_effective = None
        else:
            # AS/NZS 1170.2 effective surfaces: each wall, the roof (both
            # slopes as one) and the internal surfaces (only if |Cpi| >= 0.4).
            # On a transverse frame: the roof, each external clad column line,
            # and internal pressure. The external factor serves both Cpi cases,
            # so internal counts only when both reach 0.4. That is the
            # conservative choice (fewer surfaces, higher Kc), and with main.py's
            # Cpi rules the two cases always fall on the same side.
            external = (0 if free_roof else 1) + clad_columns
            internal = not free_roof and all(abs(v) >= 0.4 for v in cpis.values())
            self.surfaces = external + int(internal)
            self.internal_effective = internal
            self.kce = self.kc_for(self.surfaces)
            self._kc_with_internal = self.kc_for(external + 1)

    @staticmethod
    def kc_for(surfaces: int) -> float:
        """Count-to-Kc mapping, Table 5.5 (confirmed 2026-11-11; same as the 3D bracing's end-wall rule)."""
        return 0.8 if surfaces >= 3 else 0.9 if surfaces == 2 else 1.0

    def external(self, surface: str, area_m2: float) -> float:
        return max(area_reduction_factor(self.inputs, surface, area_m2) * self.kce, 0.8)

    def internal(self, cpi: float) -> float:
        if self.rule == "legacy":
            return action_combination_factors(self.inputs, cpi)[1]
        # Per Cpi case: an internal surface with |Cpi| < 0.4 is not effective (Kc,i 1.0).
        if self.free_roof or abs(cpi) < 0.4:
            return 1.0
        return self._kc_with_internal

    def evidence(self) -> dict:
        data = {"kc_rule": self.rule, "kce": self.kce}
        if self.surfaces is not None:
            data.update(effective_surfaces=self.surfaces, internal_effective=self.internal_effective)
        return data


# ------------------------------------------------------------- roof strips

def _roof_zone_rows(result):
    """(name, start_m, end_m, cpe_max, cpe_min) exactly as ``calculate_wind`` positioned them."""
    return [(z.name, z.distance_from_eave_from_m, z.distance_from_eave_to_m, z.cpe_max, z.cpe_min)
            for z in result.roof_zones]


def side_wind_roof_strips(result, wind_from: str, sign: str, tributary_m: float, factors: Factors) -> tuple:
    """``WindCalculationResult.case_roof_strips`` for Left/Right with this frame's factors.

    Same zones, mirroring and skillion high-side banding as wind.py; only the
    pressure factor and tributary are per frame.
    """
    inputs, wu = result.inputs, result.wu_kn_m2
    total = result.roof_slope_length_m
    use_max = sign == "+"
    zones = []
    for name, start, end, cmax, cmin in _roof_zone_rows(result):
        factor = factors.external("roof", max(end - start, 0.0) * tributary_m)
        zones.append((name, start, end, (-(cmax if use_max else cmin)) * wu * factor * tributary_m))

    def mirrored(from_right):
        strips = []
        for _, start, end, q in zones:
            a, b = max(0.0, min(start, total)), max(0.0, min(end, total))
            if b <= a:
                continue
            if from_right:
                a, b = total - b, total - a
            strips.append((max(0.0, a), min(total, b), q))
        return tuple(sorted(strips, key=lambda item: item[0]))

    if inputs.roof_shape == "Skillion":
        if wind_from == "left":
            if inputs.roof_pitch_deg >= 10.0:
                q = next((z[3] for z in zones if z[0] == "Upwind rafter"), zones[0][3])
                return ((0.0, total, q),)
            return mirrored(False)
        cos_pitch = max(math.cos(math.radians(inputs.roof_pitch_deg)), 1e-9)
        strips = []
        for _, from_h, to_h, cmax, cmin in low_pitch_cpe_values(result.h_over_d):
            a = min(from_h * result.h_m / cos_pitch, total)
            b = min(to_h * result.h_m / cos_pitch, total)
            if b <= a:
                continue
            factor = factors.external("roof", (b - a) * tributary_m)
            strips.append((total - b, total - a, (-(cmax if use_max else cmin)) * wu * factor * tributary_m))
        return tuple(sorted(strips, key=lambda item: item[0]))
    return mirrored(wind_from == "right")


def _free_roof_strips(model, result, cpe_case: str, roof_length_m: float, tributary_m: float):
    """AS/NZS B.5 free roof for a fully open main building (main.py ``main_roof_free_roof_values``)."""
    use_uplift = cpe_case.endswith("+")
    if cpe_case.startswith(("End", "Front", "Back")):
        q = -transverse_free_roof_cpn("empty", use_uplift) * result.wu_kn_m2 * 1.0 * tributary_m
        return "uniform", ((0.0, roof_length_m, q),)
    left_wind = cpe_case.startswith("Left")

    def line(part):
        cpn_as = free_roof_cpn(result.inputs.roof_pitch_deg, result.h_over_d, part, "empty", use_uplift)
        return -cpn_as * result.wu_kn_m2 * 1.0 * tributary_m

    if model.main.roof_shape == "Gable":
        ridge = roof_length_m / 2.0
        first, second = ("windward", "leeward") if left_wind else ("leeward", "windward")
        return "slopes", ((0.0, ridge, line(first)), (ridge, roof_length_m, line(second)))
    # A skillion's apex node is its high eave; panels left of it are "windward"
    # for left wind. Positive pitch rises to the right, so all of it qualifies.
    rises_right = model.main.pitch_deg > 0.0
    part = ("windward" if left_wind else "leeward") if rises_right else ("leeward" if left_wind else "windward")
    return "bands", ((0.0, roof_length_m, line(part)),)


def canopy_free_cpn(attachment, result, cpe_case: str) -> float:
    """main.py ``canopy_free_roof_values``: frame Cpn (outward positive) of a free-roof canopy."""
    use_uplift = cpe_case.endswith("+")
    underside = "blocked" if str(attachment.properties.get("underside", "")).startswith("Blocked") else "empty"
    if cpe_case.startswith(("End", "Front", "Back")):
        return -transverse_free_roof_cpn(underside, use_uplift)
    left_wind = cpe_case.startswith("Left")
    windward = (attachment.side == "left") == left_wind
    pitch = _preset_number(attachment.properties.get("pitch_deg"), 0.0)
    return -free_roof_cpn(pitch, result.h_over_d, "windward" if windward else "leeward", underside, use_uplift)


# ----------------------------------------------------------------- engine

def _clip(segments, lo, hi):
    return [(max(a, lo), min(b, hi), v) for a, b, v in segments if min(b, hi) > max(a, lo)]


class _Engine:
    def __init__(self, model: BuildingModel, basis: str, kc_rule: str):
        if basis not in BASES:
            raise ValueError(f"basis must be one of {', '.join(BASES)}")
        if kc_rule not in KC_RULES:
            raise ValueError(f"kc_rule must be one of {', '.join(KC_RULES)}")
        issues = scope_issues(model)
        if issues:
            raise BuildingLoadsScopeError("; ".join(issues))
        self.model, self.basis, self.kc_rule = model, basis, kc_rule
        self.nominal_bay_m = max(model.bay_lengths_mm) / 1000.0
        self.tributaries = model.tributary_widths_mm
        self.stations = [g.y_mm for g in model.gridlines]
        self.eave_m = model.main.eave_mm / 1000.0
        self.length_m = model.length_mm / 1000.0
        self.gable = model.main.roof_shape == "Gable"

        self.attachments = {a.id: a for a in model.attachments}
        self.present = [model.attachments_at(g.id) for g in model.gridlines]
        self.extent = {a.id: model.attachment_extent_mm(a) for a in model.attachments}
        main_clad = _wall_has_cladding(model)
        self.main_clad = main_clad
        self.leans = {a.id: a for a in model.attachments if a.kind == "lean_to"}
        self.lean_clad = {lean_id: {part: not model.wall(f"{lean_id}/{part}").fully_open
                                    for part in ("outer", "front", "back")} for lean_id in self.leans}
        # A lean-to joins the main chamber where the main wall beside it has no cladding.
        self.connected = {lean_id: not model.wall(a.side).clad_overlapping(*self.extent[lean_id])
                          for lean_id, a in self.leans.items()}
        self.free_roof = not any(main_clad.values()) and not any(
            any(states.values()) for states in self.lean_clad.values())
        frame_type = {4: "Enclosed", 3: "3 Sided", 2: "2 Sided", 1: "1 Sided", 0: "Roof Only"}[sum(main_clad.values())]
        self.walls_act = frame_type not in {"Roof Only", "No Wind"}

        # Main side-wall tributaries: all clad, and the part alongside lean-tos
        # (an internal wall there). The rest is an external wall.
        self.wall_trib = {face: face_tributaries_mm(model, face) for face in MAIN_SIDE_WALLS}
        self.inside_trib = {}
        for side in MAIN_SIDE_WALLS:
            clad = [(a, b, 1.0) for a, b, c in model.wall(side).segments if c]
            inside = [s for lean_id, a in self.leans.items() if a.side == side for s in _clip(clad, *self.extent[lean_id])]
            self.inside_trib[side] = tuple(float(v) for v in frame_strip_integrals(self.stations, inside))
        self.attachment_trib = {a_id: tuple(float(v) for v in frame_strip_integrals(self.stations, [(*self.extent[a_id], 1.0)]))
                                for a_id in self.attachments}
        self.lean_outer_trib = {lean_id: face_tributaries_mm(model, f"{lean_id}/outer") for lean_id in self.leans}
        # wind.py canopy lengths: plan width for the main result (as wind_inputs_from_ui);
        # slope path for the continuous roof (main.py ``get_continuous_roof_wind_result``).
        self.plan_m = {a_id: a.width_mm / 1000.0 for a_id, a in self.attachments.items()}
        self.slope_m = {}
        for a_id, a in self.attachments.items():
            pitch = math.radians(_preset_number(a.properties.get("pitch_deg"), 0.0))
            self.slope_m[a_id] = math.hypot(a.width_mm, a.width_mm * math.tan(pitch)) / 1000.0
        self.max_lean_width = {side: max([a.width_mm for a in self.leans.values() if a.side == side], default=0.0)
                               for side in MAIN_SIDE_WALLS}
        self._results = {}
        self._chambers = {}

    # ---- wind results

    def lengths(self, index, continuous):
        if continuous and self.free_roof:
            return {}
        source = self.slope_m if continuous else self.plan_m
        return {side: source[a.id] for side, a in self.present[index].items()}

    def result(self, source, bay_m, lengths=None):
        key = (source, bay_m, tuple(sorted((lengths or {}).items())))
        if key not in self._results:
            self._results[key] = calculate_wind(
                replace(base_wind_inputs(self.model, bay_m, lengths), orientation=source))
        return self._results[key]

    def source(self, wind_from: str):
        """Source bearing; legacy End follows main.py's symmetric-end rule."""
        if self.basis == "legacy_2d" and wind_from == "end":
            source = self.model.wind_source_direction("front")
            if self.main_clad["front"] and self.main_clad["back"]:
                return symmetric_end_source_direction(self.model.site.region, source)
            return source
        return self.model.wind_source_direction(wind_from)

    def directions(self):
        return ("left", "right", "end") if self.basis == "legacy_2d" else ("left", "right", "front", "back")

    # ---- per-frame geometry

    def m(self, values, index):
        return values[index] / 1000.0

    def main_inside(self, side, index):
        return self.m(self.inside_trib[side], index)

    def main_outside(self, side, index):
        return max(self.m(self.wall_trib[side], index) - self.main_inside(side, index), 0.0)

    def lean_here(self, side, index):
        attachment = self.present[index].get(side)
        return attachment if attachment is not None and attachment.kind == "lean_to" else None

    def lean_outer_m(self, side, index):
        lean = self.lean_here(side, index)
        return 0.0 if lean is None else self.m(self.lean_outer_trib[lean.id], index)

    def attachment_m(self, side, index):
        attachment = self.present[index].get(side)
        return 0.0 if attachment is None else self.m(self.attachment_trib[attachment.id], index)

    def external_columns(self, index: int) -> int:
        """Clad external column lines on this frame (a lean-to's outer wall where there is one)."""
        count = 0
        for side in MAIN_SIDE_WALLS:
            if self.lean_here(side, index) is not None:
                count += (self.lean_outer_m(side, index) > 1e-12
                          or (self.walls_act and self.main_outside(side, index) > 1e-12))
            else:
                count += self.walls_act and self.m(self.wall_trib[side], index) > 1e-12
        return count

    # ---- per frame

    def frame(self, index: int, gravity: tuple) -> FrameLoadSet:
        gridline = self.model.gridlines[index]
        cases = [self.case(index, wind_from, sign) for wind_from in self.directions() for sign in SIGNS]
        walls = {face: values[index] for face, values in self.wall_trib.items()}
        for side in MAIN_SIDE_WALLS:
            lean = self.lean_here(side, index)
            if lean is not None:
                walls[f"{side}_lean"] = self.lean_outer_trib[lean.id][index]
        present = self.present[index]
        return FrameLoadSet(gridline.id, gridline.y_mm, self.tributaries[index], walls,
                            gravity, tuple(cases), self.basis, self.kc_rule,
                            {side: attachment_gravity_loads(a) for side, a in present.items()},
                            {side: self.attachment_trib[a.id][index] for side, a in present.items()})

    def case(self, index: int, wind_from: str, sign: str) -> WindCase:
        model = self.model
        cpe_case = f"{wind_from.capitalize()} {sign}"
        legacy_name = "End" if wind_from in ("end", "front", "back") else wind_from.capitalize()
        legacy_cpe_case = f"{legacy_name} {sign}"
        end_wind = legacy_name == "End"
        tributary_m = self.tributaries[index] / 1000.0
        present = self.present[index]
        source = self.source(wind_from)
        plan, slope = self.lengths(index, False), self.lengths(index, True)
        nominal = self.result(source, self.nominal_bay_m, plan)
        continuous = self.result(source, self.nominal_bay_m, slope)
        legacy = self.basis == "legacy_2d"
        bay = self.nominal_bay_m

        def width(w):
            """Line-load width: legacy keeps main.py's nominal bay × share (exact for a full bay)."""
            return bay * (w / bay) if legacy else w

        cpi, lean_cpi = self.chamber_cpis(nominal, wind_from, legacy_cpe_case)
        if legacy:
            factors = Factors("legacy", nominal.inputs, 0, self.free_roof, cpi)
            frame_result, frame_continuous = nominal, continuous
        else:
            frame_result = self.result(source, tributary_m, plan)
            frame_continuous = self.result(source, tributary_m, slope)
            factors = Factors(self.kc_rule, frame_result.inputs, self.external_columns(index), self.free_roof, cpi)
        wu = frame_result.wu_kn_m2
        scale = tributary_m / bay if legacy else 1.0

        # ---- roof external
        canopy_cpe, canopy_roof, canopy_scale = {}, {}, {}
        if self.free_roof:
            name = legacy_cpe_case if wind_from == "end" else cpe_case
            layout, strips = _free_roof_strips(model, nominal, name, nominal.roof_slope_length_m,
                                               bay if legacy else tributary_m)
            strips = tuple((a, b, q * scale) for a, b, q in strips)
            roof_basis, roof_length_m = "free_roof_cpn", nominal.roof_slope_length_m
            for side, attachment in present.items():
                canopy_cpe[side] = (canopy_free_cpn(attachment, nominal, name) * nominal.wu_kn_m2 * 1.0
                                    * width(self.attachment_m(side, index)))
        else:
            roof_basis, roof_length_m = "cpe", continuous.roof_slope_length_m
            if legacy:
                strips = tuple((a, b, q * scale) for a, b, q in continuous.case_roof_strips(legacy_cpe_case))
                layout = "uniform" if end_wind else ("slopes" if self.gable and model.main.pitch_deg >= 10.0 else "bands")
            elif not end_wind:
                strips = side_wind_roof_strips(frame_continuous, wind_from, sign, tributary_m, factors)
                layout = "slopes" if self.gable and model.main.pitch_deg >= 10.0 else "bands"
            else:
                strips = (self.end_wind_roof(index, wind_from, sign, nominal, roof_length_m, tributary_m, factors),)
                layout = "uniform"
                # A canopy integrates the bands over its own extent only.
                for side, attachment in present.items():
                    attach_m = self.attachment_m(side, index)
                    canopy_roof[side] = self.end_wind_roof(index, wind_from, sign, nominal, roof_length_m, attach_m,
                                                           factors, extent=self.extent[attachment.id])[2]
            for side in present:
                if not (end_wind and not legacy):
                    canopy_scale[side] = self.attachment_m(side, index) / tributary_m

        # ---- walls external
        wall_cpe = {}
        any_lean_outer = any(states["outer"] for states in self.lean_clad.values())
        if self.walls_act or any_lean_outer:
            for side in MAIN_SIDE_WALLS:
                lean = self.lean_here(side, index)
                lean_outer = lean is not None and self.lean_clad[lean.id]["outer"]
                outside, inside = self.main_outside(side, index), self.main_inside(side, index)
                # main.py: a main column beside a lean-to is internal, except in side wind
                # when the lean-to's outer wall is open.
                if lean is None:
                    exposed = outside + inside
                elif end_wind:
                    exposed = outside
                else:
                    exposed = outside + (0.0 if lean_outer else inside)
                if self.walls_act and exposed > 1e-12:
                    exclude = [self.extent[l_id] for l_id, a in self.leans.items() if a.side == side]
                    wall_cpe[side] = self.wall_external(index, side, side, wind_from, nominal, frame_result,
                                                        factors, exposed, exclude)
                if lean_outer and self.lean_outer_m(side, index) > 1e-12:
                    wall_cpe[f"{side}_lean"] = self.wall_external(
                        index, f"{lean.id}/outer", side, wind_from, nominal, frame_result, factors,
                        self.lean_outer_m(side, index), [])

        # ---- internal pressure
        roof_cpi = {} if self.free_roof else {
            case: cpi[case] * wu * 1.0 * factors.internal(cpi[case]) * width(tributary_m) for case in CPI_CASE_OPTIONS}
        canopy_cpi = {}
        if not self.free_roof:
            for case in CPI_CASE_OPTIONS:
                lines = {}
                for side, attachment in present.items():
                    value = lean_cpi[attachment.id][case] if attachment.kind == "lean_to" else self.open_canopy_cpi(
                        index, attachment, wind_from, legacy_cpe_case, nominal)
                    lines[side] = value * wu * 1.0 * factors.internal(value) * width(self.attachment_m(side, index))
                if lines:
                    canopy_cpi[case] = lines
        wall_cpi = {}
        if self.walls_act or any_lean_outer:
            for case in CPI_CASE_OPTIONS:
                lines = {}
                for side in MAIN_SIDE_WALLS:
                    outward = -1.0 if side == "left" else 1.0
                    lean = self.lean_here(side, index)
                    outside, inside = self.main_outside(side, index), self.main_inside(side, index)
                    if lean is None:
                        outside, inside = outside + inside, 0.0
                    total = 0.0
                    if self.walls_act and outside > 1e-12:
                        line = abs(cpi[case]) * wu * 1.0 * factors.internal(cpi[case]) * width(outside)
                        if abs(line) > 1e-12:
                            total += line * (outward if cpi[case] > 0 else -outward)
                    if lean is not None and inside > 1e-12:
                        total += self.internal_wall_net(side, lean.id, case, cpi, lean_cpi, wu, factors, width(inside))
                    if abs(total) > 1e-12:
                        lines[side] = total
                    if lean is not None and self.lean_clad[lean.id]["outer"] and self.lean_outer_m(side, index) > 1e-12:
                        value = lean_cpi[lean.id][case]
                        line = abs(value) * wu * 1.0 * factors.internal(value) * width(self.lean_outer_m(side, index))
                        if abs(line) > 1e-12:
                            lines[f"{side}_lean"] = line * (outward if value > 0 else -outward)
                if lines:
                    wall_cpi[case] = lines

        return WindCase(
            wind_from=wind_from, cpe_case=cpe_case, legacy_cpe_case=legacy_cpe_case, source_direction=source,
            wu_kn_m2=frame_result.wu_kn_m2, ws_kn_m2=frame_result.ws_kn_m2, roof_basis=roof_basis,
            roof_layout=layout, roof_length_m=roof_length_m, roof_strips=strips, wall_cpe_kn_m=wall_cpe,
            cpi=cpi, roof_cpi_kn_m=roof_cpi, wall_cpi_kn_m=wall_cpi, factors=factors.evidence(),
            canopy_cpe_kn_m=canopy_cpe, canopy_cpi_kn_m=canopy_cpi,
            canopy_roof_scale={s: v for s, v in canopy_scale.items() if v != 1.0},
            canopy_roof_kn_m=canopy_roof)

    def internal_wall_net(self, side, lean_id, cpi_case, cpi, lean_cpi, wu, factors, width):
        """main.py ``internal_wall_net_cpi_line``: the shared main/lean-to column, enveloped."""
        main_sign, lean_sign = (-1.0, 1.0) if side == "left" else (1.0, -1.0)
        candidates = []
        for main_case in CPI_CASE_OPTIONS:
            main_line = cpi[main_case] * wu * 1.0 * factors.internal(cpi[main_case]) * width
            for lean_case in CPI_CASE_OPTIONS:
                lean_value = lean_cpi[lean_id][lean_case]
                lean_line = lean_value * wu * 1.0 * factors.internal(lean_value) * width
                candidates.append(main_sign * main_line + lean_sign * lean_line)
        return (max if cpi_case == "Cpi +" else min)(candidates)

    # ---- chambers

    def chamber_cpis(self, nominal, wind_from, legacy_cpe_case):
        """({cpi case: main Cpi}, {lean-to id: {cpi case: lean-to chamber Cpi}})."""
        key = (nominal.inputs.orientation, wind_from, legacy_cpe_case)
        if key in self._chambers:
            return self._chambers[key]
        inputs = nominal.inputs
        main_openings = self.main_chamber_openings()
        surface = self.surface_cpe_function(nominal, wind_from, legacy_cpe_case)
        main = {case: chamber_cpi(inputs, case, self.areas(main_openings), lambda k: surface(k, main_openings[k]))
                for case in CPI_CASE_OPTIONS}
        lean = {}
        for lean_id, attachment in self.leans.items():
            if self.connected[lean_id]:
                lean[lean_id] = dict(main)
                continue
            openings = {attachment.side: self.open_segments(f"{lean_id}/outer"),
                        "front": self.open_segments(f"{lean_id}/front"),
                        "back": self.open_segments(f"{lean_id}/back")}
            lean[lean_id] = {case: chamber_cpi(inputs, case, self.areas(openings),
                                               lambda k, o=openings: surface(k, o[k]))
                             for case in CPI_CASE_OPTIONS}
        self._chambers[key] = (main, lean)
        return main, lean

    def open_segments(self, face, exclude=()):
        segments = [(a, b) for a, b, clad in self.model.wall(face).segments if not clad]
        for lo, hi in exclude:
            kept = []
            for a, b in segments:
                if a < lo:
                    kept.append((a, min(b, lo)))
                if b > hi:
                    kept.append((max(a, hi), b))
            segments = [(a, b) for a, b in kept if b - a > 1e-9]
        return [(face, a, b) for a, b in segments]

    def main_chamber_openings(self):
        """{wall key: [(face, a, b) open external segments]}; connected lean-tos join the chamber."""
        openings = {}
        for side in MAIN_SIDE_WALLS:
            side_leans = [(l_id, a) for l_id, a in self.leans.items() if a.side == side]
            # Main wall beside a lean-to is internal; beyond the lean-tos it is external.
            segments = self.open_segments(side, exclude=[self.extent[l_id] for l_id, _ in side_leans])
            for l_id, _ in side_leans:
                if self.connected[l_id]:
                    segments += self.open_segments(f"{l_id}/outer")
            openings[side] = segments
        for end in ("front", "back"):
            segments = self.open_segments(end)
            for l_id in self.leans:
                if self.connected[l_id]:
                    segments += self.open_segments(f"{l_id}/{end}")
            openings[end] = segments
        return openings

    def areas(self, openings):
        return {key: sum(b - a for _, a, b in segments) / 1000.0 * self.eave_m for key, segments in openings.items()}

    def surface_cpe_function(self, nominal, wind_from, legacy_cpe_case):
        if self.basis == "legacy_2d":
            def legacy(key, segments):
                surface = wall_surface_for_cpe_case(legacy_cpe_case, key)
                return surface, wall_cpe_for_surface(nominal, surface)
            return legacy

        def positional(key, segments):
            weighted, length = 0.0, 0.0
            surface = "side_wall"
            for face, a, b in segments:
                surface, cpe = self.opening_cpe(nominal, wind_from, key, face, a, b)
                weighted += cpe * (b - a)
                length += b - a
            return surface, (weighted / length if length > 0 else 0.0)
        return positional

    def opening_cpe(self, nominal, wind_from, key, face, a, b):
        """(surface, Cpe) of an open segment on an external face, from where it is."""
        if key == wind_from:
            return "windward_wall", 0.7
        if key == OPPOSITE[wind_from]:
            if wind_from in ("front", "back"):
                # Wind along the ridge: d/b = length/span with the pitch-0 row (as the 3D bracing).
                return "leeward_wall", leeward_wall_cpe(self.model.length_mm / self.model.main.span_mm, 0.0)
            return "leeward_wall", nominal.leeward_wall_cpe
        h = nominal.h_m
        if wind_from in ("front", "back"):
            s0, s1 = (a, b) if wind_from == "front" else (self.model.length_mm - b, self.model.length_mm - a)
        else:
            x_min = -self.max_lean_width["left"]
            x_max = self.model.main.span_mm + self.max_lean_width["right"]
            s0, s1 = (a - x_min, b - x_min) if wind_from == "left" else (x_max - b, x_max - a)
        return "side_wall", side_wall_band_cpe(h, s0 / 1000.0, s1 / 1000.0)

    def open_canopy_cpi(self, index, attachment, wind_from, legacy_cpe_case, nominal):
        """Open canopy underside: Table 5.1(B) single opening on its open side (``wind.open_canopy_cpi``)."""
        y0, y1 = self.extent[attachment.id]
        length_m = (y1 - y0) / 1000.0
        side = attachment.side
        if self.basis == "legacy_2d":
            cpi, _, _ = open_canopy_cpi(nominal, legacy_cpe_case, side, length_m, self.eave_m)
            return cpi
        if wind_from in ("left", "right"):
            wall_cpe = 0.7 if side == wind_from else nominal.leeward_wall_cpe
        else:
            # End wind: this frame's average side-wall band Cpe over the canopy (as the 3D bracing).
            h_mm = nominal.h_m * 1000.0
            length_mm = self.model.length_mm
            bands = []
            for a, b, c in SIDE_WALL_CPE_BANDS:
                lo, hi = a * h_mm, (length_mm if math.isinf(b) else min(length_mm, b * h_mm))
                if wind_from == "back":
                    lo, hi = length_mm - hi, length_mm - lo
                bands.append((lo, hi, c))
            integral = frame_strip_integrals(self.stations, _clip(bands, y0, y1))[index]
            wall_cpe = integral / self.attachment_trib[attachment.id][index]
        cpi, _, _ = open_canopy_cpi(nominal, legacy_cpe_case, side, length_m, self.eave_m, wall_cpe=wall_cpe)
        return cpi

    # ---- loads

    def end_wind_roof(self, index, wind_from, sign, result, roof_length_m, tributary_m, factors, extent=None):
        """Table 5.3(A) roof bands along the length (d = length, all pitches) on this frame."""
        h_m = result.h_m
        length_mm = self.model.length_mm
        bands = []
        for _, a, b, cmax, cmin in low_pitch_cpe_values(h_m / (length_mm / 1000.0)):
            lo, hi = max(0.0, a * h_m * 1000.0), min(length_mm, b * h_m * 1000.0)
            if wind_from == "back":
                lo, hi = length_mm - hi, length_mm - lo
            if hi > lo:
                bands.append((lo, hi, cmax if sign == "+" else cmin))
        if extent is not None:
            bands = _clip(bands, *extent)
        integral_mm = frame_strip_integrals(self.stations, bands)[index]
        factor = factors.external("roof", roof_length_m * tributary_m)
        q = -(integral_mm / 1000.0) * result.wu_kn_m2 * factor
        return (0.0, roof_length_m, q)

    def wall_external(self, index, face, side, wind_from, nominal, frame_result, factors, clad_m, exclude):
        """Signed +X line load on a column from external pressure on ``face``.

        ``clad_m`` is the column's exposed tributary on this face; ``exclude``
        removes lean-to extents from a main wall's end-wind band integral.
        """
        bay = self.nominal_bay_m
        if wind_from in ("left", "right"):
            windward = side == wind_from
            surface = "windward_wall" if windward else "leeward_wall"
            cpe = 0.7 if windward else frame_result.leeward_wall_cpe
            direction = 1.0 if wind_from == "left" else -1.0
            if self.basis == "legacy_2d":
                factor = factors.external(surface, bay * self.eave_m)
                return abs(cpe) * nominal.wu_kn_m2 * 1.0 * factor * bay * (clad_m / bay) * direction
            factor = factors.external(surface, clad_m * self.eave_m)
            return abs(cpe) * frame_result.wu_kn_m2 * 1.0 * factor * clad_m * direction
        if wind_from == "end":
            # 2D: first-bay side-wall average on every frame, suction outward.
            cpe = wall_cpe_for_surface(nominal, "side_wall")
            factor = factors.external("side_wall", bay * self.eave_m)
            magnitude = abs(cpe) * nominal.wu_kn_m2 * 1.0 * factor * bay * (clad_m / bay)
            return magnitude * (-1.0 if side == "left" else 1.0)
        # Building end wind: Table 5.2(C) bands from the windward end, over clad segments only.
        h_mm = nominal.h_m * 1000.0
        length_mm = self.model.length_mm
        bands = []
        for from_h, to_h, cpe in SIDE_WALL_CPE_BANDS:
            lo = from_h * h_mm
            hi = length_mm if math.isinf(to_h) else to_h * h_mm
            if wind_from == "back":
                lo, hi = length_mm - hi, length_mm - lo
            bands.append((lo, min(hi, length_mm), cpe))
        segments = []
        for a, b, clad in self.model.wall(face).segments:
            if clad:
                for lo, hi, cpe in bands:
                    segments.extend(_clip([(a, b, cpe)], lo, hi))
        for lo, hi in exclude:
            kept = []
            for a, b, cpe in segments:
                if a < lo:
                    kept.append((a, min(b, lo), cpe))
                if b > hi:
                    kept.append((max(a, hi), b, cpe))
            segments = [s for s in kept if s[1] - s[0] > 1e-9]
        integral_mm = frame_strip_integrals(self.stations, segments)[index]
        factor = factors.external("side_wall", clad_m * self.eave_m)
        inward = (integral_mm / 1000.0) * frame_result.wu_kn_m2 * factor
        return inward if side == "left" else -inward


def building_loads(model: BuildingModel, basis: str = "building", kc_rule: str = "surface_count") -> tuple:
    """A ``FrameLoadSet`` for every gridline, front to back.

    ``kc_rule`` applies to the ``building`` basis only; ``legacy_2d`` always
    uses today's whole-building Kc so it stays comparable with main.py.
    """
    engine = _Engine(model, basis, kc_rule)
    gravity = gravity_loads(model)
    return tuple(engine.frame(index, gravity) for index in range(len(model.gridlines)))


def building_loads_to_dict(frames) -> dict:
    return {"schema": SCHEMA, "frames": [frame.to_dict() for frame in frames]}
