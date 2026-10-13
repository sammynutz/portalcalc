# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


block_cipher = None

datas = [("assets", "assets")]
datas += collect_data_files("certifi")

a = Analysis(
    ["license_generator_app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "fastapi",
        "uvicorn",
        "matplotlib",
        "numpy",
        "openpyxl",
        "pandas",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Keep the frozen Qt runtime on the unversioned Windows ICU implementation.
# The incompatible ICU 78 pair exports suffixed symbols and prevents QtCore
# from importing when it is collected into the one-folder package.
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
    name="PortalCalcAdmin",
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
    name="PortalCalcAdmin",
)
