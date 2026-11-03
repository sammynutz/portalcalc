# Bracing location planning

Open **Bracing > Open 3D bracing workspace**. The default editing mode blocks
panels where bracing cannot go. Every unblocked panel remains available; it is
not a selected brace. **Find layout and size** chooses a passing arrangement from
these panels. **Keep selected panels as required** optionally retains every
currently pinned/selected panel; it defaults off, so previous automatic selections
do not freeze the next search. A blocked panel cannot be pinned. Blocking a pinned
panel removes the pin.

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

## Maximum transfer length in bays

The workspace's Maximum braced-bay spacing defaults to 5. Every consecutive
five-bay group must include a full roof-braced bay aligned with side-wall
bracing; up to four intervening unbraced bays are permitted. A braced eave
strip alone is not an intermediate full roof/wall bay. The ends of the
building are also checked for excessively long unbraced runs.

Before sizing an initial proposal, a short path planner adds the fewest
intermediate qualifying bays, then favours fewer added panels. It follows
available full roof/side-wall locations with at most the permitted bay gap.
This takes O(number of bays × permitted spacing), with no structural trials
or brute-force panel enumeration to choose the intermediate locations.
Existing selected/required panels remain. Blocks can move a proposed anchor
to a nearer bay; if no compliant chain exists, the search reports the spacing
constraint without silently increasing the limit. Later compaction, relocation
and deletion proposals must still satisfy the rule before a structural solve.

For 20 bays with retained end bracing, the default arrangement has qualifying
bays 1, 6, 11, 16 and 20. The explicit project setting is saved at
`bracing.analysis_3d.layout_search.max_braced_bay_spacing`; legacy jobs use 5.
It applies to practical and detailed searches, fixed-layout acceptance and
section-only sizing. Changing it invalidates the checked-result cache.

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

## Layout search and fixed-layout analysis

Saving locations does not run a structural solve or grant restraint.
**Find layout and size** defaults to **Practical layout (fast)**. It prefers
complete roof/side-wall bracing near the two ends, moving inward only where
blocked locations require it. A current selected layout is verified first as a
possible cost incumbent, rather than discarding a passing arrangement. A wide one-sided arrangement (nominal span/eave
ratio >= 4) tries an available continuous roof girder at the supported eave,
with end-wall bracing and end-bay collectors, before repeated full braced bays.
Distributed end-wall patterns remain available as alternatives. This ratio only
orders candidates; it is not a design limit or a promise that bracing is needed
at a particular width. For narrower one-sided arrangements, a severe initial
SLS failure moves to end-wall restraint before repeated section growth.

End-wall braces are in X/Z. They do not directly take longitudinal Y force;
their effect on roof distortion/plan twist and open-side sway is verified by the
coupled 3D solution. SLS sheet diaphragms can redistribute forces and affect
which location is economical, but do not replace the conventional ULS load path.

Practical mode includes continuous longitudinal roof-bracing strips at each eave
and at both eaves, tied to the end-bay roof collectors and end-wall braces. The
eave connected to the available vertical wall bracing is proposed first. All
cells of a continuous strip must be unblocked; the search never silently bridges
a missing cell. For a wide one-sided shed, the first proposed band extends inward
from the supported eave to at least one quarter of the horizontal roof width.
This is only a candidate-ordering heuristic, not a design criterion. Paired outer
end-panel anchors, full end restraint and narrower bands remain alternatives.
Roof-row patterns can reduce repeated full braced bays while
retaining the conventional ULS load path. Intermediate central roof/roof-and-wall
bay patterns are also available before adding both neighbouring end bays.

With a positive panel installation allowance and remaining trial budget it
tries up to four compact load-path patterns within the overall layout budget,
stopping at the first cheaper passing option. Failure of one narrow strip does
not terminate the girder-family comparison. A continuous eave girder is
preferred to deleting arbitrary whole bays. When no such compact pattern is
available, a four-bay layout can contract by one interior bay before trying a
two-bay jump; an existing continuous girder is never split this way. The trial
warm-starts from the passing sections. An established girder keeps its continuous
band and depth when reducing end restraint. Brace EA is retained for that load
path; a new girder can reconsider large angle/rod groups inherited from another
topology. It adopts only a passing lower total cost
(within project material preferences). A failed/more expensive comparison keeps
the original passing layout after bounded alternatives are exhausted. Setting the allowance to zero restores first-pass
stopping. This is not a single-panel deletion sweep or broad cost search. Default
caps are six topology trials and twelve exact analyses per sized topology. The
initial analysis is reused for sizing; a passing input incumbent is verified
once without repeating catalogue sizing. Mixed strut options are cost-ordered,
and the first locally passing priced option is selected; the combined plan and
all affected adjoining roof runs still require exact acceptance. Local option
screens recalculate only the changed roof line, including all adjoining bays;
final exact acceptance recalculates every affected line and member. SLS-only
adjustments compare the cheapest stiffer choice in each brace group by cost per
relative EA gain; this only ranks proposals, not predicts acceptance. Groups may
be revisited until the solve budget or catalogue is exhausted. Accepted brace
EA increases are retained through later ULS/local sizing corrections, preventing
shrink/grow cycles. Posts/struts do not receive global-sway EA floors.

Feasibility has first use of the twelve-solve budget. If budget remains, the compact
comparison permits at most two cheap exact checks for a lighter roof/stronger
wall/balanced-end brace proposal, with local refinement only within
the same twelve-solve cap. Full coupled/group cost-polish searches are skipped.
The report records total layout
search time. These caps may stop without a passing result; exclusions never relax.

A passing layout with angle/other steel side-wall braces also compares up to two
extra-bay rod alternatives before compacting, within the same total layout budget.
The first missing wall bay is chosen in the largest gap between existing wall
bracing. An additional wall X panel alone is tried where a complete eave girder
already connects it; a complete available roof collector at that bay is the other
alternative. All existing panels and required selections remain. The trial starts
with the stiffest eligible priced rod and restricts that wall group to rods during
fast section sizing. Missing fitting prices exclude an option. The incumbent angle EA is not imposed on the replacement rod group. Only an exactly checked
preferred/lower-total-cost result replaces the incumbent, including the additional
panel installation allowance. This comparison also operates at zero installation
allowance. It does not change **Size selected layout**, which keeps its panel set.

Older settings without a strategy now open in practical mode with six trials,
rather than retaining the old 48-trial cost search as the default.

**Detailed cost search**, selected explicitly, compares independently resized engineering
arrangements from available panels: an existing input layout, both end bays and
individual front/back/interior bays with roof and available wall bracing.
It retains two promising starts, tests completing/aligning roof bays and moving
bracing between bays, then refines individual panels and re-sizes the survivor.
Bay completion is also re-sized when the original sections pass: extra braces
may reduce collector/brace demands enough to lower the total checked cost.
This can escape layouts where every single-panel deletion fails, without
enumerating all combinations. Duplicate topology/section checks are skipped.
Its initial budget is 48 layout trials, adjustable from 1 to 160. Each trial
either runs one exact analysis or a section search capped at 12 exact analyses.
At that budget, up to six engineering starts are resized, with a dense
feasibility fallback if none passes. Existing selections are an optional start
even when they are not required. The objective follows project brace-family
preferences, then estimated materials, listed fittings and panel installation
cost. Other unpriced labour and connections remain excluded. This bounded heuristic does not
prove a global optimum or infeasibility when no passing candidate is found.
Whole bay patterns guide exploration; an irregular layout may still win when its
exactly checked total cost is lower. Symmetry and minimum panel count are not
substitutes for brace, collector/strut and post costs.

## Panel installation allowance

**Installation per braced X panel** in Sections / criteria defaults to **$50**,
is editable (including zero), and saves with the workspace/preset. Each physically
installed roof, side-wall or end-wall X panel is charged once for its crossed
pair. Slack diagonals remain installed; load cases do not multiply the allowance.
Struts, posts, unselected panels and SLS diaphragm equivalents do not incur this
charge. This is a flat installation allowance, not a complete installed quote.

The JSON/TXT cost breakdown separates materials/listed fittings and installation,
and the reported total and both search strategies include the allowance. Editing
it clears stale results and changes the calculation cache fingerprint. The 3D
workspace cost remains separate from the main building/Phase 1 totals.

Every adopted result passes all scoped member checks, enabled local/global SLS,
material eligibility and conventional bare-model taut-tangent stability. SLS
diaphragms cannot replace a conventional ULS load path. Blocked panels are never
relaxed and required selections are retained in every trial. The JSON result
records trial layouts, errors, costs, acceptance, seed/duplicate counts and the
budget; TXT summarises the constraints, chosen panels and resized alternatives.
Cancellation/failure leaves layout and
sections unchanged. Success is one undoable layout edit; undo clears results.

**Analyse and check** and **Size selected layout** retain the fixed-layout
workflow, using exactly the selected/pinned X panels.
See `BRACING_ANALYSIS.md` and `BRACING_DESIGN.md` for scope and assumptions.
The count-based Phase 1 bracing design, brace sizes, costing and
calibrated portal eave spring are unaffected.

**Invert left/right** and **Invert up/down** control the drag axes independently
and save with the workspace settings. The default uses opposite axis directions.
Force, utilisation and deformation views inspect members without editing
constraints. Switch back to Layout constraints to edit; a location change clears
previous analysis/check results.

The search retains the current end-wind analysis scope. Full connection design,
installation costs and production portal restraint coupling remain separate
integration steps. End-wall action in the portal plane remains distinct from
longitudinal restraint.

Run `python bracing_layout_regression.py` and
`python bracing_layout_search_regression.py` for focused checks or
`python smoke_checks.py --full` for the broad suite. Set `QT_QPA_PLATFORM=offscreen`
for unattended UI checks.
