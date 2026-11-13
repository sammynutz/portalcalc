# PortalCalc Code Map

Working-folder cleanup, 2026-11-03: regression scripts, standalone hay-shed study
tools, historical sample presets and selected completed review notes are archived
outside this folder. References to them below describe the archived development
workflow; restore those files before running their commands. Runtime calculation
modules and factory defaults remain in this folder.

This document is a fast navigation guide for future edits. It is not a full
design spec; it points to the files and regions that usually matter first.

## App Shape

PortalCalc is a PySide6 desktop engineering tool with a packaged Windows build.
Most UI wiring, model generation, analysis orchestration, preset handling, 2D
plotting, 3D export, costing, optimisation and report text live in `main.py`.
Specialist calculations are gradually being split into smaller modules.

Last checked against the working source: **2026-11-01**. This includes
uncommitted source changes; it does not describe only the latest packaged release.

Generated or packaged output lives in `build/`, `dist/`, `release/` and
`__pycache__/`. Do not inspect those first unless the task is explicitly about
packaging output.

## Start Here

Release packaging is performed only when the user explicitly asks. Routine changes should remain source changes with appropriate checks.

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
| `building_service_regression.py` | Includes `ProppedMullionTests`: `MainWindow.propped_mullion_check` (propped end-frame props: frame axial plus end-wall wind via `end_wall.mullion_profile_design`, hooked into `member_and_connection_ratio` for INTERNAL_COLUMN when `propped_end_context()` is set). Service v2 (`portalcalc.calculation.request.v2` with a `building`). Covers the contract and rejections, unchanged v1 digest, `building_with_frame_inputs`/`reviewed_building`, real headless `checkSpecified` with per-gridline evidence and a resubmission round trip, legacy_2d ≥ v1, and v2 Configurator Review. v2 `autoSize` needs `PORTALCALC_SLOW_TESTS=1`. See `CALCULATION_SERVICE.md`. |
| `configurator_review.py` | Configurator Review CLI parsing, request loading, controlled-input/member-selection merge, reviewed/cancelled result envelopes and atomic JSON writing. |
| `configurator_review_regression.py` | Offscreen interactive regression for request loading, UI locking, section adjustment, final exact checking, failed-result return, cancellation and atomic output. |
| `concept_3d.py` | 3D concept data assembly, HTML preview output and Collada `.dae` export helpers. |
| `eave_node_regression.py` | Eave height at the cladding pitching point: `MainWindow.eave_node_basis` (node = H + (g + D/2)tanθ − (p + t/2)/cosθ, purlin/girt from the early `purlin_check`/`wall_girt_check` made before the frame) and `apply_eave_strip_loads` (wall above the node and roof out to the cladding line as eave-node point loads, applied at the end of `apply_combination_loads`). Tests the formula, the skillion low side, the 200 mm default and the vertical load balance. |
| `bay_layout.py`, `bay_layout_regression.py` | Qt-free bay list input (`geometry.bays_mm`, e.g. `7*8000, 7000`): parse/format, the legacy length + bay size conversion (full bays, shorter last bay), stations, length and the design bay (longest). `MainWindow.set_bays` derives the unshown building length and design bay fields from it; `bay_lengths_mm`/`bay_stations_mm` give the actual bays to the 3D layout, building model, costing and concept view. |
| `building_model.py`, `building_model_regression.py` | Qt-free `portalcalc.building.v1` contract for BOLT. It covers site, main section, gridlines with frame types (including the `propped_end` lightweight end frame), canopies/lean-tos over gridline ranges, wall cladding by segment, openings (treated closed), partitions and sizing groups, with preset-shaped `design_inputs` passed through unchanged. `building_model_from_preset` turns a preset into a uniform building; `frame_preset(gridline)` goes back to a desktop preset; `engine_scope_issues` lists what the current engines can't represent. The tests cover validation, JSON round trip and offscreen desktop round trip (inputs, bracing `LayoutGeometry`, wind source directions). See `BUILDING_LOADS_3D_PLAN.md`. |
| `building_loads.py`, `building_loads_regression.py` | Qt-free per-gridline `FrameLoadSet` load descriptors: roof gravity and wind from all four directions (phases 2–3), plus canopies and lean-tos, full or part length (phases 7a/7b; per-frame attachment tributaries, external/internal main-column split, `canopy_roof_scale`/`canopy_roof_kn_m`). Covers canopy gravity, the continuous roof across canopies, free-roof canopies, lean-to outer columns, and lean-to / connected / open-canopy chamber Cpi with the shared-column net envelope. Part-length attachments, skillion + attachment and partitions raise `BuildingLoadsScopeError`. Two bases: `legacy_2d` (today's Left/Right/End cases × tributary, exact parity with main.py) and `building` (Front/Back end wind with Table 5.3(A)/5.2(C) bands per frame at d = length, per-direction Md, Cpi from each opening's own position, per-frame Ka; Kc via `kc_rule`: the default `surface_count` (roof + clad column lines + internal when |Cpi| ≥ 0.4; ≥3 → 0.8, 2 → 0.9, 1 → 1.0), or `legacy`). `ExpectedDifferenceTests` attribute the building v 2D differences. Each frame gets its own tributary width (half bays at the ends); column tributaries come from clad wall segments via `frame_strip_integrals`; main-chamber Cpi is ported from main.py's Table 5.1(B) rule using open wall-segment areas; each direction uses its own source bearing. Wind descriptors are ultimate roof strips (`bands`, or `slopes` split at the actual apex), signed column line loads and Cpi lines, with `sls_scale`. The tests check end-half/conservation/partial-cladding/dominant-opening behaviour and an arrow-by-arrow parity with main.py's `single_load_arrows` for six buildings. Canopies, lean-tos and partitions raise `BuildingLoadsScopeError`. |
| `frame_loading.py`, `frame_loading_regression.py` | Phase 4 bridge from per-gridline loads to main.py's envelope checks: `FrameLoadCombination` (a `LoadCombination` carrying its gridline `FrameLoadSet`; name gets "@GL…", `base_name` keeps the template), `frame_load_combinations` (deduplicated per combination by gravity/wind signature), `frame_roof_loads`, `roof_strips_on_nodes` (snaps "slopes"/"uniform" to the actual node path). In main.py, the desktop "Frame loads" selector (`frame_load_basis_combo`, `FRAME_LOAD_BASIS_OPTIONS`, preset `wind.frame_load_basis` written only when not single frame; `building_load_summary_lines` adds a BUILDING LOADS results section) sets `MainWindow.building_load_mode` (`None` = today; `"building"`/`"legacy_2d"`), which makes `_generate_and_solve_once` check those combinations. `apply_combination_loads` applies a combination's own `frame_loads` via `apply_frame_wind_loads`, and `current_building_frame_loads` builds and caches them (or uses `building_model_override`). Tests: a legacy interior gridline reproduces today's envelope exactly; all gridlines only add demands; the building basis leaves gravity and side-wind actions unchanged.<br>**Phase 5 screening:** with a building mode on, `auto_size_current_preset` runs `_auto_size_with_building_screening`. It sizes with `_building_active_set` (the combinations governing the last full check plus all gravity), then gates on a full-set solve, adding newly governing combinations for up to 3 rounds. The original optimiser is `_auto_size_current_preset_once`; `building_screening_disabled` turns screening off. The report adds `governing_report`. `ScreeningTests` are fast; `OptimiserComparisonTests` (about 2 min) need `PORTALCALC_SLOW_TESTS=1`. |
| `costing.py` | Frame, purlin/girt, crane runway and storage costing summary calculations/report text. |
| `purlin_selection.py` | Joint general/edge roof-purlin selection: shared profile with independent thicknesses, ranked by installed material weight; exports passing profile comparisons. |
| `loads.py` | Basic load dataclasses, load combinations, non-wind roof/wall/self-weight load application. |
| `wind.py` | Wind input dataclasses, AS/NZS 1170.2-style wind calculation helpers, Cpe/Cpi logic, wind load application to roof and columns. `open_canopy_cpi` is the open-canopy underside Cpi rule (Table 5.1(B) single opening) shared by the 2D frame and the 3D bracing loads. |
| `member_checks.py` | Steel member capacity checks and governing utilisation result objects. Optional separate compression-body area preserves legacy behaviour when unset. |
| `column_flybracing.py` | Automatic girt flybrace selection for clad columns, cached exact segment demands and same-case am; no frame solves. |
| `column_flybracing_regression.py` | Segment/load-case provenance, tall-column brace growth, LTB/in-plane separation, caching and export regression. |
| `end_wall.py` | UI-independent end-wall mullion layout, pinned-member strength selection and H/150 deflection checks. |
| `bracing_layout.py` | Qt-free versioned bracing geometry and blocked/pinned panel constraints for desktop/BOLT; no structural analysis. Open canopies (schema v2 only when present): canopy faces/panels, `strut_lines`/`canopy_line`, `BRACE_GROUPS`/`brace_group`, `canopy_bracing`/`canopy_bays` (brace canopies in the end roof-braced bays plus spacing-rule bays) and `carry_over` (keep main panels when only canopies change). Portal frame brace locations (`portal_panels`, schema v3 only when used) kept apart from X panels: `portal_braces`/`portal_blocked`/`available_portals`, with `selection`/`all_blocked` for both. See `BRACING_LAYOUT.md`. |
| `bracing_portal_brace.py`, `bracing_portal_brace_regression.py` | Qt-free portal frame brace (UB moment frame in a side-wall bay, pinned bases): 2D frame condensed onto the two eave Y translations, recovery of N/V/M and base reactions, `PortalBraceCandidate` (UB with E·I sizing stiffness), closed-form check. Tests: layout rules, independent `Structure2D` comparison, network balance and stiffening, design checks/cost/allowance, auto-size and the spacing anchor. |
| `bracing_layout_search.py`, `bracing_layout_search_regression.py` | Optimiser-only seeds `full_roof_seeds` (full roof every bay / anchor bays with both eave girders / alternate bays) and `portal_brace_seeds` (portal braces on a side wall whose end bays can't take X bracing). Practical end-load-path search with geometry-adaptive continuous eave roof girders, end-wall restraint and bounded extra-wall-bay rod alternatives and up to four compact installed-cost patterns; optional detailed cost search. Keeps passing input incumbents, blocked/required constraints and exact scoped strength/SLS/bare-stability acceptance. Fast sizing cost-orders local checks, ranks repeatable brace-EA steps and permits bounded brace balancing. See `BRACING_LAYOUT.md`. |
| `bracing_layout_ui.py` | Interactive Qt-painted 3D location editor, face filters (including canopy roof faces), local undo/redo and geometry review. |
| `bracing_layout_regression.py` | Geometry/constraint validation, real canvas clicks, preset persistence, read-only review and no structural-credit regressions. |
| `bracing_analysis.py` | Axial 3D network, tension-only rod equilibrium, portal interface condensation/recovery, building assembly, integrated end-wall loads and reaction balance. |
| `bracing_analysis_desktop.py` | Actual portal/section adapter, simultaneous gravity/end-wind combinations, frozen result evidence and readable report. Scope guards (lean-tos, skillion + canopy, canopy eave override), `actual_canopies`, `canopy_edge_purlin` (installed-purlin record for the canopy edge strut) and `roof_friction_drag` surfaces (main underside when both ends are open, canopy top and underside). See `BRACING_ANALYSIS.md`. |
| `bracing_roof_loads.py` | Frame tributaries, G/Q/solar placements, end-wind normal roof/side-wall pressure patterns and full portal load snapshots, including canopy gravity/wind (`canopy_parts`) and free-roof Cpn for fully open structures (`fully_open`); independent full-matrix/load conservation checks in `bracing_roof_loads_regression.py`. |
| `bracing_side_wind.py` | ULS and SLS (Ws, G + Ws) left/right wind, roof alternatives, internal pressure, varying end-wall suction, exact partial-member pressure integration and applicable roof friction. Roof bands run over the continuous roof from the windward canopy tip (`roof_cpe_segments(..., left_ext, right_ext)`). Independent statics/mirroring/full-matrix checks in `bracing_side_wind_regression.py`. |
| `bracing_transverse_sls_regression.py` | Factory-default transverse SLS scaling, per-frame sway/restraint evidence against an independent 2D solve, bare-network diaphragm exclusion and unchanged longitudinal acceptance. Also checks `wind.add_roof_panel_pressure` (roof wind normal to each panel) the banded Table 5.3(A) skillion high-side basis, and main/3D gable and skillion Ws sway agreement within 1%. |
| `bracing_restraint.py` | Qt-free adoption of the 3D frame restraint into the 2D portal: record from a checked result, hard (stale) and soft (recheck) fingerprints, status for the 2D solve and brace-stiffness floors for 3D sizing. |
| `combined_sizing.py` | Headless combined 2D portal + 3D bracing sizing over `MainWindow`: unrestrained 2D sizing, practical bracing, stiffer/eave-girder options, restraint adoption, 2D re-size, 3D recheck and total-cost ranking. |
| `frontier_sizing.py`, `frontier_sizing_regression.py` | Frontier optimiser for the cheapest combined frame + bracing: lighter frames with bisected restraint requirements (check solves via `MainWindow.trial_sway_restraint_kn_mm`), a 3D cost/restraint frontier of bracing options, matching, and exact verification in rank order; the floor frame is tried with each option before any re-size. Tests use solver stand-ins. |
| `bracing_parallel.py` | Qt-free process pool for independent 3D checks/sizings sharing one context (sent once via a temp file); in-order results, serial fallback, `PORTALCALC_BRACING_WORKERS`. `size_cost_task` returns the sized cost too, so the optimiser can keep the cheaper of the practical-section and light-rod starts. Callers' scripts need a `__main__` guard. |
| `bracing_restraint_regression.py` | Sway spring mechanics, adoption/staleness/recheck states, brace floors, 3D self-credit exclusion, deferred transverse SLS sizing and horizontal low-pitch roof bands. |
| `restraint_feedback_prototype.py` | Study script: per-frame secant eave restraint from the 3D model versus the production hand spring, with 2D spring re-solves. Writes `output/restraint_feedback_prototype.*`. |
| `bracing_spacing.py` | Default five-bay project rule, complete roof/side-wall anchor assessment and bounded intermediate-bay planning. See `bracing_spacing_regression.py`; small-screen/footer checks are in `bracing_workspace_fit_regression.py`. |
| `bracing_diaphragm.py` | Revision B roof/wall reference calibrations (3/6 mm/kN), explicit empirical a/b² actual-panel c calculation or direct overrides; SLS equivalents, sloping-wall conversion, physical shear recovery and independent bare stability. Regression checks cover reference factors, geometry, reversal, open faces, ULS independence and saved UI behavior. |
| `bracing_analysis_ui.py` | Compatibility import for the combined bracing workspace. |
| `bracing_calculation_cache.py`, `bracing_cache_regression.py` | Compressed optional result cache with current-model/input/engine fingerprints; reopen/job persistence, stale/corrupt rejection, preserved failure diagnostics and input-hash separation. |
| `bracing_serviceability_regression.py` | IL1 global H/80 versus local L/150, main-criterion following/overrides/cache invalidation, strength-only versus combined scope, grouped/mixed sizing and mandatory strength/stability checks. |
| `bracing_mullion_design.py`, `bracing_alignment_regression.py` | Main/3D shared mullion wind-bending plus bracing-axial checks, native properties/restraint, baseline prerequisites and checked main adoption; eave tributary geometry, pressure-area scaling, costing and cached reopen regression. |
| `bracing_workspace_ui.py` | One-window selector, section/criterion inputs, visual results, cost sizing, member inspection and TXT/JSON export. Persistent failure categories and x1 governing-movement navigation use `bracing_sizing_diagnostics.py`; the canvas circles the governing SLS node. |
| `bracing_design.py` | Scoped tension/buckling/interaction and local deflection checks; material/fitting cost and reanalysed grouped catalogue sizing. Available-EA ceilings prevent false saturation stops; remaining solve budget checks cheaper/preferred groups with exact acceptance. The `canopy_brace` group is active only for buildings with a canopy (`active_groups`). `bracing_mixed_sizing.py` shares those helpers. See `BRACING_DESIGN.md`. |
| `bracing_policy.py`, `bracing_policy_regression.py` | Agricultural wire/commercial rod preferences, mandatory rod eligibility on active unclad wall groups, whole-design preference/cost ranking and policy evidence; real-solve and UI regression checks using synthetic rope EA/prices only. |
| `bracing_design_regression.py` | Independent capacity/cost workings, tiny exhaustive sizing comparison, real orbit/member picking and transactional workspace save/sizing. |
| `bracing_components.py`, `bracing_component_ui.py` | Per-location installed/added purlin and steel assemblies, axial/bending ratings, common-end force sharing and rope EA/price input. Wire stiffness defaults blank ratings to the explicit interim 200 GPa / nominal area assumption; declared EA overrides and the adopted basis are saved and exported. `bracing_schedule.py` holds supplied rope rates and the M10/M12 turnbuckle allowances, with price-source evidence and legacy migration. |
| `purlin_combined.py` | Adopted Lysaght May 1999 simplified compression/bending calculation; Metroll C/Z web-axis properties; calculated member tension using G450 minima and explicit net-area/kt assumptions, or preserved declared Nt; source evidence and worked output. |
| `purlin_shared_design.py` | User-adopted single-addition 50/50 run/added allocation and two-addition equal-per-ply allocation; existing-run/added single-span beam demands and explicitly preliminary table-reference checks. Unequal bays: the run uses the actual spans and every bay takes the longest bay's table reference; a bay longer than the installed purlins' checked span is rejected. Candidates/frozen checks enforce matching roof profile size; each added ply is checked and costed independently. |
| `purlin_strut_tables.py` | In-range manufacturer UDL-table interpolation with stated bridging and frozen source rows; separate single/double/five-span reference arrangements. |
| `purlin_strut_regression.py` | Independent asymmetric two-span statics, single-span responses, load conservation/self-weight, table/reference limits, sizing, UI invalidation and persistence. |
| `purlin_beam.py`, `purlin_beam_design.py`, `purlin_end_local.py` | Roof-run Euler-Bernoulli model with separate Table 5.6 end-roof pressure patches, finite distributed cladding coupling, direct load pickup, single-span additions, demand evidence and gated M/V/axial checks; local fast screens recover only the changed full roof run, final exact checks recover all affected lines. |
| `purlin_beam_ui.py`, `purlin_beam_regression.py` | Native moment/shear/deflection/transfer plots; independent beam and load-sharing benchmarks, whole-run sizing and workspace persistence tests. |
| `bracing_schedule.py` | Supplied Grade 300 rod and Grade 1570 rope factored capacities, exact turnbuckle sizes and source image evidence. |
| `bracing_mixed_sizing.py`, `bracing_components_regression.py`, `bracing_sizing_recovery_regression.py` | Cost-ordered per-location screens and bounded exact reanalysis; reject unstable brace downgrades and retry sized struts/posts with last verified braces within the solve cap. Detailed callers also compare coupled purlin/brace costs. Sharing, missing/stale ratings, transactional recovery, schedule capacities and saved UI regression. |
| `bracing_sizing_diagnostics.py` | Specific sizing-stop reasons and rejected alternatives; retains unchanged-input analysis and directs the workspace to the relevant rating row. |
| `bracing_analysis_regression.py` | Closed-form mechanics, unreduced/reduced comparison, load/moment integration, missing-path failures and real offscreen desktop/export checks. |
| `bracing_canopy_regression.py` | Open canopies and open-ended roofs in 3D: canopy Cpi parity with 2D, friction surfaces, open-ended/fully open loads, canopy gravity/wind per frame, layout faces/schema v2/carry-over, the canopy bracing rule, the `canopy_brace` group, network against the uncondensed matrix, guards, desktop solve, layout editor and Find layout (25 tests; smoke-check step "open canopies, open-ended roofs and roof friction"). |
| `bracing_analysis_desktop.end_wind_cases` | Base end-pressure cases use along-wind length/span Cp,e, contributing end/internal surface Kc and external roof friction. `building_roof_cases` adds simultaneous normal roof/side-wall and gravity actions plus ULS and SLS side-wind cases. Transverse SLS gives report-only per-frame sway (`transverse_sls_summary`); it is excluded from longitudinal sway acceptance and the stiffness screen. |
| `crane_beam.py` | Crane runway beam input/result dataclasses and section checks. |
| `storage_loads.py` | Stored-material pressure/load calculations, storage panel design, storage load application and summary text. |
| `elevation_drawings.py` | PDF elevation/plan/section drawing export using Qt painting. |
| `computation_report_data.py` | Frozen full-report evidence: actual case results, adopted wind bases, section/web selections and worked capacity values. |
| `computation_report.py` | Paginated PDF calculation report, bookmarks/contents, tables and vector model/load/bending diagrams. See `COMPUTATION_REPORT.md`. |
| `computation_report_regression.py` | Real-solver and PDF integrity checks for the full computation-report export. |
| `finishes.py` | Cladding profile and COLORBOND finish lookup data. |
| `licensing.py` | Desktop license token storage, machine ID, local verification, activation and refresh client. |
| `license_server.py` | FastAPI license server, SQLite persistence, key generation, admin routes and CLI helpers. |
| `license_generator_app.py` | Admin-only PySide license generator/client. |
| `bracing.py` | Legacy UI-independent preliminary longitudinal bracing calculation; distinct from the explicit 3D network in `bracing_analysis.py`. |
| `reduced_optimizer.py` | Qt-free physics-guided stiffness screening, catalogue frontiers, candidate projection and static condensation; full-engine checking remains the acceptance gate. |
| `physics_optimiser_regression.py` | Real-frame reduced-screen evaluation and optional comparison with production optimisation. |
| `future_canopy_design.py`, `future_canopy_design_regression.py` | Merge primary-steel/reinforcement schedules and recheck the same physical frame before/after canopy installation. Desktop orchestration uses Qt. See `FUTURE_CANOPY_DESIGN.md`. |
| `future_solar_design.py` | Four-state bare/canopy/solar/combined verification and bounded repair of one common schedule. See `FUTURE_SOLAR_DESIGN.md`. |
| `hay_shed_backend.py` | Search-specific backend and build-now cost objective, preserving future-condition structural checks. |
| `hay_shed_search.py`, `hay_shed_search_regression.py` | Resumable process-parallel geometry search, exact proposal reload, ranking and policy regressions. See `HAY_SHED_SEARCH.md`. |
| `hay_shed_check.py`, `hay_shed_orientation.py` | Saved-input exact rechecking and orientation-factor assessment respectively. |
| `hay_shed_pair.py`, `hay_shed_staged.py`, `hay_shed_fixed_bearing.py` | Paired solar/no-solar searches, staged geometry refinement and fixed-bearing actual-region verification. |
| `hay_shed_future_solar.py`, `hay_shed_optional_solar_report.py` | Four-state checking of search finalists and assembly of the completed-study report. |
| `hay_shed_pitch_bale_study.py`, `hay_shed_pitch_matched_check.py` | Separate pitch/storage study and matched-geometry checks against its saved leaders. |
| `hay_shed_report.py`, `hay_shed_bale_comparison.py`, `hay_shed_pitch_bale_report.py` | Study-specific report generation and storage-capacity ranking of saved results. |
| `purlin_selection_regression.py` | Compatible general/edge profile selection and real roof-selection integration checks. |
| `storage_load_path_regression.py`, `wind_envelope_regression.py`, `web_force_envelope_regression.py` | Focused storage transfer, directional wind and web force-envelope regressions. |
| `smoke_checks.py` | Compile checks plus non-visual and UI smoke tests; selected specialist suites are also invoked here. Steps run in parallel processes (`--jobs`, `--jobs 1` serial) with single-threaded BLAS. |

## Key Data Files

- `Shape_Catalog.xlsx`: section catalogue loaded by `ShapeCatalog` in `main.py`.
- `metroll_megaspan_capacity_tables.json`: purlin/girt capacity data loaded by `main.py`.
- `presets/factory_defaults.json`: packaged factory defaults.
- `presets/*.json`: sample/customer/job presets used for manual regression and debugging.
- `assets/CSB_logo.jpg`: drawing title block/logo asset (source for the derived `csb_logo.png` / `csb_logo_white.png` used on the splash and About tab).
- `assets/portalcalc_logo*.{svg,png}`, `assets/portalcalc_icon*`: BOLT-family PortalCalc logo (navy and white variants) and app icon, generated by `make_brand_assets.py`. Brand colours live in `app_metadata.py` (`BRAND_NAVY`, `BRAND_TEAL`).

## Main.py Landmarks

Use symbol names with `rg -n` rather than fixed line numbers: this file changes
frequently and the old line-number map had drifted substantially.

| Area | Search symbols in `main.py` |
| --- | --- |
| Catalogue and section data | `SectionProfile`, `ShapeCatalog`, `PurlinLayout` |
| 2D finite-element primitives and solver | `Node`, `FrameElement2D`, `Structure2D` |
| Envelope widgets and plotting | `CompassSelector`, `BuildingEnvelopeSelector`, `PlotCanvas` |
| Embedded 3D viewers | `Concept3DViewer`, `OpenGLConcept3DViewer` |
| Desktop construction | `MainWindow`, `build_ui`, `apply_engineering_tooltips` |
| Frozen service/review evidence | `calculation_design_dossier` |
| Defaults and preset persistence | `factory_defaults_paths`, `load_factory_defaults`, `load_default_inputs`, `collect_preset_data`, `apply_preset_data` |
| Drawing export wrapper | `export_elevation_sheet` |
| Foundation calculations | `foundation_design_from_reactions`, `foundation_design_from_envelopes` |
| Wind input conversion | `wind_inputs_from_ui` |
| Model construction and topology selection | `generate_and_solve`, `_generate_and_solve_once` |
| Load-case checking and envelope assembly | `member_check_envelope_lines` |
| Optimise (the only frame optimiser: stiffness screen, exact repair/refinement, depth sweep) | `run_physics_structure_optimisation`, `auto_size_depth_sweep` |
| Optimiser entry points | `auto_size_current_preset` (building-load screening wrapper `_auto_size_with_building_screening`), `run_structure_optimisation` (the Optimise button) |
| Results and report text | `update_section_checks_text`, `update_costing_text`, `update_foundation_text`, `update_results_text` |
| Plot refresh | `refresh_plot` |
| Desktop startup and licence gate | `if __name__ == "__main__"` |

## Dependency Boundaries

- The Qt-free calculation modules include `loads.py`, `wind.py`,
  `member_checks.py`, `column_flybracing.py`, `end_wall.py`, `storage_loads.py`,
  `crane_beam.py`, `purlin_selection.py` and `reduced_optimizer.py`.
- The newer bracing/purlin features generally split geometry, mechanics,
  design/policy, desktop adaptation and Qt widgets into separate modules.
  Shared purlin modules currently obtain common types from `bracing_analysis.py`.
- `portalcalc_service.py` is a Qt-free request/result boundary. Its production
  compatibility backend in `portalcalc_headless.py` still constructs an offscreen
  `MainWindow`; headless execution is not yet an independent calculation engine.
- `future_canopy_design.py`, `future_solar_design.py` and the hay-shed backend
  orchestrate that existing engine; they are not part of a separate solver.
- `computation_report.py` renders frozen evidence rather than solving a model.
  `computation_report_data.py` has local imports of two engineering constants
  from `main.py`; this is a remaining reverse dependency on the desktop module.
- `bracing_design.py` dispatches locally to `bracing_mixed_sizing.py`, which
  imports shared helpers back from `bracing_design.py`. The delayed import
  avoids an eager import loop but the design dependency is still bidirectional.
- Desktop licensing, the FastAPI licence server and the admin desktop app have
  separate entry points and packaging responsibilities.

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

For internal column layouts:

1. Geometry offers the legacy single/paired centre offsets and Custom positions.
2. `internal_column_positions_mm` validates comma-separated metres from the left main column centreline; no clamping is applied to custom positions.
3. Presets store the entry text in `geometry.internal_column_positions_m`. Older presets retain their original offset behaviour.
4. `internal_columns_regression.py` checks layouts, connections, validation and preset compatibility using actual offscreen solves.

Calculation evidence is assembled locally by `MainWindow.calculation_design_dossier` for the headless service and Configurator Review. AI requests, API-key settings and the Structural Assistant UI have been removed.

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
python -m py_compile main.py member_checks.py loads.py wind.py smoke_checks.py licensing.py license_server.py license_generator_app.py crane_beam.py storage_loads.py elevation_drawings.py finishes.py
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

Prioritise small extractions with regression coverage:

1. Move shared engineering constants/types out of `main.py` and shared bracing
   sizing helpers out of the two mutually dependent sizing modules.
2. Extract section catalogue and 2D solver primitives, preserving existing
   imports while callers migrate. These are natural foundations for a Qt-free
   model builder.
3. Move preset conversion and widget-to-input conversion behind plain data
   contracts, then extract model construction and checked result assembly.
4. Move optimiser state/search orchestration behind the same calculation
   boundary. Retain the reduced screen and full exact-check acceptance gate.
5. Extract remaining foundation and purlin/girt calculation helpers as their
   features are edited.

The source already batches first-order load cases into a single matrix solve,
uses load/topology/wind caches, parallelises case evaluation and screens
optimiser candidates with reduced stiffness models. Column flybracing reuses
solved demands rather than rerunning frame analysis. These are concrete
performance measures, not a substitute for timing representative jobs. Dense
matrix storage and offscreen window construction remain profiling candidates;
there is no measured evidence here that either is the dominant cost.

The root directory also contains study-specific scripts alongside reusable app
modules and tests. Grouping them into packages can improve navigation later,
but moving files alone will not remove the calculation/UI coupling.
