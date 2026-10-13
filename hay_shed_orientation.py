"""Exact orientation minima for the app's regional Md tables, without frame solves.

Bearings are clockwise from true north and describe the open long side's
outward normal. Other site multipliers are held direction independent.
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

from wind import (A_SUBREGIONS, design_direction_multiplier,
                  interpolated_direction_multiplier)


def sector_lines(regions, interval_start, offset=0):
    """Linear functions whose maximum is a sector's Md on one 45° interval.

    Sector boundaries move along linear segments; interior cardinal values
    stay constant. Parameter t runs from zero to one over the interval.
    """
    lines = set()
    for region in regions:
        for boundary in (-45, 45):
            bearing = interval_start + offset + boundary
            first = interpolated_direction_multiplier(region, bearing)
            last = interpolated_direction_multiplier(region, bearing + 45)
            lines.add((round(first, 14), round(last - first, 14)))
        middle = interval_start + offset + 22.5
        for i in range(int((middle - 45) // 45) - 1,
                       int((middle + 45) // 45) + 2):
            bearing = i * 45
            if middle - 45 < bearing < middle + 45:
                lines.add((interpolated_direction_multiplier(region, bearing), 0.0))
    return sorted(lines)


def exact_minimum(regions=A_SUBREGIONS, offsets=(0,)):
    """Minimise maximum sector Md at specified building directions.

    A maximum of linear functions can attain its minimum at an interval
    boundary or an intersection of two functions. Enumerate those finite
    points; no angular grid or structural optimisation is required.
    """
    if not offsets or any(offset % 45 for offset in offsets):
        raise ValueError("axis offsets must be nonempty multiples of 45 degrees")
    trials = {}
    for start in range(0, 360, 45):
        lines = sorted(set(itertools.chain.from_iterable(
            sector_lines(regions, start, offset) for offset in offsets)))
        points = {0.0, 1.0}
        for (a, slope_a), (b, slope_b) in itertools.combinations(lines, 2):
            if abs(slope_a - slope_b) < 1e-12:
                continue
            t = (b - a) / (slope_a - slope_b)
            if 0 < t < 1:
                points.add(t)
        for t in points:
            bearing = round((start + t * 45) % 360, 10)
            value = max(a + slope * t for a, slope in lines)
            trials[bearing] = value
    best = min(trials.values())
    return {"md": round(best, 10),
            "bearings_deg": sorted(bearing for bearing, value in trials.items()
                                   if abs(value - best) < 1e-10)}


def directional_factors(bearing, regions=A_SUBREGIONS):
    factors = {name: max(design_direction_multiplier(r, bearing + offset)
                        for r in regions)
               for name, offset in (("open", 0), ("closed", 180),
                                    ("end_1", 90), ("end_2", -90))}
    factors["governing_end"] = max(factors["end_1"], factors["end_2"])
    return {name: round(value, 10) for name, value in factors.items()}


def assessment(regions=A_SUBREGIONS):
    minima = {
        "open_side": exact_minimum(regions, (0,)),
        "opposite_closed_side": exact_minimum(regions, (180,)),
        "end_pair": exact_minimum(regions, (90, 270)),
        "both_long_sides": exact_minimum(regions, (0, 180)),
    }
    end_bearing = min(minima["end_pair"]["bearings_deg"],
                      key=lambda b: (directional_factors(b, regions)["open"], b))
    chosen = [("East baseline", 90),
              ("Minimum opening Md", minima["open_side"]["bearings_deg"][0]),
              ("Minimum end-pair Md", end_bearing),
              ("Balanced long sides", minima["both_long_sides"]["bearings_deg"][0]),
              ("Minimum closed-side Md", minima["opposite_closed_side"]["bearings_deg"][0])]
    rows = [{"purpose": purpose, "open_side_bearing_deg": bearing,
             "factors": directional_factors(bearing, regions)}
            for purpose, bearing in chosen]
    baseline = directional_factors(90, regions)
    for row in rows:
        row["pressure_reduction_vs_east_percent"] = {
            name: round(100 * (1 - (row["factors"][name] / baseline[name]) ** 2), 5)
            for name in ("open", "closed", "governing_end")}
    return {"regions": list(regions), "method": "exact intersections of linear sector functions",
            "bearing_convention": "open long side outward normal, clockwise from true north",
            "assumptions": "direction independent terrain, shielding and topographic factors",
            "scope": "wind factors only; no member sizing or steel cost claim",
            "reference": "AS/NZS1170.2:2021 clause2.3 and Table3.2(A)",
            "minima": minima, "candidates": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", default=",".join(A_SUBREGIONS))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    regions = tuple(args.regions.split(","))
    if not regions or any(r not in A_SUBREGIONS for r in regions):
        parser.error("regions must be a nonempty selection of A0–A5")
    result = assessment(regions)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
