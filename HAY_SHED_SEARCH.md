# Hay shed search

`hay_shed_search.py` calls the existing offscreen PortalCalc calculation service
and **Physics Optimise** for each geometry. It changes no desktop settings,
user settings or optimiser learning. Each worker uses isolated local app data.

The initial search fixes approximately 840 m² of floor area, 6 m eave height,
7.5° gable pitch, a clear-span truss with pinned bases, and one open **long**
side. Widths default to 15–30 m in 1 m steps. Length follows from area/width;
Main truss depth is capped at **span/20** (750–1,500 mm for this width range),
rounded down to a selectable 50 mm depth. Saved depths are checked against the cap.
Canopy depths use their existing separate geometry bounds; the main-span cap does
not impose a 300 mm canopy root depth on the 6 m sloping cantilever.
every possible integer bay count giving equal bays between 5 and 10 m is tried.
Bay spacing rounds to the nearest millimetre, changing floor area by less than
0.1 m² on this grid. Wider/narrower bay limits and fractional widths are supported.

Open-side compass directions are converted to PortalCalc's north-in-elevation
selector. The right long wall is open; both ends and the left long wall are clad.
This is set explicitly because a legacy `3 Sided` preset may describe an open
end and the factory preset currently supplies four clad walls.

The search uses single-span **C wall girts** (including end walls) and continuous
lapped **Z roof purlins** for every candidate, with capacity tables 1A/1B and
5A/5B respectively. Roof purchase weights include the engine's existing 15%
lap allowance. Every ranked phase must pass the automatic roof general/edge,
wall-girt and end-wall checks as well as primary-frame checks.

Defaults are IL1, TC2, 0.15 kPa roof dead load, 0.25 kPa roof live load,
no solar/services/crane/storage loads, and first-order analysis. `--solar-kpa 0.15`
adds the preset solar allowance as a separate load; `--solar-side Left` or `Right`
limits that allowance to one roof slope (default is both slopes). Dead/live/snow
loads and material rates come from `--preset`; additional roof services are removed.
The seed defaults to `presets/factory_defaults.json`.
Use `--importance 2` or another terrain/pitch where applicable.

## Run

Preview the full grid (3,120 candidates):

```powershell
python hay_shed_search.py --plan
```

Search geometry for A5 with the opening facing east:

```powershell
python hay_shed_search.py --regions A5 --facings E --workers 6 --output hay_shed_geometry_sweep
```

Search all eight open-side directions in all six A subregions:

```powershell
python hay_shed_search.py --workers 6 --output hay_shed_full_sweep
```

Repeat the same grid with solar in a separate result folder:

```powershell
python hay_shed_search.py --workers 6 --solar-kpa 0.15 --output hay_shed_full_sweep_solar
```

Compare each folder's own winner as well as like-for-like geometries. Solar load
can improve uplift checks while increasing gravity demands, so each scenario is
independently re-optimised rather than applying a fixed price surcharge.

Allow for a future 6 m cantilever on the open side in both solar cases:

```powershell
python hay_shed_search.py --future-canopy-m 6 --workers 6 --output hay_shed_future_no_solar
python hay_shed_search.py --future-canopy-m 6 --solar-kpa 0.15 --workers 6 --output hay_shed_future_solar
```

Or run both complete grids with one command (the paired runner defaults to the
6 m future canopy and a 0.15 kPa main-roof solar allowance):

```powershell
python hay_shed_pair.py --workers 6 --output hay_shed_full_pair
```

Use `--plan` to preview both grids; ordinary search options are forwarded. Each
scenario keeps its own checkpoints and a combined `comparison.json` is written.
Once both grids finish, the paired runner also trials each subregion's winner
under the opposite roof loading as a fresh sizing seed. This protects useful
member/depth layouts that a single starting design can miss.

Run the cheaper staged search (both solar cases, span/20 cap and future canopy):

```powershell
python hay_shed_staged.py --workers 6 --output hay_shed_physics_staged
```

It sweeps all 65 geometries in A5 facing east, tries the two cheapest distinct
layouts from each scenario in all 48 subregion/direction combinations, then
checks nearby widths in 0.25 m steps around the best result and A5 result.
Each candidate still runs Physics Optimise and both installation-stage checks.
`--shortlist 3` broadens the direction stage. Repeating a command resumes its
completed stages when their source/input manifests match.

The future canopy has 5° pitch, 0.15 kPa dead load, 0.25 kPa live load, no solar,
and the existing main-roof Cpe + Cpi wind treatment. It has no outer columns.
840 m² refers to the main shed; future canopy area is additional. The search-only
backend removes future canopy frame purchases from the main-build-now price,
while retaining those members and their loads in the analysis and optimiser.
The search now uses the software's **Future Canopy Optimise** flow: independently
size both stages at a common truss geometry, envelope primary sections and
reinforcement, then check and repair the hybrid in both stages. See
`FUTURE_CANOPY_DESIGN.md` for the desktop control and saved-preset behavior.
Every passing design is also checked with the canopy removed, using the same
main sections, panel count and main web assignments. A design failing that
initial state cannot win. The price retains the higher cost in each main steel
category across the two states. Automatically selected secondary steel is listed
separately in both dossiers; final common purlin/girt/mullion selections require
review. The canopy frame cost shown separately is not a complete future canopy
price (canopy purlins, cladding, installation and connections are not fully priced).

The full grid takes hours. `--limit 12` runs only the next 12 unfinished
candidates for a pilot. Repeat the identical command without `--limit` to
continue. Ctrl+C stops scheduling new candidates and saves active calculations
as they finish. Engine/data/grid changes require a new output directory.

Refine widths around an interesting result, for example:

```powershell
python hay_shed_search.py --regions A5 --facings E --widths 19.5,19.75,20,20.25,20.5 --workers 6 --output hay_shed_refinement
```

## Results and interpretation

- `ranking.csv`: passing designs first, ranked by steel cost per actual m²;
  failed/error rows are retained after the passing rows.
- `summary.json`: progress, top 20 and the cheapest passing result per subregion.
- `best_A*_preset.json`: calculation-service inputs, including optimised truss
  depth, topology and web assignments. Exact reload is supported by the service;
  hybrid presets also retain these selections through the ordinary desktop loader.
- `calculations/*.json`: full checks, costing, foundations, sections and provenance.
- `results.jsonl`: durable per-candidate checkpoint journal.
- `manifest.json`: exact input grid, assumptions and source/catalogue hashes.

Compare sites within their actual subregion. Choosing A1 instead of A5 describes
a different site assumption, not a saving available at the same site.
The script reports the cheapest **found** result. Widths are discrete and the
physics member/depth optimiser is heuristic, so this does not prove an absolute
global minimum. An unfinished search is labelled with completed/planned counts.

The objective uses PortalCalc's structural steel estimate, including its existing
UB/WB stock allowance and 7% SHS wastage. It is not an installed shed quotation:
cladding, foundations, transport, erection and SHS cutting/nesting are not priced
comprehensively. Those could change the ranking. Whole bays and actual portal
counts are checked against the engine to prevent an extra frame caused by
rounding. Only designs passing Physics Optimise and the exact reload can win.

A staged search first sweeps the 65 equal-bay geometries, tests all compass
directions and A subregions at promising layouts, then refines widths around
the winners. This is quicker than the full 6,240 paired grid. Catalogue section
jumps and integer bay counts make a final local grid more reliable than assuming
a smooth cost surface. Regional winners remain site-specific.

Validation:

```powershell
python hay_shed_search_regression.py
```

Check a saved winner without re-optimising:

```powershell
python hay_shed_check.py hay_shed_geometry_sweep/best_A5_preset.json
```

Each passing search candidate must also pass this frozen reload and reproduce its
cost before it can win. The service now preserves main truss panel counts so web
assignment IDs refer to the same members on reload.

The fast regression checks equal area/whole bays, direction conversion, explicit
enclosure, frozen seed inputs, candidate identity, and exclusion of cheaper failed
designs. Real-engine pilot and geometry runs supply the integration checks.
# All-A orientation update

## Active fixed-bearing workflow

The active corrected run is `hay_shed_63_all_a_v2`. The earlier directory
`hay_shed_63_all_a` is invalidated: native checks exposed an existing web force
envelope bug where a larger tensile force could discard buckling-governing
compression. The engine now retains both maxima and their load combinations.
The formerly passing reference is required to fail when replayed with the
corrected checks; the freshly sized no-solar reference passes all 12 actual
subregion/state checks. Old section schedules are not reused as passing designs.

`python hay_shed_fixed_bearing.py --workers 8` searches the main shed with its
opening facing 63° clockwise from true north. All A0–A5 remain included.
It checks 65 whole-bay layouts per solar case (15–30 m widths at 1 m intervals,
5–10 m bays, about 840 m²), refines nearby widths, tries an opposite-case sizing
seed, then reloads each winner's same physical primary schedule in every actual
A subregion before and after the future canopy is installed.

The native compass selector now supports a custom angle measured clockwise
from up in the section view. A right-hand open wall facing true bearing 63°
uses custom north angle 27°. Custom orientations are preserved in saved presets.
The engine interpolates each actual regional curve, takes each ±45° window's
maximum, then envelopes regions. The search records and validates the actual
physical wind sources and design Md values in every calculation dossier.

Both solar cases reserve the 6 m cantilever canopy; no canopy solar is applied.
Main members and the union of reinforcement locations must pass both canopy
states. Walls use single-span C, roofs continuous lapped Z, with main depth
capped at span/20. Results are modelled main steel cost, excluding the future
canopy frame purchase and installed-price items. See the generated REPORT.md
and best_*_inputs.json in `hay_shed_63_all_a_v2` when the runner completes.

The historical paragraph below describing an eight-heading structural limit
is superseded by the custom-angle support above. Numeric bearings can also
be supplied to `hay_shed_search.py --facings 63`.

The current objective is one design that passes A0–A5, separately without and
with main-roof solar. `A-ALL` is a synthetic envelope available in the Wind Region
selector and `hay_shed_search.py --regions A-ALL`. It takes the highest regional
wind speed and directional multiplier from the app's A0–A5 tables. It is not a
geographic subregion. Both closed ends use the worse opposite wind window.

The structural search currently accepts eight compass headings. The 90° window
is ±45° around each building direction. These headings use the maximum of three
adjacent table values. Rotated sectors also require interpolated boundary values;
the numeric helpers in `wind.py` now calculate those exactly, but arbitrary
bearings are not yet connected to the structural UI/preset search. Thus an
East/West comparison cannot establish the best orientation at arbitrary bearings.
For intermediate bearings, A-ALL envelopes the individually interpolated regional
curves, which is tighter than interpolating the cardinal maxima.

The earlier A5 and regional sweeps remain historical candidate results. They
do not identify a universal A winner. Their source manifests predate this wind
update, so use a new output directory for any new sizing run. Final selected
physical schedules must be checked in every actual A subregion in both canopy
states. Keep all accepted C-wall/Z-roof, solar, span/20 and future-canopy assumptions.

