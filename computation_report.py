"""Paginated, bookmarked full computation PDF from frozen calculation evidence."""
from collections import defaultdict
from html import escape
import math
import os
from pathlib import Path
import tempfile

from loads import GRAVITY_M_S2, STEEL_DENSITY_KG_M3

from reportlab import __file__ as reportlab_file
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, LongTable,
                               PageBreak, PageTemplate, Paragraph, Spacer, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents


INK = colors.HexColor("#182d3b")
BLUE = colors.HexColor("#175d78")
PALE = colors.HexColor("#eef4f7")
GREY = colors.HexColor("#576b78")
RULE = colors.HexColor("#d7e2e8")
RED = colors.HexColor("#ac3030")
GREEN = colors.HexColor("#226a51")
PAGE_W, PAGE_H = A4
WIDTH = PAGE_W - 36 * mm


def fonts():
    if "Calc" in pdfmetrics.getRegisteredFontNames():
        return
    folder = Path("C:/Windows/Fonts")
    regular, bold = folder / "segoeui.ttf", folder / "segoeuib.ttf"
    if not regular.exists() or not bold.exists():
        folder = Path(reportlab_file).parent / "fonts"
        regular, bold = folder / "Vera.ttf", folder / "VeraBd.ttf"
    pdfmetrics.registerFont(TTFont("Calc", str(regular)))
    pdfmetrics.registerFont(TTFont("Calc-Bold", str(bold)))
    pdfmetrics.registerFontFamily("Calc", normal="Calc", bold="Calc-Bold", italic="Calc", boldItalic="Calc-Bold")


def clean(value):
    return str(value).replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")


def fmt(value, digits=3):
    if value is None:
        return "Not specified"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (float, int)) or hasattr(value, "item"):
        value = float(value)
        if math.isnan(value):
            return "Not available"
        if math.isinf(value):
            return "Unbounded" if value > 0 else "-Unbounded"
        if value != 0 and (abs(value) >= 1e6 or abs(value) < 0.0001):
            return f"{value:.3e}"
        return f"{value:,.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:,.0f}"
    return clean(value) if str(value).strip() else "Not specified"


def label(key):
    names = {"g": "Permanent G", "q": "Imposed Q", "solar": "Solar", "shs": "SHS", "ub": "UB",
             "cpi": "Cpi", "cpe": "Cpe", "il": "Importance level", "ahd": "AHD", "hvac": "HVAC"}
    words = key.replace("_", " ").split()
    return " ".join(names.get(word.lower(), word.capitalize() if i == 0 else word) for i, word in enumerate(words))


def input_rows(value, prefix=""):
    rows = []
    if isinstance(value, dict):
        for key, item in value.items():
            suffix = next((unit for unit in ("knm_rad", "kn_mm", "kg_m3", "kpa", "kn_m", "mm", "deg", "mpa", "m", "t") if key.endswith("_" + unit)), "")
            name = key[:-(len(suffix) + 1)] if suffix else key
            heading = f"{prefix} / {label(name)}" if prefix else label(name)
            if isinstance(item, (dict, list)):
                rows.extend(input_rows(item, heading))
            else:
                units = {"knm_rad": "kNm/rad", "kn_mm": "kN/mm", "kg_m3": "kg/m3", "kpa": "kPa", "kn_m": "kN/m", "mpa": "MPa"}.get(suffix, suffix)
                rows.append([heading, fmt(item), units])
    elif isinstance(value, list):
        if not value:
            rows.append([prefix, "None specified", ""])
        for index, item in enumerate(value, 1):
            rows.extend(input_rows(item, f"{prefix} {index}"))
    else:
        rows.append([prefix, fmt(value), ""])
    return rows


class FrameFigure(Flowable):
    """Vector model diagram with optional signed bending or applied nodal loads."""
    def __init__(self, model, case=None, mode="geometry", height=165):
        super().__init__()
        self.width, self.height = WIDTH, height
        self.model, self.case, self.mode = model, case, mode

    def draw(self):
        c = self.canv
        def caption(text):
            paragraph = Paragraph(escape(text), ParagraphStyle("FigureCaption", fontName="Calc", fontSize=6.5, leading=8, textColor=GREY))
            _, height = paragraph.wrap(self.width - 8, 24)
            paragraph.drawOn(c, 4, max(0, 8 - height))
        nodes = {n["id"]: n for n in self.model["nodes"]}
        xs, ys = [n["x_mm"] for n in nodes.values()], [n["y_mm"] for n in nodes.values()]
        span, height = max(xs) - min(xs), max(ys) - min(ys)
        plot_margin = 78 if self.mode != "geometry" else 34
        scale = min((self.width - plot_margin) / max(span, 1), (self.height - plot_margin - 4) / max(height, 1))
        x0 = (self.width - span * scale) / 2 - min(xs) * scale
        y0 = (35 if self.mode != "geometry" else 22) - min(ys) * scale
        point = lambda n: (x0 + n["x_mm"] * scale, y0 + n["y_mm"] * scale)
        for e in self.model["elements"]:
            a, b = point(nodes[e["start"]]), point(nodes[e["end"]])
            c.setStrokeColor(colors.HexColor("#526d7c") if self.mode == "geometry" else RULE)
            c.setLineWidth(0.7 if "WEB" in e["group"] or e["group"] in {"POST", "APEX"} else 1.4)
            c.line(*a, *b)
        if self.mode == "geometry":
            for n in nodes.values():
                if "Uy" in n["restraints"]:
                    x, y = point(n)
                    c.setStrokeColor(BLUE)
                    p = c.beginPath()
                    p.moveTo(x, y)
                    p.lineTo(x - 4, y - 7)
                    p.lineTo(x + 4, y - 7)
                    p.close()
                    c.drawPath(p)
                    c.setFont("Calc", 6.8)
                    c.drawCentredString(x, y - 17, f"N{n['id']}")
            c.setFillColor(GREY)
            c.setFont("Calc", 7.5)
            c.drawCentredString(self.width / 2, 1, f"Model width {span / 1000:.3f} m | max height {max(ys) / 1000:.3f} m | dimensions to analytical centrelines")
        elif self.mode == "moment":
            members = {r["member"]: r for r in self.case["members"]}
            peak = max((abs(p[2]) for r in members.values() for p in r.get("diagram", [])), default=1)
            diagram_scale = 24 / max(peak, 1)
            c.setStrokeColor(BLUE)
            c.setLineWidth(0.65)
            for e in self.model["elements"]:
                samples = members[e["id"]].get("diagram", [])
                if not samples:
                    continue
                a, b = point(nodes[e["start"]]), point(nodes[e["end"]])
                length = math.hypot(b[0] - a[0], b[1] - a[1])
                if length <= 1e-9:
                    continue
                nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
                p = c.beginPath()
                p.moveTo(*a)
                for i, sample in enumerate(samples):
                    t = i / (len(samples) - 1)
                    offset = sample[2] * diagram_scale
                    p.lineTo(a[0] + t * (b[0] - a[0]) + nx * offset, a[1] + t * (b[1] - a[1]) + ny * offset)
                p.lineTo(*b)
                c.drawPath(p)
            c.setFont("Calc", 7)
            c.setFillColor(GREY)
            caption(f"Signed local bending, 9 stations/member; plotted ordinate scale independent of geometry. Sample peak {peak / 1e6:.3f} kNm.")
        elif self.mode == "loads":
            peak = max((max(abs(n["fx_kn"]), abs(n["fy_kn"])) for n in self.case["nodes"]), default=1)
            c.setStrokeColor(RED)
            c.setLineWidth(.75)
            for n in self.case["nodes"]:
                x, y = point(n)
                for fx, fy in ((n["fx_kn"], 0), (0, n["fy_kn"])):
                    length = max(abs(fx), abs(fy))
                    if length < max(peak * .01, 1e-9):
                        continue
                    dx, dy = fx / peak * 24, fy / peak * 24
                    start_x, start_y = x - dx, y - dy
                    c.line(start_x, start_y, x, y)
                    angle = math.atan2(dy, dx)
                    for sign in (-1, 1):
                        c.line(x, y, x - 3 * math.cos(angle + sign * .45), y - 3 * math.sin(angle + sign * .45))
            c.setFont("Calc", 7)
            c.setFillColor(GREY)
            caption(f"Equivalent global nodal loads; arrows scaled to peak component {peak:.3f} kN. Applied nodal couples are listed in the optional full case tables.")


class ComputationDoc(BaseDocTemplate):
    def __init__(self, filename, data, **kwargs):
        super().__init__(filename, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                         topMargin=20 * mm, bottomMargin=18 * mm,
                         title=f"{data['metadata'].get('project', 'Portal frame')} - Structural computations",
                         author=data["metadata"].get("prepared_by") or "PortalCalc", **kwargs)
        self.data = data
        self.addPageTemplates(PageTemplate(id="Report", frames=[Frame(self.leftMargin, self.bottomMargin,
                              self.width, self.height, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)],
                              onPage=self.page_furniture))

    def page_furniture(self, c, doc):
        c.saveState()
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        if doc.page > 1:
            c.line(18 * mm, PAGE_H - 14 * mm, PAGE_W - 18 * mm, PAGE_H - 14 * mm)
            c.setFillColor(BLUE)
            c.setFont("Calc-Bold", 8)
            c.drawString(18 * mm, PAGE_H - 11 * mm, "PORTALCALC / STRUCTURAL COMPUTATIONS")
            c.setFont("Calc", 7.5)
            c.setFillColor(GREY)
            project = clean(self.data["metadata"].get("job_number") or "Calculation record")
            c.drawRightString(PAGE_W - 18 * mm, PAGE_H - 11 * mm, project[:55])
        c.line(18 * mm, 13.5 * mm, PAGE_W - 18 * mm, 13.5 * mm)
        c.setFillColor(GREY)
        c.setFont("Calc", 7)
        c.drawString(18 * mm, 9 * mm, f"PortalCalc {self.data['software_version']} | Rev {clean(self.data['metadata'].get('revision') or '-')} | {self.data['issued_at'][:10]}")
        c.drawRightString(PAGE_W - 18 * mm, 9 * mm, f"Page {doc.page}")
        c.restoreState()

    def afterFlowable(self, item):
        if isinstance(item, Paragraph) and hasattr(item, "report_anchor"):
            key = item.report_anchor
            self.canv.bookmarkPage(key)
            level = 0 if item.style.name == "ReportH1" else 1
            text = item.getPlainText()
            self.canv.addOutlineEntry(text, key, level, closed=False)
            self.notify("TOCEntry", (level, text, self.page, key))


class Report:
    def __init__(self, data, detailed):
        self.data, self.detailed = data, detailed
        self.items = []
        self.counter = 0
        self.case_ids = {case["name"]: f"C{i:02d}" for i, case in enumerate(data["cases"], 1)}
        self.case_anchors = {case["name"]: f"case_{i}" for i, case in enumerate(data["cases"], 1)}
        self.styles = {
            "body": ParagraphStyle("Body", fontName="Calc", fontSize=9.2, leading=13, textColor=INK, spaceAfter=7),
            "small": ParagraphStyle("Small", fontName="Calc", fontSize=8, leading=11, textColor=GREY, spaceAfter=5),
            "cell": ParagraphStyle("Cell", fontName="Calc", fontSize=8, leading=10.5, textColor=INK, splitLongWords=True),
            "number": ParagraphStyle("Number", fontName="Calc", fontSize=8, leading=10.5, textColor=INK, alignment=TA_RIGHT),
            "header": ParagraphStyle("Header", fontName="Calc-Bold", fontSize=8, leading=10.5, textColor=colors.white),
            "h1": ParagraphStyle("ReportH1", fontName="Calc-Bold", fontSize=19, leading=23, textColor=BLUE, spaceAfter=13, keepWithNext=True),
            "h2": ParagraphStyle("ReportH2", fontName="Calc-Bold", fontSize=11, leading=14, textColor=BLUE, spaceBefore=10, spaceAfter=8, keepWithNext=True),
        }

    def p(self, text, style="body", markup=False):
        paragraph = Paragraph(text if markup else escape(clean(text)).replace("\n", "<br/>"), self.styles[style])
        self.items.append(paragraph)
        return paragraph

    def heading(self, text, level=1, anchor=None):
        if level == 1:
            self.items.append(PageBreak())
        p = self.p(text, "h1" if level == 1 else "h2")
        self.counter += 1
        p.report_anchor = anchor or f"heading_{self.counter}"

    def table(self, headers, rows, weights=None, numeric=()):
        if not rows:
            self.p("No applicable results for this model.", "small")
            return
        widths = [WIDTH * w / sum(weights) for w in weights] if weights else [WIDTH / len(headers)] * len(headers)
        cells = [[Paragraph(escape(clean(v)), self.styles["header"]) for v in headers]]
        for row in rows:
            cells.append([Paragraph(escape(clean(v)).replace("\n", "<br/>"), self.styles["number" if i in numeric else "cell"]) for i, v in enumerate(row)])
        table = LongTable(cells, colWidths=widths, repeatRows=1, hAlign="LEFT", splitByRow=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BLUE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
            ("LINEBELOW", (0, 0), (-1, 0), 0.7, BLUE),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        self.items.extend([table, Spacer(1, 8)])

    def case_ref(self, name):
        return self.case_ids.get(name, str(name or "-"))

    def source(self, item):
        member = item.get("member", "-")
        prefix = f"M{member}" if str(member).isdigit() else str(member)
        return f"{prefix} / {self.case_ref(item.get('combo'))}"

    def notes(self, lines):
        """Flow native calculation notes without fixed-width text or clipping."""
        lines = list(lines)
        index = 0
        while index < len(lines):
            line = str(lines[index]).strip()
            index += 1
            if line.startswith("Check") and "Demand" in line and "Capacity" in line and "|" in line:
                headers = [part.strip() for part in line.split("|")]
                rows = []
                while index < len(lines) and str(lines[index]).count("|") >= len(headers) - 1:
                    rows.append([part.strip() for part in str(lines[index]).split("|", len(headers) - 1)])
                    index += 1
                if rows:
                    self.table(headers, rows, [1.2, .9, .9, .6, .7, 2] if len(headers) == 6 else None)
                continue
            if not line:
                self.items.append(Spacer(1, 4))
            elif line.isupper() and len(line) < 100 and not "|" in line:
                self.p(line, "h2")
            else:
                self.p(line, "small")

    def make(self):
        d, meta = self.data, self.data["metadata"]
        self.items.append(Spacer(1, 18))
        self.p("PORTALCALC  /  CALCULATION RECORD", "h2")
        self.p("Structural<br/>computations", "h1", markup=True)
        self.p(meta.get("project") or "Portal frame", "h2")
        self.p("Inputs, actions, frame analysis and governing design checks")
        overall = d["overall"]
        converged = all(c["analysis"].get("converged", False) for c in d["cases"])
        status = "WITHIN REPORTED LIMIT" if overall["ratio"] <= 1 + 1e-9 else "CHECKS EXCEED REPORTED LIMIT"
        if not converged:
            status = "ANALYSIS REQUIRES REVIEW"
        color = GREEN if overall["ratio"] <= 1 + 1e-9 and converged else RED
        self.p(f'<font color="{color.hexval()}"><b>{status}</b></font><br/>Governing utilisation {fmt(overall["ratio"])}: {escape(overall["group"])} / {escape(overall["check"])}', markup=True)
        self.table(["Document", "Record"], [
            ["Job reference", meta.get("job_number") or "Not specified"], ["Revision", meta.get("revision") or "-"],
            ["Prepared by", meta.get("prepared_by") or "Not specified"], ["Issued", d["issued_at"]],
            ["Software", f"PortalCalc {d['software_version']}"],
            ["Analysis coverage", f"{len(d['cases'])} cases; {len(d['model']['elements'])} analytical members; {len(d['webs'])} web/post design checks"],
        ], [1, 2])
        self.items.append(FrameFigure(d["model"], height=150))
        self.p("Generated from a fresh analysis of the selected design. The reported status covers the checks implemented by PortalCalc; connection scope and calculation assumptions are stated in this document.", "small")
        self.items.append(PageBreak())
        self.p("Contents", "h1")
        toc = TableOfContents()
        toc.levelStyles = [ParagraphStyle("TOC0", fontName="Calc-Bold", fontSize=10, leading=15, textColor=BLUE, spaceBefore=6),
                           ParagraphStyle("TOC1", fontName="Calc", fontSize=8.5, leading=12, leftIndent=12, textColor=INK)]
        self.items.append(toc)
        self.reading_guide()
        self.inputs()
        self.wind()
        self.other_loads()
        self.analysis()
        self.member_checks()
        self.web_checks()
        self.scope()
        self.case_details()
        self.native_appendix()
        return self.items

    def reading_guide(self):
        self.heading("01 / Calculation basis and result summary")
        self.p("This document records the inputs and outputs of one two-dimensional portal-frame model. Each case has a stable C-number. Member numbers M1, M2... and node numbers N1, N2... refer to the analytical schedules in Section 05.")
        self.p("A governing action is reported with its source member and case. Compression, tension, shear and moment envelopes can govern in different cases. Combined checks that use separate envelopes are identified as envelope interactions, rather than labelled as one coincident load case.")
        self.p("Internal pinned connections may use coincident analytical nodes with shared translations and separate rotations. Analysis web sections are placeholders when automatic web sizing is active; Section 07 identifies the selected design section for each physical web/post.")
        rows = [[r["label"], r["profile"], fmt(r["ratio"]), r["governing"], "OK" if r["ratio"] <= 1 + 1e-9 else "NOT OK"] for r in self.data["members"]]
        for group in sorted({w["group"] for w in self.data["webs"]}):
            w = max((w for w in self.data["webs"] if w["group"] == group), key=lambda r: r["ratio"])
            rows.append([label(group), f"M{w['member']} / {w['profile']}", fmt(w["ratio"]), w["governing"], "OK" if w["ratio"] <= 1 + 1e-9 else "NOT OK"])
        self.table(["Group", "Section / governing member", "Ratio", "Governing check", "Result"], rows, [1.1, 1.8, .55, 1.4, .65], numeric=(2,))
        self.p("Units and signs", "h2")
        self.p("Geometry and displacement: mm unless noted. Applied and reaction forces: kN. Moments: kNm. Positive global X is right, positive global Y is up and positive rotation/moment is anticlockwise. ULS member-action schedules show positive demand magnitudes; signed local nodal end forces are provided in the detailed case appendix. Distributed member loads use N/mm (numerically equal to kN/m).")
        self.p(f"Solved-input identity: {self.data['input_digest']}", "small")

    def inputs(self):
        self.heading("02 / Complete input register")
        self.p("Adopted engineering inputs and selected sections at export. Negative roof loads act downwards. Blank values are marked 'Not specified'; inactive options are retained so the saved design can be reconstructed. Display preferences and visual finishes are excluded.")
        for group, value in self.data["inputs"].items():
            self.heading(label(group), 2)
            self.table(["Input", "Adopted value", "Units"], input_rows(value), [2.3, 2, .6])

    def wind(self):
        self.heading("03 / Wind load calculations")
        self.p("The following directional results use the same wind-input conversion and wind calculator as the frame analysis. Actual applied pressures, enclosure adjustments, connected-roof strips and canopy/free-roof treatment are retained in each case's load-calculation record.")
        for wind in self.data["wind"]:
            inputs = wind["inputs"]
            self.heading(f"{wind['direction_case'].split()[0]} wind / source {inputs['orientation']}", 2)
            self.table(["Parameter", "Adopted / calculated"], [
                ["Region / importance / terrain", f"{inputs['wind_region']} / {inputs['importance_level']} / {inputs['terrain_category']}"],
                ["Mean roof height h", f"{fmt(wind['h_m'])} m"], ["h / building width", fmt(wind["h_over_d"])],
                ["Adopted wind width / eave height / pitch", f"{fmt(inputs['building_width_m'])} m / {fmt(inputs['eave_height_m'])} m / {fmt(inputs['roof_pitch_deg'])} deg"],
                ["Regional speed, ULS / SLS", f"{fmt(wind['vr_ultimate_m_s'])} / {fmt(wind['vr_service_m_s'])} m/s"],
                ["Selected Md / design Md", f"{fmt(wind['direction_multiplier'])} / {fmt(wind['design_direction_multiplier'])}"],
                ["Terrain multiplier Mz,cat", fmt(wind["mz_cat"])],
                ["Vdes = Vr x Md,design x Mz,cat", f"{fmt(wind['vr_ultimate_m_s'])} x {fmt(wind['design_direction_multiplier'])} x {fmt(wind['mz_cat'])} = {fmt(wind['vdes_b_m_s'])} m/s"],
                ["Vdes,SLS", f"{fmt(wind['vdes_sls_m_s'])} m/s"],
                ["q = pressure factor x 0.5 x rho x Vdes^2 / 1000", f"{fmt(inputs['reduction_factor'])} x 0.5 x {fmt(inputs['air_density'])} x {fmt(wind['vdes_b_m_s'])}^2 / 1000 = {fmt(wind['wu_kn_m2'])} kPa"],
                ["SLS reference pressure", f"{fmt(wind['ws_kn_m2'])} kPa"],
                ["Reference Cpi positive / negative", f"{fmt(wind['cpi_max'])} / {fmt(wind['cpi_min'])}"],
                ["Leeward wall Cpe", fmt(wind["leeward_wall_cpe"])],
            ], [2, 2.7])
            continuous = wind.get("continuous_roof_basis", {}).get("inputs", inputs)
            self.p(f"Connected-roof pressure-strip basis: width {fmt(continuous['building_width_m'])} m; eave height {fmt(continuous['eave_height_m'])} m; pitch {fmt(continuous['roof_pitch_deg'])} deg; left/right added slope lengths {fmt(continuous['left_canopy_length_m'])} / {fmt(continuous['right_canopy_length_m'])} m. The solver's equivalent continuous-roof geometry can differ from the main-building inputs in Section 02.", "small")
            self.p("Reference roof pressure zones", "h2")
            self.p("Zone distances are along the roof slope. These reference strip values do not replace the connected-roof, end-wind tributary averaging or free-roof loads recorded for the individual applied case.", "small")
            self.table(["Zone", "From / to (m)", "Cpe + / -", "Pressure + / - (kPa)", "Line load + / - (kN/m)"],
                       [[z["name"], f"{fmt(z['distance_from_eave_from_m'])} / {fmt(z['distance_from_eave_to_m'])}",
                         f"{fmt(z['cpe_max'])} / {fmt(z['cpe_min'])}", f"{fmt(z['pressure_max_kn_m2'])} / {fmt(z['pressure_min_kn_m2'])}",
                         f"{fmt(z['line_load_max_kn_m'])} / {fmt(z['line_load_min_kn_m'])}"] for z in wind["roof_zones"]], [1.4, 1, .8, 1.2, 1.2])
        self.p("Applied case factors and pressure selection", "h2")
        seen, rows = set(), []
        for case in self.data["cases"]:
            combo = case["combination"]
            key = (combo["wind_pressure"], combo["cpe_case"], combo["cpi_case"])
            if not combo["wind_pressure"] or key in seen:
                continue
            seen.add(key)
            selected = [line for line in case["load_lines"] if any(token in line for token in ("Main chamber Cpi", "Applied Kce", "Open-side Cpi rule", "Wind source dir"))]
            rows.append([self.case_ref(case["name"]), f"{combo['wind_pressure']} / {combo['cpe_case']} / {combo['cpi_case']}", "\n".join(selected) or "See applied load record"])
        self.table(["Case", "Wind pattern", "Applied factors / enclosure selection"], rows, [.55, 1.7, 2.8])

    def other_loads(self):
        self.heading("04 / Gravity, imposed and additional loads")
        self.p("Area loads are multiplied by the tributary bay width. The distributed line load is applied over the horizontal projection of each loaded roof segment and resolved into the member's local axes. Permanent components share the G factor; imposed components share the Q factor. Self-weight is included separately from the applied roof area loads.")
        inputs = self.data["inputs"]
        bay = self.data["wind"][0]["inputs"]["bay_size_m"]
        rows = []
        for key in ("g_kpa", "q_kpa", "solar_kpa", "fire_service_kpa", "hvac_kpa", "other_kpa"):
            value = inputs["loads"].get(key)
            if value is not None:
                p = -abs(float(value))
                rows.append([label(key[:-4]), fmt(p), fmt(bay), f"{fmt(p)} x {fmt(bay)} = {fmt(p * bay)}"])
        self.table(["Component", "Area load (kPa)", "Bay (m)", "Unfactored line load (kN/m)"], rows, [1.4, 1, .8, 1.8])
        self.p("Load combinations", "h2")
        self.table(["Case", "Combination / storage pattern", "G factor", "Q factor", "Class"],
                   [[self.case_ref(c["name"]), c["name"], fmt(c["combination"]["g_factor"]), fmt(c["combination"]["q_factor"]),
                     "SLS" if c["name"].startswith("SLS") else "ULS"] for c in self.data["cases"]], [.55, 3.4, .65, .65, .5], numeric=(2, 3))
        self.p("Member self-weight", "h2")
        weight = sum(e["area_mm2"] * 1e-6 * e["length_mm"] * 1e-3 * STEEL_DENSITY_KG_M3 * GRAVITY_M_S2 / 1000 for e in self.data["model"]["elements"])
        self.p(f"The analysis applies member self-weight vertically at the element end nodes. For each member: W = A x L x density x g. Density = {STEEL_DENSITY_KG_M3:,.0f} kg/m3; g = {GRAVITY_M_S2:g} m/s2. Total analysis-model weight = {fmt(weight)} kN before G factors. Automatically selected design webs can differ from the fixed analysis webs; this is the model weight rather than a fabrication weight.")
        for name in ("Storage loads",):
            text = self.data["native_reports"].get(name, "")
            if text.strip():
                self.p(name, "h2")
                self.notes(text.splitlines())
        self.p("Snow, crane and canopy/lean-to load derivations are included in the applied case records when active. The complete raw input register remains in Section 02, including inactive load options.")

    def analysis(self):
        self.heading("05 / Frame model and analysis outputs")
        self.items.append(FrameFigure(self.data["model"], height=180))
        self.p("Solver and model", "h2")
        self.p("The frame uses two-dimensional elastic beam-column elements with three degrees of freedom per node. The stiffness solve uses the selected first- or second-order analysis option. Member distributed loads are retained for force and deflection recovery; they are not treated solely as endpoint forces.")
        self.notes([f"{label(k)}: {fmt(v)}" for k, v in (self.data["analysis"] or {}).items()])
        moduli = sorted({e["E_mpa"] for e in self.data["model"]["elements"]})
        self.p("Analysis elastic modulus E (MPa): " + ", ".join(fmt(v, 0) for v in moduli) + ".")
        self.p("Node coordinates, restraints and shared degrees of freedom", "h2")
        self.p("Repeated global DOF numbers identify shared movements at released joints. Translations can be tied while the rotation remains independent. The case load table reports each global applied-load entry once at its native node.", "small")
        self.table(["Node", "X (mm)", "Y (mm)", "Ux / Uy / Rz DOFs", "Fixed DOFs / springs"],
                   [[f"N{n['id']}", fmt(n["x_mm"], 1), fmt(n["y_mm"], 1), " / ".join(str(v) for v in n["dofs"]),
                     ", ".join(n["restraints"]) + (f"; springs {n['springs']}" if n["springs"] else "") or "Free"] for n in self.data["model"]["nodes"]], [.55, .9, .9, 1.45, 1.4], numeric=(1, 2))
        self.p("Analytical member schedule", "h2")
        self.table(["Member", "Nodes i / j", "Group", "Analysis section", "L (mm)", "A (mm2)", "I (mm4)"],
                   [[f"M{e['id']}", f"N{e['start']} / N{e['end']}", label(e["group"]), e["analysis_profile"], fmt(e["length_mm"], 1), fmt(e["area_mm2"], 1), fmt(e["I_mm4"])] for e in self.data["model"]["elements"]], [.5, .75, .9, 1.3, .7, .7, .85], numeric=(4, 5, 6))
        self.p("ULS action envelopes by analytical member", "h2")
        self.p("Each value is a demand magnitude; the case beneath it identifies its source. Chord design averaging, internal-column eccentricity and design-only doublers are applied separately in the section-check chapter.", "small")
        envelopes = {}
        for case in self.data["cases"]:
            if case["name"].startswith("SLS"):
                continue
            for member in case["members"]:
                target = envelopes.setdefault(member["member"], {})
                for key in ("compression_kn", "tension_kn", "shear_kn", "moment_knm"):
                    if key not in target or member[key] > target[key][0]:
                        target[key] = (member[key], self.case_ref(case["name"]))
        self.table(["Member", "Nc (kN)", "Nt (kN)", "V (kN)", "M (kNm)"],
                   [[f"M{member}"] + [f"{fmt(values[key][0])}\n{values[key][1]}" for key in ("compression_kn", "tension_kn", "shear_kn", "moment_knm")]
                    for member, values in sorted(envelopes.items())], [.7, 1.1, 1.1, 1.1, 1.1], numeric=(1, 2, 3, 4))
        self.p("Support reaction envelopes", "h2")
        self.table(["Support", "Compression / case (kN)", "Uplift / case (kN)", "Shear / case (kN)", "Moment / case (kNm)"],
                   [[f"{r['label']} / N{r['node_id']}"] + [f"{fmt(r[key])}\n{self.case_ref(r[combo])}" for key, combo in (("compression_kn", "compression_combo"), ("uplift_kn", "uplift_combo"), ("shear_kn", "shear_combo"), ("moment_knm", "moment_combo"))]
                    for r in self.data["envelopes"].get("foundation_reactions", [])], [1.1, 1.1, 1.1, 1.1, 1.1])
        self.p("Foundation compression is an SLS envelope; uplift, shear and moment are ULS envelopes. They are separate maxima and must not be read as simultaneous reactions from one case.", "small")
        self.p("Governing serviceability records", "h2")
        rows = []
        def walk(value, path):
            if not isinstance(value, dict):
                return
            if "value" in value and "combo" in value and value["value"] >= 0:
                rows.append([label(path), fmt(value["value"]), self.case_ref(value["combo"]), f"N{value['node']}" if "node" in value else f"M{value.get('member', '-')}"])
            else:
                for key, item in value.items():
                    walk(item, f"{path} / {key}")
        for key, value in self.data["envelopes"].items():
            if key.startswith("sls_"):
                walk(value, key)
        self.table(["Deflection record", "Demand (mm)", "Case", "Location"], rows, [3.2, .85, .6, .9])
        self.p("Allowable limits, ratios and any required restraint/flybracing notes are reproduced in the detailed design-check record in Appendix B.", "small")

    def properties(self, record):
        p, a = record["properties"], record["actions"]
        self.table(["Property", "Value", "Property", "Value"], [
            ["Area (mm2)", fmt(p["area_mm2"]), "fy / fu (MPa)", f"{fmt(p['fy_mpa'])} / {fmt(p['fu_mpa'])}"],
            ["Iy / Iz (mm4)", f"{fmt(p['iy_mm4'])} / {fmt(p['iz_mm4'])}", "J / Iw (mm4 / mm6)", f"{fmt(p['j_mm4'])} / {fmt(p['iw_mm6'])}"],
            ["Sy / Sz (mm3)", f"{fmt(p['sy_mm3'])} / {fmt(p['sz_mm3'])}", "Depth / width (mm)", f"{fmt(p['depth_mm'])} / {fmt(p['flange_width_mm'])}"],
            ["Flange / web thickness (mm)", f"{fmt(p['flange_thickness_mm'])} / {fmt(p['web_thickness_mm'])}", "Effective lengths y / z (mm)", f"{fmt(a['effective_length_y_mm'])} / {fmt(a['effective_length_z_mm'])}"],
            ["kf / alpha_b", f"{fmt(p['kf'])} / {fmt(p['alpha_b'])}", "am; kt / kl / kr", f"{fmt(a['moment_modifier'])}; {fmt(a['kt'])} / {fmt(a['kl'])} / {fmt(a['kr'])}"],
            ["Adopted net area An (mm2)", fmt(p["area_mm2"] if p["net_area_mm2"] is None else p["net_area_mm2"]), "fy,web (MPa) / tension kt", f"{fmt(p['fyw_mpa'] or p['fy_mpa'])} / {fmt(p['kt'])}"],
        ], [1.3, 1.2, 1.5, 1.3])

    def check_table(self, checks):
        self.table(["Check", "Demand", "Capacity", "Units", "Ratio", "Source"],
                   [[c["label"], fmt(c["demand"]), fmt(c["capacity"]), c["unit"],
                     f"{fmt(c['ratio'])}\n{'OK' if c['ratio'] <= 1 + 1e-9 else 'NOT OK'}", self.source(c["source"])] for c in checks],
                   [1.2, .8, .8, .5, .7, 1.65], numeric=(1, 2, 4))
        for c in checks:
            if c.get("notes"):
                self.p(f"{c['label']}: {c['notes']}", "small")

    def working(self, record):
        self.table(["Calculation step", "Expression / adopted values", "Result", "Units"],
                   [[name, formula, fmt(value), unit] for name, formula, value, unit in record["working"]], [1.4, 2.5, .8, .5], numeric=(2,))

    def member_checks(self):
        self.heading("06 / Governing section checks")
        self.p("Worked values below call the same capacity functions as the app. The maximum of axial and bending interaction is checked with shear separately. The governing report output includes any additional chord-doubler length/zone or web-joint requirement, even where the basic section table itself passes.")
        for record in self.data["members"]:
            self.heading(f"{record['label']} / {record['profile']}", 2)
            self.p(f"Governing result: {record['governing']} | utilisation {fmt(record['ratio'])} | {'OK' if record['ratio'] <= 1 + 1e-9 else 'NOT OK'}")
            self.properties(record)
            for note in record["notes"]:
                self.p(note)
            if record["group"] == "INTERNAL_COLUMN":
                self.p("The internal-column bending envelope includes eccentricity M = Nc x D/2, as implemented by the frame checks.", "small")
            self.p("Demand, capacity and governing source", "h2")
            self.check_table(record["checks"])
            self.p("Worked capacity values", "h2")
            self.working(record)
        zones = self.data["envelopes"].get("doubled_chord_zones", [])
        if zones:
            self.p("Doubled-chord design allowances", "h2")
            self.p("Doubler zones are design/fabrication allowances; the analysis stiffness and force path are not silently upgraded. The full zone/length checks and any additional flybracing are retained in Appendix B.")
            self.table(["Group / member", "Length (mm)", "Case", "Single ratio / check", "Doubled ratio / check"],
                       [[f"{label(z['group'])} / M{z['member']}", fmt(z["length_mm"], 1), self.case_ref(z["combo"]),
                         f"{fmt(z['single_ratio'])} / {z['single_check']}", f"{fmt(z['doubled_ratio'])} / {z['doubled_check']}"] for z in zones],
                       [1.4, .8, .6, 1.4, 1.4], numeric=(1,))

    def web_checks(self):
        self.heading("07 / Web members and connection checks")
        self.p("Every web/post below uses its selected design section, independent compression and tension envelopes, actual member length and the stored joint context. The detailed governing checks are organised by web group and selected size so repeated workings are kept together.")
        webs = self.data["webs"]
        self.table(["Member", "Design section", "L (mm)", "Nc / source (kN)", "Nt / source (kN)", "Governing / ratio"],
                   [[f"M{w['member']}", w["profile"], fmt(w["length_mm"], 1), f"{fmt(w['compression_kn'])}\n{self.case_ref(w['compression_combo'])}",
                     f"{fmt(w['tension_kn'])}\n{self.case_ref(w['tension_combo'])}", f"{w['governing']}\n{fmt(w['ratio'])} {'OK' if w['ratio'] <= 1 + 1e-9 else 'NOT OK'}"] for w in webs], [.55, 1.4, .65, .95, .95, 1.2])
        buckets = defaultdict(list)
        for web in webs:
            buckets[(web["group"], web["profile"])].append(web)
        for (group, profile), records in buckets.items():
            self.heading(f"{label(group)} / {profile} / {len(records)} members", 2)
            governing = max(records, key=lambda r: max(c["ratio"] for c in r["checks"]))
            self.p(f"Worked member-capacity example: M{governing['member']}, length {fmt(governing['length_mm'], 1)} mm. Each check row independently identifies the governing member and case across this size group.")
            self.properties(governing)
            check_keys = list(governing["checks"])
            checks = []
            for template in check_keys:
                checks.append(max((c for w in records for c in w["checks"] if c["key"] == template["key"]), key=lambda c: c["ratio"]))
            self.check_table(checks)
            self.working(governing)
            connection_buckets = defaultdict(list)
            for w in records:
                for check in w["connections"]:
                    connection_buckets[(check["chord"], check["label"])].append((w, check))
            self.p("Governing connection screens", "h2")
            rows, notes = [], []
            for key, checks in connection_buckets.items():
                web, check = max(checks, key=lambda pair: pair[1]["ratio"])
                rows.append([check["chord"], check["label"], f"M{web['member']} / {self.case_ref(check['combo'])}", fmt(check["demand"]),
                             fmt(check["capacity"]), check["unit"], fmt(check["ratio"])])
                notes.append(f"{check['chord']} / {check['label']} / M{web['member']}: {check.get('notes', '')}")
                notes.extend(check.get("working", []))
            self.table(["Chord", "Check", "Source", "Demand", "Capacity", "Units", "Ratio"], rows, [.8, 1.2, 1.1, .7, .7, .45, .6], numeric=(3, 4, 6))
            for note in notes:
                self.p(note, "small")
            warnings = sorted({(w["member"], c["chord"], c["notes"], self.case_ref(c["combo"]))
                               for w in records for c in w["connections"]
                               if "warning" in c["label"].lower()})
            if warnings:
                self.p("Joint validity messages for all affected members", "h2")
                self.table(["Member / case", "Chord", "Validity message"],
                           [[f"M{member} / {case}", chord, notes] for member, chord, notes, case in warnings], [1, .8, 3.5])
        self.p("Connection design scope", "h2")
        self.p("Implemented checks include the assumed continuous fillet weld, branch/web wall yield and fracture, chord local wall shear, punching, face yielding and the implemented K-joint screen/validity messages. These are the current connection checks, not a completed design of every connection. Base plates, anchors, eave/apex connections, splices and other unimplemented details are not represented as checked.")

    def scope(self):
        self.heading("08 / Secondary checks and calculation scope")
        self.p("The detailed design-check appendix contains the app's full purlin, side/end-wall girt, end-wall mullion, bracing, crane and serviceability output where applicable, including the adopted restraint assumptions and implementation notes. Inactive systems are identified through the input register and the calculation output.")
        self.p("Capacity equations recorded in this report", "h2")
        self.table(["Check", "Production calculation basis"], [
            ["Compression", "phi Nc is the section compression capacity reduced by the governing y/z global-buckling factor. The hollow-wall local capacity is checked separately when applicable."],
            ["Tension", "phi Nt = phi x min(Ag x fy, kt x An x fu) / 1000."],
            ["Bending", "phi Ms = phi x fy x S / 10^6; phi Mb = min(phi Ms, am x alpha_s x phi Ms). Mo uses the effective length and the section's minor-axis, torsion and warping properties."],
            ["Shear", "The implemented section-type shear area and web-slenderness reduction feed the production design shear capacity. See the notes beside the returned check."],
            ["Interaction", "max(Nc*/phi Nc + M*/phi Mb, Nt*/phi Nt + M*/phi Mb). Shear is checked separately."],
        ], [1, 4])
        self.p("Review the implementation notes carried with the capacity and joint checks. The current model uses preliminary AS 4100-style member checks and a calibrated K-joint screen; compactness refinement and items outside the stated scope remain matters for engineering review. The report records implemented computations and does not turn an unimplemented check into a pass.")
        if self.data["native_reports"].get("Foundations"):
            self.p("Foundation calculation output", "h2")
            self.notes(self.data["native_reports"]["Foundations"].splitlines())

    def governing_names(self):
        found = set()
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if (key == "combo" or str(key).endswith("_combo")) and isinstance(item, str) and item in self.case_ids:
                        found.add(item)
                    else:
                        walk(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)
        walk(self.data["envelopes"])
        walk(self.data["webs"])
        return found

    def case_details(self):
        self.heading("Appendix A / Governing-case analysis records")
        names = self.governing_names()
        self.p(f"{len(names)} cases govern at least one retained action, serviceability, foundation or web/joint record. The following tables are actual case results. Cases are listed in the same order as the combination register; values from different cases are not combined here.")
        self.p("This export includes every nodal and member result for the governing cases." if self.detailed else
               "This export includes governing-case diagrams, load workings, signed support reactions and case extrema. The complete nodal/member tables for every governing case can be included using the large-appendix option when exporting.")
        for case in self.data["cases"]:
            if case["name"] not in names:
                continue
            self.heading(f"{self.case_ref(case['name'])} / {case['name']}", 2, self.case_anchors[case["name"]])
            self.p("Analysis record: " + "; ".join(f"{label(key)} = {fmt(value)}" for key, value in case["analysis"].items()), "small")
            if any(r.get("diagram") for r in case["members"]):
                self.items.append(FrameFigure(self.data["model"], case, "loads", height=155))
                self.items.append(FrameFigure(self.data["model"], case, "moment", height=170))
            self.p("Applied load calculations", "h2")
            self.notes(case["load_lines"])
            self.p("Self-weight is added to the final case force vector using its G factor. The load-calculation text for first-order matrix cases lists the other loads before this separate self-weight addition.", "small")
            self.p("Support reactions - signed global axes", "h2")
            self.table(["Node", "Rx (kN)", "Ry (kN)", "Mz (kNm)"], [[f"N{r['node']}", fmt(r["rx_kn"]), fmt(r["ry_kn"]), fmt(r["mz_knm"])] for r in case["reactions"]], [.6, 1, 1, 1], numeric=(1, 2, 3))
            if not self.detailed:
                self.p("Case extrema", "h2")
                rows = []
                for key, title, unit in (("ux_mm", "Maximum absolute horizontal displacement", "mm"), ("uy_mm", "Maximum absolute vertical displacement", "mm")):
                    item = max(case["nodes"], key=lambda n: abs(n[key]))
                    rows.append([title, fmt(item[key]), unit, f"N{item['id']}"])
                for key, title, unit in (("compression_kn", "Maximum compression", "kN"), ("tension_kn", "Maximum tension", "kN"), ("shear_kn", "Maximum shear magnitude", "kN"), ("moment_knm", "Maximum moment magnitude", "kNm")):
                    item = max(case["members"], key=lambda n: n[key])
                    rows.append([title, fmt(item[key]), unit, f"M{item['member']}"])
                self.table(["Output", "Result", "Units", "Location"], rows, [2.8, 1, .6, .7], numeric=(1,))
                continue
            self.p("Nodal displacement and applied-load schedule", "h2")
            self.table(["Node", "Ux (mm)", "Uy (mm)", "Rz (rad)", "Fx (kN)", "Fy (kN)", "Mz (kNm)"],
                       [[f"N{n['id']}", fmt(n["ux_mm"]), fmt(n["uy_mm"]), fmt(n["rz_rad"], 6), fmt(n["fx_kn"]), fmt(n["fy_kn"]), fmt(n["mz_knm"])] for n in case["nodes"]], [.5, .75, .75, .9, .75, .75, .8], numeric=(1, 2, 3, 4, 5, 6))
            self.p("Member action magnitudes", "h2")
            self.table(["Member", "Nc (kN)", "Nt (kN)", "V (kN)", "M (kNm)"], [[f"M{m['member']}"] + [fmt(m[k]) for k in ("compression_kn", "tension_kn", "shear_kn", "moment_knm")] for m in case["members"]], [.6, 1, 1, 1, 1], numeric=(1, 2, 3, 4))
            self.p("Local nodal end forces", "h2")
            self.p("Vector k_local x d_local minus the equivalent local member loads, in i/j element order. These end-force signs differ from the section-cut sign convention used for the bending diagram.", "small")
            self.table(["Member", "Ni (kN)", "Vi (kN)", "Mi (kNm)", "Nj (kN)", "Vj (kN)", "Mj (kNm)"],
                       [[f"M{m['member']}"] + [fmt(float(v) / (1e6 if i in (2, 5) else 1000)) for i, v in enumerate(m["end_forces"])] for m in case["members"]], [.5, .8, .8, .8, .8, .8, .8], numeric=(1, 2, 3, 4, 5, 6))
            if case["member_loads"]:
                self.p("Distributed member loads - local axes", "h2")
                self.p("Two components mean uniform qx, qy. Four components mean qx,i, qy,i, qx,j, qy,j for a linearly varying load. Units: N/mm, equal numerically to kN/m.", "small")
                self.table(["Member", "Local load components"], [[f"M{r['member']}", ", ".join(fmt(v) for v in r["local_load"])] for r in case["member_loads"]], [.7, 4.5])

    def native_appendix(self):
        self.heading("Appendix B / Detailed design-check record")
        self.p("Complete design-check output from the solved app, retained to show the detailed serviceability limits, web-size buckets, chord doublers, secondary steel checks, assumptions and qualifications behind the structured chapters.")
        self.notes(self.data["native_reports"].get("Section checks", "").splitlines())


def export_computation_pdf(data, path, *, include_case_details=False):
    """Build atomically so a layout failure cannot replace a previous report."""
    fonts()
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".portalcalc-computations-", suffix=".pdf", dir=path.parent)
    os.close(handle)
    try:
        report = Report(data, include_case_details)
        ComputationDoc(temporary, data).multiBuild(report.make())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path)
