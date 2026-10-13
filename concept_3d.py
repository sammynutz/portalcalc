"""3D concept data, HTML preview and Collada export helpers for PortalCalc."""

import html
import json
import math
import os

import numpy as np
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QMessageBox

from finishes import colour_hex


def concept_3d_data(self):
    if self.structure is None or not self.structure.nodes:
        raise ValueError("Generate + Solve before exporting a 3D concept.")

    length_m = max(float(getattr(self, "concept_building_length_m", 0.0) or self.building_length_input.text()), 0.1)
    bay_size_m = max(float(getattr(self, "concept_bay_size_m", 0.0) or self.bay_size_m_from_ui()), 0.1)
    bay_count = max(1, math.ceil(length_m / bay_size_m))
    z_positions = [length_m * i / bay_count for i in range(bay_count + 1)]
    nodes = self.structure.nodes
    node_index = {id(node): index for index, node in enumerate(nodes)}

    frame_lines = []
    for element in self.structure.elements:
        profile = element.profile
        frame_lines.append({
            "a": node_index[id(element.start)],
            "b": node_index[id(element.end)],
            "group": element.group,
            "profile": {
                "name": profile.name,
                "group_name": profile.group_name,
                "shape_type": profile.shape_type,
                "depth_mm": profile.depth,
                "flange_width_mm": profile.flange_width,
                "flange_thickness_mm": profile.flange_thickness,
                "web_thickness_mm": profile.web_thickness,
            },
        })

    roof_indices = [node_index[id(node)] for node in getattr(self, "concept_roof_top_nodes", []) if id(node) in node_index]
    main_top_indices = [node_index[id(node)] for node in getattr(self, "concept_main_top_nodes", []) if id(node) in node_index]
    wall_states = self.envelope_plan_selector.wall_states()
    left_lean_states = self.left_lean_enclosure_selector.wall_states()
    right_lean_states = self.right_lean_enclosure_selector.wall_states()
    wall_segments = [
        {
            "x": float(segment.get("x", 0.0)) / 1000.0,
            "y1": float(segment.get("y1", 0.0)) / 1000.0,
            "y2": float(segment.get("y2", 0.0)) / 1000.0,
            "label": str(segment.get("label", "Wall")),
        }
        for segment in getattr(self, "wall_cladding_segments", [])
    ]
    left_lean_roof_indices = [node_index[id(node)] for node in getattr(self, "concept_left_lean_top_nodes", []) if id(node) in node_index]
    right_lean_roof_indices = [node_index[id(node)] for node in getattr(self, "concept_right_lean_top_nodes", []) if id(node) in node_index]
    left_lean_states = {key: bool(value) and bool(left_lean_roof_indices) for key, value in left_lean_states.items()}
    right_lean_states = {key: bool(value) and bool(right_lean_roof_indices) for key, value in right_lean_states.items()}

    purlin_match = (self.purlin_check or {}).get("match", {})
    local_purlin_match = (self.purlin_check or {}).get("local_match", {})
    purlin_section = (
        local_purlin_match.get("section")
        if local_purlin_match.get("status") == "OK"
        else purlin_match.get("section")
    )
    girt_section = ((self.wall_girt_check or {}).get("match", {}) or {}).get("section")
    purlin_profile = self.metroll_section_dimensions(purlin_section)
    girt_profile = self.metroll_section_dimensions(girt_section or purlin_section)

    def profile_payload(profile):
        if profile is None:
            return {}
        return {
            "name": profile.name,
            "group_name": profile.group_name,
            "shape_type": profile.shape_type,
            "depth_mm": profile.depth,
            "flange_width_mm": profile.flange_width,
            "flange_thickness_mm": profile.flange_thickness,
            "web_thickness_mm": profile.web_thickness,
        }

    storage_results = getattr(self, "storage_results", None)
    storage_panel_design = getattr(self, "storage_panel_design", None)
    storage_enabled = bool(storage_results is not None and storage_results.inputs.enabled)
    storage_panel_height_m = (
        max(storage_results.inputs.stored_height_m, storage_results.inputs.rail_height_mm / 1000.0)
        if storage_enabled else 0.0
    )
    storage_data = {
        "enabled": storage_enabled,
        "panel_height_m": storage_panel_height_m,
        "panel_thickness_m": (
            max(float(storage_panel_design.thickness_mm) / 1000.0, 0.05)
            if storage_panel_design is not None else 0.15
        ),
        "rail_height_m": (storage_results.inputs.rail_height_mm / 1000.0 if storage_enabled else 0.0),
        "left_wall": bool(storage_enabled and storage_results.inputs.left_wall),
        "right_wall": bool(storage_enabled and storage_results.inputs.right_wall),
        "front_wall": bool(storage_enabled and self.storage_front_wall_checkbox.isChecked()),
        "back_wall": bool(storage_enabled and self.storage_back_wall_checkbox.isChecked()),
        "centre_stub": storage_enabled,
        "rail_profile": profile_payload(self.selected_storage_rail_profile()) if storage_enabled else {},
        "stub_profile": profile_payload(self.selected_storage_stub_profile()) if storage_enabled else {},
    }
    sliding_doors = []
    for door in getattr(self, "sliding_doors", []):
        try:
            sliding_doors.append({
                "wall": str(door.get("wall", "Left wall")),
                "height_m": max(float(door.get("height_m", 5.0)), 0.1),
                "width_m": max(float(door.get("width_m", 6.0)), 0.1),
                "position_m": max(float(door.get("position_m", 0.0)), 0.0),
                "opening": str(door.get("opening", "Slide left")),
                "tracks": max(1, min(int(door.get("tracks", 1)), 3)),
                "door_count": max(1, min(int(door.get("door_count", door.get("tracks", 1))), 12)),
            })
        except (TypeError, ValueError):
            continue
    pa_doors = []
    for door in getattr(self, "pa_doors", []):
        try:
            pa_doors.append({
                "wall": str(door.get("wall", "Left wall")),
                "height_m": max(float(door.get("height_m", 2.1)), 0.1),
                "width_m": max(float(door.get("width_m", 0.9)), 0.1),
                "position_m": max(float(door.get("position_m", 2.0)), 0.0),
                "opening": str(door.get("opening", "Open left")),
            })
        except (TypeError, ValueError):
            continue
    end_wall_design = getattr(self, "end_wall_mullion_design", None) or {}
    end_wall_selected = end_wall_design.get("selected") or {}
    end_mullion_profile = profile_payload(
        self.catalog.get(end_wall_selected.get("section")) or self.catalog.get("200 UB 22.3")
    )
    sliding_door_header_profile = profile_payload(self.catalog.get("200 UB 22.3"))
    sliding_door_vertical_profile = self.metroll_section_dimensions("C/Z 150 19")

    def max_profile_depth_for_groups(group_tokens, default_mm):
        depths = []
        for line in frame_lines:
            group = str(line.get("group", "")).upper()
            if any(token in group for token in group_tokens):
                profile = line.get("profile", {})
                depth_value = float(profile.get("depth_mm") or profile.get("flange_width_mm") or 0.0)
                if depth_value > 0.0:
                    depths.append(depth_value)
        return max(depths or [default_mm])

    primary_roof_depth_m = max_profile_depth_for_groups(["TOP"], 200.0) / 1000.0
    primary_wall_depth_m = max_profile_depth_for_groups(["COLUMN"], 200.0) / 1000.0
    purlin_depth_m = max(float(purlin_profile.get("depth_mm") or 150.0) / 1000.0, 0.050)
    girt_depth_m = max(float(girt_profile.get("depth_mm") or 150.0) / 1000.0, 0.050)
    clearance_m = 0.030
    cladding_standoff_m = 0.005
    roof_cladding_depth_m = 0.030
    cladding_back_offset_m = 0.006
    purlin_offset_m = primary_roof_depth_m / 2.0 + purlin_depth_m / 2.0 + clearance_m
    girt_offset_m = primary_wall_depth_m / 2.0 + girt_depth_m / 2.0 + clearance_m
    roof_cladding_offset_m = purlin_offset_m + purlin_depth_m / 2.0 + cladding_standoff_m
    wall_cladding_offset_m = girt_offset_m + girt_depth_m / 2.0 + cladding_standoff_m
    roof_edge_overhang_m = 0.0
    building_end_overhang_m = 0.250

    def roof_edge_overhang_for_x(x_m):
        if len(roof_indices) < 2:
            return 0.0
        left_edge_x = nodes[roof_indices[0]].x / 1000.0
        right_edge_x = nodes[roof_indices[-1]].x / 1000.0
        if abs(x_m - left_edge_x) <= 0.050:
            return -roof_edge_overhang_m
        if abs(x_m - right_edge_x) <= 0.050:
            return roof_edge_overhang_m
        return 0.0

    def wall_offset_for_label(label):
        text = str(label or "").lower()
        if "left" in text:
            return -wall_cladding_offset_m
        if "right" in text:
            return wall_cladding_offset_m
        return 0.0

    def offset_roof_segment_points(index_a, index_b):
        a = nodes[index_a]
        b = nodes[index_b]
        x1 = a.x / 1000.0
        y1 = a.y / 1000.0
        x2 = b.x / 1000.0
        y2 = b.y / 1000.0
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy) or 1.0
        nx = -dy / length
        ny = dx / length
        if ny < 0.0:
            nx = -nx
            ny = -ny
        return (
            x1 + nx * roof_cladding_offset_m,
            y1 + ny * roof_cladding_offset_m,
            x2 + nx * roof_cladding_offset_m,
            y2 + ny * roof_cladding_offset_m,
        )

    def roof_cladding_y_at_x(x_m):
        candidates = []
        for a, b in zip(roof_indices[:-1], roof_indices[1:]):
            x1, y1, x2, y2 = offset_roof_segment_points(a, b)
            dx = x2 - x1
            if abs(dx) <= 1e-9:
                y = max(y1, y2)
                distance = abs(x_m - x1)
            else:
                t = (x_m - x1) / dx
                y = y1 + (y2 - y1) * t
                if 0.0 <= t <= 1.0:
                    distance = 0.0
                else:
                    distance = min(abs(x_m - x1), abs(x_m - x2))
            candidates.append((distance, -y, y))
        if not candidates:
            return None
        return min(candidates)[2]

    for segment in wall_segments:
        label = str(segment.get("label", "")).lower()
        if (storage_data["left_wall"] and "left wall" in label) or (storage_data["right_wall"] and "right wall" in label):
            segment["y1"] = max(segment["y1"], storage_panel_height_m)
        segment["sheet_x"] = segment["x"] + wall_offset_for_label(segment["label"])
        segment["roof_edge_x"] = segment["sheet_x"]
        sheet_x = segment["sheet_x"]
        roof_y = roof_cladding_y_at_x(sheet_x)
        if roof_y is not None:
            segment["y2"] = max(segment["y2"], roof_y)

    def interpolate_polyline(poly_nodes, station_mm):
        remaining = station_mm
        for n1, n2 in zip(poly_nodes[:-1], poly_nodes[1:]):
            dx = n2.x - n1.x
            dy = n2.y - n1.y
            segment_length = math.hypot(dx, dy)
            if segment_length <= 1e-9:
                continue
            if remaining <= segment_length:
                t = remaining / segment_length
                nx = -dy / segment_length
                ny = dx / segment_length
                if ny < 0.0:
                    nx = -nx
                    ny = -ny
                return (n1.x + dx * t, n1.y + dy * t, nx, ny)
            remaining -= segment_length
        n1 = poly_nodes[-2]
        n2 = poly_nodes[-1]
        dx = n2.x - n1.x
        dy = n2.y - n1.y
        segment_length = math.hypot(dx, dy) or 1.0
        nx = -dy / segment_length
        ny = dx / segment_length
        if ny < 0.0:
            nx = -nx
            ny = -ny
        return (poly_nodes[-1].x, poly_nodes[-1].y, nx, ny)

    def purlin_points_for_slope(poly_nodes):
        if len(poly_nodes) < 2:
            return []
        segment_lengths = [
            math.hypot(n2.x - n1.x, n2.y - n1.y)
            for n1, n2 in zip(poly_nodes[:-1], poly_nodes[1:])
        ]
        slope_length = sum(segment_lengths)
        if slope_length <= 1e-9:
            return []
        source_layout = self.purlin_layout
        end_limit = float(getattr(source_layout, "end_spacing_limit_mm", 1200.0) or 1200.0)
        mid_limit = float(getattr(source_layout, "mid_spacing_limit_mm", 1700.0) or 1700.0)
        if slope_length <= 2.0 * end_limit:
            stations = [0.0, slope_length]
        else:
            middle_length = slope_length - 2.0 * end_limit
            middle_spaces = max(1, math.ceil(middle_length / max(mid_limit, 1.0)))
            actual_mid_spacing = middle_length / middle_spaces
            stations = [0.0, end_limit]
            stations.extend(end_limit + actual_mid_spacing * i for i in range(1, middle_spaces))
            stations.extend([slope_length - end_limit, slope_length])
        return [interpolate_polyline(poly_nodes, station) for station in sorted(set(round(s, 6) for s in stations))]

    roof_purlins = []
    if roof_indices:
        roof_nodes = [nodes[index] for index in roof_indices]
        side_junctions = set()
        if main_top_indices and roof_indices[0] != main_top_indices[0]:
            side_junctions.add((round(nodes[main_top_indices[0]].x, 3), round(nodes[main_top_indices[0]].y, 3)))
        if main_top_indices and roof_indices[-1] != main_top_indices[-1]:
            side_junctions.add((round(nodes[main_top_indices[-1]].x, 3), round(nodes[main_top_indices[-1]].y, 3)))
        if left_lean_roof_indices:
            side_junctions.add((round(nodes[left_lean_roof_indices[-1]].x, 3), round(nodes[left_lean_roof_indices[-1]].y, 3)))
        if right_lean_roof_indices:
            side_junctions.add((round(nodes[right_lean_roof_indices[0]].x, 3), round(nodes[right_lean_roof_indices[0]].y, 3)))
        seen = set()
        for slope_nodes in self.roof_slope_node_sets(roof_nodes):
            for x, y, nx, ny in purlin_points_for_slope(slope_nodes):
                key = (round(x, 3), round(y, 3))
                if key not in seen and key not in side_junctions:
                    seen.add(key)
                    edge_overhang_x = roof_edge_overhang_for_x(x / 1000.0)
                    if abs(edge_overhang_x) > 1e-9:
                        roof_y = roof_cladding_y_at_x((x / 1000.0) + edge_overhang_x)
                        if roof_y is not None:
                            y = (roof_y - ny * roof_cladding_offset_m) * 1000.0
                    is_eave = key in {
                        (round(roof_nodes[0].x, 3), round(roof_nodes[0].y, 3)),
                        (round(roof_nodes[-1].x, 3), round(roof_nodes[-1].y, 3)),
                    }
                    eave_drop_m = 0.035
                    adopted_offset_m = purlin_offset_m
                    roof_purlins.append({
                        "x": x / 1000.0 + edge_overhang_x,
                        "y": y / 1000.0,
                        "offset_x": nx * adopted_offset_m,
                        "offset_y": ny * adopted_offset_m,
                        "normal_x": nx,
                        "normal_y": ny,
                        "eave_drop_m": eave_drop_m if is_eave else 0.0,
                        "profile": purlin_profile,
                        "reverse_lips": bool(
                            (self.side_addition_selected(self.left_canopy_type_combo) and abs(x / 1000.0 - nodes[roof_indices[0]].x / 1000.0) <= 0.075)
                            or (self.side_addition_selected(self.right_canopy_type_combo) and abs(x / 1000.0 - nodes[roof_indices[-1]].x / 1000.0) <= 0.075)
                        ),
                    })

    girt_spacing_mm = 1500.0
    if self.wall_girt_check:
        girt_spacing_mm = float(self.wall_girt_check.get("spacing_mm") or girt_spacing_mm)
    wall_girts = []
    for segment in wall_segments:
        y1 = min(segment["y1"], segment["y2"])
        y2 = max(segment["y1"], segment["y2"])
        height_mm = max((y2 - y1) * 1000.0, 0.0)
        if height_mm <= 1e-9:
            continue
        spaces = max(1, math.ceil(height_mm / max(girt_spacing_mm, 1.0)))
        for i in range(spaces + 1):
            wall_girts.append({
                "x": segment["x"],
                "y": y1 + (height_mm / spaces) * i / 1000.0,
                "offset_x": (-girt_offset_m if "left" in segment["label"].lower() else girt_offset_m if "right" in segment["label"].lower() else 0.0),
                "offset_y": 0.0,
                "label": segment["label"],
                "profile": girt_profile,
            })

    def end_wall_surfaces(poly_indices, states, label):
        surfaces = []
        if len(poly_indices) < 2:
            return surfaces
        offset_segments = [offset_roof_segment_points(a, b) for a, b in zip(poly_indices[:-1], poly_indices[1:])]

        def line_intersection(first, second):
            x1, y1, x2, y2 = first
            x3, y3, x4, y4 = second
            denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
            if abs(denominator) <= 1e-9:
                return ((x2 + x3) / 2.0, (y2 + y3) / 2.0)
            determinant1 = x1 * y2 - y1 * x2
            determinant2 = x3 * y4 - y3 * x4
            return (
                (determinant1 * (x3 - x4) - (x1 - x2) * determinant2) / denominator,
                (determinant1 * (y3 - y4) - (y1 - y2) * determinant2) / denominator,
            )

        top_outline = [(offset_segments[0][0], offset_segments[0][1])]
        for first, second in zip(offset_segments[:-1], offset_segments[1:]):
            top_outline.append(line_intersection(first, second))
        top_outline.append((offset_segments[-1][2], offset_segments[-1][3]))
        for point_index, node_index in ((0, poly_indices[0]), (-1, poly_indices[-1])):
            node_x = nodes[node_index].x / 1000.0
            matching_wall = next((segment for segment in wall_segments if abs(segment["x"] - node_x) <= 0.025), None)
            if matching_wall:
                top_outline[point_index] = (matching_wall["sheet_x"], top_outline[point_index][1])
        for wall_key, z_index in [("front", 0), ("back", -1)]:
            if not states.get(wall_key):
                continue
            base_height = storage_panel_height_m if storage_data.get(f"{wall_key}_wall") else 0.0
            z = z_positions[z_index]
            surfaces.append({
                "points": [
                    {"x": top_outline[0][0], "y": base_height, "z": z},
                    *({"x": x, "y": y, "z": z} for x, y in top_outline),
                    {"x": top_outline[-1][0], "y": base_height, "z": z},
                ],
                "label": f"{label} {wall_key}",
            })
        return surfaces

    end_wall_surfaces_data = []
    end_wall_surfaces_data.extend(end_wall_surfaces(main_top_indices, wall_states, "Main"))
    end_wall_surfaces_data.extend(end_wall_surfaces(left_lean_roof_indices, left_lean_states, "Left lean-to"))
    end_wall_surfaces_data.extend(end_wall_surfaces(right_lean_roof_indices, right_lean_states, "Right lean-to"))

    return {
        "title": "Portal frame 3D concept",
        "length_m": length_m,
        "main_span_m": max(float(self.span_input.text()), 0.1),
        "left_addition_type": self.left_canopy_type_combo.currentText(),
        "right_addition_type": self.right_canopy_type_combo.currentText(),
        "north_direction_in_section": self.wind_orientation_selector.currentText(),
        "finishes": {
            "roof": self.roof_finish_combo.currentText(),
            "roof_hex": colour_hex(self.roof_finish_combo.currentText()),
            "walls": self.wall_finish_combo.currentText(),
            "walls_hex": colour_hex(self.wall_finish_combo.currentText()),
            "cladding_back": "Surfmist",
            "cladding_back_hex": colour_hex("Surfmist"),
            "barge": self.barge_finish_combo.currentText(),
            "barge_hex": colour_hex(self.barge_finish_combo.currentText()),
            "ridge_capping": self.ridge_finish_combo.currentText(),
            "ridge_capping_hex": colour_hex(self.ridge_finish_combo.currentText()),
            "corner_trim": self.corner_trim_finish_combo.currentText(),
            "corner_trim_hex": colour_hex(self.corner_trim_finish_combo.currentText()),
            "gutter": self.gutter_finish_combo.currentText(),
            "gutter_hex": colour_hex(self.gutter_finish_combo.currentText()),
            "roof_profile": self.roof_cladding_profile_combo.currentText(),
            "wall_profile": self.wall_cladding_profile_combo.currentText(),
            "skylights": self.skylight_count_combo.currentText(),
            "skylight_run": self.skylight_run_combo.currentText(),
            "show_slab": bool(getattr(self, "show_slab_checkbox", None).isChecked()) if getattr(self, "show_slab_checkbox", None) is not None else True,
        },
        "bay_count": bay_count,
        "z_positions": z_positions,
        "nodes": [{"x": node.x / 1000.0, "y": node.y / 1000.0} for node in nodes],
        "frame_lines": frame_lines,
        "roof_indices": roof_indices,
        "main_top_indices": main_top_indices,
        "wall_states": wall_states,
        "left_lean_states": left_lean_states,
        "right_lean_states": right_lean_states,
        "left_lean_roof_indices": left_lean_roof_indices,
        "right_lean_roof_indices": right_lean_roof_indices,
        "wall_segments": wall_segments,
        "end_wall_surfaces": end_wall_surfaces_data,
        "roof_purlins": roof_purlins,
        "wall_girts": wall_girts,
        "storage": storage_data,
        "sliding_doors": sliding_doors,
        "pa_doors": pa_doors,
        "sliding_door_header_profile": sliding_door_header_profile,
        "sliding_door_vertical_profile": sliding_door_vertical_profile,
        "end_mullion_profile": end_mullion_profile,
        "end_mullion_target_spacing_m": max(float(end_wall_design.get("target_spacing_mm", 8000.0) or 8000.0) / 1000.0, 0.1),
        "end_girt_spacing_m": max(float((getattr(self, "end_wall_girt_check", None) or {}).get("spacing_mm", 1500.0) or 1500.0) / 1000.0, 0.1),
        "roof_cladding_offset_m": roof_cladding_offset_m,
        "roof_cladding_depth_m": roof_cladding_depth_m,
        "cladding_back_offset_m": cladding_back_offset_m,
        "wall_cladding_offset_m": wall_cladding_offset_m,
        "roof_edge_overhang_m": roof_edge_overhang_m,
        "building_end_overhang_m": building_end_overhang_m,
    }



def concept_3d_html(self, data):
    payload = json.dumps(data)
    title = html.escape(data.get("title", "3D concept"))
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
html, body {{ margin: 0; height: 100%; overflow: hidden; font-family: Arial, sans-serif; background: #f4f1ec; color: #202020; }}
#toolbar {{ position: fixed; left: 16px; top: 14px; background: rgba(255,255,255,.86); border: 1px solid #c8c2b7; padding: 10px 12px; border-radius: 6px; box-shadow: 0 2px 12px rgba(0,0,0,.12); }}
#toolbar strong {{ display: block; margin-bottom: 4px; }}
#toolbar span {{ font-size: 12px; color: #555; }}
canvas {{ display: block; width: 100vw; height: 100vh; cursor: grab; }}
canvas:active {{ cursor: grabbing; }}
  </style>
</head>
<body>
<canvas id="view"></canvas>
<div id="toolbar"><strong>{title}</strong><span>Drag to orbit, wheel to zoom. Concept geometry only.</span></div>
<script>
const data = {payload};
const canvas = document.getElementById('view');
const ctx = canvas.getContext('2d');
let yaw = -0.72, pitch = 0.42, zoom = 62;
let dragging = false, lastX = 0, lastY = 0;

function resize() {{
  canvas.width = Math.max(1, window.innerWidth * devicePixelRatio);
  canvas.height = Math.max(1, window.innerHeight * devicePixelRatio);
  draw();
}}
window.addEventListener('resize', resize);
canvas.addEventListener('pointerdown', e => {{ dragging = true; lastX = e.clientX; lastY = e.clientY; canvas.setPointerCapture(e.pointerId); }});
canvas.addEventListener('pointermove', e => {{
  if (!dragging) return;
  yaw += (e.clientX - lastX) * 0.008;
  pitch = Math.max(-1.25, Math.min(1.25, pitch + (e.clientY - lastY) * 0.006));
  lastX = e.clientX; lastY = e.clientY; draw();
}});
canvas.addEventListener('pointerup', () => dragging = false);
canvas.addEventListener('wheel', e => {{ e.preventDefault(); zoom *= Math.exp(-e.deltaY * 0.001); zoom = Math.max(15, Math.min(220, zoom)); draw(); }}, {{passive:false}});

function bounds() {{
  const xs = data.nodes.map(n => n.x), ys = data.nodes.map(n => n.y);
  return {{ cx: (Math.min(...xs) + Math.max(...xs)) / 2, cy: (Math.min(...ys) + Math.max(...ys)) / 2, cz: data.length_m / 2 }};
}}
const b = bounds();

function project(p) {{
  let x = p.x - b.cx, y = p.y - b.cy, z = p.z - b.cz;
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const x1 = x * cy - z * sy;
  const z1 = x * sy + z * cy;
  const y1 = y * cp - z1 * sp;
  const z2 = y * sp + z1 * cp;
  const perspective = 900 / (900 + z2 * zoom * 0.08);
  return {{
x: canvas.width / 2 + x1 * zoom * perspective * devicePixelRatio,
y: canvas.height / 2 - y1 * zoom * perspective * devicePixelRatio,
depth: z2
  }};
}}

function drawLine(a, b, color, width=1.5) {{
  const pa = project(a), pb = project(b);
  ctx.strokeStyle = color;
  ctx.lineWidth = width * devicePixelRatio;
  ctx.beginPath();
  ctx.moveTo(pa.x, pa.y);
  ctx.lineTo(pb.x, pb.y);
  ctx.stroke();
}}

function drawPoly(points, color) {{
  if (points.length < 3) return;
  const ps = points.map(project);
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(ps[0].x, ps[0].y);
  for (const p of ps.slice(1)) ctx.lineTo(p.x, p.y);
  ctx.closePath();
  ctx.fill();
}}

function point(nodeIndex, z) {{
  const n = data.nodes[nodeIndex];
  return {{x:n.x, y:n.y, z:z}};
}}

function offsetRoofPoint(a, c, p) {{
  const offset = Number(data.roof_cladding_offset_m || 0.09);
  const dx = c.x - a.x, dy = c.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  return {{x:p.x - dy / len * offset, y:p.y + dx / len * offset, z:p.z}};
}}

function offsetWallX(x, label='') {{
  const text = String(label).toLowerCase();
  const offset = Number(data.wall_cladding_offset_m || 0.09);
  if (text.includes('left')) return x - offset;
  if (text.includes('right')) return x + offset;
  return x;
}}

function roofWallTargetX(originalX) {{
  for (const wall of data.wall_segments || []) {{
if (Math.abs(Number(wall.x) - originalX) < 0.025) return Number(wall.sheet_x ?? offsetWallX(wall.x, wall.label));
  }}
  return null;
}}

function extendedRoofPoint(a, c, p) {{
  const shifted = offsetRoofPoint(a, c, p);
  const targetX = roofWallTargetX(p.x);
  if (targetX === null) return shifted;
  const sa = offsetRoofPoint(a, c, a);
  const sc = offsetRoofPoint(a, c, c);
  const dx = sc.x - sa.x;
  if (Math.abs(dx) < 1e-9) return {{x:targetX, y:shifted.y, z:shifted.z}};
  const t = (targetX - sa.x) / dx;
  return {{x:targetX, y:sa.y + (sc.y - sa.y) * t, z:shifted.z}};
}}

function offsetEndSurface(surface) {{
  const offset = Number(data.building_end_overhang_m || 0.25);
  const text = String(surface.label || '').toLowerCase();
  const sign = text.includes('front') ? -1 : (text.includes('back') ? 1 : 0);
  return (surface.points || []).map(p => {{ return {{x:p.x, y:p.y, z:p.z + sign * offset}}; }});
}}

function drawCladding() {{
  const endOverhang = Number(data.building_end_overhang_m || 0.25);
  const wallZ0 = data.z_positions[0], wallZ1 = data.z_positions[data.z_positions.length - 1];
  const roofZ0 = wallZ0 - endOverhang, roofZ1 = wallZ1 + endOverhang;
  if (data.roof_indices.length >= 2) {{
for (let i = 0; i < data.roof_indices.length - 1; i++) {{
  const a0 = point(data.roof_indices[i], roofZ0), b0 = point(data.roof_indices[i+1], roofZ0);
  const b1 = point(data.roof_indices[i+1], roofZ1), a1 = point(data.roof_indices[i], roofZ1);
  drawPoly([extendedRoofPoint(a0, b0, a0), extendedRoofPoint(a0, b0, b0), extendedRoofPoint(a1, b1, b1), extendedRoofPoint(a1, b1, a1)], 'rgba(108, 148, 166, .18)');
}}
  }}
  if ((data.wall_segments || []).length) {{
for (const wall of data.wall_segments) {{
  const y1 = Math.min(wall.y1, wall.y2), y2 = Math.max(wall.y1, wall.y2);
  const x = Number(wall.sheet_x ?? offsetWallX(wall.x, wall.label));
  drawPoly([{{x:x,y:y1,z:wallZ0}}, {{x:x,y:y2,z:wallZ0}}, {{x:x,y:y2,z:wallZ1}}, {{x:x,y:y1,z:wallZ1}}], 'rgba(194, 113, 78, .18)');
}}
  }} else {{
if (data.wall_states.left && data.main_top_indices.length) {{
  const i = data.main_top_indices[0], n = data.nodes[i];
  const x = offsetWallX(n.x, 'left');
  drawPoly([{{x:x,y:0,z:wallZ0}}, {{x:x,y:n.y,z:wallZ0}}, {{x:x,y:n.y,z:wallZ1}}, {{x:x,y:0,z:wallZ1}}], 'rgba(194, 113, 78, .18)');
}}
if (data.wall_states.right && data.main_top_indices.length) {{
  const i = data.main_top_indices[data.main_top_indices.length-1], n = data.nodes[i];
  const x = offsetWallX(n.x, 'right');
  drawPoly([{{x:x,y:0,z:wallZ0}}, {{x:x,y:n.y,z:wallZ0}}, {{x:x,y:n.y,z:wallZ1}}, {{x:x,y:0,z:wallZ1}}], 'rgba(194, 113, 78, .18)');
}}
  }}
  for (const surface of data.end_wall_surfaces || []) {{
drawPoly(offsetEndSurface(surface), 'rgba(194, 113, 78, .12)');
  }}
}}

function secondaryWidth(item) {{
  const profile = item.profile || {{}};
  const depth = Number(profile.depth_mm || 150);
  return Math.max(1.4, Math.min(3.0, depth / 85));
}}

function secondaryZStations(extendEnds) {{
  const endOverhang = extendEnds ? Number(data.building_end_overhang_m || 0.25) : 0;
  return [data.z_positions[0] - endOverhang, ...data.z_positions.slice(1, -1), data.z_positions[data.z_positions.length - 1] + endOverhang];
}}

function drawSecondarySymbol(item, color, extendEnds=false) {{
  const zs = secondaryZStations(extendEnds);
  for (const z of zs) {{
const p = {{x:item.x + Number(item.offset_x || 0), y:item.y + Number(item.offset_y || 0), z:z}};
const size = 0.045;
drawLine({{x:p.x-size,y:p.y,z:p.z}}, {{x:p.x+size,y:p.y,z:p.z}}, color, 0.75);
drawLine({{x:p.x,y:p.y-size,z:p.z}}, {{x:p.x,y:p.y+size,z:p.z}}, color, 0.75);
  }}
}}

function drawSecondaryRuns(items, color, extendEnds=false) {{
  const zs = secondaryZStations(extendEnds);
  for (const item of items || []) {{
const x = item.x + Number(item.offset_x || 0);
const y = item.y + Number(item.offset_y || 0);
for (let zi = 0; zi < zs.length - 1; zi++) {{
  drawLine({{x:x,y:y,z:zs[zi]}}, {{x:x,y:y,z:zs[zi+1]}}, color, secondaryWidth(item));
}}
drawSecondarySymbol(item, color, extendEnds);
  }}
}}

function drawSecondarySteel() {{
  drawSecondaryRuns(data.roof_purlins, '#a9b1b1', true);
  drawSecondaryRuns(data.wall_girts, '#9ea7a7', false);
}}

function draw() {{
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#f4f1ec';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  drawCladding();
  for (const z of data.z_positions) {{
for (const line of data.frame_lines) {{
  const major = ['TOP','BOTTOM','LEFT_COLUMN','RIGHT_COLUMN','INTERNAL_COLUMN','COLUMN'].includes(line.group);
  drawLine(point(line.a,z), point(line.b,z), major ? '#4b5052' : '#6a7072', major ? 2.2 : 1.1);
}}
  }}
  drawSecondarySteel();
}}
resize();
</script>
</body>
</html>
"""



def concept_3d_dae(self, data, return_mesh=False):
    vertices = []
    triangles = []
    current_material = "steel"

    def set_material(material):
        nonlocal current_material
        current_material = material

    def add_vertex(point):
        vertices.append(point)
        return len(vertices) - 1

    def add_triangle(a, b, c):
        triangles.append((current_material, (add_vertex(a), add_vertex(b), add_vertex(c))))

    def add_quad(a, b, c, d):
        add_triangle(a, b, c)
        add_triangle(a, c, d)

    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def cross(a, b):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def length(v):
        return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])

    def normalize(v):
        size = length(v)
        if size <= 1e-12:
            return (0.0, 0.0, 0.0)
        return (v[0] / size, v[1] / size, v[2] / size)

    def scale(v, factor):
        return (v[0] * factor, v[1] * factor, v[2] * factor)

    def add(a, b):
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    def add_scaled(origin, *terms):
        result = origin
        for vector, factor in terms:
            result = add(result, scale(vector, factor))
        return result

    def point(node_index, z_m):
        node = data["nodes"][node_index]
        # Collada is exported Z-up for SketchUp: X = frame width,
        # Y = building length, Z = height.
        return (node["x"], z_m, node["y"])

    def concept_point(x_m, y_m, z_m):
        return (x_m, z_m, y_m)

    def section_axes(a, b):
        axis = sub(b, a)
        if length(axis) <= 1e-9:
            return None
        direction = normalize(axis)
        width_axis = (0.0, 1.0, 0.0)
        if length(cross(direction, width_axis)) <= 1e-9:
            width_axis = (1.0, 0.0, 0.0)
        depth_axis = normalize(cross(direction, width_axis))
        if length(depth_axis) <= 1e-9:
            depth_axis = (0.0, 0.0, 1.0)
        width_axis = normalize(cross(depth_axis, direction))
        return direction, width_axis, depth_axis

    def add_box_along_member(a, b, width_axis, depth_axis, width, depth, width_offset=0.0, depth_offset=0.0):
        half_width = max(width, 0.002) / 2.0
        half_depth = max(depth, 0.002) / 2.0
        corners = [
            (-half_width, -half_depth),
            (half_width, -half_depth),
            (half_width, half_depth),
            (-half_width, half_depth),
        ]
        a_pts = [
            add_scaled(a, (width_axis, width_offset + w), (depth_axis, depth_offset + d))
            for w, d in corners
        ]
        b_pts = [
            add_scaled(b, (width_axis, width_offset + w), (depth_axis, depth_offset + d))
            for w, d in corners
        ]
        for i in range(4):
            add_quad(a_pts[i], a_pts[(i + 1) % 4], b_pts[(i + 1) % 4], b_pts[i])
        add_quad(a_pts[3], a_pts[2], a_pts[1], a_pts[0])
        add_quad(b_pts[0], b_pts[1], b_pts[2], b_pts[3])

    def add_circular_tube_along_member(a, b, width_axis, depth_axis, diameter, thickness, segments=16):
        outer_radius = max(float(diameter) / 2.0, 0.0)
        inner_radius = max(outer_radius - float(thickness), 0.0)
        if outer_radius <= 0.0 or inner_radius <= 0.0:
            return

        def ring_point(origin, radius, angle):
            return add(
                origin,
                add(scale(width_axis, radius * math.cos(angle)), scale(depth_axis, radius * math.sin(angle))),
            )

        for index in range(segments):
            angle_a = 2.0 * math.pi * index / segments
            angle_b = 2.0 * math.pi * (index + 1) / segments
            a_outer_1 = ring_point(a, outer_radius, angle_a)
            a_outer_2 = ring_point(a, outer_radius, angle_b)
            b_outer_1 = ring_point(b, outer_radius, angle_a)
            b_outer_2 = ring_point(b, outer_radius, angle_b)
            a_inner_1 = ring_point(a, inner_radius, angle_a)
            a_inner_2 = ring_point(a, inner_radius, angle_b)
            b_inner_1 = ring_point(b, inner_radius, angle_a)
            b_inner_2 = ring_point(b, inner_radius, angle_b)
            add_quad(a_outer_1, a_outer_2, b_outer_2, b_outer_1)
            add_quad(a_inner_2, a_inner_1, b_inner_1, b_inner_2)
            add_quad(a_outer_2, a_outer_1, a_inner_1, a_inner_2)
            add_quad(b_outer_1, b_outer_2, b_inner_2, b_inner_1)

    def add_prism(a, b, thickness):
        axes = section_axes(a, b)
        if axes is None:
            return
        _, side, other = axes
        half = thickness / 2.0
        offsets = [
            add(scale(side, -half), scale(other, -half)),
            add(scale(side, half), scale(other, -half)),
            add(scale(side, half), scale(other, half)),
            add(scale(side, -half), scale(other, half)),
        ]
        a_pts = [add(a, offset) for offset in offsets]
        b_pts = [add(b, offset) for offset in offsets]
        for i in range(4):
            add_quad(a_pts[i], a_pts[(i + 1) % 4], b_pts[(i + 1) % 4], b_pts[i])
        add_quad(a_pts[3], a_pts[2], a_pts[1], a_pts[0])
        add_quad(b_pts[0], b_pts[1], b_pts[2], b_pts[3])

    def add_axis_box(x1, x2, length1, length2, height1, height2):
        x1, x2 = sorted((x1, x2))
        length1, length2 = sorted((length1, length2))
        height1, height2 = sorted((height1, height2))
        p000, p100 = (x1, length1, height1), (x2, length1, height1)
        p110, p010 = (x2, length2, height1), (x1, length2, height1)
        p001, p101 = (x1, length1, height2), (x2, length1, height2)
        p111, p011 = (x2, length2, height2), (x1, length2, height2)
        for face in (
            (p000, p100, p101, p001), (p100, p110, p111, p101),
            (p110, p010, p011, p111), (p010, p000, p001, p011),
            (p000, p010, p110, p100), (p001, p101, p111, p011),
        ):
            add_quad(*face)

    def add_profile_member(a, b, profile, fallback_thickness, quarter_turn=False):
        axes = section_axes(a, b)
        if axes is None:
            return
        _, width_axis, depth_axis = axes
        if quarter_turn:
            width_axis, depth_axis = depth_axis, scale(width_axis, -1.0)
        text = f"{profile.get('name', '')} {profile.get('group_name', '')} {profile.get('shape_type', '')}".upper()
        depth = float(profile.get("depth_mm") or 0.0) / 1000.0
        flange_width = float(profile.get("flange_width_mm") or 0.0) / 1000.0
        flange_thickness = float(profile.get("flange_thickness_mm") or 0.0) / 1000.0
        web_thickness = float(profile.get("web_thickness_mm") or 0.0) / 1000.0

        has_plate_dims = depth > 0.0 and flange_width > 0.0 and flange_thickness > 0.0 and web_thickness > 0.0
        if has_plate_dims and any(token in text for token in ["UB", "UC", "WB", "WC", "I SECTION", "UNIVERSAL BEAM", "UNIVERSAL COLUMN"]):
            web_depth = max(depth - 2.0 * flange_thickness, flange_thickness)
            flange_offset = max((depth - flange_thickness) / 2.0, 0.0)
            add_box_along_member(a, b, width_axis, depth_axis, flange_width, flange_thickness, depth_offset=flange_offset)
            add_box_along_member(a, b, width_axis, depth_axis, flange_width, flange_thickness, depth_offset=-flange_offset)
            add_box_along_member(a, b, width_axis, depth_axis, web_thickness, web_depth)
            return

        if has_plate_dims and any(token in text for token in ["PFC", "CHANNEL", "C PURLIN", "CEE"]):
            web_offset = -(flange_width - web_thickness) / 2.0
            flange_depth_offset = max((depth - flange_thickness) / 2.0, 0.0)
            flange_width_offset = max(web_thickness / 2.0, 0.0)
            add_box_along_member(a, b, width_axis, depth_axis, web_thickness, depth, width_offset=web_offset)
            add_box_along_member(a, b, width_axis, depth_axis, flange_width, flange_thickness, width_offset=flange_width_offset, depth_offset=flange_depth_offset)
            add_box_along_member(a, b, width_axis, depth_axis, flange_width, flange_thickness, width_offset=flange_width_offset, depth_offset=-flange_depth_offset)
            return

        if has_plate_dims and any(token in text for token in ["CHS", "CIRCULAR HOLLOW"]):
            add_circular_tube_along_member(a, b, width_axis, depth_axis, max(depth, flange_width), web_thickness)
            return

        if has_plate_dims and any(token in text for token in ["RHS", "SHS", "HOLLOW", "TUBE"]):
            side_offset = max((flange_width - web_thickness) / 2.0, 0.0)
            depth_offset = max((depth - flange_thickness) / 2.0, 0.0)
            add_box_along_member(a, b, width_axis, depth_axis, web_thickness, depth, width_offset=-side_offset)
            add_box_along_member(a, b, width_axis, depth_axis, web_thickness, depth, width_offset=side_offset)
            add_box_along_member(a, b, width_axis, depth_axis, flange_width, flange_thickness, depth_offset=depth_offset)
            add_box_along_member(a, b, width_axis, depth_axis, flange_width, flange_thickness, depth_offset=-depth_offset)
            return

        if depth > 0.0 and flange_width > 0.0:
            add_box_along_member(a, b, width_axis, depth_axis, flange_width, depth)
            return

        add_prism(a, b, fallback_thickness)

    def add_cold_formed_member(a, b, profile, reverse_lips=False, open_down=False, section_normal=None):
        axes = section_axes(a, b)
        if axes is None:
            return
        _, width_axis, depth_axis = axes
        if section_normal is not None:
            direction = normalize(sub(b, a))
            depth_axis = normalize(section_normal)
            width_axis = normalize(cross(depth_axis, direction))
        depth = max(float(profile.get("depth_mm") or 150.0) / 1000.0, 0.050)
        flange_width = max(float(profile.get("flange_width_mm") or 60.0) / 1000.0, 0.020)
        flange_under = max(float(profile.get("flange_under_mm") or flange_width * 1000.0) / 1000.0, 0.020)
        thickness = max(float(profile.get("thickness_mm") or 1.9) / 1000.0, 0.0012)
        lip = max(min(flange_width, flange_under) * 0.28, thickness * 4.0)
        if open_down:
            web_depth_offset = -flange_width / 2.0
            left_width_offset = -(depth - thickness) / 2.0
            right_width_offset = (depth - thickness) / 2.0
            add_box_along_member(a, b, width_axis, depth_axis, depth, thickness, depth_offset=web_depth_offset)
            add_box_along_member(a, b, width_axis, depth_axis, thickness, flange_width, width_offset=left_width_offset)
            add_box_along_member(a, b, width_axis, depth_axis, thickness, flange_under, width_offset=right_width_offset)
            add_box_along_member(a, b, width_axis, depth_axis, lip, thickness, width_offset=left_width_offset + lip / 2.0, depth_offset=flange_width / 2.0)
            add_box_along_member(a, b, width_axis, depth_axis, lip, thickness, width_offset=right_width_offset - lip / 2.0, depth_offset=flange_under / 2.0)
            return
        open_sign = -1.0 if reverse_lips else 1.0
        web_offset = -open_sign * flange_width / 2.0
        top_depth_offset = max((depth - thickness) / 2.0, 0.0)
        bottom_depth_offset = -top_depth_offset
        add_box_along_member(a, b, width_axis, depth_axis, thickness, depth, width_offset=web_offset)
        add_box_along_member(a, b, width_axis, depth_axis, flange_width, thickness, depth_offset=top_depth_offset)
        add_box_along_member(a, b, width_axis, depth_axis, flange_under, thickness, depth_offset=bottom_depth_offset)
        top_lip_width_offset = web_offset + open_sign * (flange_width - thickness / 2.0)
        bottom_lip_width_offset = web_offset + open_sign * (flange_under - thickness / 2.0)
        add_box_along_member(a, b, width_axis, depth_axis, thickness, lip, width_offset=top_lip_width_offset, depth_offset=top_depth_offset - lip / 2.0)
        add_box_along_member(a, b, width_axis, depth_axis, thickness, lip, width_offset=bottom_lip_width_offset, depth_offset=bottom_depth_offset + lip / 2.0)

    def cladding_profile_height(position, profile_name, origin=0.0):
        local = position - origin
        if str(profile_name).startswith("Corry"):
            pitch = 0.0762
            phase = (local % pitch) / pitch
            return 0.008 * (1.0 + math.cos(math.tau * phase))
        pitch = 0.1905
        distance = abs((local + pitch / 2.0) % pitch - pitch / 2.0)
        top_half_width = 0.012
        rib_half_width = 0.030
        if distance <= top_half_width:
            return 0.029
        if distance >= rib_half_width:
            return 0.0
        return 0.029 * (rib_half_width - distance) / (rib_half_width - top_half_width)

    def cladding_profile_stations(start, end, profile_name, origin=0.0):
        low, high = sorted((float(start), float(end)))
        stations = {round(low, 9), round(high, 9)}
        if str(profile_name).startswith("Corry"):
            step = 0.0762 / 8.0
            first = math.floor((low - origin) / step) - 1
            last = math.ceil((high - origin) / step) + 1
            stations.update(round(origin + index * step, 9) for index in range(first, last + 1) if low < origin + index * step < high)
        else:
            pitch = 0.1905
            first = math.floor((low - origin) / pitch) - 1
            last = math.ceil((high - origin) / pitch) + 1
            for index in range(first, last + 1):
                centre = origin + index * pitch
                for offset in (-0.030, -0.012, 0.0, 0.012, 0.030):
                    value = centre + offset
                    if low < value < high:
                        stations.add(round(value, 9))
        ordered = sorted(stations)
        return ordered if start <= end else list(reversed(ordered))

    def add_cladding():
        end_overhang = max(float(data.get("building_end_overhang_m", 0.25) or 0.25), 0.0)
        wall_z0 = data["z_positions"][0]
        wall_z1 = data["z_positions"][-1]
        roof_z0 = wall_z0 - end_overhang
        roof_z1 = wall_z1 + end_overhang
        roof_offset = max(float(data.get("roof_cladding_offset_m", 0.09) or 0.09), 0.0)
        wall_offset = max(float(data.get("wall_cladding_offset_m", 0.09) or 0.09), 0.0)
        back_offset = max(float(data.get("cladding_back_offset_m", 0.006) or 0.006), 0.002)
        finishes = data.get("finishes", {})
        skylight_count = {"One per bay": 1, "Two per bay": 2}.get(finishes.get("skylights"), 0)
        skylight_bands = []
        if skylight_count:
            for bay_start, bay_end in zip(data["z_positions"][:-1], data["z_positions"][1:]):
                bay_width = bay_end - bay_start
                sheet_width = min(0.762, bay_width / (skylight_count + 1) * 0.72)
                for number in range(1, skylight_count + 1):
                    centre = bay_start + bay_width * number / (skylight_count + 1)
                    skylight_bands.append((centre - sheet_width / 2.0, centre + sheet_width / 2.0))
        main_indices = set(data.get("main_top_indices", []))
        main_nodes = [data["nodes"][index] for index in data.get("main_top_indices", [])]
        apex_x = float(max(main_nodes, key=lambda node: float(node["y"]))["x"]) if main_nodes else 0.0
        main_min_x = min((float(node["x"]) for node in main_nodes), default=0.0)
        main_max_x = max((float(node["x"]) for node in main_nodes), default=0.0)

        def wall_x_offset(label):
            text = str(label or "").lower()
            if "left" in text:
                return -wall_offset
            if "right" in text:
                return wall_offset
            return 0.0

        def roof_wall_target_x(node_index):
            node_x = data["nodes"][node_index]["x"]
            for segment in data.get("wall_segments", []):
                if abs(float(segment.get("x", 0.0)) - node_x) <= 0.025:
                    return float(segment.get("roof_edge_x", segment.get("sheet_x", float(segment.get("x", 0.0)) + wall_x_offset(segment.get("label", "")))))
            return None

        def end_wall_y_offset(label):
            text = str(label or "").lower()
            if "front" in text:
                return -end_overhang
            if "back" in text:
                return end_overhang
            return 0.0

        def add_roof_quad(a_index, b_index, start_z, end_z):
            a0 = point(a_index, start_z)
            b0 = point(b_index, start_z)
            b1 = point(b_index, end_z)
            a1 = point(a_index, end_z)
            dx = b0[0] - a0[0]
            dz = b0[2] - a0[2]
            normal = normalize((-dz, 0.0, dx))
            if length(normal) <= 1e-9:
                normal = (0.0, 0.0, 1.0)
            a0s = add_scaled(a0, (normal, roof_offset))
            b0s = add_scaled(b0, (normal, roof_offset))
            b1s = add_scaled(b1, (normal, roof_offset))
            a1s = add_scaled(a1, (normal, roof_offset))

            def extend_to_wall(point_value, line_start, line_end, target_x):
                if target_x is None:
                    return point_value
                dx_line = line_end[0] - line_start[0]
                if abs(dx_line) <= 1e-9:
                    return (target_x, point_value[1], point_value[2])
                t = (target_x - line_start[0]) / dx_line
                return (
                    target_x,
                    point_value[1],
                    line_start[2] + (line_end[2] - line_start[2]) * t,
                )

            target_a = roof_wall_target_x(a_index)
            target_b = roof_wall_target_x(b_index)
            a0s = extend_to_wall(a0s, a0s, b0s, target_a)
            a1s = extend_to_wall(a1s, a1s, b1s, target_a)
            b0s = extend_to_wall(b0s, a0s, b0s, target_b)
            b1s = extend_to_wall(b1s, a1s, b1s, target_b)
            profile_name = finishes.get("roof_profile", "5-Rib (Trimdek)")
            stations = cladding_profile_stations(start_z, end_z, profile_name)
            stations = sorted(set(stations + [edge for band in skylight_bands for edge in band if start_z < edge < end_z]))
            for first_y, second_y in zip(stations[:-1], stations[1:]):
                midpoint_y = (first_y + second_y) / 2.0
                on_main_roof = a_index in main_indices and b_index in main_indices
                segment_mid_x = (float(data["nodes"][a_index]["x"]) + float(data["nodes"][b_index]["x"])) / 2.0
                within_half_run = (
                    finishes.get("skylight_run") != "Half run"
                    or abs(segment_mid_x - apex_x) <= (main_max_x - main_min_x) / 4.0 + 1e-9
                )
                is_skylight = on_main_roof and within_half_run and any(low <= midpoint_y <= high for low, high in skylight_bands)
                set_material("skylight" if is_skylight else "roof_cladding")
                first_height = cladding_profile_height(first_y, profile_name)
                second_height = cladding_profile_height(second_y, profile_name)
                first_a = add_scaled((a0s[0], first_y, a0s[2]), (normal, first_height))
                first_b = add_scaled((b0s[0], first_y, b0s[2]), (normal, first_height))
                second_a = add_scaled((a0s[0], second_y, a0s[2]), (normal, second_height))
                second_b = add_scaled((b0s[0], second_y, b0s[2]), (normal, second_height))
                add_quad(first_a, first_b, second_b, second_a)
            set_material("cladding_back")
            underside = [add_scaled(point_value, (normal, -back_offset)) for point_value in (a0s, a1s, b1s, b0s)]
            add_quad(*underside)

        roof_indices = data.get("roof_indices", [])
        if len(roof_indices) >= 2:
            for a, b in zip(roof_indices[:-1], roof_indices[1:]):
                add_roof_quad(a, b, roof_z0, roof_z1)

        main_top = data.get("main_top_indices", [])
        wall_states = data.get("wall_states", {})
        wall_segments = data.get("wall_segments", [])
        set_material("wall_cladding")
        if wall_segments:
            for segment in wall_segments:
                x = float(segment.get("sheet_x", float(segment.get("x", 0.0)) + wall_x_offset(segment.get("label", ""))))
                y1 = min(float(segment.get("y1", 0.0)), float(segment.get("y2", 0.0)))
                y2 = max(float(segment.get("y1", 0.0)), float(segment.get("y2", 0.0)))
                profile_name = data.get("finishes", {}).get("wall_profile", "5-Rib (Trimdek)")
                outward = -1.0 if "left" in str(segment.get("label", "")).lower() else 1.0
                stations = cladding_profile_stations(roof_z0, roof_z1, profile_name)
                for first_y, second_y in zip(stations[:-1], stations[1:]):
                    set_material("wall_cladding")
                    first_x = x + outward * cladding_profile_height(first_y, profile_name)
                    second_x = x + outward * cladding_profile_height(second_y, profile_name)
                    face = (
                        concept_point(first_x, y1, first_y), concept_point(first_x, y2, first_y),
                        concept_point(second_x, y2, second_y), concept_point(second_x, y1, second_y),
                    )
                    add_quad(*face)
                set_material("cladding_back")
                back_x = x - outward * back_offset
                add_quad(
                    concept_point(back_x, y1, roof_z0), concept_point(back_x, y1, roof_z1),
                    concept_point(back_x, y2, roof_z1), concept_point(back_x, y2, roof_z0),
                )
        else:
            if wall_states.get("left") and main_top:
                top = data["nodes"][main_top[0]]
                x = top["x"] - wall_offset
                add_quad((x, wall_z0, 0.0), (x, wall_z0, top["y"]), (x, wall_z1, top["y"]), (x, wall_z1, 0.0))
            if wall_states.get("right") and main_top:
                top = data["nodes"][main_top[-1]]
                x = top["x"] + wall_offset
                add_quad((x, wall_z1, 0.0), (x, wall_z1, top["y"]), (x, wall_z0, top["y"]), (x, wall_z0, 0.0))
        for surface in data.get("end_wall_surfaces", []):
            points = surface.get("points", [])
            if len(points) < 3:
                continue
            y_offset = end_wall_y_offset(surface.get("label", ""))
            outline = [
                (float(surface_point.get("x", 0.0)), float(surface_point.get("y", 0.0)))
                for surface_point in points[1:-1]
            ]
            if len(outline) < 2:
                continue

            def outline_height(x_value):
                heights = []
                for first, second in zip(outline[:-1], outline[1:]):
                    if min(first[0], second[0]) - 1e-9 <= x_value <= max(first[0], second[0]) + 1e-9:
                        fraction = 0.0 if abs(second[0] - first[0]) <= 1e-9 else (x_value - first[0]) / (second[0] - first[0])
                        heights.append(first[1] + (second[1] - first[1]) * fraction)
                return max(heights or [0.0])

            profile_name = data.get("finishes", {}).get("wall_profile", "5-Rib (Trimdek)")
            x_start, x_end = min(point[0] for point in outline), max(point[0] for point in outline)
            stations = cladding_profile_stations(x_start, x_end, profile_name, origin=x_start)
            outward = -1.0 if "front" in str(surface.get("label", "")).lower() else 1.0
            base_y = float(points[0].get("z", 0.0)) + y_offset
            base_height = max(float(points[0].get("y", 0.0)), float(points[-1].get("y", 0.0)))
            for first_x, second_x in zip(stations[:-1], stations[1:]):
                set_material("wall_cladding")
                first_y = base_y + outward * cladding_profile_height(first_x, profile_name, origin=x_start)
                second_y = base_y + outward * cladding_profile_height(second_x, profile_name, origin=x_start)
                face = (
                    (first_x, first_y, base_height), (second_x, second_y, base_height),
                    (second_x, second_y, outline_height(second_x)),
                    (first_x, first_y, outline_height(first_x)),
                )
                add_quad(*face)
                set_material("cladding_back")
                back = tuple((point_value[0], point_value[1] - outward * back_offset, point_value[2]) for point_value in reversed(face))
                add_quad(*back)

    add_cladding()

    def add_sliding_doors():
        doors = data.get("sliding_doors", [])
        if not doors:
            return
        wall_profile_name = data.get("finishes", {}).get("wall_profile", "5-Rib (Trimdek)")
        back_offset = max(float(data.get("cladding_back_offset_m", 0.006) or 0.006), 0.002)
        vertical_profile = data.get("sliding_door_vertical_profile", {})
        header_profile = data.get("sliding_door_header_profile", {})
        header_depth = max(float(header_profile.get("depth_mm") or 200.0) / 1000.0, 0.1)
        z_front = float(data["z_positions"][0])
        z_back = float(data["z_positions"][-1])
        end_overhang = max(float(data.get("building_end_overhang_m", 0.25) or 0.25), 0.0)
        main_indices = data.get("main_top_indices", [])
        main_x = [float(data["nodes"][index]["x"]) for index in main_indices]
        if len(main_x) < 2:
            return
        main_left, main_right = min(main_x), max(main_x)

        for door in doors:
            wall = str(door.get("wall", "Left wall"))
            height = max(float(door.get("height_m", 5.0)), 0.1)
            door_bottom = 0.025
            width = max(float(door.get("width_m", 6.0)), 0.1)
            position = max(float(door.get("position_m", 0.0)), 0.0)
            tracks = max(1, min(int(door.get("tracks", 1)), 3))
            door_count = max(1, min(int(door.get("door_count", tracks)), 12))
            opening = str(door.get("opening", "Slide left"))
            is_side = wall in {"Left wall", "Right wall"}
            wall_span = (z_back - z_front) if is_side else (main_right - main_left)
            width = min(width, max(wall_span - position, 0.1))
            start = (z_front if is_side else main_left) + min(position, max(wall_span - width, 0.0))
            leaf_width = width / door_count

            if is_side:
                segment = next((item for item in data.get("wall_segments", []) if str(item.get("label", "")) == wall), None)
                if segment is None:
                    continue
                outward = -1.0 if wall == "Left wall" else 1.0
                wall_plane = float(segment.get("sheet_x", segment.get("x", 0.0)))
            else:
                outward = -1.0 if wall == "Front wall" else 1.0
                wall_plane = (z_front - end_overhang) if wall == "Front wall" else (z_back + end_overhang)

            def door_track_index(leaf):
                if tracks <= 1:
                    return 0.0
                sequence = leaf if opening != "Slide right" else door_count - 1 - leaf
                track_index = sequence % tracks
                if opening == "Bi-parting":
                    centre = (door_count - 1) / 2.0
                    track_index = int(abs(leaf - centre)) % tracks
                return float(track_index)

            def add_jamb(edge, bottom, top, inward_sign):
                reverse_lips = inward_sign > 0.0
                if is_side:
                    add_cold_formed_member(
                        (frame_plane, edge, bottom),
                        (frame_plane, edge, top),
                        vertical_profile,
                        reverse_lips=reverse_lips,
                        section_normal=(outward, 0.0, 0.0),
                    )
                else:
                    add_cold_formed_member(
                        (edge, frame_plane, bottom),
                        (edge, frame_plane, top),
                        vertical_profile,
                        reverse_lips=reverse_lips,
                        section_normal=(0.0, outward, 0.0),
                    )

            max_track_index = max(0.0, float(tracks - 1))
            outer_flashing_plane = wall_plane + outward * (0.025 + max_track_index * 0.065 + 0.055)

            for leaf in range(door_count):
                track_index = door_track_index(leaf)
                track_offset = 0.025 + track_index * 0.065
                face_plane = wall_plane + outward * track_offset
                frame_plane = face_plane - outward * 0.025
                leaf_start = start + leaf * leaf_width
                leaf_end = leaf_start + leaf_width

                stations = cladding_profile_stations(leaf_start, leaf_end, wall_profile_name, origin=start)
                for first, second in zip(stations[:-1], stations[1:]):
                    first_rib = cladding_profile_height(first, wall_profile_name, origin=start)
                    second_rib = cladding_profile_height(second, wall_profile_name, origin=start)
                    set_material("wall_cladding")
                    if is_side:
                        add_quad(
                            (face_plane + outward * first_rib, first, door_bottom),
                            (face_plane + outward * first_rib, first, height),
                            (face_plane + outward * second_rib, second, height),
                            (face_plane + outward * second_rib, second, door_bottom),
                        )
                    else:
                        add_quad(
                            (first, face_plane + outward * first_rib, door_bottom),
                            (second, face_plane + outward * second_rib, door_bottom),
                            (second, face_plane + outward * second_rib, height),
                            (first, face_plane + outward * first_rib, height),
                        )
                set_material("cladding_back")
                back_plane = face_plane - outward * back_offset
                if is_side:
                    add_quad((back_plane, leaf_start, door_bottom), (back_plane, leaf_end, door_bottom), (back_plane, leaf_end, height), (back_plane, leaf_start, height))
                else:
                    add_quad((leaf_start, back_plane, door_bottom), (leaf_start, back_plane, height), (leaf_end, back_plane, height), (leaf_end, back_plane, door_bottom))

                set_material("galvanised")
                add_jamb(leaf_start, door_bottom, height, 1.0)
                add_jamb(leaf_end, door_bottom, height, -1.0)

            header_plane = wall_plane + outward * 0.025
            header_clearance = 0.100
            header_centre_height = height + header_clearance + header_depth / 2.0
            set_material("galvanised")
            if is_side:
                add_profile_member((header_plane, start, header_centre_height), (header_plane, start + width, header_centre_height), header_profile, 0.20)
            else:
                add_profile_member((start, header_plane, header_centre_height), (start + width, header_plane, header_centre_height), header_profile, 0.20)

            flashing_top = height + header_clearance + header_depth + 0.035
            flashing_bottom = max(height - 0.050, door_bottom)
            flashing_inner_plane = wall_plane - outward * 0.025
            set_material("barge")
            if is_side:
                add_quad(
                    (flashing_inner_plane, start, flashing_top),
                    (outer_flashing_plane, start, flashing_top),
                    (outer_flashing_plane, start + width, flashing_top),
                    (flashing_inner_plane, start + width, flashing_top),
                )
                add_quad(
                    (outer_flashing_plane, start, flashing_bottom),
                    (outer_flashing_plane, start + width, flashing_bottom),
                    (outer_flashing_plane, start + width, flashing_top),
                    (outer_flashing_plane, start, flashing_top),
                )
                for edge in (start, start + width):
                    add_quad(
                        (flashing_inner_plane, edge, flashing_bottom),
                        (flashing_inner_plane, edge, flashing_top),
                        (outer_flashing_plane, edge, flashing_top),
                        (outer_flashing_plane, edge, flashing_bottom),
                    )
            else:
                add_quad(
                    (start, flashing_inner_plane, flashing_top),
                    (start + width, flashing_inner_plane, flashing_top),
                    (start + width, outer_flashing_plane, flashing_top),
                    (start, outer_flashing_plane, flashing_top),
                )
                add_quad(
                    (start, outer_flashing_plane, flashing_bottom),
                    (start, outer_flashing_plane, flashing_top),
                    (start + width, outer_flashing_plane, flashing_top),
                    (start + width, outer_flashing_plane, flashing_bottom),
                )
                for edge in (start, start + width):
                    add_quad(
                        (edge, flashing_inner_plane, flashing_bottom),
                        (edge, outer_flashing_plane, flashing_bottom),
                        (edge, outer_flashing_plane, flashing_top),
                        (edge, flashing_inner_plane, flashing_top),
                    )

    add_sliding_doors()

    def add_pa_doors():
        doors = data.get("pa_doors", [])
        if not doors:
            return
        back_offset = max(float(data.get("cladding_back_offset_m", 0.006) or 0.006), 0.002)
        z_front = float(data["z_positions"][0])
        z_back = float(data["z_positions"][-1])
        end_overhang = max(float(data.get("building_end_overhang_m", 0.25) or 0.25), 0.0)
        main_indices = data.get("main_top_indices", [])
        main_x = [float(data["nodes"][index]["x"]) for index in main_indices]
        if len(main_x) < 2:
            return
        main_left, main_right = min(main_x), max(main_x)

        for door in doors:
            wall = str(door.get("wall", "Left wall"))
            height = max(float(door.get("height_m", 2.1)), 0.1)
            width = max(float(door.get("width_m", 0.9)), 0.1)
            position = max(float(door.get("position_m", 0.0)), 0.0)
            opening = str(door.get("opening", "Open left"))
            is_side = wall in {"Left wall", "Right wall"}
            wall_span = (z_back - z_front) if is_side else (main_right - main_left)
            width = min(width, max(wall_span - position, 0.1))
            start = (z_front if is_side else main_left) + min(position, max(wall_span - width, 0.0))
            end = start + width
            if is_side:
                segment = next((item for item in data.get("wall_segments", []) if str(item.get("label", "")) == wall), None)
                if segment is None:
                    continue
                outward = -1.0 if wall == "Left wall" else 1.0
                face_plane = float(segment.get("sheet_x", segment.get("x", 0.0))) + outward * 0.035
                frame_plane = face_plane - outward * 0.020
            else:
                outward = -1.0 if wall == "Front wall" else 1.0
                face_plane = (z_front - end_overhang) if wall == "Front wall" else (z_back + end_overhang)
                face_plane += outward * 0.035
                frame_plane = face_plane - outward * 0.020

            set_material("wall_cladding")
            if is_side:
                add_quad((face_plane, start, 0.025), (face_plane, start, height), (face_plane, end, height), (face_plane, end, 0.025))
            else:
                add_quad((start, face_plane, 0.025), (end, face_plane, 0.025), (end, face_plane, height), (start, face_plane, height))
            set_material("cladding_back")
            back_plane = face_plane - outward * back_offset
            if is_side:
                add_quad((back_plane, start, 0.025), (back_plane, end, 0.025), (back_plane, end, height), (back_plane, start, height))
            else:
                add_quad((start, back_plane, 0.025), (start, back_plane, height), (end, back_plane, height), (end, back_plane, 0.025))

            set_material("wall_cladding")
            frame_size = 0.045
            if is_side:
                add_axis_box(frame_plane - outward * frame_size, frame_plane + outward * frame_size, start - frame_size, start, 0.0, height + frame_size)
                add_axis_box(frame_plane - outward * frame_size, frame_plane + outward * frame_size, end, end + frame_size, 0.0, height + frame_size)
                add_axis_box(frame_plane - outward * frame_size, frame_plane + outward * frame_size, start - frame_size, end + frame_size, height, height + frame_size)
                handle_z = height * 0.48
                handle_edge = start if opening == "Open right" else end
                set_material("galvanised")
                add_axis_box(face_plane + outward * 0.010, face_plane + outward * 0.050, handle_edge - 0.035, handle_edge + 0.035, handle_z - 0.035, handle_z + 0.035)
            else:
                set_material("wall_cladding")
                add_axis_box(start - frame_size, start, frame_plane - outward * frame_size, frame_plane + outward * frame_size, 0.0, height + frame_size)
                add_axis_box(end, end + frame_size, frame_plane - outward * frame_size, frame_plane + outward * frame_size, 0.0, height + frame_size)
                add_axis_box(start - frame_size, end + frame_size, frame_plane - outward * frame_size, frame_plane + outward * frame_size, height, height + frame_size)
                handle_z = height * 0.48
                handle_edge = start if opening == "Open right" else end
                set_material("galvanised")
                add_axis_box(handle_edge - 0.035, handle_edge + 0.035, face_plane + outward * 0.010, face_plane + outward * 0.050, handle_z - 0.035, handle_z + 0.035)

    add_pa_doors()
    set_material("steel")
    member_thickness = 0.08
    for z_m in data["z_positions"]:
        for line in data["frame_lines"]:
            group = str(line.get("group", ""))
            thickness = 0.12 if group in {"TOP", "BOTTOM", "LEFT_COLUMN", "RIGHT_COLUMN", "INTERNAL_COLUMN"} else member_thickness
            add_profile_member(point(line["a"], z_m), point(line["b"], z_m), line.get("profile", {}), thickness)

    storage = data.get("storage", {})
    main_top_indices = data.get("main_top_indices", [])
    if storage.get("enabled") and len(main_top_indices) >= 2:
        panel_height = max(float(storage.get("panel_height_m", 0.0)), 0.0)
        panel_thickness = max(float(storage.get("panel_thickness_m", 0.15)), 0.05)
        rail_height = max(float(storage.get("rail_height_m", panel_height)), 0.0)

        def column_depth(group):
            line = next((item for item in data.get("frame_lines", []) if str(item.get("group", "")).upper() == group), None)
            return max(float(((line or {}).get("profile") or {}).get("depth_mm") or 200.0) / 1000.0, 0.05)

        def column_flange_width(group):
            line = next((item for item in data.get("frame_lines", []) if str(item.get("group", "")).upper() == group), None)
            profile = (line or {}).get("profile") or {}
            return max(float(profile.get("flange_width_mm") or profile.get("depth_mm") or 150.0) / 1000.0, 0.05)

        left_column_x = float(data["nodes"][main_top_indices[0]]["x"])
        right_column_x = float(data["nodes"][main_top_indices[-1]]["x"])
        left_inside_x = left_column_x + column_depth("LEFT_COLUMN") / 2.0
        right_inside_x = right_column_x - column_depth("RIGHT_COLUMN") / 2.0

        wall_specs = []
        if storage.get("left_wall"):
            wall_specs.append((left_inside_x, -1.0))
        if storage.get("right_wall"):
            wall_specs.append((right_inside_x, 1.0))

        for inside_face_x, outward in wall_specs:
            panel_inner_x = inside_face_x - outward * panel_thickness
            set_material("storage_panel")
            for first_z, second_z in zip(data["z_positions"][:-1], data["z_positions"][1:]):
                add_axis_box(inside_face_x, panel_inner_x, first_z, second_z, 0.0, panel_height)

            rail_profile = storage.get("rail_profile", {})
            rail_depth = max(float(rail_profile.get("depth_mm") or rail_profile.get("flange_width_mm") or 100.0) / 1000.0, 0.05)
            rail_x = inside_face_x + outward * rail_depth / 2.0
            set_material("galvanised")
            add_profile_member(
                (rail_x, float(data["z_positions"][0]), rail_height),
                (rail_x, float(data["z_positions"][-1]), rail_height),
                rail_profile,
                0.10,
                quarter_turn=True,
            )
            if storage.get("centre_stub"):
                stub_profile = storage.get("stub_profile", {})
                for first_z, second_z in zip(data["z_positions"][:-1], data["z_positions"][1:]):
                    middle_z = (first_z + second_z) / 2.0
                    add_profile_member(
                        (rail_x, middle_z, 0.0),
                        (rail_x, middle_z, rail_height),
                        stub_profile,
                        0.10,
                    )

        end_specs = []
        end_column_half_width = max(column_flange_width("LEFT_COLUMN"), column_flange_width("RIGHT_COLUMN")) / 2.0
        if storage.get("front_wall"):
            end_specs.append((float(data["z_positions"][0]) + end_column_half_width, -1.0))
        if storage.get("back_wall"):
            end_specs.append((float(data["z_positions"][-1]) - end_column_half_width, 1.0))
        transverse_span = max(right_inside_x - left_inside_x, 0.1)
        transverse_spaces = max(1, math.ceil(transverse_span / 8.0))
        transverse_stations = [left_inside_x + transverse_span * index / transverse_spaces for index in range(transverse_spaces + 1)]
        for inside_face_y, outward_y in end_specs:
            panel_inner_y = inside_face_y - outward_y * panel_thickness
            set_material("storage_panel")
            for first_x, second_x in zip(transverse_stations[:-1], transverse_stations[1:]):
                add_axis_box(first_x, second_x, inside_face_y, panel_inner_y, 0.0, panel_height)
            rail_profile = storage.get("rail_profile", {})
            rail_depth = max(float(rail_profile.get("depth_mm") or rail_profile.get("flange_width_mm") or 100.0) / 1000.0, 0.05)
            rail_y = inside_face_y + outward_y * rail_depth / 2.0
            set_material("galvanised")
            add_profile_member(
                (left_inside_x, rail_y, rail_height),
                (right_inside_x, rail_y, rail_height),
                rail_profile,
                0.10,
                quarter_turn=True,
            )
            if storage.get("centre_stub"):
                stub_profile = storage.get("stub_profile", {})
                for first_x, second_x in zip(transverse_stations[:-1], transverse_stations[1:]):
                    middle_x = (first_x + second_x) / 2.0
                    add_profile_member(
                        (middle_x, rail_y, 0.0),
                        (middle_x, rail_y, rail_height),
                        stub_profile,
                        0.10,
                        quarter_turn=True,
                    )
    roof_indices = data.get("roof_indices", [])
    clad_end_surfaces = [surface for surface in data.get("end_wall_surfaces", []) if len(surface.get("points", [])) >= 4]

    def surface_top_outline(surface):
        points = surface.get("points", [])
        return [(float(point["x"]), float(point["y"])) for point in points[1:-1]]

    def surface_height_at_x(surface, x_value):
        candidates = []
        for first, second in zip(surface_top_outline(surface)[:-1], surface_top_outline(surface)[1:]):
            if min(first[0], second[0]) - 1e-9 <= x_value <= max(first[0], second[0]) + 1e-9:
                fraction = 0.0 if abs(second[0] - first[0]) <= 1e-9 else (x_value - first[0]) / (second[0] - first[0])
                candidates.append(first[1] + (second[1] - first[1]) * fraction)
        return max(candidates or [0.0])

    for surface in clad_end_surfaces:
        outline = surface_top_outline(surface)
        x1, x2 = min(point[0] for point in outline), max(point[0] for point in outline)
        mullion_target_spacing = max(float(data.get("end_mullion_target_spacing_m", 8.0) or 8.0), 0.1)
        spaces = max(1, math.ceil(abs(x2 - x1) / mullion_target_spacing))
        end_y = float(surface["points"][0].get("z", 0.0))
        set_material("galvanised")
        mullion_profile = data.get("end_mullion_profile", {})
        for index in range(1, spaces):
            x_value = x1 + (x2 - x1) * index / spaces
            add_profile_member(
                (x_value, end_y, 0.0),
                (x_value, end_y, surface_height_at_x(surface, x_value)),
                mullion_profile,
                0.12,
            )
    set_material("galvanised")
    end_overhang = max(float(data.get("building_end_overhang_m", 0.25) or 0.25), 0.0)
    for index, (z_a, z_b) in enumerate(zip(data["z_positions"][:-1], data["z_positions"][1:])):
        roof_z_a = z_a - end_overhang if index == 0 else z_a
        roof_z_b = z_b + end_overhang if index == len(data["z_positions"]) - 2 else z_b
        for purlin in data.get("roof_purlins", []):
            x = float(purlin.get("x", 0.0)) + float(purlin.get("offset_x", 0.0))
            y = float(purlin.get("y", 0.0)) + float(purlin.get("offset_y", 0.0)) - float(purlin.get("eave_drop_m", 0.0))
            add_cold_formed_member(
                concept_point(x, y, roof_z_a),
                concept_point(x, y, roof_z_b),
                purlin.get("profile", {}),
                reverse_lips=bool(purlin.get("reverse_lips", False)),
                section_normal=(
                    float(purlin.get("normal_x", purlin.get("offset_x", 0.0))),
                    0.0,
                    float(purlin.get("normal_y", purlin.get("offset_y", 1.0))),
                ),
            )
        for girt in data.get("wall_girts", []):
            x = float(girt.get("x", 0.0)) + float(girt.get("offset_x", 0.0))
            y = float(girt.get("y", 0.0)) + float(girt.get("offset_y", 0.0))
            add_cold_formed_member(concept_point(x, y, z_a), concept_point(x, y, z_b), girt.get("profile", {}), open_down=True)

    end_girt_profile = (data.get("wall_girts") or [{}])[0].get("profile", {})
    for surface in clad_end_surfaces:
        outline = surface_top_outline(surface)
        end_y = float(surface["points"][0].get("z", 0.0))
        outward = -1.0 if str(surface.get("label", "")).lower().endswith("front") else 1.0
        max_height = max(point[1] for point in outline)
        base_height = max(float(surface["points"][0].get("y", 0.0)), float(surface["points"][-1].get("y", 0.0)))
        end_girt_spacing = max(float(data.get("end_girt_spacing_m", 1.5) or 1.5), 0.1)
        first_regular_girt = max(end_girt_spacing, math.ceil(base_height / end_girt_spacing) * end_girt_spacing)
        girt_heights = sorted(set([base_height] + list(np.arange(first_regular_girt, max_height + 1e-9, end_girt_spacing))))
        for height in girt_heights:
            crossings = []
            if height <= outline[0][1] + 1e-9:
                crossings.append(outline[0][0])
            if height <= outline[-1][1] + 1e-9:
                crossings.append(outline[-1][0])
            for first, second in zip(outline[:-1], outline[1:]):
                if min(first[1], second[1]) - 1e-9 <= height <= max(first[1], second[1]) + 1e-9 and abs(second[1] - first[1]) > 1e-9:
                    crossings.append(first[0] + (second[0] - first[0]) * (height - first[1]) / (second[1] - first[1]))
            if len(crossings) < 2:
                continue
            girt_y = end_y + outward * 0.14
            add_cold_formed_member(
                (min(crossings), girt_y, float(height)),
                (max(crossings), girt_y, float(height)),
                end_girt_profile,
                open_down=True,
            )

    set_material("gutter")
    roof_offset = max(float(data.get("roof_cladding_offset_m", 0.09) or 0.09), 0.0)
    roof_profile_name = data.get("finishes", {}).get("roof_profile", "5-Rib (Trimdek)")
    wall_profile_name = data.get("finishes", {}).get("wall_profile", "5-Rib (Trimdek)")
    roof_profile_height = 0.016 if str(roof_profile_name).startswith("Corry") else 0.029
    wall_profile_height = 0.016 if str(wall_profile_name).startswith("Corry") else 0.029
    trim_z0 = data["z_positions"][0] - end_overhang
    trim_z1 = data["z_positions"][-1] + end_overhang

    def shifted_roof_pair(a_index, b_index, y_m):
        a = point(a_index, y_m)
        b = point(b_index, y_m)
        dx = b[0] - a[0]
        dz = b[2] - a[2]
        normal = normalize((-dz, 0.0, dx))
        if normal[2] < 0.0:
            normal = scale(normal, -1.0)
        shifted_a = add_scaled(a, (normal, roof_offset))
        shifted_b = add_scaled(b, (normal, roof_offset))
        base_shifted_a, base_shifted_b = shifted_a, shifted_b

        def extend_shifted(target_x):
            if abs(base_shifted_b[0] - base_shifted_a[0]) <= 1e-9:
                return (target_x, y_m, base_shifted_a[2])
            fraction = (target_x - base_shifted_a[0]) / (base_shifted_b[0] - base_shifted_a[0])
            return (target_x, y_m, base_shifted_a[2] + (base_shifted_b[2] - base_shifted_a[2]) * fraction)

        for node_index, shifted_name in ((a_index, "a"), (b_index, "b")):
            node_x = float(data["nodes"][node_index]["x"])
            matching_wall = next(
                (segment for segment in data.get("wall_segments", []) if abs(float(segment.get("x", 0.0)) - node_x) <= 0.025),
                None,
            )
            if not matching_wall:
                continue
            target_x = float(matching_wall.get("roof_edge_x", matching_wall.get("sheet_x", node_x)))
            if shifted_name == "a":
                shifted_a = extend_shifted(target_x)
            else:
                shifted_b = extend_shifted(target_x)
        return shifted_a, shifted_b

    if len(roof_indices) >= 2:
        first_a, _ = shifted_roof_pair(roof_indices[0], roof_indices[1], trim_z0)
        _, last_b = shifted_roof_pair(roof_indices[-2], roof_indices[-1], trim_z0)

        def gutter_eave_point(eave, outward):
            candidates = [
                purlin for purlin in data.get("roof_purlins", [])
                if bool(purlin.get("reverse_lips", False))
            ] or list(data.get("roof_purlins", []))
            if not candidates:
                return eave
            purlin = min(candidates, key=lambda item: float(item.get("x", 0.0))) if outward < 0.0 else max(
                candidates, key=lambda item: float(item.get("x", 0.0))
            )
            profile = purlin.get("profile", {})
            flange_width = max(float(profile.get("flange_width_mm") or 60.0) / 1000.0, 0.02)
            centre_x = float(purlin.get("x", 0.0)) + float(purlin.get("offset_x", 0.0))
            outside_face = centre_x + outward * (flange_width / 2.0 + 0.025)
            target_x = min(eave[0], outside_face) if outward < 0.0 else max(eave[0], outside_face)
            return (target_x, eave[1], eave[2])

        gutter_points = [
            (gutter_eave_point(first_a, -1.0), -1.0),
            (gutter_eave_point(last_b, 1.0), 1.0),
        ]
        for eave, outward in gutter_points:
            set_material("gutter")
            gutter_profile = [
                (eave[0], eave[2]),
                (eave[0], eave[2] - 0.15),
                (eave[0] + outward * 0.156, eave[2] - 0.15),
                (eave[0] + outward * 0.20, eave[2] - 0.027),
            ]
            for first, second in zip(gutter_profile[:-1], gutter_profile[1:]):
                add_quad(
                    (first[0], trim_z0, first[1]), (second[0], trim_z0, second[1]),
                    (second[0], trim_z1, second[1]), (first[0], trim_z1, first[1]),
                )
            for end_y in (trim_z0, trim_z1):
                for index in range(1, len(gutter_profile) - 1):
                    add_triangle(
                        (gutter_profile[0][0], end_y, gutter_profile[0][1]),
                        (gutter_profile[index][0], end_y, gutter_profile[index][1]),
                        (gutter_profile[index + 1][0], end_y, gutter_profile[index + 1][1]),
                    )

        purlin_depth = max(
            [float((item.get("profile") or {}).get("depth_mm") or 0.0) / 1000.0 for item in data.get("roof_purlins", [])]
            or [0.15]
        )
        barge_wall_depth = max(0.15, purlin_depth + 0.05)
        set_material("barge")
        for end_y, inward in ((trim_z0, 1.0), (trim_z1, -1.0)):
            barge_y = end_y - inward * (wall_profile_height + 0.035)
            for a_index, b_index in zip(roof_indices[:-1], roof_indices[1:]):
                trim_a, trim_b = shifted_roof_pair(a_index, b_index, barge_y)
                dx, dz = trim_b[0] - trim_a[0], trim_b[2] - trim_a[2]
                slope_direction = normalize((dx, 0.0, dz))
                roof_normal = normalize((-dz, 0.0, dx))
                if roof_normal[2] < 0.0:
                    roof_normal = scale(roof_normal, -1.0)
                top_a = add_scaled(trim_a, (roof_normal, roof_profile_height + 0.035))
                top_b = add_scaled(trim_b, (roof_normal, roof_profile_height + 0.035))
                a_position = roof_indices.index(a_index)
                b_position = roof_indices.index(b_index)
                if 0 < a_position < len(roof_indices) - 1:
                    a_height = float(data["nodes"][a_index]["y"])
                    if a_height >= max(float(data["nodes"][roof_indices[a_position - 1]]["y"]), float(data["nodes"][b_index]["y"])) - 1e-6:
                        top_a = add_scaled(top_a, (slope_direction, -0.12))
                if 0 < b_position < len(roof_indices) - 1:
                    b_height = float(data["nodes"][b_index]["y"])
                    if b_height >= max(float(data["nodes"][a_index]["y"]), float(data["nodes"][roof_indices[b_position + 1]]["y"])) - 1e-6:
                        top_b = add_scaled(top_b, (slope_direction, 0.12))
                add_quad(
                    top_a, top_b,
                    add(top_b, (0.0, inward * 0.15, 0.0)),
                    add(top_a, (0.0, inward * 0.15, 0.0)),
                )
                add_quad(
                    top_a, top_b,
                    add(top_b, (0.0, 0.0, -barge_wall_depth)),
                    add(top_a, (0.0, 0.0, -barge_wall_depth)),
                )

        set_material("ridge_capping")
        for index in range(1, len(roof_indices) - 1):
            left_node = data["nodes"][roof_indices[index - 1]]
            node = data["nodes"][roof_indices[index]]
            right_node = data["nodes"][roof_indices[index + 1]]
            if float(node["y"]) + 1e-6 < max(float(left_node["y"]), float(right_node["y"])):
                continue
            left_eave, left_peak = shifted_roof_pair(roof_indices[index - 1], roof_indices[index], trim_z0)
            right_peak, right_eave = shifted_roof_pair(roof_indices[index], roof_indices[index + 1], trim_z0)

            def raised_roof_line(first, second):
                direction = sub(second, first)
                normal = normalize((-direction[2], 0.0, direction[0]))
                if normal[2] < 0.0:
                    normal = scale(normal, -1.0)
                return (
                    add_scaled(first, (normal, roof_profile_height + 0.015)),
                    add_scaled(second, (normal, roof_profile_height + 0.015)),
                )

            left_line = raised_roof_line(left_eave, left_peak)
            right_line = raised_roof_line(right_peak, right_eave)
            x1, z1, x2, z2 = left_line[0][0], left_line[0][2], left_line[1][0], left_line[1][2]
            x3, z3, x4, z4 = right_line[0][0], right_line[0][2], right_line[1][0], right_line[1][2]
            denominator = (x1 - x2) * (z3 - z4) - (z1 - z2) * (x3 - x4)
            if abs(denominator) <= 1e-9:
                ridge_x = (left_line[1][0] + right_line[0][0]) / 2.0
                ridge_height = max(left_line[1][2], right_line[0][2])
            else:
                determinant1 = x1 * z2 - z1 * x2
                determinant2 = x3 * z4 - z3 * x4
                ridge_x = (determinant1 * (x3 - x4) - (x1 - x2) * determinant2) / denominator
                ridge_height = (determinant1 * (z3 - z4) - (z1 - z2) * determinant2) / denominator
            ridge_start = (ridge_x, trim_z0, ridge_height)
            ridge_end = (ridge_x, trim_z1, ridge_height)
            for outer_point in (left_line[0], right_line[1]):
                down_slope = normalize((outer_point[0] - ridge_x, 0.0, outer_point[2] - ridge_height))
                flap_start = add_scaled(ridge_start, (down_slope, 0.22))
                flap_end = (flap_start[0], trim_z1, flap_start[2])
                add_quad(ridge_start, flap_start, flap_end, ridge_end)

        set_material("corner_trim")
        storage = data.get("storage", {})
        for segment in data.get("wall_segments", []):
            label = str(segment.get("label", "")).lower()
            if "left" not in label and "right" not in label:
                continue
            sheet_x = float(segment.get("sheet_x", segment.get("x", 0.0)))
            top_height = max(float(segment.get("y1", 0.0)), float(segment.get("y2", 0.0)))
            outward_x = -1.0 if "left" in label else 1.0
            inward_x = -outward_x
            for end_y, outward_y, inward_y, storage_end in (
                (trim_z0, -1.0, 1.0, "front_wall"),
                (trim_z1, 1.0, -1.0, "back_wall"),
            ):
                corner_x = sheet_x + outward_x * (wall_profile_height + 0.035)
                corner_y = end_y + outward_y * (wall_profile_height + 0.035)
                base_height = float(storage.get("panel_height_m", 0.0)) if storage.get(storage_end) else 0.0
                add_quad(
                    (corner_x, corner_y, base_height),
                    (corner_x, corner_y, top_height),
                    (corner_x, corner_y + inward_y * 0.15, top_height),
                    (corner_x, corner_y + inward_y * 0.15, base_height),
                )
                add_quad(
                    (corner_x, corner_y, base_height),
                    (corner_x + inward_x * 0.15, corner_y, base_height),
                    (corner_x + inward_x * 0.15, corner_y, top_height),
                    (corner_x, corner_y, top_height),
                )

    if return_mesh:
        return {"vertices": vertices, "triangles": triangles}

    positions = " ".join(f"{coord:.6f}" for vertex in vertices for coord in vertex)
    vertex_count = len(vertices)
    material_triangle_xml = []
    material_names = [
        "roof_cladding", "wall_cladding", "cladding_back", "skylight",
        "steel", "galvanised", "storage_panel",
        "gutter", "barge", "ridge_capping", "corner_trim",
    ]
    for material in material_names:
        material_tris = [tri for tri_material, tri in triangles if tri_material == material]
        if not material_tris:
            continue
        indices = " ".join(str(index) for tri in material_tris for index in tri)
        material_triangle_xml.append(
            f"""        <triangles material="{material}-material" count="{len(material_tris)}">
      <input semantic="VERTEX" source="#portalcalc-vertices" offset="0"/>
      <p>{indices}</p>
    </triangles>"""
        )
    triangles_xml = "\n".join(material_triangle_xml)
    title = html.escape(data.get("title", "PortalCalc 3D concept"))
    finishes = data.get("finishes", {})

    def dae_color(hex_value, alpha=1.0):
        color = QColor(str(hex_value))
        return f"{color.redF():.4f} {color.greenF():.4f} {color.blueF():.4f} {alpha:.4f}"

    material_specs = {
        "roof_cladding": ("Roof Cladding", dae_color(finishes.get("roof_hex", "#323233")), 1.0),
        "wall_cladding": ("Wall Cladding", dae_color(finishes.get("walls_hex", "#323233")), 1.0),
        "cladding_back": ("Surfmist Cladding Back", dae_color(finishes.get("cladding_back_hex", "#E4E2D5")), 1.0),
        "skylight": ("Transparent Skylight", dae_color("#B8DCE6", 0.45), 0.45),
        "steel": ("Primary Steel", dae_color("#879397"), 1.0),
        "galvanised": ("Galvanised Purlins and Girts", dae_color("#B6BFC1"), 1.0),
        "storage_panel": ("Storage Panel", dae_color("#AEB3B8"), 1.0),
        "gutter": ("Gutter", dae_color(finishes.get("gutter_hex", "#323233")), 1.0),
        "barge": ("Barge", dae_color(finishes.get("barge_hex", "#323233")), 1.0),
        "ridge_capping": ("Ridge Capping", dae_color(finishes.get("ridge_capping_hex", "#323233")), 1.0),
        "corner_trim": ("Corner Trim", dae_color(finishes.get("corner_trim_hex", "#323233")), 1.0),
    }
    effects_xml = "\n".join(
        f'''    <effect id="{material}-effect"><profile_COMMON><technique sid="common"><lambert>
  <diffuse><color>{rgba}</color></diffuse>{f'<transparent opaque="A_ONE"><color>1 1 1 {alpha:.4f}</color></transparent><transparency><float>{alpha:.4f}</float></transparency>' if alpha < 1.0 else ''}
</lambert></technique></profile_COMMON></effect>'''
        for material, (_, rgba, alpha) in material_specs.items()
    )
    materials_xml = "\n".join(
        f'    <material id="{material}-material" name="{name}"><instance_effect url="#{material}-effect"/></material>'
        for material, (name, _, _) in material_specs.items()
    )
    bindings_xml = "\n".join(
        f'              <instance_material symbol="{material}-material" target="#{material}-material"/>'
        for material in material_names
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <asset>
<contributor><authoring_tool>PortalCalc</authoring_tool></contributor>
<unit name="meter" meter="1"/>
<up_axis>Z_UP</up_axis>
  </asset>
  <library_effects>
{effects_xml}
  </library_effects>
  <library_materials>
{materials_xml}
  </library_materials>
  <library_geometries>
<geometry id="portalcalc-geometry" name="{title}">
  <mesh>
    <source id="portalcalc-positions">
      <float_array id="portalcalc-positions-array" count="{vertex_count * 3}">{positions}</float_array>
      <technique_common>
        <accessor source="#portalcalc-positions-array" count="{vertex_count}" stride="3">
          <param name="X" type="float"/>
          <param name="Y" type="float"/>
          <param name="Z" type="float"/>
        </accessor>
      </technique_common>
    </source>
    <vertices id="portalcalc-vertices">
      <input semantic="POSITION" source="#portalcalc-positions"/>
    </vertices>
{triangles_xml}
  </mesh>
</geometry>
  </library_geometries>
  <library_visual_scenes>
<visual_scene id="Scene" name="Scene">
  <node id="PortalCalcConcept" name="PortalCalc Concept">
    <instance_geometry url="#portalcalc-geometry">
      <bind_material>
        <technique_common>
{bindings_xml}
        </technique_common>
      </bind_material>
    </instance_geometry>
  </node>
</visual_scene>
  </library_visual_scenes>
  <scene>
<instance_visual_scene url="#Scene"/>
  </scene>
</COLLADA>
"""



def export_3d_concept(self):
    try:
        data = self.concept_3d_data()
    except (TypeError, ValueError) as exc:
        QMessageBox.warning(self, "3D concept export", str(exc))
        return
    default_path = os.path.join(os.getcwd(), "portal_3d_concept.dae")
    path, selected_filter = QFileDialog.getSaveFileName(
        self,
        "Export 3D concept",
        default_path,
        "SketchUp Collada (*.dae);;HTML files (*.html);;All files (*.*)",
    )
    if not path:
        return
    if not os.path.splitext(path)[1]:
        path += ".dae" if selected_filter.startswith("SketchUp") else ".html"
    try:
        extension = os.path.splitext(path)[1].lower()
        content = self.concept_3d_dae(data) if extension == ".dae" else self.concept_3d_html(data)
        with open(path, "w", encoding="utf-8") as file:
            file.write(content)
    except OSError as exc:
        QMessageBox.critical(self, "3D concept export failed", str(exc))
        return
    QMessageBox.information(self, "3D concept exported", f"Exported:\n{path}")

