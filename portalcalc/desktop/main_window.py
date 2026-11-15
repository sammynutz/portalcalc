"""The PortalCalc desktop main window."""

from PySide6.QtCore import QEvent, QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QWidget,
)

from portalcalc.desktop.bracing_actions import BracingActionsMixin
from portalcalc.desktop.branding import app_icon
from portalcalc.desktop.menus import MenusMixin
from portalcalc.desktop.preset_files import PresetFilesMixin
from portalcalc.desktop.review_ui import ConfiguratorReviewMixin
from portalcalc.desktop.ui_build import UiBuildMixin
from portalcalc.desktop.view_state import ViewStateMixin
from portalcalc.frame.engine import FrameEngine


class MainWindow(UiBuildMixin, MenusMixin, PresetFilesMixin, ConfiguratorReviewMixin, ViewStateMixin, BracingActionsMixin, FrameEngine, QMainWindow):
    """The desktop window: input forms, result views and menus over the frame engine.

    Calculation lives in :class:`portalcalc.frame.engine.FrameEngine`; the mixins
    listed first add the Qt user interface. The engine reads the input widgets
    through their plain accessors (``text()``, ``currentText()``, ``isChecked()``).
    """

    def __init__(self):
        super().__init__()
        self.setWindowIcon(app_icon())
        self.resize(1820, 980)

        self.init_state()
        self.update_window_title()

        self.load_catalog()
        self.load_metroll_tables()
        self.build_ui()
        QApplication.instance().installEventFilter(self)
        self.load_factory_defaults(silent=True)
        self.apply_builtin_canopy_section_defaults()
        self.setup_model_history()
        self.mark_model_clean()
        self.update_window_title()

    # ---- UI hooks called by the calculation engine (see portalcalc.frame.engine.host) ----

    def process_ui_events(self):
        QApplication.processEvents()

    def notify(self, level, title, text):
        getattr(QMessageBox, level)(self, title, text)

    def set_label_rich_text(self, label, rich):
        label.setTextFormat(Qt.RichText if rich else Qt.PlainText)

    def block_input_signals(self):
        return [QSignalBlocker(widget) for widget in self.findChildren(QWidget)]

    def eventFilter(self, obj, event):
        if obj in getattr(self, "deflection_hover_overlays", {}):
            if event.type() == QEvent.Type.Enter:
                self.set_deflection_hover_overlay_active(True, obj)
            elif event.type() == QEvent.Type.Leave:
                self.set_deflection_hover_overlay_active(False, obj)
        if event.type() == QEvent.Type.Wheel and isinstance(obj, QComboBox):
            if not obj.view().isVisible():
                return True
        return super().eventFilter(obj, event)

    def generate_and_solve_with_popup(self):
        self.generate_and_solve()
        result_text = self.results_box.toPlainText() if hasattr(self, "results_box") else ""
        if result_text.startswith("Input error:") or self.structure is None:
            QMessageBox.critical(self, "Analysis failed", result_text or "The model could not be generated.")

    def closeEvent(self, event):
        if self.configurator_review is not None and not self.configurator_review_finalized:
            response = QMessageBox.question(
                self,
                "Cancel estimate review?",
                "Close PortalCalc and return a cancelled result without a reviewed estimate?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if response == QMessageBox.Yes:
                cancelled = self.cancel_configurator_review(close_after=False)
                if cancelled is not None:
                    event.accept()
                else:
                    event.ignore()
            else:
                event.ignore()
            return
        if not getattr(self, "confirm_on_close", True):
            event.accept()
            return
        if self.confirm_model_replacement("closing"):
            event.accept()
        else:
            event.ignore()

    def optimisation_progress_begin(self):
        progress = QProgressDialog("Preparing optimiser...", "", 0, 12, self)
        progress.setWindowTitle("Optimising structure")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        self.optimisation_progress_dialog = progress
        QApplication.processEvents()
