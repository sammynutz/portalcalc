"""Headless application-service contract for PortalCalc calculations.

This module deliberately has no Qt imports. It owns request validation,
calculation policy, immutable request snapshots and the structured response
envelope. A calculation backend is injected so the desktop application and a
configurator adapter can call the same service without duplicating policy.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol

from app_metadata import APP_RELEASE_DATE, APP_VERSION


REQUEST_SCHEMA = "portalcalc.calculation.request.v1"
RESULT_SCHEMA = "portalcalc.calculation.result.v1"
DOSSIER_SCHEMA = "portalcalc.calculation.dossier.v1"

SNOW_REGION_K1 = {
    "Region AN": 0.04,
    "Region AC": 0.08,
    "Region AS": 0.10,
    "Region AT": 0.12,
}
WEB_ASSIGNMENT_GROUPS = {"WEB", "LEFT_CANOPY_WEB", "RIGHT_CANOPY_WEB"}


class CalculationMode(str, Enum):
    AUTO_SIZE = "autoSize"
    CHECK_SPECIFIED = "checkSpecified"


class CalculationServiceError(ValueError):
    """Base class for request errors safe to return to an adapter client."""

    code = "calculation_service_error"


class InvalidCalculationRequest(CalculationServiceError):
    code = "invalid_request"


class UnsupportedCalculationInput(CalculationServiceError):
    code = "unsupported_input"


class CalculationBackend(Protocol):
    """Engine boundary implemented by the PortalCalc calculation runtime."""

    def calculate(self, request: "PreparedCalculationRequest") -> Mapping[str, Any]:
        """Return a complete design dossier for a validated frozen request."""


@dataclass(frozen=True)
class PreparedCalculationRequest:
    schema: str
    request_id: str
    mode: CalculationMode
    inputs: Mapping[str, Any]
    input_digest: str
    load_policy: Mapping[str, Any]


def _plain_json(value: Any) -> Any:
    """Return a detached JSON-compatible value or raise a useful error."""

    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise InvalidCalculationRequest(f"request inputs must be finite JSON data: {exc}") from exc


def json_safe(value: Any) -> Any:
    """Normalize calculation output into deterministic JSON-compatible data."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return "infinity" if value > 0 else "-infinity"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return json_safe(value.value)
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return json_safe(item_method())
        except (TypeError, ValueError):
            pass
    return str(value)


def calculation_source_provenance() -> dict[str, Any]:
    """Identify the exact calculation source used for a returned result."""

    root = Path(__file__).resolve().parent
    source_hashes = {}
    for name in (
        "main.py",
        "loads.py",
        "wind.py",
        "member_checks.py",
        "portalcalc_service.py",
        "portalcalc_headless.py",
        "configurator_review.py",
        "future_canopy_design.py",
    ):
        path = root / name
        if path.is_file():
            source_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"source_file_sha256": source_hashes}


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _number(value: Any, field: str, *, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidCalculationRequest(f"{field} must be a number") from exc
    if not math.isfinite(result):
        raise InvalidCalculationRequest(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise InvalidCalculationRequest(f"{field} must be at least {minimum:g}")
    return result


def calculate_snow_loads(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the same snow calculation currently used by the desktop engine."""

    loads = inputs.get("loads") or {}
    geometry = inputs.get("geometry") or {}
    wind = inputs.get("wind") or {}
    region = str(loads.get("snow_region", "None"))
    if region == "None":
        return {
            "region": region,
            "enabled": False,
            "k1": 0.0,
            "kp": 0.0,
            "mu": 0.0,
            "sg_kpa": 0.0,
            "ultimate_kpa": 0.0,
            "service_kpa": 0.0,
        }
    if region not in SNOW_REGION_K1:
        raise UnsupportedCalculationInput(f"loads.snow_region {region!r} is not supported")
    importance_value = _number(wind.get("importance_level", 2), "wind.importance_level", minimum=1)
    if not importance_value.is_integer():
        raise InvalidCalculationRequest("wind.importance_level must be an integer")
    importance = int(importance_value)
    if importance not in {1, 2, 3, 4}:
        raise UnsupportedCalculationInput("wind.importance_level must be 1, 2, 3 or 4")
    h0_m = _number(loads.get("snow_ahd_height_m", 0.0), "loads.snow_ahd_height_m", minimum=0.0)
    pitch_deg = _number(geometry.get("roof_pitch_deg"), "geometry.roof_pitch_deg")
    kp = {1: 1.4, 2: 1.5, 3: 1.65, 4: 1.85}[importance]
    k1 = SNOW_REGION_K1[region]
    mu = max(min(0.7 * (60.0 - pitch_deg) / 50.0, 0.7), 0.0)
    sg_kpa = max(kp * k1 * ((2.8 * h0_m) / 1000.0 - 1.2), 0.0)
    ultimate_kpa = mu * sg_kpa
    return {
        "region": region,
        "enabled": True,
        "importance": importance,
        "h0_m": h0_m,
        "pitch_deg": pitch_deg,
        "k1": k1,
        "kp": kp,
        "mu": mu,
        "sg_kpa": sg_kpa,
        "ultimate_kpa": ultimate_kpa,
        "service_kpa": ultimate_kpa / 1.5,
    }


def _enforce_linked_chords(inputs: dict[str, Any]) -> None:
    sections = inputs.get("sections")
    if not isinstance(sections, dict):
        raise InvalidCalculationRequest("inputs.sections must be an object")
    pairs = (
        ("top_chord", "bottom_chord"),
        ("left_canopy_top_chord", "left_canopy_bottom_chord"),
        ("right_canopy_top_chord", "right_canopy_bottom_chord"),
    )
    for top_key, bottom_key in pairs:
        top = sections.get(top_key)
        bottom = sections.get(bottom_key)
        if top in (None, "") and bottom in (None, ""):
            if top_key == "top_chord":
                raise InvalidCalculationRequest("sections.top_chord is required")
            continue
        adopted = top if top not in (None, "") else bottom
        if top not in (None, "") and bottom not in (None, "") and str(top) != str(bottom):
            raise UnsupportedCalculationInput(
                f"{top_key} and {bottom_key} must match because PortalCalc links those chords"
            )
        sections[top_key] = str(adopted)
        sections[bottom_key] = str(adopted)


def _normalize_calculation_selections(inputs: dict[str, Any]) -> None:
    selections = inputs.get("calculation_service")
    if selections is None:
        return
    if not isinstance(selections, dict):
        raise InvalidCalculationRequest("inputs.calculation_service must be an object")
    panel_count = selections.get("main_truss_panel_count")
    if panel_count is not None:
        count = _number(panel_count, "calculation_service.main_truss_panel_count", minimum=4)
        geometry = inputs.get("geometry") or {}
        if not count.is_integer() or (geometry.get("roof_shape", "Gable") == "Gable" and int(count) % 4):
            raise InvalidCalculationRequest("main_truss_panel_count must be an integer, divisible by 4 for a gable")
        if geometry.get("frame_system", "Truss") != "Truss":
            raise UnsupportedCalculationInput("main_truss_panel_count applies only to truss frames")
        selections["main_truss_panel_count"] = int(count)
    assignments = selections.get("auto_web_profile_assignments", {})
    if not isinstance(assignments, dict):
        raise InvalidCalculationRequest(
            "inputs.calculation_service.auto_web_profile_assignments must be an object"
        )
    normalized_assignments = {}
    for group, members in assignments.items():
        group = str(group)
        if group not in WEB_ASSIGNMENT_GROUPS:
            raise UnsupportedCalculationInput(f"unsupported automatic web assignment group {group!r}")
        if not isinstance(members, dict):
            raise InvalidCalculationRequest(f"automatic web assignments for {group} must be an object")
        normalized_members = {}
        for member, profile in members.items():
            try:
                member_id = str(int(member))
            except (TypeError, ValueError) as exc:
                raise InvalidCalculationRequest(
                    f"automatic web assignment member {member!r} in {group} must be an integer"
                ) from exc
            profile_name = str(profile or "").strip()
            if not profile_name:
                raise InvalidCalculationRequest(
                    f"automatic web assignment member {member_id} in {group} needs a profile"
                )
            normalized_members[member_id] = profile_name
        normalized_assignments[group] = normalized_members
    selections["auto_web_profile_assignments"] = normalized_assignments
    doubler_sides = selections.get("auto_web_joint_doubler_sides", {})
    if not isinstance(doubler_sides, dict):
        raise InvalidCalculationRequest(
            "inputs.calculation_service.auto_web_joint_doubler_sides must be an object"
        )
    normalized_sides = {}
    for group, sides in doubler_sides.items():
        group = str(group)
        if group not in WEB_ASSIGNMENT_GROUPS:
            raise UnsupportedCalculationInput(f"unsupported web-joint doubler group {group!r}")
        if not isinstance(sides, (list, tuple, set)):
            raise InvalidCalculationRequest(f"web-joint doubler sides for {group} must be an array")
        normalized_group_sides = []
        for side in sides:
            if not isinstance(side, (list, tuple)) or len(side) != 2:
                raise InvalidCalculationRequest(
                    f"each web-joint doubler side for {group} must be [member, chord]"
                )
            try:
                member_id = int(side[0])
            except (TypeError, ValueError) as exc:
                raise InvalidCalculationRequest(
                    f"web-joint doubler member {side[0]!r} in {group} must be an integer"
                ) from exc
            normalized_group_sides.append([member_id, str(side[1])])
        normalized_sides[group] = normalized_group_sides
    selections["auto_web_joint_doubler_sides"] = normalized_sides


def calculation_web_selection_state(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Decode JSON web selections into the current calculation-engine types."""

    selections = inputs.get("calculation_service") or {}
    assignments = {
        group: {int(member): profile for member, profile in members.items()}
        for group, members in (selections.get("auto_web_profile_assignments") or {}).items()
    }
    doubler_sides = {
        group: {(int(side[0]), str(side[1])) for side in sides}
        for group, sides in (selections.get("auto_web_joint_doubler_sides") or {}).items()
    }
    return {"assignments": assignments, "doubler_sides": doubler_sides}


def prepare_request(raw: Mapping[str, Any]) -> PreparedCalculationRequest:
    if not isinstance(raw, Mapping):
        raise InvalidCalculationRequest("request must be an object")
    schema = str(raw.get("schema", REQUEST_SCHEMA))
    if schema != REQUEST_SCHEMA:
        raise InvalidCalculationRequest(f"unsupported request schema {schema!r}")
    try:
        mode = CalculationMode(str(raw.get("mode")))
    except ValueError as exc:
        raise InvalidCalculationRequest("mode must be 'autoSize' or 'checkSpecified'") from exc
    request_id = str(raw.get("request_id") or "").strip()
    if not request_id:
        raise InvalidCalculationRequest("request_id is required")
    raw_inputs = raw.get("inputs")
    if not isinstance(raw_inputs, Mapping):
        raise InvalidCalculationRequest("inputs must be an object")
    inputs = _plain_json(raw_inputs)
    for key in ("geometry", "loads", "wind", "sections"):
        if not isinstance(inputs.get(key), dict):
            raise InvalidCalculationRequest(f"inputs.{key} must be an object")

    loads = inputs["loads"]
    for key in ("left_wall_kn_m", "right_wall_kn_m"):
        value = _number(loads.get(key, 0.0), f"loads.{key}")
        if abs(value) > 1e-12:
            raise UnsupportedCalculationInput(
                f"loads.{key} must be zero; additional wall line loads are not supported by the service"
            )
        loads[key] = 0.0

    _enforce_linked_chords(inputs)
    _normalize_calculation_selections(inputs)
    entered_q_kpa = abs(_number(loads.get("q_kpa"), "loads.q_kpa", minimum=0.0))
    snow = calculate_snow_loads(inputs)
    load_policy = {
        "entered_live_load_kpa": entered_q_kpa,
        "snow": snow,
        "effective_live_or_snow_kpa": max(entered_q_kpa, snow["service_kpa"]),
        "rule": "max(abs(Q), snow service pressure)",
    }
    frozen_inputs = _plain_json(inputs)
    return PreparedCalculationRequest(
        schema=schema,
        request_id=request_id,
        mode=mode,
        inputs=frozen_inputs,
        input_digest=_digest(frozen_inputs),
        load_policy=load_policy,
    )


class PortalCalcCalculationService:
    """Validate, freeze, execute and package one PortalCalc calculation."""

    def __init__(self, backend: CalculationBackend):
        self._backend = backend

    def run(self, raw_request: Mapping[str, Any]) -> dict[str, Any]:
        request = prepare_request(raw_request)
        dossier = json_safe(dict(self._backend.calculate(request)))
        if not dossier:
            raise RuntimeError("PortalCalc backend returned an empty dossier")
        dossier.setdefault("schema", DOSSIER_SCHEMA)
        dossier.setdefault("portalcalc_version", APP_VERSION)
        dossier["request_id"] = request.request_id
        dossier["request_digest"] = request.input_digest
        dossier["mode"] = request.mode.value
        dossier["load_policy"] = json_safe(request.load_policy)

        governing = dossier.get("overall_governing") or {}
        verified = bool(dossier.get("verified", governing.get("passes_ratio_screen", False)))
        sizing = json_safe((dossier.get("inputs") or request.inputs).get("sections", {}))
        result = {
            "schema": RESULT_SCHEMA,
            "request_id": request.request_id,
            "status": "proposal" if request.mode is CalculationMode.AUTO_SIZE else "checked",
            "verified": verified,
            "sizing": sizing,
            "checks": {
                "overall_governing": json_safe(governing),
                "members": json_safe(dossier.get("member_checks", [])),
                "serviceability": json_safe(dossier.get("serviceability_checks", [])),
            },
            "warnings": json_safe(dossier.get("warnings", [])),
            "dossier": dossier,
            "provenance": {
                "portalcalc_version": APP_VERSION,
                "portalcalc_release_date": APP_RELEASE_DATE,
                "request_schema": request.schema,
                "result_schema": RESULT_SCHEMA,
                "request_digest_sha256": request.input_digest,
                "calculated_at": datetime.now(timezone.utc).isoformat(),
                "mode": request.mode.value,
                "engine": json_safe(dossier.get("engine_provenance", {})),
                "calculation_policy": {
                    "linked_chords": True,
                    "additional_wall_line_loads": "rejected unless zero",
                    "live_vs_snow": json_safe(request.load_policy),
                    "decision_authority": "proposal-only; human review required",
                },
            },
        }
        # Keep this guarantee at the application boundary, where NumPy scalars
        # and calculation dataclasses must not leak into an adapter response.
        json.dumps(result, sort_keys=True, allow_nan=False)
        return result


def error_result(request_id: str, exc: Exception) -> dict[str, Any]:
    """Build an adapter-safe rejected result without swallowing the exception."""

    code = exc.code if isinstance(exc, CalculationServiceError) else "calculation_failed"
    return {
        "schema": RESULT_SCHEMA,
        "request_id": request_id,
        "status": "rejected" if isinstance(exc, CalculationServiceError) else "failed",
        "verified": False,
        "error": {"code": code, "message": str(exc)},
    }
