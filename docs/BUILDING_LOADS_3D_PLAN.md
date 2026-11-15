# 3D Building Loads → Governing 2D Frames: Plan

Status: 2026-11-11. Decisions D1–D7 and Q1–Q3 resolved.

- **Phase 1 done:** `portalcalc/building/model.py`, `tests/building_model_regression.py`.
- **Phase 2 done for the main building:** `portalcalc/building/loads.py`,
  `tests/building_loads_regression.py`. It covers gravity and side wind; canopies,
  lean-tos and partitions are still to add.
- **Phase 3 done.** It adds end wind in both directions, Cpi by opening
  position, Ka per frame, and Kc by effective-surface count
  (`surface_count`, the default). It runs in two bases:
  - `legacy_2d`: today's cases × tributary; exact parity with the frame engine,
    including End.
  - `building`: the proper 3D loading.

### Phase 3 results: end wind, building v today's 2D envelope

Each figure is the frame's largest roof uplift over all four end-wind
cases, as a share of the 2D End envelope. Building 21 m × 40 m × 6 m, A5,
north E. GL1 is the front end frame and GL6 the back end frame.

| Pitch | GL1 | GL2 | GL3 | GL4 | GL5 | GL6 |
| --- | --- | --- | --- | --- | --- | --- |
| 7.5° | 53% | 75% | 40% | 26% | 48% | 34% |
| 15° | 90% | **134%** | 74% | 47% | 86% | 57% |
| 22.5° | 75% | **118%** | 68% | 44% | 76% | 48% |

- **Below 10°:** no frame exceeds the 2D envelope, as it must. Per-frame
  band averages can't exceed the worst bay window, d = length gives h/d ≤
  h/span, and each end's Md ≤ the larger end's.
- **Side walls:** at most 89% of the 2D first-bay value (GL2).
- **10° and over, today's 2D end-wind roof is unconservative near the
  ends.** 2D uses the uniform downwind-rafter Cpe (−0.5 at 15°). Table 5.3(A)
  for wind along the ridge gives −0.9 within 1h of the windward end, so GL2
  carries up to 34% more uplift than the 2D frame is checked for today. The
  middle frames are well below. Whether end wind governs the frame overall
  depends on side wind; phase 4 will show that per job.
- **Directionality is large.** With north E, front-end wind has Md giving
  wu 0.892 kPa but back-end wind only 0.571 kPa. Today's 2D applies the
  larger to both.
- **Cpi from an open end wall in side wind** now uses the bands across that
  wall's span (finding 2 fixed in the `building` basis).

### D3 sign-off: Kc and Ka on frames (needs your review)

Both rules are implemented. Neither changes results for an enclosed
building.

**`legacy` (today, and the current default).**
- Kc,e = 0.8 for every frame. It becomes 0.9 only when nothing in the
  building is clad ("Roof Only", which is a free roof and takes no Kc
  anyway).
- Kc,i = 0.8 when |Cpi| ≥ 0.4, otherwise 1.0.
- The pressure factor is max(Ka × Kc,e, 0.8). Ka ≤ 1, so it is always 0.8:
  **Ka never acts on a frame under this rule.**

**`surface_count` (proposed reading of Table 5.5 for a frame).**
- Count the effective surfaces acting on *this* frame in *this* case:
  - the roof;
  - each column line with cladding framing into it;
  - internal pressure, when |Cpi| ≥ 0.4 in both Cpi cases. Requiring both
    is the conservative reading: fewer surfaces means a higher Kc.
- Kc,e = 0.8 for ≥ 3 surfaces, 0.9 for 2 and 1.0 for 1. This is the same
  count-to-Kc mapping the 3D bracing already uses for end walls
  (`end_wind_combination_factors`).
- Kc,i = Kc,e when internal pressure counts, otherwise 1.0.
- The factor is still max(Ka × Kc,e, 0.8). Ka uses the frame's own
  tributary area: zone length × tributary for roof bands, slope length ×
  tributary for end wind, eave × clad tributary for walls.

**Effect.**
- Enclosed frames: identical (3 surfaces, 0.8).
- Partly open frames, e.g. a column line with no cladding and |Cpi| < 0.4:
  Kc,e rises to 0.9 or 1.0, and Ka starts to reduce small areas. Loads go
  **up** compared with today.

**Resolved 2026-11-11, from the standard's definition of an effective
surface:**
- Each wall is a surface, whatever its role (windward, side or leeward).
- The roof is one surface; the upwind and downwind slopes count together.
- The internal surfaces are one surface, and count only when |Cpi| ≥ 0.4.

`surface_count` follows this. Kc,i is decided per Cpi case: 1.0 when that
case's |Cpi| < 0.4, which keeps today's internal rule exactly.

**Adopted 2026-11-11.** The user confirmed the count-to-Kc mapping
(≥ 3 → 0.8, 2 → 0.9, 1 → 1.0). `surface_count` is now the default for the
`building` basis. `legacy_2d` keeps today's Kc so it stays comparable with
the frame engine.

- **Phase 4 done (single sizing group).** It adds `portalcalc/frame/frame_loading.py` and
  `MainWindow.building_load_mode`. With the mode off, the frame engine behaves
  exactly as before.

- **Phase 5 done.** It adds active-set screening with a full-set acceptance
  gate, and a report of the governing gridline and case.
- **Desktop toggle done.** The "Frame loads" selector on the Geometry/Wind
  tab switches between single frame (default), building loads, and building
  loads with 2D coefficients. It is saved as `wind.frame_load_basis` only
  when not the default, and the results gain a BUILDING LOADS section. A
  service or review building keeps driving the mode when its frame preset
  is loaded.
- **Phase 7c done: lightweight propped end frames (D2/Q3).**
  - A `propped_end` frame type becomes a 2D frame with pinned internal
    columns at the mullion positions, with its own columns and a continuous
    rafter. It is its own sizing group.
  - Each prop gets the end-wall mullion check (H/2 flybrace, am 1.75,
    H/150) combined with the frame axial force. The envelope's largest
    compression and largest tension are each combined with the full wind
    bending, which is conservative.
  - That ratio feeds `member_and_connection_ratio`, so the optimiser, the
    acceptance gate and the dossier all see it.
  - Mullion lateral load still goes to bracing (D2a).
  - **D2b (decided 2026-11-12): end-wall bracing restrains a propped end
    frame's eaves with a spring.** For the end-wall bracing panel between
    each column and its nearest prop, k = E·A·cos²θ/L of the diagonal. It
    acts on the left eave, or on both eaves when the end walls are braced on
    both sides. This replaces the interior frames' L/2 path spring. Without
    it the frame failed H/80 sway even with 1200WB columns.
  - **Result** (factory building, ends at GL1/GL6, props at 7 and 14 m):
    the ends sized at 150×50×2.5 RHS columns, a 100×50×2.5 RHS rafter and
    200UB18.2 props. The props are governed by the combined check at 0.885.
    Interior frames: 310UB32.0. Both are verified, and resubmission
    reproduces them.
- **Phase 7b done: multiple sizing groups and part-length canopies and
  lean-tos.**
  - Groups must share one 2D frame and may carry `frame_inputs`.
  - Part-length attachments take their own tributary at their end gridlines,
    split the main column into its external part (beyond the lean-to) and
    internal part (alongside it), and integrate end wind over the
    attachment's extent only.
  - Service v2 checks or sizes each group separately and combines the
    results.
  - Configurator Review stays single-group.
- **Phase 7a done: full-length canopies and lean-tos.** The engine,
  `legacy_2d` parity with today's whole envelope on five attachment
  buildings, the the frame engine applier, and service v2.
  - Lean-to and canopy openings take positional Cpe in the `building`
    basis. An open lean-to front at the windward edge gives a more severe
    Cpi than today's first-bay average: −2.97 v −2.85 kN/m on the lean-to
    roof.
  - Still out of scope: part-length or eave-beam attachments (these need
    multiple sizing groups), and skillion + attachment (the frame engine's
    equivalent-geometry remap uses 2D node averages).
- **Phase 6 done.** Service v2 (`portalcalc.calculation.request.v2`) takes a
  building and returns per-gridline sections, distinct load sets, governing
  gridline/case per action, and a resubmittable `sized_building`.
  Configurator Review handles v2, and v1 is unchanged. See
  `CALCULATION_SERVICE.md`.

### Phase 5 results (2026-11-11): factory defaults, full optimiser

| Path | Time | Cost | Sections | Check |
| --- | --- | --- | --- | --- |
| Today (2D frame anywhere) | 19.7 s | $35,404 | 250UB31.4 columns, 75×3 chords, depth 1000 | ratio 0.974 |
| Building basis, unscreened | 83.0 s (4.2×) | $34,048 | 310UB32.0 columns, 65×3 chords, depth 850 | ratio 0.990 |
| Building basis, screened | **27.8 s (1.41×)** | **$34,048** (identical design) | same | ratio 0.990 |

- **Screening.** Round 1 sized on 38 of 274 combinations, and the full
  check passed first time. Three more combinations govern in the full check
  but don't fail it.
- **Saving: −3.8% cost.** The lighter chords and shallower truss come from
  per-frame end wind; the columns are deeper but lighter per metre.
- **The report** now lists the governing gridline and case for every member
  action, e.g. "BOTTOM compression 50.3 kN [0.9G + Wu (Front +, Cpi +) @GL2]".
- **Optimiser fix found along the way: stale protected state.** At start-up
  the optimiser captured its "protected" verified design from the last
  solve's envelope and cost. After a preset reload those still described the
  previous run's design.
  - In building mode a second run in the same window therefore produced
    $37,822 instead of $34,048.
  - The legacy optimiser (`_auto_size_current_preset_once`, removed
    2026-11-13) then re-solved the current inputs before capturing it.
  - Repeat runs are now identical (today: $35,404 twice; building: $34,048
    twice), and today's results are unchanged.

### Phase 4 results (2026-11-11)

- **Parity.** `legacy_2d` restricted to one interior gridline reproduces
  today's whole design envelope to 1e-7 for five buildings, including the
  governing ratio.
- **Combination counts after deduplication** (factory 40 m building):

  | Basis | Combinations | Today |
  | --- | --- | --- |
  | `legacy_2d` | 114 | 57 |
  | `building` | 274 | 57 |

  In the `building` basis, gravity and side wind are shared by GL2–GL5
  and by GL1/GL6; only end wind is per frame. Run time per solve is about
  2.6–2.8× today's (0.6 → 1.6 s). Phase 5's active-set screening targets
  this.
- **Building basis v today**, actions governed by gravity or side wind are
  unchanged.
  - *Factory defaults:* 27 of 33 governing actions unchanged. The 6 governed
    by End wind today drop:
    - TOP chord tension to 67%;
    - BOTTOM chord compression to 62%;
    - column uplift to 75% (now governed by side wind);
    - Ws truss deflection to 80%.
  - *Cyclonic C, light roof:* 16 of 33 unchanged. TOP chord tension drops
    to 84%, now governed by back-end wind at GL5.
  - In both, the overall ratio is unchanged because the factory sections
    are governed by gravity and doubled chords. Phase 5 sizing will show
    the steel saving.
- **Not yet covered:**
  - more than one sizing group (propped ends, partial lean-tos);
  - the diagram and "selected combination" display still show today's
    single-frame loads;
  - no desktop toggle yet (the mode is an attribute).

### Findings from phase 2 (feed into phase 3 / D3)

- **Ka never acts on frames today.** Every frame pressure uses
  `max(Ka·Kce, 0.8)` with Kce = 0.8, and Ka ≤ 1, so the factor is always 0.8
  (0.9 for Roof Only). Proper 3D Ka (D3) changes nothing unless Kc changes
  too.
- **Cpi uses an along-length average for side wind.** Table 5.1(B) Cpi for
  an opening in an end wall during side wind takes the "side wall" Cpe from
  `side_wall_cpe_first_frame`. That averages the Table 5.2(C) bands over the
  first *bay along the length*, even though for side wind the along-wind
  depth is the span. Phase 2 keeps this for parity (chamber Cpi uses the
  nominal bay); phase 3 replaces it with the opening's own band position.
- **Snap to the actual ridge.** For gables at 10° or more, and for free
  roofs, the frame engine splits the two slopes at the actual apex node, not at half
  the nominal slope length. The descriptors carry `roof_layout = "slopes"`
  so the phase 4 applier does the same.

## Goal

BOLT sends PortalCalc a 3D building. PortalCalc applies wind and gravity to the
building's actual surfaces, resolves a load set onto every gridline frame,
decides which frames govern each sizing group, sizes those with the existing 2D
engine, and returns sections per gridline.

The 2D solver, member checks, optimiser and acceptance gate stay as they are.
What changes is *which loads* the 2D frame sees: the actual loads of real
gridlines instead of one "frame anywhere" envelope.

## Where we are today

### The 2D frame (the frame engine)

One generic interior frame, sized for 57 combinations
(`loads.build_load_combinations`: 6 Cpe × 2 Cpi wind cases at ULS/SLS plus
gravity and crane), multiplied by up to 4 storage cases.

Conservative "frame anywhere" assumptions this project removes:

| Assumption | Where |
| --- | --- |
| Full `bay_size_m` tributary on every frame; no half-bay end frame | `portalcalc/wind.py:981,1114,1158`, `portalcalc/loads.py:151` |
| End-wind roof Cpe: the worst bay-wide window of the Table 5.3(A) bands, applied uniformly to the whole roof | `WindCalculationResult.end_wind_critical_roof_line_load_kn_m` (`portalcalc/wind.py:333-383`) |
| End-wind roof h/d taken as h/span; d should be the along-wind depth (length) | same |
| End-wind side walls: first-bay band average on every frame | `side_wall_cpe_first_frame` (`portalcalc/wind.py:636-650`) |
| Both ends clad: end wind takes the larger Md of the two ends; otherwise only one end direction is run | `wind_source_direction_for_case` (the frame engine:11328`) |
| Enclosure is 4 whole-wall booleans; open walls become full-height openings for Table 5.1(B) Cpi | `main_chamber_cpi` (the frame engine:13268`) |
| End frames are never sized separately | — |

### Code we can build on

- **`bracing_layout.LayoutGeometry`** (Qt-free, versioned JSON, written so BOLT
  could supply it): gridline `bay_stations_mm` (unequal bays allowed), span,
  eave, pitch, roof shape, end-wall stations, canopies.
- **`bracing_roof_loads.frame_strip_integrals`** distributes any piecewise
  value along the length onto gridlines by simply-supported bay reactions,
  conserving force and moment. End frames get half bays.
- **`bracing_roof_loads.building_roof_cases`** and
  **`bracing_side_wind.side_wind_cases`** already produce per-gridline loads:
  - end wind in both directions, with Table 5.3(A) bands along the length,
    side-wall bands and real Md per direction;
  - side wind left/right with roof bands tip to tip, Cpi per direction, and
    free-roof Cpn;
  - gravity with placements and canopies.
- **Per-frame 2D re-solves** already work:
  - `tests/bracing_transverse_sls_regression.py:179-215`
  - `restraint_feedback_prototype.py`

### Limits of the 3D bracing load code as a frame-load engine

1. **Wrong output form for frame checks.** Output is a pre-combined
   `loads_n` vector plus equivalent end forces, tied to one deep-copied
   `Structure2D`'s DOF numbering. the frame engine's member checks need:
   - element uniform-load tuples, for forces and deflections within a member;
   - separate Cpe and Cpi basic cases;
   - its own combination machinery.
2. **One portal repeated at every gridline.** There is no per-gridline frame
   type.
3. **Needs a live MainWindow.** Inputs come through `prepare_context(window)`.
4. **Scope gaps.** Lean-tos and skillion canopies are rejected. Crane and
   storage are not modelled, and there are no per-bay openings.
5. **Different factors from 2D:**
   - Kc uses the count of clad end walls (1.0/0.9/0.8); 2D uses 0.8.
   - Ka is based on rafter slope × frame width; 2D uses zone length × bay.

## Target architecture

```
BOLT request (portalcalc.calculation.request.v2)
  └─ portalcalc/building/model.py      BuildingModel: gridlines, frame types, faces/panels, site
       └─ portalcalc/building/loads.py  3D load engine → per-frame BasicLoadSet (descriptors, not vectors)
            └─ frame_groups.py   dedupe + group frames → sizing groups, candidate load sets
                 └─ the frame engine seam (member_check_envelope_lines)  2D solve/check/optimise per group
                      └─ result: sections per gridline, governing frame+case per member group
```

### 1. `portalcalc/building/model.py` (new, Qt-free)

A frozen dataclass with JSON `to_dict`/`from_dict` and a schema
`portalcalc.building.v1`. Building on `LayoutGeometry` rather than replacing it.

| Part | Contents |
| --- | --- |
| Site | Region, IL, terrain category, building bearing (BOLT owns the bearing). |
| Gridlines | Ordered Y stations (mm) and a `frame_type_id` per gridline. |
| Frame types | Preset-shaped `geometry` + `canopies` + `sections` blocks, so `_generate_and_solve_once` can still build each one. **Several frame types from schema v1** (D6): a gridline with a lean-to or canopy is a different frame type from one without. A "lightweight end frame" type adds mullion props (D2). |
| Attachments | Canopies and lean-tos, each with a side and a gridline range (`from_gridline`, `to_gridline`), so they can run part of the length. An optional offset past the end gridline is for the rare eave-beam-supported case (Q2); the v1 engine rejects it. They define which frame type each gridline gets. |
| Faces | Roof slopes, side walls, end walls, lean-to walls. Each is split into per-bay panels with a state: clad or open. Openings are recorded with type, size and position; they are treated as closed for loads (D7). |
| Chambers | Internal pressure zones bounded by faces and internal partitions (Q1), which run across or along the building. Each partition records its line, its extent and which frame members it is fixed to. v1 engine: one chamber, plus lean-to merging. |
| Gravity | G/Q/solar/other kPa with the existing roof placements. Storage and crane carry a bay range (v1: whole length, as today). |
| Sizing groups | `gridline → group` map. Default: one group per frame type, with end frames in the interior group (D1). A lightweight end frame is its own group. |

An **adapter from today's preset** (`building_model_from_preset`) builds a
uniform building:
- gridlines at `bay_size` over `building_length_m`;
- whole-wall enclosure;
- one frame type.

That gives the desktop and every existing preset a path through the new engine,
and gives us the parity tests.

### 2. `portalcalc/building/loads.py` (new, Qt-free): the 3D load engine

For each **wind direction relative to the building** (from left, from right,
from front end, from back end):

1. **Wind speed.** Vdes uses that direction's sector Md
   (`design_direction_multiplier`) or "Any". There is no symmetric-end
   shortcut: both end directions are run.
2. **External surface pressures.**
   - Roof: Table 5.3(A) bands or pitched tables, measured from the true
     windward edge, with the h/d basis correct for the direction.
   - Walls: windward 0.7, the leeward table, side-wall bands.
   - Free-roof Cpn where fully open.
   - Ka and Kc per decision D3.
3. **Internal pressure, once per direction for the whole chamber.** Cpi comes
   from the actual openings via Table 5.1(B):
   - largest opening ÷ the rest;
   - each opening on its face takes that face's Cpe for this direction.

   This generalises `chamber_cpi_from_openings` from whole walls to panels.
   Lean-to chambers follow the existing merge rules.
4. **Resolve onto each gridline** with `frame_strip_integrals`. The output is
   one `FrameLoadSet` per gridline: **basic cases as load descriptors** in
   frame coordinates. Kinds:
   - `RoofStrip(side, from_m, to_m, normal_kn_m)`
   - `WallLine(member, from_m, to_m, kn_m)`
   - `CanopyStrip(...)`
   - `RoofFriction(...)`
   - gravity `RoofVertical(...)`
   - storage and crane per frame
5. **Keep Cpe and Cpi as separate basic cases**, as the frame engine does
   (the frame engine:13952-13979`), so `LoadCombination` factoring is unchanged.

Gravity per gridline: tributary × kPa with the existing placements, using half
bays at the ends and unequal bays where they exist.

**Why descriptors and not load vectors.** the frame engine's own application functions
then apply them as element uniform loads:
- `apply_roof_vertical_loads_in_zones`
- `add_roof_panel_pressure`
- `apply_wall_horizontal_loads`

Results within each member, deflections and the report stay correct.
Descriptors are also independent of mesh and DOF numbering, so they survive
truss-panel and topology changes during optimisation.

The pressure calculations are lifted out of `portalcalc/bracing/roof_loads.py` and
`portalcalc/bracing/side_wind.py` into shared functions. Phase 7 points the bracing code
at the same engine, so there is one source of truth for building loads.

### 3. `frame_groups.py` (new, Qt-free): choosing governing frames

Every frame in a sizing group shares geometry and stiffness. The analysis is
first-order linear and loads are already batched into one matrix solve
(`member_check_envelope_lines`, the frame engine:15470-15487`). So for **checking**,
"size against every frame in the group" costs only extra load columns. That
makes the selection exact rather than a heuristic:

1. **Exact dedupe.** Canonicalise each frame's load set (rounded descriptors)
   and drop duplicates. Interior frames more than 3h from both ends are
   identical in every case, and a symmetric building's mirrored end frames
   collapse under the opposite end-wind direction. A typical building should
   come down to roughly 3–6 distinct frames per group.
2. **Candidate cases** = (distinct frame × combination × storage case). Each
   carries `frame_id`, which goes into the case name, the cache key and the
   envelope `combo` label.
3. **Active-set screening in the optimiser.** After each exact solve, keep the
   cases that govern (or come within x% of governing) any member group,
   deflection item or reaction. Optimiser iterations solve only that set.
   Every accepted design is re-verified against the **full** candidate set.
   This uses the same "reduced screen, exact acceptance gate" pattern as
   `portalcalc/frame/reduced_optimizer.py`.
4. **Report.** For each member group, list the governing gridline and case,
   e.g. "Left column: GL 2, ULS 0.9G+Wu End(back→front) Cpi−".

Second-order analysis is re-applied per case (the frame engine:14808`), so active-set
screening matters most there.

### 4. Integration seam in the frame engine

Use seam (a): `member_check_envelope_lines(structure, combinations,
apply_combination_loads, ...)` at the frame engine:14205`. It is also used by
`foundation_design_from_reactions`.

- **`FrameLoadCombination`** has the `LoadCombination` attributes plus
  `frame_id`. Its `name` includes the frame so the load-vector cache key
  (the frame engine:15452-15464`) stays unique.
- **The `apply_combination_loads` replacement**, when a building load set is
  active:
  - applies that frame's descriptors for the combination's basic cases;
  - applies self weight, crane and storage per frame as today.

  When no building load set is active, the existing path runs unchanged.
- **The optimiser** keeps reading `self.design_envelope_actions`; it only sees
  more cases.

This keeps the desktop's current behaviour as the default. The new path runs
only when a `BuildingModel` is attached to the request.

### 5. Service contract

**`portalcalc.calculation.request.v2`:** `inputs` = BuildingModel plus the
existing non-geometry blocks (costing, foundations, finishes, sections as
starting points).

**Result additions:**
- `frames`: gridline → sizing group → sections;
- `governing`: per group and member group, the frame and case;
- `dedupe`: the distinct load sets and which gridlines share each one.

v1 requests keep working unchanged.

The headless backend runs one sizing per sizing group (same frame type, so the
same MainWindow model) with that group's candidate load sets injected.

## Phases

Each phase lands with its own regression file, and smoke-check steps where
cheap.

| # | Phase | Done when |
| --- | --- | --- |
| 0 | **Decisions + schema** | D1–D7 and Q1–Q3 resolved. `portalcalc.building.v1` drafted with 5 fixture buildings: enclosed gable; 3-sided with canopy; unequal bays with one open bay; partial-length lean-to; lightweight end frame plus partition. |
| 1 | **BuildingModel + preset adapter** | Round-trip JSON, validation errors, and `building_model_from_preset(factory_defaults)` equals today's geometry. Qt-free import test. |
| 2 | **Load engine: gravity + side wind** | Per-frame descriptor sets. **Parity:** for a uniform building, interior frames reproduce today's 2D gravity and Left/Right wind basic cases to 1e-9 (same functions, same tributary). End frames are exactly half. Load conservation over the building. |
| 3 | **Load engine: end wind + Cpi from openings** | Both end directions with roof/side-wall bands per frame, real Md per direction, panel-based Table 5.1(B) Cpi. **Expected-difference tests:** every interior frame's end-wind load is ≤ today's 2D envelope, and each deviation is attributed (h/d basis, Kc). Agrees with `building_roof_cases` resultants per frame. |
| 4 | **Seam + multi-frame checking** | `FrameLoadCombination` and the descriptor applier. For the factory default building with all frames in one group, envelope actions are ≤ today's, and equal on side-wind-governed members. Exact dedupe counts are asserted. |
| 5 | **Governing selection in the optimiser** | Active-set screening with a full-set acceptance gate. Governing frame/case reported. Run time on factory defaults within ~1.5× of today. Sized frames are never heavier than the current path. |
| 6 | **Service v2 + headless** | v2 request → per-gridline sections and governing evidence; v1 unchanged (`tests/calculation_service_regression.py` still passes); Configurator Review loads v2. |
| 7 | **Converge and widen** | Bracing consumes `building_loads` (one engine). Then the engine covers what the schema already describes: partial-length lean-tos and canopies (several frame types per building), lightweight end frames with propped rafters and mullion axial checks, partitioned chambers, storage and crane per bay range. |

**Tonight's realistic target:** phases 0–2, possibly into 3. Phases 2 and 3 are
pure Qt-free functions with closed-form tests, so they are a good night's work
and carry no risk to the desktop.

## Decisions (resolved with the user, 2026-11-11)

- **D1 Sizing groups.**
  - **Default:** end frames are the same frame as the interior frames, with
    non-load-bearing mullions. End frames may be extended in future.
  - **One sizing group:** the end gridlines are part of the envelope, so the
    group also covers an end frame that later becomes interior.
- **D2 Lightweight end frame (option).** An end can switch to a lightweight
  rafter on load-bearing mullions, which commercial builds sometimes use.
  - It is its own frame type and sizing group.
  - The rafter is propped vertically at the mullion positions, so the 2D model
    needs mullion props (pinned) at those x positions.
  - The mullions then carry axial load plus wind bending, so `portalcalc/end_wall.py`
    needs an axial + bending check for this case.
  - An end with this option cannot be extended without adding a frame.
- **D2a Mullion lateral load** goes directly to bracing and is **not** in the
  2D analysis.
- **D3 Ka and Kc** are **calculated properly from the 3D model**, per surface
  and direction, and applied to the 2D load descriptors.
  - **Ka:** the tributary area of the surface contributing to that frame.
  - **Kc:** AS/NZS 1170.2 Table 5.5 for the surfaces acting together on the
    frame.

  This replaces both the 2D rule (0.8) and the 3D bracing rule (count of clad
  end walls). Expected-difference tests attribute every reduction. Rules need
  the user's engineering sign-off before phase 3 lands.
- **D4 h/d and end-wind roof** come from the 3D model: d is the along-wind
  depth for each direction. Wind parallel to the ridge (θ = 90°) uses the
  Table 5.3(A) bands at all pitches, matching the 3D bracing code. This
  changes results for steep roofs and for narrow, tall buildings; record it in
  the CHANGELOG.
- **D5 "Any" orientation:** max Md for every direction. It is not the normal
  case; BOLT will usually send a bearing, and then each of the four directions
  uses its own sector Md.
- **D6 Schema:** not designed yet; PortalCalc defines `portalcalc.building.v1`
  and BOLT maps to it, starting from the bracing-layout contract. It must
  cover more than PortalCalc models today:
  - several frame types along the building;
  - canopies and lean-tos that run part of the length;
  - complex openings;
  - internal partitions.
- **D7 Openings:** doors and openings other than open walls are treated as
  **closed**. Cyclonic and dominant-opening cases are handled by the existing
  Cpi options, not by door states. The schema still records openings (type,
  size, position), so a later rule can use them.

- **Q1 Internal partitions create separate pressure chambers**, running
  across or along the building.
  - Each chamber gets its own Cpi from its own external openings (openings
    closed per D7, so in practice open walls and lean-to interfaces).
  - Load descriptors therefore carry Cpi as **strips along the frame**, not
    one value per frame. A frame crossing a longitudinal partition sees a
    different Cpi on each side, and its columns take the Cpi of the chamber
    each one faces.
  - **Net partition pressure:**
    - A longitudinal partition fixed to an internal column line puts the
      partition's net pressure (Cpi,a − Cpi,b) × tributary into that column
      as an in-plane frame load.
    - A transverse partition at a gridline is out of plane of the frame and
      goes to bracing, like mullions (D2a).
  - **Engine order:**
    1. one chamber plus lean-to merging (today's behaviour);
    2. transverse partitions at gridlines;
    3. longitudinal partitions.
- **Q2 Partial-length canopies and lean-tos.**
  - To start: rafters always on main gridlines, and the attachment starts and
    stops on main gridlines.
  - Rare later case: the attachment extends partly into the next end bay with
    a rafter fixed off an eave beam. The schema allows a start/end offset
    beyond the gridline. The v1 engine rejects it with a clear message.
    Later, the main frames either side take the eave-beam reactions as point
    loads at the eave.
- **Q3 Lightweight end frame:**
  - its own columns, sized with the end frame (lighter than the interior
    frames);
  - the rafter is continuous over the mullion props.

## Risks

- **Run time.** More cases per solve. Mitigated by dedupe and active-set
  screening; measure on factory defaults at phase 4.
- **Results change for existing jobs.** Only when a BuildingModel is
  attached. The desktop defaults to the current path until we choose to flip
  it.
- **Two sources of building loads until phase 7.** The bracing and frame
  engines could disagree. Phase 3 checks per-frame resultants against
  `building_roof_cases` to keep them aligned.
- **Frozen build.** Any new optimiser worker processes need testing in the
  PyInstaller build before release.
