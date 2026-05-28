"""
Basic load definitions and application helpers for the portal frame calculator.

Wind loads are still calculated in wind.py, but this module owns the load
combination definitions and the non-wind factored load application.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


LoadArrow = Tuple[float, float, float, float]  # x, y, fx, fy in model units / N
STEEL_DENSITY_KG_M3 = 7850.0
GRAVITY_M_S2 = 9.81


@dataclass(frozen=True)
class LoadCombination:
    name: str
    g_factor: float
    q_factor: float = 0.0
    crane_position: str = "none"
    cpe_case: Optional[str] = None
    cpi_case: Optional[str] = None
    wind_pressure: str = "ultimate"
    serviceability_component: Optional[str] = None

    @property
    def includes_wind(self) -> bool:
        return self.cpe_case is not None and self.cpi_case is not None

    @property
    def is_serviceability(self) -> bool:
        return self.name.startswith("SLS:")

    @property
    def is_ultimate(self) -> bool:
        return not self.is_serviceability


def build_load_combinations(cpe_cases, cpi_cases) -> List[LoadCombination]:
    """Return strength and serviceability combinations."""
    combinations = [
        LoadCombination("1.2G + 1.5Q", g_factor=1.2, q_factor=1.5),
        LoadCombination("1.2G + 1.5Q + Crane Left", g_factor=1.2, q_factor=1.5, crane_position="left"),
        LoadCombination("1.2G + 1.5Q + Crane Right", g_factor=1.2, q_factor=1.5, crane_position="right"),
        LoadCombination("1.35G", g_factor=1.35),
    ]
    for prefix, g_factor in [("1.2G + Wu", 1.2), ("0.9G + Wu", 0.9)]:
        for cpe_case in cpe_cases:
            for cpi_case in cpi_cases:
                combinations.append(
                    LoadCombination(
                        f"{prefix} ({cpe_case}, {cpi_case})",
                        g_factor=g_factor,
                        cpe_case=cpe_case,
                        cpi_case=cpi_case,
                        wind_pressure="ultimate",
                    )
                )

    combinations.extend(
        [
            LoadCombination("SLS: G", g_factor=1.0, serviceability_component="G"),
            LoadCombination("SLS: Q", g_factor=0.0, q_factor=1.0, serviceability_component="Q"),
            LoadCombination("SLS: G + Q", g_factor=1.0, q_factor=1.0),
            LoadCombination("SLS: G + Q + Crane Left", g_factor=1.0, q_factor=1.0, crane_position="left"),
            LoadCombination("SLS: G + Q + Crane Right", g_factor=1.0, q_factor=1.0, crane_position="right"),
        ]
    )
    for cpe_case in cpe_cases:
        for cpi_case in cpi_cases:
            combinations.append(
                LoadCombination(
                    f"SLS: Ws ({cpe_case}, {cpi_case})",
                    g_factor=0.0,
                    cpe_case=cpe_case,
                    cpi_case=cpi_case,
                    wind_pressure="serviceability",
                    serviceability_component="Ws",
                )
            )
            combinations.append(
                LoadCombination(
                    f"SLS: G + Ws ({cpe_case}, {cpi_case})",
                    g_factor=1.0,
                    cpe_case=cpe_case,
                    cpi_case=cpi_case,
                    wind_pressure="serviceability",
                )
            )
    return combinations


@dataclass
class RoofLoadPlacement:
    side: str = "Both"
    from_percent: float = 0.0
    to_percent: float = 100.0


@dataclass
class BasicRoofLoads:
    """
    Roof area loads in kPa.

    Negative values act downward in global Y. The calculator converts these to
    truss line loads using: line load kN/m = area load kPa x tributary bay size m.
    """

    bay_size_m: float = 8.0
    g: float = -0.10
    q: float = 0.0
    solar: float = 0.0
    fire_service: float = 0.0
    hvac: float = 0.0
    other: float = 0.0
    placements: Dict[str, RoofLoadPlacement] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, float]:
        return {
            "G": self.g,
            "Q": self.q,
            "Solar": self.solar,
            "Fire service": self.fire_service,
            "HVAC": self.hvac,
            "Other": self.other,
        }

    def permanent_dict(self) -> Dict[str, float]:
        return {
            "G": self.g,
            "Solar": self.solar,
            "Fire service": self.fire_service,
            "HVAC": self.hvac,
            "Other": self.other,
        }

    @property
    def permanent_vertical_kpa(self) -> float:
        return sum(self.permanent_dict().values())

    @property
    def q_vertical_kpa(self) -> float:
        return self.q

    def factored_vertical_kpa(self, g_factor: float, q_factor: float) -> float:
        return self.permanent_vertical_kpa * g_factor + self.q_vertical_kpa * q_factor

    def factored_vertical_kn_per_m(self, g_factor: float, q_factor: float) -> float:
        return self.factored_vertical_kpa(g_factor, q_factor) * self.bay_size_m

    def placement_for(self, key: str) -> RoofLoadPlacement:
        return self.placements.get(key, RoofLoadPlacement())

    def factored_components(self, g_factor: float, q_factor: float) -> List[Tuple[str, float, RoofLoadPlacement]]:
        components = [
            ("G", self.g * g_factor, self.placement_for("G")),
            ("Q", self.q * q_factor, self.placement_for("Q")),
            ("Solar", self.solar * g_factor, self.placement_for("Solar")),
            ("Fire service", self.fire_service * g_factor, RoofLoadPlacement()),
            ("HVAC", self.hvac * g_factor, RoofLoadPlacement()),
            ("Other", self.other * g_factor, self.placement_for("Other")),
        ]
        return [(label, kpa, placement) for label, kpa, placement in components if abs(kpa) > 1e-12]

    @property
    def total_vertical_kpa(self) -> float:
        return sum(self.as_dict().values())

    @property
    def total_vertical_kn_per_m(self) -> float:
        return self.total_vertical_kpa * self.bay_size_m


@dataclass
class WallLoads:
    """Wall line loads in kN/m. Magnitudes are positive; direction is handled separately."""

    left_plus_x: float = 0.2
    right_minus_x: float = 0.2


@dataclass
class LoadApplicationResult:
    arrows: List[LoadArrow] = field(default_factory=list)
    summary_lines: List[str] = field(default_factory=list)
    unfactored_self_weight_kn: float = 0.0


class BasicLoadCase:
    """
    Applies the current first-pass load model to the 2D frame.

    Current behaviour:
    - G is made from G, Solar, Fire service, HVAC and Other roof loads.
    - Q is factored separately.
    - Roof line load is distributed to top chord nodes using horizontal tributary width.
    - Wall loads are treated as permanent line loads and factored with G.
    - Wu is applied by wind.py after this non-wind portion is applied.
    """

    def __init__(self, roof_loads: BasicRoofLoads, wall_loads: WallLoads):
        self.roof_loads = roof_loads
        self.wall_loads = wall_loads

    def apply(self, structure, top_nodes, left_base, right_base) -> LoadApplicationResult:
        return self.apply_factored(structure, top_nodes, left_base, right_base, 1.0, 1.0)

    def apply_factored(self, structure, top_nodes, left_base, right_base, g_factor: float, q_factor: float, roof_top_nodes=None) -> LoadApplicationResult:
        result = LoadApplicationResult()
        roof_nodes = roof_top_nodes or top_nodes

        for _, factored_kpa, placement in self.roof_loads.factored_components(g_factor, q_factor):
            result.arrows.extend(
                apply_roof_vertical_loads_in_zones(
                    structure,
                    roof_nodes,
                    factored_kpa * self.roof_loads.bay_size_m,
                    placement.side,
                    placement.from_percent,
                    placement.to_percent,
                )
            )
        result.arrows.extend(
            apply_wall_horizontal_loads(
                structure,
                left_base,
                top_nodes[0],
                self.wall_loads.left_plus_x * g_factor,
                direction=+1,
            )
        )
        result.arrows.extend(
            apply_wall_horizontal_loads(
                structure,
                right_base,
                top_nodes[-1],
                self.wall_loads.right_minus_x * g_factor,
                direction=-1,
            )
        )

        result.summary_lines.extend(self.summary_lines(g_factor, q_factor))
        return result

    def summary_lines(self, g_factor: float = 1.0, q_factor: float = 1.0) -> List[str]:
        lines = ["BASIC LOAD INPUTS"]
        lines.append(f"{'Bay size':<13}: {self.roof_loads.bay_size_m:>8.3f} m tributary width")
        for label, value in self.roof_loads.as_dict().items():
            lines.append(f"{label:<13}: {value:>8.3f} kPa roof area load")
        lines.append(f"{'G total':<13}: {self.roof_loads.permanent_vertical_kpa:>8.3f} kPa permanent roof area load")
        lines.append(f"{'Q total':<13}: {self.roof_loads.q_vertical_kpa:>8.3f} kPa live roof area load")
        lines.append(f"{'Factors':<13}: G x {g_factor:.3f}, Q x {q_factor:.3f}")
        lines.append(f"{'Factored roof':<13}: {self.roof_loads.factored_vertical_kpa(g_factor, q_factor):>8.3f} kPa = {self.roof_loads.factored_vertical_kn_per_m(g_factor, q_factor):>8.3f} kN/m on truss")
        for label, factored_kpa, placement in self.roof_loads.factored_components(g_factor, q_factor):
            lines.append(
                f"{label + ' extent':<13}: {factored_kpa:>8.3f} kPa | {placement.side}, "
                f"{placement.from_percent:.1f}% to {placement.to_percent:.1f}%"
            )
        lines.append(f"{'Left wall':<13}: {self.wall_loads.left_plus_x:>8.3f} kN/m +X")
        lines.append(f"{'Right wall':<13}: {self.wall_loads.right_minus_x:>8.3f} kN/m -X")
        lines.append(f"{'Wall factor':<13}: G x {g_factor:.3f}")
        return lines


def apply_roof_vertical_loads(structure, top_nodes, roof_load_kn_per_m: float) -> List[LoadArrow]:
    """
    Convert a roof vertical line load in kN/m into equivalent nodal Y loads.

    The line load is already the roof area load multiplied by the tributary bay
    size. Horizontal panel length is then used to generate equivalent nodal loads.
    """
    return apply_roof_vertical_loads_in_zones(
        structure,
        top_nodes,
        roof_load_kn_per_m,
        application_side="Both",
        from_percent=0.0,
        to_percent=100.0,
    )


def roof_load_intervals(top_nodes, application_side: str, from_percent: float, to_percent: float) -> List[Tuple[float, float]]:
    if len(top_nodes) < 2:
        return []
    left_x = min(node.x for node in top_nodes)
    right_x = max(node.x for node in top_nodes)
    apex_x = top_nodes[max(range(len(top_nodes)), key=lambda i: top_nodes[i].y)].x
    left_span = max(apex_x - left_x, 0.0)
    right_span = max(right_x - apex_x, 0.0)
    p1 = max(0.0, min(float(from_percent), 100.0)) / 100.0
    p2 = max(0.0, min(float(to_percent), 100.0)) / 100.0
    if p2 < p1:
        p1, p2 = p2, p1

    intervals: List[Tuple[float, float]] = []
    side = str(application_side or "Both").strip().lower()
    if side in {"left", "both"} and left_span > 0.0:
        intervals.append((left_x + p1 * left_span, left_x + p2 * left_span))
    if side in {"right", "both"} and right_span > 0.0:
        intervals.append((right_x - p2 * right_span, right_x - p1 * right_span))
    return [(a, b) for a, b in intervals if b > a]


def apply_roof_vertical_loads_in_zones(
    structure,
    top_nodes,
    roof_load_kn_per_m: float,
    application_side: str = "Both",
    from_percent: float = 0.0,
    to_percent: float = 100.0,
) -> List[LoadArrow]:
    """
    Convert a roof vertical line load into equivalent nodal loads within selected side-span zones.

    Percentages are measured from each eave toward the apex. Loads are still
    applied using horizontal tributary width to match the existing roof load model.
    """
    load_arrows: List[LoadArrow] = []
    intervals = roof_load_intervals(top_nodes, application_side, from_percent, to_percent)
    if not intervals:
        return load_arrows
    for i in range(len(top_nodes) - 1):
        n1 = top_nodes[i]
        n2 = top_nodes[i + 1]
        panel_from = min(n1.x, n2.x)
        panel_to = max(n1.x, n2.x)
        loaded_width_mm = 0.0
        loaded_center_sum = 0.0
        for interval_from, interval_to in intervals:
            overlap_from = max(panel_from, interval_from)
            overlap_to = min(panel_to, interval_to)
            overlap = max(0.0, overlap_to - overlap_from)
            loaded_width_mm += overlap
            loaded_center_sum += overlap * ((overlap_from + overlap_to) / 2.0)
        if loaded_width_mm <= 1e-9:
            continue
        tributary_load_n = roof_load_kn_per_m * (loaded_width_mm / 1000.0) * 1000
        structure.add_load(n1.uy, tributary_load_n / 2)
        structure.add_load(n2.uy, tributary_load_n / 2)
        mx = loaded_center_sum / loaded_width_mm
        my = (n1.y + n2.y) / 2
        load_arrows.append((mx, my + 800, 0, tributary_load_n))
    return load_arrows


def apply_wall_horizontal_loads(structure, base_node, top_node, wall_load_kn_per_m: float, direction: int) -> List[LoadArrow]:
    """
    Convert a wall horizontal line load in kN/m into equivalent nodal X loads.

    direction = +1 for +X, -1 for -X.
    """
    height_m = abs(top_node.y - base_node.y) / 1000
    total_load_n = wall_load_kn_per_m * height_m * 1000 * direction
    structure.add_load(base_node.ux, total_load_n / 2)
    structure.add_load(top_node.ux, total_load_n / 2)
    return [(top_node.x, (base_node.y + top_node.y) / 2, total_load_n, 0)]


def apply_structure_self_weight(structure, elements, g_factor: float, steel_density_kg_m3: float = STEEL_DENSITY_KG_M3) -> LoadApplicationResult:
    """
    Apply member self weight as a permanent G load.

    Section area is in mm^2 and member length is in mm. The applied nodal loads
    are vertical global Y loads, split equally between member end nodes.
    """
    result = LoadApplicationResult()
    total_unfactored_n = 0.0

    for element in elements:
        length_mm = element.length()
        volume_m3 = element.A * 1e-6 * length_mm * 1e-3
        unfactored_weight_n = volume_m3 * steel_density_kg_m3 * GRAVITY_M_S2
        factored_weight_n = -unfactored_weight_n * g_factor
        total_unfactored_n += unfactored_weight_n

        structure.add_load(element.start.uy, factored_weight_n / 2.0)
        structure.add_load(element.end.uy, factored_weight_n / 2.0)

        mx = (element.start.x + element.end.x) / 2.0
        my = (element.start.y + element.end.y) / 2.0
        result.arrows.append((mx, my + 450, 0.0, factored_weight_n))

    result.unfactored_self_weight_kn = total_unfactored_n / 1000.0
    result.summary_lines.extend(
        [
            "STRUCTURE SELF WEIGHT",
            f"{'Steel density':<13}: {steel_density_kg_m3:>8.1f} kg/m3",
            f"{'Unfactored':<13}: {result.unfactored_self_weight_kn:>8.3f} kN",
            f"{'G factor':<13}: x {g_factor:.3f}",
            f"{'Applied':<13}: {-result.unfactored_self_weight_kn * g_factor:>8.3f} kN vertical",
        ]
    )
    return result
