"""
First-pass steel member check helpers for the portal frame calculator.

This module is intentionally small and explicit.  It gives the app a stable
place to collect AS 4100-style capacity checks while the detailed code clauses
are verified against Sam's worked examples / spreadsheets.
"""

from dataclasses import dataclass, field
import math
from typing import Dict, Optional


PHI_STEEL = 0.9
E_STEEL_MPA = 200_000.0


@dataclass
class SectionCheckProperties:
    name: str
    shape_type: str
    fabrication: str
    area_mm2: float
    j_mm4: float
    iy_mm4: float
    iz_mm4: float
    iw_mm6: float = 0.0
    sy_mm3: Optional[float] = None
    sz_mm3: Optional[float] = None
    fy_mpa: float = 300.0
    fyw_mpa: Optional[float] = None
    fu_mpa: float = 440.0
    depth_mm: Optional[float] = None
    flange_width_mm: Optional[float] = None
    flange_thickness_mm: Optional[float] = None
    web_thickness_mm: Optional[float] = None
    kf: float = 1.0
    kt: float = 1.0
    net_area_mm2: Optional[float] = None
    alpha_b: float = 0.0
    nonuniform_shear_stress_ratio: float = 2.0

    @property
    def design_fyw_mpa(self) -> float:
        return self.fyw_mpa if self.fyw_mpa is not None and self.fyw_mpa > 0.0 else self.fy_mpa

    @property
    def design_net_area_mm2(self) -> float:
        return self.net_area_mm2 if self.net_area_mm2 is not None else self.area_mm2

    @property
    def analysis_i_mm4(self) -> float:
        return max(self.iy_mm4, self.iz_mm4)

    @property
    def analysis_s_mm3(self) -> Optional[float]:
        values = [v for v in [self.sy_mm3, self.sz_mm3] if v is not None and v > 0.0]
        return max(values) if values else None

    @property
    def minor_i_mm4(self) -> float:
        return min(self.iy_mm4, self.iz_mm4)

    @property
    def radius_y_mm(self) -> float:
        return math.sqrt(max(self.iy_mm4, 0.0) / self.area_mm2)

    @property
    def radius_z_mm(self) -> float:
        return math.sqrt(max(self.iz_mm4, 0.0) / self.area_mm2)


@dataclass
class MemberActions:
    compression_kn: float = 0.0
    tension_kn: float = 0.0
    shear_kn: float = 0.0
    moment_knm: float = 0.0
    effective_length_y_mm: Optional[float] = None
    effective_length_z_mm: Optional[float] = None
    moment_modifier: float = 1.0
    kt: float = 1.0
    kl: float = 1.0
    kr: float = 1.0


@dataclass
class CapacityCheck:
    label: str
    demand: float
    capacity: float
    ratio: float
    unit: str
    notes: str = ""


@dataclass
class MemberCheckResult:
    section_name: str
    checks: Dict[str, CapacityCheck] = field(default_factory=dict)

    @property
    def governing_ratio(self) -> float:
        return max((check.ratio for check in self.checks.values()), default=0.0)

    @property
    def governing_check(self) -> str:
        if not self.checks:
            return "-"
        return max(self.checks.values(), key=lambda check: check.ratio).label


def safe_ratio(demand: float, capacity: float) -> float:
    if capacity <= 1e-12:
        return float("inf") if demand > 1e-12 else 0.0
    return demand / capacity


def compression_section_capacity_kn(section: SectionCheckProperties) -> float:
    """Design section compression capacity, phi Ns."""
    return PHI_STEEL * section.kf * section.design_net_area_mm2 * section.fy_mpa / 1000.0


def alpha_a(lambda_n: float) -> float:
    denominator = lambda_n**2 - 15.3 * lambda_n + 2050.0
    if abs(denominator) < 1e-12:
        return 0.0
    return 2100.0 * (lambda_n - 13.5) / denominator


def compression_slenderness_reduction(lambda_n: float, alpha_b: float = 0.0) -> float:
    """AS 4100-style member slenderness reduction factor alpha_c."""
    if lambda_n <= 0.0:
        return 1.0
    lam = max(lambda_n + alpha_a(lambda_n) * alpha_b, 1e-9)
    eta = max(0.00326 * (lam - 13.5), 0.0)
    xi = ((lam / 90.0) ** 2 + 1.0 + eta) / (2.0 * (lam / 90.0) ** 2)
    radicand = max(0.0, 1.0 - (90.0 / (xi * lam)) ** 2)
    return min(max(xi * (1.0 - math.sqrt(radicand)), 0.0), 1.0)


def member_compression_capacity_kn(section: SectionCheckProperties, actions: MemberActions) -> float:
    """Design member compression capacity, phi Nc, governing about y/z buckling axes."""
    ns = compression_section_capacity_kn(section)
    lengths = [
        (actions.effective_length_y_mm, section.radius_y_mm),
        (actions.effective_length_z_mm, section.radius_z_mm),
    ]
    alpha_c_values = []
    for effective_length_mm, radius_mm in lengths:
        if effective_length_mm is None or radius_mm <= 0.0:
            continue
        lambda_n = (effective_length_mm / radius_mm) * math.sqrt(section.kf) * math.sqrt(section.fy_mpa / 250.0)
        alpha_c_values.append(compression_slenderness_reduction(lambda_n, section.alpha_b))
    if not alpha_c_values:
        return ns
    return min(ns, min(alpha_c_values) * ns)


def tension_capacity_kn(section: SectionCheckProperties) -> float:
    """Design tension capacity, phi Nt, from gross yield and net fracture."""
    gross_yield = section.area_mm2 * section.fy_mpa
    net_fracture = section.kt * section.design_net_area_mm2 * section.fu_mpa
    return PHI_STEEL * min(gross_yield, net_fracture) / 1000.0


def section_moment_capacity_knm(section: SectionCheckProperties) -> float:
    """Design section moment capacity, phi Ms, using available section modulus."""
    s_mm3 = section.analysis_s_mm3
    if s_mm3 is None:
        return 0.0
    return PHI_STEEL * section.fy_mpa * s_mm3 / 1_000_000.0


def reference_buckling_moment_knm(section: SectionCheckProperties, actions: MemberActions) -> float:
    """Reference elastic buckling moment Mo for lateral torsional buckling."""
    le = actions.effective_length_z_mm
    if not le or le <= 0.0 or section.minor_i_mm4 <= 0.0 or section.j_mm4 <= 0.0:
        return float("inf")

    le = le * max(actions.kt, 1e-9) * max(actions.kl, 1e-9) * max(actions.kr, 1e-9)
    elastic_minor = (math.pi**2 * E_STEEL_MPA * section.minor_i_mm4) / (le**2)
    torsion = 80_000.0 * section.j_mm4
    warping = (math.pi**2 * E_STEEL_MPA * section.iw_mm6) / (le**2) if section.iw_mm6 > 0.0 else 0.0
    mo_nmm = math.sqrt(max(elastic_minor * (torsion + warping), 0.0))
    return mo_nmm / 1_000_000.0


def bending_slenderness_reduction(ms_knm: float, mo_knm: float) -> float:
    if ms_knm <= 0.0:
        return 0.0
    if not math.isfinite(mo_knm) or mo_knm <= 0.0:
        return 1.0
    ratio = math.sqrt(max(ms_knm / mo_knm, 0.0))
    if ratio <= 0.4:
        return 1.0
    return min(max(0.6 * (math.sqrt((ratio / 0.6) ** 2 + 3.0) - ratio / 0.6), 0.0), 1.0)


def bending_capacity_knm(section: SectionCheckProperties, actions: MemberActions) -> float:
    """Design member moment capacity, phi Mb, capped at phi Ms."""
    ms = section_moment_capacity_knm(section)
    mo = reference_buckling_moment_knm(section, actions)
    alpha_s = bending_slenderness_reduction(ms, mo)
    return min(ms, max(actions.moment_modifier, 0.0) * alpha_s * ms)


def clear_web_depth_mm(section: SectionCheckProperties) -> Optional[float]:
    if not section.depth_mm:
        return None
    if section.flange_thickness_mm:
        return max(section.depth_mm - 2.0 * section.flange_thickness_mm, 0.0)
    return section.depth_mm


def shear_stress_distribution(section: SectionCheckProperties) -> str:
    """Return the assumed principal shear stress distribution for this section."""
    shape = (section.shape_type or "").upper()
    name = section.name.upper()
    if "I" in shape or "H" in shape or "PFC" in name or "CHANNEL" in shape:
        return "uniform"
    if "CHS" in name or "CIRCULAR" in shape:
        return "uniform"
    if "SHS" in name or "RHS" in name or "HOLLOW" in shape or "TUBE" in shape:
        return "uniform"
    return "nonuniform"


def shear_area_mm2(section: SectionCheckProperties) -> float:
    """Gross web area Aw for the principal 2D frame shear direction."""
    shape = (section.shape_type or "").upper()
    name = section.name.upper()
    fabrication = (section.fabrication or "").upper()

    if "CHS" in name or "CIRCULAR" in shape:
        return section.area_mm2

    if "I" in shape or "H" in shape or "CHANNEL" in shape or "PFC" in name:
        if section.depth_mm and section.web_thickness_mm:
            web_depth = section.depth_mm
            if "WELD" in fabrication:
                web_depth = clear_web_depth_mm(section) or section.depth_mm
            return web_depth * section.web_thickness_mm

    if "HOLLOW" in shape or "TUBE" in shape or "SHS" in name or "RHS" in name:
        if section.depth_mm and section.web_thickness_mm:
            web_depth = clear_web_depth_mm(section) or section.depth_mm
            return 2.0 * web_depth * section.web_thickness_mm

    return 0.6 * section.area_mm2


def uniform_shear_capacity_kn(section: SectionCheckProperties) -> float:
    """Uniform web shear capacity Vu before capacity factor."""
    aw = shear_area_mm2(section)
    fyw = section.design_fyw_mpa
    if aw <= 0.0 or fyw <= 0.0:
        return 0.0

    if "CHS" in section.name.upper() or "CIRCULAR" in (section.shape_type or "").upper():
        return 0.36 * fyw * section.area_mm2 / 1000.0

    dp = clear_web_depth_mm(section) or section.depth_mm
    if not dp or not section.web_thickness_mm:
        return 0.6 * fyw * aw / 1000.0

    slenderness = dp / section.web_thickness_mm
    limit = 82.0 / math.sqrt(fyw / 250.0)
    alpha_v = 1.0
    if slenderness > limit:
        alpha_v = (82.0 / (slenderness * math.sqrt(fyw / 250.0))) ** 2

    return alpha_v * 0.6 * fyw * aw / 1000.0


def shear_capacity_kn(section: SectionCheckProperties) -> float:
    """First-pass design shear capacity, phi Vv, using Clause 5.11-style web shear."""
    vu = uniform_shear_capacity_kn(section)
    if shear_stress_distribution(section) == "uniform":
        vv = vu
    else:
        ratio = max(section.nonuniform_shear_stress_ratio, 1.0)
        vv = min(2.0 * vu / (0.9 + ratio), vu)
    return PHI_STEEL * vv


def check_member(section: SectionCheckProperties, actions: MemberActions) -> MemberCheckResult:
    result = MemberCheckResult(section_name=section.name)

    nc = member_compression_capacity_kn(section, actions)
    nt = tension_capacity_kn(section)
    mb = bending_capacity_knm(section, actions)
    vv = shear_capacity_kn(section)

    result.checks["compression"] = CapacityCheck(
        "Compression", actions.compression_kn, nc, safe_ratio(actions.compression_kn, nc), "kN"
    )
    result.checks["tension"] = CapacityCheck(
        "Tension", actions.tension_kn, nt, safe_ratio(actions.tension_kn, nt), "kN"
    )
    result.checks["bending"] = CapacityCheck(
        "Bending", actions.moment_knm, mb, safe_ratio(actions.moment_knm, mb), "kNm",
        f"Member bending phi Mb with am={actions.moment_modifier:.2f}, kt=kl=kr={actions.kt:.2f}; compactness refinement still pending.",
    )
    result.checks["shear"] = CapacityCheck(
        "Shear", actions.shear_kn, vv, safe_ratio(actions.shear_kn, vv), "kN",
        "First-pass Clause 5.11-style shear capacity.",
    )

    compression_bending_ratio = result.checks["compression"].ratio + result.checks["bending"].ratio
    tension_bending_ratio = result.checks["tension"].ratio + result.checks["bending"].ratio
    interaction_ratio = max(compression_bending_ratio, tension_bending_ratio)
    result.checks["combined"] = CapacityCheck(
        "Combined", interaction_ratio, 1.0, interaction_ratio, "",
        "Axial plus member bending interaction; shear is checked separately under Clause 5.11.",
    )
    return result
