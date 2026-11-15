"""Constants, lookup tables and small pure helpers shared by the 2D frame engine."""

import copy
import math
import os
import re

from portalcalc.resources import data_path


def plain_copy(value):
    """Deep copy of plain JSON-like data (dict/list/scalars), much faster than
    copy.deepcopy. Any other object is passed to copy.deepcopy, so the result
    is never shared with the input."""
    kind = type(value)
    if kind is dict:
        return {key: plain_copy(item) for key, item in value.items()}
    if kind is list:
        return [plain_copy(item) for item in value]
    if kind in (str, int, float, bool, type(None)):
        return value
    if kind is tuple and all(type(item) in (str, int, float, bool, type(None)) for item in value):
        return value
    return copy.deepcopy(value)


def merge_web_member_demand(current, source, *, owned=False):
    """Keep independent tension/compression envelopes for one physical web.

    A larger tension force must not discard a lower compression force: buckling
    capacity can be much lower than tensile capacity. Retain each force's source
    combination, and independently retain the governing connection context.

    ``owned=True`` is for the envelope solver's own merges: ``current`` is
    updated in place and ``source`` values are taken by reference, because the
    caller built both and discards ``source``. Otherwise inputs are not mutated
    or shared.
    """
    copier = (lambda value: value) if owned else copy.deepcopy
    if current is None:
        result = dict(source) if owned else copy.deepcopy(source)
        for kind in ("compression", "tension"):
            result.setdefault(f"{kind}_combo", source.get("combo", "-"))
        return result
    result = current if owned else copy.deepcopy(current)
    for kind in ("compression", "tension"):
        key = f"{kind}_kn"
        if source.get(key, 0.0) > current.get(key, 0.0):
            result[key] = source[key]
            result[f"{kind}_combo"] = source.get(f"{kind}_combo", source.get("combo", "-"))
        else:
            result.setdefault(f"{kind}_combo", current.get("combo", "-"))
    if source.get("axial_kn", 0.0) > current.get("axial_kn", 0.0):
        result["axial_kn"] = source["axial_kn"]
        result["combo"] = source.get("combo", "-")
    if source.get("connection_ratio", 0.0) > current.get("connection_ratio", 0.0):
        for key in ("connection_ratio", "connection_demand_kn", "connection_combo", "connection"):
            result[key] = copier(source.get(key))
    return result


def parallel_worker_count(item_count):
    return min(item_count, max((os.cpu_count() or 2) - 1, 1), 8)


SNOW_REGION_K1 = {
    "Region AN": 0.2,
    "Region AC": 0.7,
    "Region AS": 1.0,
    "Region AT": 1.6,
}


COSTING_DEFAULTS = {
    "shs_rate_per_kg": "3.92",
    "doubled_chord_rate_per_kg": "3.92",
    "ub_rate_per_kg": "2.87",
    "wb_rate_per_kg": "3.65",
    "purlin_rate_per_kg": "1.90",
}


SERVICEABILITY_LIMIT_DEFAULTS = {
    "column_il1": 80.0,   # project-adopted IL1 eave sway H/80 (2026-11-11; was H/100)
    "column_standard": 150.0,
    "storage_eave": 100.0,
    "crane_rail_height": 250.0,
    "truss_combined_il1": 150.0,
    "truss_combined_standard": 300.0,
    "truss_g_il1": 240.0,
    "truss_g_standard": 360.0,
    "truss_q_il1": 180.0,
    "truss_q_standard": 240.0,
    "truss_ws_il1": 100.0,
    "truss_ws_standard": 150.0,
    "canopy_tip_il1": 100.0,
    "canopy_tip_standard": 180.0,
    "storage_steel": 250.0,
}


# Roof sheeting profile; sets the roof friction drag in the 3D bracing end-wind
# loads (bracing_analysis_desktop.roof_friction_drag).
ROOF_CLADDING_PROFILES = ["5-Rib (Trimdek)", "Corry (Corodek)"]


DEFAULT_ROOF_CLADDING_PROFILE = "5-Rib (Trimdek)"


FACTORY_DEFAULTS_FILENAME = "factory_defaults.json"


CANOPY_CHORD_DEFAULT_FRAGMENTS = ["65*2.5 SHS", "65 x 2.5 SHS", "65 SHS"]


LEAN_TO_COLUMN_DEFAULT_FRAGMENTS = ["250 UB 25.7", "250 UB 26", "250UB26", "250 UB"]


UB_STOCK_LENGTHS_M = [4.5, 5.0, 5.5, 6.0, 6.75, 7.5, 8.25, 9.0, 10.5, 12.0, 13.5, 15.0, 16.5, 18.0, 20.0]


STANDARD_PIER_DIAMETERS_MM = [600, 750, 900, 1200]


EAVE_BRACING_PATH_CALIBRATION_FACTOR = 4.0 / 53.0


WEB_CONNECTION_CFW_LEG_MM = 6.0


DOUBLED_CHORD_TRIGGER_RATIO = 0.9999


MAX_DOUBLED_CHORD_LENGTH_FRACTION = 0.30


# Frame load basis (BUILDING_LOADS_3D_PLAN.md): (combo label, preset value).
# "single_frame" is today's one-frame "anywhere" envelope; the building
# options check every gridline's own loads (MainWindow.building_load_mode).
FRAME_LOAD_BASIS_OPTIONS = (
    ("Single frame (2D envelope)", "single_frame"),
    ("Building loads (per gridline)", "building"),
    ("Building loads, 2D coefficients", "legacy_2d"),
)


MAIN_TRUSS_DEPTH_MIN_RATIO = 0.03


# Eave height is the cladding pitching point; these set the top chord node
# below it when no girt/purlin section is selected (MainWindow.eave_node_basis).
GIRT_DEPTH_DEFAULT_MM = 200.0


PURLIN_DEPTH_DEFAULT_MM = 200.0


MAIN_TRUSS_DEPTH_START_RATIO = 0.033


# The optimiser must be able to investigate deeper stiffness/cost basins. The
# previous 4.5% automatic ceiling excluded verified economical designs such as
# a 1100 mm truss on a 21 m skillion frame. The user's explicit maximum remains
# authoritative when one is entered.
MAIN_TRUSS_DEPTH_MAX_RATIO = 0.06


MAIN_TRUSS_DEPTH_HEIGHT_BONUS_THRESHOLD_MM = 6000.0


MAIN_TRUSS_DEPTH_HEIGHT_BONUS_RATIO = 0.05


ADDITION_TRUSS_DEPTH_MAX_RATIO = 0.075


DESIGN_UTILIZATION_PASS_LIMIT = 1.0


END_WALL_BRACING_SIZE_OPTIONS = [
    ("16 DIA ROD", math.pi * 16.0**2 / 4.0, "16 DIA ROD"),
    ("20 DIA ROD", math.pi * 20.0**2 / 4.0, "20 DIA ROD"),
    ("24 DIA ROD", math.pi * 24.0**2 / 4.0, "24 DIA ROD"),
    ("30 DIA ROD", math.pi * 30.0**2 / 4.0, "30 DIA ROD"),
    ("EA 75x75x6", 2.0 * 75.0 * 6.0 - 6.0**2, "75x75x6 equal angle"),
    ("EA 75x75x8", 2.0 * 75.0 * 8.0 - 8.0**2, "75x75x8 equal angle"),
    ("EA 75x75x10", 2.0 * 75.0 * 10.0 - 10.0**2, "75x75x10 equal angle"),
    ("EA 90x90x10", 2.0 * 90.0 * 10.0 - 10.0**2, "90x90x10 equal angle"),
    ("EA 100x100x10", 2.0 * 100.0 * 10.0 - 10.0**2, "100x100x10 equal angle"),
]


CRANE_RAIL_PROFILES = {
    "Manual": None,
    "12.5 kg/m rail": {"weight_kn_m": 0.123, "height_mm": 120.0, "bte_mm": 50.0, "k": 4.0},
    "30 kg/m rail": {"weight_kn_m": 0.294, "height_mm": 108.0, "bte_mm": 50.0, "k": 4.0},
    "31 kg/m rail": {"weight_kn_m": 0.310, "height_mm": 120.0, "bte_mm": 50.0, "k": 4.0},
    "40 kg/m rail": {"weight_kn_m": 0.392, "height_mm": 120.0, "bte_mm": 50.0, "k": 4.0},
    "50 kg/m rail": {"weight_kn_m": 0.491, "height_mm": 130.0, "bte_mm": 50.0, "k": 4.0},
    "60 kg/m rail": {"weight_kn_m": 0.589, "height_mm": 140.0, "bte_mm": 50.0, "k": 4.0},
}


CRANE_RATING_LOAD_TABLE = {
    "lateral_inertia": [(5.0, 6.21), (10.0, 10.5), (20.0, 17.29)],
    "oblique": [(5.0, 6.25), (10.0, 11.27), (20.0, 17.43)],
    "pmax_dynamic": [(5.0, 43.88), (10.0, 81.29), (20.0, 151.96)],
    "pmin_dynamic": [(5.0, 13.02), (10.0, 18.92), (20.0, 28.81)],
    "pmax_static": [(5.0, 36.67), (10.0, 67.42), (20.0, 125.3)],
    "pmin_static": [(5.0, 11.75), (10.0, 17.04), (20.0, 25.72)],
}


CRANE_PFC_CAP_PROFILES = {
    "No PFC cap": None,
    "75 PFC": {"mass_kg_m": 5.92, "depth_mm": 75.0, "width_mm": 40.0, "thickness_mm": 6.1, "web_thickness_mm": 3.8, "area_mm2": 754.0, "centroid_mm": 13.7, "ix_mm4": 0.683e6, "iy_mm4": 0.120e6, "zex_mm3": 21.4e3, "zey_mm3": 6.84e3, "j_mm4": 8.42e3, "iw_mm6": 0.103e9},
    "100 PFC": {"mass_kg_m": 8.33, "depth_mm": 100.0, "width_mm": 50.0, "thickness_mm": 6.7, "web_thickness_mm": 4.2, "area_mm2": 1060.0, "centroid_mm": 16.7, "ix_mm4": 1.74e6, "iy_mm4": 0.267e6, "zex_mm3": 40.3e3, "zey_mm3": 12.0e3, "j_mm4": 13.6e3, "iw_mm6": 0.419e9},
    "125 PFC": {"mass_kg_m": 11.9, "depth_mm": 125.0, "width_mm": 65.0, "thickness_mm": 7.5, "web_thickness_mm": 4.7, "area_mm2": 1520.0, "centroid_mm": 21.8, "ix_mm4": 3.97e6, "iy_mm4": 0.658e6, "zex_mm3": 72.8e3, "zey_mm3": 22.8e3, "j_mm4": 23.8e3, "iw_mm6": 1.64e9},
    "150 PFC": {"mass_kg_m": 17.7, "depth_mm": 150.0, "width_mm": 75.0, "thickness_mm": 9.5, "web_thickness_mm": 6.0, "area_mm2": 2250.0, "centroid_mm": 24.9, "ix_mm4": 8.34e6, "iy_mm4": 1.29e6, "zex_mm3": 129e3, "zey_mm3": 38.5e3, "j_mm4": 56.6e3, "iw_mm6": 4.56e9},
    "180 PFC": {"mass_kg_m": 20.9, "depth_mm": 180.0, "width_mm": 75.0, "thickness_mm": 11.0, "web_thickness_mm": 6.0, "area_mm2": 2660.0, "centroid_mm": 24.5, "ix_mm4": 14.1e6, "iy_mm4": 1.51e6, "zex_mm3": 182e3, "zey_mm3": 44.9e3, "j_mm4": 84.5e3, "iw_mm6": 7.75e9},
    "200 PFC": {"mass_kg_m": 22.9, "depth_mm": 200.0, "width_mm": 75.0, "thickness_mm": 12.0, "web_thickness_mm": 6.0, "area_mm2": 2920.0, "centroid_mm": 24.4, "ix_mm4": 19.1e6, "iy_mm4": 1.65e6, "zex_mm3": 221e3, "zey_mm3": 46.7e3, "j_mm4": 105e3, "iw_mm6": 10.5e9},
    "230 PFC": {"mass_kg_m": 25.1, "depth_mm": 230.0, "width_mm": 75.0, "thickness_mm": 12.0, "web_thickness_mm": 6.5, "area_mm2": 3200.0, "centroid_mm": 22.6, "ix_mm4": 26.8e6, "iy_mm4": 1.76e6, "zex_mm3": 271e3, "zey_mm3": 45.1e3, "j_mm4": 112e3, "iw_mm6": 14.9e9},
    "250 PFC": {"mass_kg_m": 35.5, "depth_mm": 250.0, "width_mm": 90.0, "thickness_mm": 15.0, "web_thickness_mm": 8.0, "area_mm2": 4520.0, "centroid_mm": 28.6, "ix_mm4": 45.1e6, "iy_mm4": 3.64e6, "zex_mm3": 421e3, "zey_mm3": 88.7e3, "j_mm4": 248e3, "iw_mm6": 35.9e9},
    "300 PFC": {"mass_kg_m": 40.1, "depth_mm": 300.0, "width_mm": 90.0, "thickness_mm": 16.0, "web_thickness_mm": 8.0, "area_mm2": 5110.0, "centroid_mm": 27.2, "ix_mm4": 72.4e6, "iy_mm4": 4.04e6, "zex_mm3": 564e3, "zey_mm3": 82.3e3, "j_mm4": 304e3, "iw_mm6": 58.1e9},
    "380 PFC": {"mass_kg_m": 55.2, "depth_mm": 380.0, "width_mm": 100.0, "thickness_mm": 17.5, "web_thickness_mm": 10.0, "area_mm2": 7030.0, "centroid_mm": 27.5, "ix_mm4": 152e6, "iy_mm4": 6.48e6, "zex_mm3": 946e3, "zey_mm3": 115.0e3, "j_mm4": 491e3, "iw_mm6": 152e9},
}


def profile_label_with_weight(name, kg_m):
    return f"{name} ({kg_m:.1f} kg/m)"


def clean_profile_label(label):
    return re.sub(r"\s+\([0-9]+(?:\.[0-9]+)?\s*kg/m\)\s*$", "", str(label or ""), flags=re.IGNORECASE)


def normalize_end_wall_brace_label(value):
    if value is None:
        return None
    text = clean_profile_label(value).strip()
    if not text:
        return None
    match = re.match(r"RB\s+(\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
    if match:
        return f"{float(match.group(1)):.0f} DIA ROD"
    match = re.match(r"(\d+(?:\.\d+)?)\s*(?:mm\s*)?(?:dia\s*)?(?:rod|round\s*bar)?$", text, flags=re.IGNORECASE)
    if match:
        return f"{float(match.group(1)):.0f} DIA ROD"
    return text


def crane_pfc_cap_display_name(name):
    pfc = CRANE_PFC_CAP_PROFILES.get(name)
    if not pfc:
        return name
    return profile_label_with_weight(name, pfc.get("mass_kg_m", 0.0))


def metres_from_mm_text(value, default_text=""):
    if value is None or value == "":
        return default_text
    try:
        metres = float(value) / 1000.0
    except (TypeError, ValueError):
        return str(value)
    return f"{metres:.3f}".rstrip("0").rstrip(".")


def mm_from_metres_text(value):
    return float(value) * 1000.0


def mm_text_from_metres_widget(widget):
    text = widget.text().strip()
    if not text:
        return ""
    return f"{mm_from_metres_text(text):.3f}".rstrip("0").rstrip(".")


def internal_column_positions_mm(text, span_mm):
    """Parse distinct support distances measured from the left column centreline."""
    try:
        positions = [float(value.strip()) * 1000.0 for value in text.split(",")]
    except ValueError:
        raise ValueError("Internal column positions must be comma-separated distances in metres, e.g. 5, 12, 20.") from None
    if any(not math.isfinite(x) or not 0.0 < x < span_mm for x in positions):
        raise ValueError(
            f"Internal column positions must be greater than 0 and less than {span_mm / 1000.0:g} m "
            "from the left column centreline."
        )
    positions.sort()
    if any(right - left <= 1e-6 for left, right in zip(positions, positions[1:])):
        raise ValueError("Internal column positions must be distinct; remove repeated positions.")
    return positions


def governing_height_deflection_item(items):
    """Return the deflection item with the highest demand-to-height ratio."""
    candidates = [
        item for item in items
        if item.get("value", -1.0) >= 0.0 and (item.get("height_mm") or 0.0) > 0.0
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item["value"] / item["height_mm"])


def canopy_depth_mm_from_preset(canopies, side, canopy_type, default_text):
    depth = canopies.get(f"{side}_depth_mm")
    if depth not in (None, ""):
        return str(depth)

    legacy_depth = canopies.get(f"{side}_shallow_depth_mm", canopies.get("shallow_depth_mm"))
    if legacy_depth in (None, ""):
        return default_text
    try:
        depth_mm = float(legacy_depth)
        if canopy_type == "Canopy":
            length_mm = float(canopies.get(f"{side}_length_mm") or 0.0)
            pitch_deg = float(canopies.get(f"{side}_pitch_deg", canopies.get("pitch_deg", 0.0)) or 0.0)
            depth_mm += abs(math.tan(math.radians(pitch_deg)) * length_mm)
        return f"{depth_mm:.3f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(legacy_depth)


def pitch_compensated_gable_top_stations(span_mm, depth_mm, pitch_deg, num_panels):
    if num_panels < 4 or num_panels % 4 != 0 or span_mm <= 0.0 or depth_mm <= 0.0:
        return [span_mm * index / max(num_panels, 1) for index in range(max(num_panels, 0) + 1)]
    panels_per_side = num_panels // 2
    roof_slope_tan = math.tan(math.radians(pitch_deg))
    regular_panel = span_mm / num_panels
    offset = roof_slope_tan * depth_mm / (1.0 + roof_slope_tan ** 2)
    stations = [regular_panel * index for index in range(num_panels + 1)]
    for index in range(1, num_panels, 2):
        stations[index] += -offset if index < panels_per_side else offset
    stations[-1] = span_mm
    return stations


# ==========================================================
# RESOURCE PATH
# ==========================================================

def resource_path(relative_path):
    """Path of a bundled data file, from source or the packaged build."""
    return str(data_path(relative_path))
