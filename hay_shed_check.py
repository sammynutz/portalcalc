"""Reload and exactly check saved hay-search calculation-service inputs."""
import argparse
import copy
import json
from pathlib import Path

from hay_shed_search import atomic_json, initialize_worker, secondary_verified
from portalcalc_headless import HeadlessPortalCalcBackend
from portalcalc_service import PortalCalcCalculationService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preset", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = (args.output or args.preset.with_suffix(".checked.json")).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    initialize_worker(str(output.parent / "verification_runtime"))
    inputs = json.loads(args.preset.read_text(encoding="utf-8"))
    if (inputs.get("hay_shed_search") or {}).get("future_canopy_m"):
        from hay_shed_backend import FutureCanopyBackend
        backend = FutureCanopyBackend()
    else:
        backend = HeadlessPortalCalcBackend()
    service = PortalCalcCalculationService(backend)
    result = service.run(
        {"request_id": "hay-shed-exact-reload", "mode": "checkSpecified", "inputs": inputs})
    result["verified"] = result["verified"] and secondary_verified(result["dossier"])
    if (inputs.get("hay_shed_search") or {}).get("future_canopy_m"):
        initial_inputs = copy.deepcopy(inputs)
        initial_inputs["canopies"]["right_type"] = "None"
        selections = initial_inputs.get("calculation_service", {})
        for key in ("auto_web_profile_assignments", "auto_web_joint_doubler_sides"):
            selections[key] = {group: value for group, value in selections.get(key, {}).items() if group == "WEB"}
        initial = service.run({"request_id": "hay-shed-initial-reload", "mode": "checkSpecified", "inputs": initial_inputs})
        result["future_verified"] = result["verified"]
        result["initial_state"] = initial
        initial["verified"] = initial["verified"] and secondary_verified(initial["dossier"])
        result["verified"] = result["verified"] and initial["verified"]
        costs = result["dossier"]["costing_summary"]["costs"]
        result["paired_main_cost"] = sum(max(value, initial["dossier"]["costing_summary"]["costs"].get(key, 0))
                                        for key, value in costs.items())
    atomic_json(output, result)
    print(f"Passing: {result['verified']}; {result['checks']['overall_governing']}")
    print(f"Modelled steel: ${result['dossier']['costing_summary']['total_cost']:,.2f}; saved {output}")
    if not result["verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
