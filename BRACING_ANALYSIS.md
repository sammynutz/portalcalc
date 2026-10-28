# Fixed-layout 3D bracing analysis

This is a small-displacement, wind-only demand model for a fixed layout. The
combined workspace adds scoped member checks and section sizing described in
`BRACING_DESIGN.md`. A solved model is **not a passing structural design**.
Automatic layout selection and restraint feedback into the production 2D portal
remain separate work.

## Desktop workflow

1. Establish the portal sections in the normal 2D workflow.
2. In **Bracing > Open 3D bracing workspace**, block unavailable panels and
   pin the X-braced panels to analyse. Unmarked panels are not installed braces.
3. Select the five section groups and check assumptions in Sections / criteria.
4. Use **Analyse and check** or **Auto size for lowest checked cost**. These
   choices are independent of Phase 1 and legacy portal-restraint controls.
5. Read the assumptions, ULS member envelopes, SLS movement and per-case
   reactions/equilibrium checks. Select forces, governing utilisation or displaced
   bracing; click a member for its source case and demand/capacity values.
   Export TXT for readable workings and JSON for complete evidence,
   including recovered portal nodal movements and local member end forces.

Analysis section choices save under optional `bracing.analysis_3d` with schema
`portalcalc.bracing-analysis-settings.v1`. Legacy presets omit it. Results are
transient: changing a section, check assumption or layout clears the previous result and
disables exports until another successful run. The evidence freezes the source
preset and its SHA256, actual analysis geometry, sections, load workings and
case results. Cancel/window close discards unsaved layout/section changes; Save
layout and sections records both. Configurator Review locks inputs and automatic
sizing while allowing analysis and visual inspection.

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
- There is no sheet/diaphragm stiffness and no weak-axis portal column bending.

Eight wind cases cover front-to-back/back-to-front, Cpi +/- and ULS/SLS. Each
uses simultaneous windward/leeward pressures from the existing wind module,
with actual source direction, enclosure flags and pressure factors. Reversing
wind swaps physical end identities for the Cpi model. `End +`/`End -` in the
existing roof-pressure API are coefficient branches, not opposite wind directions.
An open end receives no cladding pressure.

End cladding/mullions use the stated simply supported vertical-strip assumption.
Half of each strip's resultant is delivered to roof and half directly to ground.
Two-point Gaussian integration preserves the resultant and overturning for the
piecewise linear roof height. Roof forces interpolate onto roof stations; ground
forces interpolate onto real end-post stations. This is a declared load model,
not a separately verified cladding/girt/mullion connection design.

Excluded actions include G/Q, transverse wind, roof/wall friction or drag, crane,
storage and full production load combinations. They cannot be inferred from a
successful end-wind solve. ULS/SLS pressure scales are applied within each wind
case; rod active sets are solved afresh rather than superposing nonlinear results.

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
