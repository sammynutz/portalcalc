"""Search-only objective: build the main shed now, carrying future canopy loads."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import costing
from portalcalc_headless import HeadlessPortalCalcBackend

SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def main_now_cost_summary(window, structure, summary):
    """Remove future canopy frame purchases using the engine's exact allowances.

    Existing purlin/girt and end-wall items already describe the main shed only.
    This does not estimate a complete future canopy supply/installation price.
    """
    structural_copy = SimpleNamespace(elements=[copy.copy(e) for e in structure.elements])
    window.apply_auto_web_assignments_to_structure(structural_copy)
    future_elements = [e for e in structural_copy.elements
                       if e.group.startswith(("LEFT_CANOPY", "RIGHT_CANOPY"))
                       or e.group in {"LEFT_LEAN_COLUMN", "RIGHT_LEAN_COLUMN"}]
    count = summary["portal_count"]
    weights = {"shs": 0.0, "ub": 0.0, "wb": 0.0}
    for profile, length_m in costing.costing_physical_cut_lengths_m(SimpleNamespace(elements=future_elements)):
        bucket = window.section_cost_bucket(profile)
        purchase_length = length_m * 1.07 if bucket == "shs" else window.stock_length_m_for_cut(length_m)
        weights[bucket] += profile.A * 1e-6 * 7850 * purchase_length * count
    future_costs = {bucket: kg * summary["rates"][bucket] for bucket, kg in weights.items()}
    doubled_weight = 0.0
    combos = {"LEFT_CANOPY_TOP": window.left_canopy_top_profile_combo,
              "LEFT_CANOPY_BOTTOM": window.left_canopy_bottom_profile_combo,
              "RIGHT_CANOPY_TOP": window.right_canopy_top_profile_combo,
              "RIGHT_CANOPY_BOTTOM": window.right_canopy_bottom_profile_combo}
    for zone in (window.design_envelope_actions or {}).get("doubled_chord_zones", []):
        combo = combos.get(zone.get("group"))
        if combo is None:
            continue
        profile = window.selected_profile(combo)
        if profile is None:
            continue
        kg = profile.A * 1e-6 * 7850 * float(zone.get("length_mm", 0)) / 1000 * count
        if window.section_cost_bucket(profile) == "shs":
            kg *= 1.07
        doubled_weight += kg
    future_costs["doubled_chord"] = doubled_weight * summary["rates"]["doubled_chord"]
    result = copy.deepcopy(summary)
    result["full_modelled_steel_cost"] = summary["total_cost"]
    result["full_modelled_costs"] = copy.deepcopy(summary["costs"])
    result["future_canopy_frame_cost"] = sum(future_costs.values())
    result["future_canopy_frame_costs"] = future_costs
    for key, value in future_costs.items():
        result["costs"][key] -= value
        if result["costs"][key] < -0.01:
            raise ValueError(f"Future canopy cost exceeds full {key} cost")
        result["costs"][key] = max(result["costs"][key], 0.0)
    result["total_cost"] = sum(result["costs"].values())
    result["cost_scope"] = "Main shed now; future canopy frame steel excluded, canopy loads included"
    return result


class PhysicsSearchBackend(HeadlessPortalCalcBackend):
    @staticmethod
    def optimise_window(window):
        import math
        from unittest.mock import patch
        from PySide6.QtCore import QSignalBlocker
        from PySide6.QtWidgets import QMessageBox
        for side in ("left", "right"):
            if getattr(window, f"{side}_canopy_type_combo").currentText() == "None":
                continue
            if getattr(window, f"{side}_canopy_frame_system_combo").currentText() != "Truss":
                continue
            bounds = window.addition_depth_bounds(side)
            if bounds is None:
                continue
            minimum, maximum = bounds
            field = getattr(window, f"{side}_canopy_shallow_depth_input")
            first, last = math.ceil(minimum / 50) * 50, math.floor(maximum / 50) * 50
            if first > last:
                raise ValueError(f"No selectable {side} addition depth inside bounds")
            depth = min(max(math.ceil(float(field.text()) / 50) * 50, first), last)
            blocker = QSignalBlocker(field)
            field.setText(f"{depth:.0f}")
            del blocker
        # The desktop physics flow has modal completion/error notifications.
        # Preserve its returned failure details without opening hidden dialogs.
        with patch.object(QMessageBox, "information", return_value=QMessageBox.Ok), \
             patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok), \
             patch.object(QMessageBox, "critical", return_value=QMessageBox.Ok):
            result = window.run_physics_structure_optimisation()
        result["algorithm"] = "physics"
        return result


class FutureCanopyBackend(PhysicsSearchBackend):
    @staticmethod
    def optimise_window(window):
        return window.run_future_canopy_optimisation(show_completion=False)

    @staticmethod
    def create_window(window_class):
        class FutureCostWindow(window_class):
            def costing_from_structure(self, structure, building_length_m, bay_size_m):
                summary = super().costing_from_structure(structure, building_length_m, bay_size_m)
                return main_now_cost_summary(self, structure, summary)

        return FutureCostWindow()

    def calculate(self, request):
        dossier = dict(super().calculate(request))
        dossier.setdefault("inputs", {})["hay_shed_search"] = copy.deepcopy(
            request.inputs.get("hay_shed_search") or {})
        dossier["engine_provenance"]["hay_shed_backend_sha256"] = SOURCE_SHA256
        dossier["engine_provenance"]["cost_objective"] = "main shed now, with future canopy provision"
        return dossier
