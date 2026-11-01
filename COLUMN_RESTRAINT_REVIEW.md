# Column buckling and twist-restraint investigation

Subsequent production update: [COLUMN_FLYBRACING.md](COLUMN_FLYBRACING.md) documents automatic clad-column girt selection, calculated segment am and separate LTB/in-plane lengths. Statements below about production settings refer to the historical investigation stage.

Latest supplied-frame and actual-flybrace results: [55808_RESTRAINT_RESULTS.md](55808_RESTRAINT_RESULTS.md). That study separates the production grouped capacity failure, actual segment locations, selected-web frame buckling and illustrative base-spring sensitivity. Earlier generic geometry and equivalent-truss figures below are retained as historical sensitivity studies.

31 October 2026. Investigation only; production restraint rules have not been changed by this review. The earlier requested clad-column ke=1.00 and am=1.30 changes remain in place.

Comparison clarification: the original ke=0.85 fixed-top/pinned-base idealisation assumes translation restrained at both ends in the buckling plane. The diagnostic sway-frame factors use a different top-restraint condition and are not direct replacements for that 0.85. Adding base rotational stiffness with all other restraints unchanged increases elastic buckling resistance and reduces equivalent ke. The diagnostic itself shows this: pinned base 2.619, 20,000 kNm/rad spring 1.769, fixed-base comparison 1.274. The conversational suggestion to carry forward ke=2.0 is withdrawn as an adoption recommendation; this simplified model has not validated a production factor. Retaining 0.85 is consistent with the original idealisation if its top rotational and translational restraint assumptions are established for the relevant plane. Whether the actual truss supplies those restraints is a separate question.

Latest bracing clarification from the user: the roof bracing does not provide translation restraint in the portal plane; the moment frame itself provides in-plane lateral stability. Roof/end-wall bracing is designed separately for the applicable load path. A rigid column-to-truss joint enforces rotational compatibility between the connected members; it does not hold their common rotation or translation at zero. Therefore the earlier conditional reassurance about ke=0.85 cannot establish its applicability to in-plane compression merely from the roof bracing or rigid-joint assumption. Assess local connection rigidity, rotational restraint from the flexible truss/adjacent members and whole-frame sway stability as distinct questions. The simplified diagnostic is an illustration of the sway distinction, not a validated production factor. Out-of-plane roof/flybrace restraint and bending kt/kl/kr remain separate. No new production ke is adopted by this review.

## Detail under review

User-supplied information:

- Nominal 460UB75 column, 6.75 m high.
- Adopted checked column length is 5300 mm from base to the bottom truss node, explicitly confirmed by the user. Do not infer it by subtracting the truss depth from the overall height.
- Truss span 30 m; portal bays 8 m.
- Truss with 89 x 3.5 chords and 1300 mm depth.
- Eight-bolt moment connection to the truss.
- SHS50 x 1.6 flybrace, 1684 mm long, at the first purlin, intended to resist rotation.
- Lapped Z20019 purlin.
- 25 mm thick baseplate with four M30 rods at 300 x 120 mm centres (centres confirmed by user).
- Baseplate plan 445 x 200 mm, with the 445 mm dimension in the strong-axis plane. The 6 mm overhang of each column flange is intentional for the weld detail, confirmed by user; it is not corrected to a projecting plate. The 6 mm offset does not establish the weld leg size.
- Grout layer 25 mm thick, specified strength 50 MPa; concrete strength 25 MPa; anchor embedment approximately 900 mm.
- The 300 mm rod spacing is in the column strong-axis bending plane.
- Roof bracing connects to the top chord near the haunch, but is not necessarily present in every bay.
- Footing rotation in the ground is taken as zero by user instruction. The baseplate/anchor connection is semi-rigid, not fully fixed; its rotational spring stiffness is to be assessed. This supersedes the briefly proposed fixed-connection assumption.
- Purlin axial strength is taken as adequate by user instruction for this investigation. This does not establish purlin/connection stiffness or point-load bending capacity.

The section sensitivity uses the app's 460 UB 74.6 catalogue entry for the nominal 460UB75. The actual supplied section designation, grade and dimensions should be confirmed. The chord cross-section shape is not confirmed by the user's size alone.

Flybrace angle/geometry and end connections, purlin supplier/laps/bridging and connection stiffness, braced-bay positions, anchor head/hook and sleeved/unbonded details, plate grade and actual weld sizes are still required for a calibrated restraint model. Roof pitch, truss web details and project loads are also unsupplied. The screenshot identifies the intended arrangement but cannot establish connection stiffness or the hidden anchorage path. Soil rotational flexibility is excluded by the user's assumption; local concrete/anchor deformation remains relevant.

## Structural assessment

Three distinct checks are needed:

| Behaviour | Relevant restraint | Assessment of this detail |
| --- | --- | --- |
| In-plane column/frame buckling | Joint and truss stiffness, base moment-rotation response, frame sway restraint | An eight-bolt moment joint can transfer moment without fixing the column top against global frame sway. Roof plan bracing does not by itself establish transverse in-plane restraint. Determine this from the assembled portal, not a blanket braced-column ke. |
| Out-of-plane compression buckling | Longitudinal restraint to the column cross-section and rotational stiffness about its minor axis | The top restraint must connect through the flybrace/purlin/eaves system to a braced bay. The full base-to-top-restraint length remains unbraced where there are no wall girts/flybraces. A top flybrace is not a mid-height column restraint. |
| Lateral-torsional buckling | Critical-flange lateral restraint, twist restraint, load height and lateral rotational restraint | Establish the actual restraint stations and their classifications. A moment connection in the portal plane does not automatically provide the required out-of-plane flange rotation or torsional restraint. |

For a four-anchor base, nominally pinned in-plane bending and effective lateral/twist restraint are compatible. Do not equate a pinned bending support with a torsionally free base. Conversely, anchor count does not establish bending fixity, full twist restraint or minor-axis rotational stiffness. Baseplate flexure, column welds, anchor layout/stiffness, concrete anchorage, grout and footing response must be considered.

For the truss connection, the upper chord's connection to roof bracing can provide an anchorage point. It does not automatically restrain the lower chord or both column flanges against lateral displacement and twist. The connection region, column web and flybrace must transfer the required restraint action to that anchorage.

For bays without roof bracing, restraint is possible through a continuous longitudinal load path. Purlin axial strength is now accepted as an adopted assumption. Assess the stiffness and slip of the purlin/eaves strut, laps/splices, cleats, fasteners and braced-bay anchorage, including accumulated forces from other restrained frames and simultaneous roof loads. A nearby brace in a different bay is not sufficient evidence of restraint stiffness by itself.

The flybrace needs strength and stiffness in the required direction, including compression behaviour and wind reversal. Rotation prevented at one flange must not be inferred at the other flange without checking the connecting mechanism and cross-section distortion.

## Applicable design distinctions

AS 4100:2020 distinguishes compression effective length, le=ke*l, from bending buckling length, le=kt*kl*kr*l. The same numerical reduction may sometimes be justified for both, but it needs separate evidence.

In particular, a bending kr=0.85 can be available for suitable end classifications with effective lateral rotational restraint at one end. That is restraint against rotation out of the bending plane; it is not established merely by an eight-bolt in-plane moment joint. Clause 5.4.3.4 requires suitable relative flexural stiffness. The base may potentially provide that restraint, subject to its detail.

For full/partial twist restraint, assess AS 4100 Clauses 5.4.1 to 5.4.3 and Appendix H. The lateral/twist restraint strength provisions generally use 2.5% of the relevant maximum critical-flange force, with qualifications for restraint spacing and multiple restrained members. This is not simply 2.5% of the column axial compression. Include the flange force from bending and apply the actual brace geometry to determine brace axial force. Slip and flexibility in the transfer path also matter.

For an unclad column with a nominally pinned base, no intermediate transverse loads and an approximately linear moment diagram to the truss connection, am=1.75 is consistent with the triangular moment pattern. The current semi-rigid base introduces a base moment, so that triangular pattern can no longer be presumed. Determine am from the same case's moment diagram after applying the base spring. Strong-axis base restraint does not automatically establish the minor-axis rotational restraint needed for bending kr credit.

## Code findings

1. `main.py:member_check_restraint_lengths` assigns the same ke to major- and minor-axis compression lengths. Unclad main/lean-to columns retain ke=0.85, am=1.75 and kt=kl=kr=1.00 regardless of actual support/joint stiffness.
2. `main.py:member_actions_from_envelope` multiplies both stored restraint lengths by ke. `member_checks.py:reference_buckling_moment_knm` then uses the already shortened major-axis compression length as the lateral-torsional buckling length, before applying kt*kl*kr. Consequently, the unclad ke=0.85 also gives a 15% bending-length reduction without an independently recorded bending restraint justification. It also couples an in-plane compression length to a mode controlled by lateral/twist restraints.
3. The main frame has ux, uy and in-plane rz degrees of freedom only. Its optional second-order solver cannot verify weak-axis or torsional restraint. There is no production eigenvalue buckling calculation supplying case-specific column effective lengths.
4. The separate 3D bracing workspace is an axial network with portal interface stiffness. It does not model column cross-section twist/warping or independently verify this flange-to-flybrace detail, and does not feed verified column restraint classifications into the production checks.
5. The earlier clad-column change altered factors only. Half-height lengths in both directions remain assumptions. A wall flybrace does not establish an in-plane mid-height restraint without an in-plane reaction path.

## Numerical sensitivity for the supplied column size

This is a check of the current engine's sensitivity, not a verified design capacity. It uses catalogue 460 UB 74.6, flange fy=300 MPa, current section-property treatment and fixed am=1.75, kt=kl=kr=1.00. No actual load combinations or connection stiffness are inferred.

The physical checked spacing is now 5.300 m to the bottom truss node, supplied directly by the user. The earlier 5.45 m and 6.75 m illustrations are superseded for this segment. The bending calculation still requires the bottom node to provide the adopted lateral/twist restraint.

| Physical spacing | Current engine with 0.85 applied to bending length | Bending length equal to physical spacing | Reduction in calculated bending capacity |
| --- | --- | --- | --- |
| 5.300 m | 448.2 kNm | 404.7 kNm | 9.7% |

The 0.85 reduction may be recoverable as a separately justified bending kr; the calculation above shows why its justification matters. Neither column acceptance nor rejection follows from these values alone.

Current machine-readable values: `tmp/restraint_review/supplied_detail_results.json`, `column_5300mm_ltb_sensitivity`. The old `user_section_sensitivity.json` is retained as an earlier illustration only. am=1.75 is held constant solely to compare lengths; it is not verified for the semi-rigid-base moment pattern.

## Diagnostic verification

### Follow-up using the supplied member and base dimensions

The anchor pattern is 300 x 120 mm between rod centres, with the 300 mm spacing in the strong-axis bending plane. For identical axial rod springs, the sum of squared lever arms differs between directions by (300/120)^2=6.25. This is the rod group's bending-stiffness geometry ratio only. It is not the total base stiffness, torsional stiffness or a fixed-base classification.

With ground rotation suppressed, the connection still deforms through anchor extension, baseplate bending and concrete/grout bearing or anchorage response. Model those components as a rotational spring to the non-rotating footing. In a simple component assembly, connection rotational compliance is the sum of the relevant component compliances; eliminating soil rotation removes only the soil contribution. Determine stiffness and moment resistance together, including uplift/contact changes and nonlinear stiffness after yielding or slip. Research on partially restrained bases supports this component approach; it does not supply a transferable spring value for this detail.

#### Preliminary connection spring estimate from the supplied plate and anchors

The diagnostic now includes a transparent one-dimensional plate-strip estimate, not a fully qualified T-stub/baseplate design. With column flange centroids 442.5 mm apart and tension anchors at 150 mm from centre, the plate bending distance m is 71.25 mm and the tension-anchor-to-compression-flange lever arm z is 371.25 mm.

The elastic model uses two tension rods with Kb=2*Es*As/Lb; a cantilever plate strip with Kp=Es*b_eff*t^3/(4*m^3); approximate concrete compression Kc=1.3*Ec*sqrt(Acontact); and grout compression Kg=Eg*Acontact/tg. These translational springs act in series, with Ktheta=z^2/(1/Kb+1/Kp+1/Kc+1/Kg). This is an elastic, anchor-active branch, not a prediction of initial contact stiffness at zero moment for every axial load. A fully compressed base can behave differently before uplift. No soil rotational compliance is added. M30 coarse thread area is approximately 561 mm2. Es=200 GPa and Ec=Eg=25 GPa are explicit preliminary modulus assumptions; grout compressive strength alone does not specify its elastic modulus. Approximate compression contact area is 14,200 mm2, including the intentional plate truncation.

Research gives approximately 8d embedded elastic stretch length as one estimate for some ordinary bonded anchors. For these M30 rods that is 240 mm, not 900 mm. Because the HDB bonding/sleeving/head details are unconfirmed, the diagnostic also includes a softer case with all 900 mm of embedment participating in extension. Both add 65 mm above the concrete for grout, plate and an assumed half-nut/washer allowance. The resulting deformable lengths are 305 and 965 mm. These are sensitivity cases, not proven bounds on the real anchor response.

Effective plate-strip widths of 100 and 200 mm are also sensitivity assumptions; they are not established from a yield-line or two-way plate analysis.

| Assumed effective strip width | Assumed rod deformable length | Calculated initial Ktheta |
| --- | --- | --- |
| 100 mm | 965 mm | 14,900 kNm/rad |
| 100 mm | 305 mm | 21,800 kNm/rad |
| 200 mm | 965 mm | 19,800 kNm/rad |
| 200 mm | 305 mm | 34,400 kNm/rad |

An initial spring of 20,000 kNm/rad, with sensitivity from about 15,000 to 35,000, is therefore a useful provisional elastic study value. It is not a verified ULS input or a connection acceptance. The estimate omits anchor slip/gaps, prying, two-way plate/web action, weld flexibility and nonlinear concrete contact. It also depends on axial force and the active tension/compression pattern.

A separate strip calculation assuming unsupplied plate Fy=250 MPa reaches first elastic bending yield at approximately 14-27 kNm for those strip widths. These are not joint moment capacities: the actual plate grade, two-way action, weld layout, axial compression and anchorage can materially change them. They show why a constant initial spring should not be extrapolated indefinitely. A subsequent connection model should include a checked moment resistance and reduced tangent/secant stiffness as the connection deforms.

For SHS50 x 1.6, the manufacturer C350L0 tables give Ag=303 mm2 and I=0.117 x 10^6 mm4. Pure, concentric member compression capacities are approximately:

| Effective brace length | Published phi Nc, C350L0 |
| --- | --- |
| 1.0 m | 82.2 kN |
| 1.5 m | 64.2 kN |
| 1.684 m | 56.3 kN calculated using the tabulated properties and code curve |
| 2.0 m | 44.0 kN |
| 2.5 m | 30.0 kN |
| 3.0 m | 21.4 kN |

These values do not verify the user's steel grade, flattened/slotted tube ends, eccentric connections, bolts, thin-wall bearing/tear-out, welds or stiffness. The diagnostic independently reproduced the published values within 1%, using alpha_b=-0.5 for the stated section/grade. The 1684 mm brace's bare elastic axial stiffness EA/L is about 36.0 kN/mm; stiffness in the required transverse direction depends on its angle and the flexibility of the attached system. SHS50 x 1.6 is absent from the app's current catalogue; the diagnostic creates an explicit manufacturer-property section and does not substitute SHS50 x 2.

For scale only, with assumed column N*=100 kN and M*=300 kNm, the equivalent-flange screening expression 0.025*(M*/0.4425+N*/2) gives 18.2 kN transverse restraint force. A single brace whose axis is 45 degrees to the required force direction would carry about 25.7 kN. This illustrates why a short brace could have useful member strength but a long or poorly aligned one could govern. Neither the forces nor the angle have been supplied for this building, and no load sharing between paired flybraces is assumed.

The 2025 Metroll Z20019 example has area 712.5 mm2. If it is the installed product, the bare single-section axial stiffness EA/L over 8 m is about 17.8 kN/mm before connection slip and the rest of the load path. At a single applied 20 kN axial force, one such span elongates/shortens about 1.12 mm. Purlin axial strength is accepted by user assumption; longer transfer paths and accumulated restraint forces can still increase movement. The published roof UDL tables do not verify the remaining restraint point-load bending effects or connection stiffness. The actual purlin manufacturer has not been specified.

A separate idealised in-plane study now uses 460UB74.6 columns of 5300 mm checked length, 30 m span, equal connection springs at the bases and rigid joints to a flat equivalent-truss beam. The springs act relative to a non-rotating footing. Assuming 89 x 3.5 SHS chord centroids 1300 mm apart gives equivalent truss I=2*(I_chord+A_chord*650^2). Web shear deformation and truss-joint flexibility were omitted. Equal unit compression was applied to the columns, with no roof-member precompression. A separate first-order 1 kN total horizontal roof load gives a comparative sway response.

| Connection stiffness at each base, kNm/rad | Equivalent elastic sway k on 5.300 m | Roof sway per 1 kN total horizontal force, mm |
| --- | --- | --- |
| 0, pinned comparison | 2.619 | 0.731 |
| 1,000 | 2.515 | 0.677 |
| 5,000 | 2.222 | 0.535 |
| 15,000 | 1.865 | 0.382 |
| 30,000 | 1.645 | 0.298 |
| 100,000 | 1.404 | 0.210 |
| Infinite, fixed comparison only | 1.274 | 0.161 |

The round spring values are sensitivity inputs. The four additional component-derived estimates above are also run through the frame diagnostic; they are preliminary elastic estimates, not calibrated connection stiffnesses. The 15,000 kNm/rad case reduces first-order sway by about 48% relative to the pinned comparison. It also develops about 1.01 kNm base moment at each column for that 1 kN total horizontal load. A 20,000 kNm/rad spring gives approximately 53% less sway in this illustration. Actual design loads, gravity moments and second-order effects are not included in this unit-load example. Increased spring stiffness must not be credited while discarding the resulting base moment.

Results converged between 8 and 16 subdivisions to better than 0.0001% in k, passed eigen-equation residual checks and showed monotonically improving elastic buckling load as base stiffness increased.

This flat, elastic, unit-load study is not the actual pitched truss and its k values must not be adopted for the user's design. It quantifies the effect of partial base restraint and demonstrates why local moment restraint cannot justify a global braced-column ke=0.85. Actual roof/chord compression, joint/base flexibility, geometry and design actions require a case-specific portal assessment. The 8 m bay width affects those design loads; it is not needed for this unit-column-load stiffness illustration.

Reproducible follow-up: `tmp/restraint_review/supplied_detail.py`; results: `tmp/restraint_review/supplied_detail_results.json`.

### Earlier factory-frame numerical validation

A separate read-only elastic buckling diagnostic in `tmp/restraint_review/investigate.py` forms K*v=lambda*(-Kg)*v using the existing element matrices. It was checked against pinned-pinned, fixed-pinned and fixed-free Euler columns using 16 elements: computed theoretical factors agreed within 0.0005%.

An initial factory-frame illustration, completed before receiving the user's member dimensions, used the 21 m span/6 m eave factory truss with both side walls open. Its first elastic load factor for gravity was approximately 4.593; the change between four and eight subdivisions per member was 0.0041%. This is not the user's frame or a design pass. It does not include measured connection flexibility, imperfections, residual stresses, material yielding, weak-axis buckling or twist. Its output is retained in `tmp/restraint_review/results.json` for traceability, not for design adoption.

## Recommended implementation

1. Separate major-axis compression length, minor-axis compression length and lateral-torsional buckling segment length. Record each restraint's position, direction, flange, classification and anchorage path.
2. Derive in-plane stability from the portal system using a validated elastic buckling/second-order design method, with the actual base and truss connection stiffness. Do not substitute a blanket ke=1.0 for a sway assessment.
   The existing app already provides a base rotational Spring input in kNm/rad. Use a calibrated connection spring against the user-adopted non-rotating footing; changing the support alone does not update the current hard-coded member effective-length factors.
3. Establish the actual out-of-plane top and base restraints. Use no unverified rotational reduction; a translation-braced pin-ended assumption can be a starting point only after the translation restraints themselves are verified.
4. Assess bending kt, kl and kr independently. Retain possible kr=0.85 only when the actual lateral rotational restraint meets the code requirements.
5. Determine am from the same load case's moments over the actual restrained segment, and find governing utilisation across cases. Keep local section checks separate from member buckling checks.
6. Check flybrace strength/stiffness, compression/reversal, joints, accumulated forces and anchorage stiffness at the braced bays. Purlin axial capacity is adopted as adequate for this investigation.

## Sources

- [AS 4100:2020, Clauses 4.6.3, 5.4 and 5.6.3; Appendix H](https://sarah.com.au/wp-content/uploads/2024/09/4100_AS_2020_Steel_Structure.pdf).
- [ASI, Design of Portal Frame Buildings, connection extract, Sections 5.2 and 5.3](https://www.steel.org.au/resources/elibrary/resources/portal-frame-connections-bk180/?view=download). Includes four-anchor nominally pinned bases and the influence of bolting/connection rigidity.
- [SPACE GASS, Flange restraints](https://www.spacegass.com/manual/Steel_Member_Design/Flange_restraints.htm). Distinguishes flange restraint types, purlin stiffness, roof restraint and required restraint forces.
- [ASI, Design principles for steel sheds](https://www.steel.org.au/getattachment/31d81b3e-d1dd-4fee-8ba2-899e08611fee/1_Design_principles_for_steel_sheds_bk850_2014.pdf). Separate axis/mode and case-dependent effective-length assessment; cold-formed discussion is not used to assign hot-rolled column factors.
- [Australian Tube Mills, Design Capacity Tables, Tables 3.1-3(1) and 6.3-3(1)](https://www.steel.org.au/resources/elibrary/resources/design-capacity-tables-for-structural-steel-hollow/?view=download), printed pages 3-10 and 6-28. SHS50 x 1.6 properties and C350L0 compression values.
- [Metroll Purlin Design Guide, September 2025](https://www.metroll.com.au/wp-content/uploads/metroll_purlin_design_guide_2025.pdf), printed pages 7-9. Product properties, UDL design assumptions and lap detailing. Used as a manufacturer example only until the installed supplier is confirmed.
- [Kim, Leon and Galambos, Behaviour of Steel Joist Girder Structures with PR Column Bases, AISC Engineering Journal, 2007](https://ej.aisc.org/index.php/engj/article/download/913/912/912), component method and effects of partial base restraint. Used to support the modelling approach, not to assign this connection a published test stiffness.
- [Wald, Sokol and Jaspart, Base Plate in Bending and Anchor Bolts in Tension](https://people.fsv.cvut.cz/wald/Clanky%20v%20Adobe%20%28Pdf%29/05_CB2-Tension_rew5.pdf), elastic plate/anchor components and sensitivity to effective embedded stretch length. The approximate 8d embedded stretch assumption is not a universal rule for all holding-down rods.
