# Combined 3D bracing workspace

Open **Bracing > Open 3D bracing workspace**. Edit exclusions and pinned X panels
on the left, choose sections/criteria on the right, then Analyse and check or
Auto size for lowest checked cost. Unmarked panels are not installed braces.
Save and close records layout, analysis settings and current calculations together;
the window X does the same. Cancel discards edits.
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
case/view changes do not invalidate a frozen calculation. Orbit is locked to
**Invert left/right ON, Invert up/down OFF**, including when opening older presets.

## Reopening calculations

Reopening the workspace restores the tables, forces, beam results, workings and
selected case/view without repeating the 3D analysis. Saving the building preset
also writes an optional compressed `bracing.calculation_3d` result cache. It is
separate from engineering inputs, source hashes, model history and service request
digests, so result payloads cannot recursively enter another calculation.
Old presets without results continue to work. A fingerprint covers the current
source inputs, actual portal stiffness/attachments/supports, layout, member
properties, wind cases, purlin data/tables, criteria, rates/ratings and engine
revision. Stale, corrupt or incompatible results are rejected and require rerunning.
View changes do not invalidate calculations. Failed scoped checks and retained-input
sizing stops restore as failures/stops, never as optimised passes. Cancel preserves
the prior saved workspace; window X saves the current workspace.

## Reading brace loads

This analysis applies wind normal to the front/back end walls, along the building
length (Y). Roof bracing and struts transfer the roof reaction into the side-wall
braces and down to their bases. Front/back end-wall braces lie across the span in
X/Z, so their direct Y force projection is zero. They can still participate through
spatial coupling. A smaller front/back brace is not evidence that the longitudinal
load is missing. Those braces require separate transverse-wind cases before final
sizing; these are not yet included. Peak force also does not alone determine size:
global movement and brace orientation/stiffness can govern.

Result summary shows ULS tension peaks by brace group. Each TXT/JSON case records
the roof-applied Y load, direct-to-base end-wall load, member base reactions by
face, and the roof-to-member-base longitudinal force residual. The direct base
half of end-wall pressure is kept separate from the load carried by bracing.
The summary labels external roof friction separately from normal end-wall
pressure, including its force, coefficient, loaded area and preliminary
open-sided applicability assumption. Wall/underside/exposed-member drag are
excluded. The end-wind leeward coefficient uses length/span,
not the transverse portal's ratio/pitch; pressure reductions count the actually
applied end/internal surfaces. Worked case evidence records the coefficient and
reduction basis. A member-capacity pass in this subset is not final brace sizing.

## Scoped checks

The underlying model remains the first-order end-wind model in
`BRACING_ANALYSIS.md`. End posts and roof/side/end braces have common group sizes.
Struts default to per-location assemblies; uniform dedicated steel is an explicit
alternative. A scoped member/SLS pass is not complete structural acceptance.

The toolbar's **Include serviceability** toggle selects the design basis. It is
on by default, including in older presets. On means strength plus overall
longitudinal movement and local member/purlin deflection checks. Off means
**Strength only**: all SLS cases/deflections still calculate and appear in plots,
workings and reports, but their results do not govern member selection, design
pass/fail or SLS stiffness growth. The summary marks movement **REPORT ONLY**, and
reports local SLS exceptions separately. Member UC/colour and the governing case
then use included strength checks only. A strength-only pass does not establish
serviceability acceptance. Strength capacities, required strength ratings, brace
material rules and taut-brace stability remain mandatory in either mode.
The pre-existing main mullion strength/deflection requirements remain mandatory
for mullion replacements in both modes; the toggle applies to additional bracing
serviceability, not to downgrading the main building's mullion design.

## Aligned mullions

The main end-wall calculation and 3D workspace share
`end_wall.mullion_profile_design`: wind bending/shear, self-weight, catalogue
properties, compression/tension interaction, and the main assumed mid-height
flybrace effective lengths/moment modifier. Each 3D mullion adds its wind-only
axial force to local wind bending. Local bending uses the larger of the main
wind envelope and that end-wind case; height/tributary width envelope the main
layout and actual 3D geometry. This is explicitly conservative independent
enveloping. Open ends receive no clad-wall flybrace or cladding-pressure credit.
No mullion bending stiffness is added to the global axial network.

Old independent post selections start from the main mullion. New selections save
`mullion_basis_version=main_combined_v1`. The 3D auto-sizer checks candidates against
both main prerequisites and bracing interaction. Save and close adopts a section
only with checked passing mullions, verified taut-brace tangent and no stopped
sizing proposal. It records `adopted_mullion_section` in the 3D settings and rebuilds
the main calculation so its mullion section, costing and exports use the same
member. The main UI marks it 3D-adopted; its displayed ratios remain the main
wind check, while the combined axial/bending workings are in 3D results. Cancel
or an unverified replacement does not adopt a new main section. Adopted sections
are rechecked by the main calculation when inputs change, and cached 3D results
are separately invalidated against the current main basis.

The setting saves as `criteria.include_serviceability` (boolean, default true),
appears in TXT/JSON and is part of the saved-calculation fingerprint. Toggling it
clears results; a cached strength-only result cannot be reused for a combined
strength/serviceability check. The reference H/n and L/n values remain editable
for reporting even in strength-only mode.

## Purlins as struts

Eave-aligned struts connect to the first inboard purlin and use the full
roof-check spacing for gravity/wind bending and serviceability. Wind factors
use the full loaded area and that purlin's wind zone. The sheet-edge eave
purlin's half-span tributary is recorded as geometry only. Added-member
self-weight and frame-to-frame compression spans are unchanged. Apex pairs
retain their separate per-slope half tributaries.

Rating entries include the bending-load snapshot in addition to section,
restraint and span. The tables identify tributary width, zone and representative
roof line so different duties are visible. Legacy entries migrate when their
frozen bending snapshot still matches. Old ratings for a changed duty require
review rather than sharing the first matching section's M/V basis.
Common transfer-stiffness inputs and modelling choices are retained across a
load-only change when the old entries agree; strength/residual capacities are
invalidated independently.

Components / ratings lists each bay/roof-line path. Enter the number of identical
existing purlins connected to that path (for example two at an apex). Alternatives
are reuse alone, an extra purlin, a back-to-back mate or additional SHS/RHS.
Braced bays are labelled for context; additions are not forced into particular bays.
Common end translations share axial force by EA. Every component is checked,
with no invented composite bending inertia. Collector/cleat load transfer is assumed
and still requires connection checks. Counts do not change the roof bending design.

Installed purlins default to the project-adopted **Lysaght May 1999 p20 simplified
combined-load calculation**, using Metroll MEGASPAN 2023 p7 full-section properties:
`Ns = 903 Ag/(L/rx')^2`, `Nav = Ns(1-Wa/Ws)` and compression
`UC = Ncompression/Ns + Wa/Ws`. No extra phi, generic net-area/kt factor or Euler
amplification is applied to that expression. `Wa/Ws` is the conservative current
bending envelope; negative available compression is reported and clipped to zero
for display, while utilisation still fails. The full bay span is used, with no
automatic bridging reduction of L. Both inward/outward capacities retain the
installed bending/span/restraint basis and SLS remains a separate bending check.

Select C or Z for each property group; the default is Z. The radius uses the
web-relative/rectangular axis (Metroll labels it rx, corresponding to Ix-prime),
not rotated principal Ix. Ag, radius, span, Ns, bending utilisation and remaining
compression are displayed/exported. Properties and source SHA256 are frozen in
JSON. `purlin_combined.py` contains the transcribed catalogue; no user-local PDF
or network is required at runtime. The inconsistent published Z15024 Ag/I/r row
is rejected without silently correcting it; C15024 has a separate valid row.

Existing explicitly rated presets retain their declared Nc/Nex method. Selecting
that alternative uses `N/Nc + bending/(1-N/Nex)`. **Member tension now defaults
to calculated `phi Nt = 0.9 min(Ag fy, 0.85 kt An fu)`**, using G450 lower-bound
material fy450/fu480 MPa for the manufacturer's G450/G500/G550 range. The
Sections / criteria net-area factor and kt apply (defaults 0.70 and 0.85);
these are explicit project assumptions, not verified holes/connection details.
No cold-work enhancement is credited. The additive `N/phiNt + bending` screen
takes no beneficial tension effect. UI, TXT and JSON show the inputs, yield and
fracture capacities, reference and governing value. Supplier Nt/source remains
an explicit alternative; existing entered capacities retain declared mode.
Invalid or stale declared capacities do not silently fall back to calculation.
Sheeted beam additions may use the same calculated member tensile check when
their separate Nt is blank; their cladding and M/V data are still required.
Unsheeted added-ply residual ratings remain separate. The compression formula
does not provide Nt; the tension calculation is a separate member check.
The adopted historic method is a scoped compression screen, not a claim that
Metroll published the Lysaght formula or a complete current AS/NZS 4600 design.

Tension formula: [Hancock, Design of Cold-Formed Steel Structures, 4th ed,
p170](https://www.steel.org.au/getattachment/09a22a15-030c-4d27-b341-7959aa5fd49b/Cold-formed-members-in-combined-axial-tensile-load-and-bending_bk150.pdf).
Material minima: [BlueScope GALVASPAN G450 datasheet](https://metaltex.com.au/downloads/GALVASPAN_G450.pdf).

Added cold-formed plies require separate residual capacities
after their applicable bending and restraints, plus SLS deflection. Back-to-back
sharing halves one existing ply's bending only when explicitly declared.
Changed bending snapshots invalidate added-ply ratings; mismatched span/axial
bases cannot pass. Missing required data remain visibly UNVERIFIED and cannot auto-size.

Original method: [Lysaght Zeds & Cees, May 1999 reprint, p20](https://www.scribd.com/document/993312318/1999-Complete-Purlins).
Property source: user-supplied `metroll_MEGASPAN_Design_Manual_23.pdf`, p7,
SHA256 `3de61ae96adffca89fbc902a4368ed6fb7d8939eefc04faaa8689761c7dcae38`.

Only added material is costed for these strut alternatives; existing roof purlins
are already in the building cost. Added purlins use the captured purlin rate and
7% waste. Each candidate is reanalysed with its actual per-path EA before adoption.

## Automatic purlin additions - preliminary reference checks

The user-adopted automatic option is **Sheeted purlin: 50/50 load
(preliminary)**. In a bay containing an addition, half the total original roof
line load goes to the existing purlin line and half to the single added member.
The **Added back-to-back pair: equal roof load per ply** option adds two new
identical purlins alongside the existing roof purlin(s). With one existing member,
each of the three physical purlins carries one-third of the original total roof
load. With the two existing apex purlins plus two additions, each carries one-quarter.
Every added ply has an independent single-span beam/buckling check and its own
self-weight; no composite bending I, built-up buckling enhancement or reduced
effective length is credited. Axial force shares by EA across all connected plies.
The existing-run continuity and adjoining bay loads remain in the beam analysis.
For multiple linked originals the total original line load is count times the
load per original purlin. Remaining bays retain their full applied load. This is
an explicit load-allocation assumption, not sharing derived from a fastener
stiffness or equal bending moments. Added self-weight is separately included in
downward cases; favourable self-weight is not credited against uplift.

The existing run is solved with its actual continuity and the changed bay loads;
every adjoining bay is checked. Each added purlin is independently single span.
Axial forces share by EA across all physical components, without composite
bending stiffness. SLS uses separate wind directions, G plus inward service
wind and G+Q. No spring transfer is calculated under this assigned-load option.

**Strength acceptance remains PRELIMINARY.** Unit-load elastic reference
analyses are scaled to the current manufacturer's UDL capacity for the relevant
single, double or five-span lapped arrangement and bridging level. Reference
moment is the largest absolute per-ply moment over the rated span group;
reference shear is its largest per-ply shear. Actual M/V envelopes are divided
by those reference actions. The scoped ratio is axial interaction plus the
larger normalised M/V envelope. These are **reference actions, not independent
member resistances**; the approximation does not establish full cold-formed
buckling, bearing or moment/shear interaction for the changed diagram.

SLS retains both the gross-EI calculated deflection and the deflection adjusted
to the manufacturer's L/150 table-rated reference response, using the larger
ratio. Source rows, spans, bridging, interpolated columns, cache SHA256,
reference actions, actual diagrams, comparisons and assumptions are frozen in
TXT/JSON. The overall workspace explicitly says **PRELIMINARY PASS** when it
uses any of these reference checks. Missing data cannot produce acceptance.

The reference option supports equal single spans, equal double-span pairs
(final odd bay assessed as single) and exactly five Z spans with 15% laps for
tables 5A/5B. Nonstandard releases, unequal spans or other lap configurations
require the declared-capacity beam option. Single-span tables 1A/1B remain
separate from the installed roof tables. The catalogue C purlins with verified
properties and span/bridging rows compete on added material cost with SHS/RHS.
Added purlins must match the installed roof's catalogue profile size/web depth;
thickness can differ. Candidate generation, manual selection and frozen beam
checks enforce the same rule. A legacy different-size added section migrates to
an available matching-size section for reanalysis; missing capacity/restraint data
still prevent acceptance. The old declared-residual mate-to-existing arrangement
remains distinct from the new two-addition pair.
No section type or location is forced. The chosen added section saves with each
location and participates in the final exact 3D axial reanalysis.

Under **Shared-load strut assumptions**, confirm flange screw fixing to cladding and
the installed bridging count. Manufacturer rotational-restraint requirements
still apply; the adopted split does not verify fastener, cleat, collector or
cladding capacities. Bridging must actually connect to the added member. New
connection/bridging labour and fittings are not priced. Unchecking the fixing
assumption, removing required bridging or losing table coverage prevents these
arrangements from passing. The original roof profile selection is unchanged.

Selection compares reuse alone, a matching-size single addition, a matching-size
added pair and the other rated alternatives by checked incremental cost. The
passing candidate found by this bounded search is not an exhaustive global optimum.

The user-confirmed gable apex detail defaults to two existing connected purlins,
one from each slope, at the common apex analytical line. This credits both axial
areas and checks each physical member under its existing bending. Single-slope
and other lines default to one. Existing default count=1 presets migrate to the
installed pair; explicit new manual overrides and non-default legacy counts are
retained. The source, installed default and selected count are frozen in the
component records. Common-end axial transfer remains a connection assumption.
Each apex purlin takes half the adjacent roof-sheet end span as its tributary
roof width, derived from the actual purlin layout on each slope. A 1200 mm sheet
end span therefore gives 600 mm per apex purlin; its longitudinal member span
between frames remains unchanged. If the two widths differ, the larger is used
as a per-ply envelope; if adjacent rows are unavailable, the full roof-check width
is retained. Apex bending/serviceability demands and beam area-load inputs use
this local width, while member buckling length/capacities and added-member
self-weight are unchanged. Widths, source, connected count and actual frame span
are shown in inspection/TXT/JSON and included in bending-demand invalidation.

Primary source: [Metroll MEGASPAN October 2025 manual, design assumptions p5
and tables 1A/1B, 2A/2B, 5A/5B](https://www.metroll.com.au/wp-content/uploads/metroll_MEGASPAN_Purlin_Design_Manual_25.pdf).

## Optional finite-coupling beam model and declared capacities

Choose **Sheeted single-span purlin (beam model)** at a strut location. This is
separate from the existing unsheeted and declared back-to-back alternatives.
The 3D engine continues to solve axial forces using the actual total EA and
common-end force sharing. A small first-order beam solve covers the full affected
roof line, including adjoining bays without additions. It does not add a global
roof diaphragm or change the production portal model.

Existing roof continuity follows the selected Single span, Double span or
Continuous lapped arrangement. Double spans form pairs starting at the front,
with a final odd bay single. Continuous lapped runs assume fully effective laps,
default total lap length 15% of span, with half on each side of the support.
Lap bending stiffness is the sum of ply EIs, and moments/shears are distributed
per ply. These are explicit run/detail assumptions; no lap slip is modelled.
Each added purlin has independent end rotations and is single span.
The roof-release input can override the default run segmentation: `auto`,
`none`, or 1-based bay numbers such as `2,4` (a rotation release after those
bays). No lap stiffness is credited across a release. The actual release list
is displayed/exported so the assumed run can be checked against the detailing.

Cladding fasteners are the stated connection. The model uses an entered finite
distributed stiffness **kN/m per mm of relative vertical displacement** (numerically
N/mm2). This is normal roof-load transfer between the two lines, distinct from
sheet diaphragm shear or torsional/lateral purlin restraint. Metroll's published
10,000 Nmm/mm rotational restraint is **not** this transfer stiffness.

Also enter the direct roof-load pickup fraction for the added line (0–1). This
represents how the sheeting initially delivers roof load to its attachment lines;
the springs then redistribute further load by relative displacement. Neither
input is inferred from fixing both purlins to cladding. A supplied source must
cover the sheeting profile, geometry/separation, fixings and the adopted linear
range/directions. Blank data give fallback demands (defaults: full load to the
existing line, zero coupling) and remain UNVERIFIED. An explicit zero with a
source is an intentional uncoupled model, not a missing value.
The beam mesh refines for short transfer lengths; stiffness requiring more than
48 elements per bay or more than 1600 DOFs is rejected rather than silently
using an under-resolved rigid-coupling approximation.

Loads use the current general/edge pressure and gravity inputs: ULS inward
envelope, ULS outward without favourable added-member gravity, SLS outward,
SLS G plus inward wind envelope, and conservative SLS G+Q. Added-member
self-weight is included in downward cases. Loads are not duplicated by roof
sharing: direct pickup plus the remaining original roof load equals the original
total; spring transfer is equal and opposite. Existing linked counts retain the
same roof load per original member as the previous model.

**Redistributed member capacities are separate from demands.** Enter design
member M+, M- and V ratings for the actual roof/added restraint and moment
configuration, with a capacity source. The main Metroll UDL tables are not
converted into arbitrary moment capacities. The scoped check uses the adopted
Ns compression screen (or the existing declared Nc/Nex alternative), separate
Nt, linear N/M and conservative quadratic M/V envelopes. Missing stiffness,
pickup, M/V or required Nt cannot pass. Changed bending snapshots invalidate
beam M/V entries. Full cold-formed stability/biaxial/bearing and all cladding,
fastener, lap and collector connection acceptance remain separate.

**Purlin beam results** plots per-ply moments, shears, deflections and distributed
cladding transfer for each roof load case. TXT/JSON include the full-run load
inputs, sources, peak demands, transfer forces and equilibrium evidence.
Automatic sizing re-solves the affected run for a trial arrangement. The final
combined candidate must pass exact global reanalysis and all adjoining member
checks before adoption. The bounded search is still not a global optimum.

Run `python purlin_beam_regression.py` for independent single/double-span beam
benchmarks, finite-coupling equilibrium, direct load pickup, reversal, mesh
convergence, laps, missing-data gates, plot/save/invalidation and exact reanalysis
of an adopted sizing candidate. Test stiffness/capacity inputs are synthetic.

## Project brace schedule

`bracing_schedule.py` records the user image supplied on 29 October 2026, retained
in `assets/project_bracing_schedule_2026-10-29.png` with its SHA256.
Grade 300 rods are restricted to 16, 20, 24 and 30 mm, with factored capacities
54.28672, 84.823, 122.1451 and 190.8518 kN. Grade 1570 wire ropes are 8, 10,
12, 14 and 16 mm, with factored capacities 22.96, 35.92, 51.2, 70.4 and 92 kN.
These supplied factored strengths are used directly, without applying phi or a
generic net-area reduction again. The generic criteria below apply to other steel.

Both forged-rated and Grade S turnbuckle designations are recorded. Costing uses
the listed forged-rated option and exact matching size prices. Wire M10 and M12
use the user's $4 each interim allowances; M16 and M20 retain $9.62 and $16.60.
Missing M28 quotes require entries; an M30 price is not substituted for an M28 turnbuckle.
The designation alone does not verify fitting, termination, thread or anchor strength.
Wire ropes use declared EA/source or the project-adopted interim stiffness below,
plus rope $/m and fitting price per physical diagonal. Nominal circular area is
independent of the supplied factored strength. Unpriced options cannot be accepted
by automatic sizing. No rope construction is inferred from the Grade 1570 strength schedule.

Angle/rod brace yield strength is the user-confirmed 300 MPa. The UI adopts it
for older saved 250 MPa defaults and displays it read-only. The existing
Fu=410 MPa assumption remains editable; this change does not infer a new Fu.
Scheduled Grade 300 rod strengths and G1570 rope strengths remain unchanged.
Other editable assumptions default to An/Ag=0.70,
kt=0.85, strut/post effective K=1.0 and SLS H/150 or L/150. Catalogue steel
sections retain their own material properties. Project holes/threads,
connections and restraints must establish the appropriate net-area, distribution
and effective-length values; defaults do not verify connection details.

Steel tension-only braces use the user-adopted member-only screen
`0.9 Ag Fy`, with Grade 300 for angles and rods. Holes, threads, shear lag,
net fracture, fittings and anchors remain separate connection checks. Results
identify this scope explicitly; a pass is not complete tension-member acceptance.
Scheduled rod strengths already include 0.9 and are not reduced a second time;
supplied wire-rope strengths are also used directly.

Other calculated steel member tension retains
`phi Nt = 0.9 min(Ag Fy, 0.85 kt An Fu)`. The additional
0.85 net-fracture multiplier is explicit in this feature. The shared legacy
`member_checks.tension_capacity_kn` helper does not include it itself; this
adapter incorporates it in the helper's effective kt. Other production callers
are unchanged and require their own review. Both configured and effective
parameters are retained in evidence.

Steel strut/post compression uses gross member-body area, independent of the
configured tension connection deductions. Connection holes/end details remain
separate. The shared engine preserves its prior area behaviour for other callers
unless a separate compression area is provided. Compression checks include both global member buckling axes and the existing
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

### Project brace material policy

**Components / ratings > Project use / brace preference** saves an explicit
project classification. Agricultural prefers wire rope; Commercial / industrial
prefers rod. Unspecified preserves cost-only family ranking for older presets;
importance level is not used to infer building use. Family preference ranks
passing, eligible and priced candidates before estimated material cost. Among
the preferred family, the cheapest checked section is chosen. Missing or
inadequate preferred options can fall back to another eligible family. The same
ranking is used for the retained best whole-building result, so an initially
cheaper rod design cannot silently override an agricultural wire preference.
Both uniform-steel and per-location purlin sizing honour this policy.
If a preferred candidate's reanalysis fails, an already verified fallback is
retained with its matching section selections and evidence; a failed preferred
selection is never paired with a previous candidate's passing result.

**Rod is mandatory on unclad walls**, independent of project use or prices.
Eligibility uses the actual front/back/left/right cladding flags and pinned
panels from the building context. Side-wall and end-wall braces currently have
one section per group: if an installed brace is on any unclad face, that entire
group must use rod (including its clad faces). Roof bracing remains independent.
Angles are not a substitute for the required rod. Manual wire/angle selections
on these wall groups are visibly unverified and cannot produce a passing design;
auto-sizing filters them out. The policy does not add or move any braces.

The standard wire product is the user-confirmed **Austlift 6x24 fibre core G1570**.
Legacy unspecified construction labels migrate to this description, while
explicitly declared custom construction data are retained. The user schedule's
already factored strengths remain unchanged; no manufacturer minimum breaking
load is substituted or factored again. Austlift's [8 mm product certificate,
part 864080](https://www.austlift.com.au/lifting/cdn/productdocument/864080-batch-21209.pdf)
identifies 6x24+FC RHOL, G1570 and specified breaking load 28.7 kN. Its
construction/strength data do not establish axial stiffness for the full range.
The [Austlift fibre-core product family](https://www.austlift.com.au/lifting/products/category/441/1/austlift/fibre-core-wire-rope)
also distinguishes other constructions that must not be mixed into this basis.

**Interim stiffness adopted by the user, 31 October 2026:** default to E = 200 GPa
with nominal circular area pi*d^2/4, giving EA = 200*A kN. This is an explicit
solid-steel modelling assumption, not a supplier-verified effective rope modulus.
Ropes remain tension-only and the supplied factored strengths are unchanged.
The assumed stiffness may underpredict stretch; omitted cladding stiffness does
not establish that it is conservative. Global diaphragm stiffness is excluded by
default; the separate optional SLS stressed-skin model is described in
`BRACING_ANALYSIS.md`.
Each diameter has an editable choice between this assumption and declared EA/source.
Previously entered EA is retained; older blank ratings adopt the interim assumption.
Explicit invalid or missing declared EA/source still blocks analysis/checks.
Results, member inspection and TXT/JSON record the adopted basis and EA.
Supplier EA (kN), or effective modulus with matching area basis, can replace the
assumption later.

**Project rope pricing, 31 October 2026:** 8/10/12/14/16 mm use
$1.35/$1.75/$1.90/$2.40/$3.50 per metre. The supplied 12 mm range is $1.80-$1.90;
sizing adopts its upper end. Source: `assets/project_wire_rope_prices_2026-10-31.png`,
SHA256 `E8DEA355AA5A2DFF1AD297ECDECE57CEC9B5056DA95AD90EFBBF08118A16BD51`.
Cost includes one matching forged-rated turnbuckle per physical diagonal, including
the slack diagonal of an X. The prior schedule maps ropes to M10/M12/M16/M16/M20;
M10 and M12 are explicitly user-adopted $4 estimates. Grips and other termination
hardware are excluded at the user's request; connection capacities remain separate.
Rates and turnbuckle allowances are editable. Old blank prices migrate to these
defaults, existing entered prices remain, and deliberately cleared prices after
migration stay missing. Zero/invalid rope prices or missing turnbuckle allowances
cannot become free passing options. TXT/JSON retain the rate source, quoted range,
adopted rate, exact turnbuckle size/count and exclusions. Synthetic fixture prices
are not inserted into real presets.
TXT/JSON freeze the project rule, active faces, rod-required faces, selected
family and preference rank alongside the existing member/cost evidence.

**Analyse and check** evaluates the current selections and may show failures.
**Auto size for lowest checked cost** screens alternatives, recalculates their
loads and adopts a selection only after all scoped member and SLS checks pass.
SLS stiffness growth is capped at the largest eligible, priced section in each
group (and each location's strut alternatives). A saturated group may remain at
its maximum while other groups increase; the search does not demand an impossible
further increase and stop prematurely. At a strut location, an EA target above
every locally passing, priced arrangement is capped at the strongest checked
alternative; other groups can still increase. A very large steel addition that
fails the existing purlin's local bending must not prevent a checked purlin pair
from being tried. Every combined proposal still requires exact reanalysis.
Once a passing design is found, remaining
analysis budget is used for lower-cost/preferred grouped alternatives. Every
change is exactly reanalysed and must pass all scoped member, stability and SLS
checks before adoption. Before the final grouped pass, up to four coupled trials
compare the strongest matching single/pair purlin additions with current braces
or the stiffest eligible priced braces. This permits cheaper purlin struts to be
traded against brace stiffness; it does not force a material family or alter any
pinned panels. The current per-location strut plan then stays fixed during the
final grouped cost-reduction pass. The default total budget remains 18 exact
3D analysis attempts, including rejected cost-reduction trials. Trial counts and
adoption decisions are frozen in TXT/JSON. This is a bounded search, not proof of
global optimality or of layout infeasibility when no candidate is found.
Retained failed inputs after a stopped search are explicitly labelled as such.
The workspace's persistent Result summary separates overall longitudinal SLS
movement (actual mm, limit, ratio and governing node) from members failing ULS
strength, local SLS deflection and missing checks. Counts are per member and can
overlap. Wall X-panel counts make the analysed restraint layout visible. A stopped
auto-size states that original selections are retained, and an SLS-driven stop
opens the governing movement case automatically. Review movement works for any
analysed result, selects that case, resets display magnification to x1 and circles
the governing node. Member tables/details identify the check type; inspection
selects the actual governing component rather than the first row from that case.
The view labels FRONT/BACK/LEFT/RIGHT, with left/right defined looking from the
front. Actual wall cladding is shown as a translucent overlay with CLAD/OPEN
labels and a Clad walls visibility toggle. The analysis freezes the effective
face-cladding flags. This is a visual reference only and adds no diaphragm or
other cladding stiffness to the analysis.
Default Metroll roof purlins no longer require manually entered Nt to run this
search; their calculated member tension uses the displayed project assumptions.

The roof purlin report also compares the minimum-weight compatible pair for
every passing common profile, preserving independent general/edge thicknesses.
For the 8 m double-span default, C25015/C25019 uses 83.13 kg per metre of
building across 9 general/6 edge rows, versus 92.76 kg/m for C20019/C20024
(11.6% more material). C20015 general and C20019 edge fail uplift at 1.319/1.835
kN/m against 1.230/1.790 kN/m capacities. Roof material ranking uses the common
purlin $/kg rate; bridging/connection labour and supplier-specific profile
prices are not included. No fixed C200 or C250 preference is imposed.

Pools include per-location purlin/addition arrangements, the existing SHS/RHS
strut catalogue, end-post catalogue and scheduled rod/wire plus angle braces.
Cost uses current Costing-tab rates, 7% hollow-section
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

If no strut alternative can be checked, the workspace retains the analysis for
the original input selections and shows an actionable sizing stop. It identifies
the bay/eave/roof line, section, source case, demand and missing/failed check;
rejected alternatives are listed in Full results and frozen under
`sizing_failure` in JSON. **Review sizing issue** opens the matching input row
(for example Declared Nt/source). No failed sizing proposal is adopted. Editing
an input clears this retained analysis and diagnosis as usual. Cancellation and
solver errors remain separate from missing-data/capacity stops.

When global longitudinal SLS movement is still over limit and stiffness floors
exhaust the eligible strut alternatives, the stop reports both original-input
and screening SLS ratios, movement/limit and governing node/case. Review sizing
issue opens the retained SLS deformed shape at scale 1. A fixed-layout sizing
search does not add wall/roof braces or prove that further member increases can
resolve a load-path deficiency; layout alternatives must be reviewed separately.

## Boundaries and verification

The optional SLS stressed-skin model in `BRACING_ANALYSIS.md` participates in
exact serviceability reanalysis and sizing. Its surrogate diagonals are not
physical brace candidates, costs or strength checks. Conventional ULS and bare
SLS stability remain independent prerequisites.

The actual purlin offset from the idealised roof interface is recorded;
collector connection stiffness/capacity remains assumed.

Connection detailing, turnbuckle capacity, portal-column weak-axis checks, full
G/Q/transverse/drag combinations, second-order/geometric stability, foundation
flexibility and production portal-restraint feedback remain separate. Neutral
rod stability flags prevent automatic member/SLS acceptance. Production cost
totals and the full-computations PDF are not overwritten.

Run `python bracing_design_regression.py`, `python bracing_analysis_regression.py`
or `python bracing_components_regression.py`, or `python smoke_checks.py --full`. Tests cover independent fracture, beam and
cost workings; a tiny catalogue exhaustively enumerated and exactly reanalysed;
infeasible SLS; cancellation; real orbit/member picking; linked invalidation;
and transactional layout/settings save. Visual QA covers full and compact sizes.
No releases are packaged.

Primary tension reference: [Australian Steel Institute / Australian Tube Mills
design tables, Part 7](https://www.steel.org.au/resources/elibrary/resources/design-capacity-tables-for-structural-steel-hollow/?view=download).
Load-path background: [SCI braced-frame systems](https://steelconstruction.info/topics/design/braced-frames).
