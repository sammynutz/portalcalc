# PortalCalc Calculation Service

The application-service contract is implemented in `portalcalc_service.py`.
It accepts one complete preset-shaped input object and supports two modes:

- `autoSize`: produce a sizing proposal and a full checked dossier.
- `checkSpecified`: freeze the supplied sections and rerun the checks.

The contract rejects non-zero additional wall line loads, rejects conflicting
linked chord selections, and records the effective `max(Q, snow service)` load
policy in the result provenance. Every response is proposal-only and requires a
human engineering review before quote, Tekla or rendering release.

`portalcalc_headless.py` is the isolated compatibility backend for the current
1.5 working calculation source. It creates no visible window, shows no dialogs,
and does not write optimiser learning. The service contract itself has no Qt
dependency. The remaining migration is to move model generation and optimiser
state out of `MainWindow`, then replace this adapter without changing the
configurator contract.

Sizing proposals now include `inputs.calculation_service.main_truss_panel_count`
alongside automatic web assignments. Supply that optional field on
`checkSpecified` to reproduce the adopted truss topology before restoring web
member IDs. Counts must be at least four and divisible by four for gable trusses.
The hay shed search checks every passing proposal again from its exported inputs
and requires the steel cost to match before ranking it. Use `hay_shed_check.py`
to reload those calculation-service inputs; ordinary desktop preset loading does
not currently consume the service's automatic web assignments.

Baseline identity for this extraction:

- Application version: 1.5
- Release date: 2026-08-14
- Repository HEAD when extraction began: `c7a1501bb732607ccefa0cf843e532d0ec1f90a0`
- The working tree already contained substantial unreleased calculation changes;
  therefore the SHA above identifies ancestry, not the exact working-source
  calculation baseline. Result provenance includes the app version and a SHA-256
  digest of every frozen request.

Minimal in-process usage:

```python
from portalcalc_headless import HeadlessPortalCalcBackend
from portalcalc_service import PortalCalcCalculationService

service = PortalCalcCalculationService(HeadlessPortalCalcBackend())
result = service.run({
    "schema": "portalcalc.calculation.request.v1",
    "request_id": "configurator-job-123",
    "mode": "autoSize",
    "inputs": complete_portalcalc_preset,
})
```

Run the frozen real-engine regression separately from the quick smoke suite:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python calculation_service_regression.py --mode both
```

## Interactive Configurator Review

PortalCalc can open an ordinary visible desktop session around a calculation
service request:

```powershell
PortalCalc.exe --review-request request.json --review-result result.json
```

Both arguments are required together. Without them PortalCalc starts normally.
Review mode displays **Estimate review — not engineering approval**, freezes the
configurator-owned geometry, loads, wind, enclosure and support inputs, and
allows member selections, truss depth and the deterministic optimiser. The
request's `geometry.truss_depth_mm` is the starting depth and
`geometry.max_truss_depth_mm` is an immutable Master Engine ceiling. Depth is
limited to 50 mm increments between PortalCalc's 3%-of-span minimum and that
ceiling. Optimiser truss depth may be adopted; every other configurator-owned
geometry value is restored before the exact solve.
The deterministic optimiser is queued automatically once the review window has
opened and the normal PortalCalc licence/startup gate has succeeded.

**Return reviewed estimate** performs a final `checkSpecified` run and atomically
replaces the result JSON. A failed or unchecked design can be returned, but the
result status and warnings identify it prominently. The result uses
`reviewStatus: "reviewedForEstimate"`; it never creates engineering-approval or
construction-release state. It also reports `selectedGeometry.trussDepthMm` and
`selectedGeometry.maxTrussDepthMm`. **Cancel** atomically writes a cancelled result.

Run the interactive workflow regression offscreen with:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python configurator_review_regression.py
```

Add `--with-optimiser` to include the slower real optimiser/restoration path.
