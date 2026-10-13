# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


block_cipher = None

datas = [
    ("Shape_Catalog.xlsx", "."),
    ("metroll_megaspan_capacity_tables.json", "."),
    ("EULA.txt", "."),
    ("presets", "presets"),
    ("assets", "assets"),
]
datas += collect_data_files("certifi")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "portalcalc_service",
        "portalcalc_headless",
        "configurator_review",
        "matplotlib.backends.backend_qtagg",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "fastapi",
        "uvicorn",
        "pandas",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# PySide 6.11 links Qt6Core against the unversioned Windows ICU API. PyInstaller
# can otherwise collect version-suffixed ICU 78 DLLs from unrelated installed
# packages; Windows then loads those first and QtCore fails at startup because
# symbols such as ucnv_open are exported only as ucnv_open_78.
incompatible_icu_binaries = {"icudt78.dll", "icuuc.dll"}
a.binaries[:] = [
    entry
    for entry in a.binaries
    if Path(entry[0]).name.lower() not in incompatible_icu_binaries
]
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PortalCalc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/portalcalc_icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PortalCalc",
)
