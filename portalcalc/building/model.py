"""Qt-free 3D building model: the BOLT -> PortalCalc building contract.

Schema ``portalcalc.building.v1``. See ``BUILDING_LOADS_3D_PLAN.md``.

World axes (the same as ``bracing_layout``): X across the main span from the
left main column line (0) to the right (span), Y along the building from the
front end gridline (0) to the back (length), Z up. Millimetres throughout.

The model describes *where things are*: gridlines and the frame type on each,
canopies/lean-tos over gridline ranges, wall cladding by segment, openings and
internal partitions. Inputs that are not geometry (gravity kPa, crane,
storage, costing, finishes, ...) are carried unchanged in ``design_inputs``,
which has the same shape as a PortalCalc preset.

``building_model_from_preset`` turns a current single-frame preset into a
uniform building; ``BuildingModel.frame_preset`` turns one gridline back into a
preset the existing 2D engine can build. For a uniform building that round trip
reproduces the original preset.

Recorded but not yet used by any load engine (D7, Q1, Q2 in the plan):
openings (treated as closed), partitions (separate pressure chambers) and
attachment offsets past the end gridline. ``engine_scope_issues`` lists what
the current engines cannot represent.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Mapping

from portalcalc.wind import DIRECTION_ORDER, REGIONAL_WIND_SPEEDS, direction_bearing_degrees


SCHEMA = "portalcalc.building.v1"
TOL_MM = 1e-6

SIDES = ("left", "right")
MAIN_SIDE_WALLS = ("left", "right")
MAIN_END_WALLS = ("front", "back")
MAIN_WALLS = MAIN_SIDE_WALLS + MAIN_END_WALLS
WIND_FROM = ("left", "right", "front", "back")
ROOF_SHAPES = ("Gable", "Skillion")
FRAME_KINDS = ("portal", "propped_end")
ATTACHMENT_KINDS = ("canopy", "lean_to")
LEAN_TO_WALLS = ("outer", "front", "back")
OPENING_KINDS = ("door", "roller_door", "window", "opening")
PARTITION_DIRECTIONS = ("transverse", "longitudinal")
TERRAIN_CATEGORIES = (1.0, 2.0, 2.5, 3.0, 4.0)

# Preset <-> model key ownership.
MAIN_GEOMETRY_KEYS = ("span_mm", "roof_pitch_deg", "eave_height_mm", "roof_shape")
# End-wall and longitudinal-bracing inputs are building level, so they stay in
# design_inputs.geometry rather than in a frame type.
BUILDING_GEOMETRY_PREFIXES = ("end_wall_",)
SITE_WIND_KEYS = ("region", "importance_level", "terrain_category", "orientation",
                  "internal_pressure_mode", "additional_pressure_factor")
ATTACHMENT_SECTION_PREFIXES = ("canopy_", "lean_to_")
PRESET_ADDITION_TYPES = {"Canopy": "canopy", "Lean-to": "lean_to"}
ADDITION_TYPE_NAMES = {kind: name for name, kind in PRESET_ADDITION_TYPES.items()}
# Keys of a preset canopies side block that the attachment itself owns.
ATTACHMENT_OWNED_KEYS = ("type", "length_mm", "lean_enclosure")


class BuildingModelError(ValueError):
    """An invalid building payload; the message names the offending field."""


# ----------------------------------------------------------------- helpers

def _number(value: Any, path: str, *, positive: bool = False, minimum: float | None = None,
            maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise BuildingModelError(f"{path} must be a number")
    if isinstance(value, str):
        value = value.strip()
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BuildingModelError(f"{path} must be a number") from None
    if not math.isfinite(number):
        raise BuildingModelError(f"{path} must be finite")
    if positive and number <= 0.0:
        raise BuildingModelError(f"{path} must be greater than zero")
    if minimum is not None and number < minimum - TOL_MM:
        raise BuildingModelError(f"{path} must be at least {minimum:g}")
    if maximum is not None and number > maximum + TOL_MM:
        raise BuildingModelError(f"{path} must be at most {maximum:g}")
    return number


def _text(value: Any, path: str, choices: tuple | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BuildingModelError(f"{path} must be a non-empty string")
    value = value.strip()
    if choices is not None and value not in choices:
        raise BuildingModelError(f"{path} must be one of {', '.join(choices)}")
    return value


def _object(value: Any, path: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise BuildingModelError(f"{path} must be an object")
    return value


def _array(value: Any, path: str) -> list:
    if not isinstance(value, (list, tuple)):
        raise BuildingModelError(f"{path} must be a list")
    return list(value)


def _plain(value: Any, path: str) -> Any:
    """Deep-copy JSON-shaped data, rejecting anything JSON can't carry."""
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise BuildingModelError(f"{path} keys must be strings")
            out[key] = _plain(item, f"{path}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [_plain(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, float) and not math.isfinite(value):
        raise BuildingModelError(f"{path} must be finite")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise BuildingModelError(f"{path} must be JSON data")


def format_number(value: float) -> str:
    """Preset text form of a number: 21000, 7.5, 0.25 (no trailing zeros)."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _unique_ids(items, path: str) -> None:
    seen = set()
    for index, item in enumerate(items):
        if item.id in seen:
            raise BuildingModelError(f"{path}[{index}].id {item.id!r} is used more than once")
        seen.add(item.id)


# ------------------------------------------------------------- dataclasses

@dataclass(frozen=True)
class Site:
    """Site wind parameters.

    ``north`` uses PortalCalc's orientation selector convention: the direction
    true north points on the building plan drawn with the front end at the top
    and the left wall on the left. A compass name, a bearing, or "Any".
    """
    region: str
    importance_level: int
    terrain_category: float
    north: str
    internal_pressure_mode: str = "Standard"
    pressure_factor: float = 1.0

    @classmethod
    def from_dict(cls, data: Any, path: str = "site") -> "Site":
        data = _object(data, path)
        region = _text(data.get("region"), f"{path}.region").upper()
        if region not in next(iter(REGIONAL_WIND_SPEEDS.values())):
            raise BuildingModelError(f"{path}.region {region!r} is not a supported wind region")
        importance = _number(data.get("importance_level"), f"{path}.importance_level")
        if importance not in (1.0, 2.0, 3.0, 4.0):
            raise BuildingModelError(f"{path}.importance_level must be 1, 2, 3 or 4")
        terrain = _number(data.get("terrain_category"), f"{path}.terrain_category")
        if terrain not in TERRAIN_CATEGORIES:
            raise BuildingModelError(f"{path}.terrain_category must be one of 1, 2, 2.5, 3 or 4")
        north = data.get("north")
        if isinstance(north, (int, float)) and not isinstance(north, bool):
            north = format_number(_number(north, f"{path}.north"))
        north = _text(north, f"{path}.north")
        if north != "Any":
            try:
                direction_bearing_degrees(north)
            except ValueError as error:
                raise BuildingModelError(f"{path}.north: {error}") from None
        mode = _text(data.get("internal_pressure_mode", "Standard"), f"{path}.internal_pressure_mode")
        factor = _number(data.get("pressure_factor", 1.0), f"{path}.pressure_factor", positive=True)
        return cls(region, int(importance), terrain, north, mode, factor)

    def to_dict(self) -> dict:
        return {"region": self.region, "importance_level": self.importance_level,
                "terrain_category": self.terrain_category, "north": self.north,
                "internal_pressure_mode": self.internal_pressure_mode,
                "pressure_factor": self.pressure_factor}


@dataclass(frozen=True)
class MainGeometry:
    """The main building cross-section, constant along the length."""
    span_mm: float
    eave_mm: float
    pitch_deg: float
    roof_shape: str = "Gable"

    @classmethod
    def from_dict(cls, data: Any, path: str = "main") -> "MainGeometry":
        data = _object(data, path)
        geometry = cls(
            _number(data.get("span_mm"), f"{path}.span_mm", positive=True),
            _number(data.get("eave_mm"), f"{path}.eave_mm", positive=True),
            _number(data.get("pitch_deg"), f"{path}.pitch_deg", minimum=-45.0, maximum=45.0),
            _text(data.get("roof_shape", "Gable"), f"{path}.roof_shape", ROOF_SHAPES),
        )
        if min(geometry.roof_z(0.0), geometry.roof_z(geometry.span_mm), geometry.roof_z(geometry.span_mm / 2)) <= 0.0:
            raise BuildingModelError(f"{path}: the roof must stay above ground across the span")
        return geometry

    def roof_z(self, x_mm: float) -> float:
        run = x_mm if self.roof_shape == "Skillion" else min(x_mm, self.span_mm - x_mm)
        return self.eave_mm + run * math.tan(math.radians(self.pitch_deg))

    def max_roof_z(self, x0_mm: float, x1_mm: float) -> float:
        points = [x0_mm, x1_mm]
        if self.roof_shape == "Gable" and x0_mm < self.span_mm / 2 < x1_mm:
            points.append(self.span_mm / 2)
        return max(self.roof_z(x) for x in points)

    def to_dict(self) -> dict:
        return {"span_mm": self.span_mm, "eave_mm": self.eave_mm,
                "pitch_deg": self.pitch_deg, "roof_shape": self.roof_shape}


@dataclass(frozen=True)
class Gridline:
    id: str
    y_mm: float
    frame_type: str

    def to_dict(self) -> dict:
        return {"id": self.id, "y_mm": self.y_mm, "frame_type": self.frame_type}


@dataclass(frozen=True)
class FrameType:
    """A main frame design: preset-shaped structural geometry and sections.

    ``portal``: the ordinary portal/truss frame.
    ``propped_end``: the lightweight end frame (D2/Q3): own columns, rafter
    continuous over load-bearing mullions at ``prop_x_mm``. End gridlines only.
    ``prop_x_mm`` is measured from the left main column centreline, as
    PortalCalc's internal column positions are. In the 2D frame the props are
    pinned internal columns ("Custom positions", "Pinned"), sized as the
    ``internal_column`` section. They are also checked for frame axial force
    combined with end-wall wind bending.
    """
    id: str
    kind: str
    geometry: dict
    sections: dict
    prop_x_mm: tuple = ()

    def to_dict(self) -> dict:
        data = {"id": self.id, "kind": self.kind,
                "geometry": copy.deepcopy(self.geometry), "sections": copy.deepcopy(self.sections)}
        if self.prop_x_mm:
            data["prop_x_mm"] = list(self.prop_x_mm)
        return data


@dataclass(frozen=True)
class Attachment:
    """A canopy or lean-to on one side, over a gridline range.

    ``width_mm`` is the plan projection from the main column line.
    ``properties`` are the preset ``canopies`` keys for that side without the
    side prefix (frame_system, wind_model, eave_height_mm, pitch_deg, g_kpa...).
    ``sections`` are the preset section keys without the side prefix
    (canopy_top_chord, lean_to_column, ...). Offsets extend the attachment
    past its first/last gridline (Q2's eave-beam case; not yet analysed).
    """
    id: str
    side: str
    kind: str
    from_gridline: str
    to_gridline: str
    width_mm: float
    properties: dict
    sections: dict
    start_offset_mm: float = 0.0
    end_offset_mm: float = 0.0

    def to_dict(self) -> dict:
        data = {"id": self.id, "side": self.side, "kind": self.kind,
                "from_gridline": self.from_gridline, "to_gridline": self.to_gridline,
                "width_mm": self.width_mm, "properties": copy.deepcopy(self.properties),
                "sections": copy.deepcopy(self.sections)}
        if self.start_offset_mm:
            data["start_offset_mm"] = self.start_offset_mm
        if self.end_offset_mm:
            data["end_offset_mm"] = self.end_offset_mm
        return data


@dataclass(frozen=True)
class Wall:
    """Cladding along one face as merged (from_mm, to_mm, clad) segments.

    Side walls run along Y; end walls along X. Face ids: ``left``, ``right``,
    ``front``, ``back`` for the main building and ``<attachment id>/outer``,
    ``/front``, ``/back`` for a lean-to (its inner wall is the main side wall).
    """
    face: str
    segments: tuple

    @property
    def fully_clad(self) -> bool:
        return all(clad for _, _, clad in self.segments)

    @property
    def fully_open(self) -> bool:
        return not any(clad for _, _, clad in self.segments)

    def clad_overlapping(self, a_mm: float, b_mm: float) -> bool:
        return any(clad and s0 < b_mm - TOL_MM and s1 > a_mm + TOL_MM for s0, s1, clad in self.segments)

    def clad_covering(self, a_mm: float, b_mm: float) -> bool:
        """True when [a, b] lies entirely within clad segments."""
        covered = [(max(s0, a_mm), min(s1, b_mm)) for s0, s1, clad in self.segments
                   if clad and s0 < b_mm - TOL_MM and s1 > a_mm + TOL_MM]
        position = a_mm
        for s0, s1 in sorted(covered):
            if s0 > position + TOL_MM:
                return False
            position = max(position, s1)
        return position >= b_mm - TOL_MM

    def to_dict(self) -> dict:
        if len(self.segments) == 1:
            return {"face": self.face, "clad": self.segments[0][2]}
        return {"face": self.face, "segments": [
            {"from_mm": s0, "to_mm": s1, "clad": clad} for s0, s1, clad in self.segments]}


@dataclass(frozen=True)
class Opening:
    """A door, window or other opening in a clad wall. Treated as closed (D7)."""
    id: str
    face: str
    kind: str
    from_mm: float
    to_mm: float
    sill_mm: float
    head_mm: float

    def to_dict(self) -> dict:
        return {"id": self.id, "face": self.face, "kind": self.kind, "from_mm": self.from_mm,
                "to_mm": self.to_mm, "sill_mm": self.sill_mm, "head_mm": self.head_mm}


@dataclass(frozen=True)
class Partition:
    """A full-height internal partition bounding pressure chambers (Q1).

    ``transverse``: across the building at y = position_mm, from x0 to x1.
    ``longitudinal``: along the building at x = position_mm, from y0 to y1.
    """
    id: str
    direction: str
    position_mm: float
    from_mm: float
    to_mm: float

    def to_dict(self) -> dict:
        return {"id": self.id, "direction": self.direction, "position_mm": self.position_mm,
                "from_mm": self.from_mm, "to_mm": self.to_mm}


GROUP_INPUT_BLOCKS = ("geometry", "sections", "canopies", "calculation_service")


@dataclass(frozen=True)
class SizingGroup:
    """Gridlines sized as one 2D frame (same frame type and attachments).

    ``frame_inputs`` optionally overrides the frame type for this group with
    preset-shaped ``geometry`` (frame-type keys, e.g. truss_depth_mm),
    ``sections`` (preset section keys, including e.g. left_lean_to_column) and
    ``canopies`` (e.g. left_depth_mm). Sized results are returned this way when
    a building has more than one group.
    """
    id: str
    gridlines: tuple
    frame_inputs: dict | None = None

    def to_dict(self) -> dict:
        data = {"id": self.id, "gridlines": list(self.gridlines)}
        if self.frame_inputs:
            data["frame_inputs"] = copy.deepcopy(self.frame_inputs)
        return data


# ------------------------------------------------------------------ model

@dataclass(frozen=True)
class BuildingModel:
    site: Site
    main: MainGeometry
    gridlines: tuple
    frame_types: tuple
    attachments: tuple = ()
    walls: tuple = ()
    openings: tuple = ()
    partitions: tuple = ()
    sizing_groups: tuple = ()
    design_inputs: dict | None = None

    # ---- construction / serialisation

    @classmethod
    def from_dict(cls, data: Any) -> "BuildingModel":
        data = _object(data, "building")
        schema = data.get("schema", SCHEMA)
        if schema != SCHEMA:
            raise BuildingModelError(f"unsupported building schema {schema!r}; expected {SCHEMA}")
        site = Site.from_dict(data.get("site"))
        main = MainGeometry.from_dict(data.get("main"))
        frame_types = tuple(_frame_type(item, f"frame_types[{i}]", main)
                            for i, item in enumerate(_array(data.get("frame_types"), "frame_types")))
        if not frame_types:
            raise BuildingModelError("frame_types needs at least one frame type")
        _unique_ids(frame_types, "frame_types")
        gridlines = _gridlines(data.get("gridlines"), {ft.id: ft for ft in frame_types})
        index = {g.id: i for i, g in enumerate(gridlines)}
        attachments = tuple(_attachment(item, f"attachments[{i}]", index, gridlines)
                            for i, item in enumerate(_array(data.get("attachments", []), "attachments")))
        _unique_ids(attachments, "attachments")
        _check_attachment_overlap(attachments, gridlines, index)
        partial = cls(site, main, gridlines, frame_types, attachments)
        walls = _walls(data.get("walls"), partial)
        partial = cls(site, main, gridlines, frame_types, attachments, walls)
        openings = tuple(_opening(item, f"openings[{i}]", partial)
                         for i, item in enumerate(_array(data.get("openings", []), "openings")))
        _unique_ids(openings, "openings")
        partitions = tuple(_partition(item, f"partitions[{i}]", partial)
                           for i, item in enumerate(_array(data.get("partitions", []), "partitions")))
        _unique_ids(partitions, "partitions")
        groups = _sizing_groups(data.get("sizing_groups", []), partial)
        design_inputs = _plain(data.get("design_inputs", {}), "design_inputs")
        if not isinstance(design_inputs, dict):
            raise BuildingModelError("design_inputs must be an object")
        return cls(site, main, gridlines, frame_types, attachments, walls, openings,
                   partitions, groups, design_inputs)

    def to_dict(self) -> dict:
        data = {
            "schema": SCHEMA,
            "site": self.site.to_dict(),
            "main": self.main.to_dict(),
            "gridlines": [g.to_dict() for g in self.gridlines],
            "frame_types": [f.to_dict() for f in self.frame_types],
            "attachments": [a.to_dict() for a in self.attachments],
            "walls": [w.to_dict() for w in self.walls],
            "openings": [o.to_dict() for o in self.openings],
            "partitions": [p.to_dict() for p in self.partitions],
        }
        if self.sizing_groups:
            data["sizing_groups"] = [g.to_dict() for g in self.sizing_groups]
        data["design_inputs"] = copy.deepcopy(self.design_inputs or {})
        return data

    # ---- geometry queries

    @property
    def length_mm(self) -> float:
        return self.gridlines[-1].y_mm

    @property
    def gridline_ids(self) -> tuple:
        return tuple(g.id for g in self.gridlines)

    @property
    def bay_lengths_mm(self) -> tuple:
        ys = [g.y_mm for g in self.gridlines]
        return tuple(b - a for a, b in zip(ys, ys[1:]))

    @property
    def tributary_widths_mm(self) -> tuple:
        """Half of each adjacent bay, per gridline (end frames take half a bay)."""
        bays = self.bay_lengths_mm
        return tuple((bays[i - 1] if i > 0 else 0.0) / 2 + (bays[i] if i < len(bays) else 0.0) / 2
                     for i in range(len(self.gridlines)))

    def gridline_index(self, gridline_id: str) -> int:
        for index, gridline in enumerate(self.gridlines):
            if gridline.id == gridline_id:
                return index
        raise KeyError(f"unknown gridline {gridline_id!r}")

    def frame_type(self, frame_type_id: str) -> FrameType:
        for frame_type in self.frame_types:
            if frame_type.id == frame_type_id:
                return frame_type
        raise KeyError(f"unknown frame type {frame_type_id!r}")

    def attachment_extent_mm(self, attachment: Attachment) -> tuple:
        y0 = self.gridlines[self.gridline_index(attachment.from_gridline)].y_mm - attachment.start_offset_mm
        y1 = self.gridlines[self.gridline_index(attachment.to_gridline)].y_mm + attachment.end_offset_mm
        return y0, y1

    def attachments_at(self, gridline_id: str) -> dict:
        """{side: Attachment} for the attachments with a rafter on this gridline."""
        index = self.gridline_index(gridline_id)
        found = {}
        for attachment in self.attachments:
            if self.gridline_index(attachment.from_gridline) <= index <= self.gridline_index(attachment.to_gridline):
                found[attachment.side] = attachment
        return found

    def face_extent_mm(self, face: str) -> tuple:
        """(start, end) of a wall face along its own axis (Y for side walls, X for end walls)."""
        if face in MAIN_SIDE_WALLS:
            return 0.0, self.length_mm
        if face in MAIN_END_WALLS:
            return 0.0, self.main.span_mm
        attachment_id, _, part = face.rpartition("/")
        attachment = next((a for a in self.attachments if a.id == attachment_id), None)
        if attachment is None or attachment.kind != "lean_to" or part not in LEAN_TO_WALLS:
            raise KeyError(f"unknown wall face {face!r}")
        if part == "outer":
            return self.attachment_extent_mm(attachment)
        if attachment.side == "left":
            return -attachment.width_mm, 0.0
        return self.main.span_mm, self.main.span_mm + attachment.width_mm

    def expected_wall_faces(self) -> tuple:
        faces = list(MAIN_WALLS)
        for attachment in self.attachments:
            if attachment.kind == "lean_to":
                faces.extend(f"{attachment.id}/{part}" for part in LEAN_TO_WALLS)
        return tuple(faces)

    def wall(self, face: str) -> Wall:
        for wall in self.walls:
            if wall.face == face:
                return wall
        raise KeyError(f"unknown wall face {face!r}")

    def side_wall_clad_at(self, face: str, gridline_id: str) -> bool:
        """A column line is clad when girts in either adjacent bay frame into it."""
        index = self.gridline_index(gridline_id)
        y = self.gridlines[index].y_mm
        y_prev = self.gridlines[index - 1].y_mm if index > 0 else y
        y_next = self.gridlines[index + 1].y_mm if index + 1 < len(self.gridlines) else y
        return self.wall(face).clad_overlapping(y_prev, y_next)

    def frame_signature(self, gridline_id: str) -> tuple:
        """Gridlines with equal signatures have the same 2D frame model."""
        attached = self.attachments_at(gridline_id)
        return (self.gridlines[self.gridline_index(gridline_id)].frame_type,
                tuple(attached[side].id if side in attached else None for side in SIDES))

    def default_sizing_groups(self) -> tuple:
        """One group per frame signature, end frames included (D1)."""
        groups: dict = {}
        for gridline in self.gridlines:
            groups.setdefault(self.frame_signature(gridline.id), []).append(gridline.id)
        return tuple(SizingGroup(f"G{i + 1}", tuple(ids)) for i, ids in enumerate(groups.values()))

    def resolved_sizing_groups(self) -> tuple:
        return self.sizing_groups or self.default_sizing_groups()

    def wind_source_direction(self, wind_from: str):
        """Compass source of wind blowing onto the ``wind_from`` face.

        Matches ``MainWindow.wind_source_direction_for_case`` for Left/Right/End
        (End = from the front) without the symmetric-end shortcut; "back" is the
        reverse end direction. Returns a compass name when ``north`` is one,
        otherwise a bearing in degrees; "Any" passes through.
        """
        if wind_from not in WIND_FROM:
            raise ValueError(f"wind_from must be one of {', '.join(WIND_FROM)}")
        if self.site.north == "Any":
            return "Any"
        screen = {"front": 0.0, "right": 90.0, "back": 180.0, "left": 270.0}[wind_from]
        bearing = (screen - direction_bearing_degrees(self.site.north)) % 360.0
        if self.site.north in DIRECTION_ORDER:
            return DIRECTION_ORDER[int(round(bearing / 45.0)) % 8]
        return bearing

    # ---- engine scope

    def engine_scope_issues(self) -> list:
        """Features the current single-frame 2D engine cannot represent."""
        issues = []
        if len(self.frame_types) > 1:
            issues.append("more than one frame type along the building")
        if any(ft.kind != "portal" for ft in self.frame_types):
            issues.append("lightweight propped end frames are not analysed yet")
        first, last = self.gridlines[0].id, self.gridlines[-1].id
        for attachment in self.attachments:
            if attachment.from_gridline != first or attachment.to_gridline != last:
                issues.append(f"attachment {attachment.id} runs part of the building length")
            if attachment.start_offset_mm or attachment.end_offset_mm:
                issues.append(f"attachment {attachment.id} extends past a gridline (eave-beam support)")
        for wall in self.walls:
            if len(wall.segments) > 1:
                issues.append(f"wall {wall.face} is partly clad")
        if self.partitions:
            issues.append("internal partitions (separate pressure chambers) are not analysed yet")
        bays = self.bay_lengths_mm
        if bays and max(bays[:-1] or bays) - min(bays[:-1] or bays) > TOL_MM:
            issues.append("unequal bays (only the last bay may be shorter)")
        return issues

    # ---- back to the 2D engine

    def frame_preset(self, gridline_id: str) -> dict:
        """The PortalCalc preset for the 2D frame on one gridline.

        Wind enclosure in the result is the legacy whole-wall form: a side
        wall is clad when cladding frames into that column line, an end wall
        when any of it is clad. Building loads (phase 2+) supersede it.
        The legacy bay size is the largest bay, as the 2D frame always used
        one full bay of tributary.
        """
        index = self.gridline_index(gridline_id)
        gridline = self.gridlines[index]
        frame_type = self.frame_type(gridline.frame_type)
        preset = copy.deepcopy(self.design_inputs or {})

        geometry = preset.setdefault("geometry", {})
        geometry.update(copy.deepcopy(frame_type.geometry))
        if frame_type.kind == "propped_end":
            geometry.update({
                "internal_column_count": "Custom positions",
                "internal_column_positions_m": ", ".join(format_number(x / 1000.0) for x in frame_type.prop_x_mm),
                "internal_column_connection": "Pinned",
            })
        geometry.update({
            "span_mm": format_number(self.main.span_mm),
            "roof_pitch_deg": format_number(self.main.pitch_deg),
            "eave_height_mm": format_number(self.main.eave_mm),
            "roof_shape": self.main.roof_shape,
        })
        sections = preset.setdefault("sections", {})
        sections.update(copy.deepcopy(frame_type.sections))

        walls = {face: (self.side_wall_clad_at(face, gridline_id) if face in MAIN_SIDE_WALLS
                        else not self.wall(face).fully_open) for face in MAIN_WALLS}
        canopies = preset.setdefault("canopies", {})
        attached = self.attachments_at(gridline_id)
        for side in SIDES:
            attachment = attached.get(side)
            if attachment is None:
                canopies[f"{side}_type"] = "None"
                continue
            canopies[f"{side}_type"] = ADDITION_TYPE_NAMES[attachment.kind]
            canopies[f"{side}_length_mm"] = format_number(attachment.width_mm)
            for key, value in attachment.properties.items():
                canopies[f"{side}_{key}"] = copy.deepcopy(value)
            for key, value in attachment.sections.items():
                sections[f"{side}_{key}"] = copy.deepcopy(value)
            if attachment.kind == "lean_to":
                outer = self.wall(f"{attachment.id}/outer")
                y = gridline.y_mm
                y_prev = self.gridlines[index - 1].y_mm if index > 0 else y
                y_next = self.gridlines[index + 1].y_mm if index + 1 < len(self.gridlines) else y
                inner_key = "right" if side == "left" else "left"
                outer_clad = outer.clad_overlapping(y_prev, y_next)
                canopies[f"{side}_lean_enclosure"] = {
                    side: outer_clad,
                    inner_key: walls[side],
                    "front": not self.wall(f"{attachment.id}/front").fully_open,
                    "back": not self.wall(f"{attachment.id}/back").fully_open,
                }
                canopies[f"{side}_lean_outer_wall_clad"] = "Yes" if outer_clad else "No"

        group = next((g for g in self.sizing_groups if gridline_id in g.gridlines), None)
        for block, values in ((group.frame_inputs or {}) if group else {}).items():
            preset.setdefault(block, {}).update(copy.deepcopy(values))

        wind = preset.setdefault("wind", {})
        wind.update({
            "region": self.site.region,
            "importance_level": str(self.site.importance_level),
            "terrain_category": format_number(self.site.terrain_category),
            "orientation": self.site.north,
            "internal_pressure_mode": self.site.internal_pressure_mode,
            "additional_pressure_factor": format_number(self.site.pressure_factor),
            "wall_enclosure": walls,
            "building_length_m": format_number(self.length_mm / 1000.0),
        })
        from portalcalc import bay_layout
        preset["geometry"]["bays_mm"] = bay_layout.format_bays(self.bay_lengths_mm)
        loads = preset.setdefault("loads", {})
        bay_mm = max(self.bay_lengths_mm)
        loads["bay_size_mm"] = format_number(bay_mm)
        loads["bay_size_m"] = bay_mm / 1000.0
        return preset

    def layout_geometry(self):
        """The ``bracing_layout.LayoutGeometry`` for this building.

        Mirrors ``MainWindow.current_bracing_layout_geometry``: full-length open
        canopies on gable roofs only; mullion stations from the end-wall
        spacing override when set.
        """
        from portalcalc.bracing.layout import LayoutGeometry

        geometry_inputs = (self.design_inputs or {}).get("geometry", {})
        override = str(geometry_inputs.get("end_wall_mullion_spacing_override_mm", "") or "").strip()
        end_stations = None
        if override:
            spacing = _number(override, "design_inputs.geometry.end_wall_mullion_spacing_override_mm", positive=True)
            spaces = max(1, math.ceil(self.main.span_mm / spacing))
            end_stations = [self.main.span_mm * i / spaces for i in range(spaces + 1)]
        canopies = []
        if self.main.roof_shape == "Gable":
            first, last = self.gridlines[0].id, self.gridlines[-1].id
            for attachment in self.attachments:
                if attachment.kind != "canopy":
                    continue
                if attachment.from_gridline != first or attachment.to_gridline != last:
                    raise ValueError(f"canopy {attachment.id} runs part of the length; "
                                     "the bracing layout supports full-length canopies only")
                pitch = _number(attachment.properties.get("pitch_deg", 0.0), f"{attachment.id}.pitch_deg")
                canopies.append({"side": attachment.side, "width_mm": attachment.width_mm,
                                 "drop_mm": math.tan(math.radians(pitch)) * attachment.width_mm})
        return LayoutGeometry.create(
            self.main.span_mm, self.length_mm, self.main.eave_mm, self.main.pitch_deg, self.main.roof_shape,
            bay_stations_mm=[g.y_mm for g in self.gridlines], end_wall_stations_mm=end_stations,
            canopies=canopies or None)


# ------------------------------------------------------------- validators

def _frame_type(data: Any, path: str, main: MainGeometry) -> FrameType:
    data = _object(data, path)
    kind = _text(data.get("kind", "portal"), f"{path}.kind", FRAME_KINDS)
    props = tuple(_number(x, f"{path}.prop_x_mm[{i}]")
                  for i, x in enumerate(_array(data.get("prop_x_mm", []), f"{path}.prop_x_mm")))
    if kind == "portal" and props:
        raise BuildingModelError(f"{path}.prop_x_mm is only allowed on a propped_end frame type")
    if kind == "propped_end":
        if not props:
            raise BuildingModelError(f"{path}.prop_x_mm needs at least one mullion prop position")
        if any(not TOL_MM < x < main.span_mm - TOL_MM for x in props):
            raise BuildingModelError(f"{path}.prop_x_mm positions must lie strictly inside the span")
        if any(b - a <= TOL_MM for a, b in zip(props, props[1:])):
            raise BuildingModelError(f"{path}.prop_x_mm must be strictly increasing")
    geometry = _plain(data.get("geometry", {}), f"{path}.geometry")
    sections = _plain(data.get("sections", {}), f"{path}.sections")
    if not isinstance(geometry, dict) or not isinstance(sections, dict):
        raise BuildingModelError(f"{path}.geometry and {path}.sections must be objects")
    owned = set(MAIN_GEOMETRY_KEYS).intersection(geometry)
    if owned:
        raise BuildingModelError(f"{path}.geometry must not set {', '.join(sorted(owned))}; use main")
    return FrameType(_text(data.get("id"), f"{path}.id"), kind, geometry, sections, props)


def _gridlines(value: Any, frame_types: dict) -> tuple:
    items = _array(value, "gridlines")
    if len(items) < 2:
        raise BuildingModelError("gridlines needs at least two gridlines")
    gridlines = []
    for i, item in enumerate(items):
        path = f"gridlines[{i}]"
        item = _object(item, path)
        frame_type = _text(item.get("frame_type"), f"{path}.frame_type")
        if frame_type not in frame_types:
            raise BuildingModelError(f"{path}.frame_type {frame_type!r} is not a defined frame type")
        gridlines.append(Gridline(_text(item.get("id"), f"{path}.id"),
                                  _number(item.get("y_mm"), f"{path}.y_mm"), frame_type))
    _unique_ids(gridlines, "gridlines")
    if abs(gridlines[0].y_mm) > TOL_MM:
        raise BuildingModelError("gridlines[0].y_mm must be 0 (the front end)")
    for i in range(1, len(gridlines)):
        if gridlines[i].y_mm - gridlines[i - 1].y_mm <= TOL_MM:
            raise BuildingModelError(f"gridlines[{i}].y_mm must be greater than the previous gridline")
    for i, gridline in enumerate(gridlines):
        if frame_types[gridline.frame_type].kind == "propped_end" and 0 < i < len(gridlines) - 1:
            raise BuildingModelError(f"gridlines[{i}]: a propped_end frame type is only allowed on an end gridline")
    if len(gridlines) > 501:
        raise BuildingModelError("gridlines supports at most 500 bays")
    return tuple(gridlines)


def _attachment(data: Any, path: str, index: dict, gridlines: tuple) -> Attachment:
    data = _object(data, path)
    side = _text(data.get("side"), f"{path}.side", SIDES)
    kind = _text(data.get("kind"), f"{path}.kind", ATTACHMENT_KINDS)
    first = _text(data.get("from_gridline"), f"{path}.from_gridline")
    last = _text(data.get("to_gridline"), f"{path}.to_gridline")
    for name, value in (("from_gridline", first), ("to_gridline", last)):
        if value not in index:
            raise BuildingModelError(f"{path}.{name} {value!r} is not a gridline")
    if index[first] >= index[last]:
        raise BuildingModelError(f"{path}.from_gridline must come before to_gridline")
    start = _number(data.get("start_offset_mm", 0.0), f"{path}.start_offset_mm", minimum=0.0)
    end = _number(data.get("end_offset_mm", 0.0), f"{path}.end_offset_mm", minimum=0.0)
    if start and index[first] == 0:
        raise BuildingModelError(f"{path}.start_offset_mm would extend past the front of the building")
    if end and index[last] == len(gridlines) - 1:
        raise BuildingModelError(f"{path}.end_offset_mm would extend past the back of the building")
    if start and start >= gridlines[index[first]].y_mm - gridlines[index[first] - 1].y_mm - TOL_MM:
        raise BuildingModelError(f"{path}.start_offset_mm must be less than the adjacent bay")
    if end and end >= gridlines[index[last] + 1].y_mm - gridlines[index[last]].y_mm - TOL_MM:
        raise BuildingModelError(f"{path}.end_offset_mm must be less than the adjacent bay")
    properties = _plain(data.get("properties", {}), f"{path}.properties")
    sections = _plain(data.get("sections", {}), f"{path}.sections")
    if not isinstance(properties, dict) or not isinstance(sections, dict):
        raise BuildingModelError(f"{path}.properties and {path}.sections must be objects")
    owned = set(ATTACHMENT_OWNED_KEYS).intersection(properties)
    if owned:
        raise BuildingModelError(f"{path}.properties must not set {', '.join(sorted(owned))}")
    return Attachment(_text(data.get("id"), f"{path}.id"), side, kind, first, last,
                      _number(data.get("width_mm"), f"{path}.width_mm", positive=True),
                      properties, sections, start, end)


def _check_attachment_overlap(attachments: tuple, gridlines: tuple, index: dict) -> None:
    for side in SIDES:
        spans = sorted(
            (index[a.from_gridline], index[a.to_gridline],
             gridlines[index[a.from_gridline]].y_mm - a.start_offset_mm,
             gridlines[index[a.to_gridline]].y_mm + a.end_offset_mm, a.id)
            for a in attachments if a.side == side)
        for (i0, i1, _, y1, a_id), (j0, _, z0, _, b_id) in zip(spans, spans[1:]):
            if j0 <= i1 or z0 < y1 - TOL_MM:
                raise BuildingModelError(f"attachments {a_id} and {b_id} overlap on the {side} side")


def _segments(data: Mapping, path: str, extent: tuple) -> tuple:
    if "segments" in data:
        if "clad" in data:
            raise BuildingModelError(f"{path}: give either clad or segments, not both")
        raw = []
        for i, item in enumerate(_array(data.get("segments"), f"{path}.segments")):
            item = _object(item, f"{path}.segments[{i}]")
            clad = item.get("clad")
            if not isinstance(clad, bool):
                raise BuildingModelError(f"{path}.segments[{i}].clad must be true or false")
            raw.append((_number(item.get("from_mm"), f"{path}.segments[{i}].from_mm"),
                        _number(item.get("to_mm"), f"{path}.segments[{i}].to_mm"), clad))
    else:
        clad = data.get("clad")
        if not isinstance(clad, bool):
            raise BuildingModelError(f"{path}.clad must be true or false (or give segments)")
        raw = [(extent[0], extent[1], clad)]
    if not raw:
        raise BuildingModelError(f"{path}.segments must not be empty")
    position = extent[0]
    for i, (s0, s1, _) in enumerate(raw):
        if abs(s0 - position) > TOL_MM:
            raise BuildingModelError(
                f"{path}.segments[{i}] must start at {position:g} mm; segments must cover "
                f"{extent[0]:g} to {extent[1]:g} mm in order without gaps or overlaps")
        if s1 - s0 <= TOL_MM:
            raise BuildingModelError(f"{path}.segments[{i}] must have positive length")
        position = s1
    if abs(position - extent[1]) > TOL_MM:
        raise BuildingModelError(f"{path}.segments must finish at {extent[1]:g} mm")
    merged = []
    for s0, s1, clad in raw:
        if merged and merged[-1][2] == clad:
            merged[-1] = (merged[-1][0], s1, clad)
        else:
            merged.append((s0, s1, clad))
    merged[0] = (extent[0], merged[0][1], merged[0][2])
    merged[-1] = (merged[-1][0], extent[1], merged[-1][2])
    return tuple(merged)


def _walls(value: Any, model: BuildingModel) -> tuple:
    expected = model.expected_wall_faces()
    walls = {}
    for i, item in enumerate(_array(value, "walls")):
        path = f"walls[{i}]"
        item = _object(item, path)
        face = _text(item.get("face"), f"{path}.face")
        if face not in expected:
            raise BuildingModelError(f"{path}.face {face!r} is not a wall of this building "
                                     f"(expected one of {', '.join(expected)})")
        if face in walls:
            raise BuildingModelError(f"{path}.face {face!r} is given more than once")
        walls[face] = Wall(face, _segments(item, path, model.face_extent_mm(face)))
    missing = [face for face in expected if face not in walls]
    if missing:
        raise BuildingModelError(f"walls is missing {', '.join(missing)}")
    return tuple(walls[face] for face in expected)


def _opening(data: Any, path: str, model: BuildingModel) -> Opening:
    data = _object(data, path)
    face = _text(data.get("face"), f"{path}.face")
    if face not in model.expected_wall_faces():
        raise BuildingModelError(f"{path}.face {face!r} is not a wall of this building")
    x0, x1 = model.face_extent_mm(face)
    start = _number(data.get("from_mm"), f"{path}.from_mm", minimum=x0, maximum=x1)
    end = _number(data.get("to_mm"), f"{path}.to_mm", minimum=x0, maximum=x1)
    if end - start <= TOL_MM:
        raise BuildingModelError(f"{path}.to_mm must be greater than from_mm")
    sill = _number(data.get("sill_mm", 0.0), f"{path}.sill_mm", minimum=0.0)
    head = _number(data.get("head_mm"), f"{path}.head_mm", positive=True)
    if head - sill <= TOL_MM:
        raise BuildingModelError(f"{path}.head_mm must be above sill_mm")
    if face in MAIN_SIDE_WALLS:
        height = model.main.eave_mm
    elif face in MAIN_END_WALLS:
        height = model.main.max_roof_z(start, end)
    else:
        height = None
    if height is not None and head > height + TOL_MM:
        raise BuildingModelError(f"{path}.head_mm is above the wall ({height:g} mm)")
    if not model.wall(face).clad_covering(start, end):
        raise BuildingModelError(f"{path} must lie within clad parts of wall {face}")
    return Opening(_text(data.get("id"), f"{path}.id"), face,
                   _text(data.get("kind", "opening"), f"{path}.kind", OPENING_KINDS),
                   start, end, sill, head)


def _partition(data: Any, path: str, model: BuildingModel) -> Partition:
    data = _object(data, path)
    direction = _text(data.get("direction"), f"{path}.direction", PARTITION_DIRECTIONS)
    across, along = model.main.span_mm, model.length_mm
    position_limit, extent = (along, across) if direction == "transverse" else (across, along)
    position = _number(data.get("position_mm"), f"{path}.position_mm")
    if not TOL_MM < position < position_limit - TOL_MM:
        raise BuildingModelError(f"{path}.position_mm must lie strictly inside the building")
    start = _number(data.get("from_mm"), f"{path}.from_mm", minimum=0.0, maximum=extent)
    end = _number(data.get("to_mm"), f"{path}.to_mm", minimum=0.0, maximum=extent)
    if end - start <= TOL_MM:
        raise BuildingModelError(f"{path}.to_mm must be greater than from_mm")
    return Partition(_text(data.get("id"), f"{path}.id"), direction, position, start, end)


def _sizing_groups(value: Any, model: BuildingModel) -> tuple:
    items = _array(value, "sizing_groups")
    if not items:
        return ()
    groups, owner = [], {}
    for i, item in enumerate(items):
        path = f"sizing_groups[{i}]"
        item = _object(item, path)
        ids = tuple(_text(x, f"{path}.gridlines[{j}]")
                    for j, x in enumerate(_array(item.get("gridlines"), f"{path}.gridlines")))
        if not ids:
            raise BuildingModelError(f"{path}.gridlines must not be empty")
        for gridline_id in ids:
            if gridline_id not in model.gridline_ids:
                raise BuildingModelError(f"{path}: {gridline_id!r} is not a gridline")
            if gridline_id in owner:
                raise BuildingModelError(f"{path}: gridline {gridline_id} is already in group {owner[gridline_id]}")
        frame_inputs = _plain(item.get("frame_inputs") or {}, f"{path}.frame_inputs")
        if not isinstance(frame_inputs, dict):
            raise BuildingModelError(f"{path}.frame_inputs must be an object")
        for block, values in frame_inputs.items():
            if block not in GROUP_INPUT_BLOCKS or not isinstance(values, dict):
                raise BuildingModelError(f"{path}.frame_inputs may only hold objects for {', '.join(GROUP_INPUT_BLOCKS)}")
        owned = set(MAIN_GEOMETRY_KEYS).intersection(frame_inputs.get("geometry", {}))
        if owned:
            raise BuildingModelError(f"{path}.frame_inputs.geometry must not set {', '.join(sorted(owned))}")
        group = SizingGroup(_text(item.get("id"), f"{path}.id"), ids, frame_inputs or None)
        types = {model.gridlines[model.gridline_index(g)].frame_type for g in ids}
        if len(types) > 1:
            raise BuildingModelError(f"{path}: a sizing group must use one frame type, found {', '.join(sorted(types))}")
        signatures = {model.frame_signature(g) for g in ids}
        if len(signatures) > 1:
            raise BuildingModelError(f"{path}: a sizing group must share one 2D frame; its gridlines have "
                                     "different canopies or lean-tos")
        owner.update({g: group.id for g in ids})
        groups.append(group)
    _unique_ids(groups, "sizing_groups")
    missing = [g for g in model.gridline_ids if g not in owner]
    if missing:
        raise BuildingModelError(f"sizing_groups must include every gridline; missing {', '.join(missing)}")
    return tuple(groups)


# ------------------------------------------------------------ from preset

def _preset_float(value: Any, path: str, default: float | None = None) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        if default is None:
            raise BuildingModelError(f"preset {path} is missing")
        return default
    return _number(value, f"preset {path}")


def building_model_from_preset(preset: Mapping) -> BuildingModel:
    """A uniform building from a current single-frame PortalCalc preset.

    Gridlines follow ``bracing_layout.LayoutGeometry``: full bays from the
    front with a shorter last bay when the length is not a whole number of
    bays. Every gridline gets the one frame type; canopies and lean-tos run the
    full length; walls are clad or open over their whole extent.
    """
    preset = _object(preset, "preset")
    design = _plain(preset, "preset")
    geometry = design.setdefault("geometry", {})
    canopies = design.setdefault("canopies", {})
    sections = design.setdefault("sections", {})
    wind = design.setdefault("wind", {})
    loads = design.setdefault("loads", {})

    main = MainGeometry.from_dict({
        "span_mm": _preset_float(geometry.get("span_mm"), "geometry.span_mm"),
        "eave_mm": _preset_float(geometry.get("eave_height_mm"), "geometry.eave_height_mm"),
        "pitch_deg": _preset_float(geometry.get("roof_pitch_deg"), "geometry.roof_pitch_deg"),
        "roof_shape": geometry.get("roof_shape") or "Gable",
    })
    site = Site.from_dict({
        "region": wind.get("region", "A5"),
        "importance_level": wind.get("importance_level", 1),
        "terrain_category": wind.get("terrain_category", 2),
        "north": wind.get("orientation", "E"),
        "internal_pressure_mode": wind.get("internal_pressure_mode") or "Standard",
        "pressure_factor": _preset_float(wind.get("additional_pressure_factor"),
                                         "wind.additional_pressure_factor", 1.0),
    })

    from portalcalc import bay_layout
    bays_text = geometry.pop("bays_mm", None)
    if bays_text not in (None, ""):
        try:
            stations = bay_layout.stations(bay_layout.parse_bays(bays_text))
        except bay_layout.BayListError as error:
            raise BuildingModelError(f"geometry.bays_mm: {error}") from None
    else:
        # Older presets: full bays from the front and a shorter last bay (as the bracing layout).
        length = _preset_float(wind.get("building_length_m"), "wind.building_length_m") * 1000.0
        if loads.get("bay_size_mm") not in (None, ""):
            bay = _preset_float(loads.get("bay_size_mm"), "loads.bay_size_mm")
        else:
            bay = _preset_float(loads.get("bay_size_m"), "loads.bay_size_m") * 1000.0
        if length <= 0.0 or bay <= 0.0:
            raise BuildingModelError("preset building length and bay size must be greater than zero")
        count = math.ceil(length / bay)
        stations = [min(i * bay, length) for i in range(count + 1)]

    frame_geometry = {key: geometry.pop(key) for key in list(geometry)
                      if key not in MAIN_GEOMETRY_KEYS and not key.startswith(BUILDING_GEOMETRY_PREFIXES)}
    for key in MAIN_GEOMETRY_KEYS:
        geometry.pop(key, None)
    side_section_keys = {key for key in sections
                         if any(key.startswith(f"{side}_{prefix}") for side in SIDES
                                for prefix in ATTACHMENT_SECTION_PREFIXES)}
    frame_sections = {key: sections.pop(key) for key in list(sections) if key not in side_section_keys}
    frame_types = [{"id": "FT1", "kind": "portal", "geometry": frame_geometry, "sections": frame_sections}]
    gridlines = [{"id": f"GL{i + 1}", "y_mm": y, "frame_type": "FT1"} for i, y in enumerate(stations)]

    enclosure = wind.pop("wall_enclosure", None)
    if not isinstance(enclosure, Mapping):
        enclosure = _legacy_wall_states(wind.get("frame_type"))
    main_walls = {face: bool(enclosure.get(face, True)) for face in MAIN_WALLS}

    attachments, lean_walls = [], []
    for side in SIDES:
        width = _preset_float(canopies.get(f"{side}_length_mm"), f"canopies.{side}_length_mm", 0.0)
        kind_name = canopies.get(f"{side}_type")
        if kind_name is None and width > 0.0:
            kind_name = "Canopy"  # apply_preset_data's legacy rule
        kind = PRESET_ADDITION_TYPES.get(kind_name)
        if kind is None or width <= 1e-6:
            continue
        attachment_id = f"{side}_{kind}"
        prefix = f"{side}_"
        properties = {key[len(prefix):]: canopies.pop(key) for key in list(canopies)
                      if key.startswith(prefix) and key[len(prefix):] not in ATTACHMENT_OWNED_KEYS}
        # A canopy keeps its (unused) lean enclosure in design_inputs.
        enclosure_states = canopies.pop(f"{side}_lean_enclosure", None) if kind == "lean_to" else None
        canopies.pop(f"{side}_type", None)
        canopies.pop(f"{side}_length_mm", None)
        attachment_sections = {key[len(prefix):]: sections.pop(key) for key in list(sections)
                               if key in side_section_keys and key.startswith(prefix)}
        attachments.append({"id": attachment_id, "side": side, "kind": kind,
                            "from_gridline": gridlines[0]["id"], "to_gridline": gridlines[-1]["id"],
                            "width_mm": width, "properties": properties, "sections": attachment_sections})
        if kind == "lean_to":
            inner_key = "right" if side == "left" else "left"
            if isinstance(enclosure_states, Mapping):
                states = enclosure_states
            else:
                outer_yes = str(properties.get("lean_outer_wall_clad", "No")).strip().lower() == "yes"
                states = {side: outer_yes}
            properties.pop("lean_outer_wall_clad", None)  # regenerated from the outer wall
            if states.get(inner_key):
                main_walls[side] = True  # set_lean_wall_states syncs the shared wall
            lean_walls += [{"face": f"{attachment_id}/outer", "clad": bool(states.get(side, False))},
                           {"face": f"{attachment_id}/front", "clad": bool(states.get("front", False))},
                           {"face": f"{attachment_id}/back", "clad": bool(states.get("back", False))}]

    for key in SITE_WIND_KEYS + ("building_length_m",):
        wind.pop(key, None)
    loads.pop("bay_size_mm", None)
    loads.pop("bay_size_m", None)

    return BuildingModel.from_dict({
        "schema": SCHEMA,
        "site": site.to_dict(),
        "main": main.to_dict(),
        "gridlines": gridlines,
        "frame_types": frame_types,
        "attachments": attachments,
        "walls": [{"face": face, "clad": main_walls[face]} for face in MAIN_WALLS] + lean_walls,
        "design_inputs": design,
    })


def _legacy_wall_states(frame_type: Any) -> dict:
    """``MainWindow.wall_states_from_legacy_frame_type``."""
    if frame_type in ("Roof Only", "No Wind"):
        return {"left": False, "right": False, "front": False, "back": False}
    if frame_type == "3 Sided":
        return {"left": True, "right": True, "front": False, "back": True}
    if frame_type == "2 Sided":
        return {"left": True, "right": False, "front": False, "back": True}
    if frame_type == "1 Sided":
        return {"left": True, "right": False, "front": False, "back": False}
    return {"left": True, "right": True, "front": True, "back": True}
