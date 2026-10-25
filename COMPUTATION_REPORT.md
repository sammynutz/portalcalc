# Full structural computation report

Use **File > Export Full Computations (PDF)**. Enter the project, job reference,
prepared-by name and revision. Export performs a fresh analysis of the current
sections and preserves the selected web distribution. The existing short
Engineering Summary is still available.

The standard report includes:

1. A cover with the actual governing result, linked contents and bookmarks.
2. All engineering inputs, with units and inactive settings retained.
3. Actual directional and connected-roof wind bases, speed/pressure workings,
   reference Cpe zones and applied enclosure/pressure selections.
4. Gravity/other load calculations and the complete combination register.
5. Analytical node/member schedules, shared DOFs, restraints, action envelopes,
   foundation reaction envelopes and governing serviceability records.
6. Governing member checks and worked capacity values, with demand sources,
   chord averaging, eccentricity, doublers and flybracing notes.
7. Every selected web/post, independent compression/tension sources and
   governing implemented weld/chord-wall/K-joint screens with equations.
8. Secondary checks, calculation scope and the full native design-check record.
9. Governing-case equivalent nodal-load/bending diagrams, load-calculation
   records, signed reactions and case extrema.

Select **Include all nodal and member results for governing cases** for the large
appendix. It adds every nodal displacement/applied force, member action, signed
local end force and distributed member load for those cases. For a complex
model this can add hundreds of pages. All combinations are analysed in both
report modes; the option controls printed detail.

## Implementation

- `MainWindow.capture_computation_report` enables capture for one exact solve,
  restores capture flags in `finally`, and freezes the final evidence.
- `computation_report_data.py` builds the frozen evidence. Cases are captured
  inside the existing envelope solver, including storage patterns and
  second-order results. Wind records are copied from the actual solver caches.
- `computation_report.py` renders the PDF from that evidence without reading the
  UI or re-solving. It uses ReportLab, repeating table headers, PDF bookmarks and
  an automatically paginated table of contents. Writes are atomic.
- `MainWindow.resolved_member_check` is shared by the live Section Checks and
  the report, including the existing bottom-chord flybracing fallback.
- Standard app solves do not capture the large report case records.
- `computation_report_regression.py` checks exact result identity, web-selection
  evidence, source cases, frozen inputs, PDF structure and atomic failures.

The report describes implemented calculations, including their existing
preliminary checks and validity notes. It does not claim that unimplemented
connections have been designed. Independent envelope maxima are explicitly
distinguished from coincident case forces.

## Validation

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python computation_report_regression.py
python smoke_checks.py --full
```

For PDF layout review, render the exported example using Poppler and inspect
cover, contents, inputs, wind, member/web checks and governing-case pages.
