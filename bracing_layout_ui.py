"""Lightweight interactive bracing-location editor using projected Qt painting."""
import copy
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget,
                              QPushButton, QToolTip, QVBoxLayout, QWidget)

from bracing_layout import BracingLayout, FACE_LABELS


class BracingLayoutCanvas(QWidget):
    panelClicked = Signal(str)
    panelHovered = Signal(str)
    memberClicked = Signal(str)
    memberHovered = Signal(str)

    def __init__(self, layout, parent=None):
        super().__init__(parent)
        self.layout_model = layout
        self.face = ""
        self.azimuth, self.elevation = -135.0, 28.0
        self.selected = None
        self.read_only = False
        self.invert_orbit_horizontal = True
        self.invert_orbit_vertical = False
        self.result = None
        self.case_index = 0
        self.result_mode = "layout"
        self.deformation_factor = 50.
        self.member_selected = None
        self.hit_members = []
        self.hit_panels = []
        self._press = None
        self._dragged = False
        self.setMinimumSize(480, 350)
        self.setMouseTracking(True)
        self.setAccessibleName("3D bracing panels. Click to edit; drag to rotate. Use the panel list for keyboard selection.")

    def set_layout(self, layout):
        self.layout_model = layout
        self.selected = None
        self.update()

    def set_result(self, result, case_index=0):
        self.result = result
        self.case_index = case_index
        self.hit_members = []
        self.member_selected = None
        self.update()

    def result_points(self, deformed=False):
        if self.result is None:
            return []
        nominal = self.layout_model.geometry
        actual = self.result["analysis_layout"]["geometry"]
        from bracing_layout import LayoutGeometry
        ag = LayoutGeometry.from_dict(actual)
        movements = self.result["cases"][self.case_index]["displacements_mm"]
        points = []
        for node, movement in zip(self.result["nodes"], movements):
            x, y, z = node["point_mm"]
            nx = x*nominal.span_mm/ag.span_mm
            zscale = nominal.roof_z(nx)/ag.roof_z(x)
            point = (nx, y, z*zscale)
            if deformed:
                point = (point[0]+movement[0]*nominal.span_mm/ag.span_mm*self.deformation_factor,
                         point[1]+movement[1]*self.deformation_factor,
                         point[2]+movement[2]*zscale*self.deformation_factor)
            points.append(point)
        return points

    def member_description(self, identifier):
        if not self.result:
            return ""
        member = next(m for m in self.result["cases"][self.case_index]["members"] if m["id"] == identifier)
        check = self.result.get("section_checks", {}).get(identifier, {})
        ratio = check.get("ratio")
        uc = f"{ratio:.1%}" if ratio is not None else "not checked"
        return (f"{identifier}\n{check.get('section', member['kind'])}: {member['axial_n']/1000:.3f} kN ({member['state']})"
                f"\nGoverning utilisation: {uc} | {check.get('mode', '-')}\n{check.get('case', '-')}")

    def projection(self):
        geometry = self.layout_model.geometry
        azimuth, elevation = math.radians(self.azimuth), math.radians(self.elevation)
        def raw(point):
            x, y, z = point
            x -= geometry.span_mm / 2
            y -= geometry.length_mm / 2
            u = math.cos(azimuth) * x - math.sin(azimuth) * y
            depth_plane = math.sin(azimuth) * x + math.cos(azimuth) * y
            v = math.cos(elevation) * z - math.sin(elevation) * depth_plane
            depth = math.sin(elevation) * z + math.cos(elevation) * depth_plane
            return u, v, depth
        points = [raw(point) for panel in self.layout_model.panels.values() for point in panel.vertices]
        if self.result is not None and self.result_mode == "deformed":
            points += [raw(p) for p in self.result_points(True)]
        low_x, high_x = min(p[0] for p in points), max(p[0] for p in points)
        low_y, high_y = min(p[1] for p in points), max(p[1] for p in points)
        scale = min((self.width() - 70) / max(high_x - low_x, 1),
                    (self.height() - 100) / max(high_y - low_y, 1))
        def project(point):
            u, v, depth = raw(point)
            return QPointF(self.width() / 2 + (u - (low_x + high_x) / 2) * scale,
                           self.height() / 2 + 8 - (v - (low_y + high_y) / 2) * scale), depth
        return project

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f6f9fc"))
        project = self.projection()
        items = []
        for panel in self.layout_model.panels.values():
            projected = [project(point) for point in panel.vertices]
            polygon = QPolygonF([p[0] for p in projected])
            items.append((sum(p[1] for p in projected) / len(projected), panel, polygon))
        self.hit_panels = []
        for depth, panel, polygon in sorted(items, key=lambda item: item[0]):
            active = not self.face or self.face == panel.face
            blocked = panel.id in self.layout_model.blocked
            pinned = panel.id in self.layout_model.pinned
            color = QColor("#d74343" if blocked else "#128265" if pinned else "#50748d")
            color.setAlpha(55 if blocked and active else 45 if pinned and active else 13 if active else 3)
            painter.setBrush(color)
            painter.setPen(QPen(QColor("#8b9eac" if active else "#d1dbe2"), .8))
            painter.drawPolygon(polygon)
            if active:
                self.hit_panels.append((polygon, panel.id, depth))
            if active and (blocked or pinned) and (self.result is None or self.result_mode == "layout"):
                painter.setPen(QPen(QColor("#b63232" if blocked else "#128265"), 2.2,
                                   Qt.DashLine if blocked else Qt.SolidLine))
                for a, b in panel.diagonals:
                    painter.drawLine(project(a)[0], project(b)[0])
            if panel.id == self.selected:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor("#e69b18"), 2.6))
                painter.drawPolygon(polygon)
        # Structural reference lines are a geometric guide, not analysis members.
        g = self.layout_model.geometry
        painter.setPen(QPen(QColor("#415f74"), 1.1))
        for y in g.bay_stations_mm:
            roof = [(x, y, g.roof_z(x)) for x in g.roof_stations]
            for a, b in zip(roof, roof[1:]):
                painter.drawLine(project(a)[0], project(b)[0])
            for x in (0, g.span_mm):
                painter.drawLine(project((x, y, 0))[0], project((x, y, g.roof_z(x)))[0])
        for y in (0, g.length_mm):
            for x in g.end_wall_stations_mm[1:-1]:
                painter.drawLine(project((x, y, 0))[0], project((x, y, g.roof_z(x)))[0])
        self.hit_members = []
        if self.result is not None and self.result_mode != "layout":
            points = self.result_points(self.result_mode == "deformed")
            original = self.result_points()
            members = self.result["cases"][self.case_index]["members"]
            maximum = max((abs(m["axial_n"]) for m in members), default=1.) or 1.
            draw = []
            for member in members:
                a, b = project(points[member["start"]]), project(points[member["end"]])
                check = self.result.get("section_checks", {}).get(member["id"], {})
                ratio = check.get("ratio")
                if self.result_mode == "utilisation":
                    color = "#8b9eac" if ratio is None else "#c43b3b" if ratio > 1 else "#d28b1d" if ratio > .8 else "#128265"
                elif self.result_mode == "deformed":
                    color = "#a43db2"
                else:
                    color = "#c1c8ce" if member["state"] == "slack" else "#ce4d43" if member["axial_n"] >= 0 else "#287bb4"
                width = 1.5+2*abs(member["axial_n"])/maximum
                draw.append(((a[1]+b[1])/2, a[0], b[0], color, width, member))
            for depth, a, b, color, width, member in sorted(draw, key=lambda item: item[0]):
                if self.result_mode == "deformed":
                    painter.setPen(QPen(QColor("#bac8d2"), 1, Qt.DashLine))
                    painter.drawLine(project(original[member["start"]])[0], project(original[member["end"]])[0])
                selected = member["id"] == self.member_selected
                painter.setPen(QPen(QColor("#e69b18" if selected else color), 4.5 if selected else width))
                painter.drawLine(a, b)
                self.hit_members.append((a, b, member["id"], depth))
        painter.setPen(QColor("#284960"))
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(QRectF(16, 8, self.width() - 32, 36), Qt.TextWordWrap,
                         ("Click a member to inspect  |  Drag to orbit" if self.result is not None and self.result_mode != "layout" else
                          "Click a panel to edit  |  Drag to orbit  |  Filter a face to reach hidden panels"))
        front = project((g.span_mm / 2, 0, 0))[0]
        back = project((g.span_mm / 2, g.length_mm, 0))[0]
        painter.drawText(front + QPointF(-28, 22), "FRONT")
        painter.drawText(back + QPointF(-23, 22), "BACK")
        painter.drawText(16, self.height() - 16,
                         f"{g.span_mm / 1000:g} m span  x  {g.length_mm / 1000:g} m long  |  {len(g.bay_stations_mm) - 1} bays")
        painter.end()

    def member_at(self, point):
        hits = []
        for a, b, identifier, depth in self.hit_members:
            dx, dy = b.x()-a.x(), b.y()-a.y()
            length2 = dx*dx+dy*dy
            if length2 <= 1e-12:
                continue
            t = max(0., min(1., ((point.x()-a.x())*dx+(point.y()-a.y())*dy)/length2))
            distance = math.hypot(point.x()-a.x()-t*dx, point.y()-a.y()-t*dy)
            if distance < 7:
                hits.append((distance, -depth, identifier))
        return min(hits)[2] if hits else None

    def panel_at(self, point):
        hits = [(depth, identifier) for polygon, identifier, depth in self.hit_panels
                if polygon.containsPoint(point, Qt.OddEvenFill)]
        return max(hits)[1] if hits else None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.position()
            self._last = event.position()
            self._dragged = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.LeftButton:
            if (event.position() - self._press).manhattanLength() > 5:
                self._dragged = True
            if self._dragged:
                delta = event.position() - self._last
                horizontal = -1 if self.invert_orbit_horizontal else 1
                vertical = -1 if self.invert_orbit_vertical else 1
                self.azimuth += horizontal * delta.x() * .45
                self.elevation = min(80, max(5, self.elevation + vertical * delta.y() * .3))
                self.update()
            self._last = event.position()
            return
        if self.result is not None and self.result_mode != "layout":
            identifier = self.member_at(event.position())
            if identifier:
                QToolTip.showText(event.globalPosition().toPoint(), self.member_description(identifier), self)
                self.memberHovered.emit(identifier)
            else:
                QToolTip.hideText()
            return
        identifier = self.panel_at(event.position())
        if identifier:
            panel = self.layout_model.panels[identifier]
            status = "Blocked" if identifier in self.layout_model.blocked else "Pinned brace" if identifier in self.layout_model.pinned else "Available"
            QToolTip.showText(event.globalPosition().toPoint(), f"{panel.label}\n{status}", self)
            self.panelHovered.emit(identifier)
        else:
            QToolTip.hideText()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press is not None:
            if not self._dragged:
                inspect = self.result is not None and self.result_mode != "layout"
                identifier = self.member_at(event.position()) if inspect else self.panel_at(event.position())
                if identifier:
                    if inspect:
                        self.member_selected = identifier
                        self.memberClicked.emit(identifier)
                    else:
                        self.selected = identifier
                        self.panelHovered.emit(identifier)
                        if not self.read_only:
                            self.panelClicked.emit(identifier)
                    self.update()
            self._press = None


class BracingLayoutDialog(QDialog):
    layoutChanged = Signal()
    def __init__(self, geometry, saved=None, parent=None, read_only=False):
        super().__init__(parent)
        self.setWindowTitle("Bracing locations - 3D planning")
        self.resize(1080, 760)
        self.current_geometry = geometry
        self.read_only = read_only
        self.load_error = ""
        try:
            self.model = BracingLayout.from_dict(saved) if saved is not None else BracingLayout(geometry)
        except ValueError as error:
            self.model = BracingLayout(geometry)
            self.load_error = str(error)
        self._history = [self.model.to_dict()]
        self._history_index = 0
        layout = QVBoxLayout(self)
        intro = QLabel("Mark where bracing cannot go. All other panels remain available for the future layout search. Pin the braces to include in a fixed-layout analysis or retain in a future search. This view covers the main building; canopies and lean-tos are not included.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Edit"))
        self.edit_mode = QComboBox()
        self.edit_mode.addItem("Block / unblock bracing", "blocked")
        self.edit_mode.addItem("Pin / unpin a brace", "pinned")
        controls.addWidget(self.edit_mode)
        controls.addWidget(QLabel("Show"))
        self.face_filter = QComboBox()
        controls.addWidget(self.face_filter)
        self.view_combo = QComboBox()
        self.view_combo.addItems(["Front left", "Front right", "Back left", "Back right", "Roof plan"])
        controls.addWidget(self.view_combo)
        from PySide6.QtWidgets import QCheckBox
        self.orbit_horizontal = QCheckBox("Invert left/right")
        self.orbit_vertical = QCheckBox("Invert up/down")
        self.orbit_horizontal.setChecked(True)
        self.orbit_horizontal.setEnabled(False)
        self.orbit_vertical.setEnabled(False)
        controls.addWidget(self.orbit_horizontal)
        controls.addWidget(self.orbit_vertical)
        layout.addLayout(controls)
        middle = QHBoxLayout()
        self.canvas = BracingLayoutCanvas(self.model)
        self.canvas.read_only = read_only
        self.orbit_horizontal.toggled.connect(lambda value: setattr(self.canvas, "invert_orbit_horizontal", value))
        self.orbit_vertical.toggled.connect(lambda value: setattr(self.canvas, "invert_orbit_vertical", value))
        middle.addWidget(self.canvas, 3)
        side = QVBoxLayout()
        self.panel_list = QListWidget()
        self.panel_list.setAccessibleName("Bracing panel locations and constraint state")
        self.panel_list.setMinimumWidth(265)
        side.addWidget(self.panel_list)
        self.toggle_button = QPushButton("Toggle selected panel")
        side.addWidget(self.toggle_button)
        middle.addLayout(side, 1)
        layout.addLayout(middle, 1)
        self.location_label = QLabel("Choose a panel in the view or list.")
        self.location_label.setWordWrap(True)
        layout.addWidget(self.location_label)
        legend = QLabel("Red dashed X: blocked    Green X: pinned brace    Unmarked: available")
        self.legend_label = legend
        layout.addWidget(legend)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.geometry_note = QLabel()
        self.geometry_note.setWordWrap(True)
        layout.addWidget(self.geometry_note)
        self.reset_geometry_button = QPushButton("Use current dimensions and clear saved locations")
        layout.addWidget(self.reset_geometry_button)
        note = QLabel("Saving locations does not change the Phase 1 checks or portal eave spring. Use Analyse fixed bracing layout in 3D on the Bracing tab to calculate wind-only demands for the pinned panels. Automatic layout selection and restraint coupling remain separate.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #697b87;")
        layout.addWidget(note)
        buttons = QHBoxLayout()
        self.undo_button = QPushButton("Undo")
        self.redo_button = QPushButton("Redo")
        self.clear_button = QPushButton("Clear shown face")
        for button in (self.undo_button, self.redo_button, self.clear_button):
            buttons.addWidget(button)
        buttons.addStretch()
        self.save_button = QPushButton("Save locations")
        close_button = QPushButton("Close" if read_only else "Cancel")
        buttons.addWidget(self.save_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.canvas.panelClicked.connect(self.toggle_panel)
        self.canvas.panelHovered.connect(self.describe_panel)
        self.toggle_button.clicked.connect(self.toggle_selected)
        self.panel_list.currentItemChanged.connect(self.select_list_item)
        self.face_filter.currentIndexChanged.connect(self.refresh_panels)
        self.view_combo.currentIndexChanged.connect(self.change_view)
        self.undo_button.clicked.connect(lambda: self.restore_history(-1))
        self.redo_button.clicked.connect(lambda: self.restore_history(1))
        self.clear_button.clicked.connect(self.clear_face)
        self.reset_geometry_button.clicked.connect(self.use_current_geometry)
        self.save_button.clicked.connect(self.accept)
        close_button.clicked.connect(self.reject)
        self.populate_faces()
        self.refresh_panels()
        self.edit_mode.setEnabled(not read_only)
        self.toggle_button.setEnabled(not read_only)
        self.clear_button.setEnabled(not read_only)

    def populate_faces(self):
        previous = self.face_filter.currentData()
        self.face_filter.blockSignals(True)
        self.face_filter.clear()
        self.face_filter.addItem("All panels", "")
        for face, title in FACE_LABELS.items():
            if any(p.face == face for p in self.model.panels.values()):
                self.face_filter.addItem(title, face)
        index = self.face_filter.findData(previous)
        self.face_filter.setCurrentIndex(max(index, 0))
        self.face_filter.blockSignals(False)

    def refresh_panels(self):
        face = self.face_filter.currentData() or ""
        selected = self.canvas.selected
        if selected not in self.model.panels or (face and self.model.panels[selected].face != face):
            selected = None
            self.location_label.setText("Choose a panel in the view or list.")
        self.canvas.face = face
        self.clear_button.setText("Clear shown face" if face else "Clear all locations")
        self.panel_list.blockSignals(True)
        self.panel_list.clear()
        for panel in self.model.panels.values():
            if face and panel.face != face:
                continue
            state = "BLOCKED" if panel.id in self.model.blocked else "PINNED" if panel.id in self.model.pinned else "Available"
            self.panel_list.addItem(f"{state} | {panel.label}")
            item = self.panel_list.item(self.panel_list.count() - 1)
            item.setData(Qt.UserRole, panel.id)
            item.setToolTip(panel.id)
            if panel.id == selected:
                self.panel_list.setCurrentItem(item)
        self.panel_list.blockSignals(False)
        self.canvas.set_layout(self.model)
        self.canvas.selected = selected
        self.status_label.setText(f"{len(self.model.blocked)} blocked  |  {len(self.model.pinned)} pinned braces  |  {len(self.model.available)} available panels (includes pins)")
        changed = self.model.geometry != self.current_geometry
        problem = f"Saved layout could not be read: {self.load_error}. Saving replaces it with the displayed layout." if self.load_error else (
            "Building dimensions have changed. The view shows the saved dimensions. Update the geometry before saving new locations; this clears locations and can be undone." if changed else "")
        self.geometry_note.setText(problem)
        self.geometry_note.setStyleSheet("color: #a65120;")
        self.reset_geometry_button.setVisible(changed or bool(self.load_error))
        self.reset_geometry_button.setEnabled(not self.read_only)
        self.save_button.setEnabled(not self.read_only and not changed)
        self.save_button.setVisible(not self.read_only)
        self.undo_button.setEnabled(not self.read_only and self._history_index > 0)
        self.redo_button.setEnabled(not self.read_only and self._history_index < len(self._history) - 1)

    def remember(self):
        state = self.model.to_dict()
        if state != self._history[self._history_index]:
            del self._history[self._history_index + 1:]
            self._history.append(state)
            self._history_index += 1
        self.refresh_panels()
        self.layoutChanged.emit()

    def toggle_panel(self, identifier):
        if self.read_only:
            return
        try:
            self.model.toggle(identifier, self.edit_mode.currentData())
        except ValueError as error:
            self.location_label.setText(str(error))
            return
        self.canvas.selected = identifier
        self.describe_panel(identifier)
        self.remember()

    def toggle_selected(self):
        item = self.panel_list.currentItem()
        if item:
            self.toggle_panel(item.data(Qt.UserRole))

    def select_list_item(self, item, previous=None):
        if item:
            self.canvas.selected = item.data(Qt.UserRole)
            self.describe_panel(self.canvas.selected)
            self.canvas.update()

    def describe_panel(self, identifier):
        if identifier in self.model.panels:
            self.location_label.setText(self.model.panels[identifier].label)

    def clear_face(self):
        if self.read_only:
            return
        face = self.face_filter.currentData()
        ids = {p.id for p in self.model.panels.values() if not face or p.face == face}
        self.model.blocked.difference_update(ids)
        self.model.pinned.difference_update(ids)
        self.remember()

    def use_current_geometry(self):
        if not self.read_only:
            self.model = BracingLayout(self.current_geometry)
            self.load_error = ""
            self.populate_faces()
            self.remember()

    def restore_history(self, delta):
        target = self._history_index + delta
        if not self.read_only and 0 <= target < len(self._history):
            self._history_index = target
            self.model = BracingLayout.from_dict(copy.deepcopy(self._history[target]))
            self.populate_faces()
            self.refresh_panels()
            self.layoutChanged.emit()

    def change_view(self):
        self.canvas.azimuth, self.canvas.elevation = {
            "Front left": (-135, 28), "Front right": (135, 28),
            "Back left": (-45, 28), "Back right": (45, 28), "Roof plan": (-90, 80),
        }[self.view_combo.currentText()]
        self.canvas.update()

    def snapshot(self):
        return self.model.to_dict()

    def accept(self):
        if not self.read_only and self.model.geometry == self.current_geometry:
            super().accept()
