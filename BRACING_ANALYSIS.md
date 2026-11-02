# Fixed-layout 3D bracing analysis

This is a small-displacement, wind-only demand model for a fixed layout. The
combined workspace adds scoped member checks and section sizing described in
`BRACING_DESIGN.md`. A solved model is **not a passing structural design**.
The workspace can now search layouts built from unblocked panels, using this
fixed-layout solver for every candidate (see `BRACING_LAYOUT.md`). Restraint
feedback into the production 2D portal remains separate work.

## Desktop workflow

1. Establish the portal sections in the normal 2D workflow.
2. In **Bracing > Open 3D bracing workspace**, block unavailable panels. Unmarked
   panels are available search locations, not installed braces. Optionally select
   panels for fixed-layout checks or as required locations during search.
3. Select the five section groups and check assumptions in Sections / criteria.
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
default **H/100 for IL1**, H/150 for IL2+, with explicit main limits retained.
A separate bracing global H/n override is available. Local member/purlin
deflection keeps its independent L/n limit (default L/150). The adopted global
criterion and source are frozen in JSON and shown in Result summary/TXT.

The production portal is rebuilt once before opening the dialog. The 3D adapter
then works on a private unloaded copy: it removes legacy eave springs/restraints,
retains actual base supports/springs and internal columns, and does not change
the live frame. Canopies/lean-tos are currently rejected. The nominal location
grid is mapped proportionally onto actual main portal centrelines; roof/brace
lengths use those actual coordinates. Both nominal and actual grids are exported.
Identical portal sections repeat at each longitudinal station, including ends.

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
independent of the mullion/brace strip mesh. Each panel has two crossed
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
ULS independence, recovery, sizing and saved-workspace behavior.

TXT/JSON include the Revision B calibration records: roof test c = 4.5 mm/kN on
a 6 x 6 m specimen, geometry adjustment to the reference roof, judgement factors
1.50 and 1.20, raw c about 2.820 and rounded reference 3.0; wall LTP20/LTP45 test
maxima 2.753/4.854, depths 17.4/43 mm, interpolation to 29 mm (3.705), reference
geometry, thickness/pitch exponents and assumed 175.1 mm reference pitch, judgement
factors 1.20/1.20, raw c about 5.941 and rounded reference 6.0. Every panel retains
its actual a/b, geometry factor, adopted c and equivalent diagonal properties.
Measured source inputs are distinguished from empirical interpolation/allowances.
The note's 0.42 BMT five-rib fixing/support assumptions remain explicit.

Eight wind cases cover front-to-back/back-to-front, Cpi +/- and ULS/SLS. Each
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
not a roof pressure that this model does not apply. Case evidence freezes d/b,
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
free-roof aerodynamic check. Underside, wall friction and exposed-member drag
are excluded. The summary and full TXT/JSON expose the coefficient, load strip,
projected area, force, source and applicability assumption. Adding this load does
not add cladding/diaphragm stiffness. Old cached calculations are invalidated.

End-wall pressure acts normal to the front/back walls along Y. Side-wall braces
have a Y component and carry longitudinal forces to ground; front/back end-wall
braces are in X/Z and have no direct Y component. Their coupled spatial actions
are calculated, but transverse-wind end-wall bracing sizing is not included.
Per-case load-path evidence resolves actual axial forces at their ground ends,
reports reactions by wall face and balances the roof-applied Y load separately
from pressure delivered directly to end-post bases.

End cladding/mullions use the stated simply supported vertical-strip assumption.
Half of each strip's resultant is delivered to roof and half directly to ground.
Two-point Gaussian integration preserves the resultant and overturning for the
piecewise linear roof height. Roof forces interpolate onto roof stations; ground
forces interpolate onto real end-post stations. This is a declared load model,
not a separately verified cladding/girt/mullion connection design.

Excluded actions include G/Q, transverse wind, wall/underside/exposed-member drag, crane,
storage and full production load combinations. They cannot be inferred from a
successful end-wind solve. ULS/SLS pressure scales are applied within each wind
case; rod active sets are solved afresh rather than superposing nonlinear results.
Normal roof and side-wall pressures are also outside this reduced 3D solve;
the separate purlin roof-bending envelope is not a simultaneous whole-building
wind-pressure pattern.

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
is rejected. The generic reduction retains load condensation/recovery, but this
building stage explicitly requires an unloaded macro and applies end-wall wind
to the 3D network. Portal nodal displacements, support forces/couples and local
member end-force vectors are recovered after every case.
For interior attachments, physical member end-force recovery subtracts the
consistent point-load vector from the deformation forces; exact nodal
attachments remain joint loads. Those point-load vectors are retained in JSON.

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

The current dense solver is limited to 600 free roof DOFs. Portal reduction is
performed once and reused across frame lines and cases. No new dependencies
are required. Sparse assembly/caching can be added for larger buildings later.

## Remaining design work

The low-level solver returns **demands only**. The workspace's scoped member
checks and cost model are described in `BRACING_DESIGN.md`. Combined existing
purlin duties, connections, foundation flexibility, second-order/geometric
stability and complete installed costs remain separate. Existing Phase 1
results, portal eave springs, costing and the full
computation PDF export are not overwritten. The next coupling stage must retain
the coupled interface stiffness/load vector and recover portal actions; a set of
independent grounded springs or copied 3D moments is not substituted here.

## Verification and references

`python bracing_analysis_regression.py` checks closed-form crossed rods and
spatial bars, load reversal/slackening, area scaling, missing paths, integrated
end-wall forces/overturning, a sway mechanism stabilised by a real rod, loaded
non-nodal portal reduction and an independently assembled unreduced building
with base springs. The cantilever point-load benchmark independently checks
member end actions. Desktop checks exercise actual sections and offsets, legacy
restraint removal, state preservation, eight wind cases, openings, stale geometry,
section persistence, dialog tables, failed-rerun invalidation and JSON export.
The suite is also included in `python smoke_checks.py --full`.

For headless checks set `QT_QPA_PLATFORM=offscreen`, `OPENBLAS_NUM_THREADS=1` and
`OMP_NUM_THREADS=1` before starting Python. No release packaging is performed.

Primary background references:

- [SCIA: tension-only members require nonlinear analysis](https://help.scia.net/25.0/en/analysis/nonlinear_analysis/beam_local_nonlinearity/beam_local_nonlinearity.htm).
- [Ansys: substructures retain interface stiffness and load vectors](https://ansyshelp.ansys.com/public/Views/Secured/corp/v251/en/wb_sim/ds_condensed_parts.html).
- [SCI: portal frames and longitudinal stability systems](https://steelconstruction.info/topics/design/portal-frames/).
