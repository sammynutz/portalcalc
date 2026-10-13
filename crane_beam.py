from dataclasses import dataclass
import math


E_STEEL_KN_PER_MM2 = 200.0
E_STEEL_MPA = 200_000.0
G_STEEL_MPA = 80_000.0


@dataclass
class CraneBeamInputs:
    span_mm: float = 9000.0
    wheelbase_mm: float = 3500.0
    load_height_mm: float = 120.0
    beam_self_weight_kn_m: float = 1.0
    rail_self_weight_kn_m: float = 0.123
    pmax_21_kn: float = 44.2
    pmax_22_kn: float = 44.2
    pmin_11_kn: float = 15.2
    pmin_12_kn: float = 15.2
    pstatic_21_kn: float = 40.2
    pstatic_22_kn: float = 40.2
    pstatic_min_11_kn: float = 13.8
    pstatic_min_12_kn: float = 13.8
    phb_21_kn: float = 6.7
    phb_22_kn: float = -6.7
    phi_2_kn: float = 4.6
    dead_factor: float = 1.25
    hoisted_factor: float = 1.50
    inertial_factor: float = 1.40
    oblique_factor: float = 1.50
    ix_mm4: float = 323.5e6
    iy_top_mm4: float = 78.5e6
    iy_oblique_mm4: float = 78.5e6
    vertical_limit_ratio: float = 500.0
    vertical_limit_cap_mm: float = 60.0
    horizontal_limit_ratio: float = 600.0
    horizontal_limit_cap_mm: float = 10.0
    include_self_weight_in_deflection: bool = True
    beam_name: str = "410 UB 59.7"
    pfc_cap_name: str = "300 PFC"
    rail_name: str = "12.5 kg/m rail"
    beam_depth_mm: float = 414.0
    beam_flange_width_mm: float = 178.0
    beam_flange_thickness_mm: float = 12.8
    beam_minor_iy_mm4: float = 12_100_000.0
    beam_j_mm4: float = 337_000.0
    beam_iw_mm6: float = 467_000_000_000.0
    beam_zx_mm3: float = 1_585_000.0
    beam_zy_mm3: float = 183_000.0
    beam_fy_mpa: float = 300.0
    beam_flange_fy_mpa: float = 300.0
    pfc_cap_centroid_mm: float = 27.2
    pfc_cap_area_mm2: float = 5_110.0
    pfc_cap_ix_mm4: float = 72_400_000.0
    pfc_cap_iy_mm4: float = 4_040_000.0
    pfc_cap_zex_mm3: float = 564_000.0
    pfc_cap_zey_mm3: float = 82_300.0
    pfc_cap_j_mm4: float = 304_000.0
    pfc_cap_iw_mm6: float = 58_100_000_000.0
    pfc_cap_depth_mm: float = 300.0
    pfc_cap_width_mm: float = 90.0
    pfc_cap_thickness_mm: float = 16.0
    pfc_cap_web_thickness_mm: float = 8.0
    pfc_cap_fy_mpa: float = 300.0
    composite_area_mm2: float = 0.0
    composite_depth_mm: float = 0.0
    composite_centroid_top_mm: float = 0.0
    composite_minor_iy_mm4: float = 0.0
    composite_top_flange_iy_mm4: float = 0.0
    composite_j_mm4: float = 0.0
    composite_iw_mm6: float = 0.0
    composite_ztop_mm3: float = 0.0
    composite_zbottom_mm3: float = 0.0
    composite_zmin_mm3: float = 0.0
    composite_ry_mm: float = 0.0
    rail_bte_mm: float = 50.0
    rail_eccentricity_k: float = 4.0
    capacity_phi: float = 0.9
    as4100_ke: float = 1.4
    as4100_alpha_m: float = 1.0


@dataclass
class CraneBeamResults:
    x1_mm: float
    x2_mm: float
    c_mm: float
    xm_mm: float
    n_mm: float
    rav_kn: float
    rbv_kn: float
    shear_left_p1_kn: float
    shear_right_p1_kn: float
    shear_left_p2_kn: float
    shear_right_p2_kn: float
    mv1_knm: float
    mv2_knm: float
    mv12_knm: float
    mvm_knm: float
    mv_max_knm: float
    mhl_coincident_knm: float
    mhl_midspan_single_knm: float
    mhl_knm: float
    mho_coincident_knm: float
    mho_midspan_single_knm: float
    mho_knm: float
    rai_kn: float
    rbi_kn: float
    rao_kn: float
    rbo_kn: float
    rail_reaction_ends_kn: float
    frame_reaction_max_kn: float
    frame_reaction_min_kn: float
    lateral_reaction_both_sides_kn: float
    oblique_reaction_max_kn: float
    oblique_reaction_min_kn: float
    vertical_deflection_mm: float
    lateral_deflection_mm: float
    oblique_deflection_mm: float
    vertical_limit_mm: float
    horizontal_limit_mm: float
    vertical_deflection_ratio: float
    lateral_deflection_ratio: float
    oblique_deflection_ratio: float
    required_ix_vertical_mm4: float
    required_iy_lateral_mm4: float
    required_iy_oblique_mm4: float
    rail_eccentricity_mm: float
    top_centroid_mm: float
    bottom_centroid_mm: float
    lever_df_mm: float
    top_flange_multiplier: float
    bottom_flange_multiplier: float
    eccentricity_multiplier: float
    mh_design_knm: float
    mh_top_knm: float
    mh_bottom_knm: float
    phi_mbx_knm: float
    phi_msx_knm: float
    as4100_le_mm: float
    as4100_mo_knm: float
    as4100_alpha_s: float
    as4100_alpha_m_effective: float
    as4100_beta_x_mm: float
    ref8_mob_knm: float
    ref8_alpha_sb: float
    phi_mbx_as4100_knm: float
    phi_mbx_ref8_knm: float
    phi_msy_knm: float
    phi_msy_top_knm: float
    phi_msy_bottom_knm: float
    vertical_bending_ratio: float
    top_flange_ratio: float
    bottom_flange_ratio: float
    combined_top_ratio: float
    combined_bottom_ratio: float
    composite_area_mm2: float
    composite_depth_mm: float
    composite_centroid_top_mm: float
    composite_minor_iy_mm4: float
    composite_top_flange_iy_mm4: float
    composite_j_mm4: float
    composite_iw_mm6: float
    composite_ztop_mm3: float
    composite_zbottom_mm3: float
    composite_zmin_mm3: float
    composite_ry_mm: float


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _reaction_pair(span_mm: float, loads: list[tuple[float, float]], udl_kn_mm: float = 0.0) -> tuple[float, float]:
    total = sum(p for p, _ in loads) + udl_kn_mm * span_mm
    moment_about_a = sum(p * x for p, x in loads) + udl_kn_mm * span_mm * (span_mm / 2.0)
    rb = moment_about_a / span_mm
    return total - rb, rb


def _moment_at(span_mm: float, x_mm: float, loads: list[tuple[float, float]], udl_kn_mm: float = 0.0) -> float:
    ra, _ = _reaction_pair(span_mm, loads, udl_kn_mm)
    moment = ra * x_mm - udl_kn_mm * x_mm * x_mm / 2.0
    for p, a in loads:
        if x_mm >= a:
            moment -= p * (x_mm - a)
    return moment


def _point_deflection(span_mm: float, x_mm: float, load_kn: float, a_mm: float, ei_kn_mm2: float) -> float:
    b_mm = span_mm - a_mm
    if x_mm <= a_mm:
        return load_kn * b_mm * x_mm * (span_mm**2 - b_mm**2 - x_mm**2) / (6.0 * span_mm * ei_kn_mm2)
    return load_kn * a_mm * (span_mm - x_mm) * (span_mm**2 - a_mm**2 - (span_mm - x_mm) ** 2) / (6.0 * span_mm * ei_kn_mm2)


def _udl_deflection(span_mm: float, x_mm: float, udl_kn_mm: float, ei_kn_mm2: float) -> float:
    return udl_kn_mm * x_mm * (span_mm**3 - 2.0 * span_mm * x_mm**2 + x_mm**3) / (24.0 * ei_kn_mm2)


def _max_deflection(span_mm: float, loads: list[tuple[float, float]], udl_kn_mm: float, inertia_mm4: float) -> float:
    ei = E_STEEL_KN_PER_MM2 * max(inertia_mm4, 1.0)
    max_delta = 0.0
    for index in range(1001):
        x = span_mm * index / 1000.0
        delta = _udl_deflection(span_mm, x, udl_kn_mm, ei)
        for load_kn, a_mm in loads:
            delta += _point_deflection(span_mm, x, load_kn, a_mm, ei)
        max_delta = max(max_delta, abs(delta))
    return max_delta


def _required_inertia(span_mm: float, loads: list[tuple[float, float]], udl_kn_mm: float, limit_mm: float) -> float:
    if limit_mm <= 1e-9:
        return math.inf
    unit_delta = _max_deflection(span_mm, loads, udl_kn_mm, 1.0)
    return unit_delta / limit_mm


def _toolkit_moment_reduction(phi_msx_knm: float, mo_knm: float, phi: float) -> float:
    if phi_msx_knm <= 0.0:
        return 0.0
    if not math.isfinite(mo_knm) or mo_knm <= 0.0:
        return 1.0
    msx_knm = phi_msx_knm / max(phi, 1e-9)
    ratio = max(msx_knm / mo_knm, 0.0)
    return min(max(0.6 * (math.sqrt(ratio**2 + 3.0) - ratio), 0.0), 1.0)


def _as4100_mo_knm(inputs: CraneBeamInputs, span_mm: float, beta_x_mm: float) -> tuple[float, float]:
    le = max(inputs.as4100_ke * span_mm, 1.0)
    iy = inputs.composite_minor_iy_mm4
    if iy <= 0.0:
        cap_offset = inputs.beam_flange_width_mm / 2.0 + inputs.pfc_cap_centroid_mm
        cap_parallel_axis_iy = inputs.pfc_cap_area_mm2 * cap_offset**2
        iy = inputs.beam_minor_iy_mm4 + inputs.pfc_cap_iy_mm4 + cap_parallel_axis_iy
    j = inputs.composite_j_mm4 if inputs.composite_j_mm4 > 0.0 else inputs.beam_j_mm4 + inputs.pfc_cap_j_mm4
    iw = inputs.composite_iw_mm6 if inputs.composite_iw_mm6 > 0.0 else inputs.beam_iw_mm6 + inputs.pfc_cap_iw_mm6
    iy = max(iy, 0.0)
    j = max(j, 0.0)
    iw = max(iw, 0.0)
    if iy <= 0.0 or j <= 0.0:
        return le, math.inf
    elastic_minor = math.pi**2 * E_STEEL_MPA * iy / le**2
    torsion_warping = G_STEEL_MPA * j + math.pi**2 * E_STEEL_MPA * iw / le**2
    beta = max(beta_x_mm, 0.0)
    mo = (
        math.sqrt(max(elastic_minor * (torsion_warping + beta**2 * elastic_minor / 4.0), 0.0))
        + beta * elastic_minor / 2.0
    ) / 1_000_000.0
    return le, mo


def calculate_crane_beam(inputs: CraneBeamInputs) -> CraneBeamResults:
    span = max(inputs.span_mm, 1.0)
    wheelbase = _clamp(inputs.wheelbase_mm, 0.0, span)
    p1 = max(inputs.pmax_21_kn, 0.0) * inputs.hoisted_factor
    p2 = max(inputs.pmax_22_kn, 0.0) * inputs.hoisted_factor
    total_p = max(p1 + p2, 1e-9)
    c = min(p1, p2) * wheelbase / total_p
    x1 = _clamp((span - c) / 2.0, 0.0, span)
    x2 = _clamp(x1 + wheelbase, 0.0, span)
    n = span - x2

    udl_strength = (inputs.beam_self_weight_kn_m + inputs.rail_self_weight_kn_m) * inputs.dead_factor / 1000.0
    vertical_loads = [(p1, x1), (p2, x2)]
    rav, rbv = _reaction_pair(span, vertical_loads, udl_strength)
    shear_left_p1 = rav - udl_strength * x1
    shear_right_p1 = shear_left_p1 - p1
    shear_left_p2 = shear_right_p1 - udl_strength * (x2 - x1)
    shear_right_p2 = shear_left_p2 - p2
    mv1 = _moment_at(span, x1, vertical_loads, udl_strength) / 1000.0
    mv2 = _moment_at(span, x2, vertical_loads, udl_strength) / 1000.0
    mv12 = max(mv1, mv2)
    mvm = _moment_at(span, span / 2.0, vertical_loads, udl_strength) / 1000.0
    mv_max = max(mv12, mvm)

    lateral_loads = [
        (inputs.phb_21_kn * inputs.inertial_factor, x1),
        (inputs.phb_22_kn * inputs.inertial_factor, x2),
    ]
    rai, rbi = _reaction_pair(span, lateral_loads)
    mhl_coincident = max(abs(_moment_at(span, x1, lateral_loads)), abs(_moment_at(span, x2, lateral_loads))) / 1000.0
    mhl_midspan_single = abs(inputs.phb_21_kn * inputs.inertial_factor) * span / 4.0 / 1000.0
    mhl = mhl_coincident

    oblique_loads = [(inputs.phi_2_kn * inputs.oblique_factor, x1)]
    rao, rbo = _reaction_pair(span, oblique_loads)
    mho_coincident = max(abs(_moment_at(span, x1, oblique_loads)), abs(_moment_at(span, x2, oblique_loads))) / 1000.0
    mho_midspan_single = abs(inputs.phi_2_kn * inputs.oblique_factor) * span / 4.0 / 1000.0
    mho = mho_coincident

    composite_area = inputs.composite_area_mm2 if inputs.composite_area_mm2 > 0.0 else inputs.pfc_cap_area_mm2
    composite_depth = inputs.composite_depth_mm if inputs.composite_depth_mm > 0.0 else inputs.beam_depth_mm
    composite_centroid = inputs.composite_centroid_top_mm
    composite_iy = inputs.composite_minor_iy_mm4 if inputs.composite_minor_iy_mm4 > 0.0 else inputs.iy_top_mm4
    composite_top_iy = inputs.composite_top_flange_iy_mm4 if inputs.composite_top_flange_iy_mm4 > 0.0 else inputs.iy_top_mm4
    composite_j = inputs.composite_j_mm4 if inputs.composite_j_mm4 > 0.0 else inputs.beam_j_mm4 + inputs.pfc_cap_j_mm4
    composite_iw = inputs.composite_iw_mm6 if inputs.composite_iw_mm6 > 0.0 else inputs.beam_iw_mm6 + inputs.pfc_cap_iw_mm6
    composite_ztop = inputs.composite_ztop_mm3
    composite_zbottom = inputs.composite_zbottom_mm3
    composite_zmin = inputs.composite_zmin_mm3 if inputs.composite_zmin_mm3 > 0.0 else min(
        value for value in [composite_ztop, composite_zbottom, inputs.beam_zx_mm3] if value > 0.0
    )
    composite_ry = inputs.composite_ry_mm

    udl_service = (inputs.beam_self_weight_kn_m + inputs.rail_self_weight_kn_m) / 1000.0 if inputs.include_self_weight_in_deflection else 0.0
    service_loads = [(max(inputs.pstatic_21_kn, 0.0), x1), (max(inputs.pstatic_22_kn, 0.0), x2)]
    vertical_delta = _max_deflection(span, service_loads, udl_service, inputs.ix_mm4)
    lateral_delta = _max_deflection(span, [(abs(inputs.phb_21_kn), span / 2.0)], 0.0, inputs.iy_top_mm4)
    oblique_delta = _max_deflection(span, [(abs(inputs.phi_2_kn), span / 2.0)], 0.0, inputs.iy_oblique_mm4)

    vertical_limit = min(span / inputs.vertical_limit_ratio, inputs.vertical_limit_cap_mm)
    horizontal_limit = min(span / inputs.horizontal_limit_ratio, inputs.horizontal_limit_cap_mm)

    rail_reaction_ends = (inputs.rail_self_weight_kn_m * span / 1000.0) / 2.0
    service_ra, service_rb = _reaction_pair(span, service_loads, inputs.rail_self_weight_kn_m / 1000.0)

    top_centroid = max(inputs.pfc_cap_centroid_mm, inputs.beam_flange_thickness_mm / 2.0, 0.0)
    bottom_centroid = max(inputs.beam_flange_thickness_mm / 2.0, 0.0)
    lever_df = max(inputs.beam_depth_mm - top_centroid - bottom_centroid, 1.0)
    mh_design = max(abs(mhl), abs(mho_coincident))
    rail_eccentricity = span / 1000.0 + inputs.rail_bte_mm / max(inputs.rail_eccentricity_k, 1e-9)
    top_multiplier = (inputs.load_height_mm + top_centroid + lever_df) / lever_df
    bottom_multiplier = abs(1.0 - top_multiplier)
    eccentricity_multiplier = rail_eccentricity / lever_df
    mh_top = top_multiplier * mh_design + eccentricity_multiplier * mv_max
    mh_bottom = bottom_multiplier * mh_design + eccentricity_multiplier * mv_max
    beta_factor = 0.8 * (2.0 * 0.928 - 1.0)
    section_cap_factor = 0.84
    beta_x = beta_factor * lever_df
    composite_zx = inputs.beam_zx_mm3 + section_cap_factor * inputs.pfc_cap_zex_mm3
    phi_msx = inputs.capacity_phi * inputs.beam_fy_mpa * max(composite_zx, 0.0) / 1_000_000.0
    as4100_le, as4100_mo = _as4100_mo_knm(inputs, span, beta_x)
    as4100_alpha_s = _toolkit_moment_reduction(phi_msx, as4100_mo, inputs.capacity_phi)
    alpha_m = max(inputs.as4100_alpha_m, 1.0 + 0.35 * (1.0 - 2.0 * c / span) ** 2)
    phi_mbx_as4100 = min(phi_msx, alpha_m * as4100_alpha_s * phi_msx)
    ref8_mob = 1.25 * as4100_mo
    ref8_alpha_sb = _toolkit_moment_reduction(phi_msx, ref8_mob, inputs.capacity_phi)
    phi_mbx_ref8 = min(phi_msx, ref8_alpha_sb * phi_msx)
    phi_mbx = min(phi_mbx_as4100, phi_mbx_ref8)
    bottom_flange_z = inputs.beam_flange_thickness_mm * inputs.beam_flange_width_mm**2 / 4.0
    phi_msy_bottom = inputs.capacity_phi * inputs.beam_flange_fy_mpa * max(bottom_flange_z, 0.0) / 1_000_000.0
    phi_msy_top = phi_msy_bottom + inputs.capacity_phi * inputs.pfc_cap_fy_mpa * max(inputs.pfc_cap_zex_mm3, 0.0) / 1_000_000.0
    phi_msy = min(phi_msy_top, phi_msy_bottom)
    vertical_ratio = mv_max / phi_mbx if phi_mbx > 1e-9 else math.inf
    top_ratio = mh_top / phi_msy_top if phi_msy_top > 1e-9 else math.inf
    bottom_ratio = mh_bottom / phi_msy_bottom if phi_msy_bottom > 1e-9 else math.inf

    return CraneBeamResults(
        x1_mm=x1,
        x2_mm=x2,
        c_mm=c,
        xm_mm=x1,
        n_mm=n,
        rav_kn=rav,
        rbv_kn=rbv,
        shear_left_p1_kn=shear_left_p1,
        shear_right_p1_kn=shear_right_p1,
        shear_left_p2_kn=shear_left_p2,
        shear_right_p2_kn=shear_right_p2,
        mv1_knm=mv1,
        mv2_knm=mv2,
        mv12_knm=mv12,
        mvm_knm=mvm,
        mv_max_knm=mv_max,
        mhl_coincident_knm=mhl_coincident,
        mhl_midspan_single_knm=mhl_midspan_single,
        mhl_knm=mhl,
        mho_coincident_knm=mho_coincident,
        mho_midspan_single_knm=mho_midspan_single,
        mho_knm=mho,
        rai_kn=rai,
        rbi_kn=rbi,
        rao_kn=rao,
        rbo_kn=rbo,
        rail_reaction_ends_kn=rail_reaction_ends,
        frame_reaction_max_kn=max(service_ra, service_rb),
        frame_reaction_min_kn=min(service_ra, service_rb),
        lateral_reaction_both_sides_kn=max(abs(rai), abs(rbi)),
        oblique_reaction_max_kn=max(abs(rao), abs(rbo)),
        oblique_reaction_min_kn=min(abs(rao), abs(rbo)),
        vertical_deflection_mm=vertical_delta,
        lateral_deflection_mm=lateral_delta,
        oblique_deflection_mm=oblique_delta,
        vertical_limit_mm=vertical_limit,
        horizontal_limit_mm=horizontal_limit,
        vertical_deflection_ratio=span / vertical_delta if vertical_delta > 1e-9 else math.inf,
        lateral_deflection_ratio=span / lateral_delta if lateral_delta > 1e-9 else math.inf,
        oblique_deflection_ratio=span / oblique_delta if oblique_delta > 1e-9 else math.inf,
        required_ix_vertical_mm4=_required_inertia(span, service_loads, udl_service, vertical_limit),
        required_iy_lateral_mm4=_required_inertia(span, [(abs(inputs.phb_21_kn), span / 2.0)], 0.0, horizontal_limit),
        required_iy_oblique_mm4=_required_inertia(span, [(abs(inputs.phi_2_kn), span / 2.0)], 0.0, horizontal_limit),
        rail_eccentricity_mm=rail_eccentricity,
        top_centroid_mm=top_centroid,
        bottom_centroid_mm=bottom_centroid,
        lever_df_mm=lever_df,
        top_flange_multiplier=top_multiplier,
        bottom_flange_multiplier=bottom_multiplier,
        eccentricity_multiplier=eccentricity_multiplier,
        mh_design_knm=mh_design,
        mh_top_knm=mh_top,
        mh_bottom_knm=mh_bottom,
        phi_mbx_knm=phi_mbx,
        phi_msx_knm=phi_msx,
        as4100_le_mm=as4100_le,
        as4100_mo_knm=as4100_mo,
        as4100_alpha_s=as4100_alpha_s,
        as4100_alpha_m_effective=alpha_m,
        as4100_beta_x_mm=beta_x,
        ref8_mob_knm=ref8_mob,
        ref8_alpha_sb=ref8_alpha_sb,
        phi_mbx_as4100_knm=phi_mbx_as4100,
        phi_mbx_ref8_knm=phi_mbx_ref8,
        phi_msy_knm=phi_msy,
        phi_msy_top_knm=phi_msy_top,
        phi_msy_bottom_knm=phi_msy_bottom,
        vertical_bending_ratio=vertical_ratio,
        top_flange_ratio=top_ratio,
        bottom_flange_ratio=bottom_ratio,
        combined_top_ratio=vertical_ratio + top_ratio if math.isfinite(vertical_ratio) and math.isfinite(top_ratio) else math.inf,
        combined_bottom_ratio=vertical_ratio + bottom_ratio if math.isfinite(vertical_ratio) and math.isfinite(bottom_ratio) else math.inf,
        composite_area_mm2=composite_area,
        composite_depth_mm=composite_depth,
        composite_centroid_top_mm=composite_centroid,
        composite_minor_iy_mm4=composite_iy,
        composite_top_flange_iy_mm4=composite_top_iy,
        composite_j_mm4=composite_j,
        composite_iw_mm6=composite_iw,
        composite_ztop_mm3=composite_ztop,
        composite_zbottom_mm3=composite_zbottom,
        composite_zmin_mm3=composite_zmin,
        composite_ry_mm=composite_ry,
    )
