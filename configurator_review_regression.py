"""Offscreen regression for the interactive Configurator Review workflow."""

from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QToolButton

from configurator_review import (
    CANCELLED_STATUS,
    REVIEW_STATUS,
    ReviewLaunchOptions,
    load_review_request,
    merge_reviewable_selections,
    parse_review_arguments,
    review_truss_depth_bounds,
)
from main import MainWindow


def run_regression(*, with_optimiser: bool = False) -> None:
    root = Path(__file__).resolve().parent
    request_inputs = json.loads(
        (root / "presets" / "factory_defaults.json").read_text(encoding="utf-8")
    )
    request_inputs["geometry"]["truss_depth_mm"] = "900"
    request_inputs["geometry"]["max_truss_depth_mm"] = "1800"
    minimum_depth_mm, maximum_depth_mm = review_truss_depth_bounds(request_inputs)
    request = {
        "schema": "portalcalc.calculation.request.v1",
        "request_id": "configurator-review-regression",
        "mode": "checkSpecified",
        "inputs": request_inputs,
    }

    assert parse_review_arguments([]) == (None, [])
    try:
        parse_review_arguments(["--review-request", "request.json"])
        raise AssertionError("unpaired review arguments were accepted")
    except ValueError:
        pass

    with tempfile.TemporaryDirectory(prefix="portalcalc-review-") as temp_dir:
        temp = Path(temp_dir)
        request_path = temp / "request.json"
        result_path = temp / "nested" / "result.json"
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        options, remaining = parse_review_arguments([
            "--review-request", str(request_path),
            "--review-result", str(result_path),
            "-style", "fusion",
        ])
        assert options is not None
        assert remaining == ["-style", "fusion"]
        loaded = load_review_request(request_path)
        assert loaded.prepared.request_id == request["request_id"]

        changed = copy.deepcopy(request_inputs)
        changed["geometry"]["span_mm"] = "99000"
        changed["geometry"]["truss_depth_mm"] = "1000"
        changed["geometry"]["max_truss_depth_mm"] = "9900"
        changed["sections"]["left_column"] = "310 UB 32.0"
        merged = merge_reviewable_selections(request_inputs, changed)
        assert merged["geometry"]["span_mm"] == request_inputs["geometry"]["span_mm"]
        assert merged["geometry"]["truss_depth_mm"] == "1000"
        assert merged["geometry"]["max_truss_depth_mm"] == "1800"
        assert merged["sections"]["left_column"] == "310 UB 32.0"

        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.start_configurator_review(ReviewLaunchOptions(request_path, result_path))
        assert window.configurator_review_auto_optimise_pending is True
        assert window.configurator_review_title_label.text() == "Estimate review — not engineering approval"
        assert "NOT ENGINEERING APPROVAL" in window.windowTitle()
        assert window.span_input.isEnabled() is False
        assert window.max_truss_depth_input.isEnabled() is False
        assert window.depth_input.isEnabled() is True
        assert window.left_column_profile_combo.isEnabled() is True
        assert window.optimise_structure_button.isEnabled() is True
        depth_buttons = window.depth_input.parentWidget().findChildren(QToolButton)
        assert len(depth_buttons) == 2 and all(button.isEnabled() for button in depth_buttons)
        assert window.max_main_truss_depth_mm() == 1800.0
        locked_geometry_widgets = {
            "span": window.span_input.text(),
            "pitch": window.pitch_input.text(),
            "eave": window.eave_input.text(),
            "maximum": window.max_truss_depth_input.text(),
            "roof_shape": window.roof_shape_combo.currentText(),
            "frame_system": window.frame_system_combo.currentText(),
            "left_support": window.left_support_combo.currentText(),
            "right_support": window.right_support_combo.currentText(),
        }

        window.depth_input.setText("1850")
        window.on_configurator_review_depth_edited()
        assert "DEPTH ERROR" in window.configurator_review_status_label.text()
        assert window.return_configurator_review(show_message=False, close_after=False) is None
        assert not result_path.exists()

        window.depth_input.setText(f"{minimum_depth_mm - 50:.0f}")
        window.on_configurator_review_depth_edited()
        assert (
            f"between {minimum_depth_mm:.0f} mm and {maximum_depth_mm:.0f} mm"
            in window.configurator_review_status_label.text()
        )
        assert window.return_configurator_review(show_message=False, close_after=False) is None

        window.depth_input.setText("975")
        window.on_configurator_review_depth_edited()
        assert "50 mm increments" in window.configurator_review_status_label.text()
        assert window.return_configurator_review(show_message=False, close_after=False) is None

        window.depth_input.setText("950")
        window.max_truss_depth_input.setText("9999")  # Immutable maximum cannot be adopted.
        window.on_configurator_review_depth_edited()
        assert window.depth_input.text() == "950"
        assert window.max_truss_depth_input.text() == "1800"
        window.step_depth_input(window.depth_input, 50.0)
        assert window.depth_input.text() == "1000"
        window.depth_input.setText("1800")
        window.step_depth_input(window.depth_input, 50.0)
        assert window.depth_input.text() == "1800"
        window.depth_input.setText("950")
        window.on_configurator_review_depth_edited()

        adopted_optimiser_depth = None
        if with_optimiser:
            with mock.patch.object(window, "run_physics_structure_optimisation", wraps=window.run_physics_structure_optimisation) as physics, mock.patch.object(window, "run_structure_optimisation", side_effect=AssertionError("Legacy optimiser must not run during Configurator Review")) as legacy:
                assert window.start_pending_configurator_review_optimisation() is True
                physics.assert_called_once_with(show_completion=False)
                legacy.assert_not_called()
            assert window.configurator_review_auto_optimise_pending is False
            after_optimise = window.collect_preset_data()
            adopted_optimiser_depth = float(after_optimise["geometry"]["truss_depth_mm"])
            assert minimum_depth_mm <= adopted_optimiser_depth <= maximum_depth_mm
            frozen_after_optimise = merge_reviewable_selections(
                request_inputs,
                after_optimise,
            )
            for key, original_value in request_inputs["geometry"].items():
                if key == "truss_depth_mm":
                    continue
                assert frozen_after_optimise["geometry"][key] == original_value, key
            assert window.span_input.text() == locked_geometry_widgets["span"]
            assert window.pitch_input.text() == locked_geometry_widgets["pitch"]
            assert window.eave_input.text() == locked_geometry_widgets["eave"]
            assert window.max_truss_depth_input.text() == locked_geometry_widgets["maximum"]
            assert window.roof_shape_combo.currentText() == locked_geometry_widgets["roof_shape"]
            assert window.frame_system_combo.currentText() == locked_geometry_widgets["frame_system"]
            assert window.left_support_combo.currentText() == locked_geometry_widgets["left_support"]
            assert window.right_support_combo.currentText() == locked_geometry_widgets["right_support"]

        original_span = window.span_input.text()
        original_index = window.left_column_profile_combo.currentIndex()
        next_index = min(original_index + 1, window.left_column_profile_combo.count() - 1)
        if next_index == original_index:
            next_index = max(original_index - 1, 0)
        window.left_column_profile_combo.setCurrentIndex(next_index)
        adjusted_section = window.left_column_profile_combo.currentText()
        window.span_input.setText("99")  # Programmatic drift must still be discarded.
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text('{"partial":true}', encoding="utf-8")

        reviewed = window.return_configurator_review(show_message=False, close_after=False)
        assert reviewed is not None
        assert reviewed["reviewStatus"] == REVIEW_STATUS
        assert reviewed["request_id"] == request["request_id"]
        assert reviewed["originalRequestDigest"] == loaded.prepared.input_digest
        assert reviewed["finalInputDigest"] == reviewed["provenance"]["request_digest_sha256"]
        if not with_optimiser:
            assert reviewed["verified"] is False
            assert reviewed["status"] == "reviewedEstimateFailedOrUnchecked"
            assert reviewed["warnings"][0].startswith("FAILED OR UNCHECKED")
        selected_depth = float(reviewed["selectedGeometry"]["trussDepthMm"])
        assert selected_depth == (adopted_optimiser_depth if with_optimiser else 950.0)
        assert reviewed["selectedGeometry"]["maxTrussDepthMm"] == 1800.0
        assert reviewed["selectedSections"]["presetSelections"]["left_column"] == adjusted_section
        assert reviewed["dossier"]["inputs"]["geometry"]["span_mm"] == request_inputs["geometry"]["span_mm"]
        assert float(reviewed["dossier"]["inputs"]["geometry"]["truss_depth_mm"]) == selected_depth
        assert reviewed["dossier"]["inputs"]["geometry"]["max_truss_depth_mm"] == "1800"
        assert window.span_input.text() == original_span
        assert reviewed["checks"]["overall_governing"]
        assert reviewed["dossier"]["engine_provenance"]["source_file_sha256"]["main.py"]
        if with_optimiser:
            assert reviewed["dossier"]["engine_provenance"]["estimate_optimiser"] == "physics"
        assert result_path.is_file()
        on_disk = json.loads(result_path.read_text(encoding="utf-8"))
        assert on_disk == reviewed
        assert not list(result_path.parent.glob(f".{result_path.name}.*.tmp"))

        cancel_path = temp / "cancelled.json"
        window.configurator_review_result_path = cancel_path
        window.configurator_review_finalized = False
        cancelled = window.cancel_configurator_review(close_after=False)
        assert cancelled is not None
        assert json.loads(cancel_path.read_text(encoding="utf-8"))["status"] == CANCELLED_STATUS
        assert json.loads(cancel_path.read_text(encoding="utf-8"))["reviewStatus"] == CANCELLED_STATUS

        window.configurator_review_finalized = True
        window.confirm_on_close = False
        window.close()
        app.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-optimiser", action="store_true")
    args = parser.parse_args()
    run_regression(with_optimiser=args.with_optimiser)
    print("configurator review regression ok")
