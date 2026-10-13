"""Isolated runtime adapter for the current PortalCalc desktop calculation.

The public service contract is Qt-free (see :mod:`portalcalc_service`). This
adapter is intentionally isolated while calculation orchestration is migrated
out of ``MainWindow``. It never shows the window, suppresses optimiser dialogs
and disables optimiser-learning writes.
"""

from __future__ import annotations

import copy
import os
from dataclasses import replace
from typing import Any, Mapping

from portalcalc_service import (
    CalculationMode,
    PreparedCalculationRequest,
    calculation_source_provenance,
    calculation_web_selection_state,
)


class HeadlessPortalCalcBackend:
    def calculate(self, request: PreparedCalculationRequest) -> Mapping[str, Any]:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from main import MainWindow

        app = QApplication.instance()
        owns_app = app is None
        if app is None:
            app = QApplication([])
        window = self.create_window(MainWindow)
        window.confirm_on_close = False
        try:
            window.apply_preset_data(copy.deepcopy(dict(request.inputs)))
            panel_count = (request.inputs.get("calculation_service") or {}).get("main_truss_panel_count")
            if panel_count is not None:
                window._panel_count_override = int(panel_count)
            web_state = calculation_web_selection_state(request.inputs)
            assignments = web_state["assignments"]
            if assignments:
                window.auto_web_profile_assignments = copy.deepcopy(assignments)
                window.auto_web_joint_doubler_sides = copy.deepcopy(
                    web_state["doubler_sides"]
                )
                window.auto_web_assignment_basis = {
                    group: window.web_assignment_basis_signature(group) for group in assignments
                }
            previous_preserve = getattr(window, "_preserve_web_assignments_on_solve", False)
            previous_topology = window._suppress_panel_topology_search
            window._preserve_web_assignments_on_solve = bool(assignments)
            window._suppress_panel_topology_search = panel_count is not None
            try:
                window.generate_and_solve()
            finally:
                window._preserve_web_assignments_on_solve = previous_preserve
                window._suppress_panel_topology_search = previous_topology
            self._raise_if_unsolved(window)
            optimiser = None
            if request.mode is CalculationMode.AUTO_SIZE:
                optimiser = self.optimise_window(window)
                self._raise_if_unsolved(window)
            dossier = window.assistant_design_dossier()
            from future_canopy_design import secondary_checks
            dossier["secondary_steel_checks"] = secondary_checks(window)
            from wind import calculate_wind
            wind_basis = window.wind_inputs_from_ui(float(window.span_input.text()) * 1000,
                                                    float(window.pitch_input.text()),
                                                    float(window.eave_input.text()) * 1000)
            dossier["wind_direction_checks"] = {}
            for case in ("Left +", "Right +", "End +"):
                source = window.wind_source_direction_for_case(wind_basis.orientation, case)
                wind_result = calculate_wind(replace(wind_basis, orientation=source))
                dossier["wind_direction_checks"][case] = {
                    "source_direction": source,
                    "design_md": wind_result.design_direction_multiplier,
                    "ultimate_pressure_kpa": wind_result.wu_kn_m2,
                    "service_pressure_kpa": wind_result.ws_kn_m2,
                }
            if window.frame_system_combo.currentText() == "Truss" and (optimiser is not None or panel_count is not None):
                dossier.setdefault("inputs", {}).setdefault("calculation_service", {})[
                    "main_truss_panel_count"
                ] = int(window.panel_count_label.text())
            if optimiser is not None:
                dossier["verified"] = bool(optimiser.get("verified"))
                dossier["optimisation"] = copy.deepcopy(optimiser)
            if optimiser is not None or assignments:
                dossier.setdefault("inputs", {}).setdefault("calculation_service", {})[
                    "auto_web_profile_assignments"
                ] = copy.deepcopy(window.auto_web_profile_assignments)
                dossier["inputs"]["calculation_service"][
                    "auto_web_joint_doubler_sides"
                ] = copy.deepcopy(window.auto_web_joint_doubler_sides)
            dossier["warnings"] = [
                "Preliminary proposal only; engineering review and approval are required.",
            ]
            dossier["engine_provenance"] = {
                "runtime": "isolated MainWindow compatibility backend",
                **calculation_source_provenance(),
            }
            return dossier
        finally:
            window.close()
            if owns_app:
                app.quit()

    @staticmethod
    def optimise_window(window):
        return window.auto_size_current_preset(show_completion=False, save_learning=False)

    @staticmethod
    def create_window(window_class):
        """Allow search adapters to specialise reporting/cost objectives."""
        return window_class()

    @staticmethod
    def _raise_if_unsolved(window: Any) -> None:
        if window.structure is not None and window.design_envelope_actions is not None:
            return
        detail = window.results_box.toPlainText().strip()
        raise ValueError(detail or "PortalCalc did not produce a solved model")
