# PortalCalc Packaging

## Recommendation

Build the customer desktop app as a Windows PyInstaller one-folder package, not a single-file EXE. PortalCalc uses PySide6, matplotlib, numpy, an Excel section catalog, JSON capacity tables, and presets; one-folder packaging starts faster, is easier to inspect, and avoids unpacking a large scientific/Qt stack every launch.

Keep the license server as a deployed FastAPI service on Render using `requirements.txt` and `render.yaml`. Build `license_generator_app.py` as a separate admin-only desktop tool when you want a local admin client.

## Build

From this folder:

```powershell
.\build_desktop.ps1 -Version 2026.06.02
```

If `-Version` is omitted, the script uses `APP_VERSION` from `app_metadata.py`.
The script runs `smoke_checks.py`, builds:

- `dist\PortalCalc\PortalCalc.exe`
- `dist\PortalCalcAdmin\PortalCalcAdmin.exe`

and creates:

- `release\PortalCalc-<version>-windows.zip`

After PyInstaller completes, the build also launches both frozen executables in
offscreen smoke-test mode. Packaging stops before creating the release archive
if either application cannot load its bundled Qt runtime or if incompatible
ICU 78 DLLs have been collected.

Before building a customer release, update `APP_VERSION` and `APP_RELEASE_DATE`
in `app_metadata.py`, then add the release notes to `CHANGELOG.md`. This keeps
the package filename, About panel, and release history aligned.

Use `-SkipSmoke` only when you are deliberately rebuilding after an already-passing smoke run.
For faster local packaging after recent full coverage, use `.\build_desktop.ps1 -SmokeMode quick`.
The quick mode still compiles sources, runs the non-UI calculation checks, starts the app, and exercises the first UI solve path.

## Runtime Files

The customer app bundles:

- `Shape_Catalog.xlsx`
- `metroll_megaspan_capacity_tables.json`
- `EULA.txt`
- logo and icon files from `assets\`
- sample files from `presets\`
- packaged factory defaults from `presets\factory_defaults.json`

Startup applies defaults in this order:

1. hardcoded Python fallback values
2. `presets\factory_defaults.json` next to `PortalCalc.exe`, falling back to the internal bundled copy

User presets are written under the normal PortalCalc app-data directory, not inside the installed program folder. Updating `presets\factory_defaults.json` before building changes the factory defaults packaged into the next build. In the one-folder package, the editable file is `PortalCalc\presets\factory_defaults.json`, and it is loaded on every app open.

## Distribution Notes

For first customer installs, ship the zip or wrap the `dist\PortalCalc` folder in an installer such as Inno Setup. For a paid/customer release, code-sign the EXE or installer to reduce Windows SmartScreen warnings.

Do not distribute `license_server.py` or server private keys with the customer app. The desktop app only needs the public key embedded in `licensing.py` or supplied through `PORTALCALC_LICENSE_PUBLIC_KEY`.
