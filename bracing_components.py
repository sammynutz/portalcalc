"""Rated wire ropes and per-location purlin/steel strut assemblies.

Diameter is not a rope stiffness or design capacity. Cold-formed axial ratings
must match the section/span/restraint basis; hot-rolled capacity approximations
are deliberately not substituted for missing supplier data.
"""
from dataclasses import dataclass
import math

from bracing_analysis import AnalysisError
from bracing_schedule import WIRE_SCHEDULE, SOURCE, SOURCE_SHA256, fitting_price
from purlin_combined import METHOD, method, installed_check, properties


WIRE_DIAMETERS_MM = tuple(WIRE_SCHEDULE)


def wire_options(data=None):
    data = data or {}
    options = []
    for d, (capacity, forged, grade_s) in WIRE_SCHEDULE.items():
        rating = {"design_tension_kn": capacity, "construction": "Grade 1570; construction not specified",
                  "source": SOURCE, "capacity_basis": "project_schedule", "fittings_per_run": fitting_price(forged)}
        rating.update({key: value for key, value in (data.get(str(d), {}) or {}).items() if value is not None and value != ""})
        if rating.get("source") != SOURCE:
            rating["capacity_basis"] = "declared"
        options.append({"label": f"{d} mm WIRE ROPE", "component_type": "wire_rope", "diameter_mm": d,
                        "area_mm2": math.pi*d*d/4, "description": "Tension-only wire rope; nominal area is a reference only",
                        "grade_mpa": 1570., "forged_turnbuckle": forged, "grade_s_turnbuckle": grade_s,
                        "source_image_sha256": SOURCE_SHA256, "rating": rating})
    return options


def positive_rating(row, name):
    try:
        value = float(row.get(name, 0))
    except (TypeError, ValueError):
        value = 0.
    if not math.isfinite(value) or value <= 0:
        label = {"ea_kn": "rope EA (kN)", "design_tension_kn": "rope assembly design tension (kN)",
                 "compression_kn": "purlin design compression (kN)", "tension_kn": "purlin design tension (kN)",
                 "elastic_buckling_kn": "purlin elastic buckling load (kN)", "price_per_m": "rope price ($/m)"}.get(name, name.replace("_", " "))
        raise AnalysisError(f"A positive {label} rating is required")
    return value


def wire_modulus(option):
    rating = option.get("rating", {})
    ea = positive_rating(rating, "ea_kn")
    if rating.get("capacity_basis") == "project_schedule" and not str(rating.get("ea_source", "")).strip():
        raise AnalysisError("Rope EA source is required; the bracing schedule contains strength, not stiffness")
    return ea*1000/option["area_mm2"]


def wire_check(option, force_kn, name):
    rating = option.get("rating", {})
    try:
        wire_modulus(option)
        capacity = positive_rating(rating, "design_tension_kn")
        if rating.get("source") == SOURCE and abs(capacity-WIRE_SCHEDULE[option["diameter_mm"]][0]) > 1e-6:
            raise AnalysisError("An edited wire capacity needs its own design-rating source")
        if not str(rating.get("construction", "")).strip() or not str(rating.get("source", "")).strip():
            raise AnalysisError("Rope construction/grade and design-rating source are required")
    except AnalysisError as error:
        return {"case": name, "mode": str(error), "ratio": None, "passes": False}
    scheduled = rating.get("capacity_basis") == "project_schedule"
    return {"case": name, "mode": "Project-schedule wire-rope tension" if scheduled else "Declared wire tension rating", "ratio": max(force_kn, 0)/capacity,
            "capacity_kn": capacity, "tension_kn": max(force_kn, 0),
            "source": rating.get("source"), "ea_source": rating.get("ea_source", rating.get("source")),
            "forged_turnbuckle": option.get("forged_turnbuckle"), "grade_s_turnbuckle": option.get("grade_s_turnbuckle"),
            "basis": "Supplied factored brace capacity, not MBL; turnbuckle, termination, net/thread and anchor checks remain separate"}


def wire_price(option, length_m, count):
    rating = option.get("rating", {})
    per_m = positive_rating(rating, "price_per_m")
    try:
        fitting = float(rating.get("fittings_per_run", -1))
    except (TypeError, ValueError):
        fitting = -1
    if not math.isfinite(fitting) or fitting < 0:
        raise AnalysisError("Wire-rope fittings price per diagonal must be entered (zero is explicit)")
    return {"material_cost": length_m*per_m, "listed_fittings_cost": count*fitting,
            "total": length_m*per_m+count*fitting, "price_per_m": per_m,
            "fittings_per_run": fitting, "allowance": "Declared rope $/m and end-fitting cost per physical diagonal"}


@dataclass(frozen=True)
class StrutAssembly:
    name: str
    components: tuple
    arrangement: str
    location: dict

    @property
    def A(self):
        return sum(component_area(c) for c in self.components)

    @property
    def E(self):
        return 200000.


@dataclass(frozen=True)
class StrutPlan:
    members: dict
    fallback: object
    name: str = "Per-location purlin / added-strut arrangements"

    @property
    def A(self):
        return self.fallback.A  # legacy summary; actual member areas override it

    def overrides(self):
        return {identifier: {"area_mm2": member.A, "modulus_mpa": member.E if isinstance(member, StrutAssembly) else 200000.}
                for identifier, member in self.members.items()}


def component_area(component):
    return component["data"]["area_mm2"] if component["type"] == "purlin" else component["profile"].A


def purlin_check(component, force_kn, case_name, level, sls_divisor=150., criteria=None):
    data = component["data"]
    rating = data.get("axial_rating", {})
    if abs(data.get("bending_span_mm", data.get("analysis_span_mm", 0))-data.get("analysis_span_mm", 0)) > 1e-3:
        return {"case": case_name, "mode": "Installed bending check does not match this bay span", "ratio": None, "passes": False}
    existing = component["existing"]
    if existing and method(rating) == METHOD:
        return installed_check(component, force_kn, case_name, level, sls_divisor, criteria)
    if not existing:
        prefix = component.get("rating_prefix", "extra")
        try:
            nc = positive_rating(rating, prefix+"_compression_kn")
            nt = positive_rating(rating, prefix+"_tension_kn")
            raw_deflection = rating.get(prefix+"_sls_deflection_mm")
            delta = float(raw_deflection)
            if not math.isfinite(delta) or delta < 0:
                raise ValueError()
            if rating.get("extra_bending_key") != data.get("bending_key") or not str(rating.get("source", "")).strip():
                raise AnalysisError("Added-ply residual rating must match the current bending/restraint basis")
        except AnalysisError as error:
            return {"case": case_name, "mode": str(error), "ratio": None, "passes": False}
        except (TypeError, ValueError):
            return {"case": case_name, "mode": "Added-purlin SLS deflection rating (mm) is required", "ratio": None, "passes": False}
        if level == "SLS":
            ratio = delta/(data["analysis_span_mm"]/sls_divisor)
        else:
            ratio = max(max(-force_kn, 0)/nc, max(force_kn, 0)/nt)
        return {"case": case_name, "mode": "Rated added-ply residual axial capacity / SLS",
                "ratio": ratio, "axial_kn": force_kn, "compression_capacity_kn": nc,
                "tension_capacity_kn": nt, "rated_sls_deflection_mm": delta,
                "basis": "Entered residual design capacity after this added member's applicable bending, restraints and connections; no installed-purlin bending-table substitution"}
    try:
        nc = positive_rating(rating, "compression_kn")
        from purlin_combined import tension_capacity
        tensile_result = tension_capacity(data, criteria)
        nt = tensile_result["tension_capacity_kn"]
        ne = positive_rating(rating, "elastic_buckling_kn")
        if rating.get("basis_key") != data["basis_key"] or not str(rating.get("source", "")).strip():
            raise AnalysisError("Supplier axial rating must match this purlin/span/restraint basis")
    except AnalysisError as error:
        return {"case": case_name, "mode": str(error), "ratio": None, "passes": False,
                "section": data["section"], "basis_key": data["basis_key"]}
    if level == "SLS":
        fraction = component.get("roof_bending_share", 1. if existing else 0.)
        ratio = (data["service_ratio"]*fraction + (0. if existing else data["added_service_ratio"]))*sls_divisor/150.
        return {"case": case_name, "mode": "Purlin bending serviceability", "ratio": ratio,
                "section": data["section"]}
    fraction = component.get("roof_bending_share", 1. if existing else 0.)
    bending = data["bending_ratio"]*fraction + (0. if existing else data["added_bending_ratio"])
    compression = max(-force_kn, 0)
    tension = max(force_kn, 0)
    alpha = 1-compression/ne
    # Conservative envelope interaction: no low-axial exemption and no
    # beneficial tension modification of bending. Nc/Nt/Ne are entered ratings,
    # not inferred from a hot-rolled model or an approximate purlin inertia.
    ratio = (compression/nc + bending/alpha) if alpha > 0 else None
    ratio = max(ratio, tension/nt+bending) if ratio is not None else None
    return {"case": case_name, "mode": "Purlin axial + bending envelope interaction",
            **tensile_result,
            "ratio": ratio, "section": data["section"], "axial_kn": force_kn,
            "compression_capacity_kn": nc, "tension_capacity_kn": nt, "elastic_buckling_kn": ne,
            "bending_ratio": bending, "amplification_denominator": alpha,
            "basis": "N/Nc + bending/(1-N/Ne); N/Nt + bending; independent envelope, conservative coincidence"}


def assembly_assessment(result, index, assembly, criteria, steel_assessor):
    rows = []
    total = assembly.A
    for part_index, component in enumerate(assembly.components):
        share = component_area(component)/total
        if component["type"] == "steel":
            # Physical components have common end translations. Distribute the
            # axial action by EA, and check each ply independently (no composite I).
            cases = []
            for case in result["cases"]:
                members = list(case["members"])
                members[index] = {**members[index], "axial_n": members[index]["axial_n"]*share}
                cases.append({**case, "members": members})
            checked = steel_assessor({**result, "cases": cases}, index, component["profile"], criteria)
            rows.extend({**row, "component": part_index, "load_share": share,
                         "section": component["profile"].name} for row in checked["case_checks"])
        else:
            from purlin_beam_design import beam_check, ARRANGEMENT
            for case in result["cases"]:
                identifier = assembly.location["id"]
                row = beam_check(result, identifier, component, case["members"][index]["axial_n"]*share/1000,
                                 case["case"]["name"], case["case"]["level"], criteria.sls_divisor, criteria) if component["existing"] or component.get("sheeted_beam") or component.get('shared_beam') else None
                if row is None and assembly.arrangement in {ARRANGEMENT, 'shared_purlin'}:
                    row = {"case": case["case"]["name"], "mode": "Coupled roof-run beam analysis required", "ratio": None, "passes": False}
                if row is None:
                    row = purlin_check(component, case["members"][index]["axial_n"]*share/1000,
                                       case["case"]["name"], case["case"]["level"], criteria.sls_divisor, criteria)
                rows.append({**row, "component": part_index, "load_share": share,
                             "existing": component["existing"]})
    unknown = next((row for row in rows if row["ratio"] is None), None)
    governing = unknown or max(rows, key=lambda row: row["ratio"])
    ratio = governing["ratio"]
    return {"id": result["cases"][0]["members"][index]["id"], "section": assembly.name,
            "ratio": ratio, "mode": governing["mode"], "case": governing["case"],
            "passes": ratio is not None and math.isfinite(ratio) and ratio <= 1,
            "case_checks": rows, "arrangement": assembly.arrangement,
            "load_sharing_basis": "Common end translations / axial connection path assumed; no composite bending stiffness credited"}


def assembly_properties(assembly):
    return {"type": "strut_assembly", "name": assembly.name, "arrangement": assembly.arrangement,
            "area_mm2": assembly.A, "modulus_mpa": assembly.E, "location": {k:v for k,v in assembly.location.items() if k != 'added_purlins'},
            "components": [{"type": c["type"], "existing": c["existing"],
                            **({"purlin": c["data"], 'shared_beam': c.get('shared_beam', False), "sheeted_beam": c.get("sheeted_beam", False), "rating_prefix": c.get("rating_prefix"), "roof_bending_share": c.get("roof_bending_share", 1. if c["existing"] else 0.)} if c["type"] == "purlin" else
                               {"section": c["profile"].name, "area_mm2": c["profile"].A})}
                           for c in assembly.components]}


def strut_locations(context, ratings=None, counts=None):
    """One record per analytical strut. Sharing counts are explicit inputs."""
    ratings, counts = ratings or {}, counts or {}
    g = context["layout"].geometry
    installed = context.get("installed_purlins", {})
    locations = []
    for bay, (a, b) in enumerate(zip(g.bay_stations_mm, g.bay_stations_mm[1:])):
        for line, x in enumerate(g.roof_stations):
            identifier = f"strut/bay/{bay}/line/{line}"
            source = installed.get(str(line))
            if source is None:
                raise AnalysisError("A checked installed purlin profile is required for each roof strut line")
            data = {**source}
            # Exact span/bending/restraint snapshot key protects against reuse
            # after a geometry, profile or bridging change.
            key = f"{source['basis_key']}|bay_span_mm={b-a:.6f}"
            data.update(basis_key=key, analysis_span_mm=b-a, axial_rating=dict(ratings.get(key, {})))
            if method(data["axial_rating"]) == METHOD:
                try:
                    data["compression_properties"] = properties(data["section"], data["axial_rating"].get("purlin_shape", "Z"))
                except AnalysisError as error:
                    data["compression_properties_error"] = str(error)
            count = counts.get(identifier, counts.get(str(line), 1))
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 8:
                raise AnalysisError("Connected existing purlins must be an integer from 1 to 8")
            is_braced = any(("/bay/"+str(bay)+"/") in (p+"/") for p in context["layout"].pinned)
            from purlin_shared_design import location_data
            references, added = location_data(context, data, b-a)
            locations.append({"id": identifier, "bay": bay, "line": line, "x_mm": x,
                              "length_mm": b-a, "purlin": data, "existing_count": count,
                              "braced_bay": is_braced, 'shared_reference_caps': references, 'added_purlins': added})
    return locations


def location_options(location, steel_profiles):
    data, n = location["purlin"], location["existing_count"]
    existing = tuple({"type": "purlin", "existing": True, "data": data} for _ in range(n))
    prefix = f"{n} existing {data['section']}"
    options = [StrutAssembly(prefix, existing, "existing_purlins", location)]
    for added in location.get('added_purlins', []):
        display = added['section'].replace('C/Z ', 'C ')
        options.append(StrutAssembly(prefix+' + '+display+' (50/50 roof load)',
            existing+({'type': 'purlin', 'existing': False, 'data': added, 'shared_beam': True},), 'shared_purlin', location))
    extra = {"type": "purlin", "existing": False, "data": data, "rating_prefix": "extra"}
    options.append(StrutAssembly(prefix+" + sheeted single-span purlin", existing+({**extra, "sheeted_beam": True},), "sheeted_purlin", location))
    options.append(StrutAssembly(prefix+" + extra purlin", existing+(extra,), "additional_purlin", location))
    if data.get("axial_rating", {}).get("back_to_back_sharing") is True:
        paired = ({**existing[0], "roof_bending_share": .5},) + existing[1:] + ({**extra, "roof_bending_share": .5, "rating_prefix": "back_to_back"},)
    else:
        paired = existing+({**extra, "rating_prefix": "back_to_back"},)  # no unverified bending-sharing benefit
    options.append(StrutAssembly(prefix+" + back-to-back mate", paired, "back_to_back", location))
    for profile in steel_profiles:
        options.append(StrutAssembly(prefix+" + "+profile.name,
                                     existing+({"type": "steel", "existing": False, "profile": profile},),
                                     "additional_steel", location))
    return options


def plan_from_locations(locations, fallback, selected=None):
    selected = selected or {}
    members = {}
    for location in locations:
        options = location_options(location, [fallback])
        choice = selected.get(location["id"], "existing_purlins")
        members[location["id"]] = next((a for a in options if a.arrangement == choice), options[0])
    return StrutPlan(members, fallback)
