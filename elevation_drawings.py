import math
import os
import sys

from PySide6.QtCore import QLineF, QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPageLayout, QPageSize, QPainterPath, QPdfWriter, QPen, QPainter, QPolygonF

from matplotlib import get_data_path


PAGE_WIDTH = 4200.0
PAGE_HEIGHT = 2970.0
GRID_COLOUR = QColor("#d71920")
_FONT_FAMILY = None
_CSB_LOGO = None


def _drawing_font_family():
    global _FONT_FAMILY
    if _FONT_FAMILY:
        return _FONT_FAMILY
    font_path = os.path.join(get_data_path(), "fonts", "ttf", "DejaVuSans.ttf")
    font_id = QFontDatabase.addApplicationFont(font_path)
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    _FONT_FAMILY = families[0] if families else "sans-serif"
    return _FONT_FAMILY


def _csb_logo():
    global _CSB_LOGO
    if _CSB_LOGO is None:
        base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        _CSB_LOGO = QImage(os.path.join(base_path, "assets", "CSB_logo.jpg"))
    return _CSB_LOGO


def _font(painter, size, bold=False):
    font = QFont(_drawing_font_family())
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def _text(painter, rect, text, size=24, bold=False, alignment=Qt.AlignLeft | Qt.AlignVCenter, colour=None):
    font = _font(painter, size, bold)
    x, y, width, height = rect
    lines = str(text).splitlines() or [""]
    line_height = size * 1.3
    block_height = line_height * len(lines)
    top = y + max((height - block_height) / 2.0, 0.0) if alignment & Qt.AlignVCenter else y
    for index, line in enumerate(lines):
        path = QPainterPath()
        path.addText(0.0, 0.0, font, line)
        bounds = path.boundingRect()
        if alignment & Qt.AlignHCenter:
            draw_x = x + (width - bounds.width()) / 2.0 - bounds.left()
        elif alignment & Qt.AlignRight:
            draw_x = x + width - bounds.width() - bounds.left()
        else:
            draw_x = x - bounds.left()
        baseline = top + index * line_height - bounds.top()
        path.translate(draw_x, baseline)
        painter.fillPath(path, colour or QColor("#202020"))


def _line(painter, x1, y1, x2, y2, width=5, style=Qt.SolidLine, colour=None):
    painter.setPen(QPen(colour or QColor("#202020"), width, style, Qt.SquareCap, Qt.MiterJoin))
    painter.drawLine(QLineF(x1, y1, x2, y2))


def _grid_line(painter, x1, y1, x2, y2, width=2):
    pen = QPen(GRID_COLOUR, width, Qt.CustomDashLine, Qt.SquareCap, Qt.MiterJoin)
    pen.setDashPattern([8.0, 6.0])
    painter.setPen(pen)
    painter.drawLine(QLineF(x1, y1, x2, y2))


def _polyline(painter, points, width=7):
    for a, b in zip(points[:-1], points[1:]):
        _line(painter, a[0], a[1], b[0], b[1], width)


def _dimension(painter, x1, x2, y, extension_y, label):
    _line(painter, x1, extension_y, x1, y + 14, 3)
    _line(painter, x2, extension_y, x2, y + 14, 3)
    _line(painter, x1, y, x2, y, 3)
    tick = 13
    _line(painter, x1 - tick, y + tick, x1 + tick, y - tick, 3)
    _line(painter, x2 - tick, y + tick, x2 + tick, y - tick, 3)
    _text(painter, (x1, y - 55, x2 - x1, 45), label, 23, alignment=Qt.AlignCenter)


def _grid_bubble(painter, x, y, label):
    painter.setPen(QPen(GRID_COLOUR, 4))
    painter.setBrush(Qt.white)
    painter.drawEllipse(QRectF(x - 25, y - 25, 50, 50))
    _text(painter, (x - 25, y - 25, 50, 50), label, 22, bold=True, alignment=Qt.AlignCenter, colour=GRID_COLOUR)


def _vertical_dimension(painter, x, y_bottom, y_top, extension_x, label):
    _line(painter, extension_x, y_bottom, x - 14, y_bottom, 3)
    _line(painter, extension_x, y_top, x - 14, y_top, 3)
    _line(painter, x, y_bottom, x, y_top, 3)
    tick = 13
    _line(painter, x - tick, y_bottom + tick, x + tick, y_bottom - tick, 3)
    _line(painter, x - tick, y_top + tick, x + tick, y_top - tick, 3)
    painter.save()
    painter.translate(x - 20, (y_bottom + y_top) / 2.0)
    painter.rotate(-90)
    _text(painter, (-180, -42, 360, 38), label, 21, alignment=Qt.AlignCenter)
    painter.restore()


def _level_datum(painter, x1, x2, y, label):
    _grid_line(painter, x1, y, x2, y)
    _text(painter, (x2 + 10, y - 24, 120, 36), label, 18, bold=True, colour=GRID_COLOUR)


def _arrow(painter, a, b, width=4, colour=None):
    pen_colour = colour or QColor("#202020")
    painter.setPen(QPen(pen_colour, width, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
    painter.drawLine(QLineF(a[0], a[1], b[0], b[1]))
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    size = 22
    for offset in (-0.55, 0.55):
        point = (b[0] - size * math.cos(angle + offset), b[1] - size * math.sin(angle + offset))
        painter.drawLine(QLineF(b[0], b[1], point[0], point[1]))


def _finish_colour(data, key, fallback):
    finishes = data.get("finishes", {})
    return QColor(str(finishes.get(f"{key}_hex", finishes.get("gutters_trims_hex", fallback))))


def _draw_finishes_schedule(painter, data, x, y):
    rows = [
        ("ROOF CLADDING", "roof"),
        ("WALL CLADDING", "walls"),
        ("BARGE", "barge"),
        ("RIDGE CAPPING", "ridge_capping"),
        ("CORNER TRIM", "corner_trim"),
        ("GUTTER", "gutter"),
    ]
    width, row_height = 920, 62
    _text(painter, (x, y - 48, width, 42), "FINISHES SCHEDULE", 25, bold=True)
    for index, (label, key) in enumerate(rows):
        row_y = y + index * row_height
        painter.setPen(QPen(QColor("#202020"), 3))
        painter.setBrush(Qt.white)
        painter.drawRect(QRectF(x, row_y, width, row_height))
        painter.setBrush(_finish_colour(data, key, "#D9DDDD"))
        painter.drawRect(QRectF(x + 12, row_y + 12, 38, 38))
        _text(painter, (x + 65, row_y + 5, 390, 50), label, 19, bold=True)
        finish_text = data.get("finishes", {}).get(key, "-")
        if key == "roof":
            finish_text = f"{finish_text} / {data.get('finishes', {}).get('roof_profile', '5-Rib (Trimdek)')}"
        elif key == "walls":
            finish_text = f"{finish_text} / {data.get('finishes', {}).get('wall_profile', '5-Rib (Trimdek)')}"
        _text(painter, (x + 470, row_y + 5, 430, 50), finish_text, 20)


def _draw_door_schedule(painter, data, x, y):
    rows = []
    wall_finish = data.get("finishes", {}).get("walls", "-")
    wall_profile = data.get("finishes", {}).get("wall_profile", "-")
    for index, door in enumerate(data.get("sliding_doors", []), start=1):
        rows.append((
            f"SD{index}",
            str(door.get("wall", "-")).replace(" wall", ""),
            f"{float(door.get('width_m', 0.0)) * 1000:.0f}",
            f"{float(door.get('height_m', 0.0)) * 1000:.0f}",
            f"{door.get('door_count', door.get('tracks', 1))} leaf / {door.get('tracks', 1)} track",
            f"{wall_finish} / {wall_profile}",
        ))
    for index, door in enumerate(data.get("pa_doors", []), start=1):
        rows.append((
            f"PA{index}",
            str(door.get("wall", "-")).replace(" wall", ""),
            f"{float(door.get('width_m', 0.0)) * 1000:.0f}",
            f"{float(door.get('height_m', 0.0)) * 1000:.0f}",
            str(door.get("opening", "-")),
            f"{wall_finish} flat",
        ))
    if not rows:
        return
    widths = [95, 120, 115, 115, 220, 330]
    row_height = 48
    headers = ["MARK", "WALL", "WIDTH", "HEIGHT", "TYPE", "FINISH"]
    table_width = sum(widths)
    _text(painter, (x, y - 46, table_width, 40), "DOOR SCHEDULE", 25, bold=True)
    for row_index, row in enumerate([headers] + rows):
        row_y = y + row_index * row_height
        painter.setPen(QPen(QColor("#202020"), 3))
        painter.setBrush(QColor("#f3f3f3") if row_index == 0 else Qt.white)
        painter.drawRect(QRectF(x, row_y, table_width, row_height))
        cell_x = x
        for cell_index, (width, value) in enumerate(zip(widths, row)):
            if cell_index:
                _line(painter, cell_x, row_y, cell_x, row_y + row_height, 2)
            _text(painter, (cell_x + 8, row_y + 4, width - 16, row_height - 8), value, 17, bold=row_index == 0)
            cell_x += width


def _sloped_text(painter, a, b, text, size=22):
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    if math.hypot(dx, dy) <= 1e-6:
        return
    angle = math.degrees(math.atan2(dy, dx))
    if angle > 90.0:
        angle -= 180.0
    elif angle < -90.0:
        angle += 180.0
    mid_x = (a[0] + b[0]) / 2.0
    mid_y = (a[1] + b[1]) / 2.0
    painter.save()
    painter.translate(mid_x, mid_y)
    painter.rotate(angle)
    _text(painter, (-210, -62, 420, 42), text, size, bold=True, alignment=Qt.AlignCenter)
    painter.restore()


def _fit_transform(points, rect, padding=90):
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max(max_x - min_x, 0.1)
    height = max(max_y - min_y, 0.1)
    scale = min((rect[2] - 2 * padding) / width, (rect[3] - 2 * padding) / height)
    ox = rect[0] + (rect[2] - width * scale) / 2 - min_x * scale
    oy = rect[1] + rect[3] - padding + min_y * scale
    return lambda point: (ox + point[0] * scale, oy - point[1] * scale), scale


def _roof_points(data):
    nodes = data.get("nodes", [])
    indices = data.get("roof_indices", [])
    return [(float(nodes[i]["x"]), float(nodes[i]["y"])) for i in indices if 0 <= i < len(nodes)]


def _indexed_points(data, key):
    nodes = data.get("nodes", [])
    return [(float(nodes[i]["x"]), float(nodes[i]["y"])) for i in data.get(key, []) if 0 <= i < len(nodes)]


def _pitch_label_segments(data):
    roof = _roof_points(data)
    main = _indexed_points(data, "main_top_indices")
    if len(roof) < 2 or len(main) < 2:
        return []
    main_min_x = min(point[0] for point in main)
    main_max_x = max(point[0] for point in main)
    segments = []
    left = [point for point in roof if point[0] <= main_min_x + 1e-6]
    if len(left) >= 2 and left[-1][0] - left[0][0] > 0.05:
        segments.append((left[0], left[-1]))
    apex_index = max(range(len(main)), key=lambda index: main[index][1])
    if apex_index > 0:
        segments.append((main[0], main[apex_index]))
    elif len(main) >= 2:
        segments.append((main[0], main[-1]))
    right = [point for point in roof if point[0] >= main_max_x - 1e-6]
    if len(right) >= 2 and right[-1][0] - right[0][0] > 0.05:
        segments.append((right[0], right[-1]))
    return segments


def _elevation_titles(data):
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    names = {
        "N": "NORTH", "NE": "NORTHEAST", "E": "EAST", "SE": "SOUTHEAST",
        "S": "SOUTH", "SW": "SOUTHWEST", "W": "WEST", "NW": "NORTHWEST",
    }
    north_arrow = str(data.get("north_direction_in_section", "Any"))
    if north_arrow not in directions:
        return {
            "left": "LEFT ELEVATION",
            "right": "RIGHT ELEVATION",
            "front": "FRONT ELEVATION",
            "back": "REAR ELEVATION",
        }
    north_index = directions.index(north_arrow)
    plan_screen_directions = {"back": 0, "right": 2, "front": 4, "left": 6}
    return {
        face: f"{names[directions[(screen_index - north_index) % 8]]} ELEVATION"
        for face, screen_index in plan_screen_directions.items()
    }


def _roof_planes(data):
    roof = _roof_points(data)
    main = _indexed_points(data, "main_top_indices")
    if len(roof) < 2 or len(main) < 2:
        return []
    main_min_x = min(point[0] for point in main)
    main_max_x = max(point[0] for point in main)
    apex_index = max(range(len(main)), key=lambda index: main[index][1])
    planes = []
    if roof[0][0] < main_min_x - 0.05:
        planes.append((roof[0], main[0]))
    if apex_index > 0:
        planes.append((main[0], main[apex_index]))
    if apex_index < len(main) - 1:
        planes.append((main[apex_index], main[-1]))
    if roof[-1][0] > main_max_x + 0.05:
        planes.append((main[-1], roof[-1]))
    return planes


def _roof_cladding_edges(data, roof):
    inner_offset = max(float(data.get("roof_cladding_offset_m", 0.12) or 0.12), 0.0)
    thickness = max(float(data.get("roof_cladding_depth_m", 0.03) or 0.03), 0.001)
    outer_offset = inner_offset + thickness
    overhang = max(float(data.get("roof_edge_overhang_m", 0.25) or 0.25), 0.0)
    edges = []
    for index, (a, b) in enumerate(zip(roof[:-1], roof[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        if ny < 0.0:
            nx, ny = -nx, -ny
        start_extension = -overhang if index == 0 else 0.0
        end_extension = overhang if index == len(roof) - 2 else 0.0
        base_a = (a[0] + nx * inner_offset + start_extension, a[1] + ny * inner_offset)
        base_b = (b[0] + nx * inner_offset + end_extension, b[1] + ny * inner_offset)
        top_a = (a[0] + nx * outer_offset + start_extension, a[1] + ny * outer_offset)
        top_b = (b[0] + nx * outer_offset + end_extension, b[1] + ny * outer_offset)
        edges.append((base_a, base_b, top_a, top_b))
    return edges


def _draw_scaled_member(painter, transform, start, end, profile):
    depth = max(float(profile.get("depth_mm") or 0.0) / 1000.0, 0.001)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return
    nx, ny = -dy / length * depth / 2.0, dx / length * depth / 2.0
    polygon = [
        transform((start[0] + nx, start[1] + ny)),
        transform((end[0] + nx, end[1] + ny)),
        transform((end[0] - nx, end[1] - ny)),
        transform((start[0] - nx, start[1] - ny)),
    ]
    painter.setPen(QPen(QColor("#202020"), 2, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
    painter.setBrush(QColor("#303030"))
    painter.drawPolygon(QPolygonF([QPointF(*point) for point in polygon]))


def _draw_gutter_end(painter, point, outward, colour, scale, sectioned=False):
    x, y = point
    width = max(0.20 * scale, 7.0)
    depth = max(0.15 * scale, 5.0)
    if sectioned:
        profile = [
            QPointF(x, y + 1),
            QPointF(x, y + depth),
            QPointF(x + outward * width * 0.78, y + depth),
            QPointF(x + outward * width, y + depth * 0.18),
        ]
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#202020"), 6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPolyline(QPolygonF(profile))
        painter.setPen(QPen(colour, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPolyline(QPolygonF(profile))
        return
    end_cap = [
        QPointF(x, y),
        QPointF(x + outward * width, y + depth * 0.12),
        QPointF(x + outward * width * 0.82, y + depth),
        QPointF(x, y + depth),
    ]
    painter.setPen(QPen(QColor("#202020"), 2))
    painter.setBrush(colour)
    painter.drawPolygon(QPolygonF(end_cap))


def _trim_line(painter, a, b, colour, outer_width=10, inner_width=6):
    _line(painter, a[0], a[1], b[0], b[1], outer_width)
    painter.setPen(QPen(colour, inner_width, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
    painter.drawLine(QLineF(a[0], a[1], b[0], b[1]))


def _grey_dash_line(painter, a, b, width=3):
    pen = QPen(QColor("#888888"), width, Qt.CustomDashLine, Qt.SquareCap, Qt.MiterJoin)
    pen.setDashPattern([8.0, 6.0])
    painter.setPen(pen)
    painter.drawLine(QLineF(a[0], a[1], b[0], b[1]))


def _width_dimension_parts(data, roof, main):
    if len(roof) < 2 or len(main) < 2:
        return [], max(roof[-1][0] - roof[0][0], 0.0) if len(roof) >= 2 else 0.0
    left_width = max(main[0][0] - roof[0][0], 0.0)
    main_span = float(data.get("main_span_m") or (main[-1][0] - main[0][0]))
    right_width = max(roof[-1][0] - main[-1][0], 0.0)
    physical_points = [roof[0][0], main[0][0], main[-1][0], roof[-1][0]]
    labels = [
        f"{left_width * 1000:.0f} {str(data.get('left_addition_type', 'ADDITION')).upper()}",
        f"{main_span * 1000:.0f} SPAN",
        f"{right_width * 1000:.0f} {str(data.get('right_addition_type', 'ADDITION')).upper()}",
    ]
    parts = [
        (x1, x2, label)
        for x1, x2, label in zip(physical_points[:-1], physical_points[1:], labels)
        if abs(x2 - x1) > 0.001
    ]
    return parts, left_width + main_span + right_width


def _column_x_positions(data):
    nodes = data.get("nodes", [])
    positions = []
    for member in data.get("frame_lines", []):
        group = str(member.get("group", "")).upper()
        if "COLUMN" not in group or "CANOPY_POST" in group:
            continue
        for key in ("a", "b"):
            index = int(member.get(key, -1))
            if 0 <= index < len(nodes) and abs(float(nodes[index]["y"])) < 1e-6:
                positions.append(float(nodes[index]["x"]))
    return sorted({round(value, 6) for value in positions})


def _column_specs(data):
    nodes = data.get("nodes", [])
    specs = {}
    for member in data.get("frame_lines", []):
        group = str(member.get("group", "")).upper()
        if "COLUMN" not in group or "CANOPY_POST" in group:
            continue
        for key in ("a", "b"):
            index = int(member.get(key, -1))
            if 0 <= index < len(nodes) and abs(float(nodes[index]["y"])) < 1e-6:
                specs[round(float(nodes[index]["x"]), 6)] = dict(member.get("profile", {}))
    return sorted(specs.items())


def _column_profile_at_x(data, target_x):
    specs = _column_specs(data)
    if not specs:
        return {}
    return min(specs, key=lambda item: abs(item[0] - target_x))[1]


def _draw_elevation_column(painter, x, y_top, y_bottom, profile, scale, view="end", hidden=False):
    dimension_key = "flange_width_mm" if view == "side" else "depth_mm"
    width = max(float(profile.get(dimension_key) or profile.get("depth_mm") or 150.0) / 1000.0 * scale, 1.0)
    rect = QRectF(x - width / 2.0, min(y_top, y_bottom), width, abs(y_bottom - y_top))
    if hidden:
        pen = QPen(QColor("#202020"), 2, Qt.CustomDashLine)
        pen.setDashPattern([7.0, 5.0])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
    else:
        painter.setPen(QPen(QColor("#202020"), 2))
        painter.setBrush(QColor("#303030"))
    painter.drawRect(rect)


def _draw_purlin_section(painter, transform, item):
    profile = item.get("profile", {})
    depth = max(float(profile.get("depth_mm") or 150.0) / 1000.0, 0.05)
    flange = max(float(profile.get("flange_width_mm") or 60.0) / 1000.0, 0.03)
    lip = max(float(profile.get("lip_mm") or 20.0) / 1000.0, 0.01)
    nx = float(item.get("offset_x", 0.0))
    ny = float(item.get("offset_y", 1.0))
    normal_length = math.hypot(nx, ny) or 1.0
    nx, ny = nx / normal_length, ny / normal_length
    tx, ty = ny, -nx
    cx = float(item.get("x", 0.0)) + float(item.get("offset_x", 0.0))
    cy = float(item.get("y", 0.0)) + float(item.get("offset_y", 0.0))

    def point(normal_offset, tangent_offset):
        return transform((cx + nx * normal_offset + tx * tangent_offset, cy + ny * normal_offset + ty * tangent_offset))

    web_bottom = point(-depth / 2.0, 0.0)
    web_top = point(depth / 2.0, 0.0)
    top_flange = point(depth / 2.0, flange)
    bottom_flange = point(-depth / 2.0, flange)
    top_lip = point(depth / 2.0 - lip, flange)
    bottom_lip = point(-depth / 2.0 + lip, flange)
    for a, b in ((web_bottom, web_top), (web_top, top_flange), (web_bottom, bottom_flange), (top_flange, top_lip), (bottom_flange, bottom_lip)):
        _line(painter, a[0], a[1], b[0], b[1], 3)


def _roof_only_zones(data, roof, main):
    if len(roof) < 2 or len(main) < 2:
        return []
    zones = []
    if roof[0][0] < main[0][0] - 0.05:
        addition_type = str(data.get("left_addition_type", "")).lower()
        states = data.get("left_lean_states", {})
        if "canopy" in addition_type or not states.get("left"):
            zones.append((roof[0][0], main[0][0]))
    if roof[-1][0] > main[-1][0] + 0.05:
        addition_type = str(data.get("right_addition_type", "")).lower()
        states = data.get("right_lean_states", {})
        if "canopy" in addition_type or not states.get("right"):
            zones.append((main[-1][0], roof[-1][0]))
    return zones


def _draw_column_section(painter, centre, profile, scale):
    text = " ".join(str(profile.get(key, "")) for key in ("name", "group_name", "shape_type")).upper()
    actual_depth = float(profile.get("depth_mm") or 150.0) / 1000.0 * scale
    actual_width = float(profile.get("flange_width_mm") or profile.get("depth_mm") or 100.0) / 1000.0 * scale
    depth = max(actual_depth, 1.0)
    width = max(actual_width, 1.0)
    flange_t = min(max(float(profile.get("flange_thickness_mm") or 8.0) / 1000.0 * scale, 3.0), depth / 2.0)
    web_t = min(max(float(profile.get("web_thickness_mm") or 6.0) / 1000.0 * scale, 3.0), width)
    x, y = centre
    painter.setPen(QPen(QColor("#202020"), 2))
    painter.setBrush(QColor("#202020"))
    if "CHS" in text:
        painter.setBrush(Qt.white)
        painter.drawEllipse(QRectF(x - width / 2, y - width / 2, width, width))
    elif any(token in text for token in ("SHS", "RHS")):
        painter.setBrush(Qt.white)
        painter.drawRect(QRectF(x - width / 2, y - depth / 2, width, depth))
    elif "PFC" in text or "CHANNEL" in text:
        painter.drawRect(QRectF(x - width / 2, y - depth / 2, web_t, depth))
        painter.drawRect(QRectF(x - width / 2, y - depth / 2, width, flange_t))
        painter.drawRect(QRectF(x - width / 2, y + depth / 2 - flange_t, width, flange_t))
    else:
        painter.drawRect(QRectF(x - width / 2, y - depth / 2, width, flange_t))
        painter.drawRect(QRectF(x - width / 2, y + depth / 2 - flange_t, width, flange_t))
        painter.drawRect(QRectF(x - web_t / 2, y - depth / 2, web_t, depth))


def _plan_transform(data, rect):
    roof = _roof_points(data)
    length = float(data.get("length_m", 0.0))
    if len(roof) < 2 or length <= 0.0:
        raise ValueError("The solved model does not contain usable plan geometry.")
    points = [(0.0, roof[0][0]), (0.0, roof[-1][0]), (length, roof[0][0]), (length, roof[-1][0])]
    return _fit_transform(points, rect, padding=150)[0]


def _draw_plan_grids(painter, data, transform, transverse_points, numeric_bubble_y=None, transverse_bubble_x=None):
    length = float(data.get("length_m", 0.0))
    z_positions = [float(value) for value in data.get("z_positions", [])]
    roof = _roof_points(data)
    x_min, x_max = roof[0][0], roof[-1][0]
    plan_corners = [transform((z, x)) for z in (0.0, length) for x in (x_min, x_max)]
    screen_left = min(point[0] for point in plan_corners)
    screen_right = max(point[0] for point in plan_corners)
    screen_top = min(point[1] for point in plan_corners)
    screen_bottom = max(point[1] for point in plan_corners)
    numeric_bubble_y = screen_bottom + 82 if numeric_bubble_y is None else numeric_bubble_y
    transverse_bubble_x = screen_right + 82 if transverse_bubble_x is None else transverse_bubble_x
    for index, z in enumerate(z_positions):
        x = transform((z, x_min))[0]
        _grid_line(painter, x, screen_top - 55, x, numeric_bubble_y - 25)
        _grid_bubble(painter, x, numeric_bubble_y, str(index + 1))
    for x, label in transverse_points:
        y = transform((0.0, x))[1]
        _grid_line(painter, screen_left - 55, y, transverse_bubble_x - 25, y)
        _grid_bubble(painter, transverse_bubble_x, y, label)


def _door_plan_geometry(data, door):
    main = _indexed_points(data, "main_top_indices")
    z_positions = [float(value) for value in data.get("z_positions", [])]
    if len(main) < 2 or len(z_positions) < 2:
        return None
    main_left, main_right = main[0][0], main[-1][0]
    z_front, z_back = z_positions[0], z_positions[-1]
    wall = str(door.get("wall", "Left wall"))
    width = max(float(door.get("width_m", 0.9)), 0.1)
    position = max(float(door.get("position_m", 0.0)), 0.0)
    if wall in {"Left wall", "Right wall"}:
        span = z_back - z_front
        start = z_front + min(position, max(span - width, 0.0))
        end = start + min(width, span)
        x = main_left if wall == "Left wall" else main_right
        outward = (0.0, -1.0 if wall == "Left wall" else 1.0)
        return {"wall": wall, "start": (start, x), "end": (end, x), "outward": outward, "along": (1.0, 0.0)}
    span = main_right - main_left
    start_x = main_left + min(position, max(span - width, 0.0))
    end_x = start_x + min(width, span)
    z = z_front if wall == "Front wall" else z_back
    outward = (-1.0 if wall == "Front wall" else 1.0, 0.0)
    return {"wall": wall, "start": (z, start_x), "end": (z, end_x), "outward": outward, "along": (0.0, 1.0)}


def _draw_sliding_door_plan_symbols(painter, data, transform):
    colour = QColor("#1260a0")
    for mark_index, door in enumerate(data.get("sliding_doors", []), start=1):
        geom = _door_plan_geometry(data, door)
        if not geom:
            continue
        start, end = geom["start"], geom["end"]
        p1, p2 = transform(start), transform(end)
        _line(painter, p1[0], p1[1], p2[0], p2[1], 8, colour=colour)
        opening = str(door.get("opening", "Slide left"))
        along = geom["along"]
        outward = geom["outward"]
        arrow_offset = 0.42
        door_count = max(1, min(int(door.get("door_count", door.get("tracks", 1))), 12))
        span = (end[0] - start[0], end[1] - start[1])
        for leaf in range(door_count):
            low = leaf / door_count
            high = (leaf + 1) / door_count
            centre = ((low + high) / 2.0)
            leaf_centre = (
                start[0] + span[0] * centre + outward[0] * arrow_offset,
                start[1] + span[1] * centre + outward[1] * arrow_offset,
            )
            if opening == "Bi-parting":
                direction = -1.0 if leaf < door_count / 2.0 else 1.0
            else:
                direction = -1.0 if opening == "Slide left" else 1.0
            half_arrow = max(math.hypot(span[0], span[1]) / door_count * 0.28, 0.20)
            tail = (leaf_centre[0] - along[0] * direction * half_arrow, leaf_centre[1] - along[1] * direction * half_arrow)
            head = (leaf_centre[0] + along[0] * direction * half_arrow, leaf_centre[1] + along[1] * direction * half_arrow)
            _arrow(painter, transform(tail), transform(head), width=5, colour=colour)
        label_point = (
            (start[0] + end[0]) / 2.0 + outward[0] * (arrow_offset + 0.32),
            (start[1] + end[1]) / 2.0 + outward[1] * (arrow_offset + 0.32),
        )
        lp = transform(label_point)
        _text(painter, (lp[0] - 42, lp[1] - 22, 84, 34), f"SD{mark_index}", 20, bold=True, alignment=Qt.AlignCenter, colour=colour)


def _draw_pa_door_plan_symbols(painter, data, transform):
    colour = QColor("#1260a0")
    for mark_index, door in enumerate(data.get("pa_doors", []), start=1):
        geom = _door_plan_geometry(data, door)
        if not geom:
            continue
        start, end = geom["start"], geom["end"]
        p1, p2 = transform(start), transform(end)
        _line(painter, p1[0], p1[1], p2[0], p2[1], 7, colour=colour)
        opening = str(door.get("opening", "Open left"))
        hinge = start if opening == "Open left" else end
        leaf_end = end if opening == "Open left" else start
        outward = geom["outward"]
        swing_end = (hinge[0] - outward[0] * max(float(door.get("width_m", 0.9)), 0.1), hinge[1] - outward[1] * max(float(door.get("width_m", 0.9)), 0.1))
        hp, ep, sp = transform(hinge), transform(leaf_end), transform(swing_end)
        _line(painter, hp[0], hp[1], sp[0], sp[1], 4, colour=colour)
        path = QPainterPath()
        path.moveTo(ep[0], ep[1])
        path.quadTo(hp[0], hp[1], sp[0], sp[1])
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(colour, 3, Qt.DashLine, Qt.SquareCap, Qt.MiterJoin))
        painter.drawPath(path)
        label_point = (
            (start[0] + end[0]) / 2.0 - outward[0] * 0.36,
            (start[1] + end[1]) / 2.0 - outward[1] * 0.36,
        )
        lp = transform(label_point)
        _text(painter, (lp[0] - 42, lp[1] - 22, 84, 34), f"PA{mark_index}", 20, bold=True, alignment=Qt.AlignCenter, colour=colour)


def _draw_floor_plan(painter, data):
    transform = _plan_transform(data, (220, 350, 3500, 1700))
    roof = _roof_points(data)
    main = _indexed_points(data, "main_top_indices")
    length = float(data.get("length_m", 0.0))
    z_positions = [float(value) for value in data.get("z_positions", [])]
    plan_outline = [
        transform((0.0, roof[0][0])), transform((length, roof[0][0])),
        transform((length, roof[-1][0])), transform((0.0, roof[-1][0])),
    ]
    end_girt_offset = 0.18
    for x1, x2 in _roof_only_zones(data, roof, main):
        roof_over = [
            transform((-end_girt_offset, x1)), transform((length + end_girt_offset, x1)),
            transform((length + end_girt_offset, x2)), transform((-end_girt_offset, x2)),
        ]
        for a, b in zip(roof_over, roof_over[1:] + roof_over[:1]):
            _grey_dash_line(painter, a, b)
        left = min(point[0] for point in roof_over)
        right = max(point[0] for point in roof_over)
        top = min(point[1] for point in roof_over)
        bottom = max(point[1] for point in roof_over)
        _text(painter, (left + 80, (top + bottom) / 2.0 - 24, right - left - 160, 48), "OUTLINE OF ROOF OVER", 20, bold=True, alignment=Qt.AlignCenter, colour=QColor("#888888"))

    wall_states = data.get("wall_states", {})
    main_min_x, main_max_x = main[0][0], main[-1][0]
    wall_segments = []
    if wall_states.get("left"):
        wall_segments.append(((0.0, main_min_x), (length, main_min_x), (0.0, -1.0)))
    if wall_states.get("right"):
        wall_segments.append(((0.0, main_max_x), (length, main_max_x), (0.0, 1.0)))
    if wall_states.get("front"):
        wall_segments.append(((0.0, main_min_x), (0.0, main_max_x), (-1.0, 0.0)))
    if wall_states.get("back"):
        wall_segments.append(((length, main_min_x), (length, main_max_x), (1.0, 0.0)))
    for key, roof_key in (("left", "left_lean_roof_indices"), ("right", "right_lean_roof_indices")):
        addition = _indexed_points(data, roof_key)
        if not addition:
            continue
        states = data.get(f"{key}_lean_states", {})
        outer_x = addition[0][0] if key == "left" else addition[-1][0]
        join_x = main_min_x if key == "left" else main_max_x
        if states.get(key):
            wall_segments.append(((0.0, outer_x), (length, outer_x), (0.0, -1.0 if key == "left" else 1.0)))
        if states.get("front"):
            wall_segments.append(((0.0, outer_x), (0.0, join_x), (-1.0, 0.0)))
        if states.get("back"):
            wall_segments.append(((length, outer_x), (length, join_x), (1.0, 0.0)))
    # Girts sit immediately outside the column faces; the cladding outline is
    # shown farther out again.
    for a, b, normal in wall_segments:
        girt_offset = 0.18
        clad_offset = 0.27
        girt_a = (a[0] + normal[0] * girt_offset, a[1] + normal[1] * girt_offset)
        girt_b = (b[0] + normal[0] * girt_offset, b[1] + normal[1] * girt_offset)
        clad_a = (a[0] + normal[0] * clad_offset, a[1] + normal[1] * clad_offset)
        clad_b = (b[0] + normal[0] * clad_offset, b[1] + normal[1] * clad_offset)
        pga, pgb = transform(girt_a), transform(girt_b)
        pca, pcb = transform(clad_a), transform(clad_b)
        _line(painter, pca[0], pca[1], pcb[0], pcb[1], 4)
        _line(painter, pga[0], pga[1], pgb[0], pgb[1], 7)
        # Short legs make the continuous girt read as a channel in plan.
        for endpoint in (pga, pgb):
            _line(painter, endpoint[0] - 5, endpoint[1] - 5, endpoint[0] + 5, endpoint[1] + 5, 3)

    scale = abs(transform((1.0, 0.0))[0] - transform((0.0, 0.0))[0])
    for z in z_positions:
        for x, profile in _column_specs(data):
            px, py = transform((z, x))
            _draw_column_section(painter, (px, py), profile, scale)
    _draw_sliding_door_plan_symbols(painter, data, transform)
    _draw_pa_door_plan_symbols(painter, data, transform)
    left, right = transform((0.0, roof[0][0]))[0], transform((length, roof[0][0]))[0]
    base_y = max(transform((0.0, roof[0][0]))[1], transform((0.0, roof[-1][0]))[1])
    _dimension(painter, min(left, right), max(left, right), base_y + 160, base_y + 25, f"{length * 1000:.0f} OVERALL")
    width_parts, overall_width = _width_dimension_parts(data, roof, main)
    plan_right = max(point[0] for point in plan_outline)
    for x1, x2, label in width_parts:
        y1 = transform((length, x1))[1]
        y2 = transform((length, x2))[1]
        _vertical_dimension(painter, plan_right + 95, max(y1, y2), min(y1, y2), plan_right + 20, label)
    if overall_width > 0.0:
        y1 = transform((length, roof[0][0]))[1]
        y2 = transform((length, roof[-1][0]))[1]
        _vertical_dimension(painter, plan_right + 175, max(y1, y2), min(y1, y2), plan_right + 20, f"{overall_width * 1000:.0f} OVERALL")
    _draw_plan_grids(
        painter,
        data,
        transform,
        [(main_min_x, "A"), (main_max_x, "C")],
        numeric_bubble_y=base_y + 245,
        transverse_bubble_x=plan_right + 250,
    )


def _draw_sliding_door_elevation_symbol(painter, door, p1, p2, mark):
    colour = QColor("#1260a0")
    rect = QRectF(p1[0], p2[1], p2[0] - p1[0], p1[1] - p2[1]).normalized()
    painter.setPen(QPen(colour, 5))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(rect)
    opening = str(door.get("opening", "Slide left"))
    door_count = max(1, min(int(door.get("door_count", door.get("tracks", 1))), 12))
    arrow_y = rect.top() - 34
    leaf_width = rect.width() / door_count
    for leaf in range(door_count):
        centre_x = rect.left() + leaf_width * (leaf + 0.5)
        if opening == "Bi-parting":
            direction = -1.0 if leaf < door_count / 2.0 else 1.0
        else:
            direction = -1.0 if opening == "Slide left" else 1.0
        half_arrow = max(min(leaf_width * 0.35, 70.0), 28.0)
        _arrow(painter, (centre_x - direction * half_arrow, arrow_y), (centre_x + direction * half_arrow, arrow_y), width=5, colour=colour)
    _text(painter, (rect.center().x() - 42, arrow_y - 54, 84, 34), mark, 20, bold=True, alignment=Qt.AlignCenter, colour=colour)


def _draw_pa_door_elevation_symbol(painter, door, p1, p2, mark):
    colour = QColor("#1260a0")
    rect = QRectF(p1[0], p2[1], p2[0] - p1[0], p1[1] - p2[1]).normalized()
    painter.setPen(QPen(colour, 5))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(rect)
    hinge_x = rect.left() if str(door.get("opening", "Open left")) == "Open left" else rect.right()
    latch_x = rect.right() if hinge_x == rect.left() else rect.left()
    _line(painter, hinge_x, rect.bottom(), latch_x, rect.top(), 2, style=Qt.DashLine, colour=colour)
    _text(painter, (rect.center().x() - 42, rect.top() - 54, 84, 34), mark, 20, bold=True, alignment=Qt.AlignCenter, colour=colour)


def _draw_roof_plan(painter, data):
    transform = _plan_transform(data, (220, 350, 3500, 1700))
    roof = _roof_points(data)
    main = _indexed_points(data, "main_top_indices")
    length = float(data.get("length_m", 0.0))
    planes = _roof_planes(data)
    roof_colour = _finish_colour(data, "roof", "#e5e8e8")
    annotation_colour = Qt.white if roof_colour.lightness() < 125 else QColor("#202020")
    for index, (a, b) in enumerate(planes):
        polygon = [transform((0.0, a[0])), transform((length, a[0])), transform((length, b[0])), transform((0.0, b[0]))]
        painter.setPen(QPen(QColor("#202020"), 3))
        painter.setBrush(roof_colour)
        painter.drawPolygon(QPolygonF([QPointF(*point) for point in polygon]))
    outline = [transform((0.0, roof[0][0])), transform((length, roof[0][0])), transform((length, roof[-1][0])), transform((0.0, roof[-1][0]))]
    _polyline(painter, [*outline, outline[0]], 7)
    sheet_spacing = max(length / 42.0, 0.5)
    z = 0.0
    while z <= length + 1e-9:
        a, b = transform((z, roof[0][0])), transform((z, roof[-1][0]))
        _line(painter, a[0], a[1], b[0], b[1], 2)
        z += sheet_spacing
    trim_colour = _finish_colour(data, "ridge_capping", "#202020")
    for eave_x in (roof[0][0], roof[-1][0]):
        _trim_line(painter, transform((0.0, eave_x)), transform((length, eave_x)), trim_colour, 12, 7)
    for end_z in (0.0, length):
        _trim_line(painter, transform((end_z, roof[0][0])), transform((end_z, roof[-1][0])), trim_colour, 10, 6)
    if main:
        ridge = max(main, key=lambda point: point[1])
        _trim_line(painter, transform((0.0, ridge[0])), transform((length, ridge[0])), trim_colour, 14, 9)
    plan_z = length * 0.52
    for a, b in planes:
        high, low = (a, b) if a[1] >= b[1] else (b, a)
        start = transform((plan_z, high[0]))
        end = transform((plan_z, low[0]))
        inset_start = ((start[0] * 2 + end[0]) / 3, (start[1] * 2 + end[1]) / 3)
        inset_end = ((start[0] + end[0] * 2) / 3, (start[1] + end[1] * 2) / 3)
        _arrow(painter, inset_start, inset_end, colour=annotation_colour)
        pitch = math.degrees(math.atan2(abs(b[1] - a[1]), max(abs(b[0] - a[0]), 1e-9)))
        label_rect = (min(start[0], end[0]) - 40, (start[1] + end[1]) / 2 - 55, abs(end[0] - start[0]) + 80, 40)
        painter.fillRect(QRectF(*label_rect), QColor(255, 255, 255, 210))
        _text(painter, label_rect, f"{pitch:.1f} DEG PITCH", 21, bold=True, alignment=Qt.AlignCenter)
    _draw_plan_grids(painter, data, transform, [(main[0][0], "A"), (main[-1][0], "C")])


def _draw_section(painter, data):
    nodes = data.get("nodes", [])
    roof = _roof_points(data)
    main = _indexed_points(data, "main_top_indices")
    ground = 0.0
    outline = [(roof[0][0], ground), *roof, (roof[-1][0], ground)]
    transform, scale = _fit_transform(outline, (250, 300, 3450, 1750), padding=170)
    eave_height = min(main[0][1], main[-1][1]) if len(main) >= 2 else min(roof[0][1], roof[-1][1])
    apex_height = max((point[1] for point in main), default=max(point[1] for point in roof))
    drawing_left = min(transform((roof[0][0], ground))[0], transform((roof[-1][0], ground))[0])
    drawing_right = max(transform((roof[0][0], ground))[0], transform((roof[-1][0], ground))[0])
    ground_y = transform((roof[0][0], ground))[1]
    eave_y = transform((roof[0][0], eave_height))[1]
    apex_y = transform((roof[0][0], apex_height))[1]
    datum_start = drawing_left - 120
    datum_end = drawing_right + 20
    _level_datum(painter, datum_start, datum_end, ground_y, "GROUND")
    _level_datum(painter, datum_start, datum_end, eave_y, "EAVE")
    _level_datum(painter, datum_start, datum_end, apex_y, "APEX")
    _vertical_dimension(painter, drawing_left - 70, ground_y, eave_y, drawing_left, f"EAVE HEIGHT {eave_height * 1000:.0f}")
    _vertical_dimension(painter, drawing_left - 135, ground_y, apex_y, drawing_left, f"APEX HEIGHT {apex_height * 1000:.0f}")
    roof_edges = _roof_cladding_edges(data, roof)
    roof_colour = _finish_colour(data, "roof", "#e5e8e8")
    for base_a, base_b, top_a, top_b in roof_edges:
        polygon = [transform(base_a), transform(base_b), transform(top_b), transform(top_a)]
        painter.setPen(QPen(QColor("#666666"), 2))
        painter.setBrush(roof_colour)
        painter.drawPolygon(QPolygonF([QPointF(*point) for point in polygon]))
    for purlin in data.get("roof_purlins", []):
        _draw_purlin_section(painter, transform, purlin)
    for member in data.get("frame_lines", []):
        a_index, b_index = int(member.get("a", -1)), int(member.get("b", -1))
        if not (0 <= a_index < len(nodes) and 0 <= b_index < len(nodes)):
            continue
        a = (float(nodes[a_index]["x"]), float(nodes[a_index]["y"]))
        b = (float(nodes[b_index]["x"]), float(nodes[b_index]["y"]))
        _draw_scaled_member(painter, transform, a, b, member.get("profile", {}))
    trim_colour = _finish_colour(data, "gutter", "#202020")
    if roof_edges:
        left_eave = transform(roof_edges[0][2])
        right_eave = transform(roof_edges[-1][3])
        _draw_gutter_end(painter, left_eave, -1 if left_eave[0] < right_eave[0] else 1, trim_colour, scale, sectioned=True)
        _draw_gutter_end(painter, right_eave, 1 if left_eave[0] < right_eave[0] else -1, trim_colour, scale, sectioned=True)
    section_bubble_y = apex_y - 72
    for point, label in zip((main[0], main[-1]), ("A", "C")):
        x, _ = transform(point)
        _grid_line(painter, x, section_bubble_y + 25, x, ground_y)
        _grid_bubble(painter, x, section_bubble_y, label)
    clearance_groups = [
        ("BOTTOM", main[0][0], main[-1][0], "MAIN CLEARANCE"),
        ("LEFT_CANOPY_BOTTOM", roof[0][0], main[0][0], "LEAN-TO CLEARANCE"),
        ("RIGHT_CANOPY_BOTTOM", main[-1][0], roof[-1][0], "CANOPY CLEARANCE"),
    ]
    for group_name, x1, x2, label in clearance_groups:
        members = [member for member in data.get("frame_lines", []) if str(member.get("group", "")).upper() == group_name]
        measured_points = []
        for member in members:
            for key in ("a", "b"):
                index = int(member.get(key, -1))
                if 0 <= index < len(nodes):
                    measured_points.append((float(nodes[index]["x"]), float(nodes[index]["y"])))
        if not measured_points or abs(x2 - x1) < 0.05:
            continue
        clearance = min(point[1] for point in measured_points)
        lowest = [point for point in measured_points if abs(point[1] - clearance) < 1e-6]
        measured_x = min(lowest, key=lambda point: abs(point[0] - (x1 + x2) / 2.0))[0]
        extension_x = transform((measured_x, ground))[0]
        inward = 1.0 if measured_x <= (x1 + x2) / 2.0 else -1.0
        dimension_x = extension_x + inward * 65
        y_clear = transform((measured_x, clearance))[1]
        _vertical_dimension(painter, dimension_x, ground_y, y_clear, extension_x, f"{label} {clearance * 1000:.0f}")
    width_parts, overall_width = _width_dimension_parts(data, roof, main)
    for x1, x2, label in width_parts:
        sx1 = transform((x1, ground))[0]
        sx2 = transform((x2, ground))[0]
        _dimension(painter, min(sx1, sx2), max(sx1, sx2), ground_y + 145, ground_y + 18, label)
    if overall_width > 0.0:
        _dimension(painter, drawing_left, drawing_right, ground_y + 220, ground_y + 18, f"{overall_width * 1000:.0f} OVERALL")
    for a, b in _pitch_label_segments(data):
        pitch = math.degrees(math.atan2(abs(b[1] - a[1]), max(abs(b[0] - a[0]), 1e-9)))
        _sloped_text(painter, transform(a), transform(b), f"{pitch:.1f} DEG PITCH")


def _draw_end_elevation(painter, data, rect, title, face):
    roof = _roof_points(data)
    if len(roof) < 2:
        raise ValueError("The solved model does not contain a usable roof outline.")
    ground = min(0.0, min(point[1] for point in roof))
    outline = [(roof[0][0], ground), *roof, (roof[-1][0], ground)]
    reverse = face == "back"
    view_point = (lambda point: (-point[0], point[1])) if reverse else (lambda point: point)
    base_transform, scale = _fit_transform([view_point(point) for point in outline], rect, padding=120)
    transform = lambda point: base_transform(view_point(point))
    screen = [transform(point) for point in outline]

    clad_surfaces = [
        surface for surface in data.get("end_wall_surfaces", [])
        if str(surface.get("label", "")).lower().endswith(face)
    ]
    for surface in clad_surfaces:
        polygon = [transform((float(point["x"]), float(point["y"]))) for point in surface.get("points", [])]
        if len(polygon) < 3:
            continue
        painter.setPen(QPen(QColor("#202020"), 1))
        painter.setBrush(_finish_colour(data, "walls", "#eeeeec"))
        painter.drawPolygon(QPolygonF([QPointF(*point) for point in polygon]))
    barge_colour = _finish_colour(data, "barge", "#202020")
    gutter_colour = _finish_colour(data, "gutter", "#202020")
    roof_edges = _roof_cladding_edges(data, roof)
    roof_colour = _finish_colour(data, "roof", "#e5e8e8")
    clad_ranges = []
    for surface in clad_surfaces:
        points = surface.get("points", [])
        if len(points) >= 3:
            x_values = [float(point["x"]) for point in points]
            clad_ranges.append((min(x_values), max(x_values)))
    for base_a, base_b, top_a, top_b in roof_edges:
        midpoint_x = (base_a[0] + base_b[0]) / 2.0
        has_clad_below = any(x1 - 0.05 <= midpoint_x <= x2 + 0.05 for x1, x2 in clad_ranges)
        if not has_clad_below:
            polygon = [transform(base_a), transform(base_b), transform(top_b), transform(top_a)]
            painter.setPen(QPen(QColor("#202020"), 3))
            painter.setBrush(roof_colour)
            painter.drawPolygon(QPolygonF([QPointF(*point) for point in polygon]))
    _polyline(painter, [transform(point) for point in roof], 7)
    # Barge flashings cap both clad walls and roof-only portions of the end view.
    for _, _, top_a, top_b in roof_edges:
        a, b = transform(top_a), transform(top_b)
        _line(painter, a[0], a[1], b[0], b[1], 14)
        painter.setPen(QPen(barge_colour, 9, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
        painter.drawLine(QLineF(a[0], a[1], b[0], b[1]))
    if roof_edges:
        left_eave = transform(roof_edges[0][2])
        right_eave = transform(roof_edges[-1][3])
        _draw_gutter_end(painter, left_eave, -1 if left_eave[0] < right_eave[0] else 1, gutter_colour, scale)
        _draw_gutter_end(painter, right_eave, 1 if left_eave[0] < right_eave[0] else -1, gutter_colour, scale)
    base_left = transform((outline[0][0], ground))
    base_right = transform((outline[-1][0], ground))
    if not bool(data.get("wall_states", {}).get(face)):
        _text(painter, (base_left[0], base_left[1] - 100, base_right[0] - base_left[0], 50), "OPEN", 27, bold=True, alignment=Qt.AlignCenter)

    for mark_index, door in enumerate(data.get("sliding_doors", []), start=1):
        wall = str(door.get("wall", ""))
        if (face == "front" and wall != "Front wall") or (face == "back" and wall != "Back wall"):
            continue
        height = max(float(door.get("height_m", 0.0)), 0.0)
        width = max(float(door.get("width_m", 0.0)), 0.0)
        position = max(float(door.get("position_m", 0.0)), 0.0)
        main = _indexed_points(data, "main_top_indices")
        if len(main) < 2:
            continue
        x1 = main[0][0] + min(position, max(main[-1][0] - main[0][0] - width, 0.0))
        x2 = x1 + width
        p1, p2 = transform((x1, 0.0)), transform((x2, height))
        _draw_sliding_door_elevation_symbol(painter, door, p1, p2, f"SD{mark_index}")

    for mark_index, door in enumerate(data.get("pa_doors", []), start=1):
        wall = str(door.get("wall", ""))
        if (face == "front" and wall != "Front wall") or (face == "back" and wall != "Back wall"):
            continue
        height = max(float(door.get("height_m", 0.0)), 0.0)
        width = max(float(door.get("width_m", 0.0)), 0.0)
        position = max(float(door.get("position_m", 0.0)), 0.0)
        main = _indexed_points(data, "main_top_indices")
        if len(main) < 2:
            continue
        x1 = main[0][0] + min(position, max(main[-1][0] - main[0][0] - width, 0.0))
        x2 = x1 + width
        p1, p2 = transform((x1, 0.0)), transform((x2, height))
        _draw_pa_door_elevation_symbol(painter, door, p1, p2, f"PA{mark_index}")

    main = _indexed_points(data, "main_top_indices")
    if len(main) >= 2:
        main_min_x = min(point[0] for point in main)
        main_max_x = max(point[0] for point in main)
        nodes = data.get("nodes", [])
        cladded_additions = {
            side
            for side in ("left", "right")
            if any(
                str(surface.get("label", "")).lower().startswith(f"{side} lean-to")
                for surface in clad_surfaces
            )
        }
        for member in data.get("frame_lines", []):
            a_index = int(member.get("a", -1))
            b_index = int(member.get("b", -1))
            if not (0 <= a_index < len(nodes) and 0 <= b_index < len(nodes)):
                continue
            a = (float(nodes[a_index]["x"]), float(nodes[a_index]["y"]))
            b = (float(nodes[b_index]["x"]), float(nodes[b_index]["y"]))
            midpoint_x = (a[0] + b[0]) / 2.0
            group = str(member.get("group", "")).upper()
            exposed_addition = midpoint_x < main_min_x - 1e-6 or midpoint_x > main_max_x + 1e-6
            member_side = "left" if midpoint_x < main_min_x else "right"
            hidden_by_cladding = member_side in cladded_additions
            if exposed_addition and not hidden_by_cladding and any(token in group for token in ("CANOPY", "LEAN", "TOP", "BOTTOM", "WEB", "COLUMN")):
                _polyline(painter, [transform(a), transform(b)], 4)

        for point in (main[0], main[-1]):
            column_x, column_bottom = transform((point[0], ground))
            _, column_top = transform(point)
            _draw_elevation_column(painter, column_x, column_top, column_bottom, _column_profile_at_x(data, point[0]), scale, view="end")
        for member in data.get("frame_lines", []):
            group = str(member.get("group", "")).upper()
            if "LEAN_COLUMN" not in group:
                continue
            a_index = int(member.get("a", -1))
            b_index = int(member.get("b", -1))
            if 0 <= a_index < len(nodes) and 0 <= b_index < len(nodes):
                a = (float(nodes[a_index]["x"]), float(nodes[a_index]["y"]))
                b = (float(nodes[b_index]["x"]), float(nodes[b_index]["y"]))
                column_x, column_bottom = transform(min((a, b), key=lambda point: point[1]))
                _, column_top = transform(max((a, b), key=lambda point: point[1]))
                _draw_elevation_column(painter, column_x, column_top, column_bottom, member.get("profile", {}), scale, view="end")

    grid_points = [main[0], main[-1]] if len(main) >= 2 else [roof[0], roof[-1]]
    for point, label in zip(grid_points, ("A", "C")):
        x, base_y = transform((point[0], ground))
        _, roof_y = transform(point)
        bubble_y = roof_y - 62
        _grid_line(painter, x, bubble_y + 25, x, base_y, 3)
        _grid_bubble(painter, x, bubble_y, label)

    _, overall_dimension_m = _width_dimension_parts(data, roof, main)
    dimension_left, dimension_right = sorted((base_left[0], base_right[0]))
    if len(main) >= 2:
        width_parts, _ = _width_dimension_parts(data, roof, main)
        for physical_x1, physical_x2, label in width_parts:
            x1 = transform((physical_x1, ground))[0]
            x2 = transform((physical_x2, ground))[0]
            _dimension(painter, min(x1, x2), max(x1, x2), base_left[1] + 145, base_left[1] + 18, label)
    _dimension(painter, dimension_left, dimension_right, base_left[1] + 220, base_left[1] + 18, f"{overall_dimension_m * 1000:.0f} OVERALL")
    _text(painter, (dimension_left, base_left[1] + 295, dimension_right - dimension_left, 50), title, 29, bold=True, alignment=Qt.AlignCenter)

    for a, b in _pitch_label_segments(data):
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        pitch = math.degrees(math.atan2(abs(dy), max(abs(dx), 1e-9)))
        if pitch > 0.05:
            _sloped_text(painter, transform(a), transform(b), f"{pitch:.1f} DEG PITCH")


def _draw_side_elevation(painter, data, rect, title, side):
    length = float(data.get("length_m", 0.0))
    z_positions = [float(value) for value in data.get("z_positions", [])]
    if length <= 0.0 or len(z_positions) < 2:
        raise ValueError("The solved model does not contain usable building bays.")
    main = _indexed_points(data, "main_top_indices")
    roof = _roof_points(data)
    apex_height = max((point[1] for point in main), default=max((point[1] for point in roof), default=1.0))
    wall_states = data.get("wall_states", {})
    if side == "left":
        outer_roof = _indexed_points(data, "left_lean_roof_indices")
        eave_height = main[0][1] if main else (roof[0][1] if roof else apex_height)
        clad = bool(data.get("left_lean_states", {}).get("left")) if outer_roof else bool(wall_states.get("left"))
        reverse = True
    else:
        outer_roof = _indexed_points(data, "right_lean_roof_indices")
        eave_height = main[-1][1] if main else (roof[-1][1] if roof else apex_height)
        clad = bool(data.get("right_lean_states", {}).get("right")) if outer_roof else bool(wall_states.get("right"))
        reverse = False
    points = [(0.0, 0.0), (0.0, apex_height), (length, apex_height), (length, 0.0)]
    view_point = (lambda point: (-point[0], point[1])) if reverse else (lambda point: point)
    base_transform, scale = _fit_transform([view_point(point) for point in points], rect, padding=150)
    transform = lambda point: base_transform(view_point(point))
    wall_screen = [transform(point) for point in [(0.0, 0.0), (0.0, eave_height), (length, eave_height), (length, 0.0)]]
    roof_top_left = transform((0.0, apex_height))
    roof_top_right = transform((length, apex_height))

    if clad:
        painter.setPen(Qt.NoPen)
        painter.setBrush(_finish_colour(data, "walls", "#eeeeec"))
        painter.drawRect(QRectF(wall_screen[0][0], wall_screen[1][1], wall_screen[3][0] - wall_screen[0][0], wall_screen[0][1] - wall_screen[1][1]).normalized())
    _polyline(painter, [*wall_screen, wall_screen[0]], 7)
    painter.setBrush(_finish_colour(data, "roof", "#f7f7f5"))
    painter.setPen(QPen(QColor("#202020"), 5))
    roof_band = QPolygonF([
        QPointF(*wall_screen[1]), QPointF(*roof_top_left), QPointF(*roof_top_right), QPointF(*wall_screen[2])
    ])
    painter.drawPolygon(roof_band)
    sheet_spacing = max(length / 48.0, 0.25)
    station = 0.0
    while station <= length + 1e-9:
        x, eave_y = transform((station, eave_height))
        _, apex_y = transform((station, apex_height))
        _line(painter, x, apex_y, x, eave_y, 2)
        station += sheet_spacing
    if clad:
        station = 0.0
        wall_spacing = max(length / 48.0, 0.25)
        while station <= length + 1e-9:
            x, ground_y = transform((station, 0.0))
            _, eave_y = transform((station, eave_height))
            _line(painter, x, ground_y, x, eave_y, 1)
            station += wall_spacing
    gutter_depth = max(0.15 * scale, 4.0)
    gutter_rect = QRectF(
        min(wall_screen[1][0], wall_screen[2][0]),
        min(wall_screen[1][1], wall_screen[2][1]),
        abs(wall_screen[2][0] - wall_screen[1][0]),
        gutter_depth,
    )
    painter.setPen(QPen(QColor("#202020"), 2))
    painter.setBrush(_finish_colour(data, "gutter", "#202020"))
    painter.drawRect(gutter_rect)

    profile_x = (outer_roof[0][0] if side == "left" else outer_roof[-1][0]) if outer_roof else (main[0][0] if side == "left" else main[-1][0])
    column_profile = _column_profile_at_x(data, profile_x)
    for index, z in enumerate(z_positions):
        x, base_y = transform((z, 0.0))
        _, top_y = transform((z, eave_height))
        _draw_elevation_column(painter, x, top_y, base_y, column_profile, scale, view="side", hidden=clad)
        bubble_y = roof_top_left[1] - 62
        _grid_line(painter, x, bubble_y + 25, x, base_y, 3)
        _grid_bubble(painter, x, bubble_y, str(index + 1))
        if index and index < len(z_positions):
            prev_x, _ = transform((z_positions[index - 1], 0.0))
            _dimension(painter, min(prev_x, x), max(prev_x, x), base_y + 145, base_y + 18, f"{(z - z_positions[index - 1]) * 1000:.0f}")
    if not clad:
        for index in range(len(z_positions) - 1):
            xa, _ = transform((z_positions[index], 0.0))
            xb, _ = transform((z_positions[index + 1], 0.0))
            left_x, right_x = sorted((xa, xb))
            _text(painter, (left_x, wall_screen[0][1] - 100, right_x - left_x, 50), "OPEN BAY", 22, bold=True, alignment=Qt.AlignCenter)

    side_wall_name = "Left wall" if side == "left" else "Right wall"
    for mark_index, door in enumerate(data.get("sliding_doors", []), start=1):
        if str(door.get("wall", "")) != side_wall_name:
            continue
        height = max(float(door.get("height_m", 0.0)), 0.0)
        width = max(float(door.get("width_m", 0.0)), 0.0)
        position = min(max(float(door.get("position_m", 0.0)), 0.0), max(length - width, 0.0))
        p1, p2 = transform((position, 0.0)), transform((position + width, height))
        _draw_sliding_door_elevation_symbol(painter, door, p1, p2, f"SD{mark_index}")

    for mark_index, door in enumerate(data.get("pa_doors", []), start=1):
        if str(door.get("wall", "")) != side_wall_name:
            continue
        height = max(float(door.get("height_m", 0.0)), 0.0)
        width = max(float(door.get("width_m", 0.0)), 0.0)
        position = min(max(float(door.get("position_m", 0.0)), 0.0), max(length - width, 0.0))
        p1, p2 = transform((position, 0.0)), transform((position + width, height))
        _draw_pa_door_elevation_symbol(painter, door, p1, p2, f"PA{mark_index}")

    left_x, base_y = transform((0.0, 0.0))
    right_x, _ = transform((length, 0.0))
    drawing_left, drawing_right = sorted((left_x, right_x))
    _dimension(painter, drawing_left, drawing_right, base_y + 220, base_y + 18, f"{length * 1000:.0f} OVERALL")
    dimension_x = drawing_left - 70
    _vertical_dimension(painter, dimension_x, base_y, transform((0.0, eave_height))[1], drawing_left, f"EAVE HEIGHT {eave_height * 1000:.0f}")
    _vertical_dimension(painter, dimension_x - 65, base_y, roof_top_left[1], drawing_left, f"APEX HEIGHT {apex_height * 1000:.0f}")
    datum_start = dimension_x - 80
    datum_end = drawing_right + 18
    _level_datum(painter, datum_start, datum_end, base_y, "GROUND")
    _level_datum(painter, datum_start, datum_end, transform((0.0, eave_height))[1], "EAVE")
    _level_datum(painter, datum_start, datum_end, roof_top_left[1], "APEX")
    _text(painter, (drawing_left, base_y + 225, drawing_right - drawing_left, 50), title, 29, bold=True, alignment=Qt.AlignCenter)


def _draw_title_block(painter, metadata, sheet_code="A300", sheet_title="ELEVATIONS"):
    x, y, w, h = 2680, 2550, 1470, 320
    _line(painter, x, y, x + w, y, 6)
    _line(painter, x, y + h, x + w, y + h, 6)
    _line(painter, x, y, x, y + h, 6)
    _line(painter, x + w, y, x + w, y + h, 6)
    _line(painter, x + 970, y + 185, x + 970, y + h, 4)
    _line(painter, x + 1180, y + 185, x + 1180, y + h, 4)
    _line(painter, x, y + 185, x + w, y + 185, 4)
    _line(painter, x + 990, y, x + 990, y + 185, 4)
    _text(painter, (x + 25, y + 10, 920, 65), metadata.get("project", "UNTITLED PROJECT"), 34, bold=True)
    _text(painter, (x + 25, y + 75, 920, 45), metadata.get("description", "PORTAL FRAME"), 24)
    _text(painter, (x + 25, y + 125, 920, 45), "PRELIMINARY ARRANGEMENT", 22, bold=True)
    logo = _csb_logo()
    if not logo.isNull():
        logo_area = QRectF(x + 1015, y + 24, 430, 137)
        logo_size = logo.size().scaled(logo_area.size().toSize(), Qt.KeepAspectRatio)
        logo_rect = QRectF(
            logo_area.x() + (logo_area.width() - logo_size.width()) / 2.0,
            logo_area.y() + (logo_area.height() - logo_size.height()) / 2.0,
            logo_size.width(),
            logo_size.height(),
        )
        painter.drawImage(logo_rect, logo)
    _text(painter, (x + 990, y + 202, 170, 30), "JOB NO", 18)
    _text(painter, (x + 990, y + 235, 170, 52), metadata.get("job_number", "-"), 28, bold=True)
    _text(painter, (x + 1200, y + 202, 130, 30), "REV", 18)
    _text(painter, (x + 1200, y + 235, 130, 52), metadata.get("revision", "A"), 28, bold=True)
    _text(painter, (x + 25, y + 202, 900, 36), "SHEET", 18)
    _text(painter, (x + 25, y + 235, 900, 52), f"{sheet_code} - {sheet_title}", 29, bold=True)


def export_elevation_pdf(data, path, metadata=None):
    metadata = dict(metadata or {})
    if not str(path).lower().endswith(".pdf"):
        path = f"{path}.pdf"
    writer = QPdfWriter(path)
    writer.setTitle(f"{metadata.get('project', 'Portal frame')} - Drawing Set")
    writer.setCreator("Portal Calc")
    writer.setResolution(300)
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A3), QPageLayout.Landscape, QMarginsF(0, 0, 0, 0)))
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(f"Could not create PDF: {path}")
    painter.setWindow(0, 0, int(PAGE_WIDTH), int(PAGE_HEIGHT))

    def start_sheet(title):
        painter.fillRect(QRectF(0, 0, PAGE_WIDTH, PAGE_HEIGHT), Qt.white)
        painter.setPen(QPen(Qt.black, 6))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(45, 45, PAGE_WIDTH - 90, PAGE_HEIGHT - 90))
        _text(painter, (90, 65, 2200, 70), title, 43, bold=True)
        _text(painter, (90, 132, 2200, 45), "PRELIMINARY ARRANGEMENT - DO NOT SCALE", 22, bold=True)

    start_sheet("FLOOR PLAN")
    _draw_floor_plan(painter, data)
    _draw_door_schedule(painter, data, 2920, 240)
    _draw_title_block(painter, metadata, "A100", "FLOOR PLAN")

    writer.newPage()
    start_sheet("ROOF PLAN")
    _draw_roof_plan(painter, data)
    _draw_finishes_schedule(painter, data, 3000, 260)
    _draw_title_block(painter, metadata, "A200", "ROOF PLAN")

    writer.newPage()
    start_sheet("ELEVATIONS")
    titles = _elevation_titles(data)
    _draw_side_elevation(painter, data, (90, 180, 1900, 940), titles["left"], "left")
    _draw_side_elevation(painter, data, (2160, 180, 1900, 940), titles["right"], "right")
    _draw_end_elevation(painter, data, (90, 1280, 1900, 940), titles["front"], "front")
    _draw_end_elevation(painter, data, (2160, 1280, 1900, 940), titles["back"], "back")
    _draw_finishes_schedule(painter, data, 3000, 180)
    _text(painter, (100, 2560, 1180, 45), "GENERAL NOTES", 27, bold=True)
    _text(painter, (100, 2610, 2300, 150), "1. DIMENSIONS ARE IN MILLIMETRES.\n2. VERIFY ALL DIMENSIONS AND SITE CONDITIONS BEFORE CONSTRUCTION.\n3. THIS SHEET IS A SIMPLE ARRANGEMENT DRAWING GENERATED FROM THE ANALYSIS MODEL.", 20)
    _draw_title_block(painter, metadata, "A300", "ELEVATIONS")

    writer.newPage()
    start_sheet("SECTION")
    _draw_section(painter, data)
    _draw_title_block(painter, metadata, "A400", "SECTION")
    painter.end()
    return os.path.abspath(path)
