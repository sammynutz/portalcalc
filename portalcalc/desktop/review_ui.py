"""Configurator Review mode: review a BOLT proposal and return the result."""

import copy

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from portalcalc.app_metadata import APP_VERSION
from portalcalc.service.configurator_review import (
    atomic_write_json,
    build_cancelled_result,
    build_reviewed_result,
    load_review_request,
    merge_reviewable_selections,
    review_truss_depth_bounds,
    ReviewLaunchOptions,
    validate_review_truss_depth,
)
from portalcalc.service.contract import (
    calculation_source_provenance,
    calculation_web_selection_state,
    CalculationMode,
    json_safe as calculation_json_safe,
    PortalCalcCalculationService,
    prepare_request as prepare_calculation_request,
)


class ConfiguratorReviewMixin:
    """Locks the UI to the reviewable selections and writes the reviewed result."""

    def start_configurator_review(self, options: ReviewLaunchOptions):
        """Load and lock a configurator-owned estimate-review session."""

        loaded = load_review_request(options.request_path)
        self.configurator_review = loaded
        self.configurator_review_result_path = options.result_path
        self.configurator_review_finalized = False
        self.configurator_review_auto_optimise_pending = True
        self.configurator_review_optimiser = None
        self.current_preset_path = None
        self.apply_preset_data(copy.deepcopy(dict(loaded.prepared.inputs)))
        if loaded.prepared.building is not None:
            # v2 request: review against every gridline's building loads.
            from portalcalc.building.model import BuildingModel
            self.building_load_mode = loaded.prepared.load_basis
            self.building_model_override = BuildingModel.from_dict(dict(loaded.prepared.building))
        self.configure_configurator_review_ui()
        self.generate_and_solve()
        self.update_configurator_review_status(prefix="Optimisation queued on open. ")
        self.mark_model_clean()
        self.update_window_title()

    def configurator_review_member_combos(self):
        names = (
            "left_column_profile_combo",
            "right_column_profile_combo",
            "internal_column_profile_combo",
            "top_profile_combo",
            "web_profile_combo",
            "left_canopy_top_profile_combo",
            "left_canopy_web_profile_combo",
            "left_lean_column_profile_combo",
            "right_canopy_top_profile_combo",
            "right_canopy_web_profile_combo",
            "right_lean_column_profile_combo",
            "end_wall_brace_size_combo",
            "roof_brace_size_combo",
            "wall_brace_size_combo",
            "storage_rail_profile_combo",
            "storage_stub_profile_combo",
            "crane_beam_profile_combo",
            "crane_beam_rail_profile_combo",
        )
        return [getattr(self, name) for name in names if getattr(self, name, None) is not None]

    def configure_configurator_review_ui(self):
        banner = QWidget()
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(10, 8, 10, 8)
        title = QLabel("Estimate review — not engineering approval")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        status = QLabel()
        status.setWordWrap(True)
        actions = QHBoxLayout()
        return_button = QPushButton("Return reviewed estimate")
        cancel_button = QPushButton("Cancel")
        return_button.clicked.connect(self.return_configurator_review)
        cancel_button.clicked.connect(self.cancel_configurator_review)
        actions.addWidget(return_button)
        actions.addWidget(cancel_button)
        banner_layout.addWidget(title)
        banner_layout.addWidget(status)
        banner_layout.addLayout(actions)
        self.left_panel_layout.insertWidget(0, banner)
        self.configurator_review_banner = banner
        self.configurator_review_title_label = title
        self.configurator_review_status_label = status
        self.configurator_review_return_button = return_button
        self.configurator_review_cancel_button = cancel_button

        allowed_combos = set(self.configurator_review_member_combos())
        allowed_combos.add(self.depth_input)
        display_controls = {
            getattr(self, name, None)
            for name in ("result_combo", "load_view_mode_combo", "single_load_combo")
        }
        allowed_combos.update(item for item in display_controls if item is not None)
        allowed_buttons = {return_button, cancel_button, self.optimise_structure_button}
        allowed_selector_parents = {combo.parentWidget() for combo in allowed_combos}

        for widget in self.findChildren(QWidget):
            if isinstance(widget, (QLineEdit, QComboBox, QCheckBox, QSpinBox, QRadioButton)):
                widget.setEnabled(widget in allowed_combos)
        for button in self.findChildren(QPushButton):
            button.setEnabled(button in allowed_buttons)
        for button in self.findChildren(QToolButton):
            button.setEnabled(button.parentWidget() in allowed_selector_parents)

        try:
            self.optimise_structure_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.optimise_structure_button.clicked.connect(self.run_configurator_review_optimisation)
        self.depth_input.editingFinished.connect(self.on_configurator_review_depth_edited)
        self.optimise_structure_button.setText("Optimise members for estimate")
        self.optimise_structure_button.setToolTip(
            "Run PortalCalc optimisation, retain its member selections, restore all configurator-owned inputs, "
            "then solve the restored configuration exactly."
        )
        for action in self.menuBar().actions():
            if "Help" not in action.text():
                action.setEnabled(False)
        self.input_tabs.setCurrentWidget(self.sections_tab)
        banner.setStyleSheet("background:#f6c453; color:#18222d; border:2px solid #9b6a00;")

    def _configurator_review_web_state(self):
        return {
            "assignments": copy.deepcopy(self.auto_web_profile_assignments),
            "doubler_sides": copy.deepcopy(self.auto_web_joint_doubler_sides),
        }

    def configurator_review_depth_validation(self):
        try:
            depth_mm = validate_review_truss_depth(
                self.configurator_review.prepared.inputs,
                self.depth_input.text(),
            )
            minimum_mm, maximum_mm = review_truss_depth_bounds(
                self.configurator_review.prepared.inputs
            )
            return depth_mm, minimum_mm, maximum_mm, None
        except ValueError as exc:
            return None, None, None, str(exc)

    def on_configurator_review_depth_edited(self):
        if self.configurator_review is None:
            return
        _, _, _, error = self.configurator_review_depth_validation()
        if error:
            self.configurator_review_status_label.setText(f"DEPTH ERROR: {error}. Return is blocked.")
            self.configurator_review_status_label.setStyleSheet(
                "font-weight:bold; color:white; background:#8f1d1d; padding:6px;"
            )
            return
        current_inputs = self.collect_preset_data()
        review_inputs = merge_reviewable_selections(
            self.configurator_review.prepared.inputs,
            current_inputs,
        )
        web_state = self._configurator_review_web_state()
        review_inputs["calculation_service"] = calculation_json_safe({
            "auto_web_profile_assignments": copy.deepcopy(web_state["assignments"]),
            "auto_web_joint_doubler_sides": copy.deepcopy(web_state["doubler_sides"]),
        })
        self._solve_configurator_review_inputs(review_inputs)
        self.update_configurator_review_status(prefix="Depth updated. ")

    def _solve_configurator_review_inputs(self, inputs, web_state=None):
        self.apply_preset_data(copy.deepcopy(dict(inputs)))
        request_web_state = calculation_web_selection_state(inputs)
        request_assignments = request_web_state["assignments"]
        assignments = copy.deepcopy(
            (web_state or {}).get("assignments") or request_assignments
        )
        if assignments:
            self.auto_web_profile_assignments = assignments
            self.auto_web_joint_doubler_sides = copy.deepcopy(
                (web_state or {}).get("doubler_sides")
                or request_web_state["doubler_sides"]
                or {}
            )
            self.auto_web_assignment_basis = {
                group: self.web_assignment_basis_signature(group) for group in assignments
            }
        previous_preserve = getattr(self, "_preserve_web_assignments_on_solve", False)
        self._preserve_web_assignments_on_solve = bool(assignments)
        try:
            self.generate_and_solve()
        finally:
            self._preserve_web_assignments_on_solve = previous_preserve

    def run_configurator_review_optimisation(self):
        if self.configurator_review is None:
            return
        self.configurator_review_auto_optimise_pending = False
        physics_result = self.run_physics_structure_optimisation(show_completion=False)
        self.configurator_review_optimiser = "physics"
        optimised_inputs = self.collect_preset_data()
        web_state = self._configurator_review_web_state()
        review_inputs = merge_reviewable_selections(
            self.configurator_review.prepared.inputs,
            optimised_inputs,
        )
        review_inputs["calculation_service"] = calculation_json_safe({
            "auto_web_profile_assignments": copy.deepcopy(web_state["assignments"]),
            "auto_web_joint_doubler_sides": copy.deepcopy(web_state["doubler_sides"]),
        })
        self._solve_configurator_review_inputs(review_inputs)
        self.optimise_structure_button.setText("Optimise members for estimate")
        verified = isinstance(physics_result, dict) and physics_result.get("verified") is True
        self.update_configurator_review_status(prefix=(
            "Optimise complete. " if verified
            else "Optimise found no verified candidate. Review the exact checks. "
        ))

    def start_pending_configurator_review_optimisation(self):
        if (
            self.configurator_review is None
            or self.configurator_review_finalized
            or not self.configurator_review_auto_optimise_pending
        ):
            return False
        self.configurator_review_auto_optimise_pending = False
        self.run_configurator_review_optimisation()
        return True

    def update_configurator_review_status(self, prefix=""):
        if self.configurator_review_status_label is None:
            return
        _, minimum_mm, maximum_mm, depth_error = self.configurator_review_depth_validation()
        if depth_error:
            self.configurator_review_status_label.setText(
                f"DEPTH ERROR: {depth_error}. Return reviewed estimate is blocked."
            )
            self.configurator_review_status_label.setStyleSheet(
                "font-weight:bold; color:white; background:#8f1d1d; padding:6px;"
            )
            return
        if self.structure is None or self.design_envelope_actions is None:
            message = "FAILED OR UNCHECKED — PortalCalc did not produce a solved model."
            colour = "#8f1d1d"
        else:
            active_specs = self.active_auto_size_group_specs()
            ratio, group, check = self.auto_size_overall_ratio(active_specs)
            verified = self.auto_size_design_acceptable(active_specs, ratio)
            if verified:
                message = (
                    f"Current exact check passes: {group} / {check}, ratio {ratio:.3f}. "
                    f"Truss depth range {minimum_mm:.0f}–{maximum_mm:.0f} mm."
                )
                colour = "#176b3a"
            else:
                message = (
                    f"FAILED OR UNCHECKED: {group} / {check}, ratio {ratio:.3f}. "
                    f"Truss depth range {minimum_mm:.0f}–{maximum_mm:.0f} mm. "
                    "It may still be returned for estimating."
                )
                colour = "#8f1d1d"
        self.configurator_review_status_label.setText(prefix + message)
        self.configurator_review_status_label.setStyleSheet(
            f"font-weight:bold; color:white; background:{colour}; padding:6px;"
        )

    def configurator_review_selected_sections(self, inputs, dossier):
        selected = {
            "presetSelections": copy.deepcopy((inputs or {}).get("sections", {})),
            "automaticWebAssignments": copy.deepcopy(self.auto_web_profile_assignments),
        }
        for label, check in (
            ("purlin", self.purlin_check),
            ("wallGirt", self.wall_girt_check),
            ("endWallGirt", self.end_wall_girt_check),
        ):
            if isinstance(check, dict):
                selected[label] = {
                    "general": (check.get("match") or {}).get("section"),
                    "local": (check.get("local_match") or {}).get("section"),
                }
        if isinstance(self.storage_rail_design, dict):
            profile = self.storage_rail_design.get("profile")
            selected["storageRail"] = getattr(profile, "name", str(profile) if profile else None)
        crane = self.current_crane_beam_summary()
        if crane:
            selected["craneRunwayBeam"] = crane.get("section")
        end_wall = self.end_wall_mullion_design or {}
        if isinstance(end_wall, dict):
            selected["endWallMullion"] = end_wall.get("section") or end_wall.get("selected_section")
        selected["doubledChordZones"] = copy.deepcopy(
            (self.design_envelope_actions or {}).get("doubled_chord_zones", [])
        )
        selected['columnFlybraces'] = self.column_flybrace_schedule()
        return calculation_json_safe(selected)

    def _configurator_review_dossier(self, final_inputs):
        if self.structure is not None and self.design_envelope_actions is not None:
            dossier = self.calculation_design_dossier()
            dossier["inputs"] = copy.deepcopy(final_inputs)
        else:
            detail = self.results_box.toPlainText().strip() or "PortalCalc did not produce a solved model"
            dossier = {
                "schema": "portalcalc.calculation.dossier.v1",
                "portalcalc_version": APP_VERSION,
                "status": "failed or unchecked",
                "inputs": copy.deepcopy(final_inputs),
                "analysis": {"status": "not solved", "error": detail},
                "overall_governing": {
                    "ratio": "infinity",
                    "group": "UNRESOLVED",
                    "check": detail,
                    "passes_ratio_screen": False,
                },
                "member_checks": [],
                "serviceability_checks": [],
            }
        dossier["warnings"] = [
            "Estimate review only — not engineering approval or construction release.",
        ]
        dossier["engine_provenance"] = {
            "runtime": "interactive Configurator Review exact checkSpecified",
            "estimate_optimiser": getattr(self, "configurator_review_optimiser", None),
            **calculation_source_provenance(),
        }
        return dossier

    def return_configurator_review(self, *, show_message=True, close_after=True):
        if self.configurator_review is None or self.configurator_review_finalized:
            return None
        depth_mm, _, maximum_mm, depth_error = self.configurator_review_depth_validation()
        if depth_error:
            self.update_configurator_review_status()
            if show_message:
                QMessageBox.warning(
                    self,
                    "Invalid truss depth",
                    f"{depth_error}. Enter a valid 50 mm increment before returning the estimate.",
                )
            return None
        self.configurator_review_return_button.setEnabled(False)
        QApplication.processEvents()
        try:
            current_inputs = self.collect_preset_data()
            final_inputs = merge_reviewable_selections(
                self.configurator_review.prepared.inputs,
                current_inputs,
            )
            web_state = self._configurator_review_web_state()
            final_inputs["calculation_service"] = calculation_json_safe({
                "auto_web_profile_assignments": copy.deepcopy(web_state["assignments"]),
                "auto_web_joint_doubler_sides": copy.deepcopy(web_state["doubler_sides"]),
            })
            original = self.configurator_review.prepared
            if original.building is not None:
                from portalcalc.service.configurator_review import reviewed_building
                final_raw = {
                    "schema": original.schema,
                    "request_id": original.request_id,
                    "mode": CalculationMode.CHECK_SPECIFIED.value,
                    "building": reviewed_building(original.building, final_inputs),
                    "options": {"load_basis": original.load_basis},
                }
            else:
                final_raw = {
                    "schema": original.schema,
                    "request_id": original.request_id,
                    "mode": CalculationMode.CHECK_SPECIFIED.value,
                    "inputs": final_inputs,
                }
            prepared_final = prepare_calculation_request(final_raw)
            self._solve_configurator_review_inputs(prepared_final.inputs)
            dossier = self._configurator_review_dossier(prepared_final.inputs)
            if prepared_final.building is not None:
                dossier["building_results"] = self.building_service_results(
                    prepared_final.building, prepared_final.inputs)

            class FrozenReviewBackend:
                def calculate(inner_self, request):
                    return copy.deepcopy(dossier)

            service_result = PortalCalcCalculationService(FrozenReviewBackend()).run(final_raw)
            selected = self.configurator_review_selected_sections(prepared_final.inputs, dossier)
            reviewed_result = build_reviewed_result(
                service_result,
                original_request_digest=self.configurator_review.prepared.input_digest,
                final_input_digest=prepared_final.input_digest,
                selected_sections=selected,
                selected_geometry={
                    "trussDepthMm": depth_mm,
                    "maxTrussDepthMm": maximum_mm,
                },
            )
            atomic_write_json(self.configurator_review_result_path, reviewed_result)
            self.configurator_review_finalized = True
            self.update_configurator_review_status(prefix="Returned. ")
            verified = bool(reviewed_result.get("verified"))
            if show_message:
                message = (
                    f"Reviewed estimate written to:\n{self.configurator_review_result_path}\n\n"
                    + ("The final exact check passes." if verified else "FAILED OR UNCHECKED — the result was returned for estimating only.")
                )
                if verified:
                    QMessageBox.information(self, "Estimate review returned", message)
                else:
                    QMessageBox.warning(self, "Failed or unchecked estimate returned", message)
            if close_after:
                self.confirm_on_close = False
                self.close()
            return reviewed_result
        except Exception as exc:
            self.configurator_review_return_button.setEnabled(True)
            self.configurator_review_status_label.setText(f"RETURN FAILED: {exc}")
            self.configurator_review_status_label.setStyleSheet(
                "font-weight:bold; color:white; background:#8f1d1d; padding:6px;"
            )
            if show_message:
                QMessageBox.critical(self, "Could not return reviewed estimate", str(exc))
            return None

    def cancel_configurator_review(self, *, show_message=False, close_after=True):
        if self.configurator_review is None or self.configurator_review_finalized:
            return None
        cancelled = build_cancelled_result(self.configurator_review)
        try:
            atomic_write_json(self.configurator_review_result_path, cancelled)
        except OSError as exc:
            if show_message:
                QMessageBox.critical(self, "Could not cancel estimate review", str(exc))
            return None
        self.configurator_review_finalized = True
        if close_after:
            self.confirm_on_close = False
            self.close()
        return cancelled
