# PortalCalc Calculation Service

The application-service contract is implemented in `portalcalc/service/contract.py`.
It accepts one complete preset-shaped input object and supports two modes:

- `autoSize`: produce a sizing proposal and a full checked dossier.
- `checkSpecified`: freeze the supplied sections and rerun the checks.

Building bays come from `geometry.bays_mm` (e.g. `"7*8000, 7000"`) when present;
otherwise from `wind.building_length_m` and `loads.bay_size_mm` (full bays,
then a shorter last bay). `loads.bay_size_*` is the design (longest) bay.

The contract rejects non-zero additional wall line loads, rejects conflicting
linked chord selections, and records the effective `max(Q, snow service)` load
policy in the result provenance. Every response is proposal-only and requires a
human engineering review before quote, Tekla or rendering release.

`portalcalc/service/headless.py` is the backend. It runs the request on
`HeadlessFrameEngine` (`portalcalc/frame/engine/headless.py`): the frame engine with
plain input fields, no window and no Qt. It shows no dialogs and does not write
optimiser learning. `tests/headless_engine_regression.py` holds it to the desktop
window result for result.

Sizing proposals now include `inputs.calculation_service.main_truss_panel_count`
alongside automatic web assignments. Supply that optional field on
`checkSpecified` to reproduce the adopted truss topology before restoring web
member IDs. Counts must be at least four and divisible by four for gable trusses.
Ordinary desktop preset loading does not currently consume the service's
automatic web assignments.

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
from portalcalc.service import calculate

result = calculate({
    "schema": "portalcalc.calculation.request.v1",
    "request_id": "configurator-job-123",
    "mode": "autoSize",
    "inputs": complete_portalcalc_preset,
})
```

`calculate` wraps `PortalCalcCalculationService(HeadlessPortalCalcBackend()).run(request)`;
use those two classes directly to supply a different backend.

Run the frozen real-engine regression separately from the quick smoke suite:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python tests/calculation_service_regression.py --mode both
```

## v2: a 3D building with per-gridline loads

`portalcalc.calculation.request.v2` replaces `inputs` with a `building`
(`portalcalc.building.v1`, see `portalcalc/building/model.py` and
`BUILDING_LOADS_3D_PLAN.md`):

```python
result = service.run({
    "schema": "portalcalc.calculation.request.v2",
    "request_id": "configurator-job-123",
    "mode": "autoSize",                      # or "checkSpecified"
    "building": building_model_dict,
    "options": {"load_basis": "building"},   # default; "legacy_2d" = today's loads per gridline
})
```

**Preparation.**
- The building is validated (`InvalidCalculationRequest` names the field).
- Canopies and lean-tos are supported, full or part length.
- **Lightweight propped end frames** (`propped_end` frame type) are
  supported.
  - Their props become pinned internal columns at `prop_x_mm`, measured
    from the left column centreline.
  - Each prop is also checked for frame axial force combined with end-wall
    wind bending (`MainWindow.propped_mullion_check`).
- Features the engines don't size yet are rejected with
  `UnsupportedCalculationInput`: eave-beam-offset attachments, skillion
  roofs with attachments, and partitions.
- **Sizing groups.** Each one is a separate 2D frame; its gridlines must share
  a frame type and attachments. The default is one group per distinct frame,
  so a part-length lean-to makes its own group. BOLT may split further, e.g.
  end frames from interior frames.
  - Each group's frame preset comes from its widest-tributary gridline (the
    middle one among equals), with the group's `frame_inputs` applied. All
    v1 policy applies to it: linked chords, zero wall loads, and
    max(Q, snow).
  - The largest group's preset is the request's primary `inputs`.
- The request digest freezes the building and the options.

**Calculation.**
- The headless backend sets `MainWindow.building_load_mode`, so every
  gridline's own loads are checked.
- `autoSize` sizes against the governing combinations and accepts only on a
  full-set check.

**Result** (`portalcalc.calculation.result.v2`): everything v1 returns,
plus `building`:
- `gridlines`: id, y, frame type, sizing group, tributary, sections;
- `sizing_groups`;
- `distinct_load_sets`;
- `combinations` (`checked`, `total`, `full_set_checked`);
- `governing`: every enveloped action with value, combination and gridlines;
- `sized_building`: the request building with the sized sections, truss
  depth and web/topology state written back. Resubmit it as
  `checkSpecified` to reproduce the result.
  - With one group they go into the frame type, with canopy and lean-to
    sections and depth on their attachments.
  - With several groups each group carries its own `frame_inputs`.

**Several groups.** Each group is checked or sized on its own gridlines'
loads, with the screened optimiser running per group.
- `sizing` and `inputs` are the largest group's.
- `checks.overall_governing` is the worst group's
  (`dossier.overall_governing_group`).
- `verified` requires every group to pass.
- `dossier.group_dossiers` holds each group's sections, checks and
  optimiser report.
- `building.combinations.by_group` gives each group's combination counts.
- Every `governing` entry names its `sizing_group`.

Configurator Review accepts single-group v2 requests only.

**Configurator Review** opens v2 requests in building-load mode and returns
its final check as a v2 result. `configurator_review.reviewed_building`
applies only the reviewer's selections.

v1 requests, results and digests are unchanged
(`tests/calculation_service_regression.py`). The v2 tests are in
`tests/building_service_regression.py`.

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
python tests/configurator_review_regression.py
```

Add `--with-optimiser` to include the slower real optimiser/restoration path.
