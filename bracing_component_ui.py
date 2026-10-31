"""Explicit project/supplier ratings and per-location strut choices."""
import math
from types import SimpleNamespace

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHeaderView, QLabel, QTabWidget,
                              QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from bracing_analysis import AnalysisError
from bracing_components import (StrutPlan, location_options, strut_locations,
                                wire_options, WIRE_DIAMETERS_MM)
from bracing_schedule import ROD_SCHEDULE, SOURCE, rod_options
from purlin_combined import (METHOD, method, properties, compression_capacity,
                            tension_method, tension_capacity)


def numeric(text):
    try:
        value = float(text)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


class ComponentEditor(QWidget):
    changed = Signal()

    def __init__(self, context, profiles, fallback, saved, read_only=False):
        super().__init__()
        self.context, self.profiles, self.fallback = context, profiles, fallback
        self.profile_by_name = {p.name: p for p in profiles}
        self.tension_criteria = SimpleNamespace(net_area_factor=.70, tension_distribution_factor=.85)
        layout = QVBoxLayout(self)
        self.mixed = QCheckBox("Use connected roof purlins; choose additions per strut location")
        self.mixed.setChecked(bool(saved.get("mixed_struts", True)))
        self.mixed.setEnabled(not read_only)
        layout.addWidget(self.mixed)
        note = QLabel("Installed compression uses the adopted Lysaght 1999 method with automatic Metroll Ag/rx' (default Z; C selectable). Declared Nc/Nex is an alternative. Member tension is calculated using G450 minima (fy450/fu480 MPa), with the net-area factor and kt from Sections / criteria; declared Nt/source can override it. These are member sizing assumptions; connection checks remain separate. Linked counts share axial force; enter 2 at a connected apex pair.")
        note.setWordWrap(True)
        layout.addWidget(note)
        tabs = QTabWidget()
        self.tabs = tabs
        layout.addWidget(tabs, 1)
        self.strut_table = QTableWidget()
        self.strut_table.setColumnCount(7)
        self.strut_table.setHorizontalHeaderLabels(["Strut", "Purlin", "Braced bay", "Identical linked count", "Arrangement", "Added steel", '50/50 added purlin'])
        self.strut_rows = []
        self.added_purlin_combos = {}
        member_settings = saved.get("strut_members", {})
        counts = {key: value.get("existing_count", 1) for key, value in member_settings.items() if isinstance(value, dict)}
        try:
            locations = strut_locations(context, saved.get("purlin_axial_ratings"), counts)
        except AnalysisError as error:
            locations = []
            note.setText(note.text()+"\n"+str(error))
        self.strut_table.setRowCount(len(locations))
        for row, location in enumerate(locations):
            config = member_settings.get(location["id"], {})
            for col, value in enumerate((location["id"], location["purlin"]["section"], "Yes" if location["braced_bay"] else "No", str(location["existing_count"]))):
                item = QTableWidgetItem(value)
                if col != 3:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.strut_table.setItem(row, col, item)
            arrangement = QComboBox()
            for text, key in (("Existing purlins only", "existing_purlins"), ("Extra unsheeted purlin", "additional_purlin"),
                              ('Sheeted purlin: 50/50 load (preliminary)', 'shared_purlin'),
                              ("Sheeted single-span purlin (beam model)", "sheeted_purlin"),
                              ("Back-to-back mate", "back_to_back"), ("Additional steel strut", "additional_steel")):
                arrangement.addItem(text, key)
            arrangement.setCurrentIndex(max(0, arrangement.findData(config.get("arrangement", "existing_purlins"))))
            steel = QComboBox()
            for p in profiles:
                steel.addItem(p.name, p.name)
            steel_name = config.get("steel_section", fallback.name)
            steel_index = steel.findData(steel_name)
            if steel_index < 0:
                steel.addItem(f"Missing saved section: {steel_name}", None)
                steel_index = steel.count()-1
            steel.setCurrentIndex(steel_index)
            self.strut_table.setCellWidget(row, 4, arrangement)
            self.strut_table.setCellWidget(row, 5, steel)
            added_combo = QComboBox()
            for candidate in location['added_purlins']:
                added_combo.addItem(candidate['section'].replace('C/Z ', 'C '), candidate['section'])
            added_name = config.get('added_purlin_section', location['purlin']['section'])
            index = added_combo.findData(added_name)
            if index < 0:
                added_combo.addItem('Unavailable saved purlin: '+str(added_name), None)
                index = added_combo.count()-1
            added_combo.setCurrentIndex(index)
            self.added_purlin_combos[location['id']] = added_combo
            self.strut_table.setCellWidget(row, 6, added_combo)
            added_combo.setEnabled(not read_only and arrangement.currentData() == 'shared_purlin')
            added_combo.currentIndexChanged.connect(lambda *_: self.changed.emit())
            arrangement.currentIndexChanged.connect(lambda _i, a=arrangement, c=added_combo: c.setEnabled(not read_only and a.currentData() == 'shared_purlin'))
            arrangement.setEnabled(not read_only)
            steel.setEnabled(not read_only and arrangement.currentData() == "additional_steel")
            arrangement.currentIndexChanged.connect(lambda _index, a=arrangement, s=steel: s.setEnabled(not read_only and a.currentData() == "additional_steel"))
            self.strut_rows.append((location, arrangement, steel))
        tabs.addTab(self.strut_table, "Strut locations")
        self.shared_table = QTableWidget(0, 3)
        self.purlin_table = QTableWidget()
        keys = {}
        for location in locations:
            keys[location["purlin"]["basis_key"]] = location
        self.rating_keys = sorted(keys)
        self.shared_table.setRowCount(len(keys))
        self.shared_table.setHorizontalHeaderLabels(['Section / span', 'Cladding fixed + bridging installed', 'Bridging rows for added purlins'])
        self.purlin_table.setColumnCount(14)
        self.purlin_table.setHorizontalHeaderLabels(["Section / restraint basis", "Span m", "Declared Nc kN", "Declared Nt kN", "Declared Nex kN", "B2B roof sharing", "Declared rating source", "Compression method", "Shape", "Metroll Ag mm²", "Web-axis radius mm", "Calculated Ns kN", "Tension method", "Calculated tension kN"])
        self.purlin_table.setRowCount(len(keys))
        self.purlin_methods, self.purlin_shapes = {}, {}
        self.purlin_tension_methods = {}
        self.extra_table = QTableWidget()
        self.extra_table.setColumnCount(7)
        self.extra_table.setHorizontalHeaderLabels(["Section / span", "Extra residual Nc kN", "Extra residual Nt kN", "Extra SLS mm", "B2B mate residual Nc kN", "B2B mate residual Nt kN", "B2B mate SLS mm"])
        self.extra_table.setRowCount(len(keys))
        self.paired_table = QTableWidget()
        self.paired_table.setColumnCount(14)
        self.paired_table.setHorizontalHeaderLabels(["Section / span", "Cladding k: kN/m per mm", "Cladding/load pickup source", "Roof M+ kNm", "Roof M- kNm", "Roof V kN", "Added M+ kNm", "Added M- kNm", "Added V kN", "Added Nt kN", "Member capacity source", "Lap/span ratio", "Direct roof pickup to added: fraction 0-1", "Roof releases after bays: auto / none / 2,4"])
        self.paired_table.setRowCount(len(keys))
        self.bending_keys = {}
        for row, key in enumerate(self.rating_keys):
            location = keys[key]
            rating = (saved.get("purlin_axial_ratings") or {}).get(key, {})
            label = QTableWidgetItem(f"{location['purlin']['section']} / {location['length_mm']/1000:g} m")
            label.setFlags(label.flags() & ~Qt.ItemIsEditable)
            self.shared_table.setItem(row, 0, label)
            fixed = QTableWidgetItem()
            fixed.setFlags((fixed.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            fixed.setCheckState(Qt.Checked if rating.get('shared_cladding_fixed', True) else Qt.Unchecked)
            self.shared_table.setItem(row, 1, fixed)
            self.shared_table.setItem(row, 2, QTableWidgetItem(str(rating.get('shared_bridging_rows', location['purlin']['bending_snapshot'].get('bridging_rows', 0)))))
            self.bending_keys[key] = location["purlin"].get("bending_key")
            for col, value in ((0, key.split("|bay_span_mm=")[0]), (1, f"{location['length_mm']/1000:g}"),
                               (2, rating.get("compression_kn")), (3, rating.get("tension_kn")),
                               (4, rating.get("elastic_buckling_kn")), (6, rating.get("source", ""))):
                item = QTableWidgetItem("" if value is None else str(value))
                if col < 2:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.purlin_table.setItem(row, col, item)
            shared = QTableWidgetItem()
            shared.setToolTip("Declared sharing for an independently established paired detail. For cladding-only coupling with a single-span addition use the sheeted-purlin beam option.")
            shared.setFlags((shared.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            shared.setCheckState(Qt.Checked if rating.get("back_to_back_sharing") is True else Qt.Unchecked)
            self.purlin_table.setItem(row, 5, shared)
            choice = QComboBox()
            choice.addItem("Lysaght 1999 simplified", METHOD)
            choice.addItem("Declared Nc / Nex", "declared")
            choice.setCurrentIndex(max(0, choice.findData(method(rating))))
            shape = QComboBox()
            shape.addItems(["Z", "C"])
            shape.setCurrentText(rating.get("purlin_shape", "Z"))
            for col, widget in ((7, choice), (8, shape)):
                widget.setEnabled(not read_only)
                self.purlin_table.setCellWidget(row, col, widget)
            self.purlin_methods[key], self.purlin_shapes[key] = choice, shape
            tension_choice = QComboBox()
            tension_choice.addItem("Calculated member capacity", "calculated")
            tension_choice.addItem("Declared Nt / source", "declared")
            tension_choice.setCurrentIndex(max(0, tension_choice.findData(tension_method(rating))))
            tension_choice.setEnabled(not read_only)
            self.purlin_table.setCellWidget(row, 12, tension_choice)
            self.purlin_tension_methods[key] = tension_choice
            for col in (9, 10, 11, 13):
                item = QTableWidgetItem()
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.purlin_table.setItem(row, col, item)
            def refresh(_index=0, r=row, loc=location, c=choice, s=shape):
                with QSignalBlocker(self.purlin_table):
                    for col in (2, 4):
                        item = self.purlin_table.item(r, col)
                        item.setFlags(item.flags() | Qt.ItemIsEditable if c.currentData() == "declared" else item.flags() & ~Qt.ItemIsEditable)
                    try:
                        props = properties(loc["purlin"]["section"], s.currentText())
                        values = (props["area_mm2"], props["web_rx_mm"], compression_capacity(props["area_mm2"], props["web_rx_mm"], loc["length_mm"]))
                        message = props["source"]+"; "+props["axis_basis"]
                    except AnalysisError as error:
                        values, message = (None, None, None), str(error)
                    for col, value in zip((9, 10, 11), values):
                        self.purlin_table.item(r, col).setText("Unavailable" if value is None else f"{value:.3f}")
                        self.purlin_table.item(r, col).setToolTip(message)
                self.changed.emit()
            refresh()
            choice.currentIndexChanged.connect(refresh)
            shape.currentIndexChanged.connect(refresh)
            shape.currentIndexChanged.connect(lambda *_: self.refresh_tension_preview())
            tension_choice.currentIndexChanged.connect(lambda *_: self.changed.emit())
            label = QTableWidgetItem(f"{location['purlin']['section']} / {location['length_mm']/1000:g} m")
            label.setFlags(label.flags() & ~Qt.ItemIsEditable)
            self.extra_table.setItem(row, 0, label)
            current = rating.get("extra_bending_key") == self.bending_keys[key]
            for col, field in enumerate(("extra_compression_kn", "extra_tension_kn", "extra_sls_deflection_mm", "back_to_back_compression_kn", "back_to_back_tension_kn", "back_to_back_sls_deflection_mm"), 1):
                value = rating.get(field) if current else None
                self.extra_table.setItem(row, col, QTableWidgetItem("" if value is None else str(value)))
            pair_current = rating.get("beam_capacity_key") == self.bending_keys[key]
            for col, value in enumerate((f"{location['purlin']['section']} / {location['length_mm']/1000:g} m",
                    rating.get("cladding_coupling_kn_m_per_mm"), rating.get("cladding_coupling_source", ""),
                    *(rating.get(field) if pair_current else None for field in
                      ("beam_roof_positive_moment_knm", "beam_roof_negative_moment_knm", "beam_roof_shear_kn", "beam_added_positive_moment_knm", "beam_added_negative_moment_knm", "beam_added_shear_kn", "beam_added_tension_kn")),
                    rating.get("beam_capacity_source", "") if pair_current else "", rating.get("beam_lap_ratio", .15), rating.get("beam_direct_roof_pickup_fraction"), rating.get("beam_roof_releases", "auto"))):
                item = QTableWidgetItem("" if value is None else str(value))
                if col == 0:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.paired_table.setItem(row, col, item)
        tabs.addTab(self.purlin_table, "Purlin axial ratings")
        tabs.addTab(self.extra_table, "Added-ply residual ratings")
        tabs.addTab(self.paired_table, "Sheeted-purlin beam")
        tabs.addTab(self.shared_table, '50/50 strut assumptions')
        self.shared_table.setToolTip('PRELIMINARY reference-envelope check, not manufacturer M/V resistances. Apply half the local roof load to each line in added bays; axial shares by EA. Original continuity and neighbours retained. Requires flange screw fixing and stated bridging. Use the finite beam model with declared M/V for supplier-rated checks.')
        note.setText(note.text()+' Automatic sizing also considers sheeted C-purlin struts with an adopted 50/50 applied-load split and PRELIMINARY table-reference checks. Confirm cladding fixing and bridging in 50/50 strut assumptions. These inputs do not establish the connection capacity.')
        note.setText(note.text()+" Sheeted single-span additions use a roof-run beam model. Enter cladding transfer stiffness, direct roof-load pickup fraction and source; missing data gives fallback demands and UNVERIFIED checks. Metroll's rotational-restraint value is not this transfer stiffness. Redistributed diagrams need actual M+/M-/V member ratings. See Sheeted-purlin beam.")
        note.setText(note.text()+" Unsheeted additions use separate residual ratings; installed roof tables are not reused for them.")
        self.wire_table = QTableWidget()
        self.wire_table.setColumnCount(10)
        self.wire_table.setHorizontalHeaderLabels(["Diameter mm", "Construction / grade", "EA kN", "Factored brace tension kN", "$/m", "Listed fittings $/diagonal", "Strength / rating source", "EA source", "Forged-rated turnbuckle", "Grade S turnbuckle"])
        self.wire_table.setRowCount(len(WIRE_DIAMETERS_MM))
        scheduled_wires = wire_options(saved.get("wire_rope_ratings"))
        for row, diameter in enumerate(WIRE_DIAMETERS_MM):
            option = scheduled_wires[row]
            record = option["rating"]
            for col, value in enumerate((diameter, record.get("construction", ""), record.get("ea_kn"), record.get("design_tension_kn"),
                                         record.get("price_per_m"), record.get("fittings_per_run"), record.get("source", ""), record.get("ea_source", ""), option["forged_turnbuckle"], option["grade_s_turnbuckle"])):
                item = QTableWidgetItem("" if value is None else str(value))
                if col in (0, 8, 9):
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.wire_table.setItem(row, col, item)
        tabs.addTab(self.wire_table, "Wire-rope ratings")
        self.rod_table = QTableWidget()
        self.rod_table.setColumnCount(7)
        self.rod_table.setHorizontalHeaderLabels(["Rod mm", "Grade MPa", "Factored tension kN", "Forged-rated TB", "Grade S TB", "Forged fitting $/diagonal", "Capacity source"])
        scheduled_rods = rod_options(saved.get("rod_fitting_prices"))
        self.rod_table.setRowCount(len(scheduled_rods))
        for row, option in enumerate(scheduled_rods):
            diameter = list(ROD_SCHEDULE)[row]
            for col, value in enumerate((diameter, option["grade_mpa"], option["design_tension_kn"], option["forged_turnbuckle"], option["grade_s_turnbuckle"], option["fitting_unit_cost"], SOURCE)):
                item = QTableWidgetItem("" if value is None else str(value))
                if col != 5:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.rod_table.setItem(row, col, item)
        tabs.addTab(self.rod_table, "Rod schedule / fitting prices")
        for table in (self.strut_table, self.purlin_table, self.extra_table, self.paired_table, self.shared_table, self.wire_table, self.rod_table):
            table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
            table.setColumnWidth(0, 190)
            table.cellChanged.connect(lambda *_: self.changed.emit())
            if read_only:
                table.setEditTriggers(QTableWidget.NoEditTriggers)
                for row in range(table.rowCount()):
                    for col in range(table.columnCount()):
                        item = table.item(row, col)
                        if item:
                            item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
        for table, widths in ((self.rod_table, (65, 85, 145, 120, 100, 180, 420)),
                              (self.wire_table, (95, 250, 95, 175, 80, 180, 420, 300, 180, 155))):
            for column, width in enumerate(widths):
                table.setColumnWidth(column, width)
        self.purlin_table.setColumnWidth(7, 200)
        self.purlin_table.setColumnWidth(8, 60)
        for column in (9, 10, 11, 13):
            self.purlin_table.setColumnWidth(column, 145)
        self.purlin_table.setColumnWidth(12, 220)
        for column, width in ((1, 190), (2, 280), (10, 300), (11, 120), (12, 280), (13, 310)):
            self.paired_table.setColumnWidth(column, width)
        header = self.purlin_table.horizontalHeader()
        for visual, logical in enumerate((0, 7, 11, 12, 13, 8, 1, 9, 10, 3, 5, 2, 4, 6)):
            header.moveSection(header.visualIndex(logical), visual)
        self.refresh_tension_preview()
        self.mixed.toggled.connect(lambda *_: self.changed.emit())
        for location, arrangement, steel in self.strut_rows:
            arrangement.currentIndexChanged.connect(lambda *_: self.changed.emit())
            steel.currentIndexChanged.connect(lambda *_: self.changed.emit())

    @staticmethod
    def cell(table, row, col):
        item = table.item(row, col)
        return item.text().strip() if item else ""

    def refresh_tension_preview(self, net_factor=None, kt=None):
        if net_factor is not None:
            self.tension_criteria = SimpleNamespace(net_area_factor=net_factor, tension_distribution_factor=kt)
        locations = {loc["purlin"]["basis_key"]: loc for loc, _, _ in self.strut_rows}
        with QSignalBlocker(self.purlin_table):
            for row, key in enumerate(self.rating_keys):
                data = {**locations[key]["purlin"], "axial_rating": {
                    "tension_method": "calculated", "purlin_shape": self.purlin_shapes[key].currentText()}}
                item = self.purlin_table.item(row, 13)
                try:
                    t = tension_capacity(data, self.tension_criteria)
                    item.setText(f"{t['tension_capacity_kn']:.3f}")
                    item.setToolTip(f"{t['tension_basis']} An={t['tension_net_area_mm2']:.2f} mm²; kt={t['tension_kt']:.3f}. {t['tension_source']}")
                except AnalysisError as error:
                    item.setText("Unavailable")
                    item.setToolTip(str(error))

    def snapshot(self):
        ratings = {}
        for row, key in enumerate(self.rating_keys):
            ratings[key] = {"basis_key": key, "compression_kn": numeric(self.cell(self.purlin_table, row, 2)),
                            "tension_kn": numeric(self.cell(self.purlin_table, row, 3)),
                            "elastic_buckling_kn": numeric(self.cell(self.purlin_table, row, 4)),
                            "back_to_back_sharing": self.purlin_table.item(row, 5).checkState() == Qt.Checked,
                            "source": self.cell(self.purlin_table, row, 6)}
            ratings[key].update(compression_method=self.purlin_methods[key].currentData(), purlin_shape=self.purlin_shapes[key].currentText(),
                                tension_method=self.purlin_tension_methods[key].currentData())
            bridge = numeric(self.cell(self.shared_table, row, 2))
            ratings[key].update(shared_cladding_fixed=self.shared_table.item(row, 1).checkState() == Qt.Checked,
                shared_bridging_rows=int(bridge) if bridge is not None and bridge.is_integer() else -1)
            ratings[key].update(beam_capacity_key=self.bending_keys[key],
                cladding_coupling_source=self.cell(self.paired_table, row, 2),
                beam_roof_releases=self.cell(self.paired_table, row, 13),
                beam_capacity_source=self.cell(self.paired_table, row, 10))
            for col, field in ((1, "cladding_coupling_kn_m_per_mm"), (3, "beam_roof_positive_moment_knm"),
                    (4, "beam_roof_negative_moment_knm"), (5, "beam_roof_shear_kn"), (6, "beam_added_positive_moment_knm"),
                    (7, "beam_added_negative_moment_knm"), (8, "beam_added_shear_kn"), (9, "beam_added_tension_kn"), (11, "beam_lap_ratio"), (12, "beam_direct_roof_pickup_fraction")):
                ratings[key][field] = numeric(self.cell(self.paired_table, row, col))
            ratings[key]["extra_bending_key"] = self.bending_keys[key]
            for col, field in enumerate(("extra_compression_kn", "extra_tension_kn", "extra_sls_deflection_mm", "back_to_back_compression_kn", "back_to_back_tension_kn", "back_to_back_sls_deflection_mm"), 1):
                ratings[key][field] = numeric(self.cell(self.extra_table, row, col))
        wires = {}
        for row, diameter in enumerate(WIRE_DIAMETERS_MM):
            wires[str(diameter)] = {"construction": self.cell(self.wire_table, row, 1),
                                   **{key: numeric(self.cell(self.wire_table, row, col)) for col, key in
                                      ((2, "ea_kn"), (3, "design_tension_kn"), (4, "price_per_m"), (5, "fittings_per_run"))},
                                   "source": self.cell(self.wire_table, row, 6), "ea_source": self.cell(self.wire_table, row, 7)}
        members = {}
        for row, (location, arrangement, steel) in enumerate(self.strut_rows):
            raw = numeric(self.cell(self.strut_table, row, 3))
            count = int(raw) if raw is not None and raw.is_integer() else -1
            members[location["id"]] = {"existing_count": count, "arrangement": arrangement.currentData(), "steel_section": steel.currentData(),
                                      'added_purlin_section': self.added_purlin_combos[location['id']].currentData()}
        prices = {str(d): numeric(self.cell(self.rod_table, row, 5)) for row, d in enumerate(ROD_SCHEDULE)}
        return {"mixed_struts": self.mixed.isChecked(), "strut_members": members, "rod_fitting_prices": prices,
                "purlin_axial_ratings": ratings, "wire_rope_ratings": wires}

    def wire_candidates(self):
        return wire_options(self.snapshot()["wire_rope_ratings"])

    def brace_candidates(self):
        data = self.snapshot()
        angles = [option for option in self.context["end_brace_options"] if "ROD" not in option["label"].upper()]
        return rod_options(data["rod_fitting_prices"])+angles+wire_options(data["wire_rope_ratings"])

    def plan(self, context, fallback):
        config = self.snapshot()
        locations = strut_locations(context, config["purlin_axial_ratings"],
                                   {key: row["existing_count"] for key, row in config["strut_members"].items()})
        members = {}
        for location in locations:
            row = config["strut_members"][location["id"]]
            profile = self.profile_by_name.get(row["steel_section"])
            if profile is None:
                raise AnalysisError("Unknown added steel profile in strut plan")
            options = [option for option in location_options(location, [profile]) if option.arrangement == row['arrangement']]
            if row['arrangement'] == 'shared_purlin':
                options = [option for option in options if next(c['data']['section'] for c in option.components if c.get('shared_beam')) == row['added_purlin_section']]
            if not options:
                raise AnalysisError('Selected added purlin is unavailable for the current span/bridging data')
            members[location['id']] = options[0]
        return StrutPlan(members, fallback)

    def adopt(self, plan):
        with QSignalBlocker(self):
            for location, arrangement, steel in self.strut_rows:
                option = plan.members[location["id"]]
                arrangement.setCurrentIndex(arrangement.findData(option.arrangement))
                added = next((c["profile"] for c in option.components if c["type"] == "steel"), None)
                if added:
                    steel.setCurrentIndex(steel.findData(added.name))
                purlin = next((c['data']['section'] for c in option.components if c.get('shared_beam')), None)
                if purlin:
                    combo = self.added_purlin_combos[location['id']]
                    if combo.findData(purlin) < 0:
                        combo.addItem(purlin.replace('C/Z ', 'C '), purlin)
                    combo.setCurrentIndex(combo.findData(purlin))

    def refresh_bracing(self, pinned):
        with QSignalBlocker(self.strut_table):
            for row, (location, arrangement, steel) in enumerate(self.strut_rows):
                braced = any(f"/bay/{location['bay']}/" in p+"/" for p in pinned)
                self.strut_table.item(row, 2).setText("Yes" if braced else "No")

    def focus_sizing_issue(self, diagnostic):
        key = diagnostic["rating_key"]
        if key not in self.rating_keys:
            return
        row = self.rating_keys.index(key)
        table = self.paired_table if diagnostic["input_target"] == "beam" else self.purlin_table
        self.tabs.setCurrentWidget(table)
        column = diagnostic["input_column"]
        table.setCurrentCell(row, column)
        item = table.item(row, column)
        if item:
            table.scrollToItem(item)
        table.setFocus()
