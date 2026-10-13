# PortalCalc Code Map

This document is a fast navigation guide for future edits. It is not a full
design spec; it points to the files and regions that usually matter first.

## App Shape

PortalCalc is a PySide6 desktop engineering tool with a packaged Windows build.
Most UI wiring, model generation, analysis orchestration, preset handling, 2D
plotting, 3D export, costing, optimisation and report text live in `main.py`.
Specialist calculations are gradually being split into smaller modules.

Generated or packaged output lives in `build/`, `dist/`, `release/` and
`__pycache__/`. Do not inspect those first unless the task is explicitly about
packaging output.

## Start Here

- `main.py`: customer desktop app entry point and main window.
- `smoke_checks.py`: fastest broad regression check before packaging.
- `PACKAGING.md`: release/build process.
- `CHANGELOG.md`: customer-facing release history.
- `app_metadata.py`: version, release date and display metadata.

## Source Files

| File | Purpose |
| --- | --- |
| `main.py` | Main PySide app, frame model builder, solver orchestration, UI state, presets, plotting, concept 3D, costing, optimisation and final report text. |
| `portalcalc_service.py` | Qt-free calculation request/result contract, validation, linked-chord and live/snow policy, frozen request hashing and proposal provenance. |
| `portalcalc_headless.py` | Offscreen compatibility backend that runs the current desktop calculation through the service contract while orchestration is extracted from `MainWindow`. |
| `calculation_service_regression.py` | Frozen end-to-end `checkSpecified` and `autoSize` regression against factory defaults. |
| `configurator_review.py` | Configurator Review CLI parsing, request loading, controlled-input/member-selection merge, reviewed/cancelled result envelopes and atomic JSON writing. |
| `configurator_review_regression.py` | Offscreen interactive regression for request loading, UI locking, section adjustment, final exact checking, failed-result return, cancellation and atomic output. |
| `concept_3d.py` | 3D concept data assembly, HTML preview output and Collada `.dae` export helpers. |
| `costing.py` | Frame, purlin/girt, crane runway and storage costing summary calculations/report text. |
| `loads.py` | Basic load dataclasses, load combinations, non-wind roof/wall/self-weight load application. |
| `wind.py` | Wind input dataclasses, AS/NZS 1170.2-style wind calculation helpers, Cpe/Cpi logic, wind load application to roof and columns. |
| `member_checks.py` | Steel member capacity checks and governing utilisation result objects. |
| `end_wall.py` | UI-independent end-wall mullion layout, pinned-member strength selection and H/150 deflection checks. |
| `crane_beam.py` | Crane runway beam input/result dataclasses and section checks. |
| `storage_loads.py` | Stored-material pressure/load calculations, storage panel design, storage load application and summary text. |
| `elevation_drawings.py` | PDF elevation/plan/section drawing export using Qt painting. |
| `finishes.py` | Cladding profile and COLORBOND finish lookup data. |
| `licensing.py` | Desktop license token storage, machine ID, local verification, activation and refresh client. |
| `structural_assistant.py` | Read-only OpenAI Responses API clients for full reviews, compact Guided Help and the Guided Optimise supervisor loop, stable snapshot hashing, strict tool schemas, usage/cost accounting and secure Windows API-key storage. |
| `license_server.py` | FastAPI license server, SQLite persistence, key generation, admin routes and CLI helpers. |
| `license_generator_app.py` | Admin-only PySide license generator/client. |
| `smoke_checks.py` | Compile checks plus non-visual and UI smoke tests. |

## Key Data Files

- `Shape_Catalog.xlsx`: section catalogue loaded by `ShapeCatalog` in `main.py`.
- `metroll_megaspan_capacity_tables.json`: purlin/girt capacity data loaded by `main.py`.
- `presets/factory_defaults.json`: packaged factory defaults.
- `presets/*.json`: sample/customer/job presets used for manual regression and debugging.
- `assets/CSB_logo.jpg`: drawing title block/logo asset.

## Main.py Landmarks

`main.py` is currently the high-context file. Use these landmarks before doing a
wide read:

- Lines 1-120: imports, environment settings, global constants and release text.
- Around 300: section catalogue model (`SectionProfile`, `ShapeCatalog`).
- Around 540: 2D finite-element primitives (`Node`, `FrameElement2D`, `Structure2D`).
- Around 965: custom wall envelope selector widgets; button contrast and rotated labels live here.
- Around 1151: 2D matplotlib plotting canvas.
- Around 1712: concept 3D viewers, including OpenGL rendering.
- Around 2492: `MainWindow` starts; the app-level combo-box mouse-wheel filter is installed here.
- Around 3176: `MainWindow.build_ui`, the main tab/control construction.
- Around 4390: engineering tooltips.
- Around 5008: storage load UI input conversion.
- Around 5229: crane beam UI/helper logic.
- Around 6070: preset/default file locations and optimisation learning paths.
- Around 6723: collect preset data.
- Around 7016: apply preset data.
- Around 7393: solve helpers and preset history.
- Around 7704: elevation drawing export wrapper; implementation is in `elevation_drawings.py`.
- Around 8161: costing/foundation wrappers; costing implementation is in `costing.py`.
- Around 8338: structural support/restraint/purlin/foundation helpers.
- Around 8647: wind input creation from UI.
- Around 8732: UI visibility/theme/license status updates.
- Around 8998: `generate_and_solve`, main model creation and analysis entry point.
- Around 11100: member/check envelope calculation across load combinations.
- Around 13050: auto-sizing candidate and optimisation logic.
- Around 15560: section check result text.
- Around 15860: thin costing text wrapper; implementation is in `costing.py`.
- Around 16760: foundation text.
- Around 16921: final results text.
- Around 17055: plot refresh.
- Around 17115: desktop app startup and license gate.

## Common Change Routes

For a new engineering input:

1. Add UI controls in `MainWindow.build_ui`.
2. Add tooltip text in `apply_engineering_tooltips` if user-facing.
3. Add preset save/load in `collect_preset_data` and `apply_preset_data`.
4. Add conversion helpers near similar input helpers.
5. Feed the value into `generate_and_solve` or the relevant specialist module.
6. Add smoke coverage in `smoke_checks.py`.

For a calculation-only change:

1. Prefer the specialist module (`wind.py`, `loads.py`, `storage_loads.py`,
   `crane_beam.py`, `member_checks.py`) if the logic can stay independent of UI.
2. Keep dataclass inputs/results plain and serialisable where practical.
3. Update `main.py` only at the UI conversion and orchestration boundary.
4. Add direct smoke checks that import the module without launching the full UI.

For Structural Assistant or Guided Help changes:

1. `structural_assistant.py` owns OpenAI request payloads, prompts, strict tool schemas, response parsing, hashing and usage accounting.
2. `MainWindow.assistant_help_snapshot` assembles the compact solved-design evidence; `assistant_help_member_economics` adds the cost/utilisation signals used to prioritise downsizing. Keep the full `assistant_design_dossier` for detailed reviews.
3. `MainWindow.assistant_execute_help_trial` applies Luna-selected changes only to an isolated base state, solves them through PortalCalc and restores the live design.
4. Keep Guided Help trials bounded and non-persistent; new trial controls require explicit validation, result compression and restoration smoke coverage.
5. `smoke_checks.py` mocks the network payload and performs a real offscreen trial/restoration check without making a live API call.

For Guided Optimise changes:

1. `structural_assistant.py` owns the bounded `run_optimisation_strategy` schema and supervisor prompt; Luna proposes macro search direction but never verifies or selects a design directly.
2. `MainWindow.assistant_run_full_optimiser` invokes the production optimiser with completion dialogs and learning writes suppressed for each branch.
3. `MainWindow.assistant_execute_optimisation_strategy` restores the deterministic baseline, runs one full strategy branch, compresses its verified evidence and restores the baseline before the next model turn.
4. `MainWindow.structural_optimise_turn_succeeded` adopts the cheapest fully verified incumbent and only then persists optimiser learning.
5. `MainWindow.assistant_unverified_concept_options` exposes suggestion-only end-wall portal restraint only when calculated serviceability evidence makes it relevant and at least one end wall is clad; these concepts are not counted as verified optimiser savings.
6. `MainWindow.set_guided_optimise_status` mirrors progress to both the Structural Assistant and Sections tabs; completion and failure also publish a concise dialog while the full audit remains in the assistant output.
   `start_structural_optimise_turn` must keep the worker success signal blocking-queued: production optimisation processes Qt events, so an ordinary queued success signal can allow `QThread.finished` to tear down the session before `pending_continuation` is recorded.
   `show_sections_ai_help_dialog` owns the Sections AI Help popup and reuses the standard review/explain/ask request path; mirror completed responses back into that dialog as well as the Structural Assistant tab.
7. `costing.end_wall_portal_restraint_item` keeps stiffness-path squares separate from material costing, allowing one physical X at every portal position with two diagonal runs per X. `costing_from_structure` prices rod/EA steel at the UB rate and includes the configured rod-size turnbuckle rate per run.

For Metroll section display changes, keep the stored table key such as `C/Z 200 19` unchanged. Route user-facing labels and reports through `MainWindow.metroll_section_display` so single/double spans render as C and continuous-lapped spans render as Z without breaking capacity or weight lookup.

For a preset/default change:

1. Check `collect_preset_data` and `apply_preset_data`.
2. Check default loading around `factory_defaults_paths`, `load_factory_defaults`
   and `load_default_inputs`.
3. Update `presets/factory_defaults.json` only when the packaged default should
   change.
4. Use old presets in `presets/` to guard backward compatibility.

For drawing/export changes:

1. `elevation_drawings.py` owns the elevation PDF.
2. `MainWindow.export_elevation_sheet` prepares data and metadata for that module.
3. `concept_3d.py` owns 3D concept data, HTML preview and Collada export output.

For costing changes:

1. `costing.py` owns the costing summary calculation and costing report text.
2. `main.py` still owns the UI controls, rates inputs and selected-profile helpers.
3. Keep new costing logic UI-independent where possible; pass plain summary data
   back to `main.py` instead of reading widgets deep inside helper functions.

For licensing changes:

1. Desktop behavior starts in `licensing.py`.
2. Startup enforcement is in `main.py` near the `if __name__ == "__main__"` block.
3. Server behavior is in `license_server.py`.
4. Admin desktop tooling is in `license_generator_app.py`.
5. Server deployment details are in `render.yaml` and `license_server_README.md`.

For packaging/release changes:

1. Update `app_metadata.py`.
2. Update `CHANGELOG.md`.
3. Check `PortalCalc.spec`, `PortalCalcAdmin.spec` and `build_desktop.ps1`.
4. Follow `PACKAGING.md`.

## Verification

Useful local commands from the repo root:

```powershell
$env:QT_QPA_PLATFORM='offscreen'; python smoke_checks.py
python -m py_compile main.py member_checks.py loads.py wind.py smoke_checks.py licensing.py structural_assistant.py license_server.py license_generator_app.py crane_beam.py storage_loads.py elevation_drawings.py finishes.py
```

For release packaging:

```powershell
.\build_desktop.ps1 -Version <version>
```

Use `-SkipSmoke` only after a passing smoke run, as described in `PACKAGING.md`.

## Current Refactor Pressure

The largest token/time cost comes from `main.py` combining UI, model building,
analysis orchestration, exports, reports and optimisation. When touching a
feature repeatedly, consider extracting the calculation or pure data assembly
into a focused module first, then leave `main.py` as the UI/orchestration layer.

Good extraction candidates:

- Foundation reaction/design summaries.
- Preset schema conversion and backward compatibility.
- Auto-sizing/optimisation search logic.
- UI-independent purlin/girt checks.
