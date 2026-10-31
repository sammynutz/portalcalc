"""Native beam demand plots for the existing roof run and sheeted additions."""
from PySide6.QtCore import Qt, QPointF, QRectF, QSignalBlocker
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel


class BeamPlot(QWidget):
    def __init__(self):
        super().__init__()
        self.run, self.case, self.field = None, None, "moment_knm"
        self.setMinimumHeight(220)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#f7f9fc"))
        if not self.run or not self.case:
            p.setPen(QColor("#42566c"))
            p.drawText(self.rect(), Qt.AlignCenter, "Choose a sheeted-purlin arrangement and run analysis.")
            return
        area = QRectF(56, 35, max(100, self.width()-76), max(100, self.height()-82))
        total = sum(self.run["spans_mm"])
        datasets = []
        if self.field == "transfer_kn_m":
            datasets = [("Cladding transfer (+ roof to added)", "#8b45a6", [b["transfer_samples"] for b in self.case["bays"]])]
        else:
            datasets = [("Existing roof, per ply", "#176b9c", [b["roof"]["samples"] for b in self.case["bays"]]),
                        ("Added single-span", "#c26314", [b["added"]["samples"] for b in self.case["bays"]])]
        largest = max((abs(s[self.field]) for _, _, groups in datasets for samples in groups for s in samples), default=0.)
        largest = max(largest*1.12, 1e-8)
        def point(x, value):
            return QPointF(area.left()+x/total*area.width(), area.center().y()-value/largest*area.height()/2)
        p.setPen(QPen(QColor("#b7c3ce"), 1))
        p.drawLine(point(0, 0), point(total, 0))
        x = 0.
        for i, span in enumerate([*self.run["spans_mm"], 0.]):
            xx = point(x, 0).x()
            p.setPen(QPen(QColor("#d8dfe6"), 1, Qt.DashLine))
            p.drawLine(QPointF(xx, area.top()), QPointF(xx, area.bottom()))
            p.setPen(QColor("#42566c"))
            p.drawText(QRectF(xx-25, area.bottom()+5, 50, 18), Qt.AlignCenter, f"{x/1000:g}")
            x += span
        unit = {"moment_knm": "kNm (+ sagging)", "deflection_mm": "mm (+ downward)", "shear_kn": "kN", "transfer_kn_m": "kN/m"}[self.field]
        p.drawText(8, 33, unit)
        p.drawText(4, int(area.top()+10), f"{largest:.2g}")
        p.drawText(4, int(area.bottom()), f"{-largest:.2g}")
        p.drawText(int(area.center().x()-40), self.height()-7, "Run position, m")
        legend_x = area.left()
        for label, colour, groups in datasets:
            p.setPen(QPen(QColor(colour), 2))
            for samples in groups:
                if not samples:
                    continue
                path = QPainterPath()
                path.moveTo(point(samples[0]["x_mm"], samples[0][self.field]))
                for sample in samples[1:]:
                    path.lineTo(point(sample["x_mm"], sample[self.field]))
                p.drawPath(path)
            p.drawText(QPointF(legend_x, 19), label)
            legend_x += 205


class PurlinBeamResults(QWidget):
    def __init__(self):
        super().__init__()
        self.runs = {}
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.lines, self.cases, self.mode = QComboBox(), QComboBox(), QComboBox()
        for label, value in (("Bending moment", "moment_knm"), ("Shear", "shear_kn"), ("Deflection", "deflection_mm"), ("Cladding transfer", "transfer_kn_m")):
            self.mode.addItem(label, value)
        for control in (self.lines, self.cases, self.mode):
            controls.addWidget(control)
        layout.addLayout(controls)
        self.note = QLabel("Sheeted-purlin beam demands appear here after analysis.")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.plot = BeamPlot()
        layout.addWidget(self.plot, 1)
        self.lines.currentIndexChanged.connect(self.choose_line)
        self.cases.currentIndexChanged.connect(self.show_case)
        self.mode.currentIndexChanged.connect(self.show_case)

    def set_result(self, result):
        self.runs = (result or {}).get("purlin_beams", {}).get("runs", {})
        with QSignalBlocker(self.lines):
            self.lines.clear()
            for key in self.runs:
                self.lines.addItem(f"Roof line {int(key)+1}", key)
        self.choose_line()

    def choose_line(self):
        run = self.runs.get(self.lines.currentData(), {})
        with QSignalBlocker(self.cases):
            self.cases.clear()
            for case in run.get("cases", []):
                self.cases.addItem(case["name"])
        self.show_case()

    def show_case(self):
        run = self.runs.get(self.lines.currentData(), {})
        index = self.cases.currentIndex()
        self.plot.run = run if run.get("cases") else None
        self.plot.case = run["cases"][index] if index >= 0 else None
        self.plot.field = self.mode.currentData()
        if run.get("error"):
            self.note.setText(run["error"])
        elif run.get("cases"):
            state = 'PRELIMINARY: adopted 50/50 applied roof load; no stiffness-derived sharing' if run.get('preliminary') else "Declared cladding/load pickup inputs" if run["coupling_defined"] else "UNVERIFIED: cladding/load pickup inputs missing; fallback demands"
            self.note.setText(f"{state}; k = {run['coupling_kn_m_per_mm']:g} kN/m per mm; direct pickup to added = {run['direct_roof_pickup_fraction']:.1%}. Existing: {run['span_type']}, releases after bays {run['roof_releases_after_bays']}; additions: single span. Demand plots do not establish member or connection acceptance.")
        else:
            self.note.setText("Select a sheeted single-span purlin in Components / ratings to analyse roof-load sharing.")
        self.plot.update()
