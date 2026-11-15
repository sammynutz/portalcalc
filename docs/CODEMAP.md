# PortalCalc Code Map

A navigation guide: where things live and which file to open first. It is not a
design spec; the feature notes beside it in `docs/` are.

Restructured **2026-11-15**: the flat scripts became the `portalcalc` package,
the 19,000-line `main.py` was split into a Qt-free frame engine and desktop UI
modules, and the 3D concept view and drawing-set export were removed. Results
were checked identical before and after (see `CHANGELOG.md`).

## Layout

| Path | What it holds |
| --- | --- |
| `portalcalc/frame/engine/` | The frame engine, one module per concern (see Frame Engine and Desktop Modules below). `FrameEngine` in `__init__.py` combines them. |
| `portalcalc/frame/sections.py`, `portalcalc/frame/solver.py`, `portalcalc/frame/common.py` | Section catalogue (`SectionProfile`, `ShapeCatalog`); 2D direct-stiffness solver (`Node`, `FrameElement2D`, `Structure2D`); shared constants, lookup tables and small helpers. |
| `portalcalc/desktop/main_window.py` and the mixins beside it | The Qt window: form construction (`ui_build.py`), menus and dialogs (`menus.py`), preset files and undo (`preset_files.py`), Configurator Review (`review_ui.py`), visibility/theme/plot refresh (`view_state.py`), bracing dialogs (`bracing_actions.py`), custom widgets (`widgets.py`, `plot_canvas.py`) and branding (`branding.py`). |
| `portalcalc/` | The package. Everything outside `portalcalc/desktop/` is Qt-free. |
| `portalcalc/service/` | **The calling interface for BOLT**: `calculate(request)`, the request/result contract, the headless backend and the Configurator Review helpers. |
| `portalcalc/frame/` | The 2D frame: sections, solver, frame loads, optimiser screen, and `engine/` (model generation, checks, sizing, reports). |
| `portalcalc/bracing/` | 3D bracing: layout, analysis, design and the sizing search. |
| `portalcalc/purlins/` | Purlin and purlin-strut design shared by the frame and the bracing. |
| `portalcalc/building/` | Whole-building model and per-gridline loads (`portalcalc.building.v1`). |
| `portalcalc/sizing/` | Combined frame + bracing sizing. |
| `portalcalc/reports/` | Computation report data and PDF. |
| `portalcalc/desktop/` | The PySide6 application: main window, 3D bracing workspace, licence admin app. |
| `portalcalc/studies/` | One-off study tools (hay shed search, future solar). The app does not use them. |
| `portalcalc/data/` | Section catalogue, purlin capacity tables, factory defaults, EULA and brand assets. |
| `portalcalc/*.py` | Shared engineering modules: `wind`, `loads`, `member_checks`, `storage_loads`, `crane_beam`, `end_wall`, `costing`, `bay_layout`, plus `licensing`, `resources` and `app_metadata`. |
| `tests/` | Regression scripts (`*_regression.py`), `smoke_checks.py` and fixtures. Run them with `python run_tests.py`. |
| `docs/` | Feature design notes, packaging and backup notes. |
| `tools/` | Developer scripts (brand asset generator). |
| `license_server.py`, `render.yaml`, `requirements.txt` | The deployed licence API (Render). They stay at the root because the deployment expects them there. |

## App Shape

PortalCalc is a PySide6 desktop engineering tool with a packaged Windows build,
and an engine that BOLT can call directly.

- `portalcalc.frame.engine.FrameEngine` builds the frame model from the inputs,
  solves it, checks members and connections, sizes sections and writes the
  reports. It imports no Qt. It reads its inputs from input-field objects through
  their plain accessors (`text()`, `currentText()`, `isChecked()`, `value()`) and
  talks to a user interface only through the hooks in
  `portalcalc/frame/engine/host.py`.
- `portalcalc.desktop.main_window.MainWindow` is that engine plus the Qt user
  interface, which lives in the mixin modules in `portalcalc/desktop/`.
- `portalcalc.service.calculate(request)` is the entry point for other
  applications. Its backend (`portalcalc/service/headless.py`) still creates an
  offscreen `MainWindow` to supply the input fields; see Current Refactor Pressure.

Generated or packaged output lives in `build/`, `dist/`, `release/` and
`__pycache__/`. Do not inspect those first unless the task is about packaging.

## Start Here

Release packaging is performed only when the user explicitly asks. Routine changes should remain source changes with appropriate checks.

- `main.py`: launcher. `portalcalc/desktop/app.py`: startup, splash and licence gate.
- `portalcalc/desktop/main_window.py`: the window class and its UI hooks.
- `portalcalc/frame/engine/solve.py`: model generation and the envelope solve.
- `portalcalc/service/__init__.py`: how another application calls PortalCalc.
- `tests/smoke_checks.py`: fastest broad regression check before packaging.
- `docs/PACKAGING.md`: release/build process.
- `CHANGELOG.md`: customer-facing release history.
- `portalcalc/app_metadata.py`: version, release date and display metadata.

## Source Files

| File | Purpose |
| --- | --- |
| `portalcalc/frame/engine/` | The frame engine, one module per concern (see Frame Engine and Desktop Modules below). `FrameEngine` in `__init__.py` combines them. |
| `portalcalc/frame/sections.py`, `portalcalc/frame/solver.py`, `portalcalc/frame/common.py` | Section catalogue (`SectionProfile`, `ShapeCatalog`); 2D direct-stiffness solver (`Node`, `FrameElement2D`, `Structure2D`); shared constants, lookup tables and small helpers. |
| `portalcalc/desktop/main_window.py` and the mixins beside it | The Qt window: form construction (`ui_build.py`), menus and dialogs (`menus.py`), preset files and undo (`preset_files.py`), Configurator Review (`review_ui.py`), visibility/theme/plot refresh (`view_state.py`), bracing dialogs (`bracing_actions.py`), custom widgets (`widgets.py`, `plot_canvas.py`) and branding (`branding.py`). |
| `portalcalc/service/contract.py` | Qt-free calculation request/result contract, validation, linked-chord and live/snow policy, frozen request hashing and proposal provenance. |
| `portalcalc/service/headless.py` | Offscreen compatibility backend that runs the current desktop calculation through the service contract while orchestration is extracted from `MainWindow`. |
| `tests/calculation_service_regression.py` | Frozen end-to-end `checkSpecified` and `autoSize` regression against factory defaults. |
| `tests/building_service_regression.py` | Includes `ProppedMullionTests`: `MainWindow.propped_mullion_check` (propped end-frame props: frame axial plus end-wall wind via `end_wall.mullion_profile_design`, hooked into `member_and_connection_ratio` for INTERNAL_COLUMN when `propped_end_context()` is set). Service v2 (`portalcalc.calculation.request.v2` with a `building`). Covers the contract and rejections, unchanged v1 digest, `building_with_frame_inputs`/`reviewed_building`, real headless `checkSpecified` with per-gridline evidence and a resubmission round trip, legacy_2d ≥ v1, and v2 Configurator Review. v2 `autoSize` needs `PORTALCALC_SLOW_TESTS=1`. See `CALCULATION_SERVICE.md`. |
| `portalcalc/service/configurator_review.py` | Configurator Review CLI parsing, request loading, controlled-input/member-selection merge, reviewed/cancelled result envelopes and atomic JSON writing. |
| `tests/configurator_review_regression.py` | Offscreen interactive regression for request loading, UI locking, section adjustment, final exact checking, failed-result return, cancellation and atomic output. |
| `tests/eave_node_regression.py` | Eave height at the cladding pitching point: `MainWindow.eave_node_basis` (node = H + (g + D/2)tanθ − (p + t/2)/cosθ, purlin/girt from the early `purlin_check`/`wall_girt_check` made before the frame) and `apply_eave_strip_loads` (wall above the node and roof out to the cladding line as eave-node point loads, applied at the end of `apply_combination_loads`). Tests the formula, the skillion low side, the 200 mm default and the vertical load balance. |
| `portalcalc/bay_layout.py`, `tests/bay_layout_regression.py` | Qt-free bay list input (`geometry.bays_mm`, e.g. `7*8000, 7000`): parse/format, the legacy length + bay size conversion (full bays, shorter last bay), stations, length and the design bay (longest). `MainWindow.set_bays` derives the unshown building length and design bay fields from it; `bay_lengths_mm`/`bay_stations_mm` give the actual bays to the 3D layout, building model, costing and concept view. |
| `portalcalc/building/model.py`, `tests/building_model_regression.py` | Qt-free `portalcalc.building.v1` contract for BOLT. It covers site, main section, gridlines with frame types (including the `propped_end` lightweight end frame), canopies/lean-tos over gridline ranges, wall cladding by segment, openings (treated closed), partitions and sizing groups, with preset-shaped `design_inputs` passed through unchanged. `building_model_from_preset` turns a preset into a uniform building; `frame_preset(gridline)` goes back to a desktop preset; `engine_scope_issues` lists what the current engines can't represent. The tests cover validation, JSON round trip and offscreen desktop round trip (inputs, bracing `LayoutGeometry`, wind source directions). See `BUILDING_LOADS_3D_PLAN.md`. |
| `portalcalc/building/loads.py`, `tests/building_loads_regression.py` | Qt-free per-gridline `FrameLoadSet` load descriptors: roof gravity and wind from all four directions (phases 2–3), plus canopies and lean-tos, full or part length (phases 7a/7b; per-frame attachment tributaries, external/internal main-column split, `canopy_roof_scale`/`canopy_roof_kn_m`). Covers canopy gravity, the continuous roof across canopies, free-roof canopies, lean-to outer columns, and lean-to / connected / open-canopy chamber Cpi with the shared-column net envelope. Part-length attachments, skillion + attachment and partitions raise `BuildingLoadsScopeError`. Two bases: `legacy_2d` (today's Left/Right/End cases × tributary, exact parity with the frame engine) and `building` (Front/Back end wind with Table 5.3(A)/5.2(C) bands per frame at d = length, per-direction Md, Cpi from each opening's own position, per-frame Ka; Kc via `kc_rule`: the default `surface_count` (roof + clad column lines + internal when |Cpi| ≥ 0.4; ≥3 → 0.8, 2 → 0.9, 1 → 1.0), or `legacy`). `ExpectedDifferenceTests` attribute the building v 2D differences. Each frame gets its own tributary width (half bays at the ends); column tributaries come from clad wall segments via `frame_strip_integrals`; main-chamber Cpi is ported from the frame engine's Table 5.1(B) rule using open wall-segment areas; each direction uses its own source bearing. Wind descriptors are ultimate roof strips (`bands`, or `slopes` split at the actual apex), signed column line loads and Cpi lines, with `sls_scale`. The tests check end-half/conservation/partial-cladding/dominant-opening behaviour and an arrow-by-arrow parity with the frame engine's `single_load_arrows` for six buildings. Canopies, lean-tos and partitions raise `BuildingLoadsScopeError`. |
| `portalcalc/frame/frame_loading.py`, `tests/frame_loading_regression.py` | Phase 4 bridge from per-gridline loads to the frame engine's envelope checks: `FrameLoadCombination` (a `LoadCombination` carrying its gridline `FrameLoadSet`; name gets "@GL…", `base_name` keeps the template), `frame_load_combinations` (deduplicated per combination by gravity/wind signature), `frame_roof_loads`, `roof_strips_on_nodes` (snaps "slopes"/"uniform" to the actual node path). In the frame engine, the desktop "Frame loads" selector (`frame_load_basis_combo`, `FRAME_LOAD_BASIS_OPTIONS`, preset `wind.frame_load_basis` written only when not single frame; `building_load_summary_lines` adds a BUILDING LOADS results section) sets `MainWindow.building_load_mode` (`None` = today; `"building"`/`"legacy_2d"`), which makes `_generate_and_solve_once` check those combinations. `apply_combination_loads` applies a combination's own `frame_loads` via `apply_frame_wind_loads`, and `current_building_frame_loads` builds and caches them (or uses `building_model_override`). Tests: a legacy interior gridline reproduces today's envelope exactly; all gridlines only add demands; the building basis leaves gravity and side-wind actions unchanged.<br>**Phase 5 screening:** with a building mode on, `auto_size_current_preset` runs `_auto_size_with_building_screening`. It sizes with `_building_active_set` (the combinations governing the last full check plus all gravity), then gates on a full-set solve, adding newly governing combinations for up to 3 rounds. The original optimiser is `_auto_size_current_preset_once`; `building_screening_disabled` turns screening off. The report adds `governing_report`. `ScreeningTests` are fast; `OptimiserComparisonTests` (about 2 min) need `PORTALCALC_SLOW_TESTS=1`. |
| `portalcalc/costing.py` | Frame, purlin/girt, crane runway and storage costing summary calculations/report text. |
| `portalcalc/frame/purlin_selection.py` | Joint general/edge roof-purlin selection: shared profile with independent thicknesses, ranked by installed material weight; exports passing profile comparisons. |
| `portalcalc/loads.py` | Basic load dataclasses, load combinations, non-wind roof/wall/self-weight load application. |
| `portalcalc/wind.py` | Wind input dataclasses, AS/NZS 1170.2-style wind calculation helpers, Cpe/Cpi logic, wind load application to roof and columns. `open_canopy_cpi` is the open-canopy underside Cpi rule (Table 5.1(B) single opening) shared by the 2D frame and the 3D bracing loads. |
| `portalcalc/member_checks.py` | Steel member capacity checks and governing utilisation result objects. Optional separate compression-body area preserves legacy behaviour when unset. |
| `portalcalc/frame/column_flybracing.py` | Automatic girt flybrace selection for clad columns, cached exact segment demands and same-case am; no frame solves. |
| `tests/column_flybracing_regression.py` | Segment/load-case provenance, tall-column brace growth, LTB/in-plane separation, caching and export regression. |
| `portalcalc/end_wall.py` | UI-independent end-wall mullion layout, pinned-member strength selection and H/150 deflection checks. |
| `portalcalc/bracing/layout.py` | Qt-free versioned bracing geometry and blocked/pinned panel constraints for desktop/BOLT; no structural analysis. Open canopies (schema v2 only when present): canopy faces/panels, `strut_lines`/`canopy_line`, `BRACE_GROUPS`/`brace_group`, `canopy_bracing`/`canopy_bays` (brace canopies in the end roof-braced bays plus spacing-rule bays) and `carry_over` (keep main panels when only canopies change). Portal frame brace locations (`portal_panels`, schema v3 only when used) kept apart from X panels: `portal_braces`/`portal_blocked`/`available_portals`, with `selection`/`all_blocked` for both. Two-tier side-wall bays: `LayoutGeometry.wall_tier_z_mm` (crane runway level, not part of equality) and `wall_tier` (else tall bays over `WALL_TIER_ASPECT` split at mid-height); `BracingPanel.tier_z_mm`/`tier`, `brace_diagonals` (installed braces) and `tier_points` (`diagonals` stay full height for cladding diaphragms). See `BRACING_LAYOUT.md`. |
| `portalcalc/bracing/portal_brace.py`, `tests/bracing_portal_brace_regression.py` | Qt-free portal frame brace (UB moment frame in a side-wall bay, pinned bases): 2D frame condensed onto the two eave Y translations, recovery of N/V/M and base reactions, `PortalBraceCandidate` (UB with E·I sizing stiffness), closed-form check. Tests: layout rules, independent `Structure2D` comparison, network balance and stiffening, design checks/cost/allowance, auto-size and the spacing anchor. |
| `portalcalc/bracing/layout_search.py`, `tests/bracing_layout_search_regression.py` | Optimiser-only seeds `full_roof_seeds` (full roof every bay / anchor bays with both eave girders / alternate bays) and `portal_brace_seeds` (portal braces on a side wall whose end bays can't take X bracing). Practical end-load-path search with geometry-adaptive continuous eave roof girders, end-wall restraint and bounded extra-wall-bay rod alternatives and up to four compact installed-cost patterns; optional detailed cost search. Keeps passing input incumbents, blocked/required constraints and exact scoped strength/SLS/bare-stability acceptance. Fast sizing cost-orders local checks, ranks repeatable brace-EA steps and permits bounded brace balancing. See `BRACING_LAYOUT.md`. |
| `portalcalc/desktop/bracing_layout_ui.py` | Interactive Qt-painted 3D location editor, face filters (including canopy roof faces), local undo/redo and geometry review. |
| `tests/bracing_layout_regression.py` | Geometry/constraint validation, real canvas clicks, preset persistence, read-only review and no structural-credit regressions. |
| `portalcalc/bracing/analysis.py` | Axial 3D network, tension-only rod equilibrium, portal interface condensation/recovery, building assembly, integrated end-wall loads and reaction balance. |
| `portalcalc/bracing/analysis_desktop.py` | Actual portal/section adapter, simultaneous gravity/end-wind combinations, frozen result evidence and readable report. Scope guards (lean-tos, skillion + canopy, canopy eave override), `actual_canopies`, `canopy_edge_purlin` (installed-purlin record for the canopy edge strut) and `roof_friction_drag` surfaces (main underside when both ends are open, canopy top and underside). See `BRACING_ANALYSIS.md`. |
| `portalcalc/bracing/roof_loads.py` | Frame tributaries, G/Q/solar placements, end-wind normal roof/side-wall pressure patterns and full portal load snapshots, including canopy gravity/wind (`canopy_parts`) and free-roof Cpn for fully open structures (`fully_open`); independent full-matrix/load conservation checks in `tests/bracing_roof_loads_regression.py`. |
| `portalcalc/bracing/side_wind.py` | ULS and SLS (Ws, G + Ws) left/right wind, roof alternatives, internal pressure, varying end-wall suction, exact partial-member pressure integration and applicable roof friction. Roof bands run over the continuous roof from the windward canopy tip (`roof_cpe_segments(..., left_ext, right_ext)`). Independent statics/mirroring/full-matrix checks in `tests/bracing_side_wind_regression.py`. |
| `tests/bracing_transverse_sls_regression.py` | Factory-default transverse SLS scaling, per-frame sway/restraint evidence against an independent 2D solve, bare-network diaphragm exclusion and unchanged longitudinal acceptance. Also checks `wind.add_roof_panel_pressure` (roof wind normal to each panel) the banded Table 5.3(A) skillion high-side basis, and main/3D gable and skillion Ws sway agreement within 1%. |
| `portalcalc/bracing/restraint.py` | Qt-free adoption of the 3D frame restraint into the 2D portal: record from a checked result, hard (stale) and soft (recheck) fingerprints, status for the 2D solve and brace-stiffness floors for 3D sizing. |
| `portalcalc/sizing/combined.py` | Headless combined 2D portal + 3D bracing sizing over `MainWindow`: unrestrained 2D sizing, practical bracing, stiffer/eave-girder options, restraint adoption, 2D re-size, 3D recheck and total-cost ranking. |
| `portalcalc/sizing/frontier.py`, `tests/frontier_sizing_regression.py` | Frontier optimiser for the cheapest combined frame + bracing: lighter frames with bisected restraint requirements (check solves via `MainWindow.trial_sway_restraint_kn_mm`), a 3D cost/restraint frontier of bracing options, matching, and exact verification in rank order; the floor frame is tried with each option before any re-size. Tests use solver stand-ins. |
| `portalcalc/bracing/parallel.py` | Qt-free process pool for independent 3D checks/sizings sharing one context (sent once via a temp file); in-order results, serial fallback, `PORTALCALC_BRACING_WORKERS`. `size_cost_task` returns the sized cost too, so the optimiser can keep the cheaper of the practical-section and light-rod starts. Callers' scripts need a `__main__` guard. |
| `tests/bracing_restraint_regression.py` | Sway spring mechanics, adoption/staleness/recheck states, brace floors, 3D self-credit exclusion, deferred transverse SLS sizing and horizontal low-pitch roof bands. |
| `portalcalc/bracing/spacing.py` | Default five-bay project rule, complete roof/side-wall anchor assessment and bounded intermediate-bay planning. See `tests/bracing_spacing_regression.py`; small-screen/footer checks are in `tests/bracing_workspace_fit_regression.py`. |
| `portalcalc/bracing/diaphragm.py` | Revision B roof/wall reference calibrations (3/6 mm/kN), explicit empirical a/b² actual-panel c calculation or direct overrides; SLS equivalents, sloping-wall conversion, physical shear recovery and independent bare stability. Regression checks cover reference factors, geometry, reversal, open faces, ULS independence and saved UI behavior. |
| `portalcalc/bracing/calculation_cache.py`, `tests/bracing_cache_regression.py` | Compressed optional result cache with current-model/input/engine fingerprints; reopen/job persistence, stale/corrupt rejection, preserved failure diagnostics and input-hash separation. |
| `tests/bracing_serviceability_regression.py` | IL1 global H/80 versus local L/150, main-criterion following/overrides/cache invalidation, strength-only versus combined scope, grouped/mixed sizing and mandatory strength/stability checks. |
| `portalcalc/bracing/mullion_design.py`, `tests/bracing_alignment_regression.py` | Main/3D shared mullion wind-bending plus bracing-axial checks, native properties/restraint, baseline prerequisites and checked main adoption; eave tributary geometry, pressure-area scaling, costing and cached reopen regression. |
| `portalcalc/desktop/bracing_workspace_ui.py` | One-window selector, section/criterion inputs, visual results, cost sizing, member inspection and TXT/JSON export. Persistent failure categories and x1 governing-movement navigation use `portalcalc/bracing/sizing_diagnostics.py`; the canvas circles the governing SLS node. |
| `portalcalc/bracing/design.py` | Scoped tension/buckling/interaction and local deflection checks; material/fitting cost and reanalysed grouped catalogue sizing. Available-EA ceilings prevent false saturation stops; remaining solve budget checks cheaper/preferred groups with exact acceptance. The `canopy_brace` group is active only for buildings with a canopy (`active_groups`). `portalcalc/bracing/mixed_sizing.py` shares those helpers. See `BRACING_DESIGN.md`. |
| `portalcalc/bracing/policy.py`, `tests/bracing_policy_regression.py` | Agricultural wire/commercial rod preferences, mandatory rod eligibility on active unclad wall groups, whole-design preference/cost ranking and policy evidence; real-solve and UI regression checks using synthetic rope EA/prices only. |
| `tests/bracing_design_regression.py` | Independent capacity/cost workings, tiny exhaustive sizing comparison, real orbit/member picking and transactional workspace save/sizing. |
| `portalcalc/bracing/components.py`, `portalcalc/desktop/bracing_component_ui.py` | Per-location installed/added purlin and steel assemblies, axial/bending ratings, common-end force sharing and rope EA/price input. Wire stiffness defaults blank ratings to the explicit interim 200 GPa / nominal area assumption; declared EA overrides and the adopted basis are saved and exported. `portalcalc/bracing/schedule.py` holds supplied rope rates and the M10/M12 turnbuckle allowances, with price-source evidence and legacy migration. |
| `portalcalc/purlins/combined.py` | Adopted Lysaght May 1999 simplified compression/bending calculation; Metroll C/Z web-axis properties; calculated member tension using G450 minima and explicit net-area/kt assumptions, or preserved declared Nt; source evidence and worked output. |
| `portalcalc/purlins/shared_design.py` | User-adopted single-addition 50/50 run/added allocation and two-addition equal-per-ply allocation; existing-run/added single-span beam demands and explicitly preliminary table-reference checks. Unequal bays: the run uses the actual spans and every bay takes the longest bay's table reference; a bay longer than the installed purlins' checked span is rejected. Candidates/frozen checks enforce matching roof profile size; each added ply is checked and costed independently. |
| `portalcalc/purlins/strut_tables.py` | In-range manufacturer UDL-table interpolation with stated bridging and frozen source rows; separate single/double/five-span reference arrangements. |
| `tests/purlin_strut_regression.py` | Independent asymmetric two-span statics, single-span responses, load conservation/self-weight, table/reference limits, sizing, UI invalidation and persistence. |
| `portalcalc/purlins/beam.py`, `portalcalc/purlins/beam_design.py`, `portalcalc/purlins/end_local.py` | Roof-run Euler-Bernoulli model with separate Table 5.6 end-roof pressure patches, finite distributed cladding coupling, direct load pickup, single-span additions, demand evidence and gated M/V/axial checks; local fast screens recover only the changed full roof run, final exact checks recover all affected lines. |
| `portalcalc/desktop/purlin_beam_ui.py`, `tests/purlin_beam_regression.py` | Native moment/shear/deflection/transfer plots; independent beam and load-sharing benchmarks, whole-run sizing and workspace persistence tests. |
| `portalcalc/bracing/schedule.py` | Supplied Grade 300 rod and Grade 1570 rope factored capacities, exact turnbuckle sizes and source image evidence. |
| `portalcalc/bracing/mixed_sizing.py`, `tests/bracing_components_regression.py`, `tests/bracing_sizing_recovery_regression.py` | Cost-ordered per-location screens and bounded exact reanalysis; reject unstable brace downgrades and retry sized struts/posts with last verified braces within the solve cap. Detailed callers also compare coupled purlin/brace costs. Sharing, missing/stale ratings, transactional recovery, schedule capacities and saved UI regression. |
| `portalcalc/bracing/sizing_diagnostics.py` | Specific sizing-stop reasons and rejected alternatives; retains unchanged-input analysis and directs the workspace to the relevant rating row. |
| `tests/bracing_analysis_regression.py` | Closed-form mechanics, unreduced/reduced comparison, load/moment integration, missing-path failures and real offscreen desktop/export checks. |
| `tests/bracing_canopy_regression.py` | Open canopies and open-ended roofs in 3D: canopy Cpi parity with 2D, friction surfaces, open-ended/fully open loads, canopy gravity/wind per frame, layout faces/schema v2/carry-over, the canopy bracing rule, the `canopy_brace` group, network against the uncondensed matrix, guards, desktop solve, layout editor and Find layout (25 tests; smoke-check step "open canopies, open-ended roofs and roof friction"). |
| `bracing_analysis_desktop.end_wind_cases` | Base end-pressure cases use along-wind length/span Cp,e, contributing end/internal surface Kc and external roof friction. `building_roof_cases` adds simultaneous normal roof/side-wall and gravity actions plus ULS and SLS side-wind cases. Transverse SLS gives report-only per-frame sway (`transverse_sls_summary`); it is excluded from longitudinal sway acceptance and the stiffness screen. |
| `portalcalc/crane_beam.py` | Crane runway beam input/result dataclasses and section checks. `portal_crane_reactions` turns the wheel loads into portal column actions (R = P1 + P2 (L − a)/L; Pmax crab side / Pmin other side; ULS dynamic × hoisted factor, SLS static; lateral same way on both columns × inertial factor; runway G). Bracket constants: `CRANE_RAIL_CLEARANCE_MM`, `CRANE_BEAM_FACE_OFFSET_MM`, `CRANE_GAUGE_CHANGE_LIMIT_MM`. `crane_column_actions`/`apply_crane_actions` apply them at the stub mid-height nodes for both the frame engine (2D) and the 3D bracing model; `loads.CRANE_CASES`/`crane_case` define the crab/lateral combinations. |
| `tests/bracing_crane_regression.py` | 3D crane cases (`bracing_roof_loads.crane_cases`, set up by `bracing_analysis_desktop.crane_racking_setup` on the interior frame nearest mid-length), the crane rail racking check (`crane_racking_summary`, `design['crane_racking']` in `bracing_design.attach_design`), buffer impact cases (`bracing_roof_loads.buffer_cases`, `case['wall_tier_loads']` applied in `bracing_analysis.solve_building_case`), two-tier crane walls and the rods-only rule (`bracing_policy.crane_rods`). |
| `tests/bracing_wall_tier_regression.py` | Two-tier side-wall bays: `LayoutGeometry.wall_tier` (crane runway level `wall_tier_z_mm`, or tall bays over `WALL_TIER_ASPECT`), `BracingPanel.brace_diagonals` / `tier_points`, tier nodes, runway beam and `wall_tier_strut` members in `build_braced_building`, buffer statics; `bracing_diaphragm.default_settings` (stressed skin by importance level). |
| `portalcalc/storage_loads.py` | Stored-material pressure/load calculations, storage panel design, storage load application and summary text. |
| `portalcalc/reports/computation_report_data.py` | Frozen full-report evidence: actual case results, adopted wind bases, section/web selections and worked capacity values. |
| `portalcalc/reports/computation_report.py` | Paginated PDF calculation report, bookmarks/contents, tables and vector model/load/bending diagrams. See `COMPUTATION_REPORT.md`. |
| `tests/computation_report_regression.py` | Real-solver and PDF integrity checks for the full computation-report export. |
| `portalcalc/licensing.py` | Desktop license token storage, machine ID, local verification, activation and refresh client. |
| `license_server.py` | FastAPI license server, SQLite persistence, key generation, admin routes and CLI helpers. |
| `portalcalc/desktop/license_generator_app.py` | Admin-only PySide license generator/client. |
| `portalcalc/frame/preliminary_bracing.py` | Legacy UI-independent preliminary longitudinal bracing calculation; distinct from the explicit 3D network in `portalcalc/bracing/analysis.py`. |
| `portalcalc/frame/reduced_optimizer.py` | Qt-free physics-guided stiffness screening, catalogue frontiers, candidate projection and static condensation; full-engine checking remains the acceptance gate. |
| `tests/physics_optimiser_regression.py` | Real-frame reduced-screen evaluation and optional comparison with production optimisation. |
| `portalcalc/frame/future_canopy_design.py`, `tests/future_canopy_design_regression.py` | Merge primary-steel/reinforcement schedules and recheck the same physical frame before/after canopy installation. Desktop orchestration uses Qt. See `FUTURE_CANOPY_DESIGN.md`. |
| `portalcalc/studies/future_solar_design.py` | Four-state bare/canopy/solar/combined verification and bounded repair of one common schedule. See `FUTURE_SOLAR_DESIGN.md`. |
| `portalcalc/studies/hay_shed_backend.py` | Search-specific backend and build-now cost objective, preserving future-condition structural checks. |
| `portalcalc/studies/hay_shed_search.py`, `tests/hay_shed_search_regression.py` | Resumable process-parallel geometry search, exact proposal reload, ranking and policy regressions. See `HAY_SHED_SEARCH.md`. |
| `hay_shed_check.py`, `portalcalc/studies/hay_shed_orientation.py` | Saved-input exact rechecking and orientation-factor assessment respectively. |
| `hay_shed_pair.py`, `hay_shed_staged.py`, `hay_shed_fixed_bearing.py` | Paired solar/no-solar searches, staged geometry refinement and fixed-bearing actual-region verification. |
| `hay_shed_future_solar.py`, `hay_shed_optional_solar_report.py` | Four-state checking of search finalists and assembly of the completed-study report. |
| `hay_shed_pitch_bale_study.py`, `hay_shed_pitch_matched_check.py` | Separate pitch/storage study and matched-geometry checks against its saved leaders. |
| `hay_shed_report.py`, `hay_shed_bale_comparison.py`, `hay_shed_pitch_bale_report.py` | Study-specific report generation and storage-capacity ranking of saved results. |
| `tests/purlin_selection_regression.py` | Compatible general/edge profile selection and real roof-selection integration checks. |
| `tests/storage_load_path_regression.py`, `tests/wind_envelope_regression.py`, `tests/web_force_envelope_regression.py` | Focused storage transfer, directional wind and web force-envelope regressions. |
| `tests/smoke_checks.py` | Compile checks plus non-visual and UI smoke tests; selected specialist suites are also invoked here. Steps run in parallel processes (`--jobs`, `--jobs 1` serial) with single-threaded BLAS. |

## Key Data Files

- `portalcalc/data/Shape_Catalog.xlsx`: section catalogue loaded by `ShapeCatalog` in `portalcalc/frame/sections.py`.
- `portalcalc/data/metroll_megaspan_capacity_tables.json`: purlin/girt capacity data loaded by `load_metroll_tables`.
- `portalcalc/data/presets/factory_defaults.json`: packaged factory defaults.
- `tests/fixtures/*.json`: presets used by the regression scripts.
- `portalcalc/data/assets/CSB_logo.jpg`: CSB logo (source for the derived `csb_logo.png` / `csb_logo_white.png` used on the splash and About tab).
- `portalcalc/data/assets/portalcalc_logo*.{svg,png}`, `portalcalc/data/assets/portalcalc_icon*`: BOLT-family PortalCalc logo (navy and white variants) and app icon, generated by `tools/make_brand_assets.py`. Brand colours live in `portalcalc/app_metadata.py` (`BRAND_NAVY`, `BRAND_TEAL`).

## Frame Engine and Desktop Modules

Search by symbol name (`rg -n "def name"`) rather than by line number.

| Module | Holds | Typical symbols |
| --- | --- | --- |
| `portalcalc/frame/engine/state.py` | Calculation state and data tables | `init_state`, `clear_analysis_state`, `load_catalog`, `load_metroll_tables` |
| `portalcalc/frame/engine/inputs.py` | Reading and normalising inputs | `wind_inputs_from_ui`, `snow_loads_from_ui`, `set_bays`, `max_main_truss_depth_mm` |
| `portalcalc/frame/engine/presets.py` | Preset save/load and factory defaults | `collect_preset_data`, `apply_preset_data`, `factory_defaults_paths`, `load_factory_defaults` |
| `portalcalc/frame/engine/solve.py` | Model generation and the envelope solve | `generate_and_solve`, `_generate_and_solve_once`, `refine_main_truss_panel_topology`, `eave_node_basis` |
| `portalcalc/frame/engine/member_checks.py` | Load-case checking and envelope assembly | `member_check_envelope_lines`, `member_actions_from_envelope` |
| `portalcalc/frame/engine/web_connections.py`, `web_sizing.py` | Truss web connections and web sizing | `welded_chord_web_connection_checks`, `auto_size_two_web_assignments`, `apply_auto_web_joint_doublers` |
| `portalcalc/frame/engine/auto_size.py`, `optimiser.py` | Optimise (the only frame optimiser: stiffness screen, exact repair/refinement, depth sweep) | `run_physics_structure_optimisation`, `auto_size_depth_sweep`, `auto_size_current_preset`, `_auto_size_with_building_screening`, `run_structure_optimisation` |
| `portalcalc/frame/engine/secondary.py` | Purlins, girts, bridging | `purlin_check_from_inputs`, `match_metroll_purlin`, `build_purlin_layout` |
| `portalcalc/frame/engine/bracing.py` | End-wall and longitudinal bracing, 3D restraint adoption, propped ends | `calculate_longitudinal_bracing_design`, `adopt_bracing_3d_restraint`, `propped_mullion_check` |
| `portalcalc/frame/engine/storage.py`, `crane.py`, `foundations.py`, `serviceability.py` | Storage walls, crane runway beam, footings and costing, deflection limits | `storage_stub_design_for_profile`, `auto_select_crane_beam`, `foundation_design_from_envelopes`, `serviceability_limit_value` |
| `portalcalc/frame/engine/results.py` | Report text and the service dossier | `update_section_checks_text`, `update_results_text`, `calculation_design_dossier`, `capture_computation_report` |
| `portalcalc/frame/engine/host.py` | The hooks the engine calls on its host UI | `process_ui_events`, `notify`, `refresh_plot`, `update_*_visibility` |
| `portalcalc/desktop/ui_build.py` | Desktop construction | `build_ui`, `apply_engineering_tooltips` |
| `portalcalc/desktop/app.py` | Desktop startup and licence gate | `main` |

## Dependency Boundaries

- The Qt-free calculation modules include `portalcalc/loads.py`, `portalcalc/wind.py`,
  `portalcalc/member_checks.py`, `portalcalc/frame/column_flybracing.py`, `portalcalc/end_wall.py`, `portalcalc/storage_loads.py`,
  `portalcalc/crane_beam.py`, `portalcalc/frame/purlin_selection.py` and `portalcalc/frame/reduced_optimizer.py`.
- The newer bracing/purlin features generally split geometry, mechanics,
  design/policy, desktop adaptation and Qt widgets into separate modules.
  Shared purlin modules currently obtain common types from `portalcalc/bracing/analysis.py`.
- `portalcalc/service/contract.py` is a Qt-free request/result boundary. Its
  backend in `portalcalc/service/headless.py` still constructs an offscreen
  `MainWindow`, because the engine reads its inputs from the window's fields.
- `portalcalc/frame/` (including `engine/`) and every other package except
  `portalcalc/desktop/` import no Qt. `tests/building_model_regression.py` and the
  split tool's own check guard this for the building model and the engine.
- `portalcalc/frame/future_canopy_design.py` and the study tools in
  `portalcalc/studies/` orchestrate the existing engine; they are not a separate solver.
- `portalcalc/reports/computation_report.py` renders frozen evidence rather than
  solving a model.
- `portalcalc/bracing/design.py` dispatches locally to `portalcalc/bracing/mixed_sizing.py`, which
  imports shared helpers back from `portalcalc/bracing/design.py`. The delayed import
  avoids an eager import loop but the design dependency is still bidirectional.
- Desktop licensing, the FastAPI licence server and the admin desktop app have
  separate entry points and packaging responsibilities.

## Common Change Routes

For a new engineering input:

1. Add UI controls in `build_ui` (`portalcalc/desktop/ui_build.py`).
2. Add tooltip text in `apply_engineering_tooltips` if user-facing.
3. Add preset save/load in `collect_preset_data` and `apply_preset_data`.
4. Add conversion helpers near similar input helpers.
5. Feed the value into `generate_and_solve` or the relevant specialist module.
6. Add smoke coverage in `tests/smoke_checks.py`.

For a calculation-only change:

1. Prefer the specialist module (`portalcalc/wind.py`, `portalcalc/loads.py`, `portalcalc/storage_loads.py`,
   `portalcalc/crane_beam.py`, `portalcalc/member_checks.py`) if the logic can stay independent of UI.
2. Keep dataclass inputs/results plain and serialisable where practical.
3. Touch `portalcalc/frame/engine/` only at the input conversion and orchestration boundary.
4. Add direct smoke checks that import the module without launching the full UI.

For internal column layouts:

1. Geometry offers the legacy single/paired centre offsets and Custom positions.
2. `internal_column_positions_mm` validates comma-separated metres from the left main column centreline; no clamping is applied to custom positions.
3. Presets store the entry text in `geometry.internal_column_positions_m`. Older presets retain their original offset behaviour.
4. `tests/internal_columns_regression.py` checks layouts, connections, validation and preset compatibility using actual offscreen solves.

Calculation evidence is assembled locally by `MainWindow.calculation_design_dossier` for the headless service and Configurator Review. AI requests, API-key settings and the Structural Assistant UI have been removed.

For Metroll section display changes, keep the stored table key such as `C/Z 200 19` unchanged. Route user-facing labels and reports through `MainWindow.metroll_section_display` so single/double spans render as C and continuous-lapped spans render as Z without breaking capacity or weight lookup.

For a preset/default change:

1. Check `collect_preset_data` and `apply_preset_data`.
2. Check default loading around `factory_defaults_paths`, `load_factory_defaults`
   and `load_default_inputs`.
3. Update `portalcalc/data/presets/factory_defaults.json` only when the packaged default should
   change.
4. Use the presets in `tests/fixtures/` to guard backward compatibility.

For costing changes:

1. `portalcalc/costing.py` owns the costing summary calculation and costing report text.
2. the frame engine still owns the UI controls, rates inputs and selected-profile helpers.
3. Keep new costing logic UI-independent where possible; pass plain summary data
   back to the frame engine instead of reading widgets deep inside helper functions.

For licensing changes:

1. Desktop behavior starts in `portalcalc/licensing.py`.
2. Startup enforcement is in `portalcalc/desktop/app.py`.
3. Server behavior is in `license_server.py`.
4. Admin desktop tooling is in `portalcalc/desktop/license_generator_app.py`.
5. Server deployment details are in `render.yaml` and `license_server_README.md`.

For packaging/release changes:

1. Update `portalcalc/app_metadata.py`.
2. Update `CHANGELOG.md`.
3. Check `PortalCalc.spec`, `PortalCalcAdmin.spec` and `build_desktop.ps1`.
4. Follow `PACKAGING.md`.

## Verification

From the repository root:

```powershell
python run_tests.py                      # every regression script and the smoke checks (10 to 15 minutes)
python run_tests.py bracing_layout       # only scripts whose name contains a word
python tests/smoke_checks.py --quick     # fastest broad check
python tests/bay_layout_regression.py    # one script directly
python -m pytest tests/bay_layout_regression.py
```

`run_tests.py` sets the offscreen Qt platform and turns licensing off; the logs
go to `build/test-logs/`.

For release packaging:

```powershell
.\build_desktop.ps1 -Version <version>
```

Use `-SkipSmoke` only after a passing smoke run, as described in `docs/PACKAGING.md`.

## Current Refactor Pressure

Done in the 2026-11-15 restructure: shared constants and types, the section
catalogue and the 2D solver are their own modules; model generation, checks,
the optimiser and report assembly are in Qt-free engine modules behind an
explicit set of UI hooks.

What is still coupled, in the order worth tackling:

1. **Inputs still live in widgets.** The engine reads `self.span_input.text()`
   and similar. The preset dictionary (`collect_preset_data` /
   `apply_preset_data`) is already the complete plain-data form of the inputs.
   Giving the engine a plain field store built from a preset, with the same
   accessors, lets `FrameEngine` run with no window at all; the desktop then
   binds its widgets to that store. The work is in `build_ui`: the field
   defaults, combo option lists and change handlers that derive values must move
   out of widget construction. Until then `service/headless.py` creates an
   offscreen `MainWindow`.
2. **Three very large functions.** `_generate_and_solve_once` (about 2,600
   lines, `engine/solve.py`), `member_check_envelope_lines` (about 1,500,
   `engine/member_checks.py`) and `update_section_checks_text` (about 1,200,
   `engine/results.py`) were moved whole. Break them up along their existing
   phases (geometry, loads, combinations, solve, envelope) with the regression
   suite as the guard.
3. **Results mixed with presentation.** `update_section_checks_text` and
   `update_results_text` both compute checks and write label text. Separate the
   checked result data from its formatting.
4. `portalcalc/bracing/design.py` and `mixed_sizing.py` still import each other
   (one side delayed).

The source already batches first-order load cases into a single matrix solve,
uses load/topology/wind caches, parallelises case evaluation and screens
optimiser candidates with reduced stiffness models. Column flybracing reuses
solved demands rather than rerunning frame analysis. These are concrete
performance measures, not a substitute for timing representative jobs. Dense
matrix storage and offscreen window construction remain profiling candidates;
there is no measured evidence here that either is the dominant cost.
