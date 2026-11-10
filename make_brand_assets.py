"""Generate PortalCalc brand assets in the BOLT visual system.

Letterforms follow the BOLT wordmark construction (22-unit strokes on a
100-unit cap height, 12-unit gaps) with the BOLT hexagon standing in for the
"O". The hexagon carries the BOLT lightning bolt striking through a portal
frame (mark "strike_pitch"). Tagline "BOLT STRUCTURAL ANALYSIS" is outlined
Montserrat SemiBold.

Usage: python make_brand_assets.py assets                  # final set
       python make_brand_assets.py <dir> options           # first-round comparison sheets
       python make_brand_assets.py <dir> strike_options    # bolt-through-frame variations
       python make_brand_assets.py <dir> big <mark> ...    # large icon previews
"""
import math
import os
import sys

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QFont, QGuiApplication, QImage, QPainter, QPainterPath, QTransform
from PySide6.QtSvg import QSvgRenderer

NAVY = "#13377E"
TEAL = "#00B5AF"
WHITE = "#FFFFFF"
SW = 22  # BOLT stroke weight (cap height 100)
GAP = 12  # BOLT inter-letter gap

OUT = sys.argv[1]
VARIANT = sys.argv[2] if len(sys.argv) > 2 else "final"
os.makedirs(OUT, exist_ok=True)

app = QGuiApplication([sys.argv[0], "-platform", "offscreen"])
from PySide6.QtGui import QFontDatabase  # noqa: E402

_fid = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\Montserrat-SemiBold.ttf")
TAG_FAMILY = QFontDatabase.applicationFontFamilies(_fid)[0]


def stroke(d, color):
    return (f'<path d="{d}" stroke="{color}" fill="none" stroke-width="{SW}" '
            f'stroke-linejoin="miter" stroke-linecap="butt"/>')


def poly(points, color):
    pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    return f'<polygon points="{pts}" fill="{color}"/>'


# ---------------------------------------------------------------- hexagon mark
HAUNCH = False
HEX_W = 91.8
HEX_TOP, HEX_BOT = -3.0, 103.0


def hex_points(cx, cy, r):
    return [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
            for a in (-90, -30, 30, 90, 150, 210)]


def frame_glyph(cx, cy, scale, weight):
    """White portal frame (columns, haunched knees, pitched rafters) in a hex of radius 53*scale."""
    s = scale
    half_span = 27 * s
    base = cy + 26 * s
    eave = cy - 9 * s
    apex = cy - 18 * s  # ~18 deg roof: reads as a shed portal, not a house
    xl, xr = cx - half_span, cx + half_span
    d = (f"M{xl:.2f},{base:.2f} V{eave:.2f} L{cx:.2f},{apex:.2f} "
         f"L{xr:.2f},{eave:.2f} V{base:.2f}")
    sw = weight * s
    parts = [f'<path d="{d}" stroke="{WHITE}" fill="none" stroke-width="{sw:.2f}" '
             f'stroke-linejoin="miter" stroke-miterlimit="10" stroke-linecap="butt"/>']
    if HAUNCH and weight <= 11:  # haunches only where there is room for them
        h = 9 * s
        rise = (eave - apex) / half_span
        for side in (-1, 1):
            kx = cx + side * (half_span - sw / 2)  # inner face of the column
            ky = eave + 1.5 * s
            tri = [(kx, ky), (kx, ky + h), (kx - side * h, ky - h * rise)]
            parts.append(poly(tri, WHITE))
    return "".join(parts)


# BOLT lightning bolt, taken from BOLT_icon.svg, as offsets from the hex centre (hex radius 53).
BOLT_OFFSETS = [(6.24, -35.88), (-20.28, 6.24), (-1.56, 6.24), (-7.8, 35.88), (20.28, -7.8), (1.56, -7.8)]

# Hexagon contents: "frame" (portal frame only), "bolt" (exact BOLT mark),
# "frame_bolt" (bolt sheltered inside the frame), "strike" (full bolt striking
# through the frame), "roof_bolt" (roof line over the bolt).
MARK = "strike_pitch"  # adopted 2026-11-10


def bolt_glyph(cx, cy, scale, halo=0.0, fill=WHITE, halo_color=TEAL):
    pts = [(cx + dx * scale, cy + dy * scale) for dx, dy in BOLT_OFFSETS]
    s = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
    out = ""
    if halo:
        out += (f'<polygon points="{s}" fill="{halo_color}" stroke="{halo_color}" '
                f'stroke-width="{halo:.2f}" stroke-linejoin="miter"/>')
    return out + f'<polygon points="{s}" fill="{fill}"/>'


def roof_glyph(cx, cy, s, weight):
    d = f"M{cx-31*s:.2f},{cy-12*s:.2f} L{cx:.2f},{cy-27*s:.2f} L{cx+31*s:.2f},{cy-12*s:.2f}"
    return (f'<path d="{d}" stroke="{WHITE}" fill="none" stroke-width="{weight*s:.2f}" '
            f'stroke-linejoin="miter" stroke-miterlimit="10" stroke-linecap="butt"/>')


def line(d, color, width):
    return (f'<path d="{d}" stroke="{color}" fill="none" stroke-width="{width:.2f}" '
            f'stroke-linejoin="miter" stroke-miterlimit="10" stroke-linecap="butt"/>')


def pitched_structure(cx, cy, s, weight, truss=None, color=WHITE):
    """Portal frame at the option-4 roof pitch (15 in 31, about 26 deg), optionally trussed.

    truss=None: plain rafters. "parallel": pitched parallel-chord rafters with
    Warren webs (as PortalCalc's truss frames). "roof": triangular roof truss
    with a horizontal bottom chord at the eaves and Fink webs.
    """
    if truss is None:
        half, apex, base = 27 * s, cy - 24 * s, cy + 26 * s
    else:  # larger, lighter structure so the truss can be read around the bolt
        half, apex, base = 33 * s, cy - 27 * s, cy + 28 * s
    eave = apex + half * 15 / 31
    xl, xr = cx - half, cx + half
    w = weight * s
    out = line(f"M{xl:.2f},{base:.2f} V{eave:.2f} L{cx:.2f},{apex:.2f} L{xr:.2f},{eave:.2f} V{base:.2f}",
               color, w)
    chord, web = w * 0.75, w * 0.5
    if truss == "parallel":
        depth = 13 * s
        out += line(f"M{xl:.2f},{eave+depth:.2f} L{cx:.2f},{apex+depth:.2f} L{xr:.2f},{eave+depth:.2f}",
                    color, chord)
        panels = 4  # web legs per side
        for side in (-1, 1):
            pts = []
            for i in range(panels + 1):
                t = i / panels
                x = cx + side * half * (1 - t)
                y_top = eave + (apex - eave) * t
                pts.append((x, y_top + (depth if i % 2 == 0 else 0)))
            d = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts)
            out += line(d, color, web)
    elif truss == "roof":
        out += line(f"M{xl:.2f},{eave:.2f} H{xr:.2f}", color, chord)
        mid_y = (eave + apex) / 2
        for side in (-1, 1):
            bx = cx + side * half / 3
            mx = cx + side * half / 2
            out += line(f"M{cx:.2f},{apex:.2f} L{bx:.2f},{eave:.2f} L{mx:.2f},{mid_y:.2f}", color, web)
    return out


def hex_mark(cx, cy, r, weight=10.0, fill=TEAL, mark=None):
    mark = mark or MARK
    s = r / 53.0
    out = poly(hex_points(cx, cy, r), fill)
    if mark == "frame":
        out += frame_glyph(cx, cy, s, weight)
    elif mark == "bolt":
        out += bolt_glyph(cx, cy, s)
    elif mark == "frame_bolt":
        out += frame_glyph(cx, cy, s, weight * 0.8)
        out += bolt_glyph(cx, cy + 6 * s, s * 0.6)
    elif mark == "strike":
        out += frame_glyph(cx, cy, s, weight)
        out += bolt_glyph(cx, cy + 2 * s, s * 0.95, halo=7 * s, halo_color=fill)
    elif mark in ("strike_pitch", "strike_truss", "strike_roof_truss", "strike_truss_navy"):
        truss = {"strike_pitch": None, "strike_truss": "parallel",
                 "strike_roof_truss": "roof", "strike_truss_navy": "parallel"}[mark]
        color = NAVY if mark == "strike_truss_navy" else WHITE
        if truss is None:
            out += pitched_structure(cx, cy, s, weight, truss, color)
            out += bolt_glyph(cx, cy + 2 * s, s * 0.95, halo=7 * s, halo_color=fill)
        else:
            out += pitched_structure(cx, cy, s, weight * 0.6, truss, color)
            out += bolt_glyph(cx, cy + 3 * s, s * 0.85, halo=5 * s, halo_color=fill)
    elif mark == "roof_bolt":
        out += roof_glyph(cx, cy, s, weight)
        out += bolt_glyph(cx, cy + 9 * s, s * 0.7)
    else:
        raise ValueError(mark)
    return out


# ---------------------------------------------------------------- letters
def glyph_P(x, c):
    return stroke(f"M{x+11},0 V100 M{x},11 H{x+38} a22.5,22.5 0 0 1 0,45 H{x}", c), 71.5


def glyph_R(x, c):
    body, _ = glyph_P(x, c)
    leg = poly([(x + 33, 56), (x + 57.6, 56), (x + 79.6, 100), (x + 55, 100)], c)
    return body + leg, 79.6


def glyph_T(x, c):
    return stroke(f"M{x},11 H{x+68} M{x+34},11 V100", c), 68


def glyph_A(x, c):
    outer = [(x, 100), (x + 28, 0), (x + 52, 0), (x + 80, 100)]
    k = 0.28  # run per unit height of the leg edges
    t = 34.0 / (2 * k)  # height above baseline where inner edges meet
    apex_y = 100 - t
    bar_top, bar_bot = 62.0, 82.0

    def li(y):
        return x + 23 + k * (100 - y)

    def ri(y):
        return x + 57 - k * (100 - y)

    counter = [(x + 40, apex_y), (ri(bar_top), bar_top), (li(bar_top), bar_top)]
    foot = [(li(bar_bot), bar_bot), (ri(bar_bot), bar_bot), (ri(100), 100.5), (li(100), 100.5)]
    # draw outer then knock out in background colour via mask
    mid = f"A{x:.0f}"
    mask = (f'<mask id="m{mid}" maskUnits="userSpaceOnUse" x="{x-2}" y="-2" width="90" height="106">'
            f'<rect x="{x-2}" y="-2" width="90" height="106" fill="#fff"/>'
            f'{poly(counter, "#000")}{poly(foot, "#000")}</mask>')
    pts = " ".join(f"{px:.2f},{py:.2f}" for px, py in outer)
    return mask + f'<polygon points="{pts}" fill="{c}" mask="url(#m{mid})"/>', 80


def glyph_L(x, c):
    return stroke(f"M{x+11},0 V89 H{x+60}", c), 60


def glyph_C(x, c):
    return stroke(f"M{x+70},11 H{x+50} A39,39 0 0 0 {x+50},89 H{x+70}", c), 70


GLYPHS = {"P": glyph_P, "R": glyph_R, "T": glyph_T, "A": glyph_A, "L": glyph_L, "C": glyph_C}


def wordmark(x0, letter_color, calc_color, word_gap, hex_fill=TEAL, hex_weight=10.0):
    parts, x = [], x0
    seq = ["P", "HEX", "R", "T", "A", "L", "|", "C", "A", "L", "C"]
    calc = False
    for i, g in enumerate(seq):
        if g == "|":
            x += word_gap - GAP
            calc = True
            continue
        if g == "HEX":
            parts.append(hex_mark(x + HEX_W / 2, 50, 53, hex_weight, hex_fill))
            x += HEX_W + GAP
            continue
        svg, w = GLYPHS[g](x, calc_color if calc else letter_color)
        parts.append(svg)
        x += w + GAP
    return "".join(parts), x - GAP


# ---------------------------------------------------------------- outlined text
def text_path(text, family, weight, px, target_width=None, spacing=0.0):
    font = QFont(family)
    font.setWeight(weight)
    font.setPixelSize(int(px * 10))
    font.setHintingPreference(QFont.PreferNoHinting)

    def build(sp):
        font.setLetterSpacing(QFont.AbsoluteSpacing, sp * 10)
        p = QPainterPath()
        p.addText(0, 0, font, text)
        return QTransform.fromScale(0.1, 0.1).map(p)

    path = build(spacing)
    if target_width:
        for _ in range(4):
            w = path.boundingRect().width()
            spacing += (target_width - w) / (len(text) - 1)
            path = build(spacing)
    return path


def path_to_d(path, dx, dy):
    out, i, n = [], 0, path.elementCount()
    while i < n:
        e = path.elementAt(i)
        if e.type == QPainterPath.MoveToElement:
            out.append(f"M{e.x+dx:.2f} {e.y+dy:.2f}")
            i += 1
        elif e.type == QPainterPath.LineToElement:
            out.append(f"L{e.x+dx:.2f} {e.y+dy:.2f}")
            i += 1
        else:
            c1, c2 = path.elementAt(i + 1), path.elementAt(i + 2)
            out.append(f"C{e.x+dx:.2f} {e.y+dy:.2f} {c1.x+dx:.2f} {c1.y+dy:.2f} {c2.x+dx:.2f} {c2.y+dy:.2f}")
            i += 3
    return "".join(out) + "Z"


TAGLINE = "BOLT STRUCTURAL ANALYSIS"
TAG_MODE = "left"  # "left": fixed tracking (adopted); "full": tracked to the wordmark width as BOLT does


def logo_svg(letter, calc, tag_color, word_gap, tag_mode=None):
    tag_mode = tag_mode or TAG_MODE
    pad = 10
    body, right = wordmark(pad, letter, calc, word_gap)
    width = right - pad
    if tag_mode == "full":
        px = 30.0
        tp = text_path(TAGLINE, TAG_FAMILY, QFont.DemiBold, px, target_width=width - 2, spacing=px * 0.3)
    else:
        px = 24.0
        tp = text_path(TAGLINE, TAG_FAMILY, QFont.DemiBold, px, spacing=px * 0.42)
    br = tp.boundingRect()
    baseline = 118 + br.height()
    tag = f'<path fill="{tag_color}" d="{path_to_d(tp, pad + 1 - br.left(), baseline)}"/>'
    vb_w, vb_h = right + pad, baseline + 26
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb_w:.1f} {vb_h}" '
            f'width="{vb_w*8:.0f}" height="{vb_h*8:.0f}"><title>PortalCalc</title>'
            f'<g transform="translate(0,10)">{body}{tag}</g></svg>'), vb_w / vb_h


def icon_svg(weight):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 106 106" width="256" height="256">'
            f'<title>PortalCalc icon</title>{hex_mark(53, 53, 53, weight)}</svg>')


def render(svg, path, width, height):
    r = QSvgRenderer(QByteArray(svg.encode()))
    img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, width, height))
    p.end()
    img.save(path)


def write(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(text)


def svg_image(svg, width, height):
    r = QSvgRenderer(QByteArray(svg.encode()))
    img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p, QRectF(0, 0, width, height))
    p.end()
    return img


def comparison_sheet(path, rows, tag_mode):
    """One row per mark: logo, 128 px icon, then 32 and 16 px icons at true size and x4."""
    from PySide6.QtGui import QColor
    row_h, logo_w, left = 260, 1000, 70
    sheet = QImage(1900, 60 + row_h * len(rows), QImage.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#ffffff"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.Antialiasing)
    label_font = QFont(TAG_FAMILY)
    label_font.setPixelSize(30)
    small_font = QFont(TAG_FAMILY)
    small_font.setPixelSize(16)
    for i, (key, title) in enumerate(rows):
        global MARK
        MARK = key
        top = 40 + i * row_h
        if i:
            p.setPen(QColor("#d5dcea"))
            p.drawLine(30, top - 20, sheet.width() - 30, top - 20)
        p.setPen(QColor(NAVY))
        p.setFont(label_font)
        p.drawText(20, top + 34, chr(ord("A") + i))
        p.setFont(small_font)
        p.drawText(left, top + 228, title)
        svg, ratio = logo_svg(NAVY, TEAL, NAVY, GAP, tag_mode)
        p.drawImage(left, top, svg_image(svg, logo_w, round(logo_w / ratio)))
        x = left + logo_w + 60
        p.drawImage(x, top + 10, svg_image(icon_svg(10), 160, 160))
        x += 200
        for size in (32, 16):
            small = svg_image(icon_svg(14), size, size)
            p.drawImage(x, top + 40, small)
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            p.drawImage(QRectF(x + size + 16, top + 10, size * 4, size * 4), small)
            p.setPen(QColor("#4d5b78"))
            p.drawText(x, top + 10 + size * 4 + 26, f"{size} px  (and x4)")
            x += size * 5 + 50
    p.end()
    sheet.save(path)


if VARIANT == "options":
    rows = [
        ("bolt", "BOLT bolt only (icon would match BOLT's)"),
        ("frame_bolt", "Portal frame with the bolt inside"),
        ("strike", "Bolt striking through the frame"),
        ("roof_bolt", "Roof line over the bolt"),
        ("frame", "Portal frame only (current)"),
    ]
    for mode in ("full", "left"):
        comparison_sheet(os.path.join(OUT, f"logo_options_tagline_{mode}.png"), rows, mode)
    sys.exit(0)

if VARIANT == "big":  # large icon previews: make_brand_assets.py <dir> big mark1 mark2 ...
    from PySide6.QtGui import QColor
    marks = sys.argv[3:]
    strip = QImage(len(marks) * 420, 420, QImage.Format_ARGB32_Premultiplied)
    strip.fill(QColor("#ffffff"))
    painter = QPainter(strip)
    for i, m in enumerate(marks):
        MARK = m
        painter.drawImage(i * 420 + 10, 10, svg_image(icon_svg(10), 400, 400))
    painter.end()
    strip.save(os.path.join(OUT, "big_" + "_".join(marks) + ".png"))
    sys.exit(0)

if VARIANT == "strike_options":
    rows = [
        ("strike", "Option 3 as before (shallower frame)"),
        ("strike_pitch", "Frame at the option 4 roof pitch"),
        ("strike_truss", "Option 4 pitch with trussed rafters (parallel chords, Warren webs)"),
        ("strike_roof_truss", "Option 4 pitch with a roof truss (eave tie + Fink webs)"),
        ("strike_truss_navy", "Trussed rafters with the structure in navy, bolt in white"),
    ]
    comparison_sheet(os.path.join(OUT, "logo_strike_variations.png"), rows, "left")
    sys.exit(0)

# ---------------------------------------------------------------- final set
LETTER, CALC, WGAP = NAVY, TEAL, GAP
svg, ratio = logo_svg(LETTER, CALC, NAVY, WGAP)
write("portalcalc_logo.svg", svg)
render(svg, os.path.join(OUT, "portalcalc_logo.png"), 1200, round(1200 / ratio))
svg_w, _ = logo_svg(WHITE, TEAL, WHITE, WGAP)
write("portalcalc_logo_white.svg", svg_w)
render(svg_w, os.path.join(OUT, "portalcalc_logo_white.png"), 1200, round(1200 / ratio))

write("portalcalc_icon.svg", icon_svg(10))
for s in (16, 24, 32, 48, 64, 128, 256):
    weight = 14 if s <= 32 else 10
    render(icon_svg(weight), os.path.join(OUT, f"portalcalc_icon_{s}.png"), s, s)

from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402


def derive_csb_logos(src, out_dir, width=640):
    """Transparent CSB logo PNGs from the white-background JPG.

    csb_logo.png keeps the brand colours; csb_logo_white.png turns the navy
    parts white (teal kept) for dark backgrounds.
    """
    im = Image.open(src).convert("RGB")
    im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    ink = 255.0 - np.asarray(im).astype(np.float64)  # distance from white
    navy = np.array([0x13, 0x37, 0x7E], float)
    teal = np.array([0x00, 0xB5, 0xAF], float)
    # Each pixel is white blended with one brand colour: pick the colour by
    # direction, then alpha = coverage of that colour (solid areas reach 1).
    ink_n, ink_t = 255.0 - navy, 255.0 - teal
    cos_n = ink @ ink_n / (np.linalg.norm(ink, axis=2) * np.linalg.norm(ink_n) + 1e-9)
    cos_t = ink @ ink_t / (np.linalg.norm(ink, axis=2) * np.linalg.norm(ink_t) + 1e-9)
    is_teal = cos_t > cos_n
    cover_n = ink @ ink_n / (ink_n @ ink_n)
    cover_t = ink @ ink_t / (ink_t @ ink_t)
    alpha = np.clip(np.where(is_teal, cover_t, cover_n), 0.0, 1.0)
    alpha[alpha < 0.04] = 0.0
    a8 = (alpha * 255).round().astype(np.uint8)[..., None]
    colour = np.where(is_teal[..., None], teal, navy)
    Image.fromarray(np.concatenate([colour.astype(np.uint8), a8], axis=2), "RGBA").save(
        os.path.join(out_dir, "csb_logo.png"))
    dark = np.where(is_teal[..., None], teal, 255.0)
    Image.fromarray(np.concatenate([dark.astype(np.uint8), a8], axis=2), "RGBA").save(
        os.path.join(out_dir, "csb_logo_white.png"))


derive_csb_logos(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "CSB_logo.jpg"), OUT)

imgs = [Image.open(os.path.join(OUT, f"portalcalc_icon_{s}.png")).convert("RGBA")
        for s in (256, 128, 64, 48, 32, 24, 16)]
imgs[0].save(os.path.join(OUT, "portalcalc_icon.ico"), format="ICO",
             sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)], append_images=imgs[1:])
print("ok", ratio)
