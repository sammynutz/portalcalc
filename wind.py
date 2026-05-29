"""
First-pass wind calculator for the portal frame app.

This module uses Sam's uploaded Wind Calculator.xlsx as the calculation map.
It is intended as an engineering aid / software scaffold, not a certified
AS/NZS 1170.2 design certificate.

Scope in this first version
---------------------------
- Regional ultimate and serviceability wind speed lookup from the spreadsheet.
- Direction multiplier lookup from the spreadsheet's direction table.
- Terrain/height multiplier interpolation from the spreadsheet's Mz,cat table.
- Basic dynamic pressure calculation: q = 0.5 * rho_air * V^2 / 1000.
- Low-pitch roof zones matching the workbook rows:
    0-0.5h, 0.5-1h, 1-2h, 2-3h, >3h.
- Pitched roof Cpe mapping for roof pitch >=10° using the workbook's
  Upwind Slope 1 / Upwind Slope 2 bilinear tables.
- Left/right, max/min roof strip outputs in kN/m over the bay width.
- Optional conversion of roof strips to equivalent nodal loads on top chord nodes.

Deliberate limitations
----------------------
- This does not yet fully cover all AS/NZS 1170.2 cases, shielding, topography,
  local pressure zones, openings, dominant openings, canopies, dynamic response,
  or full cladding design.
- Roof pitch >=10° now uses the workbook's separate pitched-roof Cpe table.
- For frame analysis, pressures are converted to global vertical nodal loads.
  Later we should support member-normal loads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Literal, Optional, Sequence, Tuple
import math


WindDirection = Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
CpeCaseName = Literal["Left +", "Left -", "Right +", "Right -", "End +", "End -"]
CpiCaseName = Literal["Cpi +", "Cpi -"]
WindCaseName = Literal["Left +", "Left -", "Right +", "Right -", "End +", "End -", "Cpi +", "Cpi -"]
CPE_CASE_OPTIONS = ["Left +", "Left -", "Right +", "Right -", "End +", "End -"]
CPI_CASE_OPTIONS = ["Cpi +", "Cpi -"]
WIND_CASE_OPTIONS = CPE_CASE_OPTIONS + CPI_CASE_OPTIONS
DIRECTION_ORDER: List[WindDirection] = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

FrameType = Literal["Enclosed", "Roof Only", "3 Sided", "2 Sided", "1 Sided", "No Wind"]


LoadArrow = Tuple[float, float, float, float]


AREA_REDUCTION_TABLE = {
    "roof": [(10.0, 1.0), (25.0, 0.9), (100.0, 0.8)],
    "side_wall": [(10.0, 1.0), (25.0, 0.9), (100.0, 0.8)],
    "windward_wall": [(10.0, 1.0), (25.0, 0.95), (100.0, 0.9)],
    "leeward_wall": [(10.0, 1.0), (25.0, 1.0), (100.0, 0.95)],
}

SIDE_WALL_CPE_BANDS = [
    (0.0, 1.0, -0.65),
    (1.0, 2.0, -0.50),
    (2.0, 3.0, -0.30),
    (3.0, float("inf"), -0.20),
]


# Ultimate regional wind speed table from the spreadsheet.
# Rows are importance levels 1-4; columns are wind regions.
REGIONAL_WIND_SPEEDS: Dict[int, Dict[str, float]] = {
    1: {"A0": 41, "A1": 41, "A2": 41, "A3": 41, "A4": 41, "A5": 41, "B1": 48, "B2": 48, "C": 53, "D": 66},
    2: {"A0": 45, "A1": 45, "A2": 45, "A3": 45, "A4": 45, "A5": 45, "B1": 60, "B2": 60, "C": 69, "D": 88},
    3: {"A0": 45, "A1": 45, "A2": 45, "A3": 45, "A4": 45, "A5": 45, "B1": 64, "B2": 64, "C": 74, "D": 95},
    4: {"A0": 48, "A1": 48, "A2": 48, "A3": 48, "A4": 48, "A5": 48, "B1": 69, "B2": 69, "C": 79, "D": 106},
}

# The spreadsheet uses 37 m/s serviceability wind speed for the A regions in the
# sample. This table is intentionally conservative/simple for first integration.
# Expand later if your workbook has separate SLS values for all regions.
SERVICEABILITY_WIND_SPEEDS: Dict[str, float] = {
    "A0": 37, "A1": 37, "A2": 37, "A3": 37, "A4": 37, "A5": 37,
    "B1": 37, "B2": 37, "C": 37, "D": 37,
}

# Directional multipliers from the spreadsheet's table. Rows are directions;
# columns are wind regions.
DIRECTION_MULTIPLIERS: Dict[WindDirection, Dict[str, float]] = {
    "NW": {"A0": 0.95, "A1": 0.95, "A2": 0.95, "A3": 0.95, "A4": 1.00, "A5": 0.95, "B1": 0.90, "B2": 0.90, "C": 0.90, "D": 0.95},
    "N":  {"A0": 0.90, "A1": 0.90, "A2": 0.85, "A3": 0.90, "A4": 0.85, "A5": 0.95, "B1": 0.75, "B2": 0.90, "C": 0.85, "D": 0.90},
    "NE": {"A0": 0.85, "A1": 0.85, "A2": 0.75, "A3": 0.75, "A4": 0.75, "A5": 0.80, "B1": 0.75, "B2": 0.90, "C": 0.85, "D": 0.90},
    "E":  {"A0": 0.85, "A1": 0.85, "A2": 0.85, "A3": 0.75, "A4": 0.75, "A5": 0.80, "B1": 0.85, "B2": 0.90, "C": 0.90, "D": 0.90},
    "SE": {"A0": 0.90, "A1": 0.80, "A2": 0.95, "A3": 0.90, "A4": 0.80, "A5": 0.80, "B1": 0.90, "B2": 0.90, "C": 0.90, "D": 0.90},
    "S":  {"A0": 0.90, "A1": 0.80, "A2": 0.95, "A3": 0.90, "A4": 0.80, "A5": 0.80, "B1": 0.95, "B2": 0.90, "C": 0.90, "D": 0.90},
    "SW": {"A0": 0.95, "A1": 0.95, "A2": 0.95, "A3": 0.95, "A4": 0.90, "A5": 0.95, "B1": 0.95, "B2": 0.90, "C": 0.90, "D": 0.95},
    "W":  {"A0": 1.00, "A1": 0.10, "A2": 1.00, "A3": 1.00, "A4": 1.00, "A5": 1.00, "B1": 0.95, "B2": 0.90, "C": 0.90, "D": 0.95},
}

# Terrain multiplier table from the spreadsheet.
# heights in metres; columns are terrain categories.
TERRAIN_MULTIPLIER_TABLE: Dict[float, Dict[float, float]] = {
    3:  {1: 0.97, 2: 0.91, 2.5: 0.87, 3: 0.83, 4: 0.75},
    5:  {1: 1.01, 2: 0.91, 2.5: 0.87, 3: 0.83, 4: 0.75},
    10: {1: 1.08, 2: 1.00, 2.5: 0.92, 3: 0.83, 4: 0.75},
    15: {1: 1.12, 2: 1.05, 2.5: 0.97, 3: 0.89, 4: 0.75},
    20: {1: 1.14, 2: 1.08, 2.5: 1.01, 3: 0.94, 4: 0.75},
    30: {1: 1.14, 2: 1.08, 2.5: 1.01, 3: 0.94, 4: 0.75},
}

# Low pitch roof Cpe table from spreadsheet rows 38-42. Values are interpolated
# between h/d = 0.5 and 1.0, or clamped outside that range like the workbook.
LOW_PITCH_ROOF_CPE = [
    ("0 to 0.5h", 0.0, 0.5, -0.9, -1.3, -0.4, -0.6),
    ("0.5 to 1h", 0.5, 1.0, -0.9, -0.7, -0.4, -0.3),
    ("1 to 2h", 1.0, 2.0, -0.5, -0.7, 0.0, -0.3),
    ("2 to 3h", 2.0, 3.0, -0.3, -0.7, 0.1, -0.3),
    (">3h", 3.0, 100.0, -0.2, -0.7, 0.2, -0.3),
]

# Roof pitch >=10° Cpe tables from the workbook. These are bilinear tables
# against roof pitch and h/d. The workbook maps them as:
# - Upwind rafter: Upwind Slope 1 for the + case, Upwind Slope 2 for the - case.
# - Downwind rafter: Roof-pitch >=10° table row at AI37:AO41 for the + case,
#   and 0.0 for the - case.
PITCHED_ROOF_PITCH_POINTS = [0, 10, 15, 20, 25, 30, 35, 45, 90]
PITCHED_ROOF_HD_POINTS = [0, 0.25, 0.5, 1, 5]
PITCHED_UPWIND_SLOPE_1 = [
    [-0.7, -0.7, -0.5, -0.3, -0.2, -0.2,  0.0, 0.0, 0.0],
    [-0.7, -0.7, -0.5, -0.3, -0.2, -0.2,  0.0, 0.0, 0.0],
    [-0.9, -0.9, -0.7, -0.4, -0.3, -0.2, -0.2, 0.0, 0.0],
    [-1.3, -1.3, -1.0, -0.7, -0.5, -0.3, -0.2, 0.0, 0.0],
    [-1.3, -1.3, -1.0, -0.7, -0.5, -0.3, -0.2, 0.0, 0.0],
]
PITCHED_UPWIND_SLOPE_2 = [
    [-0.3, -0.3,  0.0,  0.2, 0.3, 0.4, 0.5, 0.10442095377604127, 0.10442095377604127],
    [-0.3, -0.3,  0.0,  0.2, 0.3, 0.4, 0.5, 0.10442095377604127, 0.10442095377604127],
    [-0.4, -0.4, -0.3,  0.0, 0.2, 0.3, 0.4, 0.10442095377604127, 0.10442095377604127],
    [-0.6, -0.6, -0.5, -0.3, 0.0, 0.2, 0.3, 0.10442095377604127, 0.10442095377604127],
    [-0.6, -0.6, -0.5, -0.3, 0.0, 0.2, 0.3, 0.10442095377604127, 0.10442095377604127],
]
PITCHED_DOWNWIND_MAX = [
    [-0.3, -0.3, -0.5, -0.6, -0.6, -0.6, -0.6, -0.6, -0.6],
    [-0.3, -0.3, -0.5, -0.6, -0.6, -0.6, -0.6, -0.6, -0.6],
    [-0.5, -0.5, -0.5, -0.6, -0.6, -0.6, -0.6, -0.6, -0.6],
    [-0.7, -0.7, -0.6, -0.6, -0.6, -0.6, -0.6, -0.6, -0.6],
    [-0.7, -0.7, -0.6, -0.6, -0.6, -0.6, -0.6, -0.6, -0.6],
]

# AS/NZS 1170.2:2021 Appendix B pitched free roofs, theta = 0 degrees,
# 0.25 <= h/d <= 1. Values are AS net pressure coefficients, so negative
# coefficients are converted to upward frame loads by the application layer.
FREE_ROOF_PITCH_POINTS = [15.0, 22.5, 30.0]
FREE_ROOF_CPN = {
    ("windward", "empty"): [(-0.3, 0.4), (-0.3, 0.6), (-0.3, 0.8)],
    ("windward", "blocked"): [(-1.2, -1.2), (-0.9, -0.9), (-0.5, -0.5)],
    ("leeward", "empty"): [(-0.4, 0.0), (-0.6, 0.0), (-0.7, 0.0)],
    ("leeward", "blocked"): [(-0.9, -0.9), (-1.1, -1.1), (-1.3, -1.3)],
}

CPI_BY_FRAME_TYPE: Dict[str, Tuple[float, float]] = {
    "Enclosed": (0.10, -0.20),
    "Roof Only": (0.0, 0.0),
    "3 Sided": (0.76, -0.11),
    "2 Sided": (0.40, -0.10),
    "1 Sided": (0.76, -0.11),
    # Cyclonic row in the workbook; useful as a later option, not exposed in FrameType.
    "Cyclonic": (0.60, -0.60),
    "No Wind": (0.0, 0.0),
}


@dataclass
class WindInputs:
    wind_region: str = "A5"
    importance_level: int = 1
    terrain_category: float = 2.0
    eave_height_m: float = 10.0
    roof_pitch_deg: float = 7.5
    building_width_m: float = 21.0
    building_length_m: float = 40.0
    bay_size_m: float = 8.0
    orientation: WindDirection = "E"
    frame_type: FrameType = "3 Sided"
    cpi_case: CpiCaseName = "Cpi +"
    left_wall_clad: bool = True
    right_wall_clad: bool = False
    front_wall_clad: bool = True
    back_wall_clad: bool = True
    left_canopy_length_m: float = 0.0
    right_canopy_length_m: float = 0.0
    reduction_factor: float = 1.0
    air_density: float = 1.2

    def validate(self) -> None:
        region = self.wind_region.upper()
        if region not in next(iter(REGIONAL_WIND_SPEEDS.values())):
            raise ValueError(f"Unsupported wind region: {self.wind_region}")
        if self.importance_level not in REGIONAL_WIND_SPEEDS:
            raise ValueError("importance_level must be 1, 2, 3 or 4")
        if self.terrain_category not in {1, 2, 2.5, 3, 4}:
            raise ValueError("terrain_category must be one of 1, 2, 2.5, 3 or 4")
        if self.eave_height_m <= 0 or self.building_width_m <= 0 or self.building_length_m <= 0 or self.bay_size_m <= 0:
            raise ValueError("eave height, building width, building length and bay size must be positive")
        if self.orientation not in DIRECTION_MULTIPLIERS:
            raise ValueError(f"Unsupported wind orientation: {self.orientation}")
        if self.frame_type not in CPI_BY_FRAME_TYPE:
            raise ValueError(f"Unsupported frame type: {self.frame_type}")
        if self.cpi_case not in CPI_CASE_OPTIONS:
            raise ValueError(f"Unsupported Cpi case: {self.cpi_case}")


@dataclass
class RoofPressureZone:
    name: str
    distance_from_eave_from_m: float
    distance_from_eave_to_m: float
    cpe_max: float
    cpe_min: float
    pressure_max_kn_m2: float
    pressure_min_kn_m2: float
    line_load_max_kn_m: float
    line_load_min_kn_m: float


@dataclass
class WindCalculationResult:
    inputs: WindInputs
    vr_ultimate_m_s: float
    vr_service_m_s: float
    direction_multiplier: float
    design_direction_multiplier: float
    h_m: float
    apex_height_m: float
    h_over_d: float
    d_over_b: float
    b_over_d: float
    mz_cat: float
    vsit_b_m_s: float
    vdes_b_m_s: float
    vdes_sls_m_s: float
    wu_kn_m2: float
    ws_kn_m2: float
    cpi_max: float
    cpi_min: float
    active_cpi: float
    leeward_wall_cpe: float
    roof_zones: List[RoofPressureZone] = field(default_factory=list)

    def end_wind_critical_roof_line_load_kn_m(self, use_max: bool, wind_pressure: str = "ultimate") -> float:
        """
        Return the critical end-wind roof line load for one portal frame.

        For wind from the building end, the AS/NZS roof Cpe bands run along the
        building length from the windward end edge. The 2D frame model only
        analyses one representative frame, so use the worst average Cpe over
        one tributary bay width anywhere along the building length.
        """
        if self.inputs.roof_pitch_deg >= 10.0:
            source_zone = next((zone for zone in self.roof_zones if zone.name == "Downwind rafter"), self.roof_zones[-1])
            return source_zone.line_load_max_kn_m if use_max else source_zone.line_load_min_kn_m

        cpe_rows = low_pitch_cpe_values(self.h_over_d)
        length_m = max(self.inputs.building_length_m, 1e-9)
        bay_m = min(max(self.inputs.bay_size_m, 1e-9), length_m)
        pressure_scale = wind_pressure_scale(self, wind_pressure)

        boundaries = {0.0, length_m, max(length_m - bay_m, 0.0)}
        for _, from_h, to_h, _, _ in cpe_rows:
            for value in (from_h * self.h_m, to_h * self.h_m):
                clamped = min(max(value, 0.0), length_m)
                boundaries.add(clamped)
                boundaries.add(min(max(clamped - bay_m, 0.0), max(length_m - bay_m, 0.0)))
        candidates = sorted(boundaries)

        def average_cpe(start_m):
            end_m = min(start_m + bay_m, length_m)
            total = 0.0
            for _, from_h, to_h, cpe_max, cpe_min in cpe_rows:
                a = min(max(from_h * self.h_m, 0.0), length_m)
                b = min(max(to_h * self.h_m, 0.0), length_m)
                if b <= a:
                    continue
                overlap = max(0.0, min(end_m, b) - max(start_m, a))
                cpe = cpe_max if use_max else cpe_min
                total += cpe * overlap
            return total / max(end_m - start_m, 1e-9)

        critical_cpe = max((average_cpe(start) for start in candidates), key=lambda value: abs(value))

        zone_area_m2 = bay_m * self.inputs.bay_size_m
        pressure_factor = max(area_reduction_factor(self.inputs, "roof", zone_area_m2) * action_combination_factors(self.inputs)[0], 0.8)
        pressure_kn_m2 = (-critical_cpe) * self.wu_kn_m2 * pressure_scale * pressure_factor
        return pressure_kn_m2 * self.inputs.bay_size_m

    def case_roof_strips(self, case: WindCaseName) -> List[Tuple[float, float, float]]:
        """
        Return roof load strips as (from_m, to_m, line_load_kn_m).

        Case convention:
        - Left +  = wind zones measured in from the left eave, using the + / max strip values.
        - Left -  = wind zones measured in from the left eave, using the - / min strip values.
        - Right + = wind zones measured in from the right eave, mirrored onto the model, using + / max values.
        - Right - = wind zones measured in from the right eave, mirrored onto the model, using - / min values.

        Distances returned here are measured from the model's left eave along the roof slope.
        """
        case = str(case).strip()
        if case not in CPE_CASE_OPTIONS:
            raise ValueError(f"Unsupported Cpe wind case: {case}. Use one of: {', '.join(CPE_CASE_OPTIONS)}")

        use_max = case.endswith("+")
        total_roof_length = self.roof_slope_length_m
        if case.startswith("End"):
            q = self.end_wind_critical_roof_line_load_kn_m(use_max)
            return [(0.0, total_roof_length, q)]

        from_right = case.startswith("Right")

        strips = []
        for zone in self.roof_zones:
            q = zone.line_load_max_kn_m if use_max else zone.line_load_min_kn_m
            # Clamp each zone to the analysed roof length before mirroring.
            # This avoids negative mirrored extents where h-zone tables extend past
            # the actual roof length.
            a = max(0.0, min(zone.distance_from_eave_from_m, total_roof_length))
            b = max(0.0, min(zone.distance_from_eave_to_m, total_roof_length))
            if b <= a:
                continue
            if from_right:
                a, b = total_roof_length - b, total_roof_length - a
            strips.append((max(0.0, a), min(total_roof_length, b), q))
        strips.sort(key=lambda item: item[0])
        return strips

    @property
    def roof_slope_length_m(self) -> float:
        half_span = self.inputs.building_width_m / 2.0
        half_rise = math.tan(math.radians(self.inputs.roof_pitch_deg)) * half_span
        return (
            2.0 * math.hypot(half_span, half_rise)
            + max(self.inputs.left_canopy_length_m, 0.0)
            + max(self.inputs.right_canopy_length_m, 0.0)
        )

    def summary_lines(self) -> List[str]:
        lines = ["WIND LOAD INPUTS / RESULTS"]
        lines.append(f"Region / IL / TC : {self.inputs.wind_region} / {self.inputs.importance_level} / {self.inputs.terrain_category}")
        lines.append(f"Orientation       : {self.inputs.orientation}")
        lines.append(f"Frame type        : {derived_frame_type_from_walls(self.inputs)}")
        cladding = wall_cladding(self.inputs)
        lines.append(
            "Wall enclosure   : "
            f"L {'clad' if cladding['left'] else 'open'}, "
            f"R {'clad' if cladding['right'] else 'open'}, "
            f"F {'clad' if cladding['front'] else 'open'}, "
            f"B {'clad' if cladding['back'] else 'open'}"
        )
        lines.append(f"Canopies L/R      : {self.inputs.left_canopy_length_m:.3f} / {self.inputs.right_canopy_length_m:.3f} m")
        lines.append(f"Vr ultimate       : {self.vr_ultimate_m_s:.2f} m/s")
        lines.append(f"Md selected/design: {self.direction_multiplier:.3f} / {self.design_direction_multiplier:.3f}")
        lines.append(f"Mz,cat            : {self.mz_cat:.3f}")
        lines.append(f"Vdes,b            : {self.vdes_b_m_s:.2f} m/s")
        lines.append(f"Vdes,sls          : {self.vdes_sls_m_s:.2f} m/s")
        lines.append(f"Wu                : {self.wu_kn_m2:.3f} kPa")
        lines.append(f"Ws                : {self.ws_kn_m2:.3f} kPa")
        lines.append(f"Cpi max/min       : {self.cpi_max:.3f} / {self.cpi_min:.3f}")
        kce, kci = action_combination_factors(self, self.active_cpi)
        roof_area = self.roof_slope_length_m * self.inputs.bay_size_m
        wall_area = self.inputs.eave_height_m * self.inputs.bay_size_m
        lines.append(
            f"Ka roof / WW / LW : {area_reduction_factor(self, 'roof', roof_area):.3f} / "
            f"{area_reduction_factor(self, 'windward_wall', wall_area):.3f} / "
            f"{area_reduction_factor(self, 'leeward_wall', wall_area):.3f}"
        )
        if open_wall_count(self.inputs) > 0:
            opening_area = self.inputs.eave_height_m * self.inputs.building_length_m
            lines.append(
                f"Opening Ka/Kv      : WW {opening_area_reduction_factor('windward_wall', opening_area):.3f}, "
                f"LW {opening_area_reduction_factor('leeward_wall', opening_area):.3f} / "
                f"{open_area_volume_factor(self):.3f}"
            )
        lines.append(f"Kce / Kci         : {kce:.3f} / {kci:.3f}")
        lines.append(f"Manual pressure f : {self.inputs.reduction_factor:.3f}")
        lines.append(f"Wall Cpe WW/LW/S  : +0.700 / {self.leeward_wall_cpe:+.3f} / {side_wall_cpe_first_frame(self):+.3f}")
        lines.append(f"Wall Cpe q WW/LW  : {wall_cpe_line_load_kn_m(self, 0.7):.3f} / {wall_cpe_line_load_kn_m(self, self.leeward_wall_cpe):.3f} kN/m")
        lines.append(f"Cpi line q +/-    : {wall_cpi_line_load_kn_m(self, self.cpi_max):.3f} / {wall_cpi_line_load_kn_m(self, self.cpi_min):.3f} kN/m")
        if self.inputs.roof_pitch_deg >= 10.0:
            lines.append("Roof Cpe table    : >=10° pitched-roof table")
        else:
            lines.append("Roof Cpe table    : <10° strip-zone table")
        for zone in self.roof_zones:
            lines.append(
                f"{zone.name:<10}: {zone.distance_from_eave_from_m:>5.2f}-{zone.distance_from_eave_to_m:>5.2f} m | "
                f"q+ {zone.line_load_max_kn_m:>7.3f} kN/m | q- {zone.line_load_min_kn_m:>7.3f} kN/m"
            )
        return lines


def lerp(x: float, x1: float, y1: float, x2: float, y2: float) -> float:
    if abs(x2 - x1) < 1e-12:
        return y1
    return y1 + (x - x1) * (y2 - y1) / (x2 - x1)


def interpolate_table(x: float, table: Dict[float, float]) -> float:
    points = sorted(table.items())
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x1, y1), (x2, y2) in zip(points[:-1], points[1:]):
        if x1 <= x <= x2:
            return lerp(x, x1, y1, x2, y2)
    return points[-1][1]


def interpolate_points(x: float, points: Sequence[Tuple[float, float]]) -> float:
    table = sorted(points)
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for (x1, y1), (x2, y2) in zip(table[:-1], table[1:]):
        if x1 <= x <= x2:
            return lerp(x, x1, y1, x2, y2)
    return table[-1][1]


def wall_cladding(inputs) -> Dict[str, bool]:
    if inputs.frame_type == "No Wind":
        return {"left": False, "right": False, "front": False, "back": False}
    return {
        "left": bool(getattr(inputs, "left_wall_clad", True)),
        "right": bool(getattr(inputs, "right_wall_clad", True)),
        "front": bool(getattr(inputs, "front_wall_clad", True)),
        "back": bool(getattr(inputs, "back_wall_clad", True)),
    }


def open_wall_count(inputs) -> int:
    return sum(1 for clad in wall_cladding(inputs).values() if not clad)


def derived_frame_type_from_walls(inputs) -> str:
    if inputs.frame_type == "No Wind":
        return "No Wind"
    clad_count = sum(1 for clad in wall_cladding(inputs).values() if clad)
    if clad_count == 4:
        return "Enclosed"
    if clad_count == 0:
        return "Roof Only"
    if clad_count == 3:
        return "3 Sided"
    if clad_count == 2:
        return "2 Sided"
    return "1 Sided"


def wall_surface_for_cpe_case(cpe_case: Optional[CpeCaseName], wall: str) -> str:
    case = str(cpe_case or "").strip()
    if case.startswith("Left"):
        if wall == "left":
            return "windward_wall"
        if wall == "right":
            return "leeward_wall"
        return "side_wall"
    if case.startswith("Right"):
        if wall == "right":
            return "windward_wall"
        if wall == "left":
            return "leeward_wall"
        return "side_wall"
    if case.startswith("End"):
        if wall == "front":
            return "windward_wall"
        if wall == "back":
            return "leeward_wall"
        return "side_wall"
    return "side_wall"


def wall_cpe_for_surface(result, surface: str) -> float:
    if surface == "windward_wall":
        return 0.7
    if surface == "leeward_wall":
        return result.leeward_wall_cpe
    if surface == "side_wall":
        return side_wall_cpe_first_frame(result)
    return -0.65


def side_wall_cpe_first_frame(result) -> float:
    """Table 5.2(C) side-wall Cpe averaged over the first analysed bay."""
    h_m = max(getattr(result, "h_m", 0.0), 1e-9)
    inputs = getattr(result, "inputs", result)
    length_m = max(getattr(inputs, "building_length_m", 0.0), 1e-9)
    bay_m = min(max(getattr(inputs, "bay_size_m", 0.0), 1e-9), length_m)
    total = 0.0
    for from_h, to_h, cpe in SIDE_WALL_CPE_BANDS:
        a = min(max(from_h * h_m, 0.0), length_m)
        b = length_m if math.isinf(to_h) else min(max(to_h * h_m, 0.0), length_m)
        if b <= a:
            continue
        overlap = max(0.0, min(bay_m, b) - max(0.0, a))
        total += cpe * overlap
    return total / max(bay_m, 1e-9)


def local_pressure_dimension_a(result_or_inputs) -> float:
    """Figure 5.3 local-pressure dimension a, using average roof height h."""
    inputs = getattr(result_or_inputs, "inputs", result_or_inputs)
    h_m = getattr(result_or_inputs, "h_m", None)
    if h_m is None:
        h_m = inputs.eave_height_m + math.tan(math.radians(inputs.roof_pitch_deg)) * inputs.building_width_m * 0.25
    return max(min(0.2 * inputs.building_width_m, 0.2 * inputs.building_length_m, h_m), 0.0)


def roof_local_pressure_factor(result, tributary_area_m2: float, proximity_m: float, roof_location: str = "edge") -> float:
    """Table 5.6 roof local pressure factor for cladding/support checks."""
    a_m = local_pressure_dimension_a(result)
    if a_m <= 1e-9:
        return 1.0
    area = max(tributary_area_m2, 0.0)
    proximity = max(proximity_m, 0.0)
    factor = 1.0
    if area <= a_m * a_m and proximity < a_m:
        factor = max(factor, 1.5)
    if area <= 0.25 * a_m * a_m and proximity < 0.5 * a_m:
        factor = max(factor, 2.0)
    # Corner factors need a two-edge local region. The frame-wide purlin check
    # uses one-span line loads, so it deliberately does not smear RC1/RC2 over
    # the whole purlin span.
    return factor


def area_reduction_factor(result_or_inputs, surface: str, tributary_area_m2: float) -> float:
    """AS/NZS 1170.2 Table 5.4 area reduction factor for main-frame roof/wall pressures."""
    frame_type = getattr(result_or_inputs, "frame_type", None)
    if hasattr(result_or_inputs, "inputs"):
        frame_type = result_or_inputs.inputs.frame_type
    inputs = getattr(result_or_inputs, "inputs", result_or_inputs)
    if derived_frame_type_from_walls(inputs) != "Enclosed":
        return 1.0
    if surface not in AREA_REDUCTION_TABLE:
        return 1.0
    return interpolate_points(max(tributary_area_m2, 0.0), AREA_REDUCTION_TABLE[surface])


def opening_area_reduction_factor(surface: str, opening_area_m2: float) -> float:
    """Ka for dominant-opening Cpi, using opening area as the tributary area."""
    if surface not in AREA_REDUCTION_TABLE:
        return 1.0
    return interpolate_points(max(opening_area_m2, 0.0), AREA_REDUCTION_TABLE[surface])


def action_combination_factors(result_or_inputs, cpi: float = 0.0) -> Tuple[float, float]:
    """Return (Kce, Kci) for the current whole-frame external/internal action combination."""
    inputs = getattr(result_or_inputs, "inputs", result_or_inputs)
    frame_type = derived_frame_type_from_walls(inputs)
    if frame_type in {"No Wind"}:
        return 1.0, 1.0
    if frame_type == "Roof Only":
        return 0.9, 1.0
    kce = 0.8
    # AS/NZS 1170.2 treats small internal pressure as not an effective surface.
    kci = 0.8 if abs(cpi) >= 0.4 else 1.0
    return kce, kci


def external_pressure_factor(result, surface: str, tributary_area_m2: float) -> float:
    ka = area_reduction_factor(result, surface, tributary_area_m2)
    kce, _ = action_combination_factors(result)
    return max(ka * kce, 0.8)


def internal_pressure_factor(result, cpi: float) -> float:
    _, kci = action_combination_factors(result, cpi)
    return kci


def open_area_volume_factor(result_or_inputs) -> float:
    """Simplified Kv for dominant open-side wall openings."""
    inputs = getattr(result_or_inputs, "inputs", result_or_inputs)
    return 1.085 if open_wall_count(inputs) > 0 else 1.0


def dominant_opening_cpi(result, cpe_case: CpeCaseName) -> float:
    """Cpi for an open-sided shed treated as a dominant wall opening, ratio >= 6."""
    openings = [wall for wall, clad in wall_cladding(result.inputs).items() if not clad]
    if len(openings) != 1:
        return result.active_cpi
    open_wall = openings[0]
    if open_wall in {"left", "right"}:
        opening_width_m = result.inputs.building_length_m
    else:
        opening_width_m = result.inputs.building_width_m
    opening_area_m2 = result.inputs.eave_height_m * opening_width_m
    local_pressure_factor = 1.0
    volume_factor = open_area_volume_factor(result)
    surface = wall_surface_for_cpe_case(cpe_case, open_wall)
    cpe = wall_cpe_for_surface(result, surface)
    return opening_area_reduction_factor(surface, opening_area_m2) * local_pressure_factor * volume_factor * cpe


def terrain_multiplier(height_m: float, terrain_category: float) -> float:
    column = {h: row[terrain_category] for h, row in TERRAIN_MULTIPLIER_TABLE.items()}
    return interpolate_table(height_m, column)


def low_pitch_cpe_values(h_over_d: float) -> List[Tuple[str, float, float, float, float]]:
    """Return tuples: name, from_h, to_h, cpe_max, cpe_min."""
    values = []
    h_d = min(max(h_over_d, 0.5), 1.0)
    for name, from_h, to_h, max_at_05, max_at_10, min_at_05, min_at_10 in LOW_PITCH_ROOF_CPE:
        cpe_max = lerp(h_d, 0.5, max_at_05, 1.0, max_at_10)
        cpe_min = lerp(h_d, 0.5, min_at_05, 1.0, min_at_10)
        values.append((name, from_h, to_h, cpe_max, cpe_min))
    return values


def bracket_indices(value: float, points: Sequence[float]) -> Tuple[int, int]:
    """Return lower/upper indices for clamped interpolation."""
    if value <= points[0]:
        return 0, 0
    if value >= points[-1]:
        last = len(points) - 1
        return last, last
    for i, (a, b) in enumerate(zip(points[:-1], points[1:])):
        if a <= value <= b:
            return i, i + 1
    last = len(points) - 1
    return last, last


def bilinear_grid_value(x: float, y: float, x_points: Sequence[float], y_points: Sequence[float], values: Sequence[Sequence[float]]) -> float:
    """Bilinear interpolation with clamping at the table edges."""
    x1_i, x2_i = bracket_indices(x, x_points)
    y1_i, y2_i = bracket_indices(y, y_points)
    x1, x2 = x_points[x1_i], x_points[x2_i]
    y1, y2 = y_points[y1_i], y_points[y2_i]
    q11 = values[y1_i][x1_i]
    q21 = values[y1_i][x2_i]
    q12 = values[y2_i][x1_i]
    q22 = values[y2_i][x2_i]
    v1 = lerp(x, x1, q11, x2, q21)
    v2 = lerp(x, x1, q12, x2, q22)
    return lerp(y, y1, v1, y2, v2)


def pitched_roof_cpe_values(roof_pitch_deg: float, h_over_d: float, roof_slope_length_m: float) -> List[Tuple[str, float, float, float, float]]:
    """Return pitch >=10° zones as name, from_m, to_m, cpe_plus, cpe_minus.

    The zones are measured from the windward eave along the roof slope.
    Right-hand wind cases are mirrored later by case_roof_strips().
    """
    h_d = min(max(h_over_d, PITCHED_ROOF_HD_POINTS[0]), PITCHED_ROOF_HD_POINTS[-1])
    pitch = min(max(roof_pitch_deg, PITCHED_ROOF_PITCH_POINTS[0]), PITCHED_ROOF_PITCH_POINTS[-1])
    upwind_plus = bilinear_grid_value(pitch, h_d, PITCHED_ROOF_PITCH_POINTS, PITCHED_ROOF_HD_POINTS, PITCHED_UPWIND_SLOPE_1)
    upwind_minus = bilinear_grid_value(pitch, h_d, PITCHED_ROOF_PITCH_POINTS, PITCHED_ROOF_HD_POINTS, PITCHED_UPWIND_SLOPE_2)
    downwind_plus = bilinear_grid_value(pitch, h_d, PITCHED_ROOF_PITCH_POINTS, PITCHED_ROOF_HD_POINTS, PITCHED_DOWNWIND_MAX)
    downwind_minus = 0.0
    half = roof_slope_length_m / 2.0
    return [
        ("Upwind rafter", 0.0, half, upwind_plus, upwind_minus),
        ("Downwind rafter", half, roof_slope_length_m, downwind_plus, downwind_minus),
    ]


def free_roof_cpn(roof_pitch_deg: float, h_over_d: float, roof_part: str, underside: str, use_uplift: bool) -> float:
    """
    Interpolate the Appendix B pitched free-roof net coefficient for theta = 0.

    The table is limited to 0.25 <= h/d <= 1; h/d is clamped to that range
    because the available coefficients are constant over that range.
    Returns the AS coefficient sign, where negative is suction/uplift.
    """
    _ = min(max(h_over_d, 0.25), 1.0)
    part = str(roof_part).strip().lower()
    under = str(underside).strip().lower()
    if part not in {"windward", "leeward"}:
        raise ValueError("roof_part must be 'windward' or 'leeward'")
    if under not in {"empty", "blocked"}:
        raise ValueError("underside must be 'empty' or 'blocked'")
    values = FREE_ROOF_CPN[(part, under)]
    index = 0 if use_uplift else 1
    pitch = min(max(roof_pitch_deg, FREE_ROOF_PITCH_POINTS[0]), FREE_ROOF_PITCH_POINTS[-1])
    return interpolate_points(pitch, [(p, pair[index]) for p, pair in zip(FREE_ROOF_PITCH_POINTS, values)])



LEEWARD_WALL_CPE_TABLE: Dict[float, Dict[float, float]] = {
    0:  {1: -0.5, 2: -0.3, 4: -0.2},
    5:  {1: -0.5, 2: -0.3, 4: -0.2},
    10: {1: -0.3, 2: -0.3, 4: -0.3},
    15: {1: -0.3, 2: -0.3, 4: -0.3},
    20: {1: -0.4, 2: -0.4, 4: -0.4},
    25: {1: -0.5, 2: -0.5, 4: -0.5},
}


def leeward_wall_cpe(d_over_b: float, roof_pitch_deg: float) -> float:
    """Bilinear interpolation of the leeward wall Cpe table from the workbook."""
    pitch_keys = sorted(LEEWARD_WALL_CPE_TABLE.keys())
    ratio_keys = sorted(next(iter(LEEWARD_WALL_CPE_TABLE.values())).keys())

    def bracket(value, keys):
        if value <= keys[0]:
            return keys[0], keys[0]
        if value >= keys[-1]:
            return keys[-1], keys[-1]
        for a, b in zip(keys[:-1], keys[1:]):
            if a <= value <= b:
                return a, b
        return keys[-1], keys[-1]

    p1, p2 = bracket(roof_pitch_deg, pitch_keys)
    r1, r2 = bracket(d_over_b, ratio_keys)
    q11 = LEEWARD_WALL_CPE_TABLE[p1][r1]
    q21 = LEEWARD_WALL_CPE_TABLE[p1][r2]
    q12 = LEEWARD_WALL_CPE_TABLE[p2][r1]
    q22 = LEEWARD_WALL_CPE_TABLE[p2][r2]
    v1 = lerp(d_over_b, r1, q11, r2, q21)
    v2 = lerp(d_over_b, r1, q12, r2, q22)
    return lerp(roof_pitch_deg, p1, v1, p2, v2)

def calculate_wind(inputs: WindInputs) -> WindCalculationResult:
    inputs.validate()
    region = inputs.wind_region.upper()

    vr = REGIONAL_WIND_SPEEDS[inputs.importance_level][region]
    vr_sls = SERVICEABILITY_WIND_SPEEDS[region]
    md = DIRECTION_MULTIPLIERS[inputs.orientation][region]
    direction_index = DIRECTION_ORDER.index(inputs.orientation)
    design_directions = [
        DIRECTION_ORDER[(direction_index - 1) % len(DIRECTION_ORDER)],
        inputs.orientation,
        DIRECTION_ORDER[(direction_index + 1) % len(DIRECTION_ORDER)],
    ]
    m_design = max(DIRECTION_MULTIPLIERS[direction][region] for direction in design_directions)

    h = inputs.eave_height_m + math.tan(math.radians(inputs.roof_pitch_deg)) * inputs.building_width_m * 0.25
    apex = inputs.eave_height_m + math.tan(math.radians(inputs.roof_pitch_deg)) * inputs.building_width_m * 0.5
    mz = terrain_multiplier(h, inputs.terrain_category)

    vsit = vr * md * mz
    vdes = vr * m_design * mz
    vdes_sls = vr_sls * m_design * mz

    wu = inputs.reduction_factor * 0.001 * 0.5 * inputs.air_density * vdes**2
    ws = inputs.reduction_factor * 0.001 * 0.5 * inputs.air_density * vdes_sls**2

    d_over_b = inputs.building_width_m / inputs.building_length_m
    b_over_d = inputs.building_length_m / inputs.building_width_m
    h_over_d = h / inputs.building_width_m
    derived_frame_type = derived_frame_type_from_walls(inputs)
    cpi_max, cpi_min = CPI_BY_FRAME_TYPE[derived_frame_type]
    active_cpi = cpi_max if inputs.cpi_case == "Cpi +" else cpi_min
    lw_cpe = leeward_wall_cpe(d_over_b, inputs.roof_pitch_deg)

    zones: List[RoofPressureZone] = []
    roof_slope_length = (
        2.0 * math.hypot(inputs.building_width_m / 2.0, apex - inputs.eave_height_m)
        + max(inputs.left_canopy_length_m, 0.0)
        + max(inputs.right_canopy_length_m, 0.0)
    )
    if inputs.roof_pitch_deg >= 10.0:
        roof_cpe_rows = pitched_roof_cpe_values(inputs.roof_pitch_deg, h_over_d, roof_slope_length)
        distances_are_metres = True
    else:
        roof_cpe_rows = low_pitch_cpe_values(h_over_d)
        distances_are_metres = False

    for name, start_basis, end_basis, cpe_max, cpe_min in roof_cpe_rows:
        if distances_are_metres:
            start = start_basis
            end = end_basis
        else:
            start = start_basis * h
            end = end_basis * h
        # Frame sign convention: positive roof wind acts upward in global Y.
        # Cpe and Cpi are deliberately kept as separate load cases so they can
        # be combined later using load combinations.
        zone_area_m2 = max(end - start, 0.0) * inputs.bay_size_m
        ka = area_reduction_factor(inputs, "roof", zone_area_m2)
        kce, _ = action_combination_factors(inputs)
        pressure_factor = max(ka * kce, 0.8)
        pressure_max = (-cpe_max) * wu * pressure_factor
        pressure_min = (-cpe_min) * wu * pressure_factor
        line_max = pressure_max * inputs.bay_size_m
        line_min = pressure_min * inputs.bay_size_m
        zones.append(
            RoofPressureZone(
                name=name,
                distance_from_eave_from_m=start,
                distance_from_eave_to_m=end,
                cpe_max=cpe_max,
                cpe_min=cpe_min,
                pressure_max_kn_m2=pressure_max,
                pressure_min_kn_m2=pressure_min,
                line_load_max_kn_m=line_max,
                line_load_min_kn_m=line_min,
            )
        )

    return WindCalculationResult(
        inputs=inputs,
        vr_ultimate_m_s=vr,
        vr_service_m_s=vr_sls,
        direction_multiplier=md,
        design_direction_multiplier=m_design,
        h_m=h,
        apex_height_m=apex,
        h_over_d=h_over_d,
        d_over_b=d_over_b,
        b_over_d=b_over_d,
        mz_cat=mz,
        vsit_b_m_s=vsit,
        vdes_b_m_s=vdes,
        vdes_sls_m_s=vdes_sls,
        wu_kn_m2=wu,
        ws_kn_m2=ws,
        cpi_max=cpi_max,
        cpi_min=cpi_min,
        active_cpi=active_cpi,
        leeward_wall_cpe=lw_cpe,
        roof_zones=zones,
    )


def wind_pressure_scale(result: WindCalculationResult, wind_pressure: str = "ultimate") -> float:
    if wind_pressure == "ultimate":
        return 1.0
    if wind_pressure == "serviceability":
        if abs(result.wu_kn_m2) < 1e-12:
            return 0.0
        return result.ws_kn_m2 / result.wu_kn_m2
    raise ValueError("wind_pressure must be 'ultimate' or 'serviceability'")


def apply_roof_wind_case_to_top_nodes(structure, top_nodes: Sequence, result: WindCalculationResult, case: WindCaseName, wind_pressure: str = "ultimate") -> List[LoadArrow]:
    """
    Apply a selected wind roof strip case to the portal frame top nodes.

    The current implementation converts each top chord panel to a single average
    vertical line load using the overlap of each wind strip with the panel's
    distance along the roof from the left eave. Positive strip values are applied
    upward in global Y, matching the spreadsheet's uplift-positive output.
    """
    scale = wind_pressure_scale(result, wind_pressure)
    strips = result.case_roof_strips(case)
    arrows: List[LoadArrow] = []

    cumulative = [0.0]
    for n1, n2 in zip(top_nodes[:-1], top_nodes[1:]):
        dx = (n2.x - n1.x) / 1000
        dy = (n2.y - n1.y) / 1000
        cumulative.append(cumulative[-1] + math.hypot(dx, dy))

    for i, (n1, n2) in enumerate(zip(top_nodes[:-1], top_nodes[1:])):
        s1 = cumulative[i]
        s2 = cumulative[i + 1]
        panel_len = max(s2 - s1, 1e-9)
        total_kn = 0.0
        for a, b, q_kn_m in strips:
            overlap = max(0.0, min(s2, b) - max(s1, a))
            total_kn += q_kn_m * scale * overlap
        total_n = total_kn * 1000.0
        structure.add_load(n1.uy, total_n / 2)
        structure.add_load(n2.uy, total_n / 2)
        mx = (n1.x + n2.x) / 2
        my = (n1.y + n2.y) / 2
        arrows.append((mx, my + 800, 0, total_n))
    return arrows



def active_cpi_for_case(result: WindCalculationResult, case: CpiCaseName, cpe_case: Optional[CpeCaseName] = None) -> float:
    if open_wall_count(result.inputs) == 1 and cpe_case is not None:
        return dominant_opening_cpi(result, cpe_case)
    case = str(effective_cpi_case_for_wind(result, case, cpe_case)).strip()
    if case == "Cpi +":
        return result.cpi_max
    if case == "Cpi -":
        return result.cpi_min
    raise ValueError(f"Unsupported Cpi case: {case}. Use one of: {', '.join(CPI_CASE_OPTIONS)}")


def apply_roof_cpi_case_to_top_nodes(structure, top_nodes: Sequence, result: WindCalculationResult, case: CpiCaseName, wind_pressure: str = "ultimate", cpe_case: Optional[CpeCaseName] = None) -> List[LoadArrow]:
    """Apply Cpi-only roof pressure as a separate internal-pressure load case."""
    cpi = active_cpi_for_case(result, case, cpe_case)
    return apply_roof_cpi_to_top_nodes(structure, top_nodes, result, cpi, wind_pressure)


def apply_roof_cpi_to_top_nodes(structure, top_nodes: Sequence, result: WindCalculationResult, cpi: float, wind_pressure: str = "ultimate") -> List[LoadArrow]:
    """Apply an explicit Cpi coefficient to roof/canopy top nodes."""
    wind_pressure_kn_m2 = result.wu_kn_m2 * wind_pressure_scale(result, wind_pressure)
    line_kn_m = cpi * wind_pressure_kn_m2 * internal_pressure_factor(result, cpi) * result.inputs.bay_size_m
    arrows: List[LoadArrow] = []

    for n1, n2 in zip(top_nodes[:-1], top_nodes[1:]):
        dx = (n2.x - n1.x) / 1000
        dy = (n2.y - n1.y) / 1000
        panel_len_m = math.hypot(dx, dy)
        total_n = line_kn_m * panel_len_m * 1000.0
        structure.add_load(n1.uy, total_n / 2)
        structure.add_load(n2.uy, total_n / 2)
        mx = (n1.x + n2.x) / 2
        my = (n1.y + n2.y) / 2
        arrows.append((mx, my + 800, 0, total_n))
    return arrows


def adjacent_wall_cpe_for_side(result: WindCalculationResult, cpe_case: CpeCaseName, side: str) -> float:
    """Wall Cpe beneath a left/right canopy for the current wind side."""
    side = str(side).strip().lower()
    if str(cpe_case).startswith("End"):
        return -0.65
    left_wind = str(cpe_case).startswith("Left")
    if side == "left":
        return 0.7 if left_wind else result.leeward_wall_cpe
    if side == "right":
        return result.leeward_wall_cpe if left_wind else 0.7
    raise ValueError("side must be 'left' or 'right'")

def open_sided_frame_type(frame_type: str) -> bool:
    return frame_type in {"3 Sided", "2 Sided", "1 Sided"}


def effective_cpi_case_for_wind(result: WindCalculationResult, case: CpiCaseName, cpe_case: Optional[CpeCaseName] = None) -> CpiCaseName:
    """Return the Cpi case to use after open-side wind-direction rules."""
    if open_wall_count(result.inputs) == 1 and cpe_case is not None:
        cpi = dominant_opening_cpi(result, cpe_case)
        return "Cpi +" if cpi >= 0.0 else "Cpi -"
    return case


def wall_cpe_line_load_kn_m(result: WindCalculationResult, cpe: float, wind_pressure: str = "ultimate", surface: str = "windward_wall", tributary_area_m2: Optional[float] = None) -> float:
    """Return wall Cpe-only line load magnitude in kN/m for one analysed frame."""
    if tributary_area_m2 is None:
        tributary_area_m2 = result.inputs.bay_size_m * result.h_m
    pressure_factor = external_pressure_factor(result, surface, tributary_area_m2)
    return abs(cpe) * result.wu_kn_m2 * wind_pressure_scale(result, wind_pressure) * pressure_factor * result.inputs.bay_size_m


def wall_cpi_line_load_kn_m(result: WindCalculationResult, cpi: float, wind_pressure: str = "ultimate") -> float:
    """Return wall Cpi-only line load magnitude in kN/m for one analysed frame."""
    return abs(cpi) * result.wu_kn_m2 * wind_pressure_scale(result, wind_pressure) * internal_pressure_factor(result, cpi) * result.inputs.bay_size_m


def apply_column_wind_cpe_loads(structure, left_base, left_top, right_base, right_top, result: WindCalculationResult, case: CpeCaseName, wind_pressure: str = "ultimate") -> List[LoadArrow]:
    """
    Apply Cpe-only wall wind pressure to frame columns.

    For analysis the load is still applied as equivalent nodal loads at the
    column ends.  For plotting, multiple arrows are returned along the column
    height so the load diagram reads as a distributed column load rather than a
    single horizontal line.

    For 1/2/3-sided open sheds, the clad side is assumed to be the left column
    only, so the right column receives no wall Cpe load. Roof-only and
    no-wind frames receive no column load.
    """
    frame_type = result.inputs.frame_type
    if frame_type in {"Roof Only", "No Wind"}:
        return []

    left_wind = str(case).startswith("Left")
    windward_cpe = 0.7
    leeward_cpe = result.leeward_wall_cpe
    arrows: List[LoadArrow] = []

    def add_column_load(base, top, line_kn_m: float, direction: float, arrow_count: int = 5) -> None:
        height_m = abs(top.y - base.y) / 1000.0
        total_n = line_kn_m * height_m * 1000.0 * direction
        structure.add_load(base.ux, total_n / 2.0)
        structure.add_load(top.ux, total_n / 2.0)

        # Plot as distributed arrows. The arrows sum to the applied resultant.
        count = max(2, arrow_count)
        for i in range(count):
            t = (i + 0.5) / count
            x = base.x + (top.x - base.x) * t
            y = base.y + (top.y - base.y) * t
            arrows.append((x, y, total_n / count, 0.0))

    wall_area_m2 = result.inputs.bay_size_m * result.inputs.eave_height_m
    ww_line = wall_cpe_line_load_kn_m(result, windward_cpe, wind_pressure, "windward_wall", wall_area_m2)
    lw_line = wall_cpe_line_load_kn_m(result, leeward_cpe, wind_pressure, "leeward_wall", wall_area_m2)

    if open_sided_frame_type(frame_type):
        # User rule: 1/2/3-sided cases are always clad to the left, so the right column is unloaded.
        if left_wind:
            add_column_load(left_base, left_top, ww_line, +1.0)
        else:
            add_column_load(left_base, left_top, lw_line, -1.0)
        return arrows

    if left_wind:
        add_column_load(left_base, left_top, ww_line, +1.0)
        add_column_load(right_base, right_top, lw_line, +1.0)
    else:
        add_column_load(right_base, right_top, ww_line, -1.0)
        add_column_load(left_base, left_top, lw_line, -1.0)
    return arrows


def apply_column_wind_cpi_loads(structure, left_base, left_top, right_base, right_top, result: WindCalculationResult, case: CpiCaseName, wind_pressure: str = "ultimate", cpe_case: Optional[CpeCaseName] = None) -> List[LoadArrow]:
    """Apply Cpi-only wall pressure as a separate internal-pressure column load case."""
    frame_type = result.inputs.frame_type
    if frame_type in {"Roof Only", "No Wind"}:
        return []

    cpi = active_cpi_for_case(result, case, cpe_case)
    line_kn_m = wall_cpi_line_load_kn_m(result, cpi, wind_pressure)
    if abs(line_kn_m) < 1e-12:
        return []

    # Positive Cpi pushes outwards. Negative Cpi pulls inwards.
    left_direction = -1.0 if cpi > 0 else +1.0
    right_direction = +1.0 if cpi > 0 else -1.0
    arrows: List[LoadArrow] = []

    def add_column_load(base, top, direction: float, arrow_count: int = 5) -> None:
        height_m = abs(top.y - base.y) / 1000.0
        total_n = line_kn_m * height_m * 1000.0 * direction
        structure.add_load(base.ux, total_n / 2.0)
        structure.add_load(top.ux, total_n / 2.0)
        count = max(2, arrow_count)
        for i in range(count):
            t = (i + 0.5) / count
            x = base.x + (top.x - base.x) * t
            y = base.y + (top.y - base.y) * t
            arrows.append((x, y, total_n / count, 0.0))

    add_column_load(left_base, left_top, left_direction)
    if not open_sided_frame_type(frame_type):
        add_column_load(right_base, right_top, right_direction)
    return arrows


if __name__ == "__main__":
    # Smoke test using the workbook's visible default inputs.
    res = calculate_wind(WindInputs())
    for line in res.summary_lines():
        print(line)
