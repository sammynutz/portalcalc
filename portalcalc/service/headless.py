"""Runs a calculation request on the frame engine, with no window and no Qt.

The request/result contract is in :mod:`portalcalc.service.contract`. This
backend applies the request's inputs to a :class:`HeadlessFrameEngine`, solves
(and sizes, for ``autoSize``) and returns the design dossier.

A v2 (building) request is calculated one sizing group at a time. Each group
is its own 2D frame, checked or sized against only its gridlines' building
loads. The groups are combined into one dossier.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Mapping

from portalcalc.service.contract import (
    CalculationMode,
    PreparedCalculationRequest,
    building_with_group_inputs,
    calculation_source_provenance,
    calculation_web_selection_state,
)


class HeadlessPortalCalcBackend:
    """Runs a prepared request on a fresh :class:`HeadlessFrameEngine`."""

    def calculate(self, request: PreparedCalculationRequest) -> Mapping[str, Any]:
        engine = self.create_engine()
        if request.building is None:
            return self._frame_dossier(engine, request, request.inputs)
        from portalcalc.building.model import BuildingModel
        engine.building_load_mode = request.load_basis
        engine.building_model_override = BuildingModel.from_dict(dict(request.building))
        groups = list(request.groups) or [{"id": "G1", "gridlines": None,
                                           "representative": request.representative_gridline,
                                           "inputs": request.inputs}]
        dossiers = {}
        for group in groups:
            engine.building_load_gridlines = group["gridlines"]
            dossier = self._frame_dossier(engine, request, group["inputs"])
            dossier["building_results"] = engine.building_service_results(
                request.building, dossier.get("inputs") or group["inputs"], group_id=group["id"])
            dossiers[group["id"]] = dossier
        return combine_group_dossiers(request, groups, dossiers)

    def _frame_dossier(self, engine, request, inputs) -> dict:
        """Check or size one 2D frame (the whole v1 request, or one v2 sizing group)."""
        engine.apply_preset_data(copy.deepcopy(dict(inputs)))
        # apply_preset_data resets the panel override, and an unpreserved solve
        # clears web assignments, so nothing carries over between sizing groups.
        panel_count = (inputs.get("calculation_service") or {}).get("main_truss_panel_count")
        if panel_count is not None:
            engine._panel_count_override = int(panel_count)
        web_state = calculation_web_selection_state(inputs)
        assignments = web_state["assignments"]
        if assignments:
            engine.auto_web_profile_assignments = copy.deepcopy(assignments)
            engine.auto_web_joint_doubler_sides = copy.deepcopy(
                web_state["doubler_sides"]
            )
            engine.auto_web_assignment_basis = {
                group: engine.web_assignment_basis_signature(group) for group in assignments
            }
        previous_preserve = getattr(engine, "_preserve_web_assignments_on_solve", False)
        previous_topology = engine._suppress_panel_topology_search
        engine._preserve_web_assignments_on_solve = bool(assignments)
        engine._suppress_panel_topology_search = panel_count is not None
        try:
            engine.generate_and_solve()
        finally:
            engine._preserve_web_assignments_on_solve = previous_preserve
            engine._suppress_panel_topology_search = previous_topology
        self._raise_if_unsolved(engine)
        optimiser = None
        if request.mode is CalculationMode.AUTO_SIZE:
            optimiser = engine.auto_size_current_preset(show_completion=False)
            self._raise_if_unsolved(engine)
        dossier = engine.calculation_design_dossier()
        from portalcalc.frame.future_canopy_design import secondary_checks
        dossier["secondary_steel_checks"] = secondary_checks(engine)
        from portalcalc.wind import calculate_wind
        wind_basis = engine.wind_inputs_from_ui(float(engine.span_input.text()) * 1000,
                                                float(engine.pitch_input.text()),
                                                float(engine.eave_input.text()) * 1000)
        dossier["wind_direction_checks"] = {}
        for case in ("Left +", "Right +", "End +"):
            source = engine.wind_source_direction_for_case(wind_basis.orientation, case)
            wind_result = calculate_wind(replace(wind_basis, orientation=source))
            dossier["wind_direction_checks"][case] = {
                "source_direction": source,
                "design_md": wind_result.design_direction_multiplier,
                "ultimate_pressure_kpa": wind_result.wu_kn_m2,
                "service_pressure_kpa": wind_result.ws_kn_m2,
            }
        if engine.frame_system_combo.currentText() == "Truss" and (optimiser is not None or panel_count is not None):
            dossier.setdefault("inputs", {}).setdefault("calculation_service", {})[
                "main_truss_panel_count"
            ] = int(engine.panel_count_label.text())
        if optimiser is not None:
            dossier["verified"] = bool(optimiser.get("verified"))
            dossier["optimisation"] = copy.deepcopy(optimiser)
        if optimiser is not None or assignments:
            dossier.setdefault("inputs", {}).setdefault("calculation_service", {})[
                "auto_web_profile_assignments"
            ] = copy.deepcopy(engine.auto_web_profile_assignments)
            dossier["inputs"]["calculation_service"][
                "auto_web_joint_doubler_sides"
            ] = copy.deepcopy(engine.auto_web_joint_doubler_sides)
        dossier["warnings"] = [
            "Preliminary proposal only; engineering review and approval are required.",
        ]
        dossier["engine_provenance"] = {
            "runtime": "headless frame engine",
            **calculation_source_provenance(),
        }
        return dossier

    @staticmethod
    def create_engine():
        from portalcalc.frame.engine.headless import HeadlessFrameEngine

        return HeadlessFrameEngine()

    @staticmethod
    def _raise_if_unsolved(engine: Any) -> None:
        if engine.structure is not None and engine.design_envelope_actions is not None:
            return
        detail = engine.results_box.toPlainText().strip()
        raise ValueError(detail or "PortalCalc did not produce a solved model")


def _governing_ratio(dossier) -> float:
    try:
        return float((dossier.get("overall_governing") or {}).get("ratio") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _group_verified(dossier) -> bool:
    governing = dossier.get("overall_governing") or {}
    return bool(dossier.get("verified", governing.get("passes_ratio_screen", False)))


def combine_group_dossiers(request, groups, dossiers) -> dict:
    """One v2 dossier from per-group dossiers.

    The largest group's dossier is the base: its inputs and sections are the
    v1-style ``sizing``. The governing check is the worst group's; the result
    is verified only if every group is. Building results list every gridline,
    group, governing action (tagged with its group) and the sized building.
    """
    primary_id = max(groups, key=lambda g: len(g["gridlines"] or ()))["id"]
    combined = copy.deepcopy(dossiers[primary_id])
    worst_id = max(dossiers, key=lambda gid: _governing_ratio(dossiers[gid]))
    combined["overall_governing"] = copy.deepcopy(dossiers[worst_id].get("overall_governing"))
    combined["overall_governing_group"] = worst_id
    combined["verified"] = all(_group_verified(d) for d in dossiers.values())
    if len(dossiers) > 1:
        combined["group_dossiers"] = {
            gid: {"verified": _group_verified(d), "overall_governing": d.get("overall_governing"),
                  "sections": (d.get("inputs") or {}).get("sections", {}),
                  "member_checks": d.get("member_checks", []),
                  "serviceability_checks": d.get("serviceability_checks", []),
                  "optimisation_report": (d.get("optimisation") or {}).get("report")}
            for gid, d in dossiers.items()
        }
    results = [dossiers[g["id"]]["building_results"] for g in groups]
    base = copy.deepcopy(results[0])
    gridlines = [row for r in results for row in r["gridlines"]]
    order = {g["id"]: i for i, g in enumerate(base["all_gridlines"])}
    base["gridlines"] = sorted(gridlines, key=lambda row: order[row["id"]])
    base["sizing_groups"] = [r["sizing_groups"][0] for r in results]
    base["governing"] = [item for r in results for item in r["governing"]]
    base["combinations"] = {
        "checked": sum(r["combinations"]["checked"] or 0 for r in results),
        "total": sum(r["combinations"]["total"] or 0 for r in results),
        "full_set_checked": all(r["combinations"]["full_set_checked"] for r in results),
        "by_group": {r["sizing_groups"][0]["id"]: r["combinations"] for r in results},
    }
    base["sized_building"] = building_with_group_inputs(
        request.building, {gid: d.get("inputs") or {} for gid, d in dossiers.items()})
    base.pop("all_gridlines", None)
    combined["building_results"] = base
    return combined
