# PortalCalc

BOLT Structural Analysis. PortalCalc designs steel shed structures: the 2D portal
frame (truss or rafter, canopies and lean-tos), secondary steel, 3D bracing,
footings and costing, to AS/NZS 1170 and AS 4100. It runs as a Windows desktop
application and as an engine that BOLT calls directly.

## Layout

| Path | What it holds |
| --- | --- |
| `main.py` | Desktop launcher. |
| `portalcalc/` | The package. Everything outside `portalcalc/desktop/` runs without Qt. |
| `portalcalc/service/` | The calling interface for other applications. |
| `portalcalc/frame/`, `bracing/`, `purlins/`, `building/`, `sizing/`, `reports/` | The engineering. |
| `portalcalc/desktop/` | The PySide6 application. |
| `portalcalc/data/` | Section catalogue, capacity tables, factory defaults, brand assets. |
| `tests/` | Regression scripts and smoke checks. |
| `docs/` | Design notes. Start with `docs/CODEMAP.md`. |
| `license_server.py`, `render.yaml`, `requirements.txt` | The deployed licence API. |

## Run the desktop app

```powershell
pip install -r requirements-desktop.txt
python main.py
```

## Call it from another application

```python
from portalcalc.service import calculate

result = calculate({
    "schema": "portalcalc.calculation.request.v1",
    "request_id": "job-123",
    "mode": "autoSize",          # or "checkSpecified"
    "inputs": preset,            # a complete preset; see portalcalc/data/presets/factory_defaults.json
})
```

The request and result are plain JSON-shaped dictionaries. A v2 request carries
a whole building (`portalcalc.building.v1`) and is checked gridline by gridline.
Schemas and policy are in `docs/CALCULATION_SERVICE.md`. Every result is a
proposal that needs engineering review.

## Test

```powershell
python run_tests.py                 # everything, 10 to 15 minutes
python run_tests.py bracing_layout  # scripts whose name contains a word
python tests/smoke_checks.py --quick
```

## Build the Windows package

```powershell
.\build_desktop.ps1
```

See `docs/PACKAGING.md`.
