# 55808 — restraint and column-check investigation

Subsequent production implementation: see [COLUMN_FLYBRACING.md](COLUMN_FLYBRACING.md). PortalCalc now calculates am by actual segment/case and selects flybraces at its generated girt levels. For this preset it selects 3525 mm above base (first-order left-column utilisation 0.9571). The external engineer's actual 1253 mm offset and offline second-order comparisons below are retained separately.

The revised left-column failure is reproduced, but the grouped check does not represent the engineer's stated flybrace position. The supplied preset gives a 5539 mm bottom-chord node, and the engineer's flybrace is **1253 mm below it**, at 4286 mm in this model. The earlier 5300 mm bottom-node figure is not represented by this preset. No production factors, support settings or input preset were changed during this investigation.

These are numerical/model diagnostics, not a completed structural design acceptance. Physical restraint stiffness/strength, the exact double-chord region and the complete design method still need to agree with the adopted model.

## Reproducing the reported 410UB54 failure

The preset has a clad left wall and an open right wall. The left column is catalogue 410UB53.7, the right 460UB74.6. The current app applies a 3375 mm half-height restraint length to the left group's peak moment, irrespective of the engineer's actual flybrace location. The governing gravity combination is 1.2G + 1.5Q.

| Adopted ke | Adopted am | Left combined utilisation |
|---:|---:|---:|
| 0.85 | 1.75 | 0.971 |
| 1.00 | 1.75 | 0.985 |
| 0.85 | 1.30 | 0.996 |
| 1.00 | 1.30 | **1.113** |

With both revised factors, the app gives peak M = 274.48 kNm, phi Mb = 267.34 kNm and compression utilisation 0.086. The grouped bending utilisation is 1.027. This is a reproducible capacity change; it is not evidence by itself that the engineer's actual detail fails.

The upper flybrace-to-bottom-chord segment is 1253 mm, rather than the 3375 mm used by this grouped check. The base-to-flybrace segment becomes longer, so its actual moments must also be checked; moving the brace upward is not uniformly beneficial to every segment.

## Checks using the stated flybrace position

The following retain effective-restraint assumptions and the app's simplified capacity/interaction formulas. Restraint effectiveness in the relevant directions is assumed, not established by the new location information. The baseline app analysis uses fixed 50x2.5 SHS placeholder webs; a separate offline model uses the preset's selected 75x2.5 SHS webs, with signed same-case loads and forces.

For that selected-web model, the app's second-order solver gives the following diagnostics with pinned bases. Every captured ULS combination was checked at the stated segment stations. The governing combination in these rows is 1.2G + 1.5Q.

| Segment | Length | Peak moment | am used | Combined utilisation |
|---|---:|---:|---:|---:|
| Base to flybrace | 4286 mm | 218.43 kNm | 1.813, from its own quarter-point moments | **0.837** |
| Flybrace to bottom chord | 1253 mm | 282.07 kNm | 1.30 | **0.976** |
| Bottom chord to top chord | 1211 mm | 280.97 kNm | 1.30 | **0.932** |

The assumed compression effective-length factors in these segment capacity diagnostics are 1.0, with kt=kl=kr=1. These do not assign a mid-column brace as an actual in-plane support to the frame model. Global in-plane frame stability is studied separately below. A final check needs distinct physical compression and LTB restraint definitions rather than automatically sharing a single segment length/factor.

Applying am=1.30 indiscriminately to the longer base-to-flybrace segment gives utilisation **1.120** in the same second-order diagnostic. Its moment gradient supports approximately 1.813 by the quarter-point expression; using 1.75 as a lower-segment sensitivity gives utilisation 0.863. The lower segment must therefore retain an appropriate moment-gradient treatment, rather than inherit the upper segment's assumed am.

For the upper 1253 mm segment, the same-case quarter-point expression gives am approximately **1.105**, rather than 1.30. With this short segment, both values reach the app's section bending-capacity ceiling of 305.28 kNm, so that distinction does not change this example's governing upper-segment result. This does **not** establish 1.30 as a conservative universal factor for every upper segment.

The original app-model first-order segment diagnostic gives upper utilisation 0.951; the selected-web first-order result is approximately 0.954. The second-order selected-web result above is the more informative comparison, while remaining subject to the app solver/model limitations and missing physical details. It is not an Appendix E compliance certification or an independent reproduction of the engineer's design method.

## In-plane buckling and base stiffness

The offline eigenproblem includes actual frame connectivity, selected 75x2.5 SHS web stiffness and each ULS case's signed column and roof-member prestress. Joint connectivity remains nominally rigid. Local double chords are not silently added to analysis stiffness: their exact extent/arrangement was not supplied. The results below are consequently conditional on the stated undoubled analysis model.

All 14 distinct ULS axial-force patterns were examined on two meshes. The three isolated Euler benchmarks agree within 0.001%; first-mode mesh changes across the load patterns are below 0.7%. Gravity and the important left-wind case were additionally checked at six and eight subdivisions per element, with visual review of the retained column-participation modes.

For 1.2G + 1.5Q:

| Base connection model | Reviewed sway-like branch multiplier, fine mesh |
|---|---:|
| Pinned | **21.699** |
| Illustrative 20000 kNm/rad spring at each base | **47.995** |

The spring branch is coupled with nearby local truss modes; another participating mode occurs at about 47.225. These figures identify reviewed sway-like branches, **not** the first instability of the complete model. The lowest positive multiplier over all ULS patterns is about 3.598 for the pinned selected-web model and 3.733 for the spring model, with local truss-member deformation. Those local modes must not be relabelled as column sway modes or ignored for complete truss verification.

The pinned gravity sway branch corresponds to diagnostic Euler-equivalent factors approximately **2.43 left / 3.23 right**, normalised by the 5539 mm segment and that case's column compression. These are whole-frame sway indicators, not replacement values for every use of ke in PortalCalc. The illustrative springs improve the sway result, as expected. Their stiffness is a sensitivity assumption, not verified sustained base stiffness at ULS.

An important code distinction is that AS 4100:2020 Clause 8.4.2.2 uses ke=1.0 for the in-plane interaction capacity of braced and sway members, with a lower calculated factor permitted for braced members, while requiring the separate compression provisions to be satisfied. Frame second-order effects, axial member capacity and LTB effective lengths must be treated consistently; the larger frame-derived numbers must not simply be inserted into the current shared compression/LTB factor field. This study therefore **does not verify 0.85 as an in-plane sway effective-length assumption**, nor adopt a new production factor.

Maximum absolute first-order SLS column-top displacement among the captured selected-web cases is approximately 27.22 mm for pinned bases and 12.68 mm for the illustrative springs. The user's H/150 limit at 6750 mm is 45 mm. This serviceability comparison does not determine ke.

## Evidence and reproducibility

- Source preset: `C:/Users/SamLawless/OneDrive - Central Steel Build/Desktop/55808.json`; source bytes are checked unchanged by the offline driver.
- Production-model factor diagnosis: `tmp/restraint_review/55808_capacity_diagnosis.json`.
- Actual-location second-order segment diagnosis: `tmp/restraint_review/55808_actual_flybrace_second_order_lower_am_calculated.json`.
- Second-order frozen evidence and reconstruction checks: `tmp/restraint_review/55808_second_order_evidence.json` and `55808_second_order.log`. The recreated first-order nodal displacements agree with the captured model to better than 1e-6 mm/rad; second-order iterations converge.
- Selected-web full-case buckling results: `tmp/restraint_review/55808_physical_pinned/buckling_results.json` and `55808_physical_spring20k/buckling_results.json`.
- Fine-mesh branch checks: corresponding `55808_physical_pinned_fine` and `55808_physical_spring20k_fine` directories.
- Scripts: `verify_preset.py`, `second_order_55808.py`, and `diagnose_55808.py` in `tmp/restraint_review`. Use `--flybrace-below-chord 1253 --lower-am-from-moments` for the final segment diagnosis.

The eight-bolt joint and roof flybrace component reviews remain in `TRUSS_CONNECTION_REVIEW.md`. This 2D frame study does not complete their out-of-plane/twist strength and slip checks. The newly specified girt flybrace is also a separate restraint whose effectiveness must be confirmed.

References: [AS 4100:2020, Clauses 5.6.1.1 and 8.4.2.2](https://sarah.com.au/wp-content/uploads/2024/09/4100_AS_2020_Steel_Structure.pdf); [ASI elastic in-plane buckling guidance](https://www.steel.org.au/getattachment/92e38f35-dfff-471f-bda5-9450cef0ab93/AS-4100-DS04.pdf).
