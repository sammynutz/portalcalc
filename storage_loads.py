from dataclasses import dataclass, field
import math
import numpy as np


@dataclass
class StorageLoadInputs:
    enabled: bool = False
    load_path_mode: str = "Wall panels to horizontal rail"
    panel_support_mode: str = "Break at vertical stub"
    material_name: str = "Grain"
    bulk_density_kn_m3: float = 8.0
    internal_friction_angle_deg: float = 30.0
    backfill_slope_deg: float = 0.0
    stored_height_m: float = 3.0
    bay_spacing_m: float = 8.0
    rail_height_mm: float = 3000.0
    left_wall: bool = True
    right_wall: bool = True
    reverse_direction: bool = False
    uls_factor: float = 1.5
    sls_factor: float = 1.0


@dataclass
class StoragePanelDesignInputs:
    concrete_f_c_mpa: float = 40.0
    reo_f_y_mpa: float = 500.0
    cover_mm: float = 40.0
    strength_phi: float = 0.8
    min_reo_ratio: float = 0.0025
    deflection_limit_ratio: float = 250.0
    deflection_stiffness_factor: float = 0.35
    crack_min_thickness_mm: float = 100.0
    crack_max_spacing_mm: float = 200.0
    crack_service_stress_limit_mpa: float = 300.0
    crack_max_width_mm: float = 0.30
    shrinkage_strain: float = 610e-6
    creep_coefficient: float = 2.4
    bond_coefficient_k1: float = 0.8
    strain_coefficient_k2: float = 0.5
    thicknesses_mm: tuple[float, ...] = (125.0, 150.0, 175.0, 200.0, 225.0, 250.0, 275.0, 300.0)
    mesh_options: tuple[tuple[str, float, float, float], ...] = (
        ("SL62", 141.0, 6.0, 200.0),
        ("SL72", 179.0, 6.75, 200.0),
        ("SL82", 227.0, 7.6, 200.0),
        ("SL92", 290.0, 8.6, 200.0),
        ("SL102", 354.0, 9.5, 200.0),
        ("SL81", 454.0, 7.6, 100.0),
        ("RL718", 358.0, 6.75, 100.0),
        ("RL818", 454.0, 7.6, 100.0),
        ("RL918", 581.0, 8.6, 100.0),
        ("RL1018", 709.0, 9.5, 100.0),
        ("RL1118", 899.0, 10.7, 100.0),
        ("RL1218", 1112.0, 11.9, 100.0),
    )
    bar_diameters_mm: tuple[float, ...] = (12.0, 16.0, 20.0)
    spacings_mm: tuple[float, ...] = (300.0, 250.0, 200.0, 150.0, 125.0, 100.0)


@dataclass
class StoragePanelFaceDesign:
    label: str
    demand_knm_per_m: float
    demand_level_mm: float
    reinforcement_label: str
    reinforcement_type: str
    bar_diameter_mm: float
    spacing_mm: float
    as_provided_mm2_m: float
    as_min_mm2_m: float
    crack_spacing_limit_mm: float
    service_steel_stress_mpa: float
    service_stress_limit_mpa: float
    crack_width_mm: float
    crack_width_limit_mm: float
    crack_spacing_mm: float
    crack_effective_area_mm2: float
    crack_reinforcement_ratio: float
    crack_mean_strain: float
    capacity_knm_per_m: float
    utilization: float


@dataclass
class StoragePanelDesignResult:
    thickness_mm: float
    concrete_f_c_mpa: float
    reo_f_y_mpa: float
    cover_mm: float
    effective_depth_mm: float
    elastic_modulus_mpa: float
    gross_i_mm4_m: float
    effective_i_mm4_m: float
    deflection_mm: float
    deflection_level_mm: float
    deflection_limit_mm: float
    positive_face: StoragePanelFaceDesign
    negative_face: StoragePanelFaceDesign

    @property
    def utilization(self) -> float:
        return max(self.positive_face.utilization, self.negative_face.utilization, self.deflection_utilization)

    @property
    def deflection_utilization(self) -> float:
        return self.deflection_mm / self.deflection_limit_mm if self.deflection_limit_mm > 1e-9 else math.inf


@dataclass
class StorageRailLoad:
    level_mm: float
    tributary_height_m: float
    pressure_kpa: float
    line_load_kn_m: float
    total_resultant_kn: float
    rail_line_load_kn_m: float
    rail_reaction_kn: float
    base_reaction_kn: float
    panel_max_moment_knm_per_m: float
    panel_max_moment_level_mm: float
    panel_positive_moment_knm_per_m: float
    panel_positive_moment_level_mm: float
    panel_negative_moment_knm_per_m: float
    panel_negative_moment_level_mm: float
    panel_rail_moment_knm_per_m: float
    rail_bending_moment_knm: float
    rail_load_type: str = "udl"
    panel_span_m: float = 0.0
    stub_head_reaction_kn: float = 0.0
    stub_base_reaction_kn: float = 0.0
    direct_column_reaction_kn: float = 0.0


@dataclass
class StorageLoadResults:
    inputs: StorageLoadInputs
    rail_loads: list[StorageRailLoad] = field(default_factory=list)
    total_per_wall_kn: float = 0.0
    rail_reaction_per_wall_kn: float = 0.0
    base_reaction_per_wall_kn: float = 0.0
    ka: float = 0.0
    base_pressure_kpa: float = 0.0
    resultant_level_mm: float = 0.0

    @property
    def active_wall_count(self) -> int:
        return int(self.inputs.left_wall) + int(self.inputs.right_wall)


def _positive(value: float, fallback: float = 0.0) -> float:
    try:
        return max(float(value), fallback)
    except (TypeError, ValueError):
        return fallback


def _parse_number_list(value, default: tuple[float, ...]) -> tuple[float, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(";", ",").split(",")]
    else:
        try:
            parts = list(value)
        except TypeError:
            return default
    numbers = []
    for part in parts:
        try:
            number = float(part)
        except (TypeError, ValueError):
            continue
        if number > 0.0:
            numbers.append(number)
    return tuple(numbers) if numbers else default


def _parse_mesh_options(value, default: tuple[tuple[str, float, float, float], ...]) -> tuple[tuple[str, float, float, float], ...]:
    if value is None:
        return default
    if not isinstance(value, str):
        try:
            options = []
            for item in value:
                label = str(item[0])
                area = float(item[1])
                diameter = float(item[2])
                spacing = float(item[3]) if len(item) > 3 else 200.0
                if label and area > 0.0 and diameter > 0.0 and spacing > 0.0:
                    options.append((label, area, diameter, spacing))
            return tuple(options) if options else default
        except (TypeError, ValueError):
            return default
    options = []
    for part in value.replace(";", ",").split(","):
        tokens = [token.strip() for token in part.split(":")]
        if len(tokens) not in {3, 4}:
            continue
        label, area_text, diameter_text = tokens[:3]
        spacing_text = tokens[3] if len(tokens) == 4 else "200"
        try:
            area = float(area_text)
            diameter = float(diameter_text)
            spacing = float(spacing_text)
        except ValueError:
            continue
        if label and area > 0.0 and diameter > 0.0 and spacing > 0.0:
            options.append((label, area, diameter, spacing))
    return tuple(options) if options else default


def calculate_ka(internal_friction_angle_deg: float, backfill_slope_deg: float) -> float:
    phi = math.radians(float(internal_friction_angle_deg))
    beta = math.radians(float(backfill_slope_deg))
    cos_beta = math.cos(beta)
    radicand = cos_beta**2 - math.cos(phi) ** 2
    if radicand < -1e-12:
        raise ValueError("Backfill slope beta must not exceed internal friction angle phi for the Ka equation.")
    root = math.sqrt(max(radicand, 0.0))
    denominator = cos_beta + root
    if abs(denominator) <= 1e-12:
        raise ValueError("Invalid Ka inputs: denominator is zero.")
    return cos_beta * (cos_beta - root) / denominator


def is_centre_stub_storage_mode(inputs: StorageLoadInputs) -> bool:
    mode = str(getattr(inputs, "load_path_mode", "")).strip().lower()
    return "stub" in mode or "sideways" in mode or "side-running" in mode


def is_continuous_stub_panel_mode(inputs: StorageLoadInputs) -> bool:
    mode = str(getattr(inputs, "panel_support_mode", "")).strip().lower()
    return "continuous" in mode or "negative" in mode or "full bay" in mode


def bar_area_mm2(diameter_mm: float) -> float:
    return math.pi * float(diameter_mm) ** 2 / 4.0


def reo_area_mm2_m(diameter_mm: float, spacing_mm: float) -> float:
    return bar_area_mm2(diameter_mm) * 1000.0 / max(float(spacing_mm), 1e-9)


def panel_flexural_capacity_for_area_knm_per_m(
    thickness_mm: float,
    as_mm2_m: float,
    reinforcement_diameter_mm: float,
    concrete_f_c_mpa: float,
    reo_f_y_mpa: float,
    cover_mm: float,
    strength_phi: float,
) -> tuple[float, float]:
    b_mm = 1000.0
    d_mm = max(float(thickness_mm) - float(cover_mm) - float(reinforcement_diameter_mm) / 2.0, 1.0)
    stress_block_depth = as_mm2_m * reo_f_y_mpa / max(0.85 * concrete_f_c_mpa * b_mm, 1e-9)
    lever_arm = max(d_mm - stress_block_depth / 2.0, 0.0)
    capacity = strength_phi * as_mm2_m * reo_f_y_mpa * lever_arm / 1_000_000.0
    return capacity, d_mm


def panel_flexural_capacity_knm_per_m(
    thickness_mm: float,
    bar_diameter_mm: float,
    spacing_mm: float,
    concrete_f_c_mpa: float,
    reo_f_y_mpa: float,
    cover_mm: float,
    strength_phi: float,
) -> tuple[float, float]:
    return panel_flexural_capacity_for_area_knm_per_m(
        thickness_mm,
        reo_area_mm2_m(bar_diameter_mm, spacing_mm),
        bar_diameter_mm,
        concrete_f_c_mpa,
        reo_f_y_mpa,
        cover_mm,
        strength_phi,
    )


def panel_service_steel_stress_mpa(
    service_moment_knm_per_m: float,
    as_mm2_m: float,
    thickness_mm: float,
    cover_mm: float,
    reinforcement_diameter_mm: float,
) -> float:
    if service_moment_knm_per_m <= 1e-12:
        return 0.0
    d_mm = max(float(thickness_mm) - float(cover_mm) - float(reinforcement_diameter_mm) / 2.0, 1.0)
    lever_arm_mm = 0.9 * d_mm
    return service_moment_knm_per_m * 1_000_000.0 / max(as_mm2_m * lever_arm_mm, 1e-9)


def panel_crack_width_check(
    service_steel_stress_mpa: float,
    as_mm2_m: float,
    thickness_mm: float,
    cover_mm: float,
    reinforcement_diameter_mm: float,
    spacing_mm: float,
    concrete_f_c_mpa: float,
    inputs: StoragePanelDesignInputs,
) -> dict[str, float]:
    width_mm = 1000.0
    es_mpa = 200_000.0
    ec_mpa = concrete_elastic_modulus_mpa(concrete_f_c_mpa)
    n_eff = (1.0 + max(inputs.creep_coefficient, 0.0)) * es_mpa / max(ec_mpa, 1e-9)
    d_mm = max(thickness_mm - cover_mm - reinforcement_diameter_mm / 2.0, 1.0)
    a = width_mm / 2.0
    b = n_eff * as_mm2_m
    c = -n_eff * as_mm2_m * d_mm
    cracked_na = (-b + math.sqrt(max(b * b - 4.0 * a * c, 0.0))) / max(2.0 * a, 1e-9)
    hc_eff = min(2.5 * max(thickness_mm - d_mm, 0.0), max(thickness_mm - cracked_na, 0.0) / 3.0, thickness_mm / 2.0)
    hc_eff = max(hc_eff, 1.0)
    ac_eff = width_mm * hc_eff
    peff = as_mm2_m / max(ac_eff, 1e-9)
    fct_f = 0.6 * math.sqrt(max(concrete_f_c_mpa, 0.0))
    fct = 0.6 * fct_f
    fct_m = 1.4 * fct
    edm_min = 0.6 * max(service_steel_stress_mpa, 0.0) / es_mpa
    edm_1 = (
        max(service_steel_stress_mpa, 0.0) / es_mpa
        - 0.6 * fct_m / (es_mpa * max(peff, 1e-9)) * (1.0 + n_eff * peff)
        + max(inputs.shrinkage_strain, 0.0)
    )
    edm = max(edm_min, edm_1, 0.0)
    clear_cover_to_bar_centre = cover_mm + reinforcement_diameter_mm / 2.0
    maximum_bar_spacing = 5.0 * (clear_cover_to_bar_centre + 0.5 * reinforcement_diameter_mm)
    sr_max_1 = 1.3 * max(thickness_mm - cracked_na, 0.0)
    sr_max_2 = 3.4 * clear_cover_to_bar_centre + 0.3 * inputs.bond_coefficient_k1 * inputs.strain_coefficient_k2 * reinforcement_diameter_mm / max(peff, 1e-9)
    sr_max = min(sr_max_1, sr_max_2)
    crack_width = sr_max * edm
    return {
        "width_mm": crack_width,
        "limit_mm": inputs.crack_max_width_mm,
        "spacing_mm": sr_max,
        "bar_spacing_limit_mm": maximum_bar_spacing,
        "effective_area_mm2": ac_eff,
        "reinforcement_ratio": peff,
        "mean_strain": edm,
        "cracked_na_mm": cracked_na,
    }


def concrete_elastic_modulus_mpa(concrete_f_c_mpa: float) -> float:
    return 4700.0 * math.sqrt(max(concrete_f_c_mpa, 1.0))


def panel_service_deflection(
    results: "StorageLoadResults",
    thickness_mm: float,
    concrete_f_c_mpa: float,
    deflection_limit_ratio: float,
    stiffness_factor: float,
) -> tuple[float, float, float, float, float, float]:
    if not results.rail_loads:
        return 0.0, 0.0, 0.0, 0.0, 0.0, concrete_elastic_modulus_mpa(concrete_f_c_mpa)

    height_mm = max(results.inputs.stored_height_m * 1000.0, 1.0)
    rail_mm = max(results.inputs.rail_height_mm, 1.0)
    panel_top_mm = max(height_mm, rail_mm)
    e_mpa = concrete_elastic_modulus_mpa(concrete_f_c_mpa)
    gross_i_mm4 = 1000.0 * max(thickness_mm, 1.0) ** 3 / 12.0
    effective_i_mm4 = gross_i_mm4 * max(stiffness_factor, 1e-6)
    if is_centre_stub_storage_mode(results.inputs):
        rail = results.rail_loads[0]
        span_mm = max(rail.panel_span_m * 1000.0, 1.0)
        service_pressure_n_mm = max(results.base_pressure_kpa * max(results.inputs.sls_factor, 0.0), 0.0)
        deflection_mm = 5.0 * service_pressure_n_mm * span_mm**4 / (
            384.0 * e_mpa * max(effective_i_mm4, 1.0)
        )
        if is_continuous_stub_panel_mode(results.inputs):
            # Equal two-span continuous beam under a UDL on both spans.
            # v EI/(q L^4) = t^4/24 - t^3/16 + t/48,
            # with dv/dt=0 at t=(1+sqrt(33))/16 from the outer support.
            t = (1.0 + math.sqrt(33.0)) / 16.0
            coefficient = t**4 / 24.0 - t**3 / 16.0 + t / 48.0
            deflection_mm = coefficient * service_pressure_n_mm * span_mm**4 / (e_mpa * max(effective_i_mm4, 1.0))
        return (
            deflection_mm,
            0.0,
            span_mm / max(deflection_limit_ratio, 1e-9),
            gross_i_mm4,
            effective_i_mm4,
            e_mpa,
        )
    ei = e_mpa * effective_i_mm4
    if ei <= 1e-9:
        return math.inf, 0.0, panel_top_mm / max(deflection_limit_ratio, 1e-9), gross_i_mm4, effective_i_mm4, e_mpa

    station_count = 80
    stations = {0.0, rail_mm, height_mm, panel_top_mm}
    stations.update(panel_top_mm * index / station_count for index in range(station_count + 1))
    y_nodes = sorted(y for y in stations if -1e-9 <= y <= panel_top_mm + 1e-9)
    node_count = len(y_nodes)
    dof_count = 2 * node_count
    stiffness = np.zeros((dof_count, dof_count))
    loads = np.zeros(dof_count)
    base_pressure = results.base_pressure_kpa * max(results.inputs.sls_factor, 0.0)

    def pressure_at(y_mm: float) -> float:
        if y_mm > height_mm:
            return 0.0
        return base_pressure * max(1.0 - y_mm / max(height_mm, 1e-9), 0.0)

    for index, (y1, y2) in enumerate(zip(y_nodes[:-1], y_nodes[1:])):
        length_mm = y2 - y1
        if length_mm <= 1e-9:
            continue
        k = ei / length_mm**3 * np.array([
            [12.0, 6.0 * length_mm, -12.0, 6.0 * length_mm],
            [6.0 * length_mm, 4.0 * length_mm**2, -6.0 * length_mm, 2.0 * length_mm**2],
            [-12.0, -6.0 * length_mm, 12.0, -6.0 * length_mm],
            [6.0 * length_mm, 2.0 * length_mm**2, -6.0 * length_mm, 4.0 * length_mm**2],
        ])
        q1 = pressure_at(y1)
        q2 = pressure_at(y2)
        f = np.array([
            length_mm * (7.0 * q1 + 3.0 * q2) / 20.0,
            length_mm**2 * (3.0 * q1 + 2.0 * q2) / 60.0,
            length_mm * (3.0 * q1 + 7.0 * q2) / 20.0,
            -length_mm**2 * (2.0 * q1 + 3.0 * q2) / 60.0,
        ])
        dofs = [2 * index, 2 * index + 1, 2 * (index + 1), 2 * (index + 1) + 1]
        for a in range(4):
            loads[dofs[a]] += f[a]
            for b in range(4):
                stiffness[dofs[a], dofs[b]] += k[a, b]

    fixed_dofs = {0}
    rail_index = min(range(node_count), key=lambda index: abs(y_nodes[index] - rail_mm))
    fixed_dofs.add(2 * rail_index)
    free_dofs = [index for index in range(dof_count) if index not in fixed_dofs]
    displacements = np.zeros(dof_count)
    if free_dofs:
        kff = stiffness[np.ix_(free_dofs, free_dofs)]
        ff = loads[free_dofs]
        displacements[free_dofs] = np.linalg.solve(kff, ff)

    lateral_deflections = [abs(displacements[2 * index]) for index in range(node_count)]
    max_index = max(range(node_count), key=lambda index: lateral_deflections[index])
    limit_mm = panel_top_mm / max(deflection_limit_ratio, 1e-9)
    return lateral_deflections[max_index], y_nodes[max_index], limit_mm, gross_i_mm4, effective_i_mm4, e_mpa


def _select_panel_face_reo(
    label: str,
    demand_knm_per_m: float,
    service_demand_knm_per_m: float,
    demand_level_mm: float,
    thickness_mm: float,
    inputs: StoragePanelDesignInputs,
) -> StoragePanelFaceDesign | None:
    has_tension_demand = demand_knm_per_m > 1e-9 or service_demand_knm_per_m > 1e-9
    required_as_min = inputs.min_reo_ratio * 1000.0 * thickness_mm if has_tension_demand else 0.0
    mesh_candidates = []
    for mesh_label, as_provided, wire_diameter, mesh_spacing in inputs.mesh_options:
        capacity, _ = panel_flexural_capacity_for_area_knm_per_m(
            thickness_mm,
            as_provided,
            wire_diameter,
            inputs.concrete_f_c_mpa,
            inputs.reo_f_y_mpa,
            inputs.cover_mm,
            inputs.strength_phi,
        )
        service_stress = panel_service_steel_stress_mpa(service_demand_knm_per_m, as_provided, thickness_mm, inputs.cover_mm, wire_diameter)
        crack = panel_crack_width_check(
            service_stress,
            as_provided,
            thickness_mm,
            inputs.cover_mm,
            wire_diameter,
            mesh_spacing,
            inputs.concrete_f_c_mpa,
            inputs,
        )
        if (
            as_provided + 1e-9 >= required_as_min
            and (not has_tension_demand or capacity + 1e-9 >= demand_knm_per_m)
            and mesh_spacing <= inputs.crack_max_spacing_mm + 1e-9
            and mesh_spacing <= crack["bar_spacing_limit_mm"] + 1e-9
            and service_stress <= inputs.crack_service_stress_limit_mpa + 1e-9
            and crack["width_mm"] <= inputs.crack_max_width_mm + 1e-9
        ):
            mesh_candidates.append((as_provided, mesh_label, wire_diameter, mesh_spacing, capacity, service_stress, crack))
    if mesh_candidates:
        as_provided, mesh_label, wire_diameter, mesh_spacing, capacity, service_stress, crack = min(mesh_candidates, key=lambda item: (item[0], item[1]))
        utilization = demand_knm_per_m / capacity if capacity > 1e-9 else math.inf
        return StoragePanelFaceDesign(
            label=label,
            demand_knm_per_m=demand_knm_per_m,
            demand_level_mm=demand_level_mm,
            reinforcement_label=mesh_label,
            reinforcement_type="mesh",
            bar_diameter_mm=wire_diameter,
            spacing_mm=mesh_spacing,
            as_provided_mm2_m=as_provided,
            as_min_mm2_m=required_as_min,
            crack_spacing_limit_mm=inputs.crack_max_spacing_mm,
            service_steel_stress_mpa=service_stress,
            service_stress_limit_mpa=inputs.crack_service_stress_limit_mpa,
            crack_width_mm=crack["width_mm"],
            crack_width_limit_mm=crack["limit_mm"],
            crack_spacing_mm=crack["spacing_mm"],
            crack_effective_area_mm2=crack["effective_area_mm2"],
            crack_reinforcement_ratio=crack["reinforcement_ratio"],
            crack_mean_strain=crack["mean_strain"],
            capacity_knm_per_m=capacity,
            utilization=utilization,
        )

    bar_candidates = []
    for bar_diameter in sorted(inputs.bar_diameters_mm):
        for spacing in sorted(inputs.spacings_mm, reverse=True):
            as_provided = reo_area_mm2_m(bar_diameter, spacing)
            capacity, _ = panel_flexural_capacity_knm_per_m(
                thickness_mm,
                bar_diameter,
                spacing,
                inputs.concrete_f_c_mpa,
                inputs.reo_f_y_mpa,
                inputs.cover_mm,
                inputs.strength_phi,
            )
            service_stress = panel_service_steel_stress_mpa(service_demand_knm_per_m, as_provided, thickness_mm, inputs.cover_mm, bar_diameter)
            crack = panel_crack_width_check(
                service_stress,
                as_provided,
                thickness_mm,
                inputs.cover_mm,
                bar_diameter,
                spacing,
                inputs.concrete_f_c_mpa,
                inputs,
            )
            if (
                as_provided + 1e-9 >= required_as_min
                and (not has_tension_demand or capacity + 1e-9 >= demand_knm_per_m)
                and spacing <= inputs.crack_max_spacing_mm + 1e-9
                and spacing <= crack["bar_spacing_limit_mm"] + 1e-9
                and service_stress <= inputs.crack_service_stress_limit_mpa + 1e-9
                and crack["width_mm"] <= inputs.crack_max_width_mm + 1e-9
            ):
                bar_candidates.append((as_provided, bar_diameter, spacing, capacity, service_stress, crack))
    if not bar_candidates:
        return None
    as_provided, bar_diameter, spacing, capacity, service_stress, crack = min(bar_candidates, key=lambda item: (item[0], item[1], -item[2]))
    utilization = demand_knm_per_m / capacity if capacity > 1e-9 else math.inf
    return StoragePanelFaceDesign(
        label=label,
        demand_knm_per_m=demand_knm_per_m,
        demand_level_mm=demand_level_mm,
        reinforcement_label=f"N{bar_diameter:.0f}-{spacing:.0f}",
        reinforcement_type="bars",
        bar_diameter_mm=bar_diameter,
        spacing_mm=spacing,
        as_provided_mm2_m=as_provided,
        as_min_mm2_m=required_as_min,
        crack_spacing_limit_mm=inputs.crack_max_spacing_mm,
        service_steel_stress_mpa=service_stress,
        service_stress_limit_mpa=inputs.crack_service_stress_limit_mpa,
        crack_width_mm=crack["width_mm"],
        crack_width_limit_mm=crack["limit_mm"],
        crack_spacing_mm=crack["spacing_mm"],
        crack_effective_area_mm2=crack["effective_area_mm2"],
        crack_reinforcement_ratio=crack["reinforcement_ratio"],
        crack_mean_strain=crack["mean_strain"],
        capacity_knm_per_m=capacity,
        utilization=utilization,
    )


def design_storage_panel(results: "StorageLoadResults", inputs: StoragePanelDesignInputs) -> StoragePanelDesignResult | None:
    if not results.rail_loads:
        return None
    rail = results.rail_loads[0]
    strength_factor = max(results.inputs.uls_factor, 0.0)
    service_factor = max(results.inputs.sls_factor, 0.0)
    positive_service = rail.panel_positive_moment_knm_per_m * service_factor
    negative_service = rail.panel_negative_moment_knm_per_m * service_factor
    positive_demand = rail.panel_positive_moment_knm_per_m * strength_factor
    negative_demand = rail.panel_negative_moment_knm_per_m * strength_factor
    thicknesses = sorted(_parse_number_list(inputs.thicknesses_mm, (125.0, 150.0, 175.0, 200.0, 225.0, 250.0, 275.0, 300.0)))
    for thickness in thicknesses:
        if thickness + 1e-9 < inputs.crack_min_thickness_mm:
            continue
        positive = _select_panel_face_reo(
            "+M face",
            positive_demand,
            positive_service,
            rail.panel_positive_moment_level_mm,
            thickness,
            inputs,
        )
        negative = _select_panel_face_reo(
            "-M face",
            negative_demand,
            negative_service,
            rail.panel_negative_moment_level_mm,
            thickness,
            inputs,
        )
        if positive is None or negative is None:
            continue
        deflection, deflection_y, deflection_limit, gross_i, effective_i, elastic_modulus = panel_service_deflection(
            results,
            thickness,
            inputs.concrete_f_c_mpa,
            inputs.deflection_limit_ratio,
            inputs.deflection_stiffness_factor,
        )
        if deflection > deflection_limit + 1e-9:
            continue
        effective_depth = min(
            panel_flexural_capacity_knm_per_m(thickness, positive.bar_diameter_mm, positive.spacing_mm, inputs.concrete_f_c_mpa, inputs.reo_f_y_mpa, inputs.cover_mm, inputs.strength_phi)[1],
            panel_flexural_capacity_knm_per_m(thickness, negative.bar_diameter_mm, negative.spacing_mm, inputs.concrete_f_c_mpa, inputs.reo_f_y_mpa, inputs.cover_mm, inputs.strength_phi)[1],
        )
        return StoragePanelDesignResult(
            thickness_mm=thickness,
            concrete_f_c_mpa=inputs.concrete_f_c_mpa,
            reo_f_y_mpa=inputs.reo_f_y_mpa,
            cover_mm=inputs.cover_mm,
            effective_depth_mm=effective_depth,
            elastic_modulus_mpa=elastic_modulus,
            gross_i_mm4_m=gross_i,
            effective_i_mm4_m=effective_i,
            deflection_mm=deflection,
            deflection_level_mm=deflection_y,
            deflection_limit_mm=deflection_limit,
            positive_face=positive,
            negative_face=negative,
        )
    return None


def _storage_pressure_integral_kn_per_m(base_pressure_kpa: float, height_m: float, y_m: float) -> float:
    y = min(max(y_m, 0.0), height_m)
    return base_pressure_kpa * (height_m * y - y * y / 2.0) / max(height_m, 1e-9)


def _storage_load_moment_integral_knm_per_m(base_pressure_kpa: float, height_m: float, y_m: float) -> float:
    y = min(max(y_m, 0.0), height_m)
    return base_pressure_kpa * (height_m * y * y / 2.0 - y * y * y / 6.0) / max(height_m, 1e-9)


def _storage_panel_moment_at_y(base_reaction_kn_m: float, rail_reaction_kn_m: float, rail_level_m: float, base_pressure_kpa: float, height_m: float, y_m: float) -> float:
    y = min(max(y_m, 0.0), height_m)
    moment = base_reaction_kn_m * y
    if y > rail_level_m:
        moment += rail_reaction_kn_m * (y - rail_level_m)
    moment -= _storage_load_moment_integral_knm_per_m(base_pressure_kpa, height_m, y)
    return moment


def _storage_panel_moment_envelope(base_reaction_kn_m: float, rail_reaction_kn_m: float, rail_level_m: float, base_pressure_kpa: float, height_m: float) -> dict[str, float]:
    if height_m <= 0.0 or rail_level_m <= 0.0 or base_pressure_kpa <= 0.0:
        return {
            "max_abs_knm_per_m": 0.0,
            "max_abs_level_m": 0.0,
            "positive_knm_per_m": 0.0,
            "positive_level_m": 0.0,
            "negative_knm_per_m": 0.0,
            "negative_level_m": 0.0,
            "rail_knm_per_m": 0.0,
        }

    candidates = {0.0, min(rail_level_m, height_m), height_m}

    # Add stationary points where shear is zero.  The pressure resultant from
    # the base to y is p0 * (H*y - y^2/2) / H.
    for reaction, y_min, y_max in [
        (base_reaction_kn_m, 0.0, min(rail_level_m, height_m)),
        (base_reaction_kn_m + rail_reaction_kn_m, min(rail_level_m, height_m), height_m),
    ]:
        if y_max <= y_min + 1e-12:
            continue
        a = base_pressure_kpa / (2.0 * max(height_m, 1e-9))
        b = -base_pressure_kpa
        c = reaction
        discriminant = b * b - 4.0 * a * c
        if discriminant < -1e-12:
            continue
        root = math.sqrt(max(discriminant, 0.0))
        for y in [(-b - root) / (2.0 * a), (-b + root) / (2.0 * a)]:
            if y_min - 1e-9 <= y <= y_max + 1e-9:
                candidates.add(min(max(y, 0.0), height_m))

    signed_moments = [
        (_storage_panel_moment_at_y(base_reaction_kn_m, rail_reaction_kn_m, rail_level_m, base_pressure_kpa, height_m, y), y)
        for y in candidates
    ]
    max_abs_moment, max_abs_y = max(
        ((abs(moment), y) for moment, y in signed_moments),
        key=lambda item: item[0],
    )
    positive_moment, positive_y = max(signed_moments, key=lambda item: item[0])
    negative_moment, negative_y = min(signed_moments, key=lambda item: item[0])
    if positive_moment < 0.0:
        positive_moment, positive_y = 0.0, 0.0
    if negative_moment > 0.0:
        negative_moment, negative_y = 0.0, 0.0
    rail_moment = _storage_panel_moment_at_y(base_reaction_kn_m, rail_reaction_kn_m, rail_level_m, base_pressure_kpa, height_m, min(rail_level_m, height_m))
    return {
        "max_abs_knm_per_m": max_abs_moment,
        "max_abs_level_m": max_abs_y,
        "positive_knm_per_m": positive_moment,
        "positive_level_m": positive_y,
        "negative_knm_per_m": abs(negative_moment),
        "negative_level_m": negative_y,
        "rail_knm_per_m": rail_moment,
    }


def _calculate_vertical_panel_storage_loads(inputs: StorageLoadInputs, result: StorageLoadResults, height_m: float, bay_m: float, rail_level_mm: float) -> None:
    average_pressure_kpa = result.base_pressure_kpa / 2.0
    line_load_kn_m = average_pressure_kpa * bay_m
    result.total_per_wall_kn = line_load_kn_m * height_m
    result.rail_reaction_per_wall_kn = result.total_per_wall_kn * (result.resultant_level_mm / rail_level_mm)
    result.base_reaction_per_wall_kn = result.total_per_wall_kn - result.rail_reaction_per_wall_kn
    rail_line_load_kn_m = result.rail_reaction_per_wall_kn / bay_m
    rail_level_m = rail_level_mm / 1000.0
    rail_reaction_kn_m = result.rail_reaction_per_wall_kn / bay_m
    base_reaction_kn_m = result.base_reaction_per_wall_kn / bay_m
    panel_moments = _storage_panel_moment_envelope(
        base_reaction_kn_m,
        rail_reaction_kn_m,
        rail_level_m,
        result.base_pressure_kpa,
        height_m,
    )
    rail_bending_moment_knm = rail_line_load_kn_m * bay_m * bay_m / 8.0
    result.rail_loads.append(
        StorageRailLoad(
            level_mm=rail_level_mm,
            tributary_height_m=height_m,
            pressure_kpa=result.base_pressure_kpa,
            line_load_kn_m=line_load_kn_m,
            total_resultant_kn=result.total_per_wall_kn,
            rail_line_load_kn_m=rail_line_load_kn_m,
            rail_reaction_kn=result.rail_reaction_per_wall_kn,
            base_reaction_kn=result.base_reaction_per_wall_kn,
            panel_max_moment_knm_per_m=panel_moments["max_abs_knm_per_m"],
            panel_max_moment_level_mm=panel_moments["max_abs_level_m"] * 1000.0,
            panel_positive_moment_knm_per_m=panel_moments["positive_knm_per_m"],
            panel_positive_moment_level_mm=panel_moments["positive_level_m"] * 1000.0,
            panel_negative_moment_knm_per_m=panel_moments["negative_knm_per_m"],
            panel_negative_moment_level_mm=panel_moments["negative_level_m"] * 1000.0,
            panel_rail_moment_knm_per_m=panel_moments["rail_knm_per_m"],
            rail_bending_moment_knm=rail_bending_moment_knm,
        )
    )


def _calculate_centre_stub_storage_loads(inputs: StorageLoadInputs, result: StorageLoadResults, height_m: float, bay_m: float, rail_level_mm: float) -> None:
    half_bay_m = bay_m / 2.0
    average_pressure_kpa = result.base_pressure_kpa / 2.0
    simple_span_resultant_kn = average_pressure_kpa * half_bay_m * height_m
    if is_continuous_stub_panel_mode(inputs):
        panel_resultant_kn = simple_span_resultant_kn * 5.0 / 4.0
        direct_column_reaction_kn = simple_span_resultant_kn * 3.0 / 8.0
    else:
        panel_resultant_kn = simple_span_resultant_kn
        direct_column_reaction_kn = simple_span_resultant_kn / 2.0
    result.total_per_wall_kn = panel_resultant_kn
    result.rail_reaction_per_wall_kn = panel_resultant_kn * (result.resultant_level_mm / rail_level_mm)
    result.base_reaction_per_wall_kn = panel_resultant_kn - result.rail_reaction_per_wall_kn
    rail_point_load_kn = result.rail_reaction_per_wall_kn
    equivalent_line_load_kn_m = rail_point_load_kn / bay_m
    if is_continuous_stub_panel_mode(inputs):
        panel_positive_moment_knm_per_m = 9.0 * result.base_pressure_kpa * half_bay_m**2 / 128.0
        panel_negative_moment_knm_per_m = result.base_pressure_kpa * half_bay_m**2 / 8.0
        # Horizontal panel bending peaks at the lowest strip (maximum p).
        # The negative moment is over the centre stub at this same elevation.
        panel_negative_level_mm = 0.0
    else:
        panel_positive_moment_knm_per_m = result.base_pressure_kpa * half_bay_m**2 / 8.0
        panel_negative_moment_knm_per_m = 0.0
        panel_negative_level_mm = 0.0
    panel_max_moment_knm_per_m = max(panel_positive_moment_knm_per_m, panel_negative_moment_knm_per_m)
    rail_bending_moment_knm = rail_point_load_kn * bay_m / 4.0
    result.rail_loads.append(
        StorageRailLoad(
            level_mm=rail_level_mm,
            tributary_height_m=height_m,
            pressure_kpa=result.base_pressure_kpa,
            line_load_kn_m=average_pressure_kpa * half_bay_m,
            total_resultant_kn=result.total_per_wall_kn,
            rail_line_load_kn_m=equivalent_line_load_kn_m,
            # An internal portal column supports the rail spans in the bays on
            # both sides, so its frame reaction equals the full stub-head load.
            rail_reaction_kn=rail_point_load_kn,
            base_reaction_kn=result.base_reaction_per_wall_kn,
            panel_max_moment_knm_per_m=panel_max_moment_knm_per_m,
            panel_max_moment_level_mm=panel_negative_level_mm if panel_negative_moment_knm_per_m >= panel_positive_moment_knm_per_m else 0.0,
            panel_positive_moment_knm_per_m=panel_positive_moment_knm_per_m,
            panel_positive_moment_level_mm=0.0,
            panel_negative_moment_knm_per_m=panel_negative_moment_knm_per_m,
            panel_negative_moment_level_mm=panel_negative_level_mm,
            panel_rail_moment_knm_per_m=0.0,
            rail_bending_moment_knm=rail_bending_moment_knm,
            rail_load_type="centre_point",
            panel_span_m=half_bay_m,
            stub_head_reaction_kn=rail_point_load_kn,
            stub_base_reaction_kn=result.base_reaction_per_wall_kn,
            direct_column_reaction_kn=direct_column_reaction_kn * 2.0,
        )
    )


def calculate_storage_loads(inputs: StorageLoadInputs) -> StorageLoadResults:
    height_m = _positive(inputs.stored_height_m)
    bay_m = _positive(inputs.bay_spacing_m)
    gamma = _positive(inputs.bulk_density_kn_m3)
    ka = calculate_ka(inputs.internal_friction_angle_deg, inputs.backfill_slope_deg)
    rail_level_mm = _positive(inputs.rail_height_mm)

    result = StorageLoadResults(
        inputs=inputs,
        ka=ka,
        base_pressure_kpa=gamma * ka * height_m,
        resultant_level_mm=height_m * 1000.0 / 3.0,
    )
    if not inputs.enabled or height_m <= 0.0 or bay_m <= 0.0 or gamma <= 0.0 or ka <= 0.0 or rail_level_mm <= 0.0:
        return result
    if not inputs.left_wall and not inputs.right_wall:
        return result

    if is_centre_stub_storage_mode(inputs):
        _calculate_centre_stub_storage_loads(inputs, result, height_m, bay_m, rail_level_mm)
    else:
        _calculate_vertical_panel_storage_loads(inputs, result, height_m, bay_m, rail_level_mm)
    return result


def storage_factor_for_combination(inputs: StorageLoadInputs, combination) -> float:
    if not inputs.enabled:
        return 0.0
    if getattr(combination, "is_serviceability", False):
        if getattr(combination, "serviceability_component", None) in {"G", "Ws"}:
            return 0.0
        return inputs.sls_factor
    # Storage has its own explicit factor; the roof/crane Q factor must not
    # override it. A separate no-storage case is included in the envelope.
    return inputs.uls_factor


def apply_storage_loads_to_columns(
    structure,
    results: StorageLoadResults,
    left_nodes,
    right_nodes,
    factor: float = 1.0,
    left_base_y_mm: float = 0.0,
    right_base_y_mm: float = 0.0,
):
    arrows = []
    if factor == 0.0 or not results.rail_loads:
        return arrows
    inputs = results.inputs
    wall_targets = []
    if inputs.left_wall:
        wall_targets.append((left_nodes, -1.0, left_base_y_mm))
    if inputs.right_wall:
        wall_targets.append((right_nodes, +1.0, right_base_y_mm))
    if inputs.reverse_direction:
        wall_targets = [(nodes, -direction, base_y_mm) for nodes, direction, base_y_mm in wall_targets]
    # Validate the complete load path before adding any load. Load-only arrow
    # collectors have no elements; real analysis structures must cover H.
    planned = []
    for nodes, direction, base_y_mm in wall_targets:
        by_level = {round(node.y, 6): node for node in nodes if node is not None}
        for rail in results.rail_loads:
            node = by_level.get(round(base_y_mm + rail.level_mm, 6))
            if node is None:
                raise ValueError("Storage rail reaction has no supporting column node.")
            segments = []
            if rail.rail_load_type == "centre_point" and rail.direct_column_reaction_kn > 0.0:
                base_node = by_level.get(round(base_y_mm, 6))
                loaded_top_y_mm = base_y_mm + rail.tributary_height_m * 1000.0
                loaded_top_node = by_level.get(round(loaded_top_y_mm, 6))
                if base_node is None or loaded_top_node is None:
                    raise ValueError("Storage direct panel load has no base/top column node.")
                if hasattr(structure, "elements"):
                    low, high = base_y_mm, loaded_top_y_mm
                    for element in structure.elements:
                        if element.group not in {"LEFT_COLUMN", "RIGHT_COLUMN"}:
                            continue
                        a, b = element.start, element.end
                        if max(abs(a.x-base_node.x), abs(b.x-base_node.x)) > 1e-6:
                            continue
                        if min(a.y,b.y) < low-1e-6 or max(a.y,b.y) > high+1e-6:
                            continue
                        if abs(a.y-b.y) > 1e-6:
                            segments.append(element)
                    intervals = sorted((min(e.start.y,e.end.y), max(e.start.y,e.end.y)) for e in segments)
                    cursor = low
                    for a,b in intervals:
                        if abs(a-cursor) > 1e-6:
                            raise ValueError("Storage panel column load path has a gap or overlap.")
                        cursor = b
                    if abs(cursor-high) > 1e-6 or not hasattr(structure, "add_linear_global_line_load"):
                        raise ValueError("Storage panel load requires a complete distributed column load path.")
            planned.append((rail, node, direction, base_y_mm, segments))
    for rail, node, direction, base_y_mm, segments in planned:
        load_n = rail.rail_reaction_kn * factor * 1000.0 * direction
        structure.add_load(node.ux, load_n)
        arrows.append((node.x, node.y, load_n, 0.0))
        if rail.rail_load_type == "centre_point" and rail.direct_column_reaction_kn > 0.0:
            height_mm = rail.tributary_height_m * 1000.0
            direct_load_n = rail.direct_column_reaction_kn * factor * 1000.0 * direction
            q0 = 2.0 * direct_load_n / height_mm
            for element in segments:
                qa = q0 * (1.0-(element.start.y-base_y_mm)/height_mm)
                qb = q0 * (1.0-(element.end.y-base_y_mm)/height_mm)
                structure.add_linear_global_line_load(element, fx_start=qa, fx_end=qb)
            arrows.append((node.x, base_y_mm+height_mm/3.0, direct_load_n, 0.0))
    return arrows


def storage_summary_lines(results: StorageLoadResults, factor: float = 1.0) -> list[str]:
    inputs = results.inputs
    if not inputs.enabled:
        return []
    lines = ["", "STORAGE LOAD"]
    lines.append(f"{'Material':<16}: {inputs.material_name}")
    lines.append(f"{'Load path':<16}: {inputs.load_path_mode}")
    if is_centre_stub_storage_mode(inputs):
        lines.append(f"{'Panel support':<16}: {inputs.panel_support_mode}")
    lines.append(f"{'Density gamma':<16}: {inputs.bulk_density_kn_m3:>8.3f} kN/m3")
    lines.append(f"{'Phi internal':<16}: {inputs.internal_friction_angle_deg:>8.3f} deg")
    lines.append(f"{'Beta slope':<16}: {inputs.backfill_slope_deg:>8.3f} deg")
    lines.append(f"{'Ka calculated':<16}: {results.ka:>8.3f}")
    lines.append(f"{'Stored height':<16}: {inputs.stored_height_m:>8.3f} m")
    lines.append(f"{'Bay spacing':<16}: {inputs.bay_spacing_m:>8.3f} m")
    lines.append(f"{'Base pressure':<16}: {results.base_pressure_kpa:>8.3f} kPa")
    lines.append(f"{'Resultant y':<16}: {results.resultant_level_mm:>8.0f} mm above support")
    lines.append(f"{'Load factor':<16}: x {factor:.3f}")
    side_text = []
    direction_text = "inward" if inputs.reverse_direction else "outward"
    if inputs.left_wall:
        side_text.append(f"left wall {'+X' if inputs.reverse_direction else '-X'}")
    if inputs.right_wall:
        side_text.append(f"right wall {'-X' if inputs.reverse_direction else '+X'}")
    lines.append(f"{'Direction':<16}: {direction_text}")
    lines.append(f"{'Applied sides':<16}: {', '.join(side_text) if side_text else 'none'}")
    if is_centre_stub_storage_mode(inputs):
        panel_load_per_bay_kn = results.base_pressure_kpa * 0.5 * inputs.bay_spacing_m * inputs.stored_height_m
        lines.append(f"{'Panel load/bay':<16}: {panel_load_per_bay_kn * factor:>8.3f} kN total lateral")
        lines.append(f"{'Stub tributary':<16}: {results.total_per_wall_kn * factor:>8.3f} kN per bay")
    else:
        lines.append(f"{'Total lateral':<16}: {results.total_per_wall_kn * factor:>8.3f} kN per wall")
    rail_label = "Stub head load" if is_centre_stub_storage_mode(inputs) else "Rail reaction"
    lines.append(f"{rail_label:<16}: {results.rail_reaction_per_wall_kn * factor:>8.3f} kN per wall")
    lines.append(f"{'Base reaction':<16}: {results.base_reaction_per_wall_kn * factor:>8.3f} kN per wall, ignored by frame")
    for index, rail in enumerate(results.rail_loads, start=1):
        lines.append(
            f"Rail {index:<10}: y={rail.level_mm:>7.0f} mm above support, base p={rail.pressure_kpa:>7.3f} kPa, "
            f"H={rail.tributary_height_m:.3f} m, frame load={rail.rail_reaction_kn * factor:>7.3f} kN"
        )
        if rail.rail_load_type == "centre_point":
            lines.append(
                f"{'Stub mode':<16}: panel span={rail.panel_span_m:>7.3f} m each side, "
                f"stub head={rail.stub_head_reaction_kn * factor:>7.3f} kN, frame column={rail.rail_reaction_kn * factor:>7.3f} kN from two bays"
            )
            lines.append(
                f"{'Direct column':<16}: triangular panel reaction={rail.direct_column_reaction_kn * factor:>7.3f} kN from panels on both sides"
            )
        lines.append(
            f"{'Panel moment':<16}: {rail.panel_max_moment_knm_per_m * factor:>8.3f} kNm/m max "
            f"at y={rail.panel_max_moment_level_mm:>7.0f} mm; rail node M={rail.panel_rail_moment_knm_per_m * factor:>8.3f} kNm/m"
        )
        lines.append(
            f"{'Panel +M face':<16}: {rail.panel_positive_moment_knm_per_m * factor:>8.3f} kNm/m "
            f"at y={rail.panel_positive_moment_level_mm:>7.0f} mm"
        )
        lines.append(
            f"{'Panel -M face':<16}: {rail.panel_negative_moment_knm_per_m * factor:>8.3f} kNm/m "
            f"at y={rail.panel_negative_moment_level_mm:>7.0f} mm"
        )
        lines.append(
            f"{'Rail moment':<16}: {rail.rail_bending_moment_knm * factor:>8.3f} kNm "
            f"from {'P' if rail.rail_load_type == 'centre_point' else 'w'}="
            f"{(rail.stub_head_reaction_kn if rail.rail_load_type == 'centre_point' else rail.rail_line_load_kn_m) * factor:>7.3f} "
            f"{'kN at midspan' if rail.rail_load_type == 'centre_point' else 'kN/m'} over L={inputs.bay_spacing_m:.3f} m"
        )
    return lines
