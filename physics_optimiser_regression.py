"""Off-screen comparison harness for the experimental Physics Optimise flow."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
import time
from unittest import mock


def evaluate(preset_path: Path) -> dict:
    """Solve one preset without changing its design selections."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="portalcalc_physics_evaluate_")

    from PySide6.QtWidgets import QApplication
    from main import MainWindow

    preset = json.loads(preset_path.read_text(encoding="utf-8"))
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.confirm_on_close = False
    try:
        window.apply_preset_data(copy.deepcopy(preset))
        window.generate_and_solve()
        if window.structure is None:
            raise AssertionError(window.results_box.toPlainText() or "Solve failed")
        specs = window.active_auto_size_group_specs()
        ratio, group, check = window.auto_size_overall_ratio(specs)
        envelopes = window.design_envelope_actions or {}

        def envelope_values(group_name: str) -> dict:
            envelope = envelopes.get(group_name) or {}
            values = {}
            for key in ("compression_kn", "tension_kn", "shear_kn", "moment_knm"):
                item = envelope.get(key) or {}
                values[key] = {
                    "value": item.get("value"),
                    "combo": item.get("combo"),
                    "member": item.get("member"),
                }
            return values

        return {
            "preset": str(preset_path),
            "verified": window.auto_size_design_acceptable(specs, ratio),
            "ratio": ratio,
            "governing_group": group,
            "governing_check": check,
            "cost": float((window.costing_summary or {}).get("total_cost", float("inf"))),
            "depths_mm": {
                "main": window.depth_input.text(),
                "left_addition": window.left_canopy_shallow_depth_input.text(),
                "right_addition": window.right_canopy_shallow_depth_input.text(),
            },
            "sections": {
                group_name: combo.currentText()
                for _, combo, group_name in specs
            },
            "envelopes": {
                group_name: envelope_values(group_name)
                for group_name in (
                    "TOP", "BOTTOM", "WEB", "LEFT_COLUMN", "RIGHT_COLUMN",
                    "RIGHT_CANOPY_TOP", "RIGHT_CANOPY_BOTTOM", "RIGHT_CANOPY_WEB",
                    "RIGHT_LEAN_COLUMN",
                )
                if group_name in envelopes
            },
            "roof_geometry_mm": {
                "main_top": [
                    [node.x, node.y] for node in window.concept_main_top_nodes
                ],
                "right_lean_top": [
                    [node.x, node.y] for node in window.concept_right_lean_top_nodes
                ],
            },
            "basic_roof_case_vertical_resultants_kn": {
                label: sum(float(arrow[3]) for arrow in arrows) / 1000.0
                for label, arrows in (window.single_load_arrows or {}).items()
                if label in {
                    "G", "Q", "Solar",
                    "Right canopy G", "Right canopy Q", "Right canopy Solar",
                }
            },
        }
    finally:
        window.close()
        app.quit()


def run(
    preset_path: Path,
    *,
    compare_production: bool = False,
    max_truss_depth_mm: float | None = None,
) -> dict:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["PORTALCALC_LICENSE_ENFORCED"] = "0"
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="portalcalc_physics_regression_")

    from PySide6.QtWidgets import QApplication, QMessageBox
    from main import MainWindow

    preset = json.loads(preset_path.read_text(encoding="utf-8"))
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.confirm_on_close = False
    try:
        window.apply_preset_data(copy.deepcopy(preset))
        if max_truss_depth_mm is not None:
            window.max_truss_depth_input.setText(f"{max_truss_depth_mm:g}")
            assert window.max_main_truss_depth_mm() == max_truss_depth_mm
        window.generate_and_solve()
        if window.structure is None:
            raise AssertionError(window.results_box.toPlainText() or "Initial solve failed")
        with (
            mock.patch.object(QMessageBox, "information", return_value=QMessageBox.Ok),
            mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok),
            mock.patch.object(QMessageBox, "critical", return_value=QMessageBox.Ok),
        ):
            physics_started = time.perf_counter()
            physics = window.run_physics_structure_optimisation()
            physics_wall_seconds = time.perf_counter() - physics_started
            if max_truss_depth_mm is not None and physics.get("verified"):
                final_depth_mm = float(window.depth_input.text())
                assert final_depth_mm <= max_truss_depth_mm + 1e-9, (
                    f"Physics Optimise exceeded the entered maximum: "
                    f"{final_depth_mm:.0f} mm > {max_truss_depth_mm:.0f} mm"
                )
            result = {
                "preset": str(preset_path),
                "physics": {
                    **{key: value for key, value in physics.items() if key != "report"},
                    "wall_seconds": physics_wall_seconds,
                    "depths_mm": {
                        "main": window.depth_input.text(),
                        "left_addition": window.left_canopy_shallow_depth_input.text(),
                        "right_addition": window.right_canopy_shallow_depth_input.text(),
                    },
                    "sections": {
                        group: combo.currentText()
                        for _, combo, group in window.active_auto_size_group_specs()
                    },
                    "report": physics.get("report", ""),
                },
            }
            if compare_production:
                window.apply_preset_data(copy.deepcopy(preset))
                window.generate_and_solve()
                production_started = time.perf_counter()
                production = window.auto_size_current_preset(
                    show_completion=False,
                    save_learning=False,
                )
                production_wall_seconds = time.perf_counter() - production_started
                result["production"] = {
                    **{key: value for key, value in production.items() if key != "report"},
                    "wall_seconds": production_wall_seconds,
                    "depths_mm": {
                        "main": window.depth_input.text(),
                        "left_addition": window.left_canopy_shallow_depth_input.text(),
                        "right_addition": window.right_canopy_shallow_depth_input.text(),
                    },
                    "sections": {
                        group: combo.currentText()
                        for _, combo, group in window.active_auto_size_group_specs()
                    },
                }
            return result
    finally:
        window.close()
        app.quit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset",
        type=Path,
        default=Path(__file__).resolve().parent / "presets" / "Truss Deflection Weirdness.json",
    )
    parser.add_argument("--compare-production", action="store_true")
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument(
        "--max-truss-depth-mm",
        type=float,
        help="Set an optimiser depth ceiling and assert Physics Optimise respects it.",
    )
    args = parser.parse_args()
    result = (
        evaluate(args.preset)
        if args.evaluate_only
        else run(
            args.preset,
            compare_production=args.compare_production,
            max_truss_depth_mm=args.max_truss_depth_mm,
        )
    )
    print(json.dumps(
        result,
        indent=2,
        default=lambda value: value.item() if hasattr(value, "item") else str(value),
    ))


if __name__ == "__main__":
    main()
