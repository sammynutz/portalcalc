"""Project bracing schedule supplied by the user, 29 October 2026.

Capacities below are the supplied FACTORED brace capacities. They are not
multiplied by another capacity factor and are not turnbuckle/anchor checks.
Wire nominal area is a reference only; it does not establish rope EA.
"""
import math

from costing import END_WALL_ROD_TURNBUCKLES


SOURCE = "User bracing schedule, 2026-10-29; assets/project_bracing_schedule_2026-10-29.png"
SOURCE_SHA256 = "61975BBF5293744EF4901883D7FEAFAFDA15A7CADCD3B23856FFBBA9AD9F6E44"
ROD_SCHEDULE = {
    16: (54.28672, "M16", "M12"),
    20: (84.823, "M20", "M16"),
    24: (122.1451, "M22", "M16"),
    30: (190.8518, "M28", "M20"),
}
WIRE_SCHEDULE = {
    8: (22.96, "M10", "M10"),
    10: (35.92, "M12", "M10"),
    12: (51.2, "M16", "M12"),
    14: (70.4, "M16", "M12"),
    16: (92., "M20", "M16"),
}


def fitting_price(size):
    # Match exact nominal sizes. In particular, the old M30 price is not a
    # price for the M28 forged turnbuckle specified on a 30 mm rod here.
    return next((item["unit_cost"] for item in END_WALL_ROD_TURNBUCKLES.values()
                 if "M"+str(item["turnbuckle_size"]) == size), None)


def rod_options(prices=None):
    prices = prices or {}
    return [{"label": f"{d} DIA ROD", "description": f"Grade 300 rod - project schedule",
             "area_mm2": math.pi*d*d/4, "component_type": "scheduled_rod", "grade_mpa": 300.,
             "design_tension_kn": capacity, "forged_turnbuckle": forged, "grade_s_turnbuckle": grade_s,
             "fitting_unit_cost": prices.get(str(d), fitting_price(forged)),
             "capacity_source": SOURCE, "source_image": "assets/project_bracing_schedule_2026-10-29.png",
             "source_image_sha256": SOURCE_SHA256}
            for d, (capacity, forged, grade_s) in ROD_SCHEDULE.items()]


def rod_check(option, force_kn, name):
    capacity = option["design_tension_kn"]
    return {"case": name, "mode": "Project-schedule Grade 300 rod tension", "ratio": max(force_kn, 0.)/capacity,
            "capacity_kn": capacity, "tension_kn": max(force_kn, 0.), "grade_mpa": option["grade_mpa"],
            "forged_turnbuckle": option["forged_turnbuckle"], "grade_s_turnbuckle": option["grade_s_turnbuckle"],
            "basis": "Supplied factored brace capacity used directly. Thread/net rupture, fittings and anchor capacities remain separate.",
            "source": option["capacity_source"]}
