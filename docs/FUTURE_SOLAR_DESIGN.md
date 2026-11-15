# Optional solar and future canopy checks

`future_solar_design.run(window)` takes a saved canopy-hybrid design as its
geometry seed. It verifies one common main frame in four installation states:
bare shed, canopy only, solar only, and solar plus canopy. Solar applies to the
main roof only; the configured canopy retains its own zero solar allowance.

The seed's outside footprint, eave, pitch, truss depth, panel count and canopy
depth stay fixed. Failed states receive native Physics Optimise repairs at that
geometry. Catalogue properties envelope the proposed sections and individual
web assignments; reinforcement is the union of required chord panels and web
joint doublers. The actual combined schedule is solved in every state again.

Larger column profiles can move centroid positions inside the fixed outside
footprint. The physical coordinates are established after combining those
sections, then compared between installation states. Member schedules from
different depths or panel counts cannot be combined.

The saved `future_canopy_design` includes all four checks, the common sections,
retained reinforcement, `future_solar_allowance_kpa` and source hash. Native
preset loading preserves that physical schedule. Running the ordinary desktop
Future Canopy Optimise button performs its two canopy states at the entered
solar load; use this four-state runner when panels are a future allowance.

After the fixed-bearing coarse/refinement search completes, run:

```powershell
python hay_shed_future_solar.py --search hay_shed_63_all_a_v2 --top 8 --workers 4
```

It checks cheap geometry seeds from both roof cases, ranks their combined
four-state costs, and checks the selected saved frame in A0–A5 with and without
solar and canopy (24 actual-region checks). These are heuristic best-found
designs. The main-steel cost excludes future canopy frame purchases and takes
the higher allowance in each cost category across the four states. Automatic
secondary selections must be consolidated into the eventual purchase schedule.
