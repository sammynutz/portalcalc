"""Geometry and constraints for the future 3D bracing layout search.

No stiffness or capacity is inferred here. BOLT can supply the same versioned
payload as the desktop editor. Available locations are the complement of the
blocked set; pinned braces are additional user requirements, not design checks.
"""
from dataclasses import dataclass
import math


SCHEMA = "portalcalc.bracing-layout.v1"
FACE_LABELS = {
    "left_wall": "Left wall", "right_wall": "Right wall",
    "roof_left": "Left roof slope", "roof_right": "Right roof slope", "roof": "Roof",
    "front_end": "Front end wall", "back_end": "Back end wall",
}


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

    @classmethod
    def create(cls, span_mm, length_mm, eave_mm, pitch_deg, roof_shape,
               bay_mm=8000, bay_stations_mm=None, end_wall_stations_mm=None):
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
        if len(bays) > 501 or len(ends) > 101 or (len(bays) - 1) * (len(ends) + 3) > 5000:
            raise ValueError("The layout grid is too large; use fewer bays or end-wall panels")
        geometry = cls(span, length, eave, pitch, roof_shape, bays, ends)
        heights = (geometry.roof_z(0), geometry.roof_z(span / 2), geometry.roof_z(span))
        if any(not math.isfinite(z) or z <= 0 for z in heights):
            raise ValueError("The roof must remain above ground at every station")
        return geometry

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
        return {"span_mm": self.span_mm, "length_mm": self.length_mm, "eave_mm": self.eave_mm,
                "pitch_deg": self.pitch_deg, "roof_shape": self.roof_shape,
                "bay_stations_mm": list(self.bay_stations_mm),
                "end_wall_stations_mm": list(self.end_wall_stations_mm)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("Layout geometry must be an object")
        try:
            return cls.create(**{key: value[key] for key in (
                "span_mm", "length_mm", "eave_mm", "pitch_deg", "roof_shape",
                "bay_stations_mm", "end_wall_stations_mm")})
        except KeyError as error:
            raise ValueError(f"Missing layout geometry field: {error.args[0]}") from None


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
    for face, y in (("front_end", 0), ("back_end", g.length_mm)):
        for index, (x0, x1) in enumerate(zip(g.end_wall_stations_mm, g.end_wall_stations_mm[1:])):
            vertices = [(x0, y, 0), (x1, y, 0), (x1, y, g.roof_z(x1))]
            if g.roof_shape == "Gable" and x0 < g.span_mm / 2 < x1:
                vertices.append((g.span_mm / 2, y, g.roof_z(g.span_mm / 2)))
            vertices.append((x0, y, g.roof_z(x0)))
            add(f"{face}/panel/{index}", face,
                f"{FACE_LABELS[face]} / panel {index + 1} (x={x0 / 1000:g}-{x1 / 1000:g} m)", vertices)
    return tuple(panels)


class BracingLayout:
    def __init__(self, geometry, blocked=(), pinned=()):
        self.geometry = geometry
        self.panels = {panel.id: panel for panel in layout_panels(geometry)}
        self.blocked = self._ids(blocked, "blocked")
        self.pinned = self._ids(pinned, "pinned")
        if self.blocked & self.pinned:
            raise ValueError("A bracing panel cannot be both blocked and pinned")

    def _ids(self, values, name):
        if not isinstance(values, (list, tuple, set)) or any(not isinstance(x, str) for x in values):
            raise ValueError(f"{name.capitalize()} panels must be a list of panel IDs")
        result = set(values)
        missing = result - self.panels.keys()
        if missing:
            raise ValueError(f"Unknown {name} panel: {sorted(missing)[0]}")
        return result

    @property
    def available(self):
        return set(self.panels) - self.blocked

    def toggle(self, identifier, mode="blocked"):
        if identifier not in self.panels or mode not in {"blocked", "pinned"}:
            raise ValueError("Unknown panel or editing mode")
        if mode == "pinned" and identifier in self.blocked:
            raise ValueError("Unblock this panel before pinning a brace there")
        target, other = (self.blocked, self.pinned) if mode == "blocked" else (self.pinned, self.blocked)
        if identifier in target:
            target.remove(identifier)
        else:
            target.add(identifier)
            other.discard(identifier)

    def to_dict(self):
        return {"schema": SCHEMA, "geometry": self.geometry.to_dict(),
                "blocked_panels": sorted(self.blocked), "pinned_panels": sorted(self.pinned)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.get("schema") != SCHEMA:
            raise ValueError("Unsupported bracing-layout schema")
        if "geometry" not in value:
            raise ValueError("Missing bracing-layout geometry")
        return cls(LayoutGeometry.from_dict(value["geometry"]), value.get("blocked_panels", []), value.get("pinned_panels", []))
