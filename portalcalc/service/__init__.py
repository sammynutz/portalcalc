"""The calling interface for other applications (BOLT, the configurator).

One request in, one result out, both plain JSON-shaped dictionaries::

    from portalcalc.service import calculate

    result = calculate({
        "schema": "portalcalc.calculation.request.v1",
        "request_id": "job-123",
        "mode": "autoSize",            # or "checkSpecified"
        "inputs": preset,              # a complete preset, e.g. data/presets/factory_defaults.json
    })

``contract`` validates and freezes the request and packages the result; it has
no Qt dependency. ``headless`` is the backend that runs the calculation.
See docs/CALCULATION_SERVICE.md for the request and result schemas.
"""

from typing import Any, Mapping

from portalcalc.service.contract import (
    CalculationMode,
    CalculationServiceError,
    InvalidCalculationRequest,
    PortalCalcCalculationService,
    UnsupportedCalculationInput,
    prepare_request,
)


def calculate(request: Mapping[str, Any]) -> dict[str, Any]:
    """Run one calculation request (v1 single frame or v2 building) and return the result envelope."""
    from portalcalc.service.headless import HeadlessPortalCalcBackend

    return PortalCalcCalculationService(HeadlessPortalCalcBackend()).run(request)


__all__ = [
    "CalculationMode",
    "CalculationServiceError",
    "InvalidCalculationRequest",
    "PortalCalcCalculationService",
    "UnsupportedCalculationInput",
    "calculate",
    "prepare_request",
]
