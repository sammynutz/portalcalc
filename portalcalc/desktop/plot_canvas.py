"""The 2D frame diagram canvas (geometry, loads, deflections, force diagrams)."""

import math

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from portalcalc.loads import arrow_unit


# ==========================================================
# PLOT CANVAS
# ==========================================================

class PlotCanvas(FigureCanvasQTAgg):

    HOVER_RADIUS_PX = 14

    def __init__(self):
        self.fig = Figure(figsize=(12, 8))
        self.ax = self.fig.add_subplot(111)
        super().__init__(self.fig)
        self._hover_segments = []
        self._hover_a = None
        self._hover_b = None
        self._hover_shown = None
        self._hover_annot = None
        self._hover_marker = None
        self.mpl_connect("motion_notify_event", self._on_hover)
        self.mpl_connect("axes_leave_event", lambda _event: self._hide_hover())
        self.mpl_connect("figure_leave_event", lambda _event: self._hide_hover())

    # ------------------------------------------------------------------
    # Hover read-out. Plot routines register probes: single points (loads,
    # nodes) or polylines carrying values at each vertex (members, force
    # diagrams). The cursor snaps to the nearest probe within a few pixels,
    # values are interpolated along the segment, and a popup shows them.
    # ------------------------------------------------------------------

    def _add_hover(self, x, y, text):
        x, y = float(x), float(y)
        self._hover_segments.append((x, y, x, y, (), (), lambda: text))

    def _add_hover_line(self, points, values, fmt):
        """points[i] carries the value tuple values[i]; fmt(*interpolated) -> popup text."""
        for i in range(len(points) - 1):
            (x1, y1), (x2, y2) = points[i], points[i + 1]
            self._hover_segments.append(
                (float(x1), float(y1), float(x2), float(y2), tuple(values[i]), tuple(values[i + 1]), fmt)
            )

    @staticmethod
    def _member_label(element):
        group = str(element.group or "").replace("_", " ").title()
        return f"M{element.id} ({group})" if group else f"M{element.id}"

    def _finish_hover_setup(self):
        self._hover_shown = None
        self._hover_a = self._hover_b = None
        if self._hover_segments:
            self._hover_a = np.array([s[0:2] for s in self._hover_segments], dtype=float)
            self._hover_b = np.array([s[2:4] for s in self._hover_segments], dtype=float)
        self._hover_annot = self.ax.annotate(
            "",
            xy=(0.0, 0.0),
            xytext=(14, 14),
            textcoords="offset points",
            fontsize=8,
            color="#1c2430",
            bbox={"boxstyle": "round,pad=0.4", "fc": "#fffdf4", "ec": "#6b7280", "alpha": 0.96},
            zorder=50,
            annotation_clip=False,
        )
        self._hover_annot.set_visible(False)
        (self._hover_marker,) = self.ax.plot(
            [0.0], [0.0], marker="o", markersize=7, markerfacecolor="none",
            markeredgecolor="#d62728", markeredgewidth=1.6, linestyle="none", zorder=49,
        )
        self._hover_marker.set_visible(False)

    def _hide_hover(self):
        if self._hover_annot is not None and self._hover_annot.get_visible():
            self._hover_annot.set_visible(False)
            self._hover_marker.set_visible(False)
            self._hover_shown = None
            self.draw_idle()

    def _on_hover(self, event):
        if event.inaxes is not self.ax or self._hover_a is None or self._hover_annot is None:
            self._hide_hover()
            return
        # Nearest point on each segment, measured in screen pixels.
        a = self.ax.transData.transform(self._hover_a)
        b = self.ax.transData.transform(self._hover_b)
        ab = b - a
        p = np.array([event.x, event.y], dtype=float)
        len2 = (ab * ab).sum(axis=1)
        frac = np.where(len2 > 1e-12, ((p - a) * ab).sum(axis=1) / np.maximum(len2, 1e-12), 0.0)
        frac = np.clip(frac, 0.0, 1.0)
        nearest = a + ab * frac[:, None]
        dist2 = ((nearest - p) ** 2).sum(axis=1)
        index = int(dist2.argmin())
        if dist2[index] > self.HOVER_RADIUS_PX ** 2:
            self._hide_hover()
            return
        x1, y1, x2, y2, v1, v2, fmt = self._hover_segments[index]
        f = float(frac[index])
        x = x1 + f * (x2 - x1)
        y = y1 + f * (y2 - y1)
        text = fmt(*[u + f * (w - u) for u, w in zip(v1, v2)])
        if self._hover_shown == (round(x, 1), round(y, 1), text):
            return
        self._hover_shown = (round(x, 1), round(y, 1), text)
        # Keep the popup inside the axes: flip it to the left / below near the edges.
        bbox = self.ax.bbox
        right = event.x > bbox.x0 + 0.6 * bbox.width
        top = event.y > bbox.y0 + 0.7 * bbox.height
        self._hover_annot.xy = (x, y)
        self._hover_annot.set_text(text)
        self._hover_annot.set_position((-14 if right else 14, -14 if top else 14))
        self._hover_annot.set_ha("right" if right else "left")
        self._hover_annot.set_va("top" if top else "bottom")
        self._hover_annot.set_visible(True)
        self._hover_marker.set_data([x], [y])
        self._hover_marker.set_visible(True)
        self.draw_idle()

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
        self._hover_segments = []
        self._hover_annot = None
        self._hover_marker = None

        for element in elements:
            x1 = element.start.x
            y1 = element.start.y
            x2 = element.end.x
            y2 = element.end.y
            style = self.element_plot_style(element.group)
            self.ax.plot([x1, x2], [y1, y2], **style)

        if result_type == "Geometry":
            self._add_geometry_hover(nodes, elements)
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
        self._finish_hover_setup()
        self.draw()

    def _add_geometry_hover(self, nodes, elements):
        # Nodes first so they win ties with the member ends that meet there.
        seen = set()
        for node in nodes:
            key = (round(node.x, 6), round(node.y, 6))
            if key in seen:
                continue
            seen.add(key)
            self._add_hover(node.x, node.y, f"Node {node.id}\nX = {node.x:,.0f} mm\nY = {node.y:,.0f} mm")
        for element in elements:
            length = element.length()
            if length <= 0:
                continue
            label = self._member_label(element)
            self._add_hover_line(
                [(element.start.x, element.start.y), (element.end.x, element.end.y)],
                [(0.0,), (length,)],
                lambda s, label=label, length=length: f"{label} @ {s:,.0f} mm\nLength {length:,.0f} mm",
            )

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
            values = []
            length = element.length()
            for index in range(21):
                t = index / 20.0
                ux, uy = element.global_displacement_at(displacements, t, uniform_load)
                points.append((
                    element.start.x + t * (element.end.x - element.start.x) + ux * scale,
                    element.start.y + t * (element.end.y - element.start.y) + uy * scale,
                ))
                values.append((t * length, ux, uy))
            self.ax.plot([point[0] for point in points], [point[1] for point in points], **style)
            label = self._member_label(element)
            self._add_hover_line(
                points,
                values,
                lambda s, ux, uy, label=label: f"{label} @ {s:,.0f} mm\ndx = {ux:+.2f} mm\ndy = {uy:+.2f} mm",
            )

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
            member_points = []
            hover_values = []
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
                member_points.append((px, py))
                hover_values.append((t * L, moment_nmm / 1_000_000.0, shear_n / 1000.0, axial_n / 1000.0))

            label = self._member_label(element)
            fmt = lambda s, m, v, n, label=label: (  # noqa: E731
                f"{label} @ {s:,.0f} mm\nM = {m:+.2f} kNm\nV = {v:+.2f} kN\n"
                f"N = {abs(n):.2f} kN {'C' if n < 0 else 'T'}"
            )
            self._add_hover_line(diagram_points, hover_values, fmt)
            self._add_hover_line(member_points, hover_values, fmt)

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

        Line loads (N/m, shown as kN/m) and point loads (N, shown as kN) are
        kept apart so only like is added to like. Returns (x, y, fx, fy, unit).
        """
        combined = {}
        for arrow in load_arrows:
            x, y, fx, fy = arrow
            key = (round(x, 6), round(y, 6), arrow_unit(arrow))
            old_fx, old_fy = combined.get(key, (0.0, 0.0))
            combined[key] = (old_fx + fx, old_fy + fy)
        return [(x, y, fx, fy, unit) for (x, y, unit), (fx, fy) in combined.items()]

    def plot_load_diagram_visual(self, load_arrows, diagram_scale_factor=1.0):
        """Draw a scaled resultant load diagram using combined loads."""
        combined = self.combined_load_arrows(load_arrows)
        combined = [arrow for arrow in combined if math.hypot(arrow[2], arrow[3]) > 1e-9]
        if not combined:
            return

        all_x = [arrow[0] for arrow in combined]
        structure_span = max(all_x) - min(all_x) if len(all_x) > 1 else 10000
        # kN/m and kN are scaled separately so a large point load does not
        # flatten the line-load diagram (and vice versa).
        max_mag = {}
        for _, _, fx, fy, unit in combined:
            max_mag[unit] = max(max_mag.get(unit, 1e-9), math.hypot(fx, fy))

        vertical_points = []
        horizontal_points = []

        for x, y, fx, fy, unit in combined:
            mag = math.sqrt(fx**2 + fy**2)
            if mag == 0:
                continue

            diagram_scale = (max(structure_span * 0.04, 600) / max_mag[unit]) * diagram_scale_factor
            ox = fx * diagram_scale
            oy = fy * diagram_scale

            self.ax.plot([x, x + ox], [y, y + oy], linestyle="--", linewidth=1.1)
            self.ax.plot(x + ox, y + oy, marker=".", markersize=5)
            kind = "Line load" if unit == "kN/m" else "Point load"
            hover_text = (
                f"{kind} at X = {x:,.0f}, Y = {y:,.0f} mm\n"
                f"Fx = {fx / 1000:+.2f} {unit}\nFy = {fy / 1000:+.2f} {unit}\n|F| = {mag / 1000:.2f} {unit}"
            )
            self._add_hover(x + ox, y + oy, hover_text)
            self._add_hover(x, y, hover_text)

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

    def plot_load_arrows(self, load_arrows, show_labels=False):
        if show_labels:
            arrows = self.combined_load_arrows(load_arrows)
        else:
            arrows = [(*arrow, arrow_unit(arrow)) for arrow in load_arrows]
        label_arrows = show_labels and len(arrows) <= 30
        for x, y, fx, fy, unit in arrows:
            mag = math.sqrt(fx**2 + fy**2)
            if mag == 0:
                continue
            arrow_scale = 0.00008
            dx = fx * arrow_scale
            dy = fy * arrow_scale
            self.ax.arrow(x - dx, y - dy, dx, dy, head_width=250, head_length=300,
                          length_includes_head=True)
            if label_arrows:
                label = f"Fx={fx / 1000:+.1f} {unit}\nFy={fy / 1000:+.1f} {unit}"
                self.ax.text(x, y, label, fontsize=7)
