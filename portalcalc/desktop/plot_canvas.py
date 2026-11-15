"""The 2D frame diagram canvas (geometry, loads, deflections, force diagrams)."""

import math

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure


# ==========================================================
# PLOT CANVAS
# ==========================================================

class PlotCanvas(FigureCanvasQTAgg):

    def __init__(self):
        self.fig = Figure(figsize=(12, 8))
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)

    def element_plot_style(self, group):
        group = str(group or "").upper()
        if group in {"TOP", "LEFT_CANOPY_TOP", "RIGHT_CANOPY_TOP"}:
            return {"color": "#1f77b4", "linewidth": 2.4, "zorder": 3}
        if group in {"BOTTOM", "LEFT_CANOPY_BOTTOM", "RIGHT_CANOPY_BOTTOM"}:
            return {"color": "#2ca02c", "linewidth": 2.2, "zorder": 3}
        if group in {"LEFT_COLUMN", "RIGHT_COLUMN", "INTERNAL_COLUMN", "LEFT_LEAN_COLUMN", "RIGHT_LEAN_COLUMN", "COLUMN"}:
            return {"color": "#2f6f73", "linewidth": 2.6, "zorder": 4}
        if group in {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB", "POST", "APEX", "LEFT_CANOPY_POST", "RIGHT_CANOPY_POST"}:
            return {"color": "#666666", "linewidth": 1.2, "zorder": 2}
        return {"color": "#555555", "linewidth": 1.4, "zorder": 2}

    def plot_structure(self, nodes, elements, base_nodes, support_types, load_arrows,
                       result_type="Deflection", displacements=None, scale=100, diagram_scale_factor=1.0,
                       purlin_layout=None, wall_cladding_segments=None, doubled_chord_zones=None,
                       governing_deflection=None, analysis_structure=None, column_flybraces=None):
        self.ax.clear()

        for element in elements:
            x1 = element.start.x
            y1 = element.start.y
            x2 = element.end.x
            y2 = element.end.y
            style = self.element_plot_style(element.group)
            self.ax.plot([x1, x2], [y1, y2], **style)

        if result_type == "Geometry":
            self.plot_doubled_chord_zones(doubled_chord_zones or [])
            self.plot_governing_deflection(governing_deflection, nodes, elements, scale)
        elif result_type == "Load Diagram":
            self.plot_load_diagram_visual(load_arrows, diagram_scale_factor)
        elif result_type == "Deflection" and displacements is not None:
            self.plot_deflected_shape(
                elements,
                displacements,
                scale,
                element_load_state=(analysis_structure.element_load_snapshot() if analysis_structure else None),
            )
        elif result_type in ["Bending Moment", "Shear Force", "Axial Compression"] and displacements is not None:
            self.plot_force_diagram(
                elements,
                displacements,
                result_type,
                diagram_scale_factor,
                analysis_structure,
            )

        plotted_node_points = set()
        for node in nodes:
            key = (round(node.x, 6), round(node.y, 6))
            if key in plotted_node_points:
                continue
            plotted_node_points.add(key)
            self.ax.plot(node.x, node.y, marker="o", markersize=3, color="#222222", zorder=5)

        if purlin_layout is not None:
            self.plot_purlins(purlin_layout)
        if wall_cladding_segments:
            self.plot_wall_cladding(wall_cladding_segments)
        if result_type == 'Geometry':
            for schedule in column_flybraces or []:
                colour = '#9e2876' if schedule['passes'] else '#c43a25'
                for y in schedule['positions_mm']:
                    self.ax.plot(schedule['x_mm'], y, marker='>', markersize=7, color=colour, zorder=8)
                    self.ax.annotate(f"FB {y-schedule['base_mm']:.0f}", (schedule['x_mm'], y),
                                     xytext=(8, 5), textcoords='offset points', fontsize=7, color=colour)

        self.plot_supports(base_nodes, support_types)
        if result_type != "Geometry":
            self.plot_load_arrows(load_arrows, show_labels=(result_type == "Load Diagram"))

        self.ax.set_aspect("equal")
        self.ax.grid(True)
        self.ax.set_xlabel("X (mm)")
        self.ax.set_ylabel("Y (mm)")
        self.ax.set_title(f"2D Warren Portal Frame Analysis — {result_type}")
        if result_type == "Deflection":
            self.ax.text(0.01, 0.98, f"Deflection exaggerated x{scale:g}", transform=self.ax.transAxes,
                         va="top", ha="left", fontsize=8,
                         bbox={"boxstyle": "round,pad=0.25", "fc": "white", "alpha": 0.75})
        elif result_type in ["Load Diagram", "Bending Moment", "Shear Force", "Axial Compression"]:
            self.ax.text(0.01, 0.98, f"Diagram scale factor x{diagram_scale_factor:g}", transform=self.ax.transAxes,
                         va="top", ha="left", fontsize=8,
                         bbox={"boxstyle": "round,pad=0.25", "fc": "white", "alpha": 0.75})
        self.draw()

    def plot_governing_deflection(self, item, nodes, elements, scale):
        if not item or not nodes or item.get("displacements") is None:
            return
        displacements = item["displacements"]
        x = float(item.get("x_mm", 0.0))
        y = float(item.get("y_mm", 0.0))
        signed_value = float(item.get("signed_value_mm", item.get("value_mm", 0.0)) or 0.0)
        color = "#b42318" if item.get("status") == "NOT OK" else "#c45a00"
        self.plot_deflected_shape(
            elements,
            displacements,
            scale,
            color=color,
            linewidth=2.0,
            zorder=8,
            element_load_state=item.get("element_load_state"),
        )
        plotted_points = set()
        for node in nodes:
            key = (round(node.x, 6), round(node.y, 6))
            if key in plotted_points:
                continue
            plotted_points.add(key)
            self.ax.plot(
                node.x + displacements[node.ux] * scale,
                node.y + displacements[node.uy] * scale,
                marker="o",
                markersize=3.5,
                color=color,
                zorder=9,
            )
        self.ax.scatter([x], [y], s=70, facecolors="white", edgecolors=color, linewidths=2.0, zorder=10)
        group = str(item.get("group", "")).replace("_", " ").title()
        location = item.get("kind", "Governing deflection")
        if group:
            location = f"{location} - {group}"
        value = float(item.get("value_mm", 0.0))
        limit = float(item.get("limit_mm", 0.0))
        ratio = float(item.get("ratio", 0.0))
        combo = str(item.get("combo", "-"))
        direction = "dy" if item.get("direction") == "vertical" else "dx"
        self.ax.text(
            0.99,
            0.02,
            "\n".join([
                "Governing frame deflection",
                location,
                f"{direction} = {signed_value:+.2f} mm | |{direction}| = {value:.2f} mm",
                f"Limit {limit:.2f} mm | utilisation {ratio:.3f} | {item.get('status', '-')}",
                combo,
                f"Deflected shape exaggerated x{scale:g}",
            ]),
            transform=self.ax.transAxes,
            va="bottom",
            ha="right",
            fontsize=8,
            color="#402020",
            bbox={"boxstyle": "round,pad=0.35", "fc": "white", "ec": color, "alpha": 0.92},
            zorder=11,
        )

    def plot_doubled_chord_zones(self, doubled_chord_zones):
        if not doubled_chord_zones:
            return
        show_segment_labels = len(doubled_chord_zones) <= 10
        for zone in doubled_chord_zones:
            x1 = zone.get("x1", 0.0)
            y1 = zone.get("y1", 0.0)
            x2 = zone.get("x2", 0.0)
            y2 = zone.get("y2", 0.0)
            self.ax.plot(
                [x1, x2],
                [y1, y2],
                color="#d95f02",
                linewidth=6.0,
                alpha=0.45,
                solid_capstyle="round",
                zorder=6,
            )
            if show_segment_labels:
                mx = (x1 + x2) / 2.0
                my = (y1 + y2) / 2.0
                label = f"2x M{zone.get('member', '-')}"
                self.ax.text(mx, my, label, fontsize=7, color="#8a3a00", ha="center", va="bottom", zorder=7)
        if not show_segment_labels:
            groups = {}
            for zone in doubled_chord_zones:
                group = str(zone.get("group", "Chord")).replace("_", " ").title()
                groups[group] = groups.get(group, 0) + 1
            summary = ", ".join(f"{name}: {count}" for name, count in sorted(groups.items()))
            self.ax.text(
                0.99,
                0.98,
                f"Doubled chord zones ({len(doubled_chord_zones)}): {summary}",
                transform=self.ax.transAxes,
                va="top",
                ha="right",
                fontsize=8,
                color="#6f3000",
                bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "#d95f02", "alpha": 0.85},
                zorder=8,
            )

    def plot_purlins(self, purlin_layout):
        for x, y in purlin_layout.points:
            self.ax.plot(x, y, marker="s", markersize=4, color="#7a4c00")
            self.ax.plot([x, x], [y - 180, y + 180], color="#7a4c00", linewidth=1.1)

    def plot_wall_cladding(self, wall_cladding_segments):
        for segment in wall_cladding_segments:
            x = segment["x"]
            y1 = min(segment["y1"], segment["y2"])
            y2 = max(segment["y1"], segment["y2"])
            self.ax.plot([x, x], [y1, y2], color="#2f6f73", linewidth=3.0, alpha=0.65)
            height = max(y2 - y1, 0.0)
            if height <= 1e-9:
                continue
            tick = 260.0
            levels = segment.get('girt_levels_mm')
            if levels is None:
                levels = [y1 + i*1500.0 for i in range(int(height/1500.0)+1)]
            for y in levels:
                self.ax.plot([x - tick, x + tick], [y, y], color="#2f6f73", linewidth=1.1, alpha=0.85)

    def plot_deflected_shape(
        self,
        elements,
        displacements,
        scale,
        color=None,
        linewidth=1.4,
        zorder=6,
        element_load_state=None,
    ):
        for element in elements:
            style = {"linestyle": "--", "linewidth": linewidth, "zorder": zorder}
            if color is not None:
                style["color"] = color
            uniform_load = (element_load_state or {}).get(element.id, (0.0, 0.0))
            points = []
            for index in range(21):
                t = index / 20.0
                ux, uy = element.global_displacement_at(displacements, t, uniform_load)
                points.append((
                    element.start.x + t * (element.end.x - element.start.x) + ux * scale,
                    element.start.y + t * (element.end.y - element.start.y) + uy * scale,
                ))
            self.ax.plot([point[0] for point in points], [point[1] for point in points], **style)

    def plot_force_diagram(
        self,
        elements,
        displacements,
        result_type,
        diagram_scale_factor=1.0,
        analysis_structure=None,
    ):
        load_for = (
            analysis_structure.uniform_load_for_element
            if analysis_structure is not None
            else lambda _element: (0.0, 0.0)
        )
        summaries = [el.force_summary(displacements, load_for(el)) for el in elements]

        if result_type == "Bending Moment":
            max_value = max([s["moment_knm"] for s in summaries] + [1e-9])
            unit = "kNm"
        elif result_type == "Shear Force":
            max_value = max([s["shear_kn"] for s in summaries] + [1e-9])
            unit = "kN"
        else:
            max_value = max([s["compression_kn"] for s in summaries] + [1e-9])
            unit = "kN"

        all_x = [n for el in elements for n in [el.start.x, el.end.x]]
        structure_span = max(all_x) - min(all_x)
        diagram_scale = (max(structure_span * 0.04, 600) / max_value) * diagram_scale_factor

        for element, summary in zip(elements, summaries):
            x1 = element.start.x
            y1 = element.start.y
            x2 = element.end.x
            y2 = element.end.y
            dx = x2 - x1
            dy = y2 - y1
            L = math.sqrt(dx**2 + dy**2)
            if L == 0:
                continue

            nx = -dy / L
            ny = dx / L
            diagram_points = []
            station_values = []
            for index in range(21):
                t = index / 20.0
                axial_n, shear_n, moment_nmm = element.section_forces_at(
                    displacements,
                    t,
                    load_for(element),
                )
                if result_type == "Bending Moment":
                    value = moment_nmm / 1_000_000.0
                    label_value = summary["moment_knm"]
                elif result_type == "Shear Force":
                    value = shear_n / 1000.0
                    label_value = summary["shear_kn"]
                else:
                    value = max(-axial_n, 0.0) / 1000.0
                    label_value = summary["compression_kn"]
                px = x1 + t * dx
                py = y1 + t * dy
                diagram_points.append((px + nx * value * diagram_scale, py + ny * value * diagram_scale))
                station_values.append(value)

            self.ax.plot(
                [x1] + [point[0] for point in diagram_points] + [x2],
                [y1] + [point[1] for point in diagram_points] + [y2],
                linestyle="--",
                linewidth=1.2,
            )
            label_index = max(range(len(station_values)), key=lambda index: abs(station_values[index]))
            mx, my = diagram_points[label_index]
            self.ax.text(mx, my, f"M{element.id}: {label_value:.1f} {unit}", fontsize=7)

    def plot_supports(self, base_nodes, support_types):
        if not base_nodes:
            return
        span = abs(base_nodes[1].x - base_nodes[0].x)
        size = max(span * 0.015, 250)

        for node, support_type in zip(base_nodes, support_types):
            x = node.x
            y = node.y
            if support_type == "Pinned":
                self.ax.plot([x - size, x, x + size, x - size], [y - size, y, y - size, y - size])
            elif support_type == "Fixed":
                self.ax.plot([x - size, x + size], [y, y], linewidth=3)
                for i in range(5):
                    hx = x - size + i * (2 * size / 4)
                    self.ax.plot([hx - size * 0.2, hx + size * 0.2], [y - size * 0.5, y], linewidth=1)
            elif support_type == "Spring":
                self.ax.plot([x - size, x, x + size, x - size], [y - size, y, y - size, y - size])
                coils = 5
                xs = [x]
                ys = [y]
                for i in range(coils * 2):
                    xs.append(x + (-1 if i % 2 == 0 else 1) * size * 0.18)
                    ys.append(y - size * 0.35 - size * i / (coils * 2 - 1))
                xs.append(x)
                ys.append(y - size * 1.35)
                self.ax.plot(xs, ys, linewidth=1.5)
                self.ax.plot([x - size * 0.35, x + size * 0.35], [y - size * 1.35, y - size * 1.35], linewidth=2)
            elif support_type == "X restraint":
                self.ax.plot([x, x], [y - size * 0.65, y + size * 0.65], linewidth=2)
                self.ax.plot([x - size * 0.4, x], [y - size * 0.4, y], linewidth=1)
                self.ax.plot([x - size * 0.4, x], [y, y + size * 0.4], linewidth=1)
            elif support_type == "X spring":
                coils = 5
                xs = [x]
                ys = [y]
                for i in range(coils * 2):
                    xs.append(x + (-1 if i % 2 == 0 else 1) * size * 0.18)
                    ys.append(y - size * 0.5 + size * i / (coils * 2 - 1))
                xs.append(x)
                ys.append(y + size * 0.5)
                self.ax.plot(xs, ys, linewidth=1.5)
                self.ax.plot([x - size * 0.35, x + size * 0.35], [y + size * 0.5, y + size * 0.5], linewidth=2)

    def combined_load_arrows(self, load_arrows):
        """
        Combine coincident load arrows before plotting.

        This avoids the old sawtooth effect when, for example, dead load and wind
        load were drawn as separate arrows at the same roof panel midpoint.
        """
        combined = {}
        for x, y, fx, fy in load_arrows:
            key = (round(x, 6), round(y, 6))
            old_fx, old_fy = combined.get(key, (0.0, 0.0))
            combined[key] = (old_fx + fx, old_fy + fy)
        return [(x, y, fx, fy) for (x, y), (fx, fy) in combined.items()]

    def plot_load_diagram_visual(self, load_arrows, diagram_scale_factor=1.0):
        """Draw a scaled resultant load diagram using combined loads."""
        combined = self.combined_load_arrows(load_arrows)
        combined = [(x, y, fx, fy) for x, y, fx, fy in combined if math.sqrt(fx**2 + fy**2) > 1e-9]
        if not combined:
            return

        max_mag = max([math.sqrt(fx**2 + fy**2) for _, _, fx, fy in combined] + [1e-9])
        all_x = [x for x, _, _, _ in combined]
        structure_span = max(all_x) - min(all_x) if len(all_x) > 1 else 10000
        diagram_scale = (max(structure_span * 0.04, 600) / max_mag) * diagram_scale_factor

        vertical_points = []
        horizontal_points = []

        for x, y, fx, fy in combined:
            mag = math.sqrt(fx**2 + fy**2)
            if mag == 0:
                continue

            ox = fx * diagram_scale
            oy = fy * diagram_scale

            self.ax.plot([x, x + ox], [y, y + oy], linestyle="--", linewidth=1.1)
            self.ax.plot(x + ox, y + oy, marker=".", markersize=5)

            if abs(fy) >= abs(fx):
                vertical_points.append((x, y, y + oy))
            else:
                horizontal_points.append((y, x, x + ox))

        # Plot horizontal load envelopes per column/member station.
        # Previously all left/right acting horizontal loads were connected together.
        # On an enclosed frame, both columns can have horizontal Cpe arrows acting in
        # the same global direction, so sorting only by height connected the left and
        # right column points into a jagged zig-zag across the bay.  Group by the
        # original arrow x-position first so each column gets its own clean line.
        horizontal_by_station = {}
        for y, xbase, xoff in horizontal_points:
            station_key = round(xbase, 6)
            horizontal_by_station.setdefault(station_key, []).append((xoff, y, xbase))

        for station_points in horizontal_by_station.values():
            left = sorted([(xoff, y) for xoff, y, xbase in station_points if xoff < xbase], key=lambda p: p[1])
            right = sorted([(xoff, y) for xoff, y, xbase in station_points if xoff >= xbase], key=lambda p: p[1])
            if len(left) > 1:
                self.ax.plot([p[0] for p in left], [p[1] for p in left], linestyle="--", linewidth=1.3)
            if len(right) > 1:
                self.ax.plot([p[0] for p in right], [p[1] for p in right], linestyle="--", linewidth=1.3)

    def vertical_load_bands(self, vertical_points):
        if not vertical_points:
            return []
        points = sorted(vertical_points, key=lambda p: p[0])
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x_span = max(xs) - min(xs)
        y_span = max(ys) - min(ys)
        max_gap = max(x_span * 0.12, 1200.0)
        y_tol = max(y_span * 0.08, 500.0)
        bands = []
        current = []
        last = None

        for x, ybase, yoff in points:
            sign = 1 if yoff >= ybase else -1
            if last is None:
                current = [(x, yoff)]
            else:
                last_x, last_ybase, last_sign = last
                gap = abs(x - last_x)
                base_jump = abs(ybase - last_ybase)
                if sign != last_sign or gap > max_gap or base_jump > y_tol:
                    if len(current) > 1:
                        bands.append(current)
                    current = [(x, yoff)]
                else:
                    current.append((x, yoff))
            last = (x, ybase, sign)

        if len(current) > 1:
            bands.append(current)
        return bands

    def plot_load_arrows(self, load_arrows, show_labels=False):
        arrows = self.combined_load_arrows(load_arrows) if show_labels else load_arrows
        label_arrows = show_labels and len(arrows) <= 30
        for x, y, fx, fy in arrows:
            mag = math.sqrt(fx**2 + fy**2)
            if mag == 0:
                continue
            arrow_scale = 0.00008
            dx = fx * arrow_scale
            dy = fy * arrow_scale
            self.ax.arrow(x - dx, y - dy, dx, dy, head_width=250, head_length=300,
                          length_includes_head=True)
            if label_arrows:
                label = f"Fx={fx / 1000:+.1f} kN\nFy={fy / 1000:+.1f} kN"
                self.ax.text(x, y, label, fontsize=7)
