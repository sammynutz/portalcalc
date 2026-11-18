# Purlin and girt design: one check for 2D and 3D (plan)

Status: phase 1 steps 1 (table helpers in `purlins/tables.py`) and 2 (local
pressure switches) done 2026-11-17. Step 3 done and switched on 2026-11-18
(`ROOF_END_ZONES_ACTIVE = True` in `frame/engine/secondary.py`; snapshot and
baselines regenerated). Next: step 4 (3D reads the shared design).
Phase 2 (girts) outlined only.

Step 3 as built (2026-11-18):
- `purlins/roof.py`: every physical purlin row gets the gable-end patches
  (`end_local.py`) on its actual lapped run (`beam.solve_run`, per-bay I).
  Groups follow the patches: corner rows (RC1/RC2) and field rows (RA1/RA2).
  An end zone is 0-2 bays at each end with each row stepped up the same number
  of gauges from its own 2D section (same profile). Corner rows may instead or
  also get single-span infill purlins between them in the end bays (each infill
  splits the widest gap); the lightest passing combination is kept.
- The 2D selection compares common profiles by weight including end zones.
- Patch geometry as the CSB Purlin & Girt Loads V5.08 local pressure
  calculator (user, 2026-11-18): RA1 a x a, RA2 a/2 x a/2, RC1 a/2 x a/2 at the
  corners (RC1 was a along the purlin before; this also corrects the 3D check).
  Kl multiplies the external Cpe only. RC2 (ridge, pitch >= 10 deg) unchanged.
- Costing: upgraded end-zone lengths come off each row's base item and are
  costed at their own section; infills are a single-span item.
- Section checks text: `ResultsMixin.purlin_end_zone_lines`.

Trial results (end zones switched on in a test process only):

| Job | Today | With end zones |
|---|---|---|
| Factory default | 250 Z 15/19 | 250 Z; general rows 250 19 in 1 end bay; corners pass; +192 kg (+6%) |
| 52195 | 200 Z 15/19, 6.85 t | 200 Z; field rows 200 24 in 2 end bays (plain UC 1.52 under RA1 at the gable ends; one bay leaves bay 2 at 1.21 through lap continuity), corner rows 200 24 in 1 end bay; +1.64 t (+24%) |

Earlier trials with RC1 a long and one gauge per group gave 52195 a 300 Z
roof (+87%) or a 250 Z roof with corner infills (+43%); both were artefacts of
the over-long corner patch, which the user's calculator corrected.

Step 2 as built: `WindInputs.local_pressure_regions` (enabled regions, default
all four); `wind.roof_local_pressure_factor` and `purlins/end_local.py` skip
regions that are off; the form has four checkboxes (`local_pressure_*_checkbox`)
in Wind / snow; the preset saves `wind.local_pressure_regions` only when one is
off; the 2D purlin check, its section-checks text and the 3D workspace summary
state the override.

## Why

Job 52195 (30.85 m span, 7 bays of 8.067 m, 9 m eave, lapped Z purlins) failed
**Optimise frame + bracing** although its bracing layout was sound. The two purlin
checks disagree about the same member:

| | 2D frame purlin selection | 3D bracing purlin check |
|---|---|---|
| Where | `frame/engine/secondary.py` `purlin_check_from_inputs` | `purlins/shared_design.py`, `purlins/end_local.py` |
| Local pressure | Kl from the bay's tributary area, applied as a uniform load along the whole edge-row purlin (RA1 Kl 1.5 here; never RC1, see `wind.roof_local_pressure_factor`) | Table 5.6 patches at each gable end, including RC1 Kl 3.0 corners, analysed along the lapped run |
| Check | Line load against the Metroll table UDL capacity | Elastic run moments against the table-reference moment |
| 52195 left eave, C/Z 200 19 | passes | bay 1 about 2.0, bay 2 1.53 (RC1 front patch) |

No bracing change can fix a purlin that fails in roof bending on its own, so the
optimiser could never finish.

## Decisions (user, 2026-11-17)

- One purlin/girt design, used by the 2D frame (selection, costing, report) and
  read by the 3D bracing, which adds only the axial strut force and the
  axial-plus-bending interaction.
- It is not part of the 3D solve: jobs with bracing off still need purlins,
  the 2D auto-size re-solves many times, and the 3D model builds its struts
  from the installed purlins.
- End zones get their own section, keeping the same profile (depth and shape)
  so laps still work.
- A switch per local pressure region, on by default, for the whole building.
  Any region switched off is stated in the report as a user override.
- Girts: ignore self-weight sag.
- Phase 1: roof purlins. Phase 2: side-wall and end-wall girts.

## Phase 1: roof purlins

### Engineering basis

For each **physical purlin row** (not only the 3D strut lines), treat the row as a
run along the building over the actual bay spans, with the selected span type
(single, double, continuous lapped).

Loads, per row:

1. **General cases, unchanged from today.** Gravity 1.2G + 1.5Q, inward wind
   1.2G + Wd, outward wind, SLS, from the current pressure envelope, uniform
   along the run.
2. **Eave/ridge edge strip, unchanged from today.** Rows within a (RA1) or 0.5a
   (RA2) of the eave or ridge take Kl along their whole length, because the
   whole length is within a of that edge.
3. **Gable-end patches, new to 2D.** Table 5.6 patches at each end, the same ones
   `end_local.end_roof_local_cases` builds for 3D today:
   - RA1 and RA2 for every row (within a or 0.5a of the gable edge).
   - RC1 for rows within a of the eave at the ends (pitch below 10 degrees).
   - RC2 at the ridge (10 degrees and over).

   Each patch is limited to its Table 5.6 area (a squared, or 0.25a squared) and
   placed at the end and at mid end-bay, as now.

The check has two parts:

- **General cases (1, 2):** keep today's direct check, line load against the
  table UDL capacity. Results for purlins away from the gable ends do not change.
- **Gable-end patches (3):** elastic run analysis with `purlins/beam.solve_run`,
  using actual spans and laps. Moments and shears are checked against the
  table-reference moment and shear of each bay's own section (the method in
  `shared_design.py`), and deflection uses the table-adjusted SLS limit, as now.
  The report labels it PRELIMINARY, as the 3D check is today.

Recommended: keep the general cases on the direct table check rather than also
moving them onto the run analysis. For equal spans the two agree, but a 7-span
lapped run against the 5-span table gives slightly different numbers, and
changing every purlin result is not needed to fix the disagreement.

### Section selection

Today: one common profile, with separate thicknesses for general rows and
eave/ridge edge rows (`frame/purlin_selection.select_roof_purlin_pair`, lowest
weighted area).

New: the same common profile, with thicknesses chosen per zone:

| | interior bays | end zone (each end) |
|---|---|---|
| general rows | general section | end-zone general section |
| edge rows | edge section | end-zone corner section |

- The end zone starts at 1 bay and extends to 2 bays only if the second bay
  still fails. In 52195, bay 2 fails mainly through lap continuity from the
  loaded end bay.
- Selection minimises weighted area by length, as today, extended from 2 zones
  to 4. Where the end zone needs no change, its section equals the interior
  one and the result is today's.
- The run analysis uses each bay's own section stiffness. `beam.solve_run`
  takes one roof I per run today, so it needs a per-bay I. Mixed thickness in
  a lapped run is analysed with real stiffnesses and stays labelled
  PRELIMINARY.

### Local pressure switches

- Input: `wind.local_pressure_regions` = `{"RA1": true, "RA2": true, "RC1": true, "RC2": true}`
  (phase 2 adds the wall regions). Old presets without it load as all on.
- UI: checkboxes in the wind inputs. Add them to both `ui_build.py` and
  `HeadlessFrameEngine`, then rerun `python -m portalcalc.desktop.form_schema`.
- Applies everywhere a roof Kl is used: the 2D purlin check (edge strips and end
  patches), the 3D end patches (`end_local`, which will read the same design
  result), and the open-canopy edge purlin.
- Report: one line listing any region switched off, as a user override of
  AS/NZS 1170.2 Table 5.6.

### Code layout

`portalcalc/purlins/` becomes the home of the design (girts join it in phase 2):

- `tables.py`: move `match_metroll_purlin`, `purlin_capacity_row`,
  `bridged_strength_capacity` and the section area/weight helpers out of
  `secondary.py`, with no behaviour change. Girts and canopies use them too.
- `local_pressure.py`: the Table 5.6 regions and switches, built from
  `wind.roof_local_pressure_factor` and `end_local.py`, used by 2D and 3D.
- `roof.py`: rows, zones, load cases, run checks, selection. It returns the
  design result.
- `secondary.purlin_check_from_inputs` becomes a thin call into `roof.py`. It
  keeps the existing result keys (`match`, `local_match`, the loads and so on)
  so current consumers keep working, and adds `end_zone` (sections, bay count,
  governing patch, ratios per zone and bay).

### What has to read the new result

| Consumer | Change |
|---|---|
| `solve.py`:571 eave node depth | none (same profile, so same depth) |
| `costing.py`:229-260 | purlin items per zone, with end-zone lengths and kg/m |
| `results.py`:940-975 label, :1418-1476 section checks text | end-zone sections, governing patch, switches off |
| `engine/bracing.py`:282 (2D longitudinal bracing struts) | per-bay section on each line |
| `bracing/analysis_desktop.py`:388-479 | each strut location takes its own bay's section; `beam_inputs` and the end patches come from the shared design, so 3D bending equals 2D |
| `purlins/shared_design.py`, `beam_design.py` | allow per-bay thickness within one profile (they require one section per run today) |
| `frame/future_canopy_design.py` `secondary_checks` (service output) | add `roof_end` entries |
| `desktop/review_ui.py`:293 | show end-zone sections |
| `tests/fixtures/results_snapshot.json` | regenerate. The cases whose end zones govern change; list each changed case and section for review before accepting |

### Steps

Each step runs the suite and reports any snapshot change.

1. **Move the table helpers into `purlins/tables.py`.** No result changes;
   the snapshot is identical.
2. **Add the local pressure regions and switches.** With all on, the snapshot
   is identical. Test: switching RA1 off gives the general-row section on
   edge rows.
3. **Add the end-zone patch check and 4-zone selection in 2D.** Results
   change; review the snapshot diff.
4. **3D reads the shared design.** Per-bay sections in the 3D purlin data and
   the shared run checks.
5. **Update costing, report text, review UI and service output.**
6. **Prove it on 52195.** 2D now selects a heavier left/right-eave end zone.
   The 3D bending ratio for the same purlin equals the 2D one, and
   Optimise frame + bracing completes (or stops for a different, stated
   reason).

Checks to add (numbers and state, not wording):

- All switches on with no end patch governing: the same sections and costs as
  before.
- For one purlin, the 2D end-zone bending ratio equals the 3D bending ratio
  with zero axial force.
- Costing kg equals the sum over zones of length times kg/m times the lap
  factor.
- Turning RC1 off on 52195 removes the corner patch from both 2D and 3D.

## Phase 2: girts (outline)

- **Side-wall girts:** a run over the bay spacing, like purlins. Wind only (no
  self-weight sag, per user). Wall local regions at the corners; end zones as
  for purlins.
- **End-wall girts:** span between mullions, so their runs come from the
  end-wall mullion layout. End zones at the building corners.
- Move `wall_girt_check_from_inputs` and `wall_girt_design_pressures` onto the
  same core. Consumers: `solve.py`:570, :1088, :1724; `engine/bracing.py`:361,
  :380; costing, `results.py`:976-990 and :1477-1510; service output; review UI.

## Answered (user, 2026-11-17)

1. General cases keep the direct table check; only the gable-end patches use
   the run analysis.
2. End zone length is automatic: 1 bay, or 2 if the second bay still fails.
3. Upsizing the end-zone purlins (thicker, same profile) is fine.
4. Open-canopy edge purlins follow the same switches.
