"""Menu bar and the dialogs opened from it."""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from portalcalc.app_metadata import (
    APP_COPYRIGHT,
    APP_DISPLAY_NAME,
    APP_RELEASE_DATE,
    APP_TAGLINE,
    APP_VERSION,
)
from portalcalc.desktop.branding import brand_logo_pixmap, csb_logo_pixmap, load_eula_text
from portalcalc.frame.common import SERVICEABILITY_LIMIT_DEFAULTS


WHATS_NEW_TEXT = """Portal Calc 1.6.4 - 25 October 2026

Full computations
- File > Export Full Computations (PDF) creates a paginated calculation document.
- Includes all engineering inputs, wind/load calculations, governing analysis results,
  section capacities, selected webs and the implemented connection checks.
- Add a large appendix with every nodal/member result for governing cases when needed.

Internal column layouts
- Choose Custom positions and enter distances in metres from the left main column centreline.
- Separate positions with commas, e.g. 5, 12, 20 adds three internal columns.
- Existing single and paired centre-offset layouts remain available.

Sections workflow
- Removed AI Help, AI Optimise, Structural Assistant and OpenAI API calls.

Earlier updates

End-wall steel
- Added automatic end-wall mullion and girt design, section checks and costing.
- Mullions space evenly at no more than 8 m with an optional Geometry override.
- Mullions use pinned roof/base assumptions, a mid-height flybrace and an H/150 wind deflection check.

Longitudinal bracing
- Added preliminary roof and wall bracing design with separate brace selections and governing wind loads.
- Roof bracing panels now run between end-wall mullion lines.
- Added separate eave-strut and individually listed mullion-tie demands and section selections.

CHS columns
- Added C350L0 CHS column sizes from 60.3 mm to 406.4 mm diameter.
- CHS sections participate in analysis, section checks, presets and hollow-section costing.
"""


class MenusMixin:
    """File, Settings and Help menus, summary/print and export dialogs."""

    def build_file_menu(self):
        file_menu = self.menuBar().addMenu("&File")
        new_action = QAction("&New", self)
        new_action.triggered.connect(self.new_preset)
        file_menu.addAction(new_action)

        open_action = QAction("&Open...", self)
        open_action.triggered.connect(self.load_preset)
        file_menu.addAction(open_action)

        self.open_recent_menu = file_menu.addMenu("Open &Recent")
        self.open_recent_menu.aboutToShow.connect(self.refresh_open_recent_menu)
        self.refresh_open_recent_menu()

        file_menu.addSeparator()
        save_action = QAction("&Save", self)
        save_action.triggered.connect(self.save_preset)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save &As...", self)
        save_as_action.triggered.connect(self.save_preset_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()
        summary_action = QAction("Printable Engineering &Summary...", self)
        summary_action.setShortcut("Ctrl+P")
        summary_action.triggered.connect(self.show_engineering_summary)
        file_menu.addAction(summary_action)

        self.computation_report_action = QAction("Export Full &Computations (PDF)...", self)
        self.computation_report_action.triggered.connect(lambda: self.export_computation_report())
        file_menu.addAction(self.computation_report_action)

        file_menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        self.undo_action = QAction("&Undo", self)
        self.undo_action.setShortcut("Ctrl+Z")
        self.undo_action.triggered.connect(self.undo_model_change)
        edit_menu.addAction(self.undo_action)
        self.redo_action = QAction("&Redo", self)
        self.redo_action.setShortcuts(["Ctrl+Y", "Ctrl+Shift+Z"])
        self.redo_action.triggered.connect(self.redo_model_change)
        edit_menu.addAction(self.redo_action)

    def show_engineering_summary(self):
        try:
            report_html = self.engineering_summary_html()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Engineering summary", str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Printable Engineering Summary")
        dialog.resize(900, 760)
        layout = QVBoxLayout(dialog)
        preview = QTextEdit()
        preview.setReadOnly(True)
        preview.setHtml(report_html)
        layout.addWidget(preview)
        buttons = QHBoxLayout()
        pdf_button = QPushButton("Export PDF...")
        print_button = QPushButton("Print...")
        close_button = QPushButton("Close")
        buttons.addStretch()
        buttons.addWidget(pdf_button)
        buttons.addWidget(print_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        def document():
            doc = QTextDocument()
            doc.setHtml(report_html)
            return doc

        def export_pdf():
            path, _ = QFileDialog.getSaveFileName(dialog, "Export Engineering Summary", "PortalCalc Engineering Summary.pdf", "PDF files (*.pdf)")
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(path)
            document().print_(printer)

        def print_report():
            printer = QPrinter(QPrinter.HighResolution)
            print_dialog = QPrintDialog(printer, dialog)
            if print_dialog.exec() == QDialog.Accepted:
                document().print_(printer)

        pdf_button.clicked.connect(export_pdf)
        print_button.clicked.connect(print_report)
        close_button.clicked.connect(dialog.accept)
        dialog.exec()

    def export_computation_report(self):
        preset_name = os.path.splitext(os.path.basename(self.current_preset_path or "Untitled project"))[0]
        saved = getattr(self, "_computation_report_metadata", {})
        dialog = QDialog(self)
        dialog.setWindowTitle("Full Computations")
        dialog.resize(540, 300)
        layout = QVBoxLayout(dialog)
        intro = QLabel("Export the current frame, loads, governing analysis and member/web checks as a paginated PDF. The current selections will be analysed again for the report.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        fields = {}
        for key, label, default in (("project", "Project", preset_name), ("job_number", "Job reference", ""),
                                    ("prepared_by", "Prepared by", ""), ("revision", "Revision", "A")):
            field = QLineEdit(str(saved.get(key, default)))
            fields[key] = field
            form.addRow(label, field)
        layout.addLayout(form)
        details = QCheckBox("Include all nodal and member results for governing cases (large appendix)")
        details.setChecked(False)
        layout.addWidget(details)
        actions = QHBoxLayout()
        actions.addStretch()
        export_button = QPushButton("Export PDF...")
        cancel_button = QPushButton("Cancel")
        actions.addWidget(export_button)
        actions.addWidget(cancel_button)
        layout.addLayout(actions)
        export_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        if dialog.exec() != QDialog.Accepted:
            return
        metadata = {key: field.text().strip() for key, field in fields.items()}
        self._computation_report_metadata = metadata
        path, _ = QFileDialog.getSaveFileName(self, "Export Full Computations", f"{metadata['project'] or preset_name} - Computations.pdf", "PDF files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        progress = QProgressDialog("Analysing the selected design and preparing computations...", "", 0, 0, self)
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.show()
        QApplication.processEvents()
        try:
            from portalcalc.reports.computation_report import export_computation_pdf
            data = self.capture_computation_report(metadata)
            progress.setLabelText("Laying out the computation document...")
            QApplication.processEvents()
            export_computation_pdf(data, path, include_case_details=details.isChecked())
        except Exception as exc:
            progress.close()
            QMessageBox.warning(self, "Computation report", f"Could not export the report:\n{exc}")
            return
        progress.close()
        QMessageBox.information(self, "Computation report", f"Full computations exported:\n{path}")

    def build_settings_menu(self):
        settings_menu = self.menuBar().addMenu("&Settings")
        self.settings_menu_actions = {
            "theme": {},
            "left_support": {},
            "right_support": {},
        }

        theme_menu = settings_menu.addMenu("UI &Theme")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        for theme in ["Light", "Dark"]:
            action = QAction(theme, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, value=theme: self.set_ui_theme_from_menu(value))
            theme_group.addAction(action)
            theme_menu.addAction(action)
            self.settings_menu_actions["theme"][theme] = action

        left_support_menu = settings_menu.addMenu("&Left Base Support")
        left_group = QActionGroup(self)
        left_group.setExclusive(True)
        for support in ["Pinned", "Fixed", "Spring"]:
            action = QAction(support, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, value=support: self.set_base_support_from_menu("left", value))
            left_group.addAction(action)
            left_support_menu.addAction(action)
            self.settings_menu_actions["left_support"][support] = action

        right_support_menu = settings_menu.addMenu("&Right Base Support")
        right_group = QActionGroup(self)
        right_group.setExclusive(True)
        for support in ["Pinned", "Fixed", "Spring"]:
            action = QAction(support, self)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, value=support: self.set_base_support_from_menu("right", value))
            right_group.addAction(action)
            right_support_menu.addAction(action)
            self.settings_menu_actions["right_support"][support] = action

        advanced_menu = settings_menu.addMenu("&Advanced")
        self.serviceability_limits_action = QAction("Serviceability limits...", self)
        self.serviceability_limits_action.triggered.connect(self.show_serviceability_limits_dialog)
        advanced_menu.addAction(self.serviceability_limits_action)
        advanced_menu.addSeparator()
        self.eave_deflection_override_action = QAction(self)
        self.eave_deflection_override_action.triggered.connect(self.set_eave_deflection_override)
        advanced_menu.addAction(self.eave_deflection_override_action)
        self.clear_eave_deflection_override_action = QAction("Use automatic eave deflection limit", self)
        self.clear_eave_deflection_override_action.triggered.connect(self.clear_eave_deflection_override)
        advanced_menu.addAction(self.clear_eave_deflection_override_action)

        self.refresh_settings_menu_checks()

    def build_help_menu(self):
        help_menu = self.menuBar().addMenu("&Help")
        self.whats_new_action = QAction("&What's New", self)
        self.whats_new_action.triggered.connect(lambda: self.show_help_dialog("whats_new"))
        help_menu.addAction(self.whats_new_action)
        eula_action = QAction("&EULA", self)
        eula_action.triggered.connect(lambda: self.show_help_dialog("eula"))
        help_menu.addAction(eula_action)
        about_action = QAction("&About", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)
        self.generate_solve_action = QAction("Generate + Solve", self)
        self.generate_solve_action.triggered.connect(self.generate_and_solve_with_popup)
        self.menuBar().addAction(self.generate_solve_action)

    def refresh_settings_menu_checks(self):
        actions = getattr(self, "settings_menu_actions", None)
        if not actions:
            return
        for value, action in actions.get("theme", {}).items():
            action.setChecked(self.ui_theme_combo.currentText() == value)
        for value, action in actions.get("left_support", {}).items():
            action.setChecked(self.left_support_combo.currentText() == value)
        for value, action in actions.get("right_support", {}).items():
            action.setChecked(self.right_support_combo.currentText() == value)
        override = self.eave_deflection_limit_override
        if hasattr(self, "eave_deflection_override_action"):
            suffix = f"H/{override:g}" if override is not None else "automatic"
            self.eave_deflection_override_action.setText(f"Set eave deflection limit... ({suffix})")
            self.clear_eave_deflection_override_action.setEnabled(override is not None)

    def set_ui_theme_from_menu(self, value):
        self.ui_theme_combo.setCurrentText(value)
        self.apply_ui_theme()
        self.refresh_settings_menu_checks()

    def set_base_support_from_menu(self, side, value):
        combo = self.left_support_combo if side == "left" else self.right_support_combo
        combo.setCurrentText(value)
        self.update_base_spring_visibility()
        self.refresh_settings_menu_checks()
        self.generate_and_solve()

    def set_eave_deflection_override(self):
        current = self.eave_deflection_limit_override or self.eave_deflection_divisor()
        value, accepted = QInputDialog.getDouble(
            self,
            "Eave deflection limit",
            "Enter divisor for eave deflection limit H/:",
            current,
            1.0,
            10000.0,
            0,
        )
        if not accepted:
            return
        self.eave_deflection_limit_override = value
        self.refresh_settings_menu_checks()
        self.generate_and_solve()

    def clear_eave_deflection_override(self):
        self.eave_deflection_limit_override = None
        self.refresh_settings_menu_checks()
        self.generate_and_solve()

    def show_serviceability_limits_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Serviceability Limits")
        dialog.resize(520, 520)
        layout = QVBoxLayout(dialog)
        intro = QLabel("Enter deflection limit divisors. For example, 150 means H/150, Span/150 or L/150 as shown.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        fields = [
            ("column_il1", "Column lateral, IL1 H/"),
            ("column_standard", "Column lateral, IL2+ H/"),
            ("storage_eave", "Storage eave lateral H/"),
            ("crane_rail_height", "Portal crane-rail racking H/"),
            ("truss_combined_il1", "Truss G+Q reference, IL1 Span/"),
            ("truss_combined_standard", "Truss G+Q reference, IL2+ Span/"),
            ("truss_g_il1", "Truss G precamber advisory, IL1 Span/"),
            ("truss_g_standard", "Truss G precamber advisory, IL2+ Span/"),
            ("truss_q_il1", "Truss Q, IL1 Span/"),
            ("truss_q_standard", "Truss Q, IL2+ Span/"),
            ("truss_ws_il1", "Truss Ws, IL1 Span/"),
            ("truss_ws_standard", "Truss Ws, IL2+ Span/"),
            ("canopy_tip_il1", "Canopy tip, IL1 L/"),
            ("canopy_tip_standard", "Canopy tip, IL2+ L/"),
            ("storage_steel", "Storage rail/stub L/"),
        ]
        field_labels = dict(fields)
        inputs = {}
        for key, label in fields:
            widget = QLineEdit(f"{self.serviceability_limit_value(key):g}")
            form.addRow(label, widget)
            inputs[key] = widget
        layout.addLayout(form)

        button_row = QHBoxLayout()
        reset_button = QPushButton("Reset")
        cancel_button = QPushButton("Cancel")
        apply_button = QPushButton("Apply")
        button_row.addWidget(reset_button)
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(apply_button)
        layout.addLayout(button_row)

        def reset_values():
            for key, widget in inputs.items():
                widget.setText(f"{SERVICEABILITY_LIMIT_DEFAULTS[key]:g}")

        def apply_values():
            new_values = {}
            for key, widget in inputs.items():
                text = widget.text().strip()
                try:
                    value = float(text)
                except (TypeError, ValueError):
                    QMessageBox.warning(dialog, "Invalid limit", f"{field_labels[key]} must be a number.")
                    return
                if value <= 0.0:
                    QMessageBox.warning(dialog, "Invalid limit", f"{field_labels[key]} must be greater than zero.")
                    return
                new_values[key] = value
            self.serviceability_limit_overrides = {
                key: value
                for key, value in new_values.items()
                if abs(value - SERVICEABILITY_LIMIT_DEFAULTS[key]) >= 1e-9
            }
            self.refresh_settings_menu_checks()
            dialog.accept()
            self.generate_and_solve()

        reset_button.clicked.connect(reset_values)
        cancel_button.clicked.connect(dialog.reject)
        apply_button.clicked.connect(apply_values)
        dialog.exec()

    def show_about_dialog(self):
        self.show_help_dialog("about")

    def show_help_dialog(self, initial_tab="whats_new"):
        if initial_tab == "about":
            self.update_license_status_label(refresh_server=True)

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Help - {APP_DISPLAY_NAME}")
        dialog.resize(700, 560)
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget(dialog)

        whats_new = QTextEdit()
        whats_new.setReadOnly(True)
        whats_new.setPlainText(WHATS_NEW_TEXT)
        tabs.addTab(whats_new, "What's New")

        eula = QTextEdit()
        eula.setReadOnly(True)
        eula.setPlainText(load_eula_text())
        tabs.addTab(eula, "EULA")

        about_tab = QWidget()
        about_layout = QVBoxLayout(about_tab)
        about_logo = QLabel()
        about_logo.setAlignment(Qt.AlignLeft)
        dark_theme = getattr(self, "ui_theme_combo", None) is not None and self.ui_theme_combo.currentText() == "Dark"
        about_logo_pixmap = brand_logo_pixmap(dark_background=dark_theme)
        about_header = QHBoxLayout()
        if not about_logo_pixmap.isNull():
            about_logo.setPixmap(about_logo_pixmap.scaledToWidth(440, Qt.SmoothTransformation))
            about_header.addWidget(about_logo, 0, Qt.AlignLeft | Qt.AlignVCenter)
        about_header.addStretch(1)
        about_csb_pixmap = csb_logo_pixmap(dark_background=dark_theme)
        if not about_csb_pixmap.isNull():
            about_csb_logo = QLabel()
            about_csb_logo.setToolTip("Central Steel Build")
            about_csb_logo.setPixmap(about_csb_pixmap.scaledToWidth(130, Qt.SmoothTransformation))
            about_header.addWidget(about_csb_logo, 0, Qt.AlignRight | Qt.AlignTop)
        about_layout.addLayout(about_header)

        about = QTextEdit()
        about.setReadOnly(True)
        about.setPlainText("\n".join([
            f"{APP_DISPLAY_NAME} - {APP_TAGLINE}",
            f"Version {APP_VERSION}",
            f"Release date: {APP_RELEASE_DATE}",
            APP_COPYRIGHT,
            "",
            "License",
            self.license_status_label.text() or "Unknown",
            "",
            "Factory defaults",
            self.factory_defaults_load_status,
        ]))
        about_layout.addWidget(about)
        tabs.addTab(about_tab, "About")
        tab_indexes = {"whats_new": 0, "eula": 1, "about": 2}
        tabs.setCurrentIndex(tab_indexes.get(initial_tab, 0))
        layout.addWidget(tabs)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.close)
        layout.addWidget(close_button)

        self.help_dialog = dialog
        self.help_tabs = tabs
        self.whats_new_text = whats_new
        dialog.show()
        return dialog
