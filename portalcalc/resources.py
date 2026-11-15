"""Locations of the data files bundled with PortalCalc.

Everything the engine reads at run time (section catalogue, purlin capacity
tables, factory-default preset, EULA and brand assets) lives in
``portalcalc/data``. The same relative layout is used from source and in the
packaged build, where PyInstaller copies the folder next to the package.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"

SHAPE_CATALOG = "Shape_Catalog.xlsx"
PURLIN_CAPACITY_TABLES = "metroll_megaspan_capacity_tables.json"
FACTORY_DEFAULTS = "presets/factory_defaults.json"


def data_path(*parts: str) -> Path:
    """Path of a bundled data file, e.g. ``data_path("presets", "factory_defaults.json")``."""
    return DATA_DIR.joinpath(*parts)


def source_file_hashes(*packages: str) -> dict[str, str]:
    """SHA-256 of each PortalCalc source file, keyed by its path inside the package.

    ``packages`` limits the result to sub-packages or modules (``"bracing"``,
    ``"wind.py"``); with none given every source file is included. A packaged
    build has no ``.py`` files, so the result is empty there.
    """
    if packages:
        files: list[Path] = []
        for name in packages:
            target = PACKAGE_DIR / name
            files += sorted(target.rglob("*.py")) if target.is_dir() else ([target] if target.is_file() else [])
    else:
        files = sorted(PACKAGE_DIR.rglob("*.py"))
    return {
        path.relative_to(PACKAGE_DIR).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
