# Future Canopy Optimise

Configure the cantilever canopy under Additions, set the maximum main truss
depth if needed, then click **Future Canopy Optimise** below the optimiser
buttons in Sections. The mode supports a main truss with one or two cantilever
canopies. An empty main depth cap defaults to span/20; an entered cap is retained.

The mode sizes the future condition with Physics Optimise, then sizes the shed
without the canopy at the same main depth and panel layout. It combines the
required primary sections, web assignments, web-joint reinforcement and doubled
chord panels into one physical design. The two independent designs and their
combined sections appear in the report. Main truss geometry stays common because
member-by-member merging requires the same physical panels in both states.

For columns and chords, the initial catalogue envelope considers area,
analysis stiffness, weak-axis inertia and material strength properties. It can
select a third section when neither independent selection envelopes the other.
This is a starting choice, not a structural proof: the full hybrid is reanalysed
in both states. A failing state triggers another physics search at the common
geometry, followed by another merge and both checks. After the bounded repair
limit, an unverified proposal is reported and the original model is restored.

Doubled-chord panels use the **union of their physical locations**, rather than
the maximum total length. Separate governing zones are both retained. Extra
retained zones follow the engine's existing design-only doubler treatment; they
add fabrication/cost allowances and never suppress a failed state check. The
existing doubled-chord length limit still applies.

Every hybrid check freezes the primary sections, truss topology and web schedule.
It also requires the automatic roof general/edge, wall-girt and end-wall capacity
checks to pass in both phases. The hay search sets single-span C wall girts and
lapped Z roof purlins explicitly; the desktop mode uses the visible selectors.
The saved preset includes the common panel count, web assignments and retained
doubler panels, and the normal desktop preset loader restores them. Geometry
or canopy-dimension changes invalidate the retained layout and require rerunning
the mode. The report records the checks from the completed optimisation run;
rerun it after changing the design loads, site or member selections.

The ordinary desktop costing view describes the displayed model, including
canopy steel when it is shown. The hay-shed search instead prices the main shed
built now, excluding future canopy frame purchases while retaining their loads.
The paired allowance in the optimisation report uses the higher secondary-steel
cost by category across the two phases. Automatic secondary selections can differ between phases; a common
secondary schedule still needs to be selected. This mode currently envelopes
the primary frame and its reinforcement.

Run the fast merge-policy tests with:

```powershell
python future_canopy_design_regression.py
```

The hay search additionally freezes and independently reloads each passing
proposal in both canopy states before it can enter the ranking.
