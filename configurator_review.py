"""Configurator Review lifecycle helpers for the PortalCalc desktop app."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from portalcalc_service import (
    PreparedCalculationRequest,
    RESULT_SCHEMA,
    json_safe,
    prepare_request,
)


REVIEW_STATUS = "reviewedForEstimate"
CANCELLED_STATUS = "cancelled"
TRUSS_DEPTH_INCREMENT_MM = 50.0
TRUSS_DEPTH_MIN_SPAN_RATIO = 0.03


class ReviewConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewLaunchOptions:
    request_path: Path
    result_path: Path


@dataclass(frozen=True)
class LoadedReviewRequest:
    raw: Mapping[str, Any]
    prepared: PreparedCalculationRequest
    request_path: Path


def parse_review_arguments(argv: Sequence[str]) -> tuple[ReviewLaunchOptions | None, list[str]]:
    """Parse PortalCalc review arguments while preserving Qt arguments."""

    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--review-request")
    parser.add_argument("--review-result")
    options, remaining = parser.parse_known_args(list(argv))
    if bool(options.review_request) != bool(options.review_result):
        raise ReviewConfigurationError(
            "--review-request and --review-result must be supplied together"
        )
    if not options.review_request:
        return None, remaining
    return ReviewLaunchOptions(
        request_path=Path(options.review_request).expanduser().resolve(),
        result_path=Path(options.review_result).expanduser().resolve(),
    ), remaining


def load_review_request(path: Path) -> LoadedReviewRequest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReviewConfigurationError(f"could not read review request {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewConfigurationError(f"review request is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReviewConfigurationError("review request must contain a JSON object")
    try:
        prepared = prepare_request(raw)
    except ValueError as exc:
        raise ReviewConfigurationError(str(exc)) from exc
    validate_review_truss_depth(prepared.inputs)
    return LoadedReviewRequest(raw=copy.deepcopy(raw), prepared=prepared, request_path=path)


def _finite_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ReviewConfigurationError(f"{field} must be a number") from exc
    if not math.isfinite(number):
        raise ReviewConfigurationError(f"{field} must be finite")
    return number


def review_truss_depth_bounds(inputs: Mapping[str, Any]) -> tuple[float, float]:
    geometry = inputs.get("geometry")
    if not isinstance(geometry, Mapping):
        raise ReviewConfigurationError("inputs.geometry must be an object")
    span_mm = _finite_number(geometry.get("span_mm"), "geometry.span_mm")
    maximum_mm = _finite_number(
        geometry.get("max_truss_depth_mm"),
        "geometry.max_truss_depth_mm",
    )
    minimum_mm = math.ceil(
        (TRUSS_DEPTH_MIN_SPAN_RATIO * span_mm) / TRUSS_DEPTH_INCREMENT_MM
    ) * TRUSS_DEPTH_INCREMENT_MM
    if maximum_mm < minimum_mm:
        raise ReviewConfigurationError(
            f"geometry.max_truss_depth_mm must be at least the span-derived minimum {minimum_mm:.0f} mm"
        )
    return minimum_mm, maximum_mm


def validate_review_truss_depth(
    inputs: Mapping[str, Any], depth_value: Any | None = None
) -> float:
    geometry = inputs.get("geometry")
    if not isinstance(geometry, Mapping):
        raise ReviewConfigurationError("inputs.geometry must be an object")
    minimum_mm, maximum_mm = review_truss_depth_bounds(inputs)
    value = geometry.get("truss_depth_mm") if depth_value is None else depth_value
    depth_mm = _finite_number(value, "geometry.truss_depth_mm")
    if abs(depth_mm / TRUSS_DEPTH_INCREMENT_MM - round(depth_mm / TRUSS_DEPTH_INCREMENT_MM)) > 1e-9:
        raise ReviewConfigurationError("truss depth must be entered in 50 mm increments")
    if depth_mm < minimum_mm or depth_mm > maximum_mm:
        raise ReviewConfigurationError(
            f"truss depth must be between {minimum_mm:.0f} mm and {maximum_mm:.0f} mm"
        )
    return depth_mm


def merge_reviewable_selections(
    original_inputs: Mapping[str, Any], current_inputs: Mapping[str, Any]
) -> dict[str, Any]:
    """Keep configurator inputs frozen while carrying forward member selections."""

    merged = copy.deepcopy(dict(original_inputs))
    current = copy.deepcopy(dict(current_inputs))
    merged["sections"] = copy.deepcopy(current.get("sections", merged.get("sections", {})))
    current_geometry = current.get("geometry")
    merged_geometry = merged.get("geometry")
    if isinstance(current_geometry, dict) and isinstance(merged_geometry, dict):
        if "truss_depth_mm" in current_geometry:
            merged_geometry["truss_depth_mm"] = copy.deepcopy(current_geometry["truss_depth_mm"])
    selection_paths = (
        ("geometry", "end_wall_brace_size"),
        ("bracing", "roof_brace_size"),
        ("bracing", "wall_brace_size"),
        ("storage_loads", "rail_profile"),
        ("storage_loads", "stub_profile"),
        ("crane_beam", "beam_profile"),
        ("crane_beam", "rail_profile"),
    )
    for group, key in selection_paths:
        source_group = current.get(group)
        target_group = merged.get(group)
        if isinstance(source_group, dict) and isinstance(target_group, dict) and key in source_group:
            target_group[key] = copy.deepcopy(source_group[key])
    return merged


def build_reviewed_result(
    service_result: Mapping[str, Any],
    *,
    original_request_digest: str,
    final_input_digest: str,
    selected_sections: Mapping[str, Any],
    selected_geometry: Mapping[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(dict(service_result))
    verified = bool(result.get("verified"))
    result.update({
        "status": "reviewedEstimatePassed" if verified else "reviewedEstimateFailedOrUnchecked",
        "reviewStatus": REVIEW_STATUS,
        "originalRequestDigest": original_request_digest,
        "finalInputDigest": final_input_digest,
        "selectedSections": json_safe(selected_sections),
        "selectedGeometry": json_safe(selected_geometry),
        "verified": verified,
    })
    warnings = list(result.get("warnings") or [])
    warnings.append("Estimate review only — not engineering approval or construction release.")
    if not verified:
        warnings.insert(
            0,
            "FAILED OR UNCHECKED: the returned estimate did not achieve a verified PortalCalc check.",
        )
    result["warnings"] = list(dict.fromkeys(str(item) for item in warnings))
    dossier = result.get("dossier")
    if isinstance(dossier, dict):
        dossier["reviewStatus"] = REVIEW_STATUS
        dossier["original_request_digest"] = original_request_digest
        dossier["final_input_digest"] = final_input_digest
        dossier["review_selected_sections"] = json_safe(selected_sections)
        dossier["warnings"] = copy.deepcopy(result["warnings"])
    return json_safe(result)


def build_cancelled_result(loaded: LoadedReviewRequest) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "request_id": loaded.prepared.request_id,
        "status": CANCELLED_STATUS,
        "reviewStatus": CANCELLED_STATUS,
        "verified": False,
        "originalRequestDigest": loaded.prepared.input_digest,
        "warnings": [
            "Configurator estimate review was cancelled; no reviewed estimate was produced.",
        ],
    }


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Durably replace a result with complete JSON from the destination folder."""

    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(json_safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, destination)
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
