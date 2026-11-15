"""PortalCalc desktop application entry point (window, splash, licence check)."""

import os
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMessageBox

from portalcalc.app_metadata import BRAND_NAVY
from portalcalc.desktop.main_window import MainWindow
from portalcalc.desktop.branding import app_icon, apply_brand_palette, create_startup_splash
from portalcalc.service.configurator_review import parse_review_arguments


def main():
    if "--packaged-computation-smoke-test" in sys.argv[1:]:
        # Exercise the bundled PDF library/fonts and a real report export.
        import tempfile
        from pathlib import Path
        smoke_index = sys.argv.index("--packaged-computation-smoke-test")
        smoke_path = Path(sys.argv[smoke_index + 1]).resolve()
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
        try:
            with tempfile.TemporaryDirectory(prefix="portalcalc_report_smoke_") as smoke_data:
                os.environ["APPDATA"] = smoke_data
                smoke_app = QApplication([sys.argv[0]])
                smoke_window = MainWindow()
                smoke_window.confirm_on_close = False
                smoke_window.generate_and_solve()
                smoke_report = smoke_window.capture_computation_report({"project": "Packaged report validation", "revision": "Test"})
                from portalcalc.reports.computation_report import export_computation_pdf
                export_computation_pdf(smoke_report, smoke_path)
                smoke_window.close()
            raise SystemExit(0)
        except Exception as exc:
            smoke_path.with_suffix(".error.txt").write_text(str(exc), encoding="utf-8")
            raise SystemExit(1)

    if "--packaged-smoke-test" in sys.argv[1:]:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        packaged_smoke_app = QApplication([sys.argv[0]])
        raise SystemExit(0)

    try:
        review_options, qt_arguments = parse_review_arguments(sys.argv[1:])
    except Exception as exc:
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "Configurator Review startup failed", str(exc))
        raise SystemExit(2) from exc
    app = QApplication([sys.argv[0], *qt_arguments])
    app.setWindowIcon(app_icon())
    apply_brand_palette(app)
    splash = create_startup_splash()
    splash.show()
    splash_started_at = time.perf_counter()
    app.processEvents()
    try:
        splash.showMessage(
            "Loading sections, presets and design data...",
            Qt.AlignBottom | Qt.AlignHCenter,
            QColor(BRAND_NAVY),
        )
        app.processEvents()
        window = MainWindow()
        if review_options is not None:
            window.start_configurator_review(review_options)
        splash.showMessage(
            "Preparing workspace...",
            Qt.AlignBottom | Qt.AlignHCenter,
            QColor(BRAND_NAVY),
        )
        window.show()

        def enforce_license_after_startup():
            from portalcalc.licensing import license_enforced

            if not license_enforced():
                if review_options is not None:
                    QTimer.singleShot(0, window.start_pending_configurator_review_optimisation)
                return
            try:
                from portalcalc.licensing import require_valid_license
                licensed = require_valid_license(window)
            except Exception as exc:
                QMessageBox.critical(
                    window,
                    "License check failed",
                    str(exc),
                )
                licensed = False
            if not licensed:
                QMessageBox.warning(
                    window,
                    "License required",
                    "PortalCalc needs a valid license to continue.",
                )
                window.close()
                app.quit()
            else:
                window.update_license_status_label()
                if review_options is not None:
                    QTimer.singleShot(0, window.start_pending_configurator_review_optimisation)

        def finish_startup_splash():
            splash.finish(window)
            QTimer.singleShot(100, enforce_license_after_startup)

        remaining_splash_ms = max(0, int((2.0 - (time.perf_counter() - splash_started_at)) * 1000))
        QTimer.singleShot(remaining_splash_ms, finish_startup_splash)
        sys.exit(app.exec())
    except Exception as exc:
        splash.close()
        QMessageBox.critical(None, "Startup Error", str(exc))
        raise
