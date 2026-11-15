"""Actions that open the 3D bracing workspace or run the combined frame + bracing sizing."""

import copy
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QProgressDialog


class BracingActionsMixin:
    """3D bracing dialogs and the combined sizing run."""

    def edit_bracing_locations(self):
        try:
            geometry = self.current_bracing_layout_geometry()
        except (TypeError, ValueError, OverflowError) as error:
            QMessageBox.warning(self, "Bracing locations", f"Check the building dimensions first:\n{error}")
            return
        from portalcalc.desktop.bracing_layout_ui import BracingLayoutDialog
        read_only = self.configurator_review is not None
        dialog = BracingLayoutDialog(geometry, saved=copy.deepcopy(self.bracing_location_layout),
                                     parent=self, read_only=read_only)
        if dialog.exec() == QDialog.Accepted and not read_only:
            self.bracing_location_layout = dialog.snapshot()
            self.update_bracing_layout_summary()
            self.record_model_snapshot()

    def analyse_fixed_bracing_layout(self):
        from portalcalc.bracing.analysis_desktop import prepare_context
        from portalcalc.desktop.bracing_workspace_ui import BracingAnalysisDialog
        try:
            # Rebuild once with current selected sections. The subsequent 3D
            # solve/recovery operates on a private unloaded portal copy.
            self.generate_and_solve()
            context = prepare_context(self, allow_unconfigured=True)
        except (ValueError, ArithmeticError) as error:
            QMessageBox.warning(self, "3D bracing analysis", str(error))
            return
        read_only = self.configurator_review is not None
        dialog = BracingAnalysisDialog(context, self.catalog, saved=copy.deepcopy(self.bracing_3d_settings),
                                       parent=self, read_only=read_only,
                                       cached_calculation=self.bracing_3d_calculation_cache)
        opening_settings, opening_layout = dialog.snapshot(), dialog.layout_snapshot()
        accepted = dialog.exec() == QDialog.Accepted
        if accepted and not read_only:
            if (dialog.result and dialog.result.get('mullion_basis') and not dialog.result.get('sizing_failure')
                    and dialog.result['design']['taut_tangent_verified']):
                posts=[check for key,check in dialog.result['section_checks'].items() if key.startswith('post/')]
                if posts and all(check['passes'] for check in posts):
                    dialog.adopted_mullion_section=dialog.selections()['end_post'].name
            settings = dialog.snapshot()
            location_layout = dialog.layout_snapshot()
            if settings != self.bracing_3d_settings or location_layout != self.bracing_location_layout:
                self.bracing_3d_settings = settings
                self.bracing_location_layout = location_layout
                self.update_bracing_layout_summary()
                if settings.get('adopted_mullion_section'):
                    self.generate_and_solve()
                self.record_model_snapshot()
            if dialog.result and not dialog.result.get('sizing_failure'):
                self.adopt_bracing_3d_restraint(dialog.result)
        if accepted or (dialog.snapshot()==opening_settings and dialog.layout_snapshot()==opening_layout):
            self.bracing_3d_calculation_cache = dialog.calculation_cache()

    def run_combined_sizing(self):
        """Frontier optimiser for the frame and 3D bracing together (frontier_sizing)."""
        from portalcalc.bracing.analysis import AnalysisError
        from portalcalc.sizing import combined as combined_sizing
        from portalcalc.sizing import frontier as frontier_sizing
        if self.configurator_review is not None:
            QMessageBox.information(self, "Optimise frame + bracing", "Configurator Review is read-only.")
            return
        answer = QMessageBox.question(
            self, "Optimise frame + bracing",
            "Re-size the 2D frame and the 3D bracing together for the lowest total estimated cost?\n\n"
            "This replaces the current frame sections, bracing layout and bracing sections, and can take a few minutes. "
            "Cancel restores the current design.")
        if answer != QMessageBox.Yes:
            return
        before = combined_sizing.capture_state(self)
        progress = QProgressDialog("Preparing...", "Cancel", 0, 1000, self)
        progress.setWindowTitle("Optimise frame + bracing")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumWidth(520)
        progress.setValue(0)
        progress.show()
        started = time.perf_counter()
        cancelled = []
        current = {"message": "Preparing...", "value": 0}

        def show_label():
            if cancelled:
                return
            elapsed = int(time.perf_counter() - started)
            percent = progress.value() / 10
            progress.setLabelText(f"{current['message']}\n\n{percent:.0f}% · {elapsed // 60}:{elapsed % 60:02d} elapsed")

        # Keep the elapsed time ticking between optimiser reports (the solvers
        # process events often enough for this timer to fire).
        clock = QTimer(progress)
        clock.timeout.connect(show_label)
        clock.start(1000)

        def note(message, fraction=None):
            # Overall fraction 0-1 from frontier_sizing.Progress. Cancel stops
            # at the next safe point (never inside a 2D auto-size).
            if cancelled or progress.wasCanceled():
                if not cancelled:
                    cancelled.append(True)
                    progress.show()
                    progress.setValue(current["value"])   # Qt's cancel resets the bar
                    progress.setLabelText("Cancelling: stopping at the next safe point, then restoring "
                                          "the previous design...")
                # Keep the window responsive until the optimiser reaches a
                # safe point (a 2D auto-size in progress finishes first).
                QApplication.processEvents()
                raise AnalysisError("Optimisation cancelled; the previous design is restored.")
            if fraction is not None:
                current["value"] = int(round(1000*max(0.0, min(1.0, fraction))))
                progress.setValue(current["value"])
            current["message"] = message
            show_label()
            QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            report = frontier_sizing.optimise(self, progress=note)
        except (AnalysisError, ValueError, ArithmeticError) as error:
            clock.stop()
            combined_sizing.restore_state(self, before, exact=True)
            self.update_bracing_layout_summary()
            QApplication.restoreOverrideCursor()
            progress.close()
            QMessageBox.warning(self, "Optimise frame + bracing", str(error))
            return
        clock.stop()
        QApplication.restoreOverrideCursor()
        progress.close()
        self.update_bracing_layout_summary()
        self.record_model_snapshot()
        best = report['best']
        text = frontier_sizing.report_text(report)
        self.combined_sizing_report_text = text
        check = best.get('workspace_check') or {}
        if check.get('matches'):
            review = ("The 3D workspace's Analyse and check gives the same result and it is saved: "
                      "open the 3D bracing workspace to inspect members.")
        else:
            review = (f"The 3D workspace's Analyse and check differs ({'PASS' if check.get('passes') else 'FAIL / UNVERIFIED'}, "
                      f"bracing ${check.get('estimated_cost') or 0:,.2f}); review it in the 3D bracing workspace.")
        box = QMessageBox(self)
        box.setWindowTitle("Optimise frame + bracing")
        box.setText(f"Adopted: {best['frame']} + {best['bracing']}.\n"
                    f"Estimated total ${best['total']:,.2f} (frame ${best['frame_cost']:,.2f}, "
                    f"bracing ${best['bracing_cost']:,.2f}).\n\n"
                    f"{review} Show Details has the full comparison.")
        box.setDetailedText(text)
        box.exec()
