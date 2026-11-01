# Automatic column flybracing

Ordinary clad main and lean-to columns now select flybrace elevations at the generated wall-girt levels. There are no manual controls. Unclad columns and the existing dedicated crane/storage restraint paths retain their previous treatment.

The model builder must install the selected schedule as effective weak-axis lateral and twist restraints for both moment signs. Girt/brace/cleat load paths remain design assumptions; this selector does not calculate connection stiffness or design connections. An intermediate girt flybrace is not an in-plane portal support.

## Fast selection

1. Reuse each solved ULS case's column end forces and line loads. Capture exact local N/V/M polynomials, not a full computation-report snapshot or additional frame solve.
2. Predict one brace near 75% of the base-to-roof-connection height. Evaluate the nearest available girt and its neighbours, choosing the lowest governing utilisation, with distance to the prediction breaking ties.
3. Check every physical segment under every ULS case. Use its actual peak moment and quarter-point moments to calculate am, capped at 2.5, in accordance with AS 4100:2020 Clause 5.6.1.1. Preserve the expression's possible values slightly below 1.0. Axial and moment demands are taken from the same case and segment. Analytic extrema include maxima between nodes and quarter points.
4. If the layout fails, split the governing segment at the available girt giving the best pair of segment checks. Repeat until passing or no girt remains in that segment. Stop early when the segment's section-strength/in-plane-capacity floor cannot pass even with arbitrarily close lateral restraints.
5. Cache interval demands across profiles and section/interval capacities within the current solved force set. The search never enumerates brace subsets. Each additional brace consumes one available girt, providing a natural bound for tall buildings.

The search runs for each section tried by the existing sizing engine, allowing extra lateral restraints before a heavier section is required. It is a practical bounded heuristic, not proof of a global minimum section/layout. No brace-cost calculation is included. Layouts are recalculated when the frame is solved again; cached forces are never reused across solved models.

## Lengths and result provenance

`MemberActions.ltb_length_mm` separates physical lateral-torsional buckling segments from the in-plane compression effective length. The new clad-column path uses ke=1.0 and a base-to-first-roof-connection in-plane length; selecting girts does not shorten it. Each segment's weak-axis length and LTB length are its physical restraint spacing. Other callers leaving `ltb_length_mm=None` retain their existing behaviour.

The complete column result takes the maximum of each capacity-check ratio over all segments/cases. Every check carries its own case, segment elevations, am, quarter-point moments and action record. The displayed representative actions and worked capacity values correspond to the overall governing segment; other rows can govern at other stations and have their own source actions. This preserves reproducibility without inventing an interaction from independent load envelopes.

For zero quarter-point moments the expression uses am=1.0 as a conservative fallback; the peak moment still includes analytic interior extrema. Roof connection nodes split the column above the wall girt region. Mid-column girders/rails/crane connections are not silently classified by this new selector.

## Model-builder output

- Calculation service result: `column_flybraces`.
- Calculation dossier and computation-report evidence: `column_flybraces`.
- Configurator selected-section payload: `columnFlybraces`.
- Column stability context: `flybrace_schedule`.
- Desktop section checks: selected heights above the column base, segment lengths, calculated am and governing case/ratio.
- Geometry view: labelled flybrace markers at the selected heights; wall-girt ticks use the same generated levels.

Each schedule gives group/profile, column x coordinate, base elevation, first roof-node elevation, absolute `positions_mm`, heights above base, and a `braces` list pairing each elevation with its offsets and required restraint function. A failed schedule is explicitly marked `passes=false`; a location list alone must not be interpreted as an accepted design. The schedule is a derived output from the final solved sections, not a new editable preset input. No braces are inserted into the in-plane stiffness matrix.

The generated girt levels start 300 mm above the column base and divide the remaining height equally at or below the existing regional girt-spacing limit, matching the wall-girt capacity check. These are PortalCalc's generated levels: they do not reproduce an external engineer's girt stations unless those happen to coincide.

## Verification

`python -m unittest column_flybracing_regression -v` checks loaded-member polynomial recovery, interior moment maxima, reversal, same-case interaction, length separation, multiple braces on a tall column, caching, failed section limits and the 30 m / 410UB53.7 example with model-builder/report provenance.

For supplied preset 55808, the generated girt layout selects one flybrace at **3525 mm above base**, with first-order left-column utilisation **0.9571** using the current production analysis model. This differs from the external engineer's stated position 1253 mm below the bottom chord: PortalCalc generates its own girt positions. The existing 50x2.5 analysis-placeholder web policy is unchanged. The earlier offline selected-web, second-order and actual-engineer-position investigation remains in `55808_RESTRAINT_RESULTS.md`; those results are not silently substituted for this production model.

Local benchmark on 55808 (three interleaved runs each, same sections, first-order model, topology/web searches suppressed): median 0.7697 s with automatic flybracing disabled versus 0.7816 s enabled; approximately **11.9 ms / 1.5%** additional processing. Timing is machine/workload dependent. Evidence: `tmp/restraint_review/automatic_flybrace_benchmark.json`.

The factory-default end-to-end service regression remains verified after autosizing: 310UB32.0 left / 250UB31.4 right, with governing right-column combined utilisation 0.991265. Both service modes, six flybrace regressions, six internal-column regressions, six computation-report regressions and the full UI smoke path pass. The rendered 55808 computation report was visually reviewed for schedule, source/segment tables and pagination.

This feature retains the app's existing capacity and interaction formulas. It does not establish global sway factors, replace second-order design, verify section compactness, or complete out-of-plane connection strength/slip checks. Source rule: [AS 4100:2020, Clause 5.6.1.1](https://sarah.com.au/wp-content/uploads/2024/09/4100_AS_2020_Steel_Structure.pdf).
