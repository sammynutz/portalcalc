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
case/view changes do not invalidate a frozen calculation. Orbit is locked to
**Invert left/right ON, Invert up/down OFF**, including when opening older presets.

## Scoped checks

The underlying model remains the first-order end-wind model in
`BRACING_ANALYSIS.md`. End posts and roof/side/end braces have common group sizes.
Struts default to per-location assemblies; uniform dedicated steel is an explicit
alternative. A scoped member/SLS pass is not complete structural acceptance.

## Purlins as struts

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

## Automatic 50/50 purlin additions - preliminary reference checks

The user-adopted automatic option is **Sheeted purlin: 50/50 load
(preliminary)**. In a bay containing an addition, half the total original roof
line load goes to the existing purlin line and half to the single added member.
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
No section type or location is forced. The chosen added section saves with each
location and participates in the final exact 3D axial reanalysis.

Under **50/50 strut assumptions**, confirm flange screw fixing to cladding and
the installed bridging count. Manufacturer rotational-restraint requirements
still apply; the adopted split does not verify fastener, cleat, collector or
cladding capacities. Bridging must actually connect to the added member. New
connection/bridging labour and fittings are not priced. Unchecking the fixing
assumption, removing required bridging or losing table coverage prevents these
arrangements from passing. The original roof profile selection is unchanged.

The supplied example selects two C20012 additions with this preliminary screen,
reuses the existing roof purlins at the other 23 analytical paths and leaves
the eaves without additions. This is the lowest checked material/fitting cost
found by the bounded search, not an exhaustive global optimum.

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
the listed forged-rated option and exact matching size prices. Missing M10/M12/M28
quotes require entries; an M30 price is not substituted for an M28 turnbuckle.
The designation alone does not verify fitting, termination, thread or anchor strength.
Wire ropes also require EA and its source, rope $/m and fitting price per physical
diagonal. Nominal circular area is a reference used to express equivalent E only;
it never establishes solid-steel rope stiffness or strength. Unrated/unpriced
options cannot be accepted by automatic sizing. No rope construction is inferred
from the Grade 1570 strength schedule.

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

**Analyse and check** evaluates the current selections and may show failures.
**Auto size for lowest checked cost** screens alternatives, recalculates their
loads and adopts a selection only after all scoped member and SLS checks pass.
Retained failed inputs after a stopped search are explicitly labelled as such.
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

## Boundaries and verification

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
