"""Brand assets for the desktop app: icon, logos, palette, splash and EULA text."""

import os

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QSplashScreen

from portalcalc.app_metadata import APP_DISPLAY_NAME, APP_VERSION, BRAND_NAVY, BRAND_TEAL
from portalcalc.frame.common import resource_path


APP_ICON_FILENAME = os.path.join("assets", "portalcalc_icon.ico")


def load_eula_text():
    try:
        with open(resource_path("EULA.txt"), "r", encoding="utf-8") as file:
            return file.read()
    except OSError:
        return "EULA.txt was not found."


def app_icon():
    icon = QIcon(resource_path(APP_ICON_FILENAME))
    if icon.isNull():
        icon = QIcon(resource_path(os.path.join("assets", "portalcalc_icon.svg")))
    return icon


def brand_logo_pixmap(dark_background=False):
    name = "portalcalc_logo_white.png" if dark_background else "portalcalc_logo.png"
    return QPixmap(resource_path(os.path.join("assets", name)))


def csb_logo_pixmap(dark_background=False):
    name = "csb_logo_white.png" if dark_background else "csb_logo.png"
    return QPixmap(resource_path(os.path.join("assets", name)))


def apply_brand_palette(app):
    """Use the BOLT navy for selection/focus accents instead of the OS accent colour."""
    palette = app.palette()
    for group in (QPalette.Active, QPalette.Inactive):
        palette.setColor(group, QPalette.Highlight, QColor(BRAND_NAVY))
        palette.setColor(group, QPalette.HighlightedText, QColor("#ffffff"))
        palette.setColor(group, QPalette.Accent, QColor(BRAND_NAVY))
        palette.setColor(group, QPalette.Link, QColor(BRAND_TEAL))
    app.setPalette(palette)


def create_startup_splash():
    pixmap = QPixmap(560, 300)
    pixmap.fill(QColor("#ffffff"))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#d5dcea"), 1))
    painter.drawRect(0, 0, pixmap.width() - 1, pixmap.height() - 1)
    # BOLT navy band with a teal keyline along the top edge.
    painter.fillRect(0, 0, pixmap.width(), 8, QColor(BRAND_NAVY))
    painter.fillRect(0, 8, pixmap.width(), 3, QColor(BRAND_TEAL))

    logo = brand_logo_pixmap()
    if not logo.isNull():
        logo_width = pixmap.width() - 112
        logo_scaled = logo.scaledToWidth(logo_width, Qt.SmoothTransformation)
        painter.drawPixmap((pixmap.width() - logo_scaled.width()) // 2, 70, logo_scaled)
    else:
        font = painter.font()
        font.setPointSize(28)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(BRAND_NAVY))
        painter.drawText(QRectF(0, 72, pixmap.width(), 52), Qt.AlignCenter, APP_DISPLAY_NAME)

    font = painter.font()
    font.setPointSize(10)
    font.setBold(False)
    painter.setFont(font)
    painter.setPen(QColor("#4d5b78"))
    painter.drawText(
        QRectF(0, 182, pixmap.width(), 24),
        Qt.AlignCenter,
        f"Version {APP_VERSION}",
    )

    painter.setPen(QPen(QColor(BRAND_TEAL), 2))
    painter.drawLine(72, 226, pixmap.width() - 72, 226)

    csb_logo = csb_logo_pixmap()
    if not csb_logo.isNull():
        csb_scaled = csb_logo.scaledToWidth(92, Qt.SmoothTransformation)
        painter.drawPixmap(
            pixmap.width() - csb_scaled.width() - 16,
            pixmap.height() - csb_scaled.height() - 14,
            csb_scaled,
        )
    painter.end()

    splash = QSplashScreen(pixmap)
    splash.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    splash.showMessage("Starting...", Qt.AlignBottom | Qt.AlignHCenter, QColor(BRAND_NAVY))
    return splash
