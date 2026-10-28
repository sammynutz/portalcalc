# Combined 3D bracing workspace

Open **Bracing > Open 3D bracing workspace**. Edit exclusions and pinned X panels
on the left, choose sections/criteria on the right, then Analyse and check or
Auto size for lowest checked cost. Unmarked panels are not installed braces.
Save records layout and analysis settings together; Cancel changes neither.
The production portal, Phase 1 checks, legacy springs and cost totals are not
changed by this workspace.

## Visual output

- Layout constraints: block/unblock or pin/unpin panels. Face filters reach
  hidden panels; Panel list allows keyboard selection. Local Undo/Redo applies.
- Axial forces: red tension, blue compression, grey slack; line width scales
  with force magnitude for the selected wind case.
- Governing utilisation: green <= 0.8, amber <= 1, red > 1, grey unchecked.
  Member utilisation governs across cases, including local SLS checks.
- Deformed shape: exaggerated axial-network nodal displacements over the
  original model, with explicit scale. These are not curved 3D portal diagrams.

Click a member or table row for section, axial force, governing source/mode and
demand/capacity values. Global SLS is reported separately in the status line.
Editing engineering inputs clears old results and disables exports. Camera and
case/view changes do not invalidate a frozen calculation. **Invert left/right**
and **Invert up/down** control each drag axis independently and persist in the
workspace settings. Defaults use opposite signs. Older combined preferences
retain horizontal direction and switch vertical direction to the opposite sign.

## Scoped checks

The underlying model remains the first-order end-wind model in
`BRACING_ANALYSIS.md`. Checks use the existing preliminary member-capacity engine,
with one common section per strut, end-post, roof-brace, side-brace and end-brace
group. A scoped member/SLS pass is not complete structural acceptance.

Editable assumptions default to brace Fy=250 MPa, Fu=410 MPa, An/Ag=0.70,
kt=0.85, strut/post effective K=1.0 and SLS H/150 or L/150. Catalogue steel
sections retain their own material properties. Project holes/threads,
connections and restraints must establish the appropriate net-area, distribution
and effective-length values; defaults do not verify connection details.

The tension check is `phi Nt = 0.9 min(Ag Fy, 0.85 kt An Fu)`. The additional
0.85 net-fracture multiplier is explicit in this feature. The shared legacy
`member_checks.tension_capacity_kn` helper does not include it itself; this
adapter incorporates it in the helper's effective kt. Other production callers
are unchanged and require their own review. Both configured and effective
parameters are retained in evidence.

Compression checks include both global member buckling axes and the existing
hollow local-wall reduction. Strut effective length is full bay length times K;
post effective length is full height times K, without invented flybrace credit.
The existing axial/bending interaction and shear checks are retained.
Strong-axis orientation is assumed for bending.

Dedicated struts also receive local simply-supported self-weight beam checks:
ULS 1.2 times self-weight and SLS self-weight. End posts receive local end-pressure
bending/shear using tributary width and actual height. Post self-weight is added
to compression without taking beneficial credit against tension. Local deflection
uses `5wL^4/(384EI)`; global longitudinal roof drift uses node height / SLS
divisor. These local loads are not fed back into the wind-only global solve.

JSON freezes section properties, criteria, per-case checks, source cases, cost
data and hashes. TXT includes member summaries, cost breakdown and analysis
workings. The low-level solver still returns demands; the workspace attaches
the checking layer.

## Cost-ranked sizing

Pools are the existing SHS/RHS strut catalogue, end-post catalogue and listed
rod/angle brace options. Cost uses current Costing-tab rates, 7% hollow-section
waste, individually rounded rolled-section stock cuts, exact physical brace
lengths and one listed turnbuckle per rod. Both diagonals of each installed X
are priced even when one is slack. Rolled cuts longer than priced stock cannot
be ranked without splice design/cost. Stock and fitting tables are frozen in
the evidence.

This is **estimated material plus listed-fitting cost**. Angle fittings, other
connections, labour, supplier availability and quotations are not priced.
Different rates and waste/stock allowances can make a heavier section cheaper.

Sizing screens catalogue candidates against successive demand envelopes,
chooses the cheapest passing group candidates, reanalyses their stiffnesses and
checks the resulting member/SLS design. SLS failure can raise stiffness floors.
A bounded search of up to 18 solves retains the lowest-cost passing candidate
actually found. Infeasibility, cycles and cancellation do not adopt an unchecked
candidate or change the layout constraints. Candidate screens and analysed
rounds are exported. This is **not exhaustive proof of the globally cheapest
combination**, and does not select a layout automatically.

## Boundaries and verification

Connection detailing, turnbuckle capacity, portal-column weak-axis checks, full
G/Q/transverse/drag combinations, second-order/geometric stability, foundation
flexibility and production portal-restraint feedback remain separate. Neutral
rod stability flags prevent automatic member/SLS acceptance. Production cost
totals and the full-computations PDF are not overwritten.

Run `python bracing_design_regression.py`, `python bracing_analysis_regression.py`
or `python smoke_checks.py --full`. Tests cover independent fracture, beam and
cost workings; a tiny catalogue exhaustively enumerated and exactly reanalysed;
infeasible SLS; cancellation; real orbit/member picking; linked invalidation;
and transactional layout/settings save. Visual QA covers full and compact sizes.
No releases are packaged.

Primary tension reference: [Australian Steel Institute / Australian Tube Mills
design tables, Part 7](https://www.steel.org.au/resources/elibrary/resources/design-capacity-tables-for-structural-steel-hollow/?view=download).
Load-path background: [SCI braced-frame systems](https://steelconstruction.info/topics/design/braced-frames).
