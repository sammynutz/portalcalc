"""Geometry and constraints for the future 3D bracing layout search.

No stiffness or capacity is inferred here. BOLT can supply the same versioned
payload as the desktop editor. Available locations are the complement of the
blocked set; pinned braces are additional user requirements, not design checks.
"""
from dataclasses import dataclass
import math


SCHEMA = "portalcalc.bracing-layout.v1"
# v2 adds open canopies to the geometry; layouts without canopies stay v1.
SCHEMA_V2 = "portalcalc.bracing-layout.v2"
# v3 adds portal frame braces in side-wall bays; layouts without them stay v1/v2.
SCHEMA_V3 = "portalcalc.bracing-layout.v3"
SCHEMAS = {SCHEMA, SCHEMA_V2, SCHEMA_V3}
FACE_LABELS = {
    "left_wall": "Left wall", "right_wall": "Right wall",
    "roof_left": "Left roof slope", "roof_right": "Right roof slope", "roof": "Roof",
    "front_end": "Front end wall", "back_end": "Back end wall",
    "canopy_left": "Left canopy roof", "canopy_right": "Right canopy roof",
    "portal_left": "Left wall portal brace", "portal_right": "Right wall portal brace",
}
# Portal frame braces: a UB moment frame (two columns stitch-welded inside the
# portal columns, beam moment-connected across the top) in a side-wall bay.
# They are kept apart from the X-brace panels: ``pinned``/``blocked``/
# ``available`` stay X-brace only, ``portal_braces``/``portal_blocked`` hold them.
PORTAL_FACES = ("portal_left", "portal_right")


def is_portal_brace(identifier):
    return str(identifier).startswith(("portal_left/", "portal_right/"))


def portal_wall_panel(identifier):
    """The side-wall X panel in the same bay as a portal brace."""
    face, _, bay = identifier.split("/")
    return f"{face.split('_')[1]}_wall/bay/{bay}"
CANOPY_SIDES = ("left", "right")
# Brace design groups. canopy_brace exists only for buildings with an open
# canopy; selections/candidates omit it otherwise.
BRACE_GROUPS = ("roof_brace", "wall_brace", "end_brace", "canopy_brace")


def brace_group(face):
    """Design group of a brace panel face."""
    if face.startswith("roof"):
        return "roof_brace"
    if face in {"front_end", "back_end"}:
        return "end_brace"
    if face.startswith("canopy_"):
        return "canopy_brace"
    return "wall_brace"


def _canopies(value):
    """Normalise open canopies to ((side, plan width mm, tip drop mm), ...).

    Plan width is measured out from the main eave line; the tip drop is the
    canopy roof's fall from the main eave to its outer edge (negative rises).
    """
    if not value:
        return ()
    items = value.items() if isinstance(value, dict) else (
        (item.get("side"), item) if isinstance(item, dict) else (item[0], {"width_mm": item[1], "drop_mm": item[2]})
        for item in value)
    result = {}
    for side, data in items:
        if side not in CANOPY_SIDES:
            raise ValueError("Canopy side must be left or right")
        if side in result:
            raise ValueError(f"Only one {side} canopy is allowed")
        width = _positive(data.get("width_mm"), f"{side.capitalize()} canopy width")
        try:
            drop = float(data.get("drop_mm", 0.))
        except (TypeError, ValueError):
            raise ValueError(f"{side.capitalize()} canopy fall must be a finite number") from None
        if not math.isfinite(drop) or abs(drop) > width:
            raise ValueError(f"{side.capitalize()} canopy fall must be finite and no steeper than 45 degrees")
        result[side] = (side, width, drop)
    return tuple(result[side] for side in CANOPY_SIDES if side in result)


def _positive(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite positive number")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a finite positive number") from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return result


def _stations(values, extent, name):
    if not isinstance(values, (list, tuple)) or any(isinstance(x, bool) for x in values):
        raise ValueError(f"{name} stations must be a list of numbers")
    try:
        result = tuple(float(x) for x in values)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {name} stations") from None
    if len(result) < 2 or any(not math.isfinite(x) for x in result):
        raise ValueError(f"{name} needs at least two finite stations")
    if abs(result[0]) > 1e-6 or abs(result[-1] - extent) > 1e-6:
        raise ValueError(f"{name} stations must start at 0 and finish at {extent:g} mm")
    if any(b - a <= 1e-6 for a, b in zip(result, result[1:])):
        raise ValueError(f"{name} stations must be strictly increasing")
    return result


@dataclass(frozen=True)
class LayoutGeometry:
    span_mm: float
    length_mm: float
    eave_mm: float
    pitch_deg: float
    roof_shape: str
    bay_stations_mm: tuple
    end_wall_stations_mm: tuple
    canopies: tuple = ()

    @classmethod
    def create(cls, span_mm, length_mm, eave_mm, pitch_deg, roof_shape,
               bay_mm=8000, bay_stations_mm=None, end_wall_stations_mm=None, canopies=None):
        span = _positive(span_mm, "Span")
        length = _positive(length_mm, "Building length")
        eave = _positive(eave_mm, "Eave height")
        if isinstance(pitch_deg, bool):
            raise ValueError("Roof pitch must be a finite number between -45 and 45 degrees")
        try:
            pitch = float(pitch_deg)
        except (TypeError, ValueError):
            raise ValueError("Roof pitch must be a finite number between -45 and 45 degrees") from None
        if not math.isfinite(pitch) or not -45 <= pitch <= 45:
            raise ValueError("Roof pitch must be between -45 and 45 degrees")
        if not isinstance(roof_shape, str) or roof_shape not in {"Gable", "Skillion"}:
            raise ValueError("Roof shape must be Gable or Skillion")
        if bay_stations_mm is None:
            bay = _positive(bay_mm, "Bay size")
            if length / bay > 500:
                raise ValueError("The layout editor supports up to 500 building bays")
            count = math.ceil(length / bay)
            bay_stations_mm = [min(i * bay, length) for i in range(count + 1)]
        if end_wall_stations_mm is None:
            count = max(1, math.ceil(span / 8000))
            if count > 100:
                raise ValueError("The layout editor supports up to 100 end-wall panels")
            end_wall_stations_mm = [span * i / count for i in range(count + 1)]
        bays = _stations(bay_stations_mm, length, "Bay")
        ends = _stations(end_wall_stations_mm, span, "End-wall")
        canopy = _canopies(canopies)
        if len(bays) > 501 or len(ends) > 101 or (len(bays) - 1) * (len(ends) + 3 + len(canopy)) > 5000:
            raise ValueError("The layout grid is too large; use fewer bays or end-wall panels")
        if canopy and roof_shape != "Gable":
            raise ValueError("Open canopies are supported on gable roofs only")
        geometry = cls(span, length, eave, pitch, roof_shape, bays, ends, canopy)
        heights = (geometry.roof_z(0), geometry.roof_z(span / 2), geometry.roof_z(span))
        heights += tuple(geometry.canopy_tip(side)[1] for side, _, _ in canopy)
        if any(not math.isfinite(z) or z <= 0 for z in heights):
            raise ValueError("The roof must remain above ground at every station")
        return geometry

    def canopy(self, side):
        """(plan width, tip drop) in mm of the open canopy on ``side``, or None."""
        return next(((width, drop) for s, width, drop in self.canopies if s == side), None)

    def canopy_root_x(self, side):
        return 0. if side == "left" else self.span_mm

    def canopy_tip(self, side):
        """(x, z) of the canopy's outer edge; x from the main left eave."""
        width, drop = self.canopy(side)
        root = self.canopy_root_x(side)
        return (root - width if side == "left" else root + width), self.roof_z(root) - drop

    @property
    def strut_lines(self):
        """Longitudinal strut lines, in portal point order: (x, z, canopy side or None).

        The roof stations come first (line j is roof_stations[j]); each open
        canopy adds its outer edge line (the canopy edge purlin) after them.
        """
        lines = [(x, self.roof_z(x), None) for x in self.roof_stations]
        lines += [(*self.canopy_tip(side), side) for side, _, _ in self.canopies]
        return tuple(lines)

    def canopy_line(self, side):
        """Strut line index of the canopy edge on ``side``."""
        return len(self.roof_stations) + [s for s, _, _ in self.canopies].index(side)

    def roof_z(self, x):
        run = x if self.roof_shape == "Skillion" else min(x, self.span_mm - x)
        return self.eave_mm + run * math.tan(math.radians(self.pitch_deg))

    @property
    def roof_stations(self):
        stations = set(self.end_wall_stations_mm)
        if self.roof_shape == "Gable":
            stations.add(self.span_mm / 2)
        return tuple(sorted(stations))

    def to_dict(self):
        data = {"span_mm": self.span_mm, "length_mm": self.length_mm, "eave_mm": self.eave_mm,
                "pitch_deg": self.pitch_deg, "roof_shape": self.roof_shape,
                "bay_stations_mm": list(self.bay_stations_mm),
                "end_wall_stations_mm": list(self.end_wall_stations_mm)}
        if self.canopies:
            data["canopies"] = [{"side": side, "width_mm": width, "drop_mm": drop} for side, width, drop in self.canopies]
        return data

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Layout geometry must be an object")
        try:
            return cls.create(**{key: value[key] for key in (
                "span_mm", "length_mm", "eave_mm", "pitch_deg", "roof_shape",
                "bay_stations_mm", "end_wall_stations_mm")}, canopies=value.get("canopies"))
        except KeyError as error:
            raise ValueError(f"Missing layout geometry field: {error.args[0]}") from None

    def main_geometry(self):
        """The same building without canopies (the main frame grid)."""
        return LayoutGeometry(self.span_mm, self.length_mm, self.eave_mm, self.pitch_deg, self.roof_shape,
                              self.bay_stations_mm, self.end_wall_stations_mm, ())


@dataclass(frozen=True)
class BracingPanel:
    id: str
    face: str
    label: str
    vertices: tuple
    diagonals: tuple


def layout_panels(geometry):
    """World axes: X across the portal, Y front-to-back, Z vertical; mm."""
    panels = []
    g = geometry

    def add(identifier, face, label, vertices):
        # End-wall polygons can have a fifth point at the roof ridge. Brace
        # endpoints remain the actual end-wall panel corner/mullion points.
        diagonals = ((vertices[0], vertices[2]), (vertices[1], vertices[-1]))
        panels.append(BracingPanel(identifier, face, label, tuple(vertices), diagonals))

    for bay, (y0, y1) in enumerate(zip(g.bay_stations_mm, g.bay_stations_mm[1:])):
        suffix = f"Bay {bay + 1} ({y0 / 1000:g}-{y1 / 1000:g} m from front)"
        for face, x in (("left_wall", 0), ("right_wall", g.span_mm)):
            add(f"{face}/bay/{bay}", face, f"{FACE_LABELS[face]} / {suffix}",
                [(x, y0, 0), (x, y1, 0), (x, y1, g.roof_z(x)), (x, y0, g.roof_z(x))])
        stations = g.roof_stations
        for strip, (x0, x1) in enumerate(zip(stations, stations[1:])):
            face = "roof" if g.roof_shape == "Skillion" else "roof_left" if x1 <= g.span_mm / 2 + 1e-6 else "roof_right"
            add(f"roof/bay/{bay}/strip/{strip}", face,
                f"{FACE_LABELS[face]} / {suffix} / strip {strip + 1} (x={x0 / 1000:g}-{x1 / 1000:g} m)",
                [(x0, y0, g.roof_z(x0)), (x1, y0, g.roof_z(x1)),
                 (x1, y1, g.roof_z(x1)), (x0, y1, g.roof_z(x0))])
        # One canopy roof strip per bay, from the main eave line to the canopy edge.
        for side, width, _ in g.canopies:
            face = f"canopy_{side}"
            root = g.canopy_root_x(side)
            tip, tip_z = g.canopy_tip(side)
            add(f"{face}/bay/{bay}", face, f"{FACE_LABELS[face]} / {suffix} ({width / 1000:g} m wide)",
                [(root, y0, g.roof_z(root)), (tip, y0, tip_z), (tip, y1, tip_z), (root, y1, g.roof_z(root))])
    for face, y in (("front_end", 0), ("back_end", g.length_mm)):
        for index, (x0, x1) in enumerate(zip(g.end_wall_stations_mm, g.end_wall_stations_mm[1:])):
            vertices = [(x0, y, 0), (x1, y, 0), (x1, y, g.roof_z(x1))]
            if g.roof_shape == "Gable" and x0 < g.span_mm / 2 < x1:
                vertices.append((g.span_mm / 2, y, g.roof_z(g.span_mm / 2)))
            vertices.append((x0, y, g.roof_z(x0)))
            add(f"{face}/panel/{index}", face,
                f"{FACE_LABELS[face]} / panel {index + 1} (x={x0 / 1000:g}-{x1 / 1000:g} m)", vertices)
    return tuple(panels)


def portal_panels(geometry):
    """Portal frame brace locations: one per side-wall bay, same outline as the wall panel."""
    g = geometry
    panels = []
    for bay, (y0, y1) in enumerate(zip(g.bay_stations_mm, g.bay_stations_mm[1:])):
        suffix = f"Bay {bay + 1} ({y0 / 1000:g}-{y1 / 1000:g} m from front)"
        for face, x in (("portal_left", 0), ("portal_right", g.span_mm)):
            vertices = ((x, y0, 0), (x, y1, 0), (x, y1, g.roof_z(x)), (x, y0, g.roof_z(x)))
            panels.append(BracingPanel(f"{face}/bay/{bay}", face, f"{FACE_LABELS[face]} / {suffix}", vertices, ()))
    return tuple(panels)


def canopy_bays(targets, max_spacing):
    """End bays of ``targets`` plus the fewest intermediate ones for the spacing rule.

    No run of ``max_spacing`` or more consecutive bays between chosen bays is
    left unbraced (the main roof's braced-bay spacing rule); each gap is split
    at the available bay nearest its middle.
    """
    targets = sorted(set(targets))
    if not targets:
        return []
    chosen = sorted({targets[0], targets[-1]})
    while True:
        # The first over-long gap that still has a roof-braced bay inside it.
        split = next(((a, b, inside) for a, b in zip(chosen, chosen[1:]) if b-a-1 >= max_spacing
                      for inside in [[t for t in targets if a < t < b]] if inside), None)
        if split is None:
            return chosen
        a, b, inside = split
        chosen = sorted(chosen + [min(inside, key=lambda t: (abs(t-(a+b)/2), t))])


def canopy_bracing(geometry, blocked, selected, max_spacing=5):
    """Canopy roof panels to add so every open canopy is braced.

    A canopy edge has no along-building support except its own roof bracing.
    Each canopy is braced in the end roof-braced bays (the complete
    roof-braced bays, every roof strip pinned, else any bay with roof
    bracing), plus intermediate roof-braced bays only where needed so that no
    ``max_spacing`` consecutive canopy bays are unbraced (the braced-bay
    spacing rule). A blocked canopy panel moves to the nearest allowed bay. A
    canopy side that already has braced panels is left as it is. Returns the
    panel IDs to add (empty without canopies or without any roof bracing).
    """
    if not geometry.canopies:
        return set()
    selected = set(selected)
    count = len(geometry.bay_stations_mm) - 1
    strips = len(geometry.roof_stations) - 1
    roof_bays = sorted({int(p.split("/")[2]) for p in selected if p.startswith("roof/bay/")})
    complete = [b for b in roof_bays if all(f"roof/bay/{b}/strip/{i}" in selected for i in range(strips))]
    targets = canopy_bays(complete or roof_bays, max_spacing)
    added = set()
    for side, _, _ in geometry.canopies:
        face = f"canopy_{side}"
        if any(p.startswith(face + "/") for p in selected):
            continue
        for bay in targets:
            nearest = next((b for d in range(count) for b in (bay - d, bay + d)
                            if 0 <= b < count and f"{face}/bay/{b}" not in blocked), None)
            if nearest is not None:
                added.add(f"{face}/bay/{nearest}")
    return added


def carry_over(saved, geometry):
    """A saved layout moved onto ``geometry`` when only the canopies differ.

    The main building's blocked and pinned panels are kept (they still exist);
    panels of a removed canopy are dropped and a new canopy starts with none.
    Returns None when the main building itself has changed.
    """
    if saved.geometry.main_geometry() != geometry.main_geometry():
        return None
    panels = {panel.id for panel in layout_panels(geometry)} | {panel.id for panel in portal_panels(geometry)}
    return BracingLayout(geometry, sorted(saved.all_blocked & panels), sorted(saved.selection & panels))


class BracingLayout:
    """Blocked/pinned brace locations on a geometry.

    ``blocked``/``pinned``/``available`` are X-brace panels only. Portal frame
    brace IDs (``portal_{side}/bay/{b}``) may be passed in the same arguments
    and are kept in ``portal_blocked``/``portal_braces``; ``selection`` and
    ``all_blocked`` give both, for carrying a layout across.
    """

    def __init__(self, geometry, blocked=(), pinned=()):
        self.geometry = geometry
        self.panels = {panel.id: panel for panel in layout_panels(geometry)}
        self.portal_panels = {panel.id: panel for panel in portal_panels(geometry)}
        blocked = self._ids(blocked, "blocked")
        pinned = self._ids(pinned, "pinned")
        self.blocked = {p for p in blocked if not is_portal_brace(p)}
        self.pinned = {p for p in pinned if not is_portal_brace(p)}
        self.portal_blocked = blocked - self.blocked
        self.portal_braces = pinned - self.pinned
        if blocked & pinned:
            raise ValueError("A bracing panel cannot be both blocked and pinned")
        both = {p for p in self.portal_braces if portal_wall_panel(p) in self.pinned}
        if both:
            raise ValueError(f"A wall bay cannot have both X bracing and a portal brace: {sorted(both)[0]}")

    def _ids(self, values, name):
        if not isinstance(values, (list, tuple, set, frozenset)) or any(not isinstance(x, str) for x in values):
            raise ValueError(f"{name.capitalize()} panels must be a list of panel IDs")
        result = set(values)
        missing = result - self.panels.keys() - self.portal_panels.keys()
        if missing:
            raise ValueError(f"Unknown {name} panel: {sorted(missing)[0]}")
        return result

    @property
    def available(self):
        """X-brace panels that are not blocked (portal braces excluded)."""
        return set(self.panels) - self.blocked

    @property
    def available_portals(self):
        return set(self.portal_panels) - self.portal_blocked

    @property
    def selection(self):
        """Pinned X panels and portal braces together."""
        return self.pinned | self.portal_braces

    @property
    def all_blocked(self):
        return self.blocked | self.portal_blocked

    @property
    def all_panels(self):
        """X-brace and portal brace locations by ID (for editors and labels)."""
        return {**self.panels, **self.portal_panels}

    def state(self, identifier):
        return "blocked" if identifier in self.all_blocked else "pinned" if identifier in self.selection else "available"

    def clear(self, identifiers):
        """Unblock and unpin the given locations."""
        for target in (self.blocked, self.pinned, self.portal_blocked, self.portal_braces):
            target.difference_update(identifiers)

    def toggle(self, identifier, mode="blocked"):
        portal = is_portal_brace(identifier)
        if identifier not in (self.portal_panels if portal else self.panels) or mode not in {"blocked", "pinned"}:
            raise ValueError("Unknown panel or editing mode")
        blocked, pinned = (self.portal_blocked, self.portal_braces) if portal else (self.blocked, self.pinned)
        if mode == "pinned" and identifier in blocked:
            raise ValueError("Unblock this panel before pinning a brace there")
        if mode == "pinned" and identifier not in pinned:
            if portal and portal_wall_panel(identifier) in self.pinned:
                raise ValueError("Remove the X bracing in this wall bay before adding a portal brace")
            if not portal and any(portal_wall_panel(p) == identifier for p in self.portal_braces):
                raise ValueError("Remove the portal brace in this wall bay before adding X bracing")
        target, other = (blocked, pinned) if mode == "blocked" else (pinned, blocked)
        if identifier in target:
            target.remove(identifier)
        else:
            target.add(identifier)
            other.discard(identifier)

    def to_dict(self):
        schema = SCHEMA_V3 if self.portal_braces or self.portal_blocked else SCHEMA_V2 if self.geometry.canopies else SCHEMA
        return {"schema": schema, "geometry": self.geometry.to_dict(),
                "blocked_panels": sorted(self.all_blocked), "pinned_panels": sorted(self.selection)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.get("schema") not in SCHEMAS:
            raise ValueError("Unsupported bracing-layout schema")
        if "geometry" not in value:
            raise ValueError("Missing bracing-layout geometry")
        return cls(LayoutGeometry.from_dict(value["geometry"]), value.get("blocked_panels", []), value.get("pinned_panels", []))
