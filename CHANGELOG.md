# Changelog

All notable Portal Calc desktop releases should be recorded here.

## 1.6.4 - 2026-10-25

- Added File > Export Full Computations (PDF), with project/job/revision details, a linked contents page, bookmarks, numbered sections and page headers/footers.
- Records the full engineering input register, adopted directional and connected-roof wind bases, gravity/other load derivations and combinations, analytical geometry, member/action envelopes and governing source cases.
- Includes worked section capacities, every selected web/post and governing weld/chord-wall/K-joint connection screens, with the implemented assumptions and validity messages.
- Includes governing-case load and bending diagrams, applied load workings, reactions and extrema. An optional large appendix adds all nodal movements, member actions, signed end forces and distributed member loads for the governing cases.
- Report export runs a fresh check of the selected design and preserves web selections. Detailed case capture is active only for report generation. The short Engineering Summary remains available.

## 1.6.3 - 2026-10-25

- Removed AI Help, AI Optimise, the Structural Assistant tab and the OpenAI request/key-management implementation. Ordinary local optimisation and calculation evidence for Configurator Review remain available.
- Added Custom positions for any number of internal columns, entered as comma-separated distances in metres from the left main column centreline. Supports truss/rafter roofs and pinned/moment top connections, with preset persistence and rejection of missing, duplicate, non-finite or out-of-span positions.
- Preserved existing single-column and symmetric two-column centre-offset presets. Custom support positions also invalidate retained future-canopy designs when changed.

### Grain/storage load-path corrections - 2026-10-20

- Audited the grain/storage load path using independent statics, beam solutions and actual offscreen desktop solves. Confirmed full-bay rail moments and two-adjacent-bay internal portal reactions; no missing 1 m tributary multiplier. Retained the user-confirmed lateral base support and strong-axis rail orientation assumptions.
- Apply direct side-running panel reactions as triangular column member loads with consistent nodal forces/couples and exact member force/curvature recovery, including envelope load snapshots. Previously the equivalent endpoint forces preserved force/overturning but omitted local bending.
- Include free stub overhang above the rail in deflection and member-shear recovery; use exact stationary points for stub bending. Use the selected continuous two-span panel deflection solution and report its governing negative moment at the bottom pressure strip.
- Reject missing/gapped column load paths before applying loads. Include column-top nodes as valid rail supports and reject rail/grain heights above the supporting column. Do not treat the grain-top mesh node as a storage restraint.
- Honour the storage ULS factor consistently instead of substituting the roof/crane Q factor; omit grain from isolated G-only and Ws-only serviceability components. Existing no-storage and left/right/both storage envelopes remain.
- Added storage_load_path_regression.py: 12 focused tests covering bay scaling, split/continuous paths, force and moment conservation, reversed loads/base offsets, member-load snapshots/superposition, FE segmentation, panels, stubs, support gaps and storage factors. Existing storage and roof UDL smoke slices pass; actual desktop solves pass all three panel paths, distinct grain/rail levels, rail at column top and rejection above top. Source change only; packaged releases and Studio release pins are unchanged. Physical bracing/connection design and bulk-solid pressure-code acceptance are not represented as completed.

## 1.6.2 - 2026-10-12 (Studio development candidate)

- Configurator Review now invokes Physics Optimise automatically instead of the legacy optimiser, retaining frozen project inputs, bounded truss depth, per-member web selections and the final exact checkSpecified return.
- Reports Physics Optimise completion separately from a failure to find a verified candidate. Ordinary legacy optimiser actions remain available and unchanged.
- Built as a separate development package; prior 1.6.1 release and existing review evidence are retained. Not a production/engineering acceptance claim.

## 1.6.1 - 2026-09-07

- Fixed the packaged Windows applications failing during `PySide6.QtCore` import by excluding incompatible version-suffixed ICU 78 DLLs and using the unversioned Windows ICU runtime expected by Qt 6.11.
- Added mandatory post-build startup smoke checks for both PortalCalc and PortalCalcAdmin, including an explicit guard against reintroducing the incompatible ICU binaries.

## 1.6 - 2026-09-07

- Configurator Review now runs the deterministic PortalCalc optimiser automatically after the review window opens and the normal licence/startup gate succeeds.
- Updated Configurator Review to allow 50 mm main-truss depth changes between PortalCalc's span-derived minimum and the immutable Master Engine maximum, retain optimiser-selected depth while restoring all other geometry, block invalid manual depths, and report the selected depth and Master Engine maximum in the reviewed result.
- Added Configurator Review launch arguments (`--review-request` and `--review-result`) with a visibly estimate-only desktop session, configurator-owned input locking, adjustable/optimisable member selections, final exact `checkSpecified` checking, atomic reviewed or cancelled JSON output, and explicit failed/unchecked result states.
- Added offscreen Configurator Review regression coverage for request loading, controlled-input restoration, manual section adjustment, final exact checking, failed estimate return, cancellation and atomic result replacement.
- Added a versioned headless calculation-service contract with `autoSize` and `checkSpecified` modes, frozen request hashes, linked-chord and live/snow policy enforcement, explicit rejection of unsupported wall line loads, JSON-safe design dossiers and an offscreen compatibility backend.
- Added a frozen end-to-end service regression covering both specified-section checking and verified automatic sizing against the current 1.5 working calculation baseline.
- Added a read-only Structural Assistant trial using OpenAI GPT-5.6 Luna, with selectable reasoning effort, secure Windows Credential Manager API-key storage, solved-design snapshot hashes, token/cost reporting and explicit engineering guardrails.
- Added compact Guided Help: Luna diagnoses the governing mechanism, selects up to five adaptive main-frame sensitivity trials, receives verified before/after PortalCalc results, and returns a short recommendation while the live design is restored after every trial.
- Added strict bounded trial controls for truss depth, same-family end-column/chord/web stiffness moves and adjacent panel layouts, plus mocked Responses API coverage and an offscreen restoration regression test.
- Refined Guided Help to minimise verified total estimated cost rather than reduce the highest passing utilisation, with cost-ranked member economics, wider downsizing trials, and explicit pass/cost-saving evidence for every Luna-selected trial.
- Added Guided Optimise: PortalCalc first establishes a deterministic baseline, Luna supervises up to three alternate macro search strategies, every branch runs the complete production optimiser, and only the cheapest fully verified incumbent is adopted and saved for future warm starts.
- Added a Guided Optimise shortcut beside Optimise Structure on the Sections tab, mirrored its progress and final recommendation there, and added a completion/failure dialog so the outcome is visible without opening the Structural Assistant tab.
- Fixed Guided Optimise stopping after its first branch when the optimiser's nested Qt event processing delivered the worker-finished event too early; the worker now waits for branch result handling, and continuation API turns are reported explicitly in the Sections status.
- Simplified the Sections actions to Optimise, AI Optimise and AI Help. AI Help opens a reusable dialog containing Review Current Design, Explain Governing Check and a free-form Ask Structural Assistant entry, with the response shown in the dialog.
- Changed user-facing purlin and girt section labels to C sections for single/double spans and Z sections for continuous-lapped spans while retaining the common C/Z table identifier internally for capacity lookup.
- Limited the unverified end-wall portal-restraint suggestion to clad end walls and added its rod/EA steel weight to total costing at the UB rate. Physical costing now allows one complete X at every portal position, with two diagonal runs per X, and includes supplied turnbuckle rates for 16, 20, 24 and 30 DIA rods; 12 DIA remains explicitly cost-pending.

## 1.5 - 2026-08-14

- Added end-wall mullion and girt design to the existing geometry and Sections workflows, with automatic even mullion spacing at no more than 8 m, a manual spacing override, automatic steel section selection, pinned roof/base assumptions, mid-height flybrace effective lengths and H/150 wind deflection checks.
- Reused the side-wall girt spacing and manufacturer-table workflow for end-wall girts, persisted the new inputs in presets, and reflected the adopted mullion/girt layout in the 3D concept model.
- Added end-wall mullion stock/waste weights and variable-length end-wall girt weights to the costing summary and total estimated cost.
- Added preliminary longitudinal bracing design with separate roof and wall brace selections, governing end-wind loads and serviceability reporting.
- Configured roof bracing panels between the end-wall mullion lines and separated eave-strut demands from individually listed mullion-tie demands and selections.
- Added C350L0 CHS column sizes from 60.3 mm through 406.4 mm diameter, including nominal section properties, section checks, preset restoration and hollow-section costing.
- Added circular-tube geometry for CHS members in the 3D concept model.

## 1.4 - 2026-07-20

- Redesigned automatic truss-web sizing around member-by-member force and connection demands while retaining fixed placeholder webs in the analysis model.
- Added economical two-size web distributions, enforced web widths below the connected chord width and exhausted legal web profiles before considering doubled chords.
- Limited web-joint doubled chords to the specific unavoidable chord sides and extended fabrication zones to terminate only at web nodes.
- Added a manual adjacent-panel check beside the calculated web split; ordinary Generate & Solve now analyses one topology, while the optimiser performs one bounded final topology refinement.
- Improved optimiser coupling between web connections and chord cost so larger economical webs can enable lighter chord selections.
- Fixed preset-loaded end-wall bracing options remaining hidden when saved bracing was active.
- Expanded regression coverage for repeatable web assignments, local web-joint doublers, panel topology, fixed analysis webs and preset-driven visibility.

## 1.3 - 2026-07-09

- Added elevation drawing export support, including packaged drawing assets and release smoke coverage.
- Added COLORBOND finish/profile data and expanded 3D concept output for cladding, trims, gutters, openings and storage surfaces.
- Added the packaged PortalCalcAdmin license generator app and tightened desktop licensing/package metadata for customer releases.
- Improved release packaging with app/admin PyInstaller specs, bundled runtime assets and quick/full smoke build modes.
- Refined the Geometry/Wind UI by keeping base support controls in the Settings menu, improving wall-enclosure contrast in dark mode and preventing accidental combo-box changes from mouse-wheel scrolling.
- Updated smoke checks for the new UI, licensing, drawing/export and packaging paths.

## 1.2 - 2026-06-29

- Released the latest storage-load workflow refinements for team use.
- Improved side-running storage panel paths through centre stubs and continuous-over-stub support.
- Added storage rail/stub auto-selection checks, storage rail service deflection reporting and column restraint handling at storage levels.
- Expanded storage panel preliminary reinforcement, crack-width and deflection reporting in the UI and smoke checks.
- Updated 3D concept output for storage panels, end-wall storage surfaces and front/back storage wall options.

## 1.1 - 2026-06-19

- Added stored-material wall loading through rails, centre stubs, columns and bases.
- Added preliminary storage-panel reinforcement, crack-width, deflection and material-cost checks.
- Expanded truss, rafter, skillion, canopy and lean-to analysis and reporting.
- Added automatic doubled-chord zones for governing chord-member and chord-local web-joint checks.
- Added detailed frame, secondary steel, crane runway and storage costing, including a separate doubled-chord rate.
- Added engineering tooltips with Importance Level and Terrain Category examples.
- Hardened the Render license server with persistent-disk configuration, startup guards and SQLite backups.
- Improved presets, status summaries and smoke-test coverage.

## 1.03 - 2026-06-11

- Removed the editable legacy wind mode control from the Geometry/Wind tab.
- Made the wall enclosure diagram the source of truth for derived wind frame type.
- Presets now save the derived `wind.frame_type` from the wall enclosure, while old presets only use legacy `frame_type` when no `wall_enclosure` data exists.
- Updated side-wall Cpe and Cpi column loads to follow the selected left/right wall enclosure states instead of the old open-sided shortcut.
- Added smoke coverage to prevent stale legacy `frame_type` data overriding saved wall enclosure layouts.

## 1.02 - 2026-06-10

- Last release before the legacy wind mode cleanup.
- Release artifact present locally as `release/PortalCalc-1.02-windows.zip`.

## 1.01 - 2026-06-04

- Earlier packaged release.
- Release artifact present locally as `release/PortalCalc-1.01-windows.zip`.

## 1.0.0 - 2026-06-03

- Initial versioned packaged release.
- Release artifact present locally as `release/PortalCalc-1.0.0-windows.zip`.
