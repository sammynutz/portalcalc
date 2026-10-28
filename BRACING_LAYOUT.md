# Bracing location planning

Open **Bracing > Open 3D bracing workspace**. The default editing mode blocks
panels where bracing cannot go. Every unblocked panel remains available; it is
not a selected brace. Pin mode records a brace that a future layout search must
retain. A blocked panel cannot be pinned. Blocking a pinned panel removes the pin.

Click panels or select them in the list and use Toggle selected panel. Drag to
orbit, choose a preset view, or filter a face to reach hidden panels. Undo/Redo
and Clear apply within the workspace. Cancel discards dialog edits; Save records
the layout and section settings together in the normal preset/history. Configurator Review permits viewing
only. Geometry changes retain the original locations and flag them for review;
the explicit geometry-reset button clears them and can be undone before saving.

The view covers the main building only. It has roof strips, longitudinal wall
bays and front/back end-wall panels. Roof strips follow the end-wall stations
plus the gable ridge. End-wall brace endpoints follow actual mullion stations;
an intermediate ridge point is not an invented ground support. A final partial
longitudinal bay retains its actual length. These are geometric locations,
not analysis elements or a declaration that every location is structurally suitable.

## Saved contract and BOLT integration

The preset's optional `bracing.layout` object uses schema
`portalcalc.bracing-layout.v1`. Older presets omit it. The Qt-free model in
`bracing_layout.py` validates and serialises the same object, allowing BOLT to
supply constraints without depending on desktop widgets. BOLT transport and
automatic import are not connected yet.

```json
{
  "schema": "portalcalc.bracing-layout.v1",
  "geometry": {
    "span_mm": 21000,
    "length_mm": 40000,
    "eave_mm": 6000,
    "pitch_deg": 10,
    "roof_shape": "Gable",
    "bay_stations_mm": [0, 8000, 16000, 24000, 32000, 40000],
    "end_wall_stations_mm": [0, 7000, 14000, 21000]
  },
  "blocked_panels": ["left_wall/bay/1", "front_end/panel/1"],
  "pinned_panels": ["roof/bay/0/strip/0"]
}
```

Coordinates are millimetres: X across the portal from the left column, Y from
front to back, Z upwards. IDs use zero-based indices. Wall IDs are
`left_wall/bay/i` and `right_wall/bay/i`; roof IDs are `roof/bay/i/strip/j`;
end-wall IDs are `front_end/panel/j` and `back_end/panel/j`.
The explicit station arrays support unequal bays. IDs belong to the saved
geometry and must not be transplanted to a different grid. Unknown IDs,
overlapping blocked/pinned sets and invalid geometry are rejected.

## Fixed-layout analysis and intended next stage

The editor does not select an efficient layout or grant restraint. Saving
locations does not run a structural solve. **Analyse and check** or **Auto size
for lowest checked cost** in the same workspace analyse the pinned X panels.
See `BRACING_ANALYSIS.md` and `BRACING_DESIGN.md` for scope and assumptions.
The count-based Phase 1 bracing design, brace sizes, costing and
calibrated portal eave spring are unaffected.

**Invert left/right** and **Invert up/down** control the drag axes independently
and save with the workspace settings. The default uses opposite axis directions.
Force, utilisation and deformation views inspect members without editing
constraints. Switch back to Layout constraints to edit; a location change clears
previous analysis/check results.

The future automatic search should generate candidates from the complement of blocked
locations, retain pins, establish complete roof/strut/wall load paths and verify
them using the proposed reduced 3D analysis. The efficiency objective should
include brace and strut sizing and connection/installation cost, subject to
strength, stiffness and stability constraints. An infeasible constraint set must
report failure. It must never relax an exclusion or present the smallest brace
count as a verified optimum. Portal restraint coupling remains a separate
analysis integration step, with end-wall action in the portal plane distinguished
from longitudinal restraint.

Run `python bracing_layout_regression.py` for focused checks or
`python smoke_checks.py --full` for the broad suite. Set `QT_QPA_PLATFORM=offscreen`
for unattended UI checks.
