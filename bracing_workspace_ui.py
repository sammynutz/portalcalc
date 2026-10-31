"""Combined bracing selector, scoped checks, cost sizing and visual results."""
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QProgressDialog, QPushButton, QSizePolicy, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from bracing_analysis import AnalysisError
from bracing_analysis_desktop import text_report
from bracing_design import DesignCriteria, auto_size, candidate_name, solve_selection
from bracing_layout import BracingLayout, LayoutGeometry
from bracing_layout_ui import BracingLayoutDialog
from bracing_component_ui import ComponentEditor
from bracing_components import StrutPlan
from purlin_beam_ui import PurlinBeamResults
from bracing_sizing_diagnostics import SizingStopped, diagnostic_text


class BracingAnalysisDialog(QDialog):
    def __init__(self, context, catalog, saved=None, parent=None, read_only=False):
        super().__init__(parent)
        self.setWindowTitle("3D bracing - layout, sections and checks")
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1420, max(1080, available.width()-50)), min(860, max(680, available.height()-70)))
        self.context, self.catalog, self.read_only = context, catalog, read_only
        self.result, self.report_text, self._running = None, "", False
        saved = saved if isinstance(saved, dict) else {}
        layout = QVBoxLayout(self)
        intro = QLabel("Block unavailable panels and pin the X braces to install. Analyse/check or size that fixed layout. Main building, end-wind analysis; production portal restraint and connections remain separate.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        toolbar = QHBoxLayout()
        self.run_button = QPushButton("Analyse and check")
        self.auto_button = QPushButton("Auto size for lowest checked cost")
        toolbar.addWidget(self.run_button)
        toolbar.addWidget(self.auto_button)
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
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        splitter = QSplitter(Qt.Horizontal)
        nominal = context.get("nominal_geometry") or LayoutGeometry.from_dict(context["nominal_layout"]["geometry"])
        self.layout_editor = BracingLayoutDialog(nominal, context["nominal_layout"], self, read_only)
        editor = self.layout_editor
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
        self.populate(self.post_combo, self.post_profiles, saved.get("end_post_section") or context.get("default_end_post"), 2500)
        self.component_editor = ComponentEditor(context, self.strut_profiles,
            catalog.profile_by_name.get(self.strut_combo.currentData(), self.strut_profiles[0]), saved, read_only)
        form.addRow("Uniform steel (alternative model)", self.strut_combo)
        form.addRow("End-wall posts", self.post_combo)
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
        labels = ("Angle/default Fy (MPa)", "Angle/default Fu (MPa)", "Net / gross area", "Tension kt", "Strut effective K", "Post effective K", "SLS H/n or L/n")
        self.criteria_inputs = {}
        for i, ((name, value), label) in enumerate(zip(asdict(DesignCriteria()).items(), labels)):
            box = QDoubleSpinBox()
            box.setDecimals(2)
            box.setRange(.01, 1. if name in {"net_area_factor", "tension_distribution_factor"} else 5. if name.endswith("ke") else 2000.)
            try:
                box.setValue(float(stored.get(name, value)))
            except (TypeError, ValueError):
                box.setValue(value)
            grid.addWidget(QLabel(label), i//2, 2*(i%2))
            grid.addWidget(box, i//2, 2*(i%2)+1)
            self.criteria_inputs[name] = box
        inputs.addWidget(group)
        def refresh_tension(*_args):
            self.component_editor.refresh_tension_preview(self.criteria_inputs['net_area_factor'].value(),
                self.criteria_inputs['tension_distribution_factor'].value())
        refresh_tension()
        for field in ('net_area_factor', 'tension_distribution_factor'):
            self.criteria_inputs[field].valueChanged.connect(refresh_tension)
        notes = QLabel("Per-location roof-purlin reuse is the default: use Components / ratings for connected counts, additions and supplier ratings. Uncheck that mode for the uniform dedicated-steel approximation. The supplied schedule sets Grade 300 rod and Grade 1570 wire strengths. Wire sizes 8/10/12/14/16 mm still need rope EA/source and prices.\n\nPosts include end-wind bending. Net-area/kt applies to calculated steel and purlin member tension, not the schedule's already factored brace strengths. Purlins use G450 minima, independent of Angle/default Fy/Fu. Net area and kt are project assumptions; connections remain separate. Sizing reanalyses candidates; it is not a global optimum. Connections, labour and supplier availability remain separate.")
        notes.setWordWrap(True)
        inputs.addWidget(notes)
        rates = context["cost_rates"]
        rate_note = QLabel(f"Captured Costing rates ($/kg): hollow {rates['shs']:.2f}; rolled {rates['ub']:.2f}; welded {rates['wb']:.2f}.")
        rate_note.setWordWrap(True)
        inputs.addWidget(rate_note)
        inputs.addStretch()
        self.tabs.addTab(self.input_page, "Sections / criteria")
        force_page = QWidget()
        forces = QVBoxLayout(force_page)
        self.member_detail = QLabel("Click a member in the view or table for its governing check.")
        self.member_detail.setWordWrap(True)
        forces.addWidget(self.member_detail)
        self.member_table = QTableWidget()
        self.member_table.setColumnCount(7)
        self.member_table.setHorizontalHeaderLabels(["Member", "Section", "Axial kN", "UC", "Governing check", "Source", "State"])
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
        self.save_button, self.cancel_button = QPushButton("Close" if read_only else "Save layout and sections"), QPushButton("Cancel")
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)
        self.run_button.clicked.connect(self.run_analysis)
        self.auto_button.clicked.connect(self.run_auto_size)
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
        editor.layoutChanged.connect(self.invalidate)
        self.component_editor.changed.connect(self.invalidate)
        self.visual_mode.currentIndexChanged.connect(self.change_visual_mode)
        self.case_combo.currentIndexChanged.connect(self.show_case)
        self.deformation_scale.valueChanged.connect(self.change_visual_mode)
        self.canvas.memberClicked.connect(self.inspect_member)
        self.member_table.currentCellChanged.connect(self.select_table_member)
        self.invalidate()

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
        return DesignCriteria(**{name: box.value() for name, box in self.criteria_inputs.items()})

    def snapshot(self):
        return {"schema": "portalcalc.bracing-analysis-settings.v1", "strut_section": self.strut_combo.currentData(),
                "end_post_section": self.post_combo.currentData(),
                **{f"{g}_label": (c.currentData() or {}).get("label") for g, c in self.brace_combos.items()},
                "criteria": asdict(self.criteria()),
                "invert_orbit_horizontal": self.canvas.invert_orbit_horizontal,
                "invert_orbit_vertical": self.canvas.invert_orbit_vertical,
                **self.component_editor.snapshot()}

    def layout_snapshot(self):
        return self.layout_editor.snapshot()

    def valid_settings(self):
        return (all(c.currentData() in self.catalog.profile_by_name for c in (self.strut_combo, self.post_combo))
                and all(c.currentData() is not None for c in self.brace_combos.values()))

    def selections(self):
        fallback = self.catalog.profile_by_name[self.strut_combo.currentData()]
        wires = {option["label"]: option for option in self.component_editor.brace_candidates()}
        strut = self.component_editor.plan(self.current_context(), fallback) if self.component_editor.mixed.isChecked() else fallback
        return {"strut": strut,
                "end_post": self.catalog.profile_by_name[self.post_combo.currentData()],
                **{g: wires.get((c.currentData() or {}).get("label"), c.currentData()) for g, c in self.brace_combos.items()}}

    def current_context(self):
        editor = self.layout_editor
        if editor.model.geometry != editor.current_geometry:
            raise AnalysisError("Dimensions changed: update the saved layout geometry before analysis")
        payload = editor.snapshot()
        if not payload["pinned_panels"]:
            raise AnalysisError("Pin the X-braced panels to install before running")
        actual = BracingLayout(self.context["layout"].geometry, payload["blocked_panels"], payload["pinned_panels"])
        source = copy.deepcopy(self.context["source_preset"])
        source.setdefault("bracing", {}).update(layout=payload, analysis_3d=self.snapshot())
        return {**self.context, "layout": actual, "nominal_layout": payload, "source_preset": source,
                "source_hash": hashlib.sha256(json.dumps(source, sort_keys=True, allow_nan=False).encode()).hexdigest()}

    def invalidate(self):
        if self._running:
            return
        self.result, self.report_text = None, ""
        self.sizing_issue_button.hide()
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
        self.run_button.setEnabled(self.valid_settings())
        self.strut_combo.setEnabled(not self.component_editor.mixed.isChecked() and not self.read_only)
        self.auto_button.setEnabled(self.valid_settings() and not self.read_only)
        self.member_detail.setText("Click a member in the view or table for its governing check.")
        self.status_label.setText(f"{len(self.layout_editor.model.pinned)} pinned X panels. Pin a fixed layout, then analyse/check or auto-size. Input changes clear previous results.")

    def set_busy(self, busy):
        self._running = busy
        self.layout_editor.setEnabled(not busy)
        self.input_page.setEnabled(not busy)
        self.component_editor.setEnabled(not busy)
        self.run_button.setEnabled(not busy and self.valid_settings())
        self.auto_button.setEnabled(not busy and self.valid_settings() and not self.read_only)
        self.save_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)

    def run_analysis(self):
        self.perform(False)

    def run_auto_size(self):
        if not self.read_only:
            self.perform(True)

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
                result, chosen = auto_size(context, self.selections(), candidates, self.criteria(), context["cost_rates"], update)
                for g, combo in (("strut", self.strut_combo), ("end_post", self.post_combo)):
                    if g == "strut" and isinstance(chosen[g], StrutPlan):
                        self.component_editor.adopt(chosen[g])
                        continue
                    with QSignalBlocker(combo):
                        combo.setCurrentIndex(combo.findData(chosen[g].name))
                for g, combo in self.brace_combos.items():
                    with QSignalBlocker(combo):
                        combo.setCurrentIndex(combo.findText(candidate_name(chosen[g])))
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
            self.tabs.setCurrentWidget(self.component_editor)
            self.component_editor.focus_sizing_issue(self.result["sizing_failure"])

    def display_result(self, result):
        self.result = result
        d = result["design"]
        state = "PASS on scoped member/SLS checks" if d["passes"] else "FAIL / UNVERIFIED on scoped member/SLS checks"
        if d.get('preliminary_reference_checks'):
            state = 'PRELIMINARY '+state
        if not d['passes']:
            failed = sum(c['ratio'] is not None and c['ratio'] > 1 for c in result['section_checks'].values())
            unverified = sum(c['ratio'] is None for c in result['section_checks'].values())
            state += f" ({failed} over capacity; {unverified} missing checks)"
        rows = ["SCOPED SECTION CHECKS", state, f"Estimated materials + listed fittings: ${d['estimated_cost']:,.2f}",
                f"Global SLS ratio: {d['global_sls']['ratio']:.3f}", d["basis"], d["cost_basis"], ""]
        rows += ["COST BREAKDOWN | Purchased kg | $/kg | Material | Listed fittings | Total"]
        for r in d["cost_items"]:
            if "price_per_m" in r:
                rows.append(f"{r['group']}: {r['section']} | {r['purchased_length_m']:.2f} m @ ${r['price_per_m']:.2f}/m | material ${r['material_cost']:.2f} | fittings ${r['listed_fittings_cost']:.2f} | total ${r['total']:.2f}")
            elif r.get("incremental_only"):
                rows.append(f"{r['group']}: per-location additions | purchased {r['purchased_kg']:.2f} kg | material ${r['material_cost']:.2f} | total ${r['total']:.2f}; existing purlins excluded from added cost; see component cost record")
            else:
                rows.append(f"{r['group']}: {r['section']} | {r['purchased_kg']:.2f} | {r['material_rate_per_kg']:.2f} | ${r['material_cost']:.2f} | ${r['listed_fittings_cost']:.2f} | ${r['total']:.2f}")
        rows += [""]
        if "sizing_search" in result:
            s = result["sizing_search"]
            rows += [s["scope"], f"Candidate screens: {s['candidate_screens']}; rounds: {len(s['rounds'])}", ""]
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
                     f"Direct roof pickup to added member: {run['direct_roof_pickup_fraction']:.2%}; inputs defined: {run['coupling_defined']}",
                     f"Existing spans: {run['span_type']}; lap ratio: {run['lap_ratio']}; roof releases after bays: {run['roof_releases_after_bays']}; added single-span bays: {[b+1 for b in run['added_bays']]}",
                     ("PRELIMINARY table-reference actions are used for this 50/50 allocation; they are not verified general M/V resistances." if run.get('preliminary') else "Member acceptance uses separately entered M+/M-/V capacities for redistributed diagrams. UDL table capacities are not reused."),
                     "Roof load case | Bay | Role | M+ kNm | M- kNm | V kN | Deflection mm | Cladding transfer kN | Peak transfer kN/m"]
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
                if r.get('preliminary'):
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
        self.canvas.set_result(result, max(0, self.case_combo.currentIndex()))
        self.visual_mode.setCurrentIndex(2)
        self.change_visual_mode()
        self.tabs.setCurrentIndex(1)
        self.export_json_button.setEnabled(True)
        self.export_text_button.setEnabled(True)
        hint = "" if d['passes'] or 'sizing_search' in result else " Analyse and check evaluates current selections; use Auto size to select suitable members."
        self.status_label.setText(f"{state} | estimated materials + fittings ${d['estimated_cost']:,.2f} | SLS {d['global_sls']['ratio']:.3f}.{hint} Connections, full combinations and production portal restraint remain separate.")

    def show_case(self):
        if self.result is None or self.case_combo.currentIndex() < 0:
            self.member_table.setRowCount(0)
            return
        index = self.case_combo.currentIndex()
        self.canvas.case_index = index
        case = self.result["cases"][index]
        self.member_table.setRowCount(len(case["members"]))
        for row, m in enumerate(case["members"]):
            c = self.result["section_checks"].get(m["id"], {})
            ratio = c.get("ratio")
            values = [m["id"], c.get("section", "-"), f"{m['axial_n']/1000:.3f}",
                      f"{ratio:.3f}" if ratio is not None else "-", c.get("mode", "-"), c.get("case", "-"), m["state"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if ratio is not None and column == 3:
                    from PySide6.QtGui import QColor
                    item.setForeground(QColor("#b32929" if ratio > 1 else "#128265"))
                self.member_table.setItem(row, column, item)
        self.canvas.update()

    def change_visual_mode(self):
        mode = self.visual_mode.currentData() or "layout"
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
        self.layout_editor.legend_label.setText(legends[mode])
        self.canvas.update()

    def inspect_member(self, identifier):
        if self.result is None:
            return
        members = self.result["cases"][self.case_combo.currentIndex()]["members"]
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
            identifier = self.result["cases"][self.case_combo.currentIndex()]["members"][row]["id"]
            self.canvas.member_selected = identifier
            self.member_detail.setText(self.check_description(identifier))
            self.canvas.update()

    def check_description(self, identifier):
        text = self.canvas.member_description(identifier)
        check = self.result["section_checks"][identifier]
        row = next((r for r in check["case_checks"] if r["case"] == check["case"]), {})
        if row.get("method") in {"coupled_purlin_beam", 'assigned_half_reference'}:
            text += f"\n{row['beam_role'].title()} beam, bay {row['beam_bay']+1}; axial {row['axial_kn']:.3f} kN. {row['mode']}"
            for d in row.get("beam_demands", []):
                text += f"\n{d['case']}: M+ {d['positive_moment_knm']:.3f}, M- {d['negative_moment_knm']:.3f} kNm; V {d['shear_kn']:.3f} kN; deflection {d['deflection_mm']:.3f} mm"
        elif "capacity_kn" in row:
            text += f"\nN* {row['tension_kn']:.3f} kN / design capacity {row['capacity_kn']:.3f} kN"
            if "gross_yield_kn" in row:
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
