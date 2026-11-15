"""Construction of the main window's input forms and result tabs."""

import math

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from portalcalc import bay_layout
from portalcalc.bracing.restraint import OPTION as BRACING_3D_RESTRAINT_OPTION
from portalcalc.desktop.plot_canvas import PlotCanvas
from portalcalc.desktop.widgets import BuildingEnvelopeSelector, CompassSelector
from portalcalc.frame.common import (
    CANOPY_CHORD_DEFAULT_FRAGMENTS,
    COSTING_DEFAULTS,
    crane_pfc_cap_display_name,
    CRANE_PFC_CAP_PROFILES,
    CRANE_RAIL_PROFILES,
    DEFAULT_ROOF_CLADDING_PROFILE,
    END_WALL_BRACING_SIZE_OPTIONS,
    FRAME_LOAD_BASIS_OPTIONS,
    LEAN_TO_COLUMN_DEFAULT_FRAGMENTS,
    ROOF_CLADDING_PROFILES,
)
from portalcalc.service.configurator_review import (
    review_truss_depth_bounds,
    validate_review_truss_depth,
)
from portalcalc.wind import WIND_CASE_OPTIONS


class UiBuildMixin:
    """Builds the widgets, wires their signals and sets the tooltips."""

    def add_canopy_purlin_rows(self, form, side):
        """Canopy edge purlin inputs and check (open canopies only)."""
        combo = QComboBox()
        combo.addItem("Auto (lightest passing)")
        sections = []
        for profile in (((self.metroll_tables or {}).get("tables", {}) or {}).get("1A", {}) or {}).get("profiles", []):
            section = profile.get("section")
            if section and section not in sections:
                sections.append(section)
        combo.addItems(sections)
        combo.setToolTip("Purlin at the canopy's outer edge. It is also the canopy-edge strut in the 3D bracing model.")
        spacing = QLineEdit("")
        spacing.setPlaceholderText("main roof spacing")
        spacing.setToolTip("Canopy purlin spacing (mm). Blank uses the main roof purlin spacing; the edge purlin carries half of it.")
        check = QLabel("-")
        setattr(self, f"{side}_canopy_purlin_combo", combo)
        setattr(self, f"{side}_canopy_purlin_spacing_input", spacing)
        setattr(self, f"{side}_canopy_purlin_check_label", check)
        title = side.capitalize()
        form.addRow(f"{title} canopy edge purlin", combo)
        form.addRow(f"{title} canopy purlin spacing (mm)", spacing)
        form.addRow(f"{title} canopy edge purlin check", check)
        return [form.labelForField(combo), combo, form.labelForField(spacing), spacing, form.labelForField(check), check]

    def build_ui(self):
        self.build_file_menu()
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout()
        central.setLayout(layout)

        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_panel.setLayout(left_layout)
        self.left_panel_layout = left_layout

        tabs = QTabWidget()
        self.input_tabs = tabs
        left_layout.addWidget(tabs)

        # ---------------- Geometry / Wind tab ----------------
        geometry_wind_tab = QScrollArea()
        geometry_wind_tab.setWidgetResizable(True)
        geometry_wind_content = QWidget()
        geometry_wind_layout = QVBoxLayout()
        geometry_wind_content.setLayout(geometry_wind_layout)
        geometry_wind_tab.setWidget(geometry_wind_content)

        def geometry_group(title):
            group = QGroupBox(title)
            layout = QFormLayout()
            group.setLayout(layout)
            geometry_wind_layout.addWidget(group)
            return layout

        self.span_input = QLineEdit("30")
        # Bays are the length input (bay_layout). Building length and the
        # design bay are derived from them into these two unshown fields, which
        # the calculations read.
        self.bays_input = QLineEdit("5*8000")
        self.bays_input.setPlaceholderText(f"e.g. {bay_layout.EXAMPLE}")
        self._bay_lengths_mm = [8000.0] * 5
        self.bays_summary_label = QLabel(bay_layout.summary(self._bay_lengths_mm))
        self.bays_summary_label.setWordWrap(True)
        self.building_length_input = QLineEdit("40.0")
        self.end_wall_mullion_spacing_override_input = QLineEdit("")
        self.pitch_input = QLineEdit("10")
        self.eave_input = QLineEdit("7")
        self.max_truss_depth_input = QLineEdit("")
        self.depth_input = QLineEdit("900")
        self.roof_shape_combo = QComboBox()
        self.roof_shape_combo.addItems(["Gable", "Skillion"])
        self.frame_system_combo = QComboBox()
        self.frame_system_combo.addItems(["Truss", "Rafter"])
        self.rafter_haunch_length_input = QLineEdit("3")
        self.load_bay_size_input = QLineEdit("8")
        self.snow_region_combo = QComboBox()
        self.snow_region_combo.addItems(["None", "Region AN", "Region AC", "Region AS", "Region AT"])
        self.snow_ahd_height_input = QLineEdit("0")
        self.internal_column_count_combo = QComboBox()
        self.internal_column_count_combo.addItems(["None", "1 column", "2 columns", "Custom positions"])
        self.internal_column_offset_input = QLineEdit("3")
        self.internal_column_positions_input = QLineEdit("")
        self.internal_column_positions_input.setPlaceholderText("e.g. 5, 12, 20")
        self.internal_column_connection_combo = QComboBox()
        self.internal_column_connection_combo.addItems(["Pinned", "Moment"])
        self.left_column_length_adjust_input = QLineEdit("0")
        self.right_column_length_adjust_input = QLineEdit("0")
        self.eave_x_restraint_combo = QComboBox()
        self.eave_x_restraint_combo.addItems(["None", "One end", "Both ends", BRACING_3D_RESTRAINT_OPTION])
        self.end_wall_braced_sides_combo = QComboBox()
        self.end_wall_braced_sides_combo.addItems(["One side", "Both sides"])
        self.end_wall_brace_size_combo = QComboBox()
        for label, area_mm2, description in END_WALL_BRACING_SIZE_OPTIONS:
            kg_m = area_mm2 * 1e-6 * 7850.0
            self.end_wall_brace_size_combo.addItem(
                f"{label} ({kg_m:.1f} kg/m)",
                {"label": label, "area_mm2": area_mm2, "description": description},
            )
        self.span_input.editingFinished.connect(self.update_truss_depth_from_span)
        self.max_truss_depth_input.editingFinished.connect(self.generate_and_solve)

        main_geometry_layout = geometry_group("Main geometry")
        self.main_geometry_layout = main_geometry_layout
        internal_columns_layout = geometry_group("Internal columns")
        supports_layout = geometry_group("Supports / bracing")

        main_geometry_layout.addRow("Span (m)", self.span_input)
        main_geometry_layout.addRow("Bays (mm)", self.bays_input)
        main_geometry_layout.addRow("", self.bays_summary_label)
        main_geometry_layout.addRow("Roof Pitch (deg)", self.pitch_input)
        main_geometry_layout.addRow("Eave Height (m)", self.eave_input)
        main_geometry_layout.addRow("Max truss depth (mm)", self.max_truss_depth_input)
        main_geometry_layout.addRow("End-wall mullion spacing override (m)", self.end_wall_mullion_spacing_override_input)
        main_geometry_layout.addRow("Roof shape", self.roof_shape_combo)
        main_geometry_layout.addRow("Frame system", self.frame_system_combo)
        main_geometry_layout.addRow("Rafter haunch length (m)", self.rafter_haunch_length_input)
        self.rafter_haunch_rows = [
            main_geometry_layout.labelForField(self.rafter_haunch_length_input),
            self.rafter_haunch_length_input,
        ]
        internal_columns_layout.addRow("Internal columns", self.internal_column_count_combo)
        internal_columns_layout.addRow("Internal column offset from centre (m)", self.internal_column_offset_input)
        internal_columns_layout.addRow("Positions from left column centreline (m)", self.internal_column_positions_input)
        internal_columns_layout.addRow("Internal column top connection", self.internal_column_connection_combo)
        self.internal_column_offset_rows = [
            internal_columns_layout.labelForField(self.internal_column_offset_input),
            self.internal_column_offset_input,
        ]
        self.internal_column_positions_rows = [
            internal_columns_layout.labelForField(self.internal_column_positions_input),
            self.internal_column_positions_input,
        ]
        self.internal_column_connection_rows = [
            internal_columns_layout.labelForField(self.internal_column_connection_combo),
            self.internal_column_connection_combo,
        ]
        supports_layout.addRow("Left column length adjust (m)", self.left_column_length_adjust_input)
        supports_layout.addRow("Right column length adjust (m)", self.right_column_length_adjust_input)
        supports_layout.addRow("End-wall portal restraint", self.eave_x_restraint_combo)
        supports_layout.addRow("Restrained eaves", self.end_wall_braced_sides_combo)
        supports_layout.addRow("End-wall restraint brace size", self.end_wall_brace_size_combo)
        self.end_wall_brace_rows = [
            supports_layout.labelForField(self.end_wall_braced_sides_combo),
            self.end_wall_braced_sides_combo,
            supports_layout.labelForField(self.end_wall_brace_size_combo),
            self.end_wall_brace_size_combo,
        ]

        # ---------------- Canopy / lean-to geometry ----------------
        canopies_layout = geometry_group("Canopy / lean-to geometry")

        self.left_canopy_length_input = QLineEdit("0")
        self.right_canopy_length_input = QLineEdit("0")
        self.left_canopy_type_combo = QComboBox()
        self.left_canopy_type_combo.addItems(["None", "Canopy", "Lean-to"])
        self.right_canopy_type_combo = QComboBox()
        self.right_canopy_type_combo.addItems(["None", "Canopy", "Lean-to"])
        self.left_canopy_frame_system_combo = QComboBox()
        self.left_canopy_frame_system_combo.addItems(["Truss", "Rafter"])
        self.right_canopy_frame_system_combo = QComboBox()
        self.right_canopy_frame_system_combo.addItems(["Truss", "Rafter"])
        self.left_canopy_wind_model_combo = QComboBox()
        self.left_canopy_wind_model_combo.addItems(["Main roof Cpe + Cpi", "AS/NZS B.5 free roof", "Manual net Cpn"])
        self.right_canopy_wind_model_combo = QComboBox()
        self.right_canopy_wind_model_combo.addItems(["Main roof Cpe + Cpi", "AS/NZS B.5 free roof", "Manual net Cpn"])
        self.left_canopy_underside_combo = QComboBox()
        self.left_canopy_underside_combo.addItems(["Empty under", "Blocked under"])
        self.right_canopy_underside_combo = QComboBox()
        self.right_canopy_underside_combo.addItems(["Empty under", "Blocked under"])
        self.left_lean_outer_wall_clad_combo = QComboBox()
        self.left_lean_outer_wall_clad_combo.addItems(["No", "Yes"])
        self.right_lean_outer_wall_clad_combo = QComboBox()
        self.right_lean_outer_wall_clad_combo.addItems(["No", "Yes"])
        self.left_canopy_eave_height_input = QLineEdit("")
        self.right_canopy_eave_height_input = QLineEdit("")
        self.left_canopy_pitch_input = QLineEdit("5")
        self.right_canopy_pitch_input = QLineEdit("5")
        self.left_canopy_cpn_uplift_input = QLineEdit("0.0")
        self.left_canopy_cpn_downward_input = QLineEdit("0.0")
        self.right_canopy_cpn_uplift_input = QLineEdit("0.0")
        self.right_canopy_cpn_downward_input = QLineEdit("0.0")
        self.left_canopy_shallow_depth_input = QLineEdit("300")
        self.right_canopy_shallow_depth_input = QLineEdit("300")
        self.left_canopy_g_load_input = QLineEdit("0.15")
        self.left_canopy_q_load_input = QLineEdit("0.25")
        self.left_canopy_solar_load_input = QLineEdit("0.0")
        self.right_canopy_g_load_input = QLineEdit("0.15")
        self.right_canopy_q_load_input = QLineEdit("0.25")
        self.right_canopy_solar_load_input = QLineEdit("0.0")

        canopies_layout.addRow("Left addition", self.left_canopy_type_combo)
        canopies_layout.addRow("Left framing", self.left_canopy_frame_system_combo)
        canopies_layout.addRow("Left width (m)", self.left_canopy_length_input)
        canopies_layout.addRow("Left eave height override (m)", self.left_canopy_eave_height_input)
        canopies_layout.addRow("Left pitch (deg)", self.left_canopy_pitch_input)
        canopies_layout.addRow("Left wind model", self.left_canopy_wind_model_combo)
        self.left_canopy_wind_model_rows = [
            canopies_layout.labelForField(self.left_canopy_wind_model_combo),
            self.left_canopy_wind_model_combo,
        ]
        canopies_layout.addRow("Left underside", self.left_canopy_underside_combo)
        self.left_canopy_underside_rows = [
            canopies_layout.labelForField(self.left_canopy_underside_combo),
            self.left_canopy_underside_combo,
        ]
        canopies_layout.addRow("Left manual Cpn uplift (+ up)", self.left_canopy_cpn_uplift_input)
        canopies_layout.addRow("Left manual Cpn downward (+ down)", self.left_canopy_cpn_downward_input)
        self.left_canopy_manual_cpn_rows = [
            canopies_layout.labelForField(self.left_canopy_cpn_uplift_input),
            self.left_canopy_cpn_uplift_input,
            canopies_layout.labelForField(self.left_canopy_cpn_downward_input),
            self.left_canopy_cpn_downward_input,
        ]
        self.left_canopy_geometry_rows = [
            canopies_layout.labelForField(self.left_canopy_length_input),
            self.left_canopy_length_input,
            canopies_layout.labelForField(self.left_canopy_frame_system_combo),
            self.left_canopy_frame_system_combo,
            canopies_layout.labelForField(self.left_canopy_eave_height_input),
            self.left_canopy_eave_height_input,
            canopies_layout.labelForField(self.left_canopy_pitch_input),
            self.left_canopy_pitch_input,
        ]
        self.left_lean_enclosure_rows = []

        canopies_layout.addRow("Right addition", self.right_canopy_type_combo)
        canopies_layout.addRow("Right framing", self.right_canopy_frame_system_combo)
        canopies_layout.addRow("Right width (m)", self.right_canopy_length_input)
        canopies_layout.addRow("Right eave height override (m)", self.right_canopy_eave_height_input)
        canopies_layout.addRow("Right pitch (deg)", self.right_canopy_pitch_input)
        canopies_layout.addRow("Right wind model", self.right_canopy_wind_model_combo)
        self.right_canopy_wind_model_rows = [
            canopies_layout.labelForField(self.right_canopy_wind_model_combo),
            self.right_canopy_wind_model_combo,
        ]
        canopies_layout.addRow("Right underside", self.right_canopy_underside_combo)
        self.right_canopy_underside_rows = [
            canopies_layout.labelForField(self.right_canopy_underside_combo),
            self.right_canopy_underside_combo,
        ]
        canopies_layout.addRow("Right manual Cpn uplift (+ up)", self.right_canopy_cpn_uplift_input)
        canopies_layout.addRow("Right manual Cpn downward (+ down)", self.right_canopy_cpn_downward_input)
        self.right_canopy_manual_cpn_rows = [
            canopies_layout.labelForField(self.right_canopy_cpn_uplift_input),
            self.right_canopy_cpn_uplift_input,
            canopies_layout.labelForField(self.right_canopy_cpn_downward_input),
            self.right_canopy_cpn_downward_input,
        ]
        self.right_canopy_geometry_rows = [
            canopies_layout.labelForField(self.right_canopy_length_input),
            self.right_canopy_length_input,
            canopies_layout.labelForField(self.right_canopy_frame_system_combo),
            self.right_canopy_frame_system_combo,
            canopies_layout.labelForField(self.right_canopy_eave_height_input),
            self.right_canopy_eave_height_input,
            canopies_layout.labelForField(self.right_canopy_pitch_input),
            self.right_canopy_pitch_input,
        ]
        self.right_lean_enclosure_rows = []

        self.left_support_combo = QComboBox()
        self.left_support_combo.addItems(["Pinned", "Fixed", "Spring"])
        self.right_support_combo = QComboBox()
        self.right_support_combo.addItems(["Pinned", "Fixed", "Spring"])
        self.base_x_spring_input = QLineEdit("0.0")
        # Base support controls live in the Settings menu; keep the combos as the state holders.
        supports_layout.addRow("Base moment spring kR (kNm/rad)", self.base_x_spring_input)
        self.base_x_spring_rows = [
            supports_layout.labelForField(self.base_x_spring_input),
            self.base_x_spring_input,
        ]
        self.eave_x_spring_input = QLineEdit("0.0")
        self.end_wall_brace_dia_input = QLineEdit("12.0")
        self.end_wall_brace_frame_offset_input = QLineEdit("")
        self.base_x_spring_input.hide()
        self.eave_x_spring_input.hide()
        self.end_wall_brace_dia_input.hide()
        self.end_wall_brace_frame_offset_input.hide()
        self.ui_theme_combo = QComboBox()
        self.ui_theme_combo.addItems(["Light", "Dark"])
        self.license_status_label = QLabel()
        self.license_status_label.setWordWrap(True)
        self.factory_defaults_status_label = QLabel("Not loaded yet")
        self.factory_defaults_status_label.setWordWrap(True)
        self.eave_x_spring_rows = []
        self.build_settings_menu()
        self.build_help_menu()

        self.wind_combination_note = QLabel("Wu combinations use every Cpe/Cpi pairing from these wind inputs. Md uses the worst multiplier in the selected direction's 90 degree window, or the worst direction in the region when Any orientation is selected.")
        self.wind_combination_note.setWordWrap(True)
        self.wind_region_combo = QComboBox()
        self.wind_region_combo.addItems(["A0", "A1", "A2", "A3", "A4", "A5", "A-ALL", "B1", "B2", "C", "D"])
        self.wind_region_combo.setCurrentText("A5")

        self.importance_combo = QComboBox()
        self.importance_combo.addItems(["1", "2", "3", "4"])
        self.terrain_combo = QComboBox()
        self.terrain_combo.addItems(["1", "2", "2.5", "3", "4"])
        self.terrain_combo.setCurrentText("2")

        self.wind_orientation_selector = CompassSelector(default="E")

        self.internal_pressure_combo = QComboBox()
        self.internal_pressure_combo.addItems(["Standard", "Louvre vented walls"])
        self.envelope_plan_selector = BuildingEnvelopeSelector()
        self.left_lean_enclosure_selector = self.envelope_plan_selector.left_lean_proxy
        self.right_lean_enclosure_selector = self.envelope_plan_selector.right_lean_proxy
        self.derived_frame_type_label = QLabel("-")

        self.wind_case_combo = QComboBox()
        self.wind_case_combo.addItems(WIND_CASE_OPTIONS)

        self.wind_reduction_input = QLineEdit("1.0")

        wind_snow_layout = geometry_group("Wind / snow")
        wind_snow_layout.addRow(self.wind_combination_note)
        wind_snow_layout.addRow("Wind Region", self.wind_region_combo)
        wind_snow_layout.addRow("Internal pressure", self.internal_pressure_combo)
        self.internal_pressure_rows = [
            wind_snow_layout.labelForField(self.internal_pressure_combo),
            self.internal_pressure_combo,
        ]
        wind_snow_layout.addRow("Importance Level", self.importance_combo)
        wind_snow_layout.addRow("Terrain Category", self.terrain_combo)
        wind_snow_layout.addRow("Snow Region", self.snow_region_combo)
        wind_snow_layout.addRow("AHD Height h0 (m)", self.snow_ahd_height_input)
        self.snow_ahd_height_rows = [
            wind_snow_layout.labelForField(self.snow_ahd_height_input),
            self.snow_ahd_height_input,
        ]
        wind_snow_layout.addRow("North direction in section view", self.wind_orientation_selector)
        wind_snow_layout.addRow("Wall enclosure", self.envelope_plan_selector)
        wind_snow_layout.addRow("Derived frame type", self.derived_frame_type_label)
        self.frame_load_basis_combo = QComboBox()
        for label, basis in FRAME_LOAD_BASIS_OPTIONS:
            self.frame_load_basis_combo.addItem(label, basis)
        self.frame_load_basis_combo.currentIndexChanged.connect(self.on_frame_load_basis_changed)
        wind_snow_layout.addRow("Frame loads", self.frame_load_basis_combo)
        geometry_wind_layout.addStretch(1)

        tabs.addTab(geometry_wind_tab, "Geometry/Wind")

        # ---------------- Longitudinal bracing tab ----------------
        bracing_tab = QScrollArea()
        bracing_tab.setWidgetResizable(True)
        bracing_content = QWidget()
        bracing_layout = QVBoxLayout(bracing_content)
        bracing_tab.setWidget(bracing_content)

        def bracing_group(title):
            group = QGroupBox(title)
            form = QFormLayout(group)
            bracing_layout.addWidget(group)
            return form

        self.longitudinal_bracing_enabled_checkbox = QCheckBox("Calculate longitudinal bracing")
        self.longitudinal_bracing_enabled_checkbox.setChecked(False)
        setup_layout = bracing_group("Bracing layout")
        setup_layout.addRow(self.longitudinal_bracing_enabled_checkbox)
        self.longitudinal_wall_sides_combo = QComboBox()
        self.longitudinal_wall_sides_combo.addItems(["One side", "Both sides"])
        self.longitudinal_wall_sides_combo.setCurrentText("Both sides")
        self.end_wall_braced_bay_count_input = QSpinBox()
        self.end_wall_braced_bay_count_input.setRange(1, 8)
        self.end_wall_braced_bay_count_input.setValue(1)
        setup_layout.addRow("Side walls braced", self.longitudinal_wall_sides_combo)
        setup_layout.addRow("Braced bays per side", self.end_wall_braced_bay_count_input)

        locations_layout = bracing_group("3D bracing locations")
        self.bracing_layout_button = QPushButton("Open 3D bracing workspace...")
        self.bracing_layout_button.clicked.connect(self.analyse_fixed_bracing_layout)
        self.bracing_layout_summary_label = QLabel("No exclusions or pinned locations saved.")
        self.bracing_layout_summary_label.setWordWrap(True)
        locations_layout.addRow(self.bracing_layout_button)
        self.bracing_analysis_button = self.bracing_layout_button
        locations_layout.addRow(self.bracing_layout_summary_label)
        layout_scope = QLabel("Mark blocked roof, side-wall and end-wall panels. Location planning is separate from the Phase 1 checks below.")
        layout_scope.setWordWrap(True)
        locations_layout.addRow(layout_scope)
        self.combined_sizing_button = QPushButton("Optimise frame + bracing together...")
        self.combined_sizing_button.setToolTip(
            "Size the 2D frame and the 3D bracing together for the lowest total estimated cost. "
            "Builds a frontier of bracing options and the restraint each gives, sizes the frame for the "
            "promising ones and verifies the winner exactly. Takes a few minutes.")
        self.combined_sizing_button.clicked.connect(self.run_combined_sizing)
        locations_layout.addRow(self.combined_sizing_button)

        def add_brace_sizes(combo):
            for label, area_mm2, description in END_WALL_BRACING_SIZE_OPTIONS:
                kg_m = area_mm2 * 1e-6 * 7850.0
                combo.addItem(
                    f"{label} ({kg_m:.1f} kg/m)",
                    {"label": label, "area_mm2": area_mm2, "description": description},
                )

        roof_layout = bracing_group("Roof-plane bracing")
        self.roof_brace_size_combo = QComboBox()
        add_brace_sizes(self.roof_brace_size_combo)
        self.roof_bracing_load_label = QLabel("Not calculated")
        self.roof_bracing_load_label.setWordWrap(True)
        roof_layout.addRow("Roof brace size", self.roof_brace_size_combo)
        roof_layout.addRow("Maximum roof brace load", self.roof_bracing_load_label)

        wall_layout = bracing_group("Side-wall bracing")
        self.wall_brace_size_combo = QComboBox()
        add_brace_sizes(self.wall_brace_size_combo)
        self.wall_bracing_load_label = QLabel("Not calculated")
        self.wall_bracing_load_label.setWordWrap(True)
        wall_layout.addRow("Wall brace size", self.wall_brace_size_combo)
        wall_layout.addRow("Maximum wall brace load", self.wall_bracing_load_label)

        strut_layout = bracing_group("Eave struts and mullion ties")
        self.bracing_strut_result_label = QLabel(
            "Automatic hierarchy: existing purlin, additional purlin, back-to-back purlins, then SHS/RHS."
        )
        self.bracing_strut_result_label.setWordWrap(True)
        strut_layout.addRow("Individual checks", self.bracing_strut_result_label)

        results_layout = bracing_group("Bracing result")
        self.longitudinal_bracing_status_label = QLabel("Disabled")
        self.longitudinal_bracing_status_label.setWordWrap(True)
        self.longitudinal_bracing_status_label.setStyleSheet("font-weight: bold;")
        self.longitudinal_bracing_case_label = QLabel("No governing end-wind case calculated.")
        self.longitudinal_bracing_case_label.setWordWrap(True)
        results_layout.addRow("Status", self.longitudinal_bracing_status_label)
        results_layout.addRow("Governing load case", self.longitudinal_bracing_case_label)
        bracing_layout.addStretch(1)
        self.bracing_tab = bracing_tab
        tabs.addTab(bracing_tab, "Bracing")

        # ---------------- Sections tab ----------------
        sections_tab = QScrollArea()
        sections_tab.setWidgetResizable(True)
        sections_content = QWidget()
        sections_layout = QFormLayout()
        sections_content.setLayout(sections_layout)
        sections_tab.setWidget(sections_content)

        self.optimise_structure_button = QPushButton("Optimise")
        self.optimise_structure_button.setToolTip(
            "Search for a low-cost verified design: a fast elastic screen finds the frame's stiffness "
            "distribution (sections and truss depth), projects it to catalogue sections, then verifies, "
            "repairs and locally refines the leading designs, including two-size web assignments."
        )
        self.optimise_structure_button.clicked.connect(lambda: self.run_structure_optimisation())
        self.future_canopy_optimise_button = QPushButton("Future Canopy Optimise")
        self.future_canopy_optimise_button.setToolTip(
            "Optimise with and without the configured cantilever canopy, combine the primary "
            "sections and doubled-chord zones, then verify the same hybrid design in both stages."
        )
        self.future_canopy_optimise_button.clicked.connect(lambda: self.run_future_canopy_optimisation())
        optimiser_buttons = QHBoxLayout()
        optimiser_buttons.addWidget(self.optimise_structure_button)
        sections_layout.addRow(optimiser_buttons)
        sections_layout.addRow(self.future_canopy_optimise_button)
        self.left_column_profile_combo = QComboBox()
        self.right_column_profile_combo = QComboBox()
        # Optimise on a symmetric building: the right column follows the left
        # (see columns_should_match / auto_size_group_specs).
        self._matched_columns = False
        self.left_column_profile_combo.currentIndexChanged.connect(self._sync_matched_columns)
        self.right_column_profile_combo.currentIndexChanged.connect(
            lambda *_: self._sync_matched_columns(source=self.right_column_profile_combo))
        self.top_profile_combo = QComboBox()
        self.bottom_profile_combo = self.top_profile_combo
        self.purlin_span_type_combo = QComboBox()
        self.purlin_span_type_combo.addItems(["Single span", "Double span", "Continuous lapped"])
        self.wall_girt_span_type_combo = QComboBox()
        self.wall_girt_span_type_combo.addItems(["Single span", "Double span", "Continuous lapped"])
        self.web_profile_combo = QComboBox()
        self.internal_column_profile_combo = QComboBox()
        self.left_canopy_top_profile_combo = QComboBox()
        self.left_canopy_bottom_profile_combo = self.left_canopy_top_profile_combo
        self.left_canopy_web_profile_combo = QComboBox()
        self.left_lean_column_profile_combo = QComboBox()
        self.right_canopy_top_profile_combo = QComboBox()
        self.right_canopy_bottom_profile_combo = self.right_canopy_top_profile_combo
        self.right_canopy_web_profile_combo = QComboBox()
        self.right_lean_column_profile_combo = QComboBox()

        for profile in self.catalog.all_profiles():
            self.left_column_profile_combo.addItem(profile.display_name)
            self.right_column_profile_combo.addItem(profile.display_name)
            self.internal_column_profile_combo.addItem(profile.display_name)
            self.left_lean_column_profile_combo.addItem(profile.display_name)
            self.right_lean_column_profile_combo.addItem(profile.display_name)

        shs_profiles = self.catalog.shs_profiles()
        main_chord_profiles = shs_profiles + self.catalog.ub_profiles() + self.catalog.rhs_profiles()
        for profile in main_chord_profiles:
            self.top_profile_combo.addItem(profile.display_name)
        for combo in [self.web_profile_combo, self.left_canopy_web_profile_combo, self.right_canopy_web_profile_combo]:
            for profile in shs_profiles:
                combo.addItem(profile.display_name)
        for combo in [self.left_canopy_top_profile_combo, self.right_canopy_top_profile_combo]:
            for profile in main_chord_profiles:
                combo.addItem(profile.display_name)

        self.set_default_combo(self.left_column_profile_combo, ["250 UC", "200 UC", "310 UB", "250 PFC"])
        self.set_default_combo(self.right_column_profile_combo, ["250 UC", "200 UC", "310 UB", "250 PFC"])
        self.set_default_combo(self.internal_column_profile_combo, ["200 UC", "150 UC", "250 UC", "100 SHS"])
        self.set_default_combo(self.top_profile_combo, ["150 SHS", "125 SHS", "100 SHS"])
        self.set_default_combo(self.web_profile_combo, ["50*2.5 SHS", "50 x 2.5 SHS", "50 SHS"])
        for combo in [self.left_canopy_top_profile_combo, self.right_canopy_top_profile_combo]:
            self.set_default_combo(combo, CANOPY_CHORD_DEFAULT_FRAGMENTS)
        for combo in [self.left_canopy_web_profile_combo, self.right_canopy_web_profile_combo]:
            self.set_default_combo(combo, ["50*2.5 SHS", "50 x 2.5 SHS", "50 SHS"])
        # Web combos are hidden calculated outputs. Structural analysis always
        # uses these fixed placeholder profiles; calculated web sizes are used
        # only for member/connection design, reporting, and costing.
        self.analysis_web_placeholder_names = {
            "WEB": self.web_profile_combo.currentText(),
            "LEFT_CANOPY_WEB": self.left_canopy_web_profile_combo.currentText(),
            "RIGHT_CANOPY_WEB": self.right_canopy_web_profile_combo.currentText(),
        }
        for combo in [self.left_lean_column_profile_combo, self.right_lean_column_profile_combo]:
            self.set_default_combo(combo, LEAN_TO_COLUMN_DEFAULT_FRAGMENTS)

        self.left_column_check_label = QLabel("Not checked")
        self.right_column_check_label = QLabel("Not checked")
        self.internal_column_check_label = QLabel("Not checked")
        self.top_chord_check_label = QLabel("Not checked")
        self.bottom_chord_check_label = QLabel("Not checked")
        self.purlin_check_label = QLabel("Not checked")
        self.wall_girt_check_label = QLabel("Not checked")
        self.crane_beam_check_label = QLabel("Not checked")
        self.storage_rail_check_label = QLabel("Not checked")
        self.storage_stub_check_label = QLabel("Not checked")
        self.storage_panel_check_label = QLabel("Not checked")
        self.web_check_label = QLabel("Not checked")
        self.web_split_label = QLabel("Single size")
        self.web_panel_check_button = QPushButton("Check panels")
        self.web_panel_check_button.clicked.connect(self.check_main_web_panel_layout)
        self.left_canopy_top_check_label = QLabel("Not checked")
        self.left_canopy_bottom_check_label = QLabel("Not checked")
        self.left_canopy_web_check_label = QLabel("Not checked")
        self.left_canopy_web_split_label = QLabel("Single size")
        self.left_canopy_deflection_check_label = QLabel("Not checked")
        self.left_lean_deflection_check_label = QLabel("Not checked")
        self.left_lean_column_check_label = QLabel("Not checked")
        self.left_lean_column_deflection_check_label = QLabel("Not checked")
        self.right_canopy_top_check_label = QLabel("Not checked")
        self.right_canopy_bottom_check_label = QLabel("Not checked")
        self.right_canopy_web_check_label = QLabel("Not checked")
        self.right_canopy_web_split_label = QLabel("Single size")
        self.right_canopy_deflection_check_label = QLabel("Not checked")
        self.right_lean_deflection_check_label = QLabel("Not checked")
        self.right_lean_column_check_label = QLabel("Not checked")
        self.right_lean_column_deflection_check_label = QLabel("Not checked")
        self.column_deflection_check_label = QLabel("Not checked")
        self.column_deflection_check_label.setWordWrap(True)
        self.truss_deflection_check_label = QLabel("Not checked")
        self.total_cost_label = QLabel("Not calculated")
        for label in [
            self.left_column_check_label,
            self.right_column_check_label,
            self.internal_column_check_label,
            self.top_chord_check_label,
            self.bottom_chord_check_label,
            self.purlin_check_label,
            self.wall_girt_check_label,
            self.crane_beam_check_label,
            self.storage_rail_check_label,
            self.storage_stub_check_label,
            self.storage_panel_check_label,
            self.web_check_label,
            self.web_split_label,
            self.left_canopy_top_check_label,
            self.left_canopy_bottom_check_label,
            self.left_canopy_web_check_label,
            self.left_canopy_web_split_label,
            self.left_canopy_deflection_check_label,
            self.left_lean_deflection_check_label,
            self.left_lean_column_check_label,
            self.left_lean_column_deflection_check_label,
            self.right_canopy_top_check_label,
            self.right_canopy_bottom_check_label,
            self.right_canopy_web_check_label,
            self.right_canopy_web_split_label,
            self.right_canopy_deflection_check_label,
            self.right_lean_deflection_check_label,
            self.right_lean_column_check_label,
            self.right_lean_column_deflection_check_label,
            self.column_deflection_check_label,
            self.truss_deflection_check_label,
            self.total_cost_label,
        ]:
            label.setStyleSheet("font-weight: bold;")
            label.setWordWrap(True)

        def section_group(title):
            group = QGroupBox(title)
            layout = QFormLayout()
            group.setLayout(layout)
            sections_layout.addRow(group)
            return layout

        columns_layout = section_group("Columns")
        columns_layout.addRow("Left column profile", self.section_selector_row(self.left_column_profile_combo))
        columns_layout.addRow("Left column check", self.left_column_check_label)
        columns_layout.addRow("Right column profile", self.section_selector_row(self.right_column_profile_combo))
        columns_layout.addRow("Right column check", self.right_column_check_label)
        internal_column_profile_widget = self.section_selector_row(self.internal_column_profile_combo)
        columns_layout.addRow("Internal column profile", internal_column_profile_widget)
        self.internal_column_section_rows = [columns_layout.labelForField(internal_column_profile_widget), internal_column_profile_widget]
        columns_layout.addRow("Internal column check", self.internal_column_check_label)
        self.internal_column_check_rows = [columns_layout.labelForField(self.internal_column_check_label), self.internal_column_check_label]
        columns_layout.addRow("Column deflection", self.column_deflection_check_label)

        truss_layout = section_group("Truss")
        truss_layout.addRow("Top/bottom chord / rafter", self.section_selector_row(self.top_profile_combo))
        truss_layout.addRow("Top chord check", self.top_chord_check_label)
        truss_layout.addRow("Bottom chord check", self.bottom_chord_check_label)
        self.bottom_chord_check_rows = [truss_layout.labelForField(self.bottom_chord_check_label), self.bottom_chord_check_label]
        web_profile_widget = self.section_selector_row(self.web_profile_combo)
        truss_layout.addRow("Web SHS", web_profile_widget)
        self.web_section_rows = [truss_layout.labelForField(web_profile_widget), web_profile_widget]
        web_split_widget = QWidget()
        web_split_layout = QHBoxLayout(web_split_widget)
        web_split_layout.setContentsMargins(0, 0, 0, 0)
        web_split_layout.addWidget(self.web_split_label, 1)
        web_split_layout.addWidget(self.web_panel_check_button)
        truss_layout.addRow("Web split", web_split_widget)
        truss_layout.addRow("Web check", self.web_check_label)
        self.web_check_rows = [truss_layout.labelForField(self.web_check_label), self.web_check_label]
        truss_depth_widget = self.depth_selector_row()
        truss_layout.addRow("Truss depth (mm)", truss_depth_widget)
        self.truss_depth_rows = [truss_layout.labelForField(truss_depth_widget), truss_depth_widget]
        truss_layout.addRow("Truss midspan deflection", self.truss_deflection_check_label)

        purlin_layout = section_group("Purlins / Girts")
        purlin_layout.addRow("Roof purlin span type", self.purlin_span_type_combo)
        purlin_layout.addRow("Roof purlin output", self.purlin_check_label)
        purlin_adjust_row = QHBoxLayout()
        self.extra_purlin_rows_label = QLabel()
        self.add_purlin_row_button = QPushButton("Add purlin row")
        self.remove_purlin_row_button = QPushButton("Remove purlin row")
        self.add_purlin_row_button.clicked.connect(lambda: self.adjust_extra_purlin_rows(1))
        self.remove_purlin_row_button.clicked.connect(lambda: self.adjust_extra_purlin_rows(-1))
        purlin_adjust_row.addWidget(self.extra_purlin_rows_label, 1)
        purlin_adjust_row.addWidget(self.add_purlin_row_button)
        purlin_adjust_row.addWidget(self.remove_purlin_row_button)
        purlin_layout.addRow("Extra purlin rows", purlin_adjust_row)
        self.purlin_bridging_summary_label = QLabel("Not checked")
        self.purlin_bridging_summary_label.setWordWrap(True)
        purlin_layout.addRow("Wall girt span type", self.wall_girt_span_type_combo)
        purlin_layout.addRow("Wall girt output", self.wall_girt_check_label)
        self.end_wall_layout_label = QLabel("Not checked")
        self.end_wall_mullion_check_label = QLabel("Not checked")
        self.end_wall_girt_check_label = QLabel("Not checked")
        for label in [self.end_wall_layout_label, self.end_wall_mullion_check_label, self.end_wall_girt_check_label]:
            label.setWordWrap(True)
            label.setStyleSheet("font-weight: bold;")
        purlin_layout.addRow("End-wall mullion layout", self.end_wall_layout_label)
        purlin_layout.addRow("End-wall mullion output", self.end_wall_mullion_check_label)
        purlin_layout.addRow("End-wall girt output", self.end_wall_girt_check_label)
        purlin_layout.addRow("Crane runway beam", self.crane_beam_check_label)

        storage_sections_layout = section_group("Storage Loads")
        self.storage_section_group = storage_sections_layout.parentWidget()
        storage_sections_layout.addRow("Horizontal rail", self.storage_rail_check_label)
        storage_sections_layout.addRow("Vertical stub", self.storage_stub_check_label)
        storage_sections_layout.addRow("Panel thickness / reo", self.storage_panel_check_label)

        costing_summary_layout = section_group("Costing")
        costing_summary_layout.addRow("Total estimated cost", self.total_cost_label)

        canopy_layout = section_group("Canopies / Lean-Tos")
        self.left_canopy_section_rows = []
        self.right_canopy_section_rows = []
        self.left_lean_section_rows = []
        self.right_lean_section_rows = []
        self.left_canopy_check_rows = []
        self.right_canopy_check_rows = []
        self.left_lean_check_rows = []
        self.right_lean_check_rows = []
        left_depth_widget = self.canopy_depth_selector_row(self.left_canopy_shallow_depth_input)
        canopy_layout.addRow("Left canopy/lean-to depth (mm)", left_depth_widget)
        self.left_canopy_truss_only_rows = [canopy_layout.labelForField(left_depth_widget), left_depth_widget]
        self.left_canopy_section_rows.extend(self.left_canopy_truss_only_rows)
        for label, widget, check_label, check_widget in [
            ("Left canopy/lean-to chord / rafter", self.section_selector_row(self.left_canopy_top_profile_combo), "Left canopy top check", self.left_canopy_top_check_label),
            ("Left canopy/lean-to webs", self.section_selector_row(self.left_canopy_web_profile_combo), "Left canopy web/post check", self.left_canopy_web_check_label),
        ]:
            canopy_layout.addRow(label, widget)
            self.left_canopy_section_rows.extend([canopy_layout.labelForField(widget), widget])
            if "web" in label.lower():
                self.left_canopy_web_section_rows = [canopy_layout.labelForField(widget), widget]
                canopy_layout.addRow("Left canopy web split", self.left_canopy_web_split_label)
            canopy_layout.addRow(check_label, check_widget)
            self.left_canopy_check_rows.extend([canopy_layout.labelForField(check_widget), check_widget])
            if "web" in check_label.lower():
                self.left_canopy_web_check_rows = [canopy_layout.labelForField(check_widget), check_widget]
        canopy_layout.addRow("Left canopy bottom check", self.left_canopy_bottom_check_label)
        self.left_canopy_bottom_check_rows = [canopy_layout.labelForField(self.left_canopy_bottom_check_label), self.left_canopy_bottom_check_label]
        self.left_canopy_check_rows.extend(self.left_canopy_bottom_check_rows)
        self.left_canopy_purlin_rows = self.add_canopy_purlin_rows(canopy_layout, "left")
        canopy_layout.addRow("Left canopy tip deflection", self.left_canopy_deflection_check_label)
        self.left_canopy_deflection_check_rows = [canopy_layout.labelForField(self.left_canopy_deflection_check_label), self.left_canopy_deflection_check_label]
        self.left_canopy_check_rows.extend(self.left_canopy_deflection_check_rows)
        canopy_layout.addRow("Left lean-to midspan deflection", self.left_lean_deflection_check_label)
        self.left_lean_deflection_check_rows = [canopy_layout.labelForField(self.left_lean_deflection_check_label), self.left_lean_deflection_check_label]
        left_lean_widget = self.section_selector_row(self.left_lean_column_profile_combo)
        canopy_layout.addRow("Left lean-to outer column", left_lean_widget)
        self.left_lean_section_rows.extend([canopy_layout.labelForField(left_lean_widget), left_lean_widget])
        canopy_layout.addRow("Left lean-to column check", self.left_lean_column_check_label)
        self.left_lean_check_rows = [canopy_layout.labelForField(self.left_lean_column_check_label), self.left_lean_column_check_label]
        canopy_layout.addRow("Left lean-to column mid-height deflection", self.left_lean_column_deflection_check_label)
        self.left_lean_column_deflection_check_rows = [
            canopy_layout.labelForField(self.left_lean_column_deflection_check_label),
            self.left_lean_column_deflection_check_label,
        ]
        self.left_lean_check_rows.extend(self.left_lean_column_deflection_check_rows)
        self.left_lean_check_rows.extend(self.left_lean_deflection_check_rows)

        right_depth_widget = self.canopy_depth_selector_row(self.right_canopy_shallow_depth_input)
        canopy_layout.addRow("Right canopy/lean-to depth (mm)", right_depth_widget)
        self.right_canopy_truss_only_rows = [canopy_layout.labelForField(right_depth_widget), right_depth_widget]
        self.right_canopy_section_rows.extend(self.right_canopy_truss_only_rows)
        for label, widget, check_label, check_widget in [
            ("Right canopy/lean-to chord / rafter", self.section_selector_row(self.right_canopy_top_profile_combo), "Right canopy top check", self.right_canopy_top_check_label),
            ("Right canopy/lean-to webs", self.section_selector_row(self.right_canopy_web_profile_combo), "Right canopy web/post check", self.right_canopy_web_check_label),
        ]:
            canopy_layout.addRow(label, widget)
            self.right_canopy_section_rows.extend([canopy_layout.labelForField(widget), widget])
            if "web" in label.lower():
                self.right_canopy_web_section_rows = [canopy_layout.labelForField(widget), widget]
                canopy_layout.addRow("Right canopy web split", self.right_canopy_web_split_label)
            canopy_layout.addRow(check_label, check_widget)
            self.right_canopy_check_rows.extend([canopy_layout.labelForField(check_widget), check_widget])
            if "web" in check_label.lower():
                self.right_canopy_web_check_rows = [canopy_layout.labelForField(check_widget), check_widget]
        canopy_layout.addRow("Right canopy bottom check", self.right_canopy_bottom_check_label)
        self.right_canopy_bottom_check_rows = [canopy_layout.labelForField(self.right_canopy_bottom_check_label), self.right_canopy_bottom_check_label]
        self.right_canopy_check_rows.extend(self.right_canopy_bottom_check_rows)
        self.right_canopy_purlin_rows = self.add_canopy_purlin_rows(canopy_layout, "right")
        canopy_layout.addRow("Right canopy tip deflection", self.right_canopy_deflection_check_label)
        self.right_canopy_deflection_check_rows = [canopy_layout.labelForField(self.right_canopy_deflection_check_label), self.right_canopy_deflection_check_label]
        self.right_canopy_check_rows.extend(self.right_canopy_deflection_check_rows)
        canopy_layout.addRow("Right lean-to midspan deflection", self.right_lean_deflection_check_label)
        self.right_lean_deflection_check_rows = [canopy_layout.labelForField(self.right_lean_deflection_check_label), self.right_lean_deflection_check_label]
        right_lean_widget = self.section_selector_row(self.right_lean_column_profile_combo)
        canopy_layout.addRow("Right lean-to outer column", right_lean_widget)
        self.right_lean_section_rows.extend([canopy_layout.labelForField(right_lean_widget), right_lean_widget])
        canopy_layout.addRow("Right lean-to column check", self.right_lean_column_check_label)
        self.right_lean_check_rows = [canopy_layout.labelForField(self.right_lean_column_check_label), self.right_lean_column_check_label]
        canopy_layout.addRow("Right lean-to column mid-height deflection", self.right_lean_column_deflection_check_label)
        self.right_lean_column_deflection_check_rows = [
            canopy_layout.labelForField(self.right_lean_column_deflection_check_label),
            self.right_lean_column_deflection_check_label,
        ]
        self.right_lean_check_rows.extend(self.right_lean_column_deflection_check_rows)
        self.right_lean_check_rows.extend(self.right_lean_deflection_check_rows)
        self.update_canopy_section_visibility(False, False, False, False)

        self.sections_tab = sections_tab
        tabs.addTab(sections_tab, "Sections")

        # ---------------- Loads tab ----------------
        loads_tab = QWidget()
        loads_layout = QFormLayout()
        loads_tab.setLayout(loads_layout)

        self.g_load_input = QLineEdit("0.10")
        self.q_load_input = QLineEdit("0.0")
        self.solar_load_input = QLineEdit("0.0")
        self.fire_service_load_input = QLineEdit("0.0")
        self.hvac_load_input = QLineEdit("0.0")
        self.other_load_input = QLineEdit("0.0")
        self.crane_rating_input = QLineEdit("0.0")
        self.left_wall_load_input = QLineEdit("0.0")
        self.right_wall_load_input = QLineEdit("0.0")

        loads_layout.addRow("G roof area load downward (kPa)", self.g_load_input)
        loads_layout.addRow("G roof extent", self.roof_load_placement_row("g"))
        loads_layout.addRow("Q roof area load downward (kPa)", self.q_load_input)
        loads_layout.addRow("Q roof extent", self.roof_load_placement_row("q"))
        loads_layout.addRow("Solar roof area load downward (kPa)", self.solar_load_input)
        loads_layout.addRow("Solar roof extent", self.roof_load_placement_row("solar"))
        loads_layout.addRow("Fire service area load downward (kPa)", self.fire_service_load_input)
        loads_layout.addRow("HVAC roof area load downward (kPa)", self.hvac_load_input)
        loads_layout.addRow("Other roof area load downward (kPa)", self.other_load_input)
        loads_layout.addRow("Other roof extent", self.roof_load_placement_row("other"))
        loads_layout.addRow("Crane Rating (t)", self.crane_rating_input)
        loads_layout.addRow("Left Wall Load, +X (kN/m)", self.left_wall_load_input)
        self.left_wall_load_rows = [loads_layout.labelForField(self.left_wall_load_input), self.left_wall_load_input]
        loads_layout.addRow("Right Wall Load, -X (kN/m)", self.right_wall_load_input)
        self.right_wall_load_rows = [loads_layout.labelForField(self.right_wall_load_input), self.right_wall_load_input]
        loads_layout.addRow("Left canopy G load downward (kPa)", self.left_canopy_g_load_input)
        self.left_canopy_load_rows = [loads_layout.labelForField(self.left_canopy_g_load_input), self.left_canopy_g_load_input]
        loads_layout.addRow("Left canopy Q load downward (kPa)", self.left_canopy_q_load_input)
        self.left_canopy_load_rows.extend([loads_layout.labelForField(self.left_canopy_q_load_input), self.left_canopy_q_load_input])
        loads_layout.addRow("Left canopy solar load downward (kPa)", self.left_canopy_solar_load_input)
        self.left_canopy_load_rows.extend([loads_layout.labelForField(self.left_canopy_solar_load_input), self.left_canopy_solar_load_input])
        loads_layout.addRow("Right canopy G load downward (kPa)", self.right_canopy_g_load_input)
        self.right_canopy_load_rows = [loads_layout.labelForField(self.right_canopy_g_load_input), self.right_canopy_g_load_input]
        loads_layout.addRow("Right canopy Q load downward (kPa)", self.right_canopy_q_load_input)
        self.right_canopy_load_rows.extend([loads_layout.labelForField(self.right_canopy_q_load_input), self.right_canopy_q_load_input])
        loads_layout.addRow("Right canopy solar load downward (kPa)", self.right_canopy_solar_load_input)
        self.right_canopy_load_rows.extend([loads_layout.labelForField(self.right_canopy_solar_load_input), self.right_canopy_solar_load_input])

        for widget in self.left_wall_load_rows + self.right_wall_load_rows:
            if widget is not None:
                widget.setVisible(False)

        self.load_combination_combo = QComboBox()
        self.load_combination_combo.addItems([combo.name for combo in self.load_combinations])

        tabs.addTab(loads_tab, "Loads")

        # ---------------- Storage Loads tab ----------------
        storage_tab = QScrollArea()
        storage_tab.setWidgetResizable(True)
        storage_content = QWidget()
        storage_layout = QVBoxLayout()
        storage_content.setLayout(storage_layout)
        storage_tab.setWidget(storage_content)
        storage_input_group = QGroupBox("Storage Inputs")
        storage_input_form = QFormLayout()
        storage_input_group.setLayout(storage_input_form)
        storage_output_group = QGroupBox("Storage Output")
        storage_output_form = QFormLayout()
        storage_output_group.setLayout(storage_output_form)
        storage_defaults_group = QGroupBox("Panel Defaults")
        storage_defaults_form = QFormLayout()
        storage_defaults_group.setLayout(storage_defaults_form)
        storage_layout.addWidget(storage_input_group)
        storage_layout.addWidget(storage_output_group)
        storage_layout.addWidget(storage_defaults_group)

        self.storage_enabled_checkbox = QCheckBox("Apply storage loads")
        self.storage_load_path_combo = QComboBox()
        self.storage_load_path_combo.addItems(["Wall panels to horizontal rail", "Side-running panels with centre stub"])
        self.storage_panel_support_combo = QComboBox()
        self.storage_panel_support_combo.addItems(["Break at vertical stub", "Continuous over vertical stub"])
        self.storage_material_input = QLineEdit("Grain")
        self.storage_density_input = QLineEdit("8.0")
        self.storage_phi_input = QLineEdit("30.0")
        self.storage_beta_input = QLineEdit("0.0")
        self.storage_height_input = QLineEdit("3.0")
        self.storage_rail_height_input = QLineEdit("3")
        self.storage_left_wall_checkbox = QCheckBox("Left wall")
        self.storage_left_wall_checkbox.setChecked(True)
        self.storage_right_wall_checkbox = QCheckBox("Right wall")
        self.storage_right_wall_checkbox.setChecked(True)
        self.storage_front_wall_checkbox = QCheckBox("Front wall")
        self.storage_back_wall_checkbox = QCheckBox("Back wall")
        self.storage_reverse_direction_checkbox = QCheckBox("Reverse load inward")
        self.storage_uls_factor_input = QLineEdit("1.5")
        self.storage_sls_factor_input = QLineEdit("1.0")
        self.storage_rail_profile_combo = QComboBox()
        for profile in self.catalog.all_profiles():
            if self.storage_rail_profile_allowed(profile):
                self.storage_rail_profile_combo.addItem(profile.display_name)
        self.set_default_combo(self.storage_rail_profile_combo, ["150 PFC", "180 PFC", "200 PFC", "150 UB"])
        self.storage_stub_profile_combo = QComboBox()
        for profile in self.catalog.all_profiles():
            if self.storage_stub_profile_allowed(profile):
                self.storage_stub_profile_combo.addItem(profile.display_name)
        self.set_default_combo(self.storage_stub_profile_combo, ["150 UB", "180 UB", "200 UB", "250 UB"])
        self.storage_rail_capacity_status_label = QLabel("-")
        self.storage_rail_deflection_status_label = QLabel("-")
        self.storage_stub_capacity_status_label = QLabel("-")
        self.storage_stub_deflection_status_label = QLabel("-")
        self.storage_panel_fc_input = QLineEdit("40")
        self.storage_panel_reo_fy_input = QLineEdit("500")
        self.storage_panel_cover_input = QLineEdit("40")
        self.storage_panel_deflection_limit_input = QLineEdit("250")
        self.storage_panel_stiffness_factor_input = QLineEdit("0.35")
        self.storage_panel_crack_min_thickness_input = QLineEdit("100")
        self.storage_panel_crack_max_spacing_input = QLineEdit("200")
        self.storage_panel_crack_stress_limit_input = QLineEdit("300")
        self.storage_panel_crack_width_limit_input = QLineEdit("0.30")
        self.storage_panel_shrinkage_input = QLineEdit("610")
        self.storage_panel_creep_input = QLineEdit("2.4")
        self.storage_panel_thicknesses_input = QLineEdit("125,150,175,200,225,250,275,300")
        self.storage_panel_auto_thickness_checkbox = QCheckBox("Auto")
        self.storage_panel_auto_thickness_checkbox.setChecked(True)
        self.storage_panel_selected_thickness_input = QSpinBox()
        self.storage_panel_selected_thickness_input.setRange(75, 500)
        self.storage_panel_selected_thickness_input.setSingleStep(25)
        self.storage_panel_selected_thickness_input.setSuffix(" mm")
        self.storage_panel_selected_thickness_input.setValue(150)
        self.storage_panel_mesh_input = QLineEdit("SL62:141:6.0:200,SL72:179:6.75:200,SL82:227:7.6:200,SL92:290:8.6:200,SL102:354:9.5:200,SL81:454:7.6:100,RL718:358:6.75:100,RL818:454:7.6:100,RL918:581:8.6:100,RL1018:709:9.5:100,RL1118:899:10.7:100,RL1218:1112:11.9:100")
        self.storage_panel_bars_input = QLineEdit("12,16,20")
        self.storage_panel_spacings_input = QLineEdit("300,250,200,150,125,100")

        storage_input_form.addRow("Storage", self.storage_enabled_checkbox)
        storage_input_form.addRow("Load path", self.storage_load_path_combo)
        storage_input_form.addRow("Panel support at stub", self.storage_panel_support_combo)
        storage_input_form.addRow("Material", self.storage_material_input)
        storage_input_form.addRow("Bulk density gamma (kN/m3)", self.storage_density_input)
        storage_input_form.addRow("Internal friction angle phi (deg)", self.storage_phi_input)
        storage_input_form.addRow("Backfill slope beta (deg)", self.storage_beta_input)
        storage_input_form.addRow("Stored height (m)", self.storage_height_input)
        storage_input_form.addRow("Horizontal rail height above support (m)", self.storage_rail_height_input)
        storage_input_form.addRow("Applied walls", self.two_widget_row(self.storage_left_wall_checkbox, self.storage_right_wall_checkbox))
        storage_input_form.addRow("3D end-wall storage", self.two_widget_row(self.storage_front_wall_checkbox, self.storage_back_wall_checkbox))
        storage_input_form.addRow("Load direction", self.storage_reverse_direction_checkbox)
        storage_input_form.addRow("ULS / SLS factors", self.two_widget_row(self.storage_uls_factor_input, self.storage_sls_factor_input))
        storage_output_form.addRow("Horizontal rail section", self.storage_rail_profile_combo)
        storage_output_form.addRow("Rail capacity", self.storage_rail_capacity_status_label)
        storage_output_form.addRow("Rail deflection", self.storage_rail_deflection_status_label)
        storage_output_form.addRow("Vertical stub section", self.storage_stub_profile_combo)
        storage_output_form.addRow("Stub capacity", self.storage_stub_capacity_status_label)
        storage_output_form.addRow("Stub deflection", self.storage_stub_deflection_status_label)
        storage_defaults_form.addRow("Panel f'c / reo fy (MPa)", self.two_widget_row(self.storage_panel_fc_input, self.storage_panel_reo_fy_input))
        storage_defaults_form.addRow("Panel cover (mm)", self.storage_panel_cover_input)
        storage_defaults_form.addRow("Panel deflection L/ / Ieff factor", self.two_widget_row(self.storage_panel_deflection_limit_input, self.storage_panel_stiffness_factor_input))
        storage_defaults_form.addRow("Panel crack min t / max spacing", self.two_widget_row(self.storage_panel_crack_min_thickness_input, self.storage_panel_crack_max_spacing_input))
        storage_defaults_form.addRow("Panel crack service fs limit (MPa)", self.storage_panel_crack_stress_limit_input)
        storage_defaults_form.addRow("Panel crack width limit (mm)", self.storage_panel_crack_width_limit_input)
        storage_defaults_form.addRow("Shrinkage strain micro / creep coeff", self.two_widget_row(self.storage_panel_shrinkage_input, self.storage_panel_creep_input))
        storage_defaults_form.addRow("Panel candidate thicknesses (mm)", self.storage_panel_thicknesses_input)
        storage_defaults_form.addRow("Panel thickness", self.two_widget_row(self.storage_panel_auto_thickness_checkbox, self.storage_panel_selected_thickness_input))
        storage_defaults_form.addRow("Panel mesh options name:As:dia:spacing", self.storage_panel_mesh_input)
        storage_defaults_form.addRow("Panel bar diameters (N)", self.storage_panel_bars_input)
        storage_defaults_form.addRow("Panel bar spacings (mm)", self.storage_panel_spacings_input)

        storage_button = QPushButton("Calculate Storage Loads")
        storage_button.clicked.connect(self.calculate_storage_load_report)
        storage_auto_rail_button = QPushButton("Auto Select Storage Rail / Stub")
        storage_auto_rail_button.clicked.connect(self.auto_select_storage_rail)
        storage_layout.addWidget(storage_button)
        storage_layout.addWidget(storage_auto_rail_button)
        self.storage_results_box = QTextEdit()
        self.storage_results_box.setReadOnly(True)
        self.storage_results_box.setMinimumHeight(260)
        storage_layout.addWidget(self.storage_results_box)
        tabs.addTab(storage_tab, "Storage Loads")

        # ---------------- Cladding tab ----------------
        cladding_tab = QWidget()
        cladding_layout = QVBoxLayout(cladding_tab)
        cladding_group = QGroupBox("Cladding")
        cladding_form = QFormLayout()
        cladding_group.setLayout(cladding_form)
        cladding_layout.addWidget(cladding_group)
        self.roof_cladding_profile_combo = QComboBox()
        self.roof_cladding_profile_combo.addItems(ROOF_CLADDING_PROFILES)
        self.roof_cladding_profile_combo.setCurrentText(DEFAULT_ROOF_CLADDING_PROFILE)
        self.roof_cladding_profile_combo.setToolTip("Sets the roof friction drag used in the 3D bracing end-wind loads.")
        cladding_form.addRow("Roof profile", self.roof_cladding_profile_combo)
        cladding_layout.addStretch(1)
        tabs.addTab(cladding_tab, "Cladding")

        # ---------------- Crane Beam tab ----------------
        crane_beam_tab = QScrollArea()
        crane_beam_tab.setWidgetResizable(True)
        crane_beam_content = QWidget()
        crane_beam_layout = QVBoxLayout()
        crane_beam_content.setLayout(crane_beam_layout)
        crane_beam_tab.setWidget(crane_beam_content)
        crane_beam_form = QFormLayout()
        crane_beam_layout.addLayout(crane_beam_form)

        self.crane_beam_profile_combo = QComboBox()
        crane_beam_profiles = []
        for profile in self.catalog.all_profiles():
            text = f"{profile.name} {profile.group_name} {profile.shape_type}".upper()
            if any(token in text for token in ["UB", "WB", "UC"]):
                crane_beam_profiles.append(profile)
        for profile in crane_beam_profiles:
            self.crane_beam_profile_combo.addItem(profile.display_name)
        self.set_default_combo(self.crane_beam_profile_combo, ["410 UB 59.7", "410 UB", "360 UB"])

        self.crane_beam_pfc_cap_combo = QComboBox()
        self.crane_beam_pfc_cap_combo.addItems(crane_pfc_cap_display_name(name) for name in CRANE_PFC_CAP_PROFILES.keys())
        self.set_combo_text_if_present(self.crane_beam_pfc_cap_combo, "300 PFC")

        self.crane_beam_rail_profile_combo = QComboBox()
        self.crane_beam_rail_profile_combo.addItems(CRANE_RAIL_PROFILES.keys())
        self.set_combo_text_if_present(self.crane_beam_rail_profile_combo, "12.5 kg/m rail")

        self.crane_beam_span_input = QLineEdit("9")
        self.crane_beam_wheelbase_input = QLineEdit("3.5")
        self.crane_beam_load_height_input = QLineEdit("0.12")
        self.crane_beam_self_weight_input = QLineEdit("1.00")
        self.crane_beam_rail_weight_input = QLineEdit("0.31")
        self.crane_beam_rail_bte_input = QLineEdit("50")
        self.crane_beam_rail_k_input = QLineEdit("4")
        self.crane_beam_pmax21_input = QLineEdit("44.2")
        self.crane_beam_pmax22_input = QLineEdit("44.2")
        self.crane_beam_pmin11_input = QLineEdit("15.2")
        self.crane_beam_pmin12_input = QLineEdit("15.2")
        self.crane_beam_pstatic21_input = QLineEdit("40.2")
        self.crane_beam_pstatic22_input = QLineEdit("40.2")
        self.crane_beam_pstatic_min11_input = QLineEdit("13.8")
        self.crane_beam_pstatic_min12_input = QLineEdit("13.8")
        self.crane_beam_phb21_input = QLineEdit("6.7")
        self.crane_beam_phb22_input = QLineEdit("-6.7")
        self.crane_beam_phi2_input = QLineEdit("4.6")
        # Long-travel buffer force per rail (kN). Blank = the project rule from
        # capacity and crane span (crane_beam.buffer_force_rule_kn).
        self.crane_beam_buffer_input = QLineEdit("")
        self.crane_beam_buffer_input.setPlaceholderText("auto from capacity and span")
        self.crane_beam_ix_input = QLineEdit("323.5")
        self.crane_beam_iy_top_input = QLineEdit("78.5")
        self.crane_beam_iy_oblique_input = QLineEdit("78.5")
        self.crane_beam_zx_input = QLineEdit("1585")
        self.crane_beam_zy_input = QLineEdit("183")
        self.crane_beam_depth_input = QLineEdit("414")
        self.crane_beam_flange_thickness_input = QLineEdit("12.8")
        self.crane_beam_fy_input = QLineEdit("300")
        self.crane_beam_include_sw_deflection_checkbox = QCheckBox("Include self weight in deflection")
        self.crane_beam_include_sw_deflection_checkbox.setChecked(True)

        for label, widget in [
            ("Beam profile", self.crane_beam_selector_row(self.crane_beam_profile_combo)),
            ("PFC cap", self.crane_beam_selector_row(self.crane_beam_pfc_cap_combo)),
            ("Rail profile", self.crane_beam_selector_row(self.crane_beam_rail_profile_combo)),
        ]:
            crane_beam_form.addRow(label, widget)
        self.crane_beam_capacity_status_label = QLabel("-")
        self.crane_beam_deflection_status_label = QLabel("-")
        crane_beam_form.addRow("Capacity", self.crane_beam_capacity_status_label)
        crane_beam_form.addRow("Deflection", self.crane_beam_deflection_status_label)

        for label, widget in [
            ("Span L (m)", self.crane_beam_span_input),
            ("Wheelbase wb (m)", self.crane_beam_wheelbase_input),
            ("Load height ht (m)", self.crane_beam_load_height_input),
            ("Beam self weight (kN/m)", self.crane_beam_self_weight_input),
            ("Rail self weight (kN/m)", self.crane_beam_rail_weight_input),
            ("Rail BTE / k", self.two_widget_row(self.crane_beam_rail_bte_input, self.crane_beam_rail_k_input)),
            ("Pmax.21 dynamic (kN)", self.crane_beam_pmax21_input),
            ("Pmax.22 dynamic (kN)", self.crane_beam_pmax22_input),
            ("Pmin.11 dynamic (kN)", self.crane_beam_pmin11_input),
            ("Pmin.12 dynamic (kN)", self.crane_beam_pmin12_input),
            ("Pmax.21 static (kN)", self.crane_beam_pstatic21_input),
            ("Pmax.22 static (kN)", self.crane_beam_pstatic22_input),
            ("Pmin.11 static (kN)", self.crane_beam_pstatic_min11_input),
            ("Pmin.12 static (kN)", self.crane_beam_pstatic_min12_input),
            ("Phb.21 lateral inertia (kN)", self.crane_beam_phb21_input),
            ("Phb.22 lateral inertia (kN)", self.crane_beam_phb22_input),
            ("Phi.2 oblique (kN)", self.crane_beam_phi2_input),
            ("Buffer force per rail (kN)", self.crane_beam_buffer_input),
            ("Ix stiffness (x10^6 mm^4)", self.crane_beam_ix_input),
            ("Iy top stiffness (x10^6 mm^4)", self.crane_beam_iy_top_input),
            ("Iy oblique stiffness (x10^6 mm^4)", self.crane_beam_iy_oblique_input),
            ("Zx / Zy (x10^3 mm^3)", self.two_widget_row(self.crane_beam_zx_input, self.crane_beam_zy_input)),
            ("Depth / flange t (mm)", self.two_widget_row(self.crane_beam_depth_input, self.crane_beam_flange_thickness_input)),
            ("Fy (MPa)", self.crane_beam_fy_input),
        ]:
            crane_beam_form.addRow(label, widget)
        crane_beam_form.addRow("Deflection", self.crane_beam_include_sw_deflection_checkbox)

        self.crane_beam_profile_combo.currentTextChanged.connect(self.on_crane_beam_profile_changed)
        self.crane_beam_pfc_cap_combo.currentTextChanged.connect(self.on_crane_beam_profile_changed)
        self.crane_beam_rail_profile_combo.currentTextChanged.connect(self.on_crane_beam_profile_changed)
        self.apply_crane_beam_profile_defaults()

        crane_beam_button = QPushButton("Calculate Crane Beam")
        crane_beam_button.clicked.connect(self.calculate_crane_beam_report)
        auto_crane_beam_button = QPushButton("Auto Select Efficient Crane Beam")
        auto_crane_beam_button.clicked.connect(self.auto_select_crane_beam)
        crane_beam_layout.addWidget(crane_beam_button)
        crane_beam_layout.addWidget(auto_crane_beam_button)
        self.crane_beam_results_box = QTextEdit()
        self.crane_beam_results_box.setReadOnly(True)
        self.crane_beam_results_box.setMinimumHeight(320)
        crane_beam_layout.addWidget(self.crane_beam_results_box)
        tabs.addTab(crane_beam_tab, "Crane Beam")

        # ---------------- Costing tab ----------------
        costing_tab = QWidget()
        costing_layout = QVBoxLayout()
        costing_tab.setLayout(costing_layout)

        costing_form = QFormLayout()
        self.shs_cost_input = QLineEdit(COSTING_DEFAULTS["shs_rate_per_kg"])
        self.doubled_chord_cost_input = QLineEdit(COSTING_DEFAULTS["doubled_chord_rate_per_kg"])
        self.ub_cost_input = QLineEdit(COSTING_DEFAULTS["ub_rate_per_kg"])
        self.wb_cost_input = QLineEdit(COSTING_DEFAULTS["wb_rate_per_kg"])
        self.purlin_cost_input = QLineEdit(COSTING_DEFAULTS["purlin_rate_per_kg"])
        self.storage_panel_concrete_rate_input = QLineEdit("280")
        self.storage_panel_reo_rate_input = QLineEdit("2.50")
        costing_form.addRow("SHS/RHS/CHS rate ($/kg)", self.shs_cost_input)
        costing_form.addRow("Doubled chord rate ($/kg)", self.doubled_chord_cost_input)
        costing_form.addRow("UB/UC/PFC rate ($/kg)", self.ub_cost_input)
        costing_form.addRow("WB rate ($/kg)", self.wb_cost_input)
        costing_form.addRow("Purlin/girt rate ($/kg)", self.purlin_cost_input)
        costing_form.addRow("Storage concrete ($/m3)", self.storage_panel_concrete_rate_input)
        costing_form.addRow("Storage reo ($/kg)", self.storage_panel_reo_rate_input)
        costing_layout.addLayout(costing_form)

        self.costing_box = QTextEdit()
        self.costing_box.setMinimumWidth(520)
        self.costing_box.setReadOnly(True)
        costing_layout.addWidget(self.costing_box)

        tabs.addTab(costing_tab, "Costing")

        # ---------------- Foundations tab ----------------
        foundations_tab = QWidget()
        foundations_layout = QVBoxLayout()
        foundations_tab.setLayout(foundations_layout)

        foundations_form = QFormLayout()
        self.foundation_bearing_input = QLineEdit("150")
        self.foundation_skin_friction_input = QLineEdit("15")
        self.foundation_skin_bearing_checkbox = QCheckBox("Use skin friction for bearing")
        self.foundation_skin_bearing_checkbox.setChecked(False)
        foundations_form.addRow("Allowable bearing (kPa)", self.foundation_bearing_input)
        foundations_form.addRow("Skin friction from 1 m (kPa)", self.foundation_skin_friction_input)
        foundations_form.addRow("Bearing", self.foundation_skin_bearing_checkbox)
        foundations_layout.addLayout(foundations_form)

        self.foundation_box = QTextEdit()
        self.foundation_box.setMinimumWidth(520)
        self.foundation_box.setReadOnly(True)
        foundations_layout.addWidget(self.foundation_box)

        tabs.addTab(foundations_tab, "Foundations")

        # ---------------- Results tab ----------------
        results_tab = QWidget()
        results_layout = QVBoxLayout()
        results_tab.setLayout(results_layout)

        results_form = QFormLayout()
        self.result_combo = QComboBox()
        self.result_combo.addItems(["Geometry", "Load Diagram", "Deflection", "Bending Moment", "Shear Force", "Axial Compression"])
        self.result_combo.currentTextChanged.connect(self.refresh_plot)
        results_form.addRow("Diagram View", self.result_combo)
        self.load_view_mode_combo = QComboBox()
        self.load_view_mode_combo.addItems(["Combination loads", "Single load"])
        self.load_view_mode_combo.currentTextChanged.connect(self.refresh_plot)
        self.single_load_combo = QComboBox()
        self.single_load_combo.addItem("G")
        self.single_load_combo.currentTextChanged.connect(self.refresh_plot)
        results_form.addRow("Load View Mode", self.load_view_mode_combo)
        results_form.addRow("Single Load", self.single_load_combo)
        results_form.addRow("Load Combination", self.load_combination_combo)

        self.second_order_checkbox = QCheckBox("Second-order analysis")
        self.second_order_checkbox.setChecked(False)
        self.second_order_checkbox.setToolTip("Iterate with member geometric stiffness from axial force.")
        results_form.addRow("Analysis", self.second_order_checkbox)

        self.governing_deflection_overlay_checkbox = QCheckBox("Enable deflection hover overlay")
        self.governing_deflection_overlay_checkbox.setChecked(True)
        self.governing_deflection_overlay_checkbox.toggled.connect(self.refresh_plot)
        self.governing_deflection_overlay_checkbox.setToolTip(
            "While hovering the Column deflection result, overlay the exaggerated governing SLS shape and load case on the Geometry view."
        )
        results_form.addRow("Geometry overlay", self.governing_deflection_overlay_checkbox)

        self.deflection_scale_input = QLineEdit("100")
        self.deflection_scale_input.editingFinished.connect(self.refresh_plot)
        self.diagram_scale_input = QLineEdit("1.0")
        self.diagram_scale_input.editingFinished.connect(self.refresh_plot)
        results_form.addRow("Deflection exaggeration", self.deflection_scale_input)
        results_form.addRow("Force/load diagram scale", self.diagram_scale_input)

        self.panel_label = QLabel("-")
        self.web_angle_label = QLabel("-")
        self.panel_count_label = QLabel("-")
        results_form.addRow("Actual Panel Length", self.panel_label)
        results_form.addRow("Approx Web Angle", self.web_angle_label)
        results_form.addRow("Total Panels", self.panel_count_label)
        results_layout.addLayout(results_form)

        self.results_box = QTextEdit()
        self.results_box.setMinimumWidth(520)
        results_layout.addWidget(self.results_box)

        tabs.addTab(results_tab, "Results")

        # ---------------- Section checks tab ----------------
        section_checks_tab = QWidget()
        section_checks_layout = QVBoxLayout()
        section_checks_tab.setLayout(section_checks_layout)

        self.section_checks_box = QTextEdit()
        self.section_checks_box.setMinimumWidth(520)
        section_checks_layout.addWidget(self.section_checks_box)

        tabs.addTab(section_checks_tab, "Section Checks")

        self.canvas = PlotCanvas()
        left_panel.setMinimumWidth(680)
        layout.addWidget(left_panel, 2)
        layout.addWidget(self.canvas, 3)

        self.update_web_section_visibility()
        self.update_internal_column_visibility()
        self.update_current_canopy_visibility()
        self.update_internal_pressure_visibility()
        self.update_snow_visibility()
        self.update_internal_pressure_visibility()
        self.update_frame_system_visibility()
        self.update_base_spring_visibility()
        self.update_eave_restraint_visibility()
        self.update_license_status_label()
        self.update_extra_purlin_rows_label()
        self.update_storage_section_visibility()
        self.calculate_storage_load_report()
        self.calculate_crane_beam_report()
        self.update_snow_visibility()
        self.apply_ui_theme()
        self.storage_enabled_checkbox.toggled.connect(lambda _: self.update_storage_section_visibility())
        self.storage_enabled_checkbox.toggled.connect(lambda _: self.calculate_storage_load_report())
        self.storage_enabled_checkbox.toggled.connect(lambda _: self.generate_and_solve())
        self.storage_load_path_combo.currentTextChanged.connect(lambda _: self.calculate_storage_load_report())
        self.storage_panel_support_combo.currentTextChanged.connect(lambda _: self.calculate_storage_load_report())
        self.storage_reverse_direction_checkbox.toggled.connect(lambda _: self.calculate_storage_load_report())
        self.storage_reverse_direction_checkbox.toggled.connect(lambda _: self.generate_and_solve())
        self.storage_rail_profile_combo.currentTextChanged.connect(lambda _: self.calculate_storage_load_report())
        self.storage_stub_profile_combo.currentTextChanged.connect(lambda _: self.calculate_storage_load_report())
        self.storage_panel_auto_thickness_checkbox.toggled.connect(lambda _: self.calculate_storage_load_report())
        self.storage_panel_selected_thickness_input.valueChanged.connect(lambda _: self.calculate_storage_load_report())
        self.crane_rating_input.editingFinished.connect(self.apply_crane_rating_to_crane_beam_inputs)
        self.bays_input.editingFinished.connect(self.on_bays_edited)
        self.internal_column_count_combo.currentTextChanged.connect(lambda _: self.update_internal_column_visibility())
        self.frame_system_combo.currentTextChanged.connect(lambda _: self.update_frame_system_visibility())
        self.left_support_combo.currentTextChanged.connect(lambda _: self.update_base_spring_visibility())
        self.right_support_combo.currentTextChanged.connect(lambda _: self.update_base_spring_visibility())
        self.left_support_combo.currentTextChanged.connect(lambda _: self.refresh_settings_menu_checks())
        self.right_support_combo.currentTextChanged.connect(lambda _: self.refresh_settings_menu_checks())
        self.eave_x_restraint_combo.currentTextChanged.connect(lambda _: self.update_eave_restraint_visibility())
        self.longitudinal_bracing_enabled_checkbox.toggled.connect(lambda _: self.generate_and_solve())
        self.longitudinal_wall_sides_combo.currentTextChanged.connect(lambda _: self.generate_and_solve())
        self.roof_brace_size_combo.currentTextChanged.connect(lambda _: self.generate_and_solve())
        self.wall_brace_size_combo.currentTextChanged.connect(lambda _: self.generate_and_solve())
        self.end_wall_braced_bay_count_input.valueChanged.connect(lambda _: self.generate_and_solve())
        for field in (self.span_input, self.eave_input, self.pitch_input,
                      self.end_wall_mullion_spacing_override_input):
            field.editingFinished.connect(self.update_bracing_layout_summary)
        self.roof_shape_combo.currentTextChanged.connect(lambda _: self.update_bracing_layout_summary())
        self.ui_theme_combo.currentTextChanged.connect(lambda _: self.apply_ui_theme())
        self.end_wall_mullion_spacing_override_input.editingFinished.connect(self.generate_and_solve)
        for widget in [self.left_canopy_length_input, self.right_canopy_length_input]:
            widget.editingFinished.connect(self.update_current_canopy_visibility)
        self.left_canopy_length_input.editingFinished.connect(lambda: self.update_canopy_depth_from_geometry("left"))
        self.right_canopy_length_input.editingFinished.connect(lambda: self.update_canopy_depth_from_geometry("right"))
        self.left_canopy_pitch_input.editingFinished.connect(lambda: self.update_canopy_depth_from_geometry("left"))
        self.right_canopy_pitch_input.editingFinished.connect(lambda: self.update_canopy_depth_from_geometry("right"))
        self.left_canopy_type_combo.currentTextChanged.connect(lambda _: self.update_current_canopy_visibility())
        self.right_canopy_type_combo.currentTextChanged.connect(lambda _: self.update_current_canopy_visibility())
        self.left_canopy_type_combo.currentTextChanged.connect(lambda _: self.update_canopy_depth_from_geometry("left"))
        self.right_canopy_type_combo.currentTextChanged.connect(lambda _: self.update_canopy_depth_from_geometry("right"))
        self.left_canopy_frame_system_combo.currentTextChanged.connect(lambda _: self.update_current_canopy_visibility())
        self.right_canopy_frame_system_combo.currentTextChanged.connect(lambda _: self.update_current_canopy_visibility())
        self.snow_region_combo.currentTextChanged.connect(lambda _: self.update_snow_visibility())
        self.wind_region_combo.currentTextChanged.connect(lambda _: self.update_internal_pressure_visibility())
        for button in self.envelope_plan_selector.all_wall_buttons():
            button.clicked.connect(self.update_derived_frame_type_label)
        self.update_derived_frame_type_label()
        self.apply_engineering_tooltips()

        if not shs_profiles:
            QMessageBox.warning(self, "Catalog warning", "No SHS profiles were found in the catalog. Check the naming/group fields.")

    def apply_engineering_tooltips(self):
        tooltips = {
            "span_input": "Outside building span (width) in metres, to the outside of the column flanges. The analysis span is between the column centrelines.",
            "bays_input": ("Portal bays front to back, in mm. Repeat a bay with count*length; separate with commas or spaces, "
                           "e.g. 7*8000, 7000 or 3*8000 2*7500 8000. The building length is their sum. A single-frame "
                           "design uses the longest bay as its tributary width and purlin/girt span; building loads give "
                           "every gridline its own tributary."),
            "pitch_input": "Main roof pitch in degrees.",
            "eave_input": ("Eave height in metres at the roof cladding pitching point (outside of the girts). The top chord "
                           "node at the column centrelines is lower: eave + (girt + column depth/2) x tan(pitch) - "
                           "(purlin + top chord depth/2) / cos(pitch). See the results for the working."),
            "max_truss_depth_input": "Optional hard cap for optimiser truss depth, in millimetres. Leave blank to use the automatic maximum: 6% of span plus 5% of eave height above 6 m. An entered value replaces that automatic ceiling.",
            "depth_input": "Main truss outside-to-outside depth in millimetres. Truss analysis offsets chord centrelines inward by half the selected top and bottom chord depths.",
            "roof_shape_combo": "Choose a gable or skillion main roof. This changes geometry and wind-pressure zoning.",
            "frame_system_combo": "Truss uses separate chords and pin-ended webs. Rafter uses a flexural roof member with optional eave haunches.",
            "rafter_haunch_length_input": "Length of the rafter stiffness haunch from each eave, in metres. Used only for rafter frames.",
            "internal_column_count_combo": "Adds internal supports beneath the main roof truss.",
            "internal_column_positions_input": "Enter one distance per internal column, separated by commas, in metres from the left main column centreline. Example: 5, 12, 20 adds three columns. All positions must lie between the main column centrelines.",
            "internal_column_offset_input": "Horizontal offset from building centreline, in metres. Single columns use the signed value; paired columns use +/- the absolute value.",
            "internal_column_connection_combo": "Pinned releases rotation at the truss/rafter support. Moment shares rotation into the rafter, or continues the internal column up through the truss chords.",
            "left_column_length_adjust_input": "Adjustment to the calculated left-column check length, in metres.",
            "right_column_length_adjust_input": "Adjustment to the calculated right-column check length, in metres.",
            "left_support_combo": "Main left-column base idealisation: pinned, fixed or rotational spring.",
            "right_support_combo": "Main right-column base idealisation: pinned, fixed or rotational spring.",
            "base_x_spring_input": "Base rotational spring stiffness used when either base support is set to Spring, in kNm/rad.",
            "eave_x_restraint_combo": "Roof-level sway restraint from bracing: none; one or both end walls (calibrated hand spring); or the 3D bracing model, which applies the least interior-frame restraint adopted from the 3D bracing workspace as a spring on the mean eave movement.",
            "end_wall_braced_sides_combo": "One side restrains the left eave only. Both sides applies the same longitudinal spring to both eave nodes.",
            "end_wall_brace_size_combo": "Rod or angle used in the end-wall bracing stiffness model.",
            "end_wall_braced_bay_count_input": "Number of parallel longitudinal braced bays provided on each selected side wall.",
            "roof_brace_size_combo": "Manual tension-brace size for the roof-plane braced bays.",
            "wall_brace_size_combo": "Manual tension-brace size for the side-wall braced bays.",
            "end_wall_mullion_spacing_override_input": "Optional target spacing in metres. Blank uses an even layout at no more than 8.0 m; an entered value manually overrides that target and the wall is redivided evenly.",
            "snow_region_combo": "Snow region used for roof snow loading. Confirm the project classification from the governing loading standard.",
            "snow_ahd_height_input": "Site elevation above Australian Height Datum in metres, used by the snow calculation.",
            "wind_region_combo": "Regional wind classification. A-ALL is the design envelope of A0–A5: maximum speed and Md by compass direction. It is not a geographic subregion.",
            "importance_combo": (
                "Building importance level. This affects design wind recurrence and serviceability limits; "
                "confirm from the project brief and governing standard.\n\n"
                "IL 1: Rarely occupied farm sheds (hay sheds, machinery sheds)\n"
                "IL 2: Normal buildings (workshops, dairy barns, arenas, commercial/industrial)\n"
                "IL 3: Buildings with large volumes of people (COLAs, halls, churches)\n"
                "IL 4: Emergency buildings (CFA)"
            ),
            "terrain_combo": (
                "Upwind terrain/roughness category used for wind multipliers. Confirm for each relevant wind "
                "direction and site exposure.\n\n"
                "TC 1: Open water\n"
                "TC 2: Open grassland / paddocks\n"
                "TC 2.5: Light industrial, scattered buildings\n"
                "TC 3: Dense industrial, urban areas\n"
                "TC 4: City centre"
            ),
            "wind_orientation_selector": "Direction of north when viewing the frame elevation. Used to map regional directional multipliers to frame wind cases.",
            "internal_pressure_combo": "Internal-pressure treatment. Availability depends on wind region and enclosure configuration.",
            "frame_load_basis_combo": (
                "Single frame: today's 2D envelope, a full bay of load with the worst end-wind zone on one representative frame.\n"
                "Building loads: every gridline is checked with its own loads, i.e. half bays at the end frames, end-wind "
                "roof and side-wall bands by position, Md for each direction, Cpi by opening position and Kc by "
                "effective surfaces. The optimiser sizes on the governing combinations and verifies on all of them.\n"
                "Building loads, 2D coefficients: every gridline with today's coefficients, for comparison."
            ),
            "envelope_plan_selector": "Toggle clad/open walls in plan. These states determine frame enclosure, Cpi treatment and column wind loading.",
            "derived_frame_type_label": "Frame enclosure classification derived from the selected wall states.",
            "wind_case_combo": "Single wind case shown in the load display. Design envelopes still check all generated wind combinations.",
            "wind_reduction_input": "Additional multiplier applied to calculated wind pressure. Use 1.0 unless a justified project-specific factor applies.",
            "purlin_span_type_combo": "Purlin support arrangement used to select capacity data from the adopted manufacturer tables.",
            "wall_girt_span_type_combo": "Wall-girt support arrangement used for manufacturer-table selection.",
            "g_load_input": "Main-roof permanent area load in kPa. Downward loading is handled internally.",
            "q_load_input": "Main-roof imposed area load in kPa.",
            "solar_load_input": "Additional solar or panel area load in kPa.",
            "fire_service_load_input": "Service load allowance associated with fire-service equipment, in kPa.",
            "hvac_load_input": "HVAC or other permanently supported plant area load in kPa.",
            "other_load_input": "Additional main-roof area load in kPa.",
            "crane_rating_input": "Nominal crane rating in tonnes. A positive value activates crane-column loads and automatic runway-beam selection.",
            "left_wall_load_input": "Additional horizontal line load on the left wall, in kN/m.",
            "right_wall_load_input": "Additional horizontal line load on the right wall, in kN/m.",
            "load_combination_combo": "Combination displayed in the plot and reaction results. Design checks use the full ULS/SLS envelope set.",
            "second_order_checkbox": "Iteratively includes geometric stiffness (P-delta effects). First-order analysis uses the faster batched solver.",
            "result_combo": "Select the structural diagram displayed on the plot.",
            "load_view_mode_combo": "Choose combined loads or individual load components for the plot.",
            "single_load_combo": "Individual load component displayed when single-load view is selected.",
            "deflection_scale_input": "Visual exaggeration factor for the deflected-shape plot. This does not change calculated deflections.",
            "diagram_scale_input": "Visual scale factor for action diagrams. This does not change analysis results.",
            "foundation_bearing_input": "Allowable soil bearing pressure in kPa for preliminary footing sizing.",
            "foundation_skin_friction_input": "Adopted shaft skin friction in kPa for preliminary pier uplift resistance.",
            "foundation_skin_bearing_checkbox": "Also include shaft skin friction when checking compressive bearing resistance.",
            "shs_cost_input": "Supply rate per kilogram for hollow sections.",
            "doubled_chord_cost_input": "All-in material and fabrication rate for the extra member in automatically doubled chord zones.",
            "ub_cost_input": "Supply rate per kilogram for UB, UC, PFC and similar open sections.",
            "wb_cost_input": "Supply rate per kilogram for welded beams.",
            "purlin_cost_input": "Supply rate per kilogram for purlins and girts.",
            "storage_enabled_checkbox": "Activates stored-material pressures, rail/stub design and storage panel checks.",
            "storage_load_path_combo": "Defines how stored-material pressure is transferred from panels into rails, stubs and columns.",
            "storage_panel_support_combo": "Selects the assumed vertical support condition for the storage wall panel.",
            "storage_density_input": "Stored-material bulk unit weight in kN/m³.",
            "storage_phi_input": "Stored-material internal friction angle in degrees.",
            "storage_beta_input": "Backfill or stored-surface slope in degrees.",
            "storage_height_input": "Maximum stored-material height in metres.",
            "storage_rail_height_input": "Horizontal storage rail height above base in metres.",
            "storage_reverse_direction_checkbox": "Reverses the storage pressure direction for the selected wall arrangement.",
            "crane_beam_profile_combo": "Runway-beam section. Automatic selection searches available UB/UC candidates when crane loading is active.",
            "crane_beam_pfc_cap_combo": "Channel cap attached to the crane runway beam for lateral and torsional performance.",
            "crane_beam_rail_profile_combo": "Crane rail size used for self weight and load eccentricity geometry.",
            "crane_beam_span_input": "Runway-beam span in metres; normally linked to the portal bay size.",
            "crane_beam_wheelbase_input": "Longitudinal crane wheel spacing in metres.",
            "crane_beam_buffer_input": ("Maximum long-travel buffer force per rail, kN (crane supplier's data sheet). "
                                        "Blank uses the project rule 9 + 2.8 x capacity (t) + 1.33 x crane span (m), "
                                        "fitted to supplier data sheets. Applied at ULS x 1.5 on both rails at the runway "
                                        "end stops, into the side-wall bracing at rail level."),
            "crane_beam_load_height_input": "Vertical offset from beam reference level to applied horizontal crane load, in metres.",
            "crane_beam_include_sw_deflection_checkbox": "Includes beam, cap and rail self weight in the runway vertical-deflection check.",
        }
        for name, tooltip in tooltips.items():
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setToolTip(tooltip)
                widget.setToolTipDuration(15000)

        for side in ["left", "right"]:
            side_name = side.title()
            side_tooltips = {
                f"{side}_canopy_length_input": f"{side_name} canopy/lean-to projection in metres. For canopies, changing this recalculates deep-end depth to retain about 300-350 mm at the shallow end.",
                f"{side}_canopy_type_combo": f"Select no {side} addition, a cantilever canopy, or an outer-column-supported lean-to.",
                f"{side}_canopy_frame_system_combo": "Truss uses chords and axial webs; Rafter uses a flexural member.",
                f"{side}_canopy_wind_model_combo": "Select main-roof pressure transfer, AS/NZS free-roof coefficients, or manually entered net Cpn.",
                f"{side}_canopy_underside_combo": "Free-roof underside condition used by the canopy wind-pressure model.",
                f"{side}_canopy_eave_height_input": "Optional connection height in metres. Blank uses the main-frame eave node.",
                f"{side}_canopy_pitch_input": "Canopy/lean-to roof pitch in degrees. Canopy depth updates automatically when this is edited.",
                f"{side}_canopy_shallow_depth_input": "For canopies, this is the deep-end truss depth; automatic sizing targets about 300-350 mm at the shallow end. For lean-tos it is the constant truss depth.",
                f"{side}_canopy_cpn_uplift_input": "Manual net uplift pressure coefficient used only with Manual net Cpn.",
                f"{side}_canopy_cpn_downward_input": "Manual net downward pressure coefficient used only with Manual net Cpn.",
                f"{side}_canopy_g_load_input": "Permanent area load on this canopy/lean-to in kPa.",
                f"{side}_canopy_q_load_input": "Imposed area load on this canopy/lean-to in kPa.",
                f"{side}_canopy_solar_load_input": "Additional solar or panel area load on this canopy/lean-to in kPa.",
                f"{side}_lean_outer_wall_clad_combo": "Whether the outer lean-to wall is clad; affects wind loading and column restraint assumptions.",
            }
            for name, tooltip in side_tooltips.items():
                widget = getattr(self, name, None)
                if widget is not None:
                    widget.setToolTip(tooltip)
                    widget.setToolTipDuration(15000)

        section_tooltip = "Selected steel section used for analysis, member checks and costing."
        for name in [
            "left_column_profile_combo", "right_column_profile_combo", "internal_column_profile_combo",
            "top_profile_combo", "web_profile_combo", "left_canopy_top_profile_combo",
            "left_canopy_web_profile_combo", "left_lean_column_profile_combo",
            "right_canopy_top_profile_combo", "right_canopy_web_profile_combo",
            "right_lean_column_profile_combo", "storage_rail_profile_combo", "storage_stub_profile_combo",
        ]:
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setToolTip(section_tooltip)
                widget.setToolTipDuration(15000)

        check_tooltip = "Hover summary only. See Section Checks for governing demand, capacity, load combination and assumptions."
        for name in [
            "left_column_check_label", "right_column_check_label", "internal_column_check_label",
            "top_chord_check_label", "bottom_chord_check_label", "web_check_label",
            "left_canopy_top_check_label", "left_canopy_bottom_check_label", "left_canopy_web_check_label",
            "left_lean_column_check_label", "right_canopy_top_check_label", "right_canopy_bottom_check_label",
            "right_canopy_web_check_label", "right_lean_column_check_label", "purlin_check_label",
            "wall_girt_check_label", "column_deflection_check_label", "truss_deflection_check_label",
            "end_wall_mullion_check_label", "end_wall_girt_check_label",
        ]:
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setToolTip(check_tooltip)
        split_tooltip = "Automatically selected light/heavy web sections and quantities from the current Generate + Solve demands."
        for name in ["web_split_label", "left_canopy_web_split_label", "right_canopy_web_split_label"]:
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setToolTip(split_tooltip)
        self.web_panel_check_button.setToolTip(
            "Manually compare the adjacent valid main-truss panel layouts. "
            "Generate + Solve does not run this additional search."
        )

    def section_selector_row(self, combo):
        row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row.setLayout(layout)
        down_button = QToolButton()
        up_button = QToolButton()
        down_button.setText("↓")
        up_button.setText("↑")
        down_button.setToolTip("Move one section smaller and solve")
        up_button.setToolTip("Move one section larger and solve")
        down_button.clicked.connect(lambda: self.step_section_combo(combo, -1))
        up_button.clicked.connect(lambda: self.step_section_combo(combo, +1))
        layout.addWidget(combo, 1)
        layout.addWidget(down_button)
        layout.addWidget(up_button)
        return row

    def two_widget_row(self, left_widget, right_widget):
        row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row.setLayout(layout)
        layout.addWidget(left_widget)
        layout.addWidget(right_widget)
        return row

    def crane_beam_selector_row(self, combo):
        row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row.setLayout(layout)
        down_button = QToolButton()
        up_button = QToolButton()
        down_button.setText("Down")
        up_button.setText("Up")
        down_button.setToolTip("Select the next smaller crane beam item and recalculate")
        up_button.setToolTip("Select the next larger crane beam item and recalculate")
        down_button.clicked.connect(lambda: self.step_crane_beam_combo(combo, -1))
        up_button.clicked.connect(lambda: self.step_crane_beam_combo(combo, +1))
        layout.addWidget(combo, 1)
        layout.addWidget(down_button)
        layout.addWidget(up_button)
        return row

    def depth_selector_row(self):
        return self.stepped_depth_row(
            self.depth_input,
            lambda delta: self.step_depth_input(self.depth_input, delta),
            "truss depth",
        )

    def canopy_depth_selector_row(self, depth_input):
        return self.stepped_depth_row(
            depth_input,
            lambda delta: self.step_depth_input(depth_input, delta),
            "canopy/lean-to depth",
        )

    def stepped_depth_row(self, depth_input, step_callback, label):
        row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row.setLayout(layout)
        down_button = QToolButton()
        up_button = QToolButton()
        down_button.setText("-50")
        up_button.setText("+50")
        down_button.setToolTip(f"Reduce {label} by 50 mm and solve")
        up_button.setToolTip(f"Increase {label} by 50 mm and solve")
        down_button.clicked.connect(lambda: step_callback(-50.0))
        up_button.clicked.connect(lambda: step_callback(50.0))
        layout.addWidget(depth_input, 1)
        layout.addWidget(down_button)
        layout.addWidget(up_button)
        return row

    def roof_load_placement_row(self, prefix):
        row = QWidget()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row.setLayout(layout)
        side_combo = QComboBox()
        side_combo.addItems(["Both", "Left", "Right"])
        from_input = QLineEdit("0")
        to_input = QLineEdit("100")
        from_input.setMaximumWidth(55)
        to_input.setMaximumWidth(55)
        setattr(self, f"{prefix}_load_side_combo", side_combo)
        setattr(self, f"{prefix}_load_from_input", from_input)
        setattr(self, f"{prefix}_load_to_input", to_input)
        layout.addWidget(side_combo)
        layout.addWidget(QLabel("from"))
        layout.addWidget(from_input)
        layout.addWidget(QLabel("to"))
        layout.addWidget(to_input)
        layout.addWidget(QLabel("%"))
        return row

    def step_section_combo(self, combo, direction):
        next_index = combo.currentIndex() + direction
        if next_index < 0 or next_index >= combo.count():
            return
        combo.setCurrentIndex(next_index)
        self.generate_and_solve()

    def step_crane_beam_combo(self, combo, direction):
        next_index = combo.currentIndex() + direction
        if next_index < 0 or next_index >= combo.count():
            return
        combo.setCurrentIndex(next_index)
        self.calculate_crane_beam_report()

    def step_depth_input(self, depth_input, delta_mm):
        if self.configurator_review is not None and depth_input is self.depth_input:
            minimum_mm, maximum_mm = review_truss_depth_bounds(
                self.configurator_review.prepared.inputs
            )
            selectable_maximum_mm = math.floor(maximum_mm / 50.0) * 50.0
            try:
                current = validate_review_truss_depth(
                    self.configurator_review.prepared.inputs,
                    depth_input.text(),
                )
            except ValueError:
                current = minimum_mm
            next_depth = min(max(current + delta_mm, minimum_mm), selectable_maximum_mm)
            next_depth = round(next_depth / 50.0) * 50.0
            depth_input.setText(f"{next_depth:.0f}")
            self.on_configurator_review_depth_edited()
            return
        try:
            current = float(depth_input.text())
        except ValueError:
            current = 0.0
        next_depth = max(50.0, current + delta_mm)
        depth_input.setText(f"{next_depth:.0f}")
        self.generate_and_solve()
        self.record_model_snapshot()
