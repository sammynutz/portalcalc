"""Project-adopted Lysaght May 1999 p20 compression/bending screen.

Section properties are transcribed from Metroll MEGASPAN 2023, p7.
The web-relative radii are used, not the Z section's rotated principal axes.
"""
import math
import re

from bracing_analysis import AnalysisError


METHOD = "lysaght_1999"
REFERENCE = "Lysaght Zeds & Cees, reprinted May 1999, p20, simplified combined loads"
REFERENCE_URL = "https://www.scribd.com/document/993312318/1999-Complete-Purlins"
PROPERTY_SOURCE = "metroll_MEGASPAN_Design_Manual_23.pdf, p7"
PROPERTY_SHA256 = "3de61ae96adffca89fbc902a4368ed6fb7d8939eefc04faaa8689761c7dcae38"

# depth, thickness (tenths): Ag mm2, rectangular/web-axis Ix (10^6 mm4), rx mm.
# Retain the published Z15024 row, but reject its inconsistent Ag/I/r tuple
# rather than silently correcting the source's 798 mm2 area to another value.
PROPERTIES = {
    "Z": {(150,12):(354,1.25,59.47), (150,15):(442,1.56,59.30),
          (150,19):(561,1.95,59.03), (150,24):(798,2.44,58.72),
          (200,12):(444,2.77,78.96), (200,15):(555,3.45,78.81),
          (200,19):(713,4.40,78.55), (200,24):(900,5.51,78.24),
          (250,15):(638,5.92,96.40), (250,19):(808,7.47,96.17),
          (250,24):(1020,9.37,95.83), (300,19):(998,13.00,114.00),
          (300,24):(1260,16.36,114.00), (300,30):(1575,20.14,113.00),
          (350,19):(1207,22.27,136.00), (350,24):(1524,27.99,136.00),
          (350,30):(1905,34.77,135.00)},
    "C": {(150,12):(354,1.25,59.40), (150,15):(443,1.55,59.24),
          (150,19):(561,1.95,58.97), (150,24):(708,2.44,58.65),
          (200,12):(444,2.77,78.97), (200,15):(555,3.45,78.83),
          (200,19):(713,4.40,78.57), (200,24):(900,5.51,78.21),
          (250,15):(638,5.93,96.41), (250,19):(808,7.47,96.19),
          (250,24):(1020,9.37,95.85), (300,19):(998,13.00,114.16),
          (300,24):(1260,16.37,113.98), (300,30):(1575,20.14,113.08),
          (350,19):(1207,22.28,135.89), (350,24):(1524,27.99,135.52),
          (350,30):(1905,34.77,135.10)},
}


def properties(section, shape="Z"):
    match = re.fullmatch(r"(?:C/Z|Z|C)\s*(\d+)\s+(\d+)", section.strip())
    key = tuple(map(int, match.groups())) if match else None
    record = PROPERTIES.get(shape, {}).get(key)
    if record is None:
        raise AnalysisError(f"No verified Metroll web-axis properties for {shape} {section}")
    area, ix, rx = record
    if abs(area*rx**2/(ix*1e6)-1) > .015:
        raise AnalysisError(f"Inconsistent published Metroll Ag/I/r values for {shape}{key[0]}{key[1]}; use declared ratings")
    return {"shape": shape, "area_mm2": float(area), "web_ix_mm4": ix*1e6,
            "web_rx_mm": rx, "source": PROPERTY_SOURCE, "source_sha256": PROPERTY_SHA256,
            "axis_basis": "Rectangular/web-relative axis; published rx corresponds to Ix-prime for Z sections"}


def method(rating):
    if rating.get("compression_method"):
        return rating["compression_method"]
    # Preserve existing explicitly rated designs; new/blank entries adopt the
    # user-approved simplified compression method.
    return "declared" if all(rating.get(k) for k in ("compression_kn", "elastic_buckling_kn", "source")) else METHOD


def compression_capacity(area_mm2, radius_mm, span_mm):
    if any(not math.isfinite(v) or v <= 0 for v in (area_mm2, radius_mm, span_mm)):
        raise AnalysisError("Positive finite Ag, web-axis radius and span are required")
    return 903*area_mm2/(span_mm/radius_mm)**2


def installed_check(component, force_kn, name, level, sls_divisor):
    data = component["data"]
    rating = data.get("axial_rating", {})
    fraction = component.get("roof_bending_share", 1.)
    bending = data["bending_ratio"]*fraction
    base = {"case": name, "method": METHOD, "section": data["section"], "axial_kn": force_kn,
            "bending_ratio": bending, "roof_bending_share": fraction, "source": REFERENCE,
            "source_url": REFERENCE_URL,
            "basis": "Ns=903 Ag/(L/rx')^2; Nav=Ns(1-Wa/Ws); UC=Ncompression/Ns+Wa/Ws. No additional phi, kt or Euler amplification. Installed sheeted purlins only; tension and connections separate."}
    if level == "SLS":
        return {**base, "mode": "Purlin bending serviceability", "ratio": data["service_ratio"]*fraction*sls_divisor/150.}
    try:
        props = properties(data["section"], rating.get("purlin_shape", "Z"))
        # Reject a materially different installed section/area rather than use
        # a similarly named catalogue entry. Small published rounding is allowed.
        if abs(data["area_mm2"]/props["area_mm2"]-1) > .005:
            raise AnalysisError("Installed purlin area does not match the selected Metroll section properties")
        ns = compression_capacity(props["area_mm2"], props["web_rx_mm"], data["analysis_span_mm"])
    except AnalysisError as error:
        return {**base, "mode": str(error), "ratio": None, "passes": False}
    compression = max(-force_kn, 0.)
    base.update(section_properties=props, span_mm=data["analysis_span_mm"],
                compression_capacity_kn=ns, available_compression_kn=max(0., ns*(1-bending)),
                raw_available_compression_kn=ns*(1-bending), compression_kn=compression,
                compression_ratio=compression/ns+bending)
    if force_kn > 1e-8:
        try:
            nt = float(rating.get("tension_kn") or 0.)
            if not math.isfinite(nt) or nt <= 0 or not str(rating.get("source", "")).strip() or rating.get("basis_key") != data["basis_key"]:
                raise ValueError()
        except (TypeError, ValueError):
            return {**base, "mode": "Purlin tension rating required; simplified compression calculated", "ratio": None, "passes": False}
        return {**base, "mode": "Declared purlin tension + bending envelope", "ratio": force_kn/nt+bending,
                "tension_capacity_kn": nt, "tension_source": rating["source"]}
    return {**base, "mode": "Lysaght 1999 simplified compression + bending", "ratio": compression/ns+bending}
