"""Combined bracing selector, scoped checks, cost sizing and visual results."""
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QProgressDialog, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from bracing_analysis import AnalysisError
from bracing_analysis_desktop import text_report
from bracing_design import (DesignCriteria, auto_size, candidate_name, solve_selection,
                           global_sway_basis, panel_installation_rate)
from bracing_layout import BracingLayout, LayoutGeometry
from bracing_layout_ui import BracingLayoutDialog
from bracing_component_ui import ComponentEditor
from bracing_components import StrutPlan
from bracing_diaphragm import DiaphragmSettings
from bracing_layout_search import find_layout, LayoutSearchCancelled, LayoutSearchStopped
from purlin_beam_ui import PurlinBeamResults
from bracing_sizing_diagnostics import (SizingStopped, diagnostic_text, result_summary,
                                       governing_check_row, governing_case_member, check_type)


def workspace_available_geometry(parent=None):
    screen=parent.screen() if parent is not None else QApplication.primaryScreen()
    return screen.availableGeometry()


class BracingAnalysisDialog(QDialog):
    def __init__(self, context, catalog, saved=None, parent=None, read_only=False, cached_calculation=None):
        super().__init__(parent)
        self.setWindowTitle("3D bracing - layout, sections and checks")
        available = workspace_available_geometry(parent)
        self.setSizeGripEnabled(True)
        self.setWindowFlag(Qt.WindowMinMaxButtonsHint,True)
        self.setMinimumSize(min(640,max(1,available.width()-40)),min(360,max(1,available.height()-70)))
        self.resize(min(1420,max(1,available.width()-40)),min(860,max(1,available.height()-70)))
        self.context, self.catalog, self.read_only = context, catalog, read_only
        self.result, self.report_text, self._running = None, "", False
        saved = saved if isinstance(saved, dict) else {}
        self.adopted_mullion_section = saved.get('adopted_mullion_section')
        outer=QVBoxLayout(self)
        self.content_scroll=QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QScrollArea.NoFrame)
        content=QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0,0,0,0)
        self.content_scroll.setWidget(content)
        outer.addWidget(self.content_scroll,1)
        intro = QLabel("Block panels where braces cannot go, then Find layout and size. Selected panels can also be checked or sized as a fixed layout. Main building: gravity/end wind plus ULS side wind; production portal restraint and connections remain separate.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        toolbar = QHBoxLayout()
        self.run_button = QPushButton("Analyse and check")
        self.auto_button = QPushButton("Size selected layout")
        toolbar.addWidget(self.run_button)
        toolbar.addWidget(self.auto_button)
        self.serviceability_check = QCheckBox('Include serviceability')
        stored_criteria = saved.get('criteria',{}) if isinstance(saved.get('criteria'),dict) else {}
        setting = stored_criteria.get('include_serviceability',True)
        self.serviceability_check.setChecked(setting if isinstance(setting,bool) else True)
        self.serviceability_check.setEnabled(not read_only)
        self.serviceability_check.setToolTip('Checked: design for strength and global/local serviceability. Unchecked: strength only; deflections remain reported and stability remains required.')
        toolbar.addWidget(self.serviceability_check)
        self.sizing_issue_button = QPushButton("Review sizing issue")
        self.sizing_issue_button.hide()
        self.sizing_issue_button.clicked.connect(self.review_sizing_issue)
        toolbar.addWidget(self.sizing_issue_button)
        self.visual_mode = QComboBox()
        for label, value in (("Layout constraints", "layout"), ("Axial forces", "forces"), ("Governing utilisation", "utilisation"), ("Deformed shape", "deformed")):
            self.visual_mode.addItem(label, value)
        toolbar.addWidget(self.visual_mode)
        self.case_combo = QComboBox()
        self.case_combo.setMinimumContentsLength(20)
        self.case_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        toolbar.addWidget(self.case_combo, 1)
        self.deformation_scale = QDoubleSpinBox()
        self.deformation_scale.setRange(1, 500)
        self.deformation_scale.setValue(50)
        self.deformation_scale.setPrefix("Shape x ")
        toolbar.addWidget(self.deformation_scale)
        layout.addLayout(toolbar)
        search_bar = QHBoxLayout()
        self.layout_search_button = QPushButton('Find layout and size')
        self.layout_search_button.setEnabled(not read_only)
        self.keep_current_panels = QCheckBox('Keep selected panels as required')
        search_settings = saved.get('layout_search', {})
        search_settings = search_settings if isinstance(search_settings, dict) else {}
        self.layout_strategy = QComboBox()
        self.layout_strategy.addItem('Practical layout (fast)', 'practical')
        self.layout_strategy.addItem('Detailed cost search', 'cost')
        self.layout_strategy.setCurrentIndex(max(0, self.layout_strategy.findData(search_settings.get('strategy', 'practical'))))
        self.layout_strategy.setEnabled(not read_only)
        self.layout_strategy.setToolTip('Practical: end collectors, end-wall restraint and continuous eave roof-girder patterns. Heavy wall steel triggers an extra-bay rod comparison before compacting. Stops at the first preferred/lower-cost passing layout. Detailed: broader bay-pattern and cost exploration.')
        self.keep_current_panels.setChecked(search_settings.get('keep_selected') is True)
        self.keep_current_panels.setToolTip('Unchecked: reconsider current selections; all unblocked panels are candidates. Checked: every currently selected panel must remain.')
        self.layout_trial_budget = QSpinBox()
        self.layout_trial_budget.setRange(1, 160)
        default_budget = 6 if self.layout_strategy.currentData() == 'practical' else 48
        budget = search_settings.get('max_trials', default_budget) if 'strategy' in search_settings else default_budget
        self.layout_trial_budget.setValue(budget if isinstance(budget, int) and not isinstance(budget, bool) else default_budget)
        self.layout_trial_budget.setPrefix('Layout trials: ')
        self.layout_trial_budget.setToolTip('Practical mode: at most 12 exact analyses per sized layout, with feasibility first. A passing heavy-wall layout can compare up to two extra-bay rod arrangements, then up to four compact patterns, all sharing this layout budget. Detailed mode explores more layouts.')
        self.layout_strategy.currentIndexChanged.connect(lambda: self.layout_trial_budget.setValue(6 if self.layout_strategy.currentData() == 'practical' else 48))
        self.keep_current_panels.setEnabled(not read_only)
        self.layout_trial_budget.setEnabled(not read_only)
        search_bar.addWidget(self.layout_search_button)
        search_bar.addWidget(self.layout_strategy)
        search_bar.addWidget(self.keep_current_panels)
        search_bar.addWidget(self.layout_trial_budget)
        search_bar.addStretch()
        layout.addLayout(search_bar)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.result_summary_box = QGroupBox('Result summary')
        summary_row = QHBoxLayout(self.result_summary_box)
        self.result_summary_label = QLabel()
        self.result_summary_label.setTextFormat(Qt.PlainText)
        self.result_summary_label.setWordWrap(True)
        self.result_summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        summary_row.addWidget(self.result_summary_label, 1)
        self.movement_button = QPushButton('Review movement')
        self.movement_button.clicked.connect(self.review_movement)
        summary_row.addWidget(self.movement_button)
        layout.addWidget(self.result_summary_box)
        splitter = QSplitter(Qt.Horizontal)
        nominal = context.get("nominal_geometry") or LayoutGeometry.from_dict(context["nominal_layout"]["geometry"])
        self.layout_editor = BracingLayoutDialog(nominal, context["nominal_layout"], self, read_only)
        editor = self.layout_editor
        editor.embedded = True
        editor.setWindowFlags(Qt.Widget)
        editor.layout().setContentsMargins(0, 0, 0, 0)
        editor.layout().itemAt(0).widget().hide()
        for button in editor.findChildren(QPushButton):
            if button.text() in {"Save locations", "Cancel", "Close"}:
                button.hide()
        for label in editor.findChildren(QLabel):
            if label.text().startswith("Saving locations does not change"):
                label.hide()
        camera = QHBoxLayout()
        controls = editor.layout().itemAt(1).layout()
        for widget in (editor.view_combo, editor.orbit_horizontal, editor.orbit_vertical):
            controls.removeWidget(widget)
            camera.addWidget(widget)
        show_list = QCheckBox("Panel list")
        camera.addWidget(show_list)
        self.cladding_overlay = QCheckBox('Clad walls')
        self.cladding_overlay.setChecked(bool(saved.get('show_cladding_overlay', True)))
        self.cladding_overlay.setToolTip('Translucent actual wall cladding for orientation; visual reference only.')
        camera.addWidget(self.cladding_overlay)
        camera.addStretch()
        editor.layout().insertLayout(2, camera)
        middle = editor.layout().itemAt(3).layout()
        side = middle.takeAt(1).layout()
        self.panel_list_container = QWidget()
        self.panel_list_container.setLayout(side)
        middle.addWidget(self.panel_list_container)
        self.panel_list_container.hide()
        show_list.toggled.connect(self.panel_list_container.setVisible)
        # User-selected fixed convention, independent of older saved preferences.
        editor.orbit_horizontal.setChecked(True)
        editor.orbit_vertical.setChecked(False)
        self.canvas = editor.canvas
        self.canvas.set_cladding(context.get('face_cladding', {}))
        self.canvas.show_cladding = self.cladding_overlay.isChecked()
        self.cladding_overlay.toggled.connect(self.set_cladding_overlay)
        self.canvas.setMinimumHeight(280)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        for label in (editor.location_label, editor.legend_label, editor.status_label, editor.geometry_note):
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        splitter.addWidget(editor)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(465)
        self.input_page = QWidget()
        inputs = QVBoxLayout(self.input_page)
        form = QFormLayout()
        self.strut_profiles = sorted([*catalog.shs_profiles(), *catalog.rhs_profiles()], key=lambda p: (p.A, p.name))
        self.post_profiles = sorted(catalog.end_wall_mullion_profiles(), key=lambda p: (p.A, p.name))
        self.strut_combo, self.post_combo = QComboBox(), QComboBox()
        self.populate(self.strut_combo, self.strut_profiles, saved.get("strut_section"), 1500)
        post_name = saved.get('end_post_section') if not context.get('mullion_basis') or saved.get('mullion_basis_version')=='main_combined_v1' else None
        self.populate(self.post_combo, self.post_profiles, post_name or context.get("default_end_post"), 2500)
        self.component_editor = ComponentEditor(context, self.strut_profiles,
            catalog.profile_by_name.get(self.strut_combo.currentData(), self.strut_profiles[0]), saved, read_only)
        form.addRow("Uniform steel (alternative model)", self.strut_combo)
        form.addRow("Mullions: main + bracing check" if context.get('mullion_basis') else "End-wall posts", self.post_combo)
        from bracing_spacing import max_bay_spacing
        self.max_braced_bay_spacing=QSpinBox()
        self.max_braced_bay_spacing.setRange(1,50)
        self.max_braced_bay_spacing.setSuffix(' bays')
        self.max_braced_bay_spacing.setValue(max_bay_spacing({'max_braced_bay_spacing':search_settings.get('max_braced_bay_spacing',max_bay_spacing(context))}))
        self.max_braced_bay_spacing.setEnabled(not read_only)
        self.max_braced_bay_spacing.setToolTip('Project load-path rule: a complete roof-braced bay with aligned side-wall bracing in every N consecutive bays. Default 5; continuous narrow eave strips alone do not count. Find layout adds intermediate bays around blocked panels; fixed-layout checks also report this limit.')
        form.addRow('Maximum braced-bay spacing',self.max_braced_bay_spacing)
        if context.get('mullion_basis'):
            note=QLabel(f"Main mullion: {context.get('default_end_post')}. Uses the same wind envelope, properties and flybrace basis, plus bracing axial force. A checked adopted section updates main results/costing on Save and close. Main strength/deflection prerequisites apply in either bracing mode.")
            note.setWordWrap(True);inputs.addWidget(note)
        self.brace_combos = {}
        for group, label in (("roof_brace", "Roof X braces"), ("wall_brace", "Side-wall X braces"), ("end_brace", "End-wall X braces")):
            combo = QComboBox()
            for brace in self.component_editor.brace_candidates():
                combo.addItem(brace["label"], brace)
            name = saved.get(f"{group}_label") or context[group]["label"]
            index = combo.findText(name)
            if index < 0:
                combo.addItem(f"Missing saved brace: {name}", None)
                index = combo.count()-1
            combo.setCurrentIndex(index)
            self.brace_combos[group] = combo
            form.addRow(label, combo)
        self.end_brace_combo = self.brace_combos["end_brace"]
        inputs.addLayout(form)
        group = QGroupBox("Section-check assumptions")
        grid = QGridLayout(group)
        stored = saved.get("criteria", {}) if isinstance(saved.get("criteria"), dict) else {}
        labels = ("Angle / rod Fy (MPa)", "Brace Fu (not used in gross yield)", "Other member tension An/Ag", "Other member tension kt", "Strut effective K", "Post effective K", "Local member / purlin L/n")
        self.criteria_inputs = {}
        numeric_criteria = {k:v for k,v in asdict(DesignCriteria()).items() if k not in ('include_serviceability','global_sls_divisor')}
        for i, ((name, value), label) in enumerate(zip(numeric_criteria.items(), labels)):
            box = QDoubleSpinBox()
            box.setDecimals(2)
            box.setRange(.01, 1. if name in {"net_area_factor", "tension_distribution_factor"} else 5. if name.endswith("ke") else 2000.)
            try:
                box.setValue(float(stored.get(name, value)))
            except (TypeError, ValueError):
                box.setValue(value)
            if name == 'brace_fy_mpa':
                box.setValue(300.)
                box.setReadOnly(True)
                box.setToolTip('User-confirmed Grade 300 angle and rod braces. Scheduled rod strengths are already factored and remain unchanged.')
            if name in {'net_area_factor', 'tension_distribution_factor'}:
                box.setToolTip('Used for other calculated member/purlin net-fracture tension checks. Scheduled rods/ropes and gross-yield steel braces are excluded. Does not reduce steel member-body compression area.')
            if name=='post_ke' and context.get('mullion_basis'):
                label='Mullion K × main restraint length'
                box.setToolTip('Multiplier on the main calculation effective length (H/2 with its assumed mid-height flybrace). Open ends use full height. Main mullion prerequisites remain mandatory.')
            grid.addWidget(QLabel(label), i//2, 2*(i%2))
            grid.addWidget(box, i//2, 2*(i%2)+1)
            self.criteria_inputs[name] = box
        auto_basis=global_sway_basis({'source_preset':context.get('source_preset',{})},DesignCriteria(),context)
        explicit_global=stored.get('global_sls_divisor')
        # A legacy non-default combined H/n/L/n was an explicit user choice.
        # Preserve it globally; the legacy default 150 becomes automatic sway.
        if 'global_sls_divisor' not in stored:
            try:
                old_divisor=float(stored.get('sls_divisor',150.))
                if old_divisor>0 and old_divisor!=150.:
                    explicit_global=old_divisor
            except (TypeError,ValueError):
                pass
        self.global_sls_auto=QCheckBox('Global sway: follow main in-plane criterion')
        self.global_sls_auto.setChecked(explicit_global is None)
        self.global_sls_auto.setEnabled(not read_only)
        self.global_sls_input=QDoubleSpinBox()
        self.global_sls_input.setDecimals(2);self.global_sls_input.setRange(.01,1000000.)
        self.global_sls_input.setValue(float(explicit_global if explicit_global is not None else auto_basis['divisor']))
        self.global_sls_input.setToolTip('Global longitudinal H/n only. Project default IL1 H/100, IL2+ H/150. Local member/purlin deflection checks retain their separate L/n.')
        grid.addWidget(self.global_sls_auto,4,0,1,4)
        grid.addWidget(QLabel('Global longitudinal sway H/'),5,0)
        grid.addWidget(self.global_sls_input,5,1)
        def refresh_global_sls(*_args):
            if self.global_sls_auto.isChecked():
                self.global_sls_input.setValue(auto_basis['divisor'])
            self.global_sls_input.setEnabled(not read_only and not self.global_sls_auto.isChecked())
        refresh_global_sls()
        self.global_sls_auto.toggled.connect(refresh_global_sls)
        inputs.addWidget(group)
        diaphragm_group = QGroupBox('SLS stressed-skin roof and wall contribution')
        diaphragm_form = QFormLayout(diaphragm_group)
        try:
            diaphragm_saved = DiaphragmSettings.from_dict(saved.get('stressed_skin'))
        except AnalysisError:
            diaphragm_saved = DiaphragmSettings()
        self.diaphragm_calibration_mode = QComboBox()
        self.diaphragm_calibration_mode.addItem('Rev B reference c + panel geometry','rev_b_geometry')
        self.diaphragm_calibration_mode.addItem('Direct panel c (sensitivity override)','direct_c')
        self.diaphragm_calibration_mode.setCurrentIndex(self.diaphragm_calibration_mode.findData(diaphragm_saved.calibration_mode))
        self.diaphragm_calibration_mode.setEnabled(not read_only)
        self.rev_b_button = QPushButton('Apply Rev B roof + walls (3 / 6)')
        self.rev_b_button.setToolTip('Enable roof and clad side/end-wall SLS diaphragms, set reference c to 3/6 mm/kN and calculate actual panel c using the documented empirical geometry proxy.')
        self.rev_b_button.setEnabled(not read_only)
        diaphragm_form.addRow('Flexibility calculation',self.diaphragm_calibration_mode)
        diaphragm_form.addRow(self.rev_b_button)
        self.stressed_skin_check = QCheckBox('Use technical-note equivalent roof diagonals for SLS')
        self.stressed_skin_check.setChecked(diaphragm_saved.enabled)
        self.stressed_skin_check.setEnabled(not read_only)
        self.diaphragm_flexibility = QDoubleSpinBox()
        self.diaphragm_flexibility.setDecimals(4)
        self.diaphragm_flexibility.setRange(.0001, 1000000.)
        self.diaphragm_flexibility.setValue(diaphragm_saved.flexibility_mm_per_kn)
        self.diaphragm_direction = QComboBox()
        self.diaphragm_direction.addItem('Along roof slope (technical note)', 'along_slope')
        self.diaphragm_direction.addItem('Longitudinal (adopt a directional c)', 'longitudinal')
        self.diaphragm_direction.setCurrentIndex(self.diaphragm_direction.findData(diaphragm_saved.shear_direction))
        diaphragm_form.addRow(self.stressed_skin_check)
        diaphragm_form.addRow('Adopted panel c (mm/kN)', self.diaphragm_flexibility)
        diaphragm_form.addRow('Shear displacement defining c', self.diaphragm_direction)
        diaphragm_note = QLabel('Rev B roof baseline: 3.0 mm/kN at 8.5 m across ribs x 12.104 m along slope. Geometry mode calculates each panel c = c_reference x [(a/b²)/(a_reference/b_reference²)], with a across ribs and b along ribs. This is the note\'s provisional empirical screening proxy, not a validated whole-panel/ECCS law. Along-slope shear defines roof c; the current wind is longitudinal. Direct-c mode retains adjustable panel inputs. The baseline factors and actual dimensions are recorded in Full results.')
        diaphragm_note.setWordWrap(True)
        diaphragm_form.addRow(diaphragm_note)
        self.side_wall_check = QCheckBox('Include clad side-wall diaphragms (SLS)')
        self.side_wall_check.setChecked(diaphragm_saved.side_walls_enabled)
        self.end_wall_check = QCheckBox('Include clad end-wall diaphragms (SLS)')
        self.end_wall_check.setChecked(diaphragm_saved.end_walls_enabled)
        self.side_wall_flexibility, self.end_wall_flexibility = QDoubleSpinBox(), QDoubleSpinBox()
        for box,value in ((self.side_wall_flexibility,diaphragm_saved.side_wall_flexibility_mm_per_kn),
                          (self.end_wall_flexibility,diaphragm_saved.end_wall_flexibility_mm_per_kn)):
            box.setDecimals(4); box.setRange(.0001,1000000.); box.setValue(value)
        for check in (self.side_wall_check,self.end_wall_check):
            check.setEnabled(not read_only)
        diaphragm_form.addRow(self.side_wall_check)
        diaphragm_form.addRow('Trial side-wall c (mm/kN)',self.side_wall_flexibility)
        diaphragm_form.addRow(self.end_wall_check)
        diaphragm_form.addRow('Trial end-wall c (mm/kN)',self.end_wall_flexibility)
        wall_note = QLabel('Rev B wall baseline: 6.0 mm/kN at 7 m width x 6 m height, for its assumed 0.42 BMT pan-fixed five-rib assembly. Geometry mode uses actual width and height (mean corner height for sloping ends); both wall references remain editable. Smaller c gives greater stiffness. Other profiles, fixings or supports need renewed calibration/review. Open faces are excluded; wall openings, slip, collector and fixed-anchor capacities remain separate. No ridge-flashing shear credit. Global sway does not upsize struts; ULS and required local deflection checks remain.')
        wall_note.setWordWrap(True)
        diaphragm_form.addRow(wall_note)
        def refresh_diaphragm(*_args):
            geometry = self.diaphragm_calibration_mode.currentData()=='rev_b_geometry'
            if geometry:
                self.diaphragm_direction.setCurrentIndex(self.diaphragm_direction.findData('along_slope'))
            prefix = 'Reference' if geometry else 'Direct panel'
            diaphragm_form.labelForField(self.diaphragm_flexibility).setText(prefix+' roof c (mm/kN)')
            diaphragm_form.labelForField(self.side_wall_flexibility).setText(prefix+' side-wall c (mm/kN)')
            diaphragm_form.labelForField(self.end_wall_flexibility).setText(prefix+' end-wall c (mm/kN)')
            active = self.stressed_skin_check.isChecked() and not read_only
            self.diaphragm_flexibility.setEnabled(active)
            self.diaphragm_direction.setEnabled(active and not geometry)
            self.side_wall_flexibility.setEnabled(self.side_wall_check.isChecked() and not read_only)
            self.end_wall_flexibility.setEnabled(self.end_wall_check.isChecked() and not read_only)
        refresh_diaphragm()
        self.stressed_skin_check.toggled.connect(refresh_diaphragm)
        self.side_wall_check.toggled.connect(refresh_diaphragm)
        self.end_wall_check.toggled.connect(refresh_diaphragm)
        self.diaphragm_calibration_mode.currentIndexChanged.connect(refresh_diaphragm)
        def use_rev_b():
            self.diaphragm_flexibility.setValue(3.)
            self.side_wall_flexibility.setValue(6.)
            self.end_wall_flexibility.setValue(6.)
            self.stressed_skin_check.setChecked(True)
            self.side_wall_check.setChecked(True)
            self.end_wall_check.setChecked(True)
            self.diaphragm_calibration_mode.setCurrentIndex(self.diaphragm_calibration_mode.findData('rev_b_geometry'))
            self.invalidate()
        self.rev_b_button.clicked.connect(use_rev_b)
        inputs.addWidget(diaphragm_group)
        def refresh_tension(*_args):
            self.component_editor.refresh_tension_preview(self.criteria_inputs['net_area_factor'].value(),
                self.criteria_inputs['tension_distribution_factor'].value())
        refresh_tension()
        for field in ('net_area_factor', 'tension_distribution_factor'):
            self.criteria_inputs[field].valueChanged.connect(refresh_tension)
        notes = QLabel("Per-location roof-purlin reuse is the default: use Components / ratings for connected counts, additions and supplier ratings. Uncheck that mode for the uniform dedicated-steel approximation. The supplied schedule sets Grade 300 rod and Grade 1570 wire strengths. Wire sizes 8/10/12/14/16 mm default to the interim 200 GPa / nominal-area stiffness assumption; declared EA/source can override it. Project rope rates and matching turnbuckle prices/allowances are loaded; grips and other terminations are excluded.\n\nSteel braces use the adopted 0.9 Ag fy member-only tension check; connections remain separate. Scheduled rods already include 0.9. Net-area/kt applies to other calculated member/purlin tension, not gross-yield braces or supplied factored strengths. Steel strut/post compression uses gross body area with local/global buckling; posts include end-wind bending. Purlins use the supplied simplified compression/bending method and G450 minima. Sizing reanalyses candidates; it is not a global optimum. Panel installation is allowed for below; other connections, labour and supplier availability remain separate.")
        notes.setWordWrap(True)
        inputs.addWidget(notes)
        rates = context["cost_rates"]
        rate_note = QLabel(f"Captured Costing rates ($/kg): hollow {rates['shs']:.2f}; rolled {rates['ub']:.2f}; welded {rates['wb']:.2f}.")
        rate_note.setWordWrap(True)
        inputs.addWidget(rate_note)
        install_row = QHBoxLayout()
        install_row.addWidget(QLabel('Installation per braced X panel'))
        self.panel_installation_input = QDoubleSpinBox()
        self.panel_installation_input.setRange(0., 1000000.)
        self.panel_installation_input.setDecimals(2)
        self.panel_installation_input.setSingleStep(10.)
        self.panel_installation_input.setPrefix('$ ')
        try:
            installation_rate = panel_installation_rate({'braced_panel_installation': saved.get(
                'installation_cost_per_panel', rates.get('braced_panel_installation', 50.))})
        except AnalysisError:
            installation_rate = 50.
        self.panel_installation_input.setValue(installation_rate)
        self.panel_installation_input.setEnabled(not read_only)
        self.panel_installation_input.setToolTip('Once for each physical crossed-brace panel on roof or walls, including both diagonals. SLS diaphragm equivalents are excluded. Included in layout cost comparisons; not a complete installation quote.')
        install_row.addWidget(self.panel_installation_input)
        inputs.addLayout(install_row)
        inputs.addStretch()
        input_scroll = QScrollArea()
        input_scroll.setWidgetResizable(True)
        input_scroll.setWidget(self.input_page)
        self.tabs.addTab(input_scroll, "Sections / criteria")
        force_page = QWidget()
        forces = QVBoxLayout(force_page)
        self.member_detail = QLabel("Click a member in the view or table for its governing check.")
        self.member_detail.setWordWrap(True)
        forces.addWidget(self.member_detail)
        self.member_table = QTableWidget()
        self.member_table.setColumnCount(8)
        self.member_table.setHorizontalHeaderLabels(["Member", "Section", "Governing axial kN", "Governing UC", 'Check type', "Governing check", "Governing case", "Governing state"])
        self.member_table.horizontalHeaderItem(2).setToolTip('Total member/assembly axial force in the case governing its utilisation. Individual ply forces are in the member detail. The diagram and selected-case detail follow the display case. Stiffness-only diaphragm rows show selected-case forces.')
        self.member_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.member_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.member_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        forces.addWidget(self.member_table)
        self.tabs.addTab(force_page, "Member checks")
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.tabs.addTab(self.report, "Full results")
        self.tabs.addTab(self.component_editor, "Components / ratings")
        self.beam_results = PurlinBeamResults()
        self.tabs.addTab(self.beam_results, "Purlin beam results")
        splitter.addWidget(self.tabs)
        splitter.setSizes([800, 600])
        layout.addWidget(splitter, 1)
        footer = QHBoxLayout()
        self.export_text_button, self.export_json_button = QPushButton("Export TXT..."), QPushButton("Export JSON...")
        footer.addWidget(self.export_text_button)
        footer.addWidget(self.export_json_button)
        footer.addStretch()
        self.save_button, self.cancel_button = QPushButton("Close" if read_only else "Save and close"), QPushButton("Cancel")
        self.save_button.setToolTip('Retain layout, sections and current calculations. Saving the building preset also saves the calculations.')
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.save_button)
        outer.addLayout(footer)
        self.run_button.clicked.connect(self.run_analysis)
        self.auto_button.clicked.connect(self.run_auto_size)
        self.layout_search_button.clicked.connect(self.run_layout_search)
        self.save_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        self.export_json_button.clicked.connect(lambda: self.export(True))
        self.export_text_button.clicked.connect(lambda: self.export(False))
        for combo in (self.strut_combo, self.post_combo, *self.brace_combos.values()):
            combo.setEnabled(not read_only)
            combo.currentIndexChanged.connect(self.invalidate)
        for box in self.criteria_inputs.values():
            box.setEnabled(not read_only)
            box.valueChanged.connect(self.invalidate)
        self.serviceability_check.toggled.connect(self.invalidate)
        self.global_sls_auto.toggled.connect(self.invalidate)
        self.global_sls_input.valueChanged.connect(self.invalidate)
        self.stressed_skin_check.toggled.connect(self.invalidate)
        self.diaphragm_flexibility.valueChanged.connect(self.invalidate)
        self.diaphragm_direction.currentIndexChanged.connect(self.invalidate)
        self.diaphragm_calibration_mode.currentIndexChanged.connect(self.invalidate)
        self.side_wall_check.toggled.connect(self.invalidate)
        self.end_wall_check.toggled.connect(self.invalidate)
        self.side_wall_flexibility.valueChanged.connect(self.invalidate)
        self.end_wall_flexibility.valueChanged.connect(self.invalidate)
        editor.layoutChanged.connect(self.invalidate)
        self.component_editor.changed.connect(self.invalidate)
        self.panel_installation_input.valueChanged.connect(self.invalidate)
        self.max_braced_bay_spacing.valueChanged.connect(self.invalidate)
        self.visual_mode.currentIndexChanged.connect(self.change_visual_mode)
        self.case_combo.currentIndexChanged.connect(self.show_case)
        self.deformation_scale.valueChanged.connect(self.change_visual_mode)
        self.canvas.memberClicked.connect(self.inspect_member)
        self.member_table.currentCellChanged.connect(self.select_table_member)
        self.invalidate()
        self.restore_calculation(cached_calculation)

    def closeEvent(self, event):
        if self._running:
            event.ignore()
            return
        # Window X has the same save behaviour as Save and close. Cancel stays
        # an explicit discard of edits, preserving the existing transaction.
        self.accept()
        event.accept()

    def reject(self):
        if not self._running:
            super().reject()

    def calculation_cache(self):
        if self.result is None:
            return None
        from bracing_calculation_cache import fingerprint, pack
        signature = fingerprint(self.current_context(),self.snapshot(),self.selections(),self.criteria())
        return pack(self.result,signature,{'case':self.case_combo.currentText(),
                    'visual_mode':self.visual_mode.currentData(),'shape_scale':self.deformation_scale.value(),
                    'member':self.canvas.member_selected})

    def restore_calculation(self, cache):
        if not cache:
            return False
        from bracing_calculation_cache import fingerprint, unpack
        try:
            signature = fingerprint(self.current_context(),self.snapshot(),self.selections(),self.criteria())
            result = unpack(cache,signature)
            if result is None:
                return False
            self.display_result(result)
            if result.get('sizing_failure'):
                self.report_text = diagnostic_text(result['sizing_failure'])+'\n\n'+self.report_text
                self.report.setPlainText(self.report_text)
                self.sizing_issue_button.show()
            view = cache.get('view',{})
            index = self.case_combo.findText(str(view.get('case','')))
            if index>=0:
                self.case_combo.setCurrentIndex(index)
            index = self.visual_mode.findData(view.get('visual_mode'))
            if index>=0:
                self.visual_mode.setCurrentIndex(index)
            self.deformation_scale.setValue(float(view.get('shape_scale',50.)))
            visible_ids = {m['id'] for m in self.canvas.display_members()}
            if view.get('member') in visible_ids:
                self.inspect_member(view['member'])
            self.status_label.setText('Saved calculation restored. '+self.status_label.text())
            return True
        except (AnalysisError,ValueError,TypeError,KeyError,IndexError,ArithmeticError):
            self.invalidate()
            return False

    @staticmethod
    def populate(combo, profiles, name, area):
        for p in profiles:
            combo.addItem(f"{p.name} (A = {p.A:.0f} mm2)", p.name)
        name = name or (min(profiles, key=lambda p: abs(p.A-area)).name if profiles else None)
        index = combo.findData(name)
        if name and index < 0:
            combo.addItem(f"Missing saved section: {name}", None)
            index = combo.count()-1
        combo.setCurrentIndex(max(0, index))

    def criteria(self):
        return DesignCriteria(**{name: box.value() for name, box in self.criteria_inputs.items()},
                              global_sls_divisor=None if self.global_sls_auto.isChecked() else self.global_sls_input.value(),
                              include_serviceability=self.serviceability_check.isChecked())

    def snapshot(self):
        return {"schema": "portalcalc.bracing-analysis-settings.v1", "strut_section": self.strut_combo.currentData(),
                "end_post_section": self.post_combo.currentData(),
                **{f"{g}_label": (c.currentData() or {}).get("label") for g, c in self.brace_combos.items()},
                "criteria": asdict(self.criteria()),
                'installation_cost_per_panel': self.panel_installation_input.value(),
                'layout_search': {'keep_selected': self.keep_current_panels.isChecked(),
                                  'max_trials': self.layout_trial_budget.value(),
                                  'max_braced_bay_spacing':self.max_braced_bay_spacing.value(),
                                  'strategy': self.layout_strategy.currentData()},
                'stressed_skin': asdict(DiaphragmSettings(self.stressed_skin_check.isChecked(),
                    self.diaphragm_flexibility.value(), self.diaphragm_direction.currentData(),
                    self.side_wall_check.isChecked(),self.side_wall_flexibility.value(),
                    self.end_wall_check.isChecked(),self.end_wall_flexibility.value(),
                    self.diaphragm_calibration_mode.currentData())),
                "invert_orbit_horizontal": self.canvas.invert_orbit_horizontal,
                "invert_orbit_vertical": self.canvas.invert_orbit_vertical,
                'show_cladding_overlay': self.cladding_overlay.isChecked(),
                **({'mullion_basis_version':'main_combined_v1'} if self.context.get('mullion_basis') else {}),
                **({'adopted_mullion_section':self.adopted_mullion_section} if self.adopted_mullion_section else {}),
                **self.component_editor.snapshot()}

    def layout_snapshot(self):
        return self.layout_editor.snapshot()

    def valid_settings(self):
        return (all(c.currentData() in self.catalog.profile_by_name for c in (self.strut_combo, self.post_combo))
                and all(c.currentData() is not None for c in self.brace_combos.values()))

    def selections(self, context=None):
        fallback = self.catalog.profile_by_name[self.strut_combo.currentData()]
        wires = {option["label"]: option for option in self.component_editor.brace_candidates()}
        strut = self.component_editor.plan(context if context is not None else self.current_context(), fallback) if self.component_editor.mixed.isChecked() else fallback
        return {"strut": strut,
                "end_post": self.catalog.profile_by_name[self.post_combo.currentData()],
                **{g: wires.get((c.currentData() or {}).get("label"), c.currentData()) for g, c in self.brace_combos.items()}}

    def current_context(self, require_selected=True):
        editor = self.layout_editor
        if editor.model.geometry != editor.current_geometry:
            raise AnalysisError("Dimensions changed: update the saved layout geometry before analysis")
        payload = editor.snapshot()
        if require_selected and not payload["pinned_panels"]:
            raise AnalysisError("Pin the X-braced panels to install before running")
        actual = BracingLayout(self.context["layout"].geometry, payload["blocked_panels"], payload["pinned_panels"])
        source = copy.deepcopy(self.context["source_preset"])
        source.setdefault("bracing", {}).update(layout=payload, analysis_3d=self.snapshot())
        return {**self.context, "layout": actual, "nominal_layout": payload, "source_preset": source,
                'max_braced_bay_spacing':self.max_braced_bay_spacing.value(),
                'cost_rates': {**self.context['cost_rates'], 'braced_panel_installation': self.panel_installation_input.value()},
                'stressed_skin': self.snapshot()['stressed_skin'],
                'brace_policy': self.component_editor.snapshot()['brace_policy'],
                "source_hash": hashlib.sha256(json.dumps(source, sort_keys=True, allow_nan=False).encode()).hexdigest()}

    def invalidate(self):
        if self._running:
            return
        self.result, self.report_text = None, ""
        self.sizing_issue_button.hide()
        self.result_summary_box.hide()
        self.result_summary_label.clear()
        self.movement_button.setEnabled(False)
        self.layout_editor.save_button.hide()
        self.component_editor.refresh_bracing(self.layout_editor.model.pinned)
        self.report.clear()
        self.case_combo.clear()
        self.member_table.setRowCount(0)
        self.canvas.set_result(None)
        self.beam_results.set_result(None)
        self.visual_mode.setCurrentIndex(0)
        self.change_visual_mode()
        self.export_json_button.setEnabled(False)
        self.export_text_button.setEnabled(False)
        self.run_button.setEnabled(self.valid_settings() and bool(self.layout_editor.model.pinned))
        self.strut_combo.setEnabled(not self.component_editor.mixed.isChecked() and not self.read_only)
        self.auto_button.setEnabled(self.valid_settings() and bool(self.layout_editor.model.pinned) and not self.read_only)
        self.auto_button.setText('Size selected layout' if self.component_editor.project_use.currentData() == 'unspecified'
                                 else 'Size selected: preference / cost')
        self.layout_search_button.setEnabled(self.valid_settings() and bool(self.layout_editor.model.available) and not self.read_only)
        self.member_detail.setText("Click a member in the view or table for its governing check.")
        self.status_label.setText(f"{len(self.layout_editor.model.blocked)} blocked; {len(self.layout_editor.model.pinned)} selected X panels. Find a layout from unblocked panels, or check/size the selected layout. Input changes clear previous results.")

    def set_busy(self, busy):
        self._running = busy
        self.layout_editor.setEnabled(not busy)
        self.input_page.setEnabled(not busy)
        self.component_editor.setEnabled(not busy)
        self.run_button.setEnabled(not busy and self.valid_settings() and bool(self.layout_editor.model.pinned))
        self.auto_button.setEnabled(not busy and self.valid_settings() and bool(self.layout_editor.model.pinned) and not self.read_only)
        self.layout_search_button.setEnabled(not busy and self.valid_settings() and bool(self.layout_editor.model.available) and not self.read_only)
        self.keep_current_panels.setEnabled(not busy and not self.read_only)
        self.layout_trial_budget.setEnabled(not busy and not self.read_only)
        self.layout_strategy.setEnabled(not busy and not self.read_only)
        self.serviceability_check.setEnabled(not busy and not self.read_only)
        self.save_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)

    def run_analysis(self):
        self.perform(False)

    def run_auto_size(self):
        if not self.read_only:
            self.perform(True)

    def adopt_sections(self, chosen):
        for group, combo in (("strut", self.strut_combo), ("end_post", self.post_combo)):
            if group == 'strut' and isinstance(chosen[group], StrutPlan):
                self.component_editor.adopt(chosen[group])
            else:
                with QSignalBlocker(combo):
                    combo.setCurrentIndex(combo.findData(chosen[group].name))
        for group, combo in self.brace_combos.items():
            with QSignalBlocker(combo):
                combo.setCurrentIndex(combo.findText(candidate_name(chosen[group])))

    def run_layout_search(self):
        if self._running or self.read_only or not self.valid_settings():
            return
        self.invalidate()
        progress = QProgressDialog('Searching available panels...', 'Cancel', 0, self.layout_trial_budget.value(), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        self.set_busy(True)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            context = self.current_context(require_selected=False)
            def update(step, total, message):
                progress.setLabelText(message)
                # Keep the dialog open until the transaction has been adopted.
                progress.setValue(min(step, total-1))
                QApplication.processEvents()
                if progress.wasCanceled():
                    raise LayoutSearchCancelled('Layout search cancelled; layout and sections are unchanged')
            candidates = {'strut': self.strut_profiles, 'end_post': self.post_profiles,
                          **{g: self.component_editor.brace_candidates() for g in self.brace_combos}}
            required = set(context['layout'].pinned) if self.keep_current_panels.isChecked() else set()
            result, chosen, selected = find_layout(context, self.selections(context), candidates,
                self.criteria(), context['cost_rates'], required=required, progress=update,
                max_trials=self.layout_trial_budget.value(), strategy=self.layout_strategy.currentData())
            from bracing_spacing import spacing_assessment
            if (not result['design']['passes'] or not spacing_assessment(selected,self.max_braced_bay_spacing.value())['passes'] or selected.blocked != context['layout'].blocked
                    or selected.pinned & selected.blocked or not required <= selected.pinned):
                raise AnalysisError('Layout search returned an unverified or inconsistent candidate; layout and sections unchanged')
            # Adopt only a checked success, as one undoable layout edit.
            editor = self.layout_editor
            editor.model = BracingLayout(editor.model.geometry, editor.model.blocked, selected.pinned)
            editor.remember()
            editor.save_button.hide()
            self.component_editor.refresh_bracing(selected.pinned)
            self.adopt_sections(chosen)
            self.display_result(result)
            self.status_label.setText(f'Layout search: {len(selected.pinned)} selected X panels; {len(selected.blocked)} blocked panel(s) retained. '+self.status_label.text())
        except LayoutSearchStopped as error:
            self.status_label.setText(str(error)+' Layout and sections unchanged.')
            self.report.setPlainText(str(error)+'\n\n'+json.dumps(error.evidence, indent=2))
        except (AnalysisError, ValueError, ArithmeticError) as error:
            self.status_label.setText(str(error))
            self.report.setPlainText(str(error))
        finally:
            progress.close()
            QApplication.restoreOverrideCursor()
            self.set_busy(False)

    def perform(self, sizing):
        if self._running:
            return
        self.invalidate()
        if not self.valid_settings():
            return
        progress = None
        self.set_busy(True)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            context = self.current_context()
            if sizing:
                progress = QProgressDialog("Sizing fixed layout...", "Cancel", 0, 18, self)
                progress.setWindowModality(Qt.WindowModal)
                progress.setMinimumDuration(0)
                def update(step, total, message):
                    progress.setLabelText(message)
                    progress.setValue(step)
                    QApplication.processEvents()
                    if progress.wasCanceled():
                        raise AnalysisError("Sizing cancelled; sections are unchanged")
                candidates = {"strut": self.strut_profiles, "end_post": self.post_profiles,
                              **{g: self.component_editor.brace_candidates() for g in self.brace_combos}}
                result, chosen = auto_size(context, self.selections(), candidates, self.criteria(), context["cost_rates"], update, fast=True)
                self.adopt_sections(chosen)
            else:
                result = solve_selection(context, self.selections(), self.criteria(), context["cost_rates"])
            self.display_result(result)
        except SizingStopped as error:
            self.display_result(error.result)
            self.report_text = diagnostic_text(error.diagnostic)+"\n\n"+self.report_text
            self.report.setPlainText(self.report_text)
            self.sizing_issue_button.show()
            case = self.case_combo.findText(error.diagnostic["case"])
            if case >= 0:
                self.case_combo.setCurrentIndex(case)
            if error.diagnostic['input_target'] == 'layout':
                self.review_sizing_issue()
            else:
                if error.diagnostic['input_target'] == 'group':
                    self.review_sizing_issue()
                else:
                    self.inspect_member(error.diagnostic["member"])
            self.status_label.setText(f"Auto sizing stopped: {error}. Current selections retained; use Review sizing issue.")
        except (AnalysisError, ValueError, ArithmeticError) as error:
            self.status_label.setText(f"Analysis/sizing failed: {error}")
            self.report.setPlainText(str(error))
        finally:
            if progress is not None:
                progress.close()
            QApplication.restoreOverrideCursor()
            self.set_busy(False)

    def review_sizing_issue(self):
        if self.result and self.result.get("sizing_failure"):
            if self.result['sizing_failure'].get('constraint')=='braced_bay_spacing':
                self.visual_mode.setCurrentIndex(self.visual_mode.findData('layout'))
                self.tabs.setCurrentIndex(0)
                self.max_braced_bay_spacing.setFocus()
                self.member_detail.setText(self.result['sizing_failure']['reason'])
                return
            if self.result['sizing_failure']['input_target'] == 'layout':
                self.review_movement()
                return
            if self.result['sizing_failure']['input_target'] == 'group':
                self.tabs.setCurrentWidget(self.input_page)
                group = self.result['sizing_failure']['group']
                widget = self.brace_combos.get(group) or (self.post_combo if group == 'end_post' else self.strut_combo)
                widget.setFocus()
                return
            self.tabs.setCurrentWidget(self.component_editor)
            self.component_editor.focus_sizing_issue(self.result["sizing_failure"])

    def review_movement(self):
        if self.result is None:
            return
        sls = self.result['design']['global_sls']
        case = self.case_combo.findText(sls['case'])
        if case >= 0:
            self.case_combo.setCurrentIndex(case)
        self.deformation_scale.setValue(1.)
        self.visual_mode.setCurrentIndex(self.visual_mode.findData('deformed'))
        self.canvas.member_selected = None
        self.member_table.clearSelection()
        self.member_detail.setText(result_summary(self.result)['movement_text']+'\n'+sls['case']+
                                  '\nCircled node governs overall longitudinal movement. Shape shown at actual scale (x1). Member utilisations are separate checks.')
        self.tabs.setCurrentIndex(1)
        self.canvas.update()

    def display_result(self, result):
        self.result = result
        self.canvas.set_cladding(result.get('face_cladding', self.context.get('face_cladding', {})))
        d = result["design"]
        summary = result_summary(result)
        self.result_summary_label.setText(summary['text'])
        self.result_summary_label.setToolTip('Member failure counts can overlap if one member fails both strength and local deflection.\nGoverning overall movement case: '+d['global_sls']['case'])
        self.result_summary_box.show()
        self.movement_button.setEnabled(True)
        state = "PASS on scoped member/SLS checks" if d["passes"] else "FAIL / UNVERIFIED on scoped member/SLS checks"
        if not d.get('include_serviceability',True):
            state = ('PASS' if d['passes'] else 'FAIL / UNVERIFIED')+' on strength checks; bracing serviceability excluded'
        if d.get('preliminary_reference_checks') or d.get('preliminary_brace_tension_checks'):
            state = 'PRELIMINARY '+state
        if d.get('assumed_wire_stiffness_groups'):
            state += ' (interim rope stiffness)'
        if not d['passes']:
            failed = sum(c['ratio'] is not None and c['ratio'] > 1 for c in result['section_checks'].values())
            unverified = sum(c['ratio'] is None for c in result['section_checks'].values())
            state += f" ({failed} over capacity; {unverified} missing checks)"
        rows = ["SCOPED SECTION CHECKS", 'Design basis: '+d.get('design_basis','Strength + serviceability'), state, f"Estimated materials + listed fittings + panel installation: ${d['estimated_cost']:,.2f}",
                summary['text'], f"Overall movement source case: {d['global_sls']['case']}", d["basis"], d["cost_basis"], ""]
        rows += ['STRUT SIZING POLICY',d['strut_sizing_basis'],'']
        if result.get('layout_search'):
            search = result['layout_search']
            rows += ['LAYOUT SEARCH', search['scope'],
                     search['method'],
                     f"Layout trials: {search['layout_trials']} / {search['max_layout_trials']}; up to {search['max_section_solves_per_trial']} exact analyses per section-sizing trial.",
                     f"Blocked panels retained: {len(search['blocked_panels'])}; required panels retained: {len(search['required_panels'])}; selected X panels: {len(search['selected_panels'])}.",
                     'Selected: '+', '.join(search['selected_panels']), '']
            if search.get('strategy') == 'practical':
                rows += [f"Practical layout search: {search['elapsed_seconds']:.2f} s; bounded demand sizing and optional compact-layout comparison. Full cost-polish trials excluded.", search['ordering_basis'], '']
            if 'structured_seed_trials' in search:
                rows += [f"Resized engineering starts: {search['structured_seed_trials']}; neighbourhood width: {search['beam_width']}; duplicate trials skipped: {search['duplicate_trials_skipped']}; winning trial: {search['winning_trial']}.", '']
                rows += ['RESIZED LAYOUT ALTERNATIVES | Panels | Pass | Estimated cost including installation | SLS UC']
                for trial in search.get('trials', []):
                    if trial.get('section_sizing'):
                        cost = trial.get('estimated_cost')
                        cost_text = f'${cost:,.2f}' if cost is not None else 'unverified'
                        sway = trial.get('global_sls_ratio')
                        sway_text = f'{sway:.3f}' if sway is not None else '-'
                        rows.append(f"{trial['reason']} | {len(trial['selected_panels'])} | {trial['passes']} | {cost_text} | {sway_text}")
                rows += ['']
        sway=d['global_sls']
        rows += [f"GLOBAL SWAY CRITERION: H/{sway.get('divisor',150.):g}; {sway.get('basis',{}).get('source','legacy criterion')}",
                 f"Local member/purlin deflection divisor: L/{result['design_request']['criteria']['sls_divisor']:g}",'']
        if result.get('mullion_basis'):
            basis=result['mullion_basis']
            rows += ['ALIGNED MAIN MULLION CHECK',basis['basis'],
                     f"Main wind envelope: ULS {basis['strength_pressure_kpa']:.4f} kPa; SLS {basis['service_pressure_kpa']:.4f} kPa; effective length factor {basis['effective_length_factor']:g}; moment modifier {basis['moment_modifier']:g}; main deflection H/{basis['deflection_divisor']:g}."]
            for check in result['section_checks'].values():
                if check.get('mullion_basis'):
                    for row in check['case_checks']:
                        if 'actions' in row:
                            a=row['actions']
                            rows.append(f"{check['id']} | {row['case']} | Ncomp {a['compression_kn']:.3f} kN; Nt {a['tension_kn']:.3f} kN; M {a['moment_knm']:.3f} kNm; V {a['shear_kn']:.3f} kN; Le {a['effective_length_y_mm']:.1f}/{a['effective_length_z_mm']:.1f} mm; am {a['moment_modifier']:g}; UC {row['ratio']}; {row['mode']}")
        rows += ["COST BREAKDOWN | Purchased kg | $/kg | Material | Listed fittings | Total"]
        for r in d["cost_items"]:
            if r['group'] == 'panel_installation':
                rows.append(f"Panel installation: {r['panel_count']} physical X panels × ${r['rate_per_panel']:.2f} = ${r['total']:.2f}. {r['allowance']}")
            elif "price_per_m" in r:
                rows.append(f"{r['group']}: {r['section']} | {r['purchased_length_m']:.2f} m @ ${r['price_per_m']:.2f}/m | material ${r['material_cost']:.2f} | fittings ${r['listed_fittings_cost']:.2f} | total ${r['total']:.2f}")
                if r.get('turnbuckle'):
                    rows.append(f"  {r['turnbuckle_count']} x {r['turnbuckle']} at ${r['fittings_per_run']:.2f}; {r.get('fitting_price_source', '')}. {r['allowance']}. Rope: {r.get('rope_price_source', '')}; {r.get('rope_price_adoption', '')}.")
            elif r.get("incremental_only"):
                rows.append(f"{r['group']}: per-location additions | purchased {r['purchased_kg']:.2f} kg | material ${r['material_cost']:.2f} | total ${r['total']:.2f}; existing purlins excluded from added cost; see component cost record")
            else:
                rows.append(f"{r['group']}: {r['section']} | {r['purchased_kg']:.2f} | {r['material_rate_per_kg']:.2f} | ${r['material_cost']:.2f} | ${r['listed_fittings_cost']:.2f} | ${r['total']:.2f}")
        rows += [""]
        strut_props = result['design_request']['section_properties'].get('strut', {})
        if strut_props.get('type') == 'strut_plan':
            rows += ['INSTALLED ROOF PURLINS | Member | Existing connected count | Tributary width per ply mm | Span mm']
            for identifier, props in strut_props['members'].items():
                location = props.get('location', {})
                data = location.get('purlin', {})
                width = data.get('tributary_width_mm')
                if width is not None:
                    rows.append(f"{identifier} | {location['existing_count']} | {width:.3f} | {location['length_mm']:.1f}; {data.get('tributary_width_source', '')}")
                if data.get('eave_tributary'):
                    e=data['eave_tributary']
                    rows.append(f"  Strut: {e.get('strut_alignment', 'roof purlin')}; eave sheet span {e['adjacent_sheet_span_mm']:.3f} mm; edge inset {e['sheet_edge_offset_mm']:.3f} mm; strut loaded area {e['loaded_area_m2']:.4f} m2; recalculated ULS roof pressures up/down {e['wind_upward_kpa']:.4f}/{e['wind_downward_kpa']:.4f} kPa.")
            rows += ['']
        wire_groups = {g: p for g, p in result['design_request']['section_properties'].items()
                       if p.get('type') == 'wire_rope'}
        if wire_groups:
            rows += ['WIRE-ROPE STIFFNESS BASIS']
            for group, props in wire_groups.items():
                rating = props['rating']
                rows.append(f"{group}: {props['diameter_mm']} mm; EA {rating['ea_kn']:.3f} kN; {rating.get('stiffness_basis', 'declared_ea')}; {rating.get('ea_source', '')}")
            rows += ['']
        if result.get('brace_policy'):
            policy = result['brace_policy']
            rows += ['PROJECT BRACING MATERIAL POLICY', 'Project use: '+policy['project_use'], policy['basis']]
            for group, record in policy['groups'].items():
                rows.append(f"{group}: selected {record['selected_family']}; preferred-family rank {record['preference_rank']}; rod-required faces: {', '.join(record['rod_required_faces']) or 'none'}")
            rows += ['']
        if "sizing_search" in result:
            s = result["sizing_search"]
            rows += [s["scope"], f"Candidate screens: {s['candidate_screens']}; rounds: {len(s['rounds'])}", ""]
            stiffness = s.get('stiffness_screening', [])
            if stiffness:
                rows += [f"3D stiffness screening: {sum(t.get('catalogue_predictions', 0) for t in stiffness)} response predictions; "
                         f"{sum(len(t.get('lightweight_trials', [])) for t in stiffness)} lightweight trials; "
                         f"{sum(t.get('sls_solves', 0) for t in stiffness)} SLS case solves. Full design checks follow each selected trial."]
            if 'exact_solves' in s:
                rows += [f"Exact 3D analyses: {s['exact_solves']} / {s['max_solves']}; stiffness growth capped at eligible priced catalogue sizes.",
                         'LOWER-COST / PREFERRED ALTERNATIVE REANALYSES | Group | Section | Pass | Adopted']
                rows += [f"{t['group']} | {t['section']} | {t.get('passes', False)} | {t['adopted']}"
                         for t in s.get('cost_reduction_trials', [])]
                rows += ['']
        rows += ["Member | Section | UC | Governing check | Source"]
        for c in result["section_checks"].values():
            uc = f"{c['ratio']:.3f}" if c["ratio"] is not None else "NOT CHECKED"
            rows.append(f"{c['id']} | {c['section']} | {uc} | {c['mode']} | {c['case']}")
        rows += ["", "SIMPLIFIED PURLIN COMPRESSION / BENDING WORKINGS", "Lysaght Zeds & Cees, May 1999 p20; Metroll MEGASPAN 2023 p7 properties. Ns=903 Ag/(L/rx')^2; Nav=Ns(1-Wa/Ws); compression UC=N/Ns+Wa/Ws. No extra phi or Euler amplification."]
        for c in result["section_checks"].values():
            for r in c["case_checks"]:
                if r.get("method") == "lysaght_1999" and "section_properties" in r:
                    p = r["section_properties"]
                    uc = "UNVERIFIED" if r["ratio"] is None else f"{r['ratio']:.4f}"
                    rows.append(f"{c['id']} | {r['case']} | component {r['component']+1} | {p['shape']} | Ag {p['area_mm2']:.2f} mm2; rx' {p['web_rx_mm']:.2f} mm; L {r['span_mm']:.1f} mm; Ns {r['compression_capacity_kn']:.3f} kN; bending {r['bending_ratio']:.4f}; Nav {r['available_compression_kn']:.3f} kN; axial {r['axial_kn']:.3f} kN; UC {uc}; {r['mode']}")
        rows += ['', 'STEEL BRACE MEMBER TENSION WORKINGS', 'Adopted 0.9 Ag fy screen; connection holes/threads, net fracture, fittings and anchors remain separate. Scheduled rod capacities already include 0.9; supplied wire capacities are unchanged.']
        for c in result['section_checks'].values():
            for r in c['case_checks']:
                if r.get('method') == 'gross_yield_brace_tension':
                    rows.append(f"{c['id']} | {r['case']} | Ag {r['gross_area_mm2']:.2f} mm2; fy {r['fy_mpa']:g} MPa; phi {r['capacity_factor']:g}; tension {r['tension_kn']:.3f} kN; capacity {r['capacity_kn']:.3f} kN; UC {r['ratio']:.4f}; connections checked: no.")
        rows += ["", "PURLIN MEMBER TENSION WORKINGS", "Net area and kt are configured member assumptions; bolt/tearout/block-shear/anchor checks remain separate."]
        for c in result["section_checks"].values():
            for r in c['case_checks']:
                if r.get('tension_method') == 'calculated':
                    rows.append(f"{c['id']} | {r['case']} | component {r['component']+1} | Ag {r['tension_gross_area_mm2']:.2f} mm2; An {r['tension_net_area_mm2']:.2f} mm2 (factor {r['tension_net_area_factor']:.3f}); kt {r['tension_kt']:.3f}; fy {r['tension_fy_mpa']:g}, fu {r['tension_fu_mpa']:g} MPa; phi {r['tension_phi']:g}; yield {r['tension_gross_yield_kn']:.3f}, fracture {r['tension_net_fracture_kn']:.3f}; design tension {r['tension_capacity_kn']:.3f} kN. {r['tension_basis']} {r['tension_source_url']}")
        for key, run in result.get("purlin_beams", {}).get("runs", {}).items():
            rows += ["", f"SHEETED PURLIN BEAM | ROOF LINE {int(key)+1}"]
            if run.get("error"):
                rows.append("UNVERIFIED: "+run["error"])
                continue
            rows += [run["basis"], run["note"],
                     f"Cladding coupling {run['coupling_kn_m_per_mm']:g} kN/m per mm; source: {run['coupling_source'] or 'MISSING - zero-sharing fallback, UNVERIFIED'}",
                     ('Assigned roof-load split by bay/physical ply (listed below)' if run.get('preliminary') else f"Direct roof pickup to added member: {run['direct_roof_pickup_fraction']:.2%}; inputs defined: {run['coupling_defined']}"),
                     f"Existing spans: {run['span_type']}; lap ratio: {run['lap_ratio']}; roof releases after bays: {run['roof_releases_after_bays']}; added single-span bays: {[b+1 for b in run['added_bays']]}",
                     ("PRELIMINARY table-reference actions are used for this assigned allocation; they are not verified general M/V resistances." if run.get('preliminary') else "Member acceptance uses separately entered M+/M-/V capacities for redistributed diagrams. UDL table capacities are not reused."),
                     "Roof load case | Bay | Role | M+ kNm | M- kNm | V kN | Deflection mm | Cladding transfer kN | Peak transfer kN/m"]
            for bay, allocation in run.get('load_allocation_by_bay', {}).items():
                rows.append(f"Bay {int(bay)+1}: {allocation['existing_count']} existing roof purlin(s) carry {allocation['existing_group_fraction']:.2%} together; {allocation['added_count']} added single-span ply/plies each carry {allocation['each_added_fraction']:.2%}. Diagrams/checks are per ply; no composite bending I.")
            for case in run["cases"]:
                rows.append(f"{case['name']}: applied {case['applied_load_n']:.3f} N; reaction {case['support_reaction_n']:.3f} N; balance error {case['vertical_balance_error_n']:.3g} N")
                for i, bay in enumerate(case["bays"]):
                    for role in ("roof", "added"):
                        actions = bay[role]
                        if not actions["samples"]:
                            continue
                        rows.append(f"{case['name']} | {i+1} | {role} | {actions['positive_moment_knm']:.4f} | {actions['negative_moment_knm']:.4f} | {actions['shear_kn']:.4f} | {actions['deflection_mm']:.4f} | {bay['transfer_kn']:.4f} | {bay['peak_transfer_kn_m']:.4f}")
        rows += ['', 'PRELIMINARY PURLIN TABLE-REFERENCE WORKINGS']
        for checked in result['section_checks'].values():
            for r in checked['case_checks']:
                if r.get('method') == 'assigned_half_reference':
                    rows.append(f"{checked['id']} | {r['case']} | {r['beam_role']} | {r['mode']} | UC {r['ratio']} | axial {r['axial_kn']:.4f} kN")
                    for comparison in r.get('reference_comparisons', []):
                        rows.append(f"  {comparison['case']}: rated UDL {comparison['reference_line_load_kn_m']:.4f} kN/m; reference M {comparison['reference_moment_knm']:.4f} kNm, V {comparison['reference_shear_kn']:.4f} kN; M ratio {comparison['moment_ratio']:.4f}, V ratio {comparison['shear_ratio']:.4f}")
                    if 'table_reference' in r:
                        cap = r['table_reference']['capacity']
                        rows.append(f"  {cap['source']} | {cap['tables']} | {cap['span_type']} | {cap['span_mm']:g} mm | {cap['bridging_rows']} bridging rows | cache snapshot {cap['source_sha256']}")
                    if 'table_adjusted_deflection_mm' in r:
                        rows.append(f"  Elastic deflection {r['elastic_deflection_mm']:.4f} mm; table-adjusted {r['table_adjusted_deflection_mm']:.4f} mm; limit {r['limit_mm']:.4f} mm")
        self.report_text = "\n".join(rows+["", text_report(result)])
        self.beam_results.set_result(result)
        self.report.setPlainText(self.report_text)
        for case in result["cases"]:
            self.case_combo.addItem(case["case"]["name"])
        self.case_combo.addItem('Governing case for each member', 'governing')
        self.canvas.set_result(result, 0)
        self.case_combo.setCurrentIndex(self.case_combo.count()-1)
        self.visual_mode.setCurrentIndex(2)
        self.change_visual_mode()
        self.tabs.setCurrentIndex(1)
        self.export_json_button.setEnabled(True)
        self.export_text_button.setEnabled(True)
        hint = "" if d['passes'] or 'sizing_search' in result or 'sizing_failure' in result else " Analyse and check evaluates current selections; use Auto size to select suitable members."
        retained = 'Auto-sizing stopped; original input selections retained. ' if 'sizing_failure' in result else ''
        self.status_label.setText(f"{retained}{state} | estimated materials + fittings + installation ${d['estimated_cost']:,.2f} | SLS {d['global_sls']['ratio']:.3f}.{hint} Connections, full combinations and production portal restraint remain separate.")

    def show_case(self):
        if self.result is None or self.case_combo.currentIndex() < 0:
            self.member_table.setRowCount(0)
            return
        governing = self.case_combo.currentData() == 'governing'
        index = 0 if governing else self.case_combo.currentIndex()
        self.canvas.governing_cases = governing
        self.canvas.case_index = index
        if governing and self.visual_mode.currentData() == 'deformed':
            self.visual_mode.setCurrentIndex(self.visual_mode.findData('utilisation'))
        members = self.canvas.display_members()
        self.member_table.setRowCount(len(members))
        for row, m in enumerate(members):
            c = self.result["section_checks"].get(m["id"], {})
            ratio = c.get("ratio")
            governing = governing_case_member(self.result, m['id'])
            values = [m["id"], c.get("section", "-"), f"{governing['axial_n']/1000:.3f}" if governing else '-',
                      f"{ratio:.3f}" if ratio is not None else "-", check_type(self.result, governing_check_row(c) or c), c.get("mode", "-"), c.get("case", "-"), governing['state'] if governing else '-']
            if m['kind'].endswith('_diaphragm'):
                values = [m['id'], 'SLS equivalent diagonal', f"{m['axial_n']/1000:.3f}", '-',
                          'Stiffness only', f"Shear component {m['panel_shear_kn']:.3f} kN", 'Adopted panel flexibility', m['state']]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if ratio is not None and column == 3:
                    from PySide6.QtGui import QColor
                    item.setForeground(QColor("#b32929" if ratio > 1 else "#128265"))
                if m['kind'].endswith('_diaphragm') and column in (2, 7):
                    item.setToolTip('Selected display case; SLS stiffness surrogate with no physical member utilisation check.')
                self.member_table.setItem(row, column, item)
        if self.canvas.member_selected:
            if any(m['id'] == self.canvas.member_selected for m in members):
                self.member_detail.setText(self.check_description(self.canvas.member_selected))
            else:
                self.canvas.member_selected = ''
                self.member_detail.setText('Click a member in the view or table for its governing check.')
        self.change_visual_mode()
        self.canvas.update()

    def change_visual_mode(self):
        mode = self.visual_mode.currentData() or "layout"
        if mode == 'deformed' and self.result and self.case_combo.currentData() == 'governing':
            # A displacement field must come from one equilibrium case.
            self.case_combo.setCurrentIndex(self.case_combo.findText(self.result['design']['global_sls']['case']))
        self.canvas.result_mode = mode
        self.canvas.deformation_factor = self.deformation_scale.value()
        self.deformation_scale.setEnabled(mode == "deformed")
        self.layout_editor.edit_mode.setEnabled(mode == "layout" and not self.read_only)
        self.layout_editor.face_filter.setEnabled(mode == "layout")
        self.canvas.face = (self.layout_editor.face_filter.currentData() or "") if mode == "layout" else ""
        legends = {"layout": "Red dashed X: blocked | Green X: pinned | Unmarked: available",
                   "forces": "Red: tension | Blue: compression | Grey: slack | Width: magnitude",
                   "utilisation": "Governing UC: green <= 0.8 | amber <= 1 | red > 1 | grey: unchecked",
                   "deformed": f"Purple: displaced model x{self.deformation_scale.value():g} | dashed: original"}
        if self.canvas.governing_cases and mode in {'forces', 'utilisation'}:
            legends[mode] += ' | Each member uses its own governing UC case; not one simultaneous load case'
        self.layout_editor.legend_label.setWordWrap(True)
        self.layout_editor.legend_label.setText(legends[mode]+(' | Teal dashed: SLS equivalent diaphragm'
            if self.result and self.result.get('stressed_skin', {}).get('active') and mode!='layout' and not self.canvas.governing_cases else ''))
        self.canvas.update()

    def set_cladding_overlay(self, visible):
        self.canvas.show_cladding = visible
        self.canvas.update()

    def inspect_member(self, identifier):
        if self.result is None:
            return
        members = self.canvas.display_members()
        row = next((i for i, m in enumerate(members) if m["id"] == identifier), -1)
        if row >= 0:
            self.member_table.selectRow(row)
            self.member_table.scrollToItem(self.member_table.item(row, 0))
        self.tabs.setCurrentIndex(1)
        self.canvas.member_selected = identifier
        self.member_detail.setText(self.check_description(identifier))
        self.canvas.update()

    def select_table_member(self, row, column, previous_row, previous_column):
        if self.result and row >= 0:
            members = self.canvas.display_members()
            if row >= len(members):
                return
            identifier = members[row]['id']
            self.canvas.member_selected = identifier
            self.member_detail.setText(self.check_description(identifier))
            self.canvas.update()

    def check_description(self, identifier):
        text = self.canvas.member_description(identifier)
        if identifier.startswith('diaphragm/'):
            settings = self.result['stressed_skin']['settings']
            panel=next(p for p in self.result['stressed_skin']['panels'] if p['id']==identifier.rsplit('/',1)[0])
            return text+f"\nAdopted c = {panel['flexibility_mm_per_kn']:g} mm/kN; displacement direction {panel['shear_direction']}. ULS uses conventional bracing only."
        check = self.result["section_checks"][identifier]
        row = governing_check_row(check)
        text += '\nCheck type: '+check_type(self.result, row)
        if row.get('component') is not None:
            text += f"\nGoverning component {row['component']+1}: {row.get('section', '-')}."
        if row.get('load_share') is not None:
            text += f"\nGoverning component axial share: {row['load_share']:.2%}."
        if check.get('mullion_basis'):
            uls=[r for r in check['case_checks'] if r.get('level')=='ULS' and not r.get('required_baseline')]
            governing_uls=max(uls,key=lambda r:float('inf') if r['ratio'] is None else r['ratio'],default=None)
            if governing_uls:
                a=governing_uls['actions']
                uc=f"{governing_uls['ratio']:.3f}" if governing_uls['ratio'] is not None else 'UNVERIFIED'
                text += f"\nCombined mullion ULS: Ncomp {a['compression_kn']:.3f} kN; Nt {a['tension_kn']:.3f} kN; M {a['moment_knm']:.3f} kNm; UC {uc}. {governing_uls['case']}"
                text += f"\n{governing_uls['restraint']}; Le {a['effective_length_y_mm']:.1f} mm; am {a['moment_modifier']:g}. Main mullion prerequisites also checked."
        if not self.result['design'].get('include_serviceability',True):
            text += ('\nStrength-only bracing design. Main mullion strength/deflection prerequisites still apply; additional bracing deflections are report-only.' if self.result.get('mullion_basis') else '\nStrength-only design. Local deflections and global movement are reported but excluded from acceptance and sizing.')
        if identifier.startswith('brace/front_end/') or identifier.startswith('brace/back_end/'):
            text += '\nFront/back end-wall brace: lies across the span in X/Z; zero direct resistance along the building length (Y). ULS side-wind roof/portal forces can govern its transverse strength check.'
        if row.get('tributary_width_mm') is not None:
            if row.get('method')=='main_mullion_combined':
                text += f"\nMullion design height {row['design_height_mm']:.1f} mm; tributary width {row['tributary_width_mm']:.1f} mm. {row['restraint']}"
                text += '\nMain wall-wind envelope plus bracing axial demand, with common main calculation section properties and interaction checks.'
                if 'actions' in row:
                    text += f"\nBracing axial {row['axial_kn']:.3f} kN; wind bending {row['actions']['moment_knm']:.3f} kNm; effective length {row['actions']['effective_length_y_mm']:.1f} mm."
            else:
                text += f"\nOriginal roof tributary width: {row['tributary_width_mm']:.1f} mm per existing purlin."
        if row.get('method') == 'assigned_half_reference':
            text += f"\nAssigned roof load on this physical ply: {row['roof_load_fraction']:.2%}; added self-weight is separate."
        if row.get("method") in {"coupled_purlin_beam", 'assigned_half_reference'}:
            text += f"\n{row['beam_role'].title()} beam, bay {row['beam_bay']+1}; governing ply axial {row['axial_kn']:.3f} kN."
            beam_case = row.get('governing_beam_case')
            if beam_case:
                text += '\nGoverning roof-bending/deflection case: '+beam_case
            local = row.get('governing_local_pressure')
            if local:
                text += f"\nTable 5.6 {local['region']}: Kl {local['kl']:g}; a {local['a_m']:.3f} m; local patch {local['patch_length_m']:.3f} m; area {local['effective_patch_area_m2']:.3f} m2."
            for d in [d for d in row.get('beam_demands',[]) if not beam_case or d['case']==beam_case]:
                text += f"\n{d['case']}: M+ {d['positive_moment_knm']:.3f}, M- {d['negative_moment_knm']:.3f} kNm; V {d['shear_kn']:.3f} kN; deflection {d['deflection_mm']:.3f} mm"
            if 'limit_mm' in row:
                delta = row.get('deflection_mm', row.get('table_adjusted_deflection_mm'))
                if delta is not None:
                    text += f"\nGoverning local deflection {delta:.3f} mm / limit {row['limit_mm']:.3f} mm."
        elif "capacity_kn" in row:
            text += f"\nN* {row['tension_kn']:.3f} kN / design capacity {row['capacity_kn']:.3f} kN"
            if 'ea_kn' in row:
                text += f"\nEA {row['ea_kn']:.3f} kN; {row.get('ea_source', '')}"
            if "gross_yield_kn" in row and 'net_fracture_kn' in row:
                text += f" (yield {row['gross_yield_kn']:.3f}, net fracture {row['net_fracture_kn']:.3f})"
        elif row.get("method") == "lysaght_1999" and "section_properties" in row:
            p = row["section_properties"]
            text += f"\nLysaght 1999 simplified: Ag {p['area_mm2']:.1f} mm2, rx' {p['web_rx_mm']:.2f} mm, L {row['span_mm']:.0f} mm. Ns {row['compression_capacity_kn']:.3f} kN; bending {row['bending_ratio']:.3f}; available compression {row['available_compression_kn']:.3f} kN."
            text += f"\n{row['mode']}"
        elif "compression_capacity_kn" in row:
            text += f"\nPurlin N {row['axial_kn']:.3f} kN; Nc {row['compression_capacity_kn']:.3f}, Nt {row['tension_capacity_kn']:.3f} kN"
            if "elastic_buckling_kn" in row:
                text += f"; Nex {row['elastic_buckling_kn']:.3f} kN; bending ratio {row['bending_ratio']:.3f}"
        elif "checks" in row:
            for name in ("compression", "tension", "bending", "shear"):
                item = row["checks"][name]
                text += f"\n{item['label']}: demand {item['demand']:.3f} / capacity {item['capacity']:.3f} {item['unit']}"
        elif "deflection_mm" in row:
            text += f"\nLocal deflection {row['deflection_mm']:.3f} / limit {row['limit_mm']:.3f} mm"
        return text

    def export(self, evidence):
        if self.result is None:
            return
        suffix = "json" if evidence else "txt"
        filename, _ = QFileDialog.getSaveFileName(self, "Export 3D bracing results", f"3D Bracing Results.{suffix}", f"{suffix.upper()} files (*.{suffix})")
        if filename:
            try:
                Path(filename).write_text(json.dumps(self.result, indent=2, allow_nan=False) if evidence else self.report_text, encoding="utf-8")
            except (OSError, ValueError) as error:
                self.status_label.setText(f"Export failed: {error}")

    def accept(self):
        if not self._running and (self.read_only or (self.valid_settings() and self.layout_editor.model.geometry == self.layout_editor.current_geometry)):
            super().accept()
