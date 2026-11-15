"""Visibility of input groups, theme, status labels and plot refresh."""

import os

from PySide6.QtWidgets import QApplication

from portalcalc.app_metadata import APP_DISPLAY_NAME


class ViewStateMixin:
    """Keeps what is shown in step with the current inputs and results."""

    def set_deflection_hover_overlay_active(self, active, label=None):
        active = bool(active)
        if active and label is None:
            label = getattr(self, "column_deflection_check_label", None)
        if not active and label is not None and label is not self._active_deflection_hover_label:
            return
        next_label = label if active else None
        if active == self._deflection_hover_active and next_label is self._active_deflection_hover_label:
            return
        self._deflection_hover_active = active
        self._active_deflection_hover_label = next_label
        if (
            self.structure is not None
            and getattr(self, "result_combo", None) is not None
            and self.result_combo.currentText() == "Geometry"
        ):
            self.refresh_plot()

    def update_window_title(self):
        if self.configurator_review is not None:
            request_id = self.configurator_review.prepared.request_id
            self.setWindowTitle(
                f"{APP_DISPLAY_NAME} - Configurator Review - {request_id} - NOT ENGINEERING APPROVAL"
            )
            return
        file_name = os.path.basename(self.current_preset_path) if self.current_preset_path else "Untitled"
        dirty_marker = " *" if self.model_is_dirty() else ""
        self.setWindowTitle(f"{APP_DISPLAY_NAME} - {file_name}{dirty_marker}")

    def update_storage_section_visibility(self):
        group = getattr(self, "storage_section_group", None)
        if group is None:
            return
        enabled = bool(getattr(self, "storage_enabled_checkbox", None) and self.storage_enabled_checkbox.isChecked())
        group.setVisible(enabled)

    def update_license_status_label(self, refresh_server=False):
        label = getattr(self, "license_status_label", None)
        if label is None:
            return
        from portalcalc.licensing import license_enforced

        if not license_enforced():
            label.setText("Not enforced for this run")
            label.setStyleSheet("color: #777;")
            return
        try:
            from portalcalc.licensing import LicenseManager

            manager = LicenseManager()
            status = manager.refresh() if refresh_server else manager.local_status()
        except Exception as exc:
            label.setText(f"Check failed: {exc}")
            label.setStyleSheet("color: #b00020;")
            return
        if status.valid:
            details = []
            if status.customer:
                details.append(status.customer)
            if status.plan:
                details.append(status.plan)
            if status.days_remaining is not None:
                details.append(f"{status.days_remaining} days remaining")
            if status.warning:
                details.append(status.warning)
            suffix = f" ({', '.join(details)})" if details else ""
            label.setText(f"Valid{suffix}")
            color = "#9a6700" if status.warning else "#1b7f3a"
            label.setStyleSheet(f"color: {color}; font-weight: bold;")
        else:
            label.setText(status.reason)
            label.setStyleSheet("color: #b00020; font-weight: bold;")

    def update_web_section_visibility(self):
        rows = []
        rows.extend(getattr(self, "web_section_rows", []))
        rows.extend(getattr(self, "web_check_rows", []))
        rows.extend(getattr(self, "left_canopy_web_section_rows", []))
        rows.extend(getattr(self, "left_canopy_web_check_rows", []))
        rows.extend(getattr(self, "right_canopy_web_section_rows", []))
        rows.extend(getattr(self, "right_canopy_web_check_rows", []))
        for widget in rows:
            if widget is not None:
                widget.setVisible(False)

    def update_internal_column_visibility(self):
        has_internal_columns = self.internal_column_count_combo.currentText() != "None"
        custom_positions = self.internal_column_count_combo.currentText() == "Custom positions"
        for widget in getattr(self, "internal_column_offset_rows", []):
            if widget is not None:
                widget.setVisible(has_internal_columns and not custom_positions)
        for widget in getattr(self, "internal_column_positions_rows", []):
            if widget is not None:
                widget.setVisible(custom_positions)
        for widget in getattr(self, "internal_column_connection_rows", []):
            if widget is not None:
                widget.setVisible(has_internal_columns)
        for widget in getattr(self, "internal_column_section_rows", []):
            if widget is not None:
                widget.setVisible(has_internal_columns)
        for widget in getattr(self, "internal_column_check_rows", []):
            if widget is not None:
                widget.setVisible(has_internal_columns)

    def update_frame_system_visibility(self):
        is_truss = getattr(self, "frame_system_combo", None) is None or self.frame_system_combo.currentText() == "Truss"
        for widget in getattr(self, "bottom_chord_check_rows", []):
            if widget is not None:
                widget.setVisible(is_truss)
        for widget in getattr(self, "truss_depth_rows", []):
            if widget is not None:
                widget.setVisible(is_truss)
        for widget in getattr(self, "rafter_haunch_rows", []):
            if widget is not None:
                widget.setVisible(not is_truss)
        for widget in getattr(self, "web_section_rows", []):
            if widget is not None:
                widget.setVisible(False)
        for widget in getattr(self, "web_check_rows", []):
            if widget is not None:
                widget.setVisible(False)

    def update_eave_restraint_visibility(self):
        restraint = self.eave_x_restraint_combo.currentText()
        show_manual_spring = False
        show_end_brace = restraint in {"One end", "Both ends"}
        for widget in getattr(self, "eave_x_spring_rows", []):
            if widget is not None:
                widget.setVisible(show_manual_spring)
        for widget in getattr(self, "end_wall_brace_rows", []):
            if widget is not None:
                widget.setVisible(show_end_brace)
        self.update_bracing_layout_summary()

    def update_base_spring_visibility(self):
        show_spring = (
            self.left_support_combo.currentText() == "Spring"
            or self.right_support_combo.currentText() == "Spring"
        )
        for widget in getattr(self, "base_x_spring_rows", []):
            if widget is not None:
                widget.setVisible(show_spring)

    def update_current_canopy_visibility(self):
        def positive_length(input_widget):
            try:
                return float(input_widget.text()) > 0.0
            except ValueError:
                return False

        left_selected = self.side_addition_selected(self.left_canopy_type_combo)
        right_selected = self.side_addition_selected(self.right_canopy_type_combo)
        left_has_canopy = left_selected and positive_length(self.left_canopy_length_input)
        right_has_canopy = right_selected and positive_length(self.right_canopy_length_input)
        left_has_lean_to = left_has_canopy and self.left_canopy_type_combo.currentText() == "Lean-to"
        right_has_lean_to = right_has_canopy and self.right_canopy_type_combo.currentText() == "Lean-to"
        for widget in getattr(self, "left_canopy_geometry_rows", []):
            if widget is not None:
                widget.setVisible(left_selected)
        for widget in getattr(self, "right_canopy_geometry_rows", []):
            if widget is not None:
                widget.setVisible(right_selected)
        if getattr(self, "envelope_plan_selector", None) is not None and hasattr(self.envelope_plan_selector, "set_lean_to_visibility"):
            self.envelope_plan_selector.set_lean_to_visibility(left_has_lean_to, right_has_lean_to)
        self.update_canopy_section_visibility(left_has_canopy, right_has_canopy, left_has_lean_to, right_has_lean_to)
        for rows, visible in ((getattr(self, "left_canopy_purlin_rows", []), left_has_canopy and not left_has_lean_to),
                              (getattr(self, "right_canopy_purlin_rows", []), right_has_canopy and not right_has_lean_to)):
            for widget in rows:
                if widget is not None:
                    widget.setVisible(visible)
        for rows in [
            getattr(self, "left_canopy_wind_model_rows", []),
            getattr(self, "left_canopy_underside_rows", []),
            getattr(self, "left_canopy_manual_cpn_rows", []),
            getattr(self, "right_canopy_wind_model_rows", []),
            getattr(self, "right_canopy_underside_rows", []),
            getattr(self, "right_canopy_manual_cpn_rows", []),
        ]:
            for widget in rows:
                if widget is not None:
                    widget.setVisible(False)

    def update_snow_visibility(self):
        snow_enabled = self.snow_region_combo.currentText() != "None"
        for widget in getattr(self, "snow_ahd_height_rows", []):
            if widget is not None:
                widget.setVisible(snow_enabled)

    def update_internal_pressure_visibility(self):
        show_internal_pressure = self.wind_region_combo.currentText().upper() in {"B2", "C", "D"}
        for widget in getattr(self, "internal_pressure_rows", []):
            if widget is not None:
                widget.setVisible(show_internal_pressure)

    def apply_ui_theme(self):
        app = QApplication.instance()
        if app is None or getattr(self, "ui_theme_combo", None) is None:
            return
        if self.ui_theme_combo.currentText() != "Dark":
            app.setStyleSheet("")
            return
        app.setStyleSheet("""
            QWidget { background: #1f2328; color: #e6edf3; selection-background-color: #13377e; selection-color: #ffffff; }
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #00b5af;
            }
            QPushButton:default { border: 1px solid #00b5af; }
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background: #0d1117; color: #e6edf3; border: 1px solid #48515c; border-radius: 4px; padding: 3px;
            }
            QPushButton {
                background: #30363d; color: #e6edf3; border: 1px solid #57606a; border-radius: 4px; padding: 5px 8px;
            }
            QPushButton:hover { background: #3d444d; }
            QGroupBox { border: 1px solid #48515c; border-radius: 6px; margin-top: 8px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            QTabWidget::pane { border: 1px solid #48515c; }
            QTabBar::tab { background: #30363d; color: #e6edf3; padding: 6px 10px; border: 1px solid #48515c; }
            QTabBar::tab:selected { background: #0d1117; border-bottom: 2px solid #00b5af; }
            QScrollArea, QFrame { background: #1f2328; }
            QLabel { background: transparent; }
        """)

    def update_canopy_section_visibility(self, left_has_canopy, right_has_canopy, left_has_lean_to, right_has_lean_to):
        left_is_rafter = self.canopy_side_is_rafter("left")
        right_is_rafter = self.canopy_side_is_rafter("right")
        for widget in getattr(self, "left_canopy_section_rows", []):
            if widget is not None:
                widget.setVisible(left_has_canopy)
        for widget in getattr(self, "left_canopy_check_rows", []):
            if widget is not None:
                widget.setVisible(left_has_canopy)
        for widget in getattr(self, "left_canopy_load_rows", []):
            if widget is not None:
                widget.setVisible(left_has_canopy)
        for widget in getattr(self, "left_canopy_truss_only_rows", []):
            if widget is not None:
                widget.setVisible(left_has_canopy and not left_is_rafter)
        for widget in getattr(self, "left_canopy_web_section_rows", []):
            if widget is not None:
                widget.setVisible(False)
        for widget in getattr(self, "left_canopy_web_check_rows", []):
            if widget is not None:
                widget.setVisible(False)
        for widget in getattr(self, "left_canopy_bottom_check_rows", []):
            if widget is not None:
                widget.setVisible(left_has_canopy and not left_is_rafter)
        for widget in getattr(self, "left_canopy_deflection_check_rows", []):
            if widget is not None:
                widget.setVisible(left_has_canopy and not left_has_lean_to)
        for widget in getattr(self, "left_lean_section_rows", []):
            if widget is not None:
                widget.setVisible(left_has_lean_to)
        for widget in getattr(self, "left_lean_check_rows", []):
            if widget is not None:
                widget.setVisible(left_has_lean_to)
        for widget in getattr(self, "left_lean_enclosure_rows", []):
            if widget is not None:
                widget.setVisible(left_has_lean_to)
        for widget in getattr(self, "right_canopy_section_rows", []):
            if widget is not None:
                widget.setVisible(right_has_canopy)
        for widget in getattr(self, "right_canopy_check_rows", []):
            if widget is not None:
                widget.setVisible(right_has_canopy)
        for widget in getattr(self, "right_canopy_load_rows", []):
            if widget is not None:
                widget.setVisible(right_has_canopy)
        for widget in getattr(self, "right_canopy_truss_only_rows", []):
            if widget is not None:
                widget.setVisible(right_has_canopy and not right_is_rafter)
        for widget in getattr(self, "right_canopy_web_section_rows", []):
            if widget is not None:
                widget.setVisible(False)
        for widget in getattr(self, "right_canopy_web_check_rows", []):
            if widget is not None:
                widget.setVisible(False)
        for widget in getattr(self, "right_canopy_bottom_check_rows", []):
            if widget is not None:
                widget.setVisible(right_has_canopy and not right_is_rafter)
        for widget in getattr(self, "right_canopy_deflection_check_rows", []):
            if widget is not None:
                widget.setVisible(right_has_canopy and not right_has_lean_to)
        for widget in getattr(self, "right_lean_section_rows", []):
            if widget is not None:
                widget.setVisible(right_has_lean_to)
        for widget in getattr(self, "right_lean_check_rows", []):
            if widget is not None:
                widget.setVisible(right_has_lean_to)
        for widget in getattr(self, "right_lean_enclosure_rows", []):
            if widget is not None:
                widget.setVisible(right_has_lean_to)

    def resolved_deflection_hover_overlay(self):
        source = getattr(self, "deflection_hover_overlays", {}).get(
            getattr(self, "_active_deflection_hover_label", None)
        )
        if source is None and getattr(self, "_deflection_hover_active", False):
            source = self.governing_column_deflection_overlay
        if self.structure is None or not source:
            return None
        overlay = dict(source)
        item = overlay.get("item", {})
        point = None
        node_id = item.get("node")
        if node_id not in (None, "-"):
            node = next((candidate for candidate in self.structure.nodes if str(candidate.id) == str(node_id)), None)
            if node is not None:
                point = (node.x, node.y)
        if point is None and item.get("member") not in (None, "-"):
            element = next(
                (candidate for candidate in self.structure.elements if str(candidate.id) == str(item.get("member"))),
                None,
            )
            if element is not None:
                group = item.get("group") or element.group
                group_elements = [candidate for candidate in self.structure.elements if candidate.group == group]
                group_nodes = [node for candidate in group_elements for node in (candidate.start, candidate.end)]
                if group_nodes:
                    target_y = (min(node.y for node in group_nodes) + max(node.y for node in group_nodes)) / 2.0
                else:
                    target_y = (element.start.y + element.end.y) / 2.0
                dy = element.end.y - element.start.y
                t = (target_y - element.start.y) / dy if abs(dy) > 1e-9 else 0.5
                t = min(max(t, 0.0), 1.0)
                point = (
                    element.start.x + t * (element.end.x - element.start.x),
                    element.start.y + t * dy,
                )
        if point is None:
            return None
        overlay["x_mm"], overlay["y_mm"] = point
        overlay["displacements"] = item.get("displacements")
        overlay["element_load_state"] = item.get("element_load_state")
        overlay.pop("item", None)
        return overlay

    def resolved_governing_column_deflection_overlay(self):
        """Backward-compatible resolver for the original column hover overlay."""
        return self.resolved_deflection_hover_overlay()

    def suspended_plotting(self):
        """Context manager: skip frame redraws (e.g. during a batch optimiser)
        and redraw once at the end. Plotting only reads the solved state."""
        from contextlib import contextmanager

        @contextmanager
        def suspended():
            self._plot_suspended = getattr(self, "_plot_suspended", 0) + 1
            try:
                yield
            finally:
                self._plot_suspended -= 1
                if not self._plot_suspended and getattr(self, "_plot_stale", False):
                    self._plot_stale = False
                    self.refresh_plot()
        return suspended()

    def refresh_plot(self):
        if self.structure is None:
            return
        if getattr(self, "_plot_suspended", 0):
            self._plot_stale = True
            return
        try:
            deflection_scale = float(self.deflection_scale_input.text())
        except (ValueError, AttributeError):
            deflection_scale = 100.0
        try:
            diagram_scale_factor = float(self.diagram_scale_input.text())
        except (ValueError, AttributeError):
            diagram_scale_factor = 1.0

        deflection_scale = max(deflection_scale, 0.0)
        diagram_scale_factor = max(diagram_scale_factor, 0.0)
        result_type = self.result_combo.currentText()
        load_arrows = self.load_arrows
        if (
            result_type == "Load Diagram"
            and getattr(self, "load_view_mode_combo", None) is not None
            and self.load_view_mode_combo.currentText() == "Single load"
        ):
            load_arrows = (self.single_load_arrows or {}).get(self.single_load_combo.currentText(), [])

        governing_deflection = None
        if (
            result_type == "Geometry"
            and getattr(self, "governing_deflection_overlay_checkbox", None) is not None
            and self.governing_deflection_overlay_checkbox.isChecked()
            and getattr(self, "_deflection_hover_active", False)
        ):
            governing_deflection = self.resolved_deflection_hover_overlay()

        self.canvas.plot_structure(
            self.structure.nodes,
            self.structure.elements,
            self.base_nodes,
            self.support_types,
            load_arrows,
            result_type,
            self.displacements,
            scale=deflection_scale,
            diagram_scale_factor=diagram_scale_factor,
            purlin_layout=self.purlin_layout,
            wall_cladding_segments=self.wall_cladding_segments,
            doubled_chord_zones=(self.design_envelope_actions or {}).get("doubled_chord_zones", []),
            governing_deflection=governing_deflection,
            analysis_structure=self.structure,
            column_flybraces=self.column_flybrace_schedule(),
        )
