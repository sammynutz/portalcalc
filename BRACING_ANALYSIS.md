# Fixed-layout 3D bracing analysis

This is a small-displacement gravity and wind demand model for a fixed layout. The
combined workspace adds scoped member checks and section sizing described in
`BRACING_DESIGN.md`. A solved model is **not a passing structural design**.
The workspace can now search layouts built from unblocked panels, using this
fixed-layout solver for every candidate (see `BRACING_LAYOUT.md`). The frame
restraint it finds can be adopted by the production 2D portal; see
**Frame restraint for the 2D portal** below.

## Desktop workflow

1. Establish the portal sections in the normal 2D workflow.
2. In **Bracing > Open 3D bracing workspace**, block unavailable panels. Unmarked
   panels are available search locations, not installed braces. Optionally select
   panels for fixed-layout checks or as required locations during search.
3. Select the five section groups (six with a canopy: **Canopy roof X braces**)
   and check assumptions in Sections / criteria.
4. Use **Find layout and size**, **Analyse and check**, or **Size selected layout**. These
   choices are independent of Phase 1 and legacy portal-restraint controls.
5. Read the assumptions, ULS member envelopes, SLS movement and per-case
   reactions/equilibrium checks. Select forces, governing utilisation or displaced
   bracing; click a member for its source case and demand/capacity values.
   Export TXT for readable workings and JSON for complete evidence,
   including recovered portal nodal movements and local member end forces.

Analysis section choices save under optional `bracing.analysis_3d` with schema
`portalcalc.bracing-analysis-settings.v1`. Legacy presets omit it. Results are
retained when the workspace closes and optionally saved with the building preset
as a compressed `bracing.calculation_3d` cache. Reopening validates the current
inputs, physical model and engine revision before restoring without a 3D solve.
Changing a section, check assumption or layout clears the previous result and
disables exports until another run. The evidence freezes the source
preset and its SHA256, actual analysis geometry, sections, load workings and
case results. Cancel discards unsaved edits; Save and close or the window X retains
the current workspace and calculations. Configurator Review locks inputs and automatic
sizing while allowing analysis and visual inspection.

Global longitudinal sway follows the main in-plane eave criterion: project
default **H/80 for IL1** (H/100 before 11 November 2026), H/150 for IL2+, with
explicit main limits retained.
A separate bracing global H/n override is available. Local member/purlin
deflection keeps its independent L/n limit (default L/150). The adopted global
criterion and source are frozen in JSON and shown in Result summary/TXT.
Global sway is measured on the main building: every portal roof node above
ground, using its own height H. Open canopy edges are excluded, because they
move with their own canopy bracing beyond the column line
(`bracing_analysis.building_sway_node`). The SLS stiffness screen uses the same
nodes.

The production portal is rebuilt once before opening the dialog. The 3D adapter
then works on a private unloaded copy: it removes legacy eave springs/restraints,
retains actual base supports/springs and internal columns, and does not change
the live frame. The nominal location
grid is mapped proportionally onto actual main portal centrelines; roof/brace
lengths use those actual coordinates. Both nominal and actual grids are exported.
Identical portal sections repeat at each longitudinal station, including ends.

### Scope: canopies, lean-tos and open ends

- **Open canopies** (canopy type **Canopy**) are supported on gable roofs. Each
  canopy stays part of every portal (stiffness, self-weight and loads) and adds
  a braceable canopy roof face; see **Canopies** below.
- **Lean-tos** are rejected with an explanation (open canopies are supported).
- **Rejected canopy arrangements:** a skillion roof with a canopy (the 2D frame
  swaps its wind geometry), and a canopy with an eave-height override that does
  not attach at the main eave. The canopy root must lie on the braced eave line.
- **Buildings with both end walls open** are analysed. With no end-wall
  pressure, the along-building load is roof friction on the top and underside of
  the roof (see the friction section below). They are rejected only when the
  building is too short for any friction (d <= 4*min(h,b)) and there is no
  end-wall pressure: "No along-building wind load".
- **Fully open structures** (all four walls open, a free roof in the 2D frame)
  use the 2D free-roof net coefficients in 3D: AS/NZS 1170.2 Table B.4(A) for end
  wind, and Table B.5 windward/leeward for side wind, with no internal pressure.
  The **External max** branch takes the uplift alternative. Partially open
  buildings keep the external Cpe plus internal Cpi model.

## Elements and load path

- Roof interface nodes have X/Y/Z translations. X is across the portal from the
  left column centreline; Y goes front to back; Z is vertical. Units are N/mm/MPa.
- All pinned panels receive two independent tension-only diagonal rods, without
  prestress or a connection at their intersection. Compression force is zero.
- Dedicated bilateral axial struts run along every roof station through all
  building bays. They are additional assumed members, not an automatic claim
  that existing purlins can also carry the axial force.
- Intermediate end posts are bilateral axial members at actual mullion stations,
  at ends which are clad or have pinned end bracing. Their section is explicit.
  They do not provide weak-axis bending stiffness. No ground post is invented
  beneath a gable ridge which is not a mullion station.
- Brace/end-post ground translations are fixed. Portal base supports and springs
  come from the 2D model. Foundation flexibility and connection slip are absent.
- Sheet/diaphragm stiffness is excluded by default. The optional SLS-only roof
  model is described below. There is no weak-axis portal column bending.
- Portal frame braces (see **Portal frame braces** below) are condensed wall
  moment frames on the eave Y translations.

## Portal frame braces

A portal frame brace (`bracing_portal_brace.py`) sits in one side-wall bay. It
is a UB column stitch-welded inside each portal column plus a UB beam
moment-connected across the top at eave level, with pinned bases on their own
base plates.

- **Frame model.** Each is a 2D Euler-Bernoulli frame in the wall's Y-Z plane:
  columns bending about their strong axis along the building, rigid knees, beam
  over the bay. It is statically condensed onto the Y translations of the two
  eave nodes (`model.add_block` on those DOFs). The columns carry their own
  axial forces to their own bases, so nothing couples vertically with the
  portal's X/Z block.
- **Simplifications.** The stitch-weld offset and the portal columns' own
  weak-axis stiffness are ignored. No cladding or self-weight load acts on the
  brace.
- **Recovery and balance.** Every case recovers member end forces (N, V, M) and
  pinned-base reactions, including search-only analyses, because the design
  checks need them. The base reactions enter the building force/moment balance
  and the load-path evidence under the face `portal_left`/`portal_right`.
- **Stiffness check.** The condensed stiffness matches an independent
  `main.Structure2D` solve of the same frame. Its total sway stiffness is within
  1% of the closed form K = 12 E Ic Ib / (h² (Ic L + 2 Ib h)), slightly softer
  because of axial strain.
- **Design.** Design groups are `portal_brace_column` and `portal_brace_beam`,
  covered in `BRACING_DESIGN.md`. Locations and the layout model are in
  `BRACING_LAYOUT.md`.

## Optional SLS stressed-skin roof and wall contribution

In **Sections / criteria > SLS stressed-skin roof and wall contribution**, enable
roof, clad side walls and clad end walls independently for serviceability.
Each family has an editable flexibility c in mm/kN; **smaller c gives greater
stiffness**. New settings use the supplied technical note **Revision B, 2 November
2026**: roof reference c = **3.0 mm/kN** at a = 8.500 m across ribs and b = 12.104 m
along the roof slope; wall reference c = **6.0 mm/kN** at a = 7.000 m horizontal
width and b = 6.000 m rib-parallel height. These are provisional engineering trial
inputs, not characteristic test values or demonstrated conservative bounds.

The **Rev B reference c + panel geometry** mode calculates each panel's actual
flexibility using the explicitly documented empirical profile-distortion proxy:

`c_panel = c_reference * (a_panel/b_panel^2) / (a_reference/b_reference^2)`

Roof a/b are actual bay length/sloping roof length. Wall a/b are actual panel
width/height; sloping end panels use mean corner height for this calibration.
The model uses its analysis attachment geometry, with collector offsets and
connection slip remaining separate assumptions. The proxy is applied to total
panel flexibility as an engineering screening assumption, **not** a validated
whole-panel law or a complete ECCS component calculation. Different profiles,
fixings, support spacing or materially different panels require renewed review.

**Direct panel c** retains a geometry-independent sensitivity override. The
roof shear direction can be changed only in that mode; Rev B defines roof c
for along-slope shear. Existing saved direct values are migrated into direct-c
mode, preserving their meaning. **Apply Rev B roof + walls (3 / 6)** enables
roof and clad side/end-wall options, sets their reference values to 3/6 and
selects geometry calculation. Individual families can then be disabled. Controls
save under `analysis_3d.stressed_skin`, including `calibration_mode`.

Each bay gets one panel across each complete roof slope (one across a skillion),
independent of the mullion/brace strip mesh.

**Open canopies** follow the roof option (there is no separate canopy setting).
With the roof enabled, each open canopy gets one panel per bay from the main eave
line to the canopy edge purlin, on the existing eave and canopy-edge nodes (no
new nodes), with the same crossed tension-only diagonals (`roof_diaphragm`
kind, ids `diaphragm/canopy_{side}/bay/{b}/0|1`). It uses the roof c and, in
geometry mode, the roof proxy with a = bay length and b = sloped canopy width
`sqrt(width^2 + fall^2)`. Because the proxy scales with a/b^2, a narrow canopy
gets a much softer (larger) panel c than the main roof: in the same bay, a
2.5 m canopy's c is (3.0/2.5)^2 = 1.44 times a 3 m canopy's. Canopy panels are SLS
only, like the roof; the canopy sheets, their transfer into the main roof at the
eave and the edge-purlin collector are assumed continuous and unchecked. The
report lists them as a separate "Open canopy roofs" line and `canopy` panel
count, which appears only for buildings with a canopy. On a typical canopy
braced in every bay the sheets mainly reduce the canopy edge's movement and its
racking relative to the eave; the eave line stays governed by the roof bracing. Each panel has two crossed
tension-only diagonals, each assigned the full area
`Aeq = Ld^3 / (E c s^2)`, where E = 200 kN/mm2 and Ld = sqrt(s^2+n^2).
The initial shear-displacement direction defining s is **along the roof slope**,
matching Rev B. The current applied wind is longitudinal. The report therefore
also gives the equivalent model's implied longitudinal flexibility; it is not a
separately validated directional test value. A longitudinal definition of c can
be selected when adopting a directional flexibility. The note's range must not
be assumed to validate that direction or another installed assembly.

**Wall sensitivity inputs:** side-wall and end-wall reference c values are
independent, initially 6.0 mm/kN; direct mode instead applies the entered value
to every panel in that family. Only faces marked clad in the building
inputs receive wall panels. Side-wall panels span one bay and use horizontal
longitudinal shear (s = bay width, n = wall height). End-wall panels span adjacent
mullion stations and use horizontal transverse shear. They provide zero direct
Y resistance but can influence the coupled X/Z and roof-brace response.

For sloping end panels, each actual corner-to-base diagonal gets its own area
from its actual length, s and height. This calibrates each load direction to
1/c for horizontal top-edge slip, rather than assigning a mean-height rod to
both diagonals. Panel tables give mean-height reference geometry and list both
actual diagonal properties. Gable infill above the line between top corners is
not assigned additional stiffness. Roof and wall panels share existing structural
collector nodes. Side-wall ground nodes are fixed anchor assumptions; unused
anchors have no links or stiffness in the bare/ULS model. Continuous wall sheets,
collectors and anchorage are assumed. Partial wall openings, connection slip and
sheet/fastener/collector/anchor capacities require separate assessment.

Conventional members remain installed. Every ULS case uses the bare network;
every SLS case solves both the bare and enhanced networks. Bare-model mechanisms
still fail, and a neutral bare tangent still prevents checked acceptance. Panels
do not cross the ridge or add ridge-flashing links. Roof panels connect to existing
structural roof nodes; collector and boundary connections are assumed. Wall
options add stiffness only; screw/collector strength and connection-slip acceptance
remain separate.
This option does not add transverse wind or alter the production portal model.

TXT/JSON retain c, its direction, s/n/Ld, Aeq, EA, equivalent diameter, bare and
enhanced longitudinal movements, diagonal forces, `Vpanel = Ndiag*s/Ld` and
average shear flow `Vpanel/(n/1000)`. SLS surrogate members appear as teal dashed
lines. Per-case net panel shear sums the signed contributions of opposite
diagonals, so equal tensions due to panel extension do not become net shear.
Individual diagonal forces remain available for boundary/collector checks. The
surrogates appear as separate stiffness-only table rows and are excluded from
physical member sizing and purchased quantities. Wall base reactions are also
separated from conventional member reactions in the load-path evidence. Changing any option, c or direction
invalidates the calculation/export cache. `bracing_diaphragm_regression.py`
checks independent racking stiffness and reversal, directional conversion,
ULS independence, recovery, sizing and saved-workspace behavior, and the open
canopy panels (count, nodes, c/area, ULS independence, SLS edge movement, report).

TXT/JSON include the Revision B calibration records: roof test c = 4.5 mm/kN on
a 6 x 6 m specimen, geometry adjustment to the reference roof, judgement factors
1.50 and 1.20, raw c about 2.820 and rounded reference 3.0; wall LTP20/LTP45 test
maxima 2.753/4.854, depths 17.4/43 mm, interpolation to 29 mm (3.705), reference
geometry, thickness/pitch exponents and assumed 175.1 mm reference pitch, judgement
factors 1.20/1.20, raw c about 5.941 and rounded reference 6.0. Every panel retains
its actual a/b, geometry factor, adopted c and equivalent diagonal properties.
Measured source inputs are distinguished from empirical interpolation/allowances.
The note's 0.42 BMT five-rib fixing/support assumptions remain explicit.

Base wind cases cover front-to-back/back-to-front, Cpi +/- and ULS/SLS. Each
uses simultaneous windward/leeward pressures from the existing wind module,
with actual source direction, enclosure flags and pressure factors. Reversing
wind swaps physical end identities for the Cpi model. `End +`/`End -` in the
existing roof-pressure API are coefficient branches, not opposite wind directions.
An open end receives no cladding pressure.

End-wind leeward coefficients use the along-wind length/span ratio and the
parallel-to-ridge coefficient branch, independently of the transverse portal's
roof-pitch lookup. The corrected leeward coefficient is also used for dominant
leeward-end openings. End-pressure action-combination factors count only the
applied clad end surfaces and an effective internal surface (abs(Cpi)>=0.4),
with these factors conservatively retained when normal roof/side-wall loads are
added. Case evidence freezes d/b,
Cp,e, surface count, Ka/Kce/Kci and the source: AS/NZS 1170.2:2021 Amendment 2,
Table 5.2(B) and clause 5.4.3. Transverse cases and connection acceptance remain
separate.

External roof friction is added simultaneously in each end-wind case, using
clause 5.5/Table 5.9: mean roof height h, crosswind breadth b, alongwind depth d,
and projected roof area b * max(d - 4*min(h,b), 0). Down-slope sheet ribs run
across longitudinal wind: Cf=0.04 for 5-Rib (Trimdek), 0.02 for Corry (Corodek).
Unknown profiles require a configured coefficient. The matching ULS/SLS velocity
pressure is used without end-wall Ka/Kce/Kci reductions. The downwind strip is
mirrored for reverse wind and integrated onto roof stations and bay lines,
including a cutoff inside a bay. All roof drag enters the roof network; it is
not split half to ground like the simply supported end-wall pressure.

For open-sided buildings this external-roof method is explicitly labelled a
PRELIMINARY extension of the enclosed-building rule. It is not a complete
free-roof aerodynamic check. Wall friction and exposed-member drag are excluded.
The summary and full TXT/JSON expose the coefficient, load strip,
projected area, force, source and applicability assumption. Adding this load does
not add cladding/diaphragm stiffness. Old cached calculations are invalidated.

Friction surfaces (each uses the same Cf, windward exclusion 4*min(h,b) and
projected-area rule, with no Ka/Kce/Kci reduction):

| Surface | When it is loaded |
|---|---|
| Main roof top | Every end-wind case. |
| Main roof underside | Only when **both** end walls are open, so wind passes under the roof. One open end keeps top-only friction. |
| Canopy top and underside | Always, for each open canopy: canopy plan width * max(d - 4*min(h,b), 0) on each surface. |

The underside and canopy surfaces apply the enclosed-building friction method to
those surfaces and are PRELIMINARY. JSON records `surfaces` for the main roof and
a `canopies` list (side, width, surfaces, projected area, force and basis).
Canopy friction is shared between the eave-line and canopy-edge nodes (see
**Canopies**). It is included in the force balance and the load-path check, under
the workings key `{side}_canopy_friction`.

End-wall pressure acts normal to the front/back walls along Y. Side-wall braces
have a Y component and carry longitudinal forces to ground; front/back end-wall
braces are in X/Z and have no direct Y component. Their coupled spatial actions
include transverse ULS wind cases, so their transverse strength demands can
govern independently of longitudinal end-wall wind.
Per-case load-path evidence resolves actual axial forces at their ground ends,
reports reactions by wall face and balances the roof-applied Y load separately
from pressure delivered directly to end-post bases.

End cladding/mullions use the stated simply supported vertical-strip assumption.
Half of each strip's resultant is delivered to roof and half directly to ground.
Two-point Gaussian integration preserves the resultant and overturning for the
piecewise linear roof height. Roof forces interpolate onto roof stations; ground
forces interpolate onto real end-post stations. This is a declared load model,
not a separately verified cladding/girt/mullion connection design.

The 3D combinations also apply gravity and normal wind pressures through the
actual portals. G includes roof dead load, solar, fire services, HVAC, other
permanent roof loads and portal member self-weight. Roof load placements follow
the main input extents. Q uses the larger of entered live load and the main
snow service load. ULS includes 1.35G, 1.2G + 1.5Q and both 0.9G + W / 1.2G + W
for every end-wind pressure branch. SLS follows the main project's G, G + Q,
Ws and G + Ws checks.

Normal roof wind uses Table 5.3(A) crosswind-slope R bands measured along the
building length from the windward end, with both external-pressure branches and
the same internal pressure as the accompanying end-wall case. Clad side-wall
normal pressures use Table 5.2(C); open side walls receive no pressure. Each bay
delivers load to its two portal lines using simply supported reaction shape
functions. This integrates pressure boundaries within bays, conserves force and
longitudinal moment, and gives half adjacent-bay gravity tributaries at the ends.
Local Table 5.6 patches remain separate purlin bending checks, not global patches.
The global model excludes purlin continuity redistribution and uses identical
portal sections at every line; end-post and installed end-brace stiffness can
still produce different end/interior frame movements and additional axial loads.

ULS side-wind checks run from both left and right, with both external roof
alternatives, both internal-pressure branches and 0.9G/1.2G. Side sources use
the same orientation convention as the main portal (270/90 degrees minus the
building's north angle). Roof normal pressures use horizontal Table 5.3(A)
bands for low pitch, Table 5.3(B/C) slope coefficients for pitched gables, and
the appropriate upwind/downwind branch for skillions. The pitched downwind
coefficient remains present in both alternative roof patterns. Windward/leeward
side-wall pressures act on clad columns only. Table 5.2(C) suction on clad end
walls varies across the span; equal opposing end resultants do not remove the
local tie actions. Pressure-band edges split integration intervals, including
partial loads within roof members. Consistent FE forces/couples preserve both
force and moment, with original distributed-load vectors retained for recovery.

Transverse external roof friction uses Cf=0.01 parallel to the down-slope sheet
ribs beyond 4*min(mean height, building length). It acts tangentially to each
roof slope and is mirrored with the wind. Open-sided use retains the declared
preliminary extension of the enclosed-building method. All side-wind cases use
the conventional ULS model, with no stressed-skin credit. Member ULS envelopes
and sizing include both wind families. Transverse friction stays on the main
roof only, as in 2D.

## Canopies

**Geometry.** The plan width and fall of each canopy come from the actual 2D
canopy roof members (`LEFT_CANOPY_TOP` / `RIGHT_CANOPY_TOP`), measured from the
main eave. The layout geometry records them (see `BRACING_LAYOUT.md`).
Each portal is condensed onto the roof stations and then the canopy edges. The
canopy edge points are appended after the roof stations, so the left eave stays
the first point (the origin and balance conventions do not change).

**Network.** Each frame gets a canopy edge node. The canopy edge purlin is a
strut line after the roof stations (`strut/bay/{i}/line/{n_roof_stations + k}`).
Canopy X-braces run from the main eave line to the canopy edge in each pinned
`canopy_{side}/bay/{b}` panel. Their kind is `canopy_brace`, so they are checked,
sized and costed as their own group (`BRACING_DESIGN.md`). A canopy edge has
no support along the building unless that canopy has roof bracing. A fixed layout
that leaves a canopy unbraced gets a specific message, not a generic instability.

**Canopy edge purlin.** Sections > Canopies has, for each canopy, a purlin
section (**Auto (lightest passing)** or a Metroll section) and a spacing (blank
means the main roof spacing). These are saved as `{side}_purlin_section` /
`{side}_purlin_spacing_mm` in the preset `canopies` section. The 2D canopy purlin
check uses the Metroll bending tables with canopy G/Q and the open-underside wind.
It spans the bay, with a tributary width of half the spacing. Its result is the
installed-purlin record for the canopy edge strut line, so strut checks treat it
like the main eave strut (axial plus bending). Without a passing canopy purlin
check, the strut location reports a canopy-specific missing-purlin error.

**Loads, computed in 3D per frame (not copied from the 2D frame):**

- **Gravity:** canopy G/Q/solar from the preset `canopies` kPa, applied to the
  canopy roof members as in 2D (full canopy, no snow), plus self-weight.
- **End wind:** the top uses the same per-frame Table 5.3(A) roof bands as the
  main roof. The open underside uses `wind.open_canopy_cpi`, which is the 2D
  single-opening rule of Table 5.1(B) (opening Ka times that frame's average
  side-wall Cpe) with Kci. Fully open structures use the B.4(A) free-roof Cpn
  instead.
- **Side wind:** roof bands run over the continuous roof from the windward
  canopy tip, as in 2D (`roof_cpe_segments(..., left_ext, right_ext)`; with no
  canopies the output is identical). Canopy members take band Cpe plus canopy
  Cpi; fully open structures use the B.5 free-roof Cpn with no Cpi.
- **Friction:** top and underside, shared between the eave-line and canopy-edge
  nodes (see the friction table above).

**Known difference from 2D.** 3D canopy side-wind loads match the 2D frame within
0.6%, except for the leeward canopy in pitched-gable (>= 10 degrees) "−" cases.
There the 3D model keeps the downwind suction, as it does for the main roof,
instead of the 2D zero alternative. Example: 2D −10.9 kN against 3D +4.4 kN on
the test frame.

**Sway.** Canopy edge nodes are not part of the global H/n sway check, which
is measured on the main building (see the start of this document). Canopy
braces are still checked for strength.

**Limitations:** canopy diaphragm credit is SLS only and follows the roof
stressed-skin option (see that section), and one canopy strip per bay. The
network has no intermediate canopy purlin lines; only the canopy edge is a strut
line.

## Transverse SLS frame sway (report only)

The same side-wind patterns are also solved at serviceability pressure, as
**Ws** and **G + Ws** for both directions, roof alternatives and internal
pressures, matching the main portal's serviceability combinations. They use
the bare conventional network: stressed-skin surrogates are not added, even
when enabled for longitudinal SLS. These cases do **not** govern 3D sizing:
global longitudinal sway acceptance, the response-guided stiffness screen and
the reported maximum resultant remain gravity/longitudinal only, and the main
2D in-plane sway check remains the acceptance. Member local-deflection rows
(including aligned mullions under end-wall side suction) and the taut-rod
tangent status do include them.

`transverse_sls` in the result reports, per frame line: the larger column-top
X movement in Ws-only cases against the main in-plane eave criterion (H/80 IL1
default), the same frame's sway under the same loads without the 3D network,
the governing G + Ws movement and the secant roof-level restraint
`-sum(roof interface X) / mean eave X` with its range across cases that sway
the unrestrained frame by more than 1 mm. Struts carry no X force, so frames
outside roof-braced bays receive zero restraint. A negative value means roof
bracing ties the frame to a softer neighbour and adds load. The secant value is
case-specific because of tension-only slackening and load pattern. The section
appears in TXT/JSON and as a one-line summary in the workspace.

Agreement with the main 2D portal: both now apply roof wind normal to each
panel (the main portal previously applied it vertically). For gable roofs the
unrestrained interior-frame Ws sway matches the main column-top check within
0.5% (factory defaults: 50.91 vs 50.96 mm at 7.5 deg; 127.8 vs 128.1 mm at
22.5 deg). Skillion roofs use one adopted basis in both models: wind on the
high side takes Table 5.3(A) bands measured horizontally from the windward
high edge at every pitch (the main portal previously applied a uniform worst-bay
Cpe). Factory geometry as a skillion agrees within 0.5%: 156.4 vs 157.0 mm at
5 deg, 335.0 vs 336.6 mm at 12 deg and 695.2 vs 697.5 mm at 20 deg.
Low-pitch Table 5.3(A) bands are horizontal distances from the windward edge
in both models; the main portal converts them to positions along the slope
(it previously measured them along the slope).
`bracing_transverse_sls_regression.py` checks the SLS/ULS scaling, recovery
and an independent 2D portal solve, bare-network use with diaphragms enabled,
and that longitudinal acceptance is identical with and without these cases.

During layout search and section sizing the transverse SLS cases are deferred:
the search runs without them and the adopted design is then checked with every
case, reusing the cases already solved for it. If that final check fails where
the reduced search passed, or a deferred case cannot be solved (a Ws-only case
with no gravity can leave an unstable tension-only rod set on a light design),
the search is repeated with every case.
`transverse_sls_search` in the result records this. Practical searches that pass
on their first trial gain nothing; searches with several exact analyses save
about a quarter of each analysis.

## Frame restraint for the 2D portal

`frame_restraint` in the result gives, per frame, the least and greatest secant
roof-level sway restraint over every transverse ULS and SLS case that sways the
unrestrained frame by more than 1 mm. The adoptable value is the least value
over **interior** frames: the 2D model analyses one representative frame with
identical sections, so the least-restrained interior frame governs. End frames
are excluded because end posts and end-wall bracing support them directly (a
single eave spring overestimates their moments by a factor of three to six) and
they carry half the roof load. If any interior frame gets zero or negative
restraint, for example when only end bays are roof-braced, nothing is adoptable.

In the main window, **End-wall portal restraint > 3D bracing model** applies the
adopted value as one spring on the mean of the two eave X movements
(`Structure2D.add_sway_spring`). It resists frame sway but not eave spread, which
interior-frame roof bracing does not restrain. On Save and close the workspace
adopts the value from a passing, taut-stable, unstopped result
(`bracing_restraint.adoption_record`), saved as `bracing.restraint_3d`.

The record is valid only for the bracing it came from:

- **Stale, no credit:** layout geometry, pinned panels, brace/strut/post
  selections or declared rope EA have changed.
- **Recheck, credit kept:** portal sections, loads, wind or canopies have
  changed. This lets the 2D portal be re-sized with the restraint it was given;
  the 3D bracing must then be rechecked with the final portal.

The results text states which applies. While the option is active, 3D sizing and
layout search keep each brace group at least as stiff as the adopted record
(`floor_candidates`), so the bracing cannot be sized below the restraint the
frame was credited with. The 3D model's private portal copy drops any adopted
sway spring, so it never credits itself. On factory defaults with a continuous
eave girder, the adopted 0.335 kN/mm reduces 2D Ws column-top sway from 51.0 to
26.9 mm against 26.0 mm for the 3D interior frames.

## Optimising frame and bracing together

**Bracing > 3D bracing locations > Optimise frame + bracing together...** runs
`frontier_sizing.optimise`. It replaces the frame sections, bracing layout and
bracing sections; Cancel restores the previous design.

1. Size the 2D frame with no restraint.
2. Find practical bracing for it, then check bracing options in 3D: the practical
   layout, up to four eave-girder layouts, full roof bracing (in every bay, in the
   spacing-rule anchor bays with girders at both eaves, and in alternate bays),
   and, for a side wall whose end bays cannot take X bracing, portal frame
   braces on that wall (end bays; every anchor bay; end bays with a continuous
   eave girder). Each is tried with roof/end-wall brace size steps within their
   family and the brace policy. Each layout is sized twice, once from the practical layout's
   sections and once from the lightest priced rods, and the cheaper pass is
   kept. The practical layout is also re-sized from light rods. Keep the
   frontier: the cheapest bracing for each level of frame restraint.
3. Size the frame once, with the 2D auto-size, at the stiffest frontier
   restraint (less a 5% margin). Frame cost cannot rise with more restraint, so
   this is a floor: bracing options whose cost plus that floor cannot beat the
   best verified total are skipped.
4. For the rest, cheapest first: first check the floor frame with the option
   (recheck the bracing in 3D with it, adopt the restraint it now delivers and
   run the exact 2D check). If it verifies with the option's own bracing it is
   the least-cost frame for that option and no re-size is needed. Otherwise
   size the frame for that restraint and check that pair the same way. The
   verified total decides.

**Why the light-rod start and full roof matter.** The fast auto-size does not
come far down from heavy starting sections, and a shed braced on one side wall
needs the roof to act as a girder to resist plan twist. On a 30 x 40 m
three-sided shed with a 6 m canopy, full roof bracing sized from the practical
EA sections cost $22,745 (EA 75 roof braces). Sized from light rods it cost
$13,610 (16 mm wire roof). The optimiser total fell from $93,634 to $90,827.

The 3D option checks and girder-layout sizings in step 2 are independent and
run in worker processes (`bracing_parallel.py`, up to 8; set
`PORTALCALC_BRACING_WORKERS=1` to run them in-process). A script that calls the
optimiser must keep its code under `if __name__ == '__main__':`, because each
worker imports the calling script.

The unrestrained frame with the cheapest bracing is always a valid starting
answer. Bracing uses the workspace's own setup from the saved 3D settings
(per-location strut plans, rope ratings/prices, angles, brace policy) and is
saved back through it. The 2D auto-size is needed per restraint level because
restraint changes more than member sizes: on factory defaults the restrained
optimum also deepens the truss and re-sizes the webs. Factory defaults
(2026-11-10, with the saved workspace bracing setup): 2 auto-sizes, 26 3D checks,
61 s (340 s before the speed-ups); the continuous left-eave roof girder with the
floor frame wins at $38,514.03. Costs are the existing estimates; options are
bounded and this is not a global optimum.

With canopies, every bracing option also braces each canopy in the same bays as
the main roof bracing (`BRACING_LAYOUT.md`), and the canopy braces are sized
by the 3D auto-size. They are not stepped like the roof/end-wall braces, which
target frame restraint. On the 24 m test project with 5 m and 6 m canopies
(2026-11-11), the optimiser adopts the unrestrained frame with the continuous
left-eave roof girder and canopy bracing in the two end bays (10 mm wire rope),
$63,814.69 in total. The floor assumption in step 3 does not hold there: the
floor frame costs $55,115 against $54,807 unrestrained.

**When the floor frame is dearer than the unrestrained frame**, the 2D
auto-size (a bounded search) has broken the assumption, so the floor is not a
safe bound. The optimiser then sizes a probe frame for the least restraint (the
cheapest restraining option):

- If the probe frame is no cheaper than the unrestrained frame either, restraint
  is not reducing the frame at either end, and the search stops with the
  incumbent. The report says so.
- Otherwise the probe's cost becomes the bound, and each remaining option's own
  frame is sized and verified (no floor-frame checks or shortcut).

On the canopy test project, both the floor and probe frames ($55,115 and
$54,993) cost more than the unrestrained frame. The search stopped after 3
auto-sizes in 139 s with the same result. Sizing every option's frame instead
took 274 s for the same result.

`combined_sizing.py` is the earlier fixed-option sweep (re-sizes the frame for
every option) and holds the shared helpers. `bracing_restraint_regression.py` covers the sway spring, adoption,
staleness, floors, deferral and roof bands.

Wall and exposed-member drag, underside friction on a roof with a clad end wall,
crane, storage and the full production combination set remain excluded. Each physical combination is solved
with its own tension-only active set, rather than superposing member results.
Identical physical load cases are solved once with equivalent source names
recorded. Geometry, assembly and active stiffness factors are reused across cases.

## Portal reduction and recovery

The actual 2D frame/truss stiffness is used, including its section stiffnesses,
releases and base springs. A matrix H maps the free portal coordinates to roof
attachment X/Z translations. Non-nodal attachments use linear axial and cubic
Euler-Bernoulli bending interpolation, including rotations; attachment forces
transfer back with H-transpose to preserve virtual work. This is consistent with
the existing FE interpolation space, not a claim of mesh-independent exact point
compliance. An attachment grid too fine for that FE space is rejected.

A scaled QR basis separates prescribed attachment movements u from internal
coordinates v: q = P u + V v, where HP = I and HV = 0. Partition B-transpose K B,
with B = [P,V], then statically condense only the internal block:

```
Kr = Kuu - Kuv Kvv^-1 Kvu
fr = fu  - Kuv Kvv^-1 fv
v  = Kvv^-1 (fv - Kvu u)
```

The reduction therefore permits an unbraced portal sway mechanism: the assembled
bracing must stabilise it. An internal mechanism remaining with attachments held
is rejected. The building uses an unloaded stiffness macro with a separate full
load vector and retained member equivalent loads for every portal in every case.
The reduction produces the equivalent interface loads and the particular internal
deformation. Portal nodal displacements, support forces/couples and physical local
member end-force vectors are recovered after every case. Global equilibrium uses
the original physical loads/couples, including loads at native supports, rather
than treating condensed interface loads as the original loading.
For interior attachments, physical member end-force recovery subtracts the
consistent point-load vector from the deformation forces; exact nodal
attachments remain joint loads. Distributed-load equivalent local vectors are
also subtracted. Both types of load vector are retained in JSON. Reports record
per-portal applied resultants and whole-building force/moment balance.

## Rod solver and checks

The rod law is N = EA/L max(extension, 0); bilateral bars use N = EA/L extension.
The solver minimises the associated convex, piecewise quadratic elastic
potential using active tangents and an energy-decreasing line search. An
all-rods stiffness can provide a search direction when an intermediate active
set is singular. It never contributes compression rod forces or a penalty support
to the accepted result. Missing paths, unresolved mechanisms and nonconvergence
fail rather than receiving weak numerical springs.

Acceptance checks cover physical free-DOF equilibrium, compatible recovered
portal movements, and whole-building force/moment balance including direct
ground loads, brace reactions, portal supports and base spring couples. If
neutral zero-force rods are needed for the tangent, the result explicitly flags
stability as unverified. A positive taut-rod elastic tangent is not a geometric
stability/buckling assessment. Displacements have no automatic acceptance limit.
For gravity-only cases, a singular intermediate tangent can use a compatible
least-squares search step without artificial supports. The physical unilateral
force law and equilibrium residual remain mandatory. Any final neutral tangent
stays explicitly unverified and prevents a passing scoped design or automatic
adoption. Adding gravity cases can therefore expose previously untested slack
load paths in layouts that passed end wind alone.

**Slack play.** Rods that are all slack at equilibrium, such as both diagonals
of an X panel slightly shortened, can leave a zero-stiffness mode in the
converged active rod set although the structure is not a mechanism. The solver
used to abort the whole analysis with "Unstable or ill-conditioned model".
It now checks each such mode in both directions (`bounded_slack_play`). If
moving either way extends a slack rod, the case completes with
`tangent_status = slack_play_stability_unverified` and a `slack_play` record:
the play in mm before the first slack rod becomes taut (mode scaled to 1 mm
largest movement) and the rods that take it up. A direction that extends no
rod is still reported as a mechanism and raises. As with neutral rods, slack
play leaves stability unverified, so the design does not pass. Sizing treats a
brace downgrade that loses verified stability like the former error: it keeps
the best verified design, and mixed-strut sizing retries with the last stable
braces. Adding end-wall bracing to a three-sided 24 x 40 m shed gave 0.21 mm
of play on the open-side roof lines in side wind.

**Accepted slack play (project-adopted, 4 November 2026).** Stability is judged on
the taut rods. Where they leave zero-stiffness modes, every other rod can take up
a movement that extends it: zero-force (neutral) rods at once, slack rods after
their gap. Design checks accept the case when every movement along the modes,
including any combination of modes, extends some rod, and the play is at most
**Sections / criteria > Accepted rod slack play** (default 1.0 mm; 0 accepts
none). The basis is that turnbuckle pretension, which the model omits, takes it
up. Combinations are checked exactly (`no_escaping_direction`, Stiemke's lemma
via non-negative least squares); with several modes the reported play is taken
along the modes. A movement that extends no rod stays unverified (or raises as a
mechanism where even zero-force rods give no stiffness). Neutral zero-force rods
under gravity were the second adopted case: they previously always failed.
`design.slack_play` records the limit, accepted cases, largest accepted play and
any unverified cases; the summary and TXT show them. Older presets take the
1.0 mm default. On the shed above, open-side end-wall bracing then passes at
$7,944 against $9,964 with EA 75x75x6 wall angles.

The current dense solver is limited to 600 free roof DOFs. Portal reduction is
performed once and reused across frame lines and cases. No new dependencies
are required. Sparse assembly/caching can be added for larger buildings later.

## Remaining design work

The low-level solver returns **demands only**. The workspace's scoped member
checks and cost model are described in `BRACING_DESIGN.md`. Combined existing
purlin duties, connections, foundation flexibility, second-order/geometric
stability and complete installed costs remain separate. Existing Phase 1
results, the hand-calculated portal eave springs, costing and the full
computation PDF export are not overwritten. Frame restraint feedback uses one
secant sway spring per representative frame (above); it reproduces 3D interior
frame column moments within about 1–4% in the checked cases but is not the full
coupled interface, and end frames are not represented by it.

## Verification and references

`python bracing_analysis_regression.py` checks closed-form crossed rods and
spatial bars, load reversal/slackening, area scaling, missing paths, integrated
end-wall forces/overturning, a sway mechanism stabilised by a real rod, loaded
non-nodal portal reduction and an independently assembled unreduced building
with base springs. The cantilever point-load benchmark independently checks
member end actions. Desktop checks exercise actual sections and offsets, legacy
restraint removal, state preservation, gravity/normal-roof combinations, openings, stale geometry,
section persistence, dialog tables, failed-rerun invalidation and JSON export.
The suite is also included in `python smoke_checks.py --full`.

`python bracing_canopy_regression.py` (25 tests) covers the canopy Cpi rule
against 2D, the friction surfaces (main underside only with both ends open,
canopy top and underside), open-ended and fully open buildings, canopy
gravity/wind resultants per frame, side-wind parity with 2D, the guards, layout
geometry/schema v2/carry-over, the canopy bracing rule, the `canopy_brace` group,
the canopy network against an uncondensed full matrix, friction delivery and
balance, the unbraced-canopy message, the desktop solve, the layout editor and
**Find layout and size**. `smoke_checks.py` runs it as the step "open canopies,
open-ended roofs and roof friction".

For headless checks set `QT_QPA_PLATFORM=offscreen`, `OPENBLAS_NUM_THREADS=1` and
`OMP_NUM_THREADS=1` before starting Python. No release packaging is performed.

Primary background references:

- [SCIA: tension-only members require nonlinear analysis](https://help.scia.net/25.0/en/analysis/nonlinear_analysis/beam_local_nonlinearity/beam_local_nonlinearity.htm).
- [Ansys: substructures retain interface stiffness and load vectors](https://ansyshelp.ansys.com/public/Views/Secured/corp/v251/en/wb_sim/ds_condensed_parts.html).
- [SCI: portal frames and longitudinal stability systems](https://steelconstruction.info/topics/design/portal-frames/).
