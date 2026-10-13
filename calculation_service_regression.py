"""Frozen end-to-end regression for the PortalCalc 1.5 service seam.

This intentionally runs the real offscreen desktop-compatible backend. It is
kept separate from quick smoke checks because ``autoSize`` is comparatively
expensive.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from portalcalc_headless import HeadlessPortalCalcBackend
from portalcalc_service import PortalCalcCalculationService


BASELINE_INPUT_DIGEST = "d27bc5545310f2dfaa95b316d3e595b28291d737fd535297412eda925d77ccad"
# Updated after retaining independent compression/tension forces in the web
# envelope. The request remains identical; separate native regression replays
# the former false pass and asserts that its buckling failure is now detected.
BASELINE = {
    "checkSpecified": {
        "verified": False,
        "ratio": 1.757758142975726,
        "group": "BOTTOM",
        "check": "Doubled chord zone",
        "sections": {
            "left_column": "310 UB 40.4 (40.9 kg/m)",
            "right_column": "310 UB 40.4 (40.9 kg/m)",
            "top_chord": "65*3 SHS (5.7 kg/m)",
            "bottom_chord": "65*3 SHS (5.7 kg/m)",
            "webs_posts": "50*2 SHS (2.9 kg/m)",
        },
    },
    "autoSize": {
        "verified": True,
        "ratio": 0.9815093042284594,
        "group": "LEFT_COLUMN",
        "check": "Combined",
        "sections": {
            "left_column": "250 UB 31.4 (31.5 kg/m)",
            "right_column": "250 UB 31.4 (31.5 kg/m)",
            "top_chord": "75*3 SHS (6.6 kg/m)",
            "bottom_chord": "75*3 SHS (6.6 kg/m)",
            "webs_posts": "50*2 SHS (2.9 kg/m)",
        },
    },
}


def run_mode(mode: str) -> dict:
    root = Path(__file__).resolve().parent
    inputs = json.loads((root / "presets" / "factory_defaults.json").read_text(encoding="utf-8"))
    service = PortalCalcCalculationService(HeadlessPortalCalcBackend())
    result = service.run({
        "request_id": f"frozen-1.5-{mode}",
        "mode": mode,
        "inputs": inputs,
    })
    expected = BASELINE[mode]
    assert result["provenance"]["request_digest_sha256"] == BASELINE_INPUT_DIGEST
    assert result["verified"] is expected["verified"]
    governing = result["checks"]["overall_governing"]
    assert governing["group"] == expected["group"]
    assert governing["check"] == expected["check"]
    assert math.isclose(governing["ratio"], expected["ratio"], rel_tol=1e-8, abs_tol=1e-10)
    for key, value in expected["sections"].items():
        assert result["sizing"][key] == value, (key, result["sizing"][key], value)
    json.dumps(result, sort_keys=True, allow_nan=False)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("checkSpecified", "autoSize", "both"),
        default="both",
    )
    args = parser.parse_args()
    modes = ("checkSpecified", "autoSize") if args.mode == "both" else (args.mode,)
    for mode in modes:
        result = run_mode(mode)
        governing = result["checks"]["overall_governing"]
        print(
            f"{mode}: verified={result['verified']} "
            f"governing={governing['group']} / {governing['check']} / {governing['ratio']:.6f}"
        )


if __name__ == "__main__":
    main()
