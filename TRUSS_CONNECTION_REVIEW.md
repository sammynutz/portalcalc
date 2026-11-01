# Truss-to-column connection restraint check

Production update: automatic clad-column girt flybracing and calculated segment am are now implemented; see [COLUMN_FLYBRACING.md](COLUMN_FLYBRACING.md). The earlier investigation narrative below is retained as background, including the separate unresolved connection/twist-verification scope.

Latest supplied-frame study: see [55808_RESTRAINT_RESULTS.md](55808_RESTRAINT_RESULTS.md). The actual preset has now been supplied and studied offline. The engineer subsequently confirmed a girt flybrace 1253 mm below the bottom chord. The earlier generic/mid-height studies below are superseded for that location; no new production factors are adopted.

31 October 2026. Offline verification of the adopted restraint assumptions; no additional calculation engine or new effective-length factor is being introduced into PortalCalc.

## What is being checked

1. In-plane connection rigidity: how much the column rotates relative to the truss as moment is transferred through the upper and lower chord connections, their bolts/plates/welds and the column connection region.
2. Rotational restraint supplied to the column by the connected truss: even a rigid joint can rotate as the truss deforms. Joint deformation and truss deformation must be distinguished.
3. Out-of-plane lateral and twist restraint: whether the truss joint plus flybrace restrains the necessary points of the column cross-section, with a sufficiently stiff path through the purlin and roof bracing.
4. Minor-axis rotational restraint: distinguish prevention of sideways movement/twist from restraint against bending rotation about the minor axis. The latter needs its own stiffness evidence if credited in compression ke or bending kr.

These checks do not assume that the roof bracing prevents portal-plane sway. The portal frame provides its own in-plane lateral stability, as clarified by the user.

## Information already supplied

- Column: nominal 460UB75, using catalogue 460 UB 74.6 properties for the current screens.
- Checked column segment: 5300 mm to the bottom truss node; overall column height was given as 6750 mm.
- Truss: 30 m span, 1300 mm depth, 89 x 3.5 chords; SHS shape is an explicit current assumption.
- Eight M24 8.8/TB bolts, with four around each chord group; 20 mm end plate bolted to the column flange; 90 mm bolt gauge, bolts 25 mm above/below chords.
- 12 mm stiffeners and 6 mm CFW throughout the stated main connection detail. Top and bottom chords are doubled locally at the haunch; doubling arrangement and extent must not be assumed over the whole span.
- SHS50 x 1.6 flybrace, 1684 mm long; lapped Z20019 purlin, 8 m portal bays.
- The shown detail has a pair of flybraces, each from a 5 mm cleat welded to the lower chord to a purlin on its respective side of the portal. Each end has one M12 bolt. The cleat is nominally 600 mm along the bottom chord; the detailed snip shows 630 mm from the haunch bolt region to the brace attachment. The local transfer screen uses 630 mm, without asserting those two reference measurements are identical.
- A single flybrace is sometimes used; it is assessed separately from the paired detail.
- Roof bracing attached to the top chord near the haunch, not necessarily in every bay.
- Purlin axial strength is taken as adequate by user assumption. Connection slip and restraint stiffness are not implied by that assumption.
- Base: 445 x 200 x 25 mm plate, four M30 rods at 300 x 120 mm centres, 300 mm in the strong-axis bending plane. The 6 mm column-flange overhangs are intentional for the weld detail.
- Grout: 25 mm, 50 MPa specified strength. Concrete: 25 MPa. Rod embedment: approximately 900 mm.
- Footing rotation in the soil is suppressed by assumption. The baseplate/anchor connection remains semi-rigid.

## Completed member-stiffness screen

Using E=200 GPa and the current catalogue properties:

- Column EI/H is about 12,640 kNm/rad. The isolated member reference cases 3EI/H and 4EI/H are approximately 37,900 and 50,600 kNm/rad. Those cases suppress end translations and are comparison scales, not the actual frame boundary conditions.
- An equivalent flat-truss bending screen, using the axial couple in the two SHS chords, gives reference end stiffnesses of approximately 17,000-26,000 kNm/rad. The range allows 1300 mm to mean overall depth or chord-centroid depth and uses 3EI/L and 4EI/L reference cases.
- This screen omits truss web shear deformation, actual pitch/web layout and connection flexibility. It does not classify the actual truss joint or determine ke. It shows that the truss itself cannot simply be assumed infinitely stiff relative to the column.

The connection will be assessed by its moment-rotation response and compared with the connected-member response. The force couple in the upper/lower chord connections and the flexibility of the plates, bolts, column flange/web and chord ends all form part of that response. Eight bolts alone do not specify it. Strength and the persistence of stiffness at the relevant moment level must also be checked.

### Supplied end-plate joint: elastic component estimate

The updated component screen uses four elastically engaged tension bolts around one chord group and a compression force at the other group. M24 8.8/TB and the end-plate arrangement are now confirmed. It includes bolt extension, end-plate bending, a column-flange bending comparison and a clear-web shear-panel reference. The shear-panel component is a deformation reference, not a direction to double-count an already modelled member region.

With stiffeners assumed aligned and welded to suppress local column-flange flexibility, the estimate gives rotational stiffness of approximately **307,000-370,000 kNm/rad**. Allowing the unstiffened column-flange comparison expands the lower end to approximately 193,000 kNm/rad. Chord force lever arms 1122-1300 mm, bolt deformation lengths 50-80 mm and nominal effective plate strips are explicit sensitivity assumptions; they are not all measured dimensions. Plate effective widths, prying, weld flexibility and SHS local wall/end distortion remain outside this simple component model.

Under an illustrative 100 kNm moment, the aligned-stiffener cases rotate approximately 0.00027-0.00033 rad relative to the connected member, including the panel reference. That is small compared with the equivalent-truss reference rotations (approximately 0.004-0.006 rad for the unreinforced member examples). These estimates support **nominally rigid joint modelling in the portal plane** for the intended fully tensioned, stiffened detail. They do not establish that the column end has zero absolute rotation, or constitute a complete connection strength/rigidity certification.

The doubled haunch chords increase local stiffness. A piecewise equivalent-beam sensitivity with 600-3000 mm doubling at each end raises the 1300 mm-centroid-depth end reference stiffness from roughly 19,500-26,000 to about 20,100-33,000 kNm/rad. Those doubling lengths are examples, not supplied dimensions; doubling a short end region does not double the stiffness of the whole 30 m truss.

## Completed flybrace member and kinematic screens

For C350L0 SHS50 x 1.6 with manufacturer Ag=303 mm2 and I=0.117 x 10^6 mm4, the concentric pin-ended compression capacity at 1684 mm is approximately 56.3 kN. Bare elastic axial stiffness EA/L is approximately 36.0 kN/mm. These are member-only results, not connection or complete restraint acceptance.

Actual transverse stiffness depends on brace direction, end connections and the receiving roof system. A small two-degree-of-freedom test also confirms that two braces acting at the same column flange point do not by themselves restrain independent column translation and twist: the cross-section can rotate about that restrained point. An additional restraint through the truss connection, opposite flange or another suitable mechanism can remove that freedom. This is a kinematic observation, not a finding that the pictured detail is deficient.

The actual attachment geometry must be mapped before deciding which restraint classification applies at the bottom truss node, including load reversal. The purlin/roof-bracing path is then included with the user's adopted axial-strength assumption and explicit stiffness/slip assumptions.

### Supplied snip: short-chord transfer and paired restraints

The latest snip establishes that the flybraces restrain the lower chord away from the column. The restraint path is column/end plate -> short truss end region -> lower-chord cleat -> flybrace -> purlin/roof-bracing system. It is not a direct flybrace on a column flange. The upper-chord roof restraint and the end plate's ability to transmit out-of-plane moments are necessary parts of the complete restraint.

The 630 mm region is included as lateral bending of the lower chord. For vertically stacked, effectively connected double SHS89 x 3.5, its 3EI/L^3 reference stiffness is about 6.62 kN/mm. A single-ply comparison is also calculated. This assumes that both chord plies share the lateral action through the region.

For direction-cosine examples 0.5-0.866 along the building, and bare Z20019 axial collector paths of 8-24 m to an ideal anchored point, the doubled-chord paired case gives approximately **3.4-5.1 kN/mm** of series lateral stiffness with joint rotation held. The separate rotation reference, with joint translation held, is approximately **1360-2010 kNm/rad** about the column's vertical axis. The column GJ/H comparison scale is about 8 kNm/rad. This indicates useful torsional restraint potential, but is not a complete elastic lateral-torsional buckling solution or a formal full-restraint classification. The two held-degree-of-freedom cases must not be claimed simultaneously from the lower path alone.

The single-brace examples give about 2.3-4.1 kN/mm and 920-1630 kNm/rad under the same assumptions. A single brace can potentially be effective in tension and compression, but cannot inherit the paired detail's result or simply be treated as half of the complete restraint stiffness.

The full assembly has a credible lateral/twist restraint mechanism when the upper-chord roof anchorage, end-plate coupling and lower-chord transfer act together. The paired layout is therefore a reasonable **effective-restraint design assumption to complete through connection strength and slip checks**, not an established failure or a free-twisting column. Actual brace direction cosines, collector stiffness and the M12/cleat/SHS end details are needed to turn that assumption into a numerical full/partial classification.

M12 bolt grade, single/double shear, hole/edge distances and the SHS end treatment remain unconfirmed. The 5 mm cleat and purlin web must also transfer the brace force without unacceptable deformation. The 56.3 kN SHS member compression capacity is not a capacity for either end connection. Strength upgrades may be made as authorised by the user, but require the actual restraint forces before selecting a final detail.

## Details requested to complete the connection calculation

The main joint size, tightening category and topology are supplied. For a full component calculation, confirm plate plan dimensions, exact doubled-chord arrangement/lever arms and extent, and stiffener/weld positions. The elastic screen above states the approximations used in their absence.

Flybrace topology, count, local station and nominal end-fastener sizes are supplied. Exact 3D attachment coordinates, M12 grade/hole/edge details, SHS end treatment and purlin-side connection are needed for final strength and slip checks. The brace is attached to the lower chord, not directly to either column flange.

Design moment, axial force and shear are needed for final demand/capacity and load-level stiffness acceptance. If those are unavailable, the connection can first be screened under unit actions and its moment-rotation response reported without claiming a design pass.

## Current status

Member-level, elastic component and restraint-path screens are complete for the supplied information. The main joint has a credible basis for nominally rigid in-plane modelling, subject to the stated detailing and strength requirements. The pictured paired flybrace/end-region/roof system has a credible effective lateral/twist restraint mechanism; a final full/partial classification still needs its actual connection-force and slip assessment. This separates a reasonable modelling idealisation from a completed design pass. No new production ke, kt or kr has been adopted from these screens. The earlier base investigation is retained separately in `COLUMN_RESTRAINT_REVIEW.md`.

Reproducible screen: `tmp/restraint_review/truss_connection_screen.py`; values: `tmp/restraint_review/truss_connection_screen_results.json`.

## Establishing an adopted in-plane ke outside PortalCalc

### Offline verification preparation (31 October 2026)

The user authorised running the actual-frame verification. No saved workspace preset matches the supplied 30 m frame, 6750 mm eaves, 460UB74.6 columns and 1300 mm / 89 x 3.5 truss. The actual preset has been requested; **the supplied frame and its in-plane ke have not been verified**.

An offline driver is prepared at `tmp/restraint_review/verify_preset.py`, with usage and modelling limits in `tmp/restraint_review/README.md`. A numerical reference run on the factory 21 m frame completed: the three Euler boundary-condition benchmarks agree within 0.001%; all 26 distinct ULS axial-force patterns had first-mode mesh changes below 0.7% (maximum 0.6958%). Maximum eigenproblem residual was 2.6 x 10^-9. The first three plotted modes for the reference frame's lowest load factor are local roof-member deformations, illustrating why the first eigenvalue must not automatically be converted into a column sway factor. These are validation results for the tool/reference model, not results for the supplied frame.

The actual model still needs its geometry, loads, releases, checked column endpoint and local double-chord analysis stiffness audited. The driver preserves the production model's connectivity and stiffness and flags design-only chord doubling rather than silently adding an assumed doubler length. No production unclad ke or support settings were changed in this step.

Passing the top-column serviceability limit H/150 does not determine a compression effective-length factor. For a 6750 mm serviceability height, that limit is 45 mm. The 5300 mm buckling segment length is a separate geometric quantity. A serviceability drift limit constrains lateral response to a specified service load; it neither holds the column top stationary nor establishes the ultimate elastic buckling load under the governing axial-force pattern.

Use an offline elastic in-plane buckling benchmark of the actual pitched truss/column assembly and relevant ULS cases. Include the real chord/web layout, local doublers, connection idealisation, roof-member compression and consistent base/support assumptions. Identify the portal/column buckling mode separately from local roof-member modes. For approximately uniform column compression under proportional loading, Ncr,i=lambda_cr*Ni and ke,i=(pi/Li)*sqrt(EIi/Ncr,i). Use forces from the same case, not independent envelopes; nonuniform forces, varying stiffness and rigid end regions require appropriate treatment. The member factors and second-order/moment-amplification treatment must form one consistent design method.

A representative-family study can then establish a documented adopted factor or lookup range without adding a connection calculator to PortalCalc. A single frame and its H/150 pass do not verify a universal factor across arbitrary spans, truss depths, sections and supports. The existing flat equivalent-truss diagnostic is a sensitivity illustration, not that benchmark.

For context only, the catalogue 460UB74.6 has Imajor/Iminor=335/16.6=20.18. Holding the weak-axis effective length at 0.85*5300 mm, the current member engine's pure compression capacity remains 1106.3 kN when major-axis ke is varied from 0.85 to 2.5, because the weak axis governs. Equal axial slenderness occurs at major ke approximately 3.82. This is not validation of the weak-axis 0.85 assumption, nor a combined-action or frame-stability pass; it explains why the two directional ke values must not be compared without their section inertias.

## References

- [ASI portal frame connection guidance](https://www.steel.org.au/resources/elibrary/resources/portal-frame-connections-bk180/?view=download): connection deformation, bolting and column flange/web effects. Its rafter details are not used as direct proof of this truss connection.
- [SPACE GASS flange restraint definitions and examples](https://www.spacegass.com/manual/Steel_Member_Design/Flange_restraints.htm): separates lateral, twist and lateral rotational restraint and explains the role of purlin/roof stiffness.
- [Australian Tube Mills design capacity tables](https://www.steel.org.au/resources/elibrary/resources/design-capacity-tables-for-structural-steel-hollow/?view=download): SHS50 x 1.6 properties and compression-curve verification.
- [ASI methods of structural analysis](https://www.steel.org.au/getattachment/f817761d-fd79-409f-a39c-3d62a734cf6f/Methods-of-structural-analysis-for-hollow-sections_bk140.pdf): elastic buckling loads and effective lengths.
