# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec for WatchPAT ONE Dashboard.

Targets
-------
Windows  : produces dist/WatchPAT/WatchPAT.exe  (one-directory, fast startup)
macOS    : produces dist/WatchPAT.app            (run: pyinstaller watchpat.spec)
Linux    : produces dist/WatchPAT/WatchPAT       (run: pyinstaller watchpat.spec)

Usage
-----
  pyinstaller watchpat.spec            # one-directory build (default)
  pyinstaller watchpat.spec --onefile  # single-file build for distribution
"""

import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Pull in every bleak sub-module so the correct OS backend is included.
bleak_datas, bleak_binaries, bleak_hiddenimports = collect_all("bleak")

a = Analysis(
    ["watchpat_gui.py"],
    pathex=[],
    binaries=bleak_binaries,
    datas=[
        ("assets", "assets"),          # app icon + any future bundled assets
        *bleak_datas,
    ],
    hiddenimports=[
        # matplotlib: TkAgg is the fallback on Windows and Linux
        "matplotlib.backends.backend_tkagg",
        "matplotlib.backends._backend_tk",
        "tkinter",
        "tkinter.ttk",
        *bleak_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # keep the bundle lean — these are unused at runtime
        "pytest",
        "IPython",
        "jupyter",
        "notebook",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # one-directory layout (binaries go to COLLECT)
    name="WatchPAT",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                  # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon="assets/icons/watchpat_app_icon.ico",  # Windows / Linux taskbar icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WatchPAT",
)

# macOS: wrap the collected folder into a .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="WatchPAT.app",
        icon="assets/icons/watchpat_app_icon.png",
        bundle_identifier="com.watchpat.dashboard",
        info_plist={
            "CFBundleShortVersionString": "1.0",
            "NSHighResolutionCapable": True,
            "NSBluetoothAlwaysUsageDescription":
                "WatchPAT needs Bluetooth to connect to the WatchPAT ONE device.",
        },
    )
