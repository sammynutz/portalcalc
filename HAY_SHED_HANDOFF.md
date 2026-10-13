# Hay shed project handoff — 13 October 2026

## Latest continuation: bale storage and 10° pitch study

The original 7.5° search remains complete and was not rerun. New authorised
10° trials are complete in `hay_shed_10deg_bale_study_v2`. Read its
`BALE_COMPARISON.md` and `BALE_COMPARISON.json` for the latest storage ranking.
Its `REPORT.md` uses the superseded initial 300 mm depth allowance.

User's existing offerings: 18 × 32 m (4 × 8 m bays) and 21 × 40 m
(5 × 8 m bays). Nominal bales 8 × 4 × 3 ft, with 8 ft along bay and 4 ft
into shed. User explicitly wants the tolerance implied by 17 rows in 21 m:
273.6 mm total depth allowance, replacing their earlier 300 mm answer.
Six layers retained; nominal stack height 5.4864 m. Illustrative bay allowance
500 mm total, before columns/aisles/variation. Counts are nominal packing,
not a physical-clearance or handling proof. User earlier described three
bales as about 7.45 m; reports use exact nominal 8 ft dimensions, so actual
bale variation/clear bay width still matters for four-bale fit.

30 new trials: 29 pass saved paired initial/future-canopy all-A envelope
checks; one failed the optimiser repair limit, not proof of infeasibility.
Study covers 576 and 840 m², target bays 5.5 / 8 / 10.3 m, widths up to
about 28 m within 15–30 m scope, and widths fitting whole rows + clearance.
No solar allowance was priced in this new study. Future solar still requires
the four-state gate; do not generalise new no-solar passes to solar provision.

At the final 273.6 mm allowance:
- 18 × 32, 7.5°: 1,008 bales, $25,090, $24.89/bale.
- 18 × 32, 10°: 1,008 bales, $25,065, $24.87/bale.
- 21 × 40, 7.5°: 1,530 bales, $34,426, $22.50/bale.
- 21 × 40, 10°: 1,530 bales, $34,738, $22.70/bale.
- Best new 576 m² at 10°: 18.641 × 30.9 m, 3 × 10.3 m bays,
  15 rows × 4 bales/bay × 3 bays × 6 layers = 1,080 bales;
  $25,137, $23.27/bale. Its NEW matched 7.5° trial costs $27,482.
- Best new 840 m² at 10°: 21.036 × 39.930 m, 5 × 7.986 m bays,
  1,530 bales; $33,998, $22.22/bale. Its NEW matched 7.5° trial costs $35,158.

Both 10° winners and both existing offerings at 10° passed fresh 12/12
A0–A5 initial/future-canopy checks each. Presets and dossiers are saved in
the study directory. Matched 7.5° checks are in `matched_7_5deg` and have
paired envelope passes, not separate regional proofs. Costs/member sizing
are heuristic best-found; do not infer the 36 mm width change alone causes
the cost difference. Wider sheds did not improve this bounded new ranking.

Drivers: `hay_shed_pitch_bale_study.py`, `hay_shed_pitch_matched_check.py`,
`hay_shed_pitch_bale_report.py`. Earlier arithmetic-only report is
`hay_shed_63_all_a_v2/BALE_COMPARISON.md` (initial allowance assumptions).
An initial `hay_shed_10deg_bale_study` batch was stopped due to sub-mm
input widths failing the native whole-mm geometry check. It is incomplete
and superseded by `_v2`; those errors do not establish structural failure.

The bounded search is complete. Read `hay_shed_63_all_a_v2/REPORT.md` and
`hay_shed_63_all_a_v2/FINAL_COMPARISON.json` before doing any further work.
Do not rerun the search by default. All confirmed results and native presets
are saved locally; chat stream disconnections did not remove them.

## Confirmed best-found layouts

| Main roof allowance | Main width × length | Bays | Main truss depth | Main steel now |
|---|---|---|---|---|
| No solar | 17 × 49.408 m | 8 × 6.176 m | 850 mm | $31,198 |
| Future solar 0.15 kPa | 16 × 52.497 m | 9 × 5.833 m | 800 mm | $32,888 |

Both open long sides face true bearing 63° (27° north of East). All A0–A5
remain included; A2 exclusion was only a what-if and was not adopted.
Both have 6 m eaves, 7.5° gable, IL1, TC2 and about 840 m² main coverage.
Main depth is capped at nominal width/20. Roof purlins use continuous lapped Z;
wall/end girts use single-span C. Pinned bases; no internal columns.

Both designs reserve a future 6 m cantilever canopy on the open side, without
outer columns or canopy solar. Native canopy assumptions: 5° pitch, G 0.15 kPa,
Q 0.25 kPa and Main roof Cpe + Cpi wind model. Its future geometry/member
schedule is saved in the presets. Future canopy frame purchases are excluded
from current main-shed steel cost.

No-solar design passes 12 actual-region checks (A0–A5 × initial/future canopy).
Future-solar design passes 24 (A0–A5 × bare/canopy/solar/solar+canopy). It uses
the same main physical section, web and chord-reinforcement schedule in all
states; exact identity with/without panels was verified. Presets:

- `hay_shed_63_all_a_v2/best_no_solar_inputs.json`
- `hay_shed_63_all_a_v2/best_solar_inputs.json`

Both winners have rear 250 UB 25.7, open-side 310 UB 32.0 and main top/bottom
65 × 2.5 SHS. 28 main truss panels; no retained main doubled-chord panels in
these winners. Individual web profiles are saved separately. Roof/wall cold
formed selections and current steel rates are detailed in REPORT.md.

## Completed implementation and verification

- `wind.py`: numeric bearings and A-ALL envelope of individually interpolated
  regional wind curves, followed by each building direction's ±45° window.
  Corrected the A1 west Md typo from 0.10 to 1.00.
- `main.py`: custom north selector, native Future Canopy Optimise, independent
  compression/tension web-demand envelope and governing combinations.
  The old maximum-absolute-force accumulation could discard critical
  compression; old false-pass results are explicitly invalidated.
- `future_canopy_design.py`: common primary section/web envelope and union of
  chord/web reinforcement, followed by exact current/future checks.
- `future_solar_design.py`: fixed-geometry four-state optional-solar gate and
  native physics repairs. Enlarged column sections may change centroid offsets
  inside the fixed outside footprint; the combined geometry is then frozen.
- `hay_shed_fixed_bearing.py`, `hay_shed_future_solar.py`,
  `hay_shed_optional_solar_report.py`: geometry search, solar gate and final report.
- Native saved-preset proof passed 12 and 24 checks for the final winners.
  Wind, force-envelope, search, canopy merge and corrected service regressions
  were run successfully; standalone new modules compile.

Ordinary desktop Future Canopy Optimise checks two canopy states at the
entered solar load. Optional panels require the four-state script; do not
assume rerunning the ordinary button retains the four-state verification.

## Search scope and limits

65 whole-bay layouts per roof case at integer widths 15–30 m and bays 5–10 m;
20 nearby-width trials per case; two cross-seed trials; four additional nearby
solar widths at nine bays. Physics optimiser used throughout. Fifteen cheap
seeds completed the final four-state gate. One optional extra seed at 16.5 m /
6.364 m bays was stopped after prolonged runtime; another seed at that geometry
already passes all four states at $34,176. Stopped/failed trials do not establish
structural infeasibility. This is a best-found bounded search, not a proof of
the absolute global minimum.

SHS stock-length nesting is deferred at the user's request; current model uses
7% SHS waste and 15% roof Z lap allowance. Prices are modelled steel purchases,
excluding cladding, foundations, full connection fabrication, freight, erection,
solar equipment and future canopy frame supply. The no-solar 21 × 40 m / 8 m
bay reference passes and costs $34,426; selected no-solar layout saves $3,228.

`comparison.json` is the original geometry-stage checkpoint and its solar
winner assumes panels present. FINAL_COMPARISON.json and the final best-solar
preset are authoritative for optional solar. Core/source snapshot and detailed
history are in `build/HAY_SHED_WORK_LOG.md` and `build/hay_shed_engine_archive`.
The workspace is very dirty from prior work; preserve unrelated changes.

## Chat issue

User repeatedly receives: "stream disconnected before completion: An error
occurred while processing your request." Request ID supplied by user:
`c84d9ba1-0d49-400a-a683-bc100a4fe9b3`.
The cause has not been diagnosed. Official troubleshooting recommends a fresh
chat with a smaller prompt for stuck conversations. If it persists in a fresh
chat, use app feedback/support with the request ID. No messages to support have
been sent. No new chat has been created automatically.
