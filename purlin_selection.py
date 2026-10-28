"""Select compatible general/edge roof purlins from passing table candidates."""
import re


def profile_key(section):
    """Catalogue profile without its final thickness designation."""
    return re.sub(r"\s+\d+(?:\.\d+)?$", "", str(section).strip())


def select_roof_purlin_pair(general, edge, general_rows, edge_rows):
    """Minimise installed area across rows, retaining one profile in both zones.

    Row lengths, density and lap factors are common to both zones. Their
    weighted area therefore ranks material weight without changing capacities.
    Only candidates already passing all three table checks are considered.
    """
    def by_profile(candidates):
        groups = {}
        for candidate in candidates:
            key = profile_key(candidate["section"])
            order = (candidate["area_mm2"], candidate.get("bridging_rows", 0), candidate["section"])
            if key not in groups or order < groups[key][0]:
                groups[key] = (order, candidate)
        return {key: value[1] for key, value in groups.items()}

    general_by_profile, edge_by_profile = by_profile(general), by_profile(edge)
    pairs = []
    for key in sorted(general_by_profile.keys() & edge_by_profile.keys()):
        normal, local = general_by_profile[key], edge_by_profile[key]
        area = general_rows * normal["area_mm2"] + edge_rows * local["area_mm2"]
        bridging = general_rows * normal.get("bridging_rows", 0) + edge_rows * local.get("bridging_rows", 0)
        score = (area, bridging, normal["area_mm2"] + local["area_mm2"], key)
        pairs.append((score, normal, local, key))
    if not pairs:
        return None
    score, normal, local, key = min(pairs, key=lambda item: item[0])
    return dict(normal), dict(local), {
        "common_profile": key,
        "general_rows": general_rows,
        "edge_rows_total": edge_rows,
        "weighted_area_mm2": score[0],
        "basis": "Lowest combined material weight across general and edge rows; same profile, independently selected thicknesses.",
    }
