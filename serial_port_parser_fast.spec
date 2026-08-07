# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the serial protocol parser.

This file is intentionally kept in the project root because the Python build manager
invokes it directly.  The onedir layout starts faster than a onefile build and
copies bundled defaults while mutable products live in LocalAppData.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


PROJECT_ROOT = Path(SPECPATH).resolve()


def _collect_package(package_name: str):
    """Collect package resources without making the spec fail unnecessarily."""
    try:
        return collect_all(package_name)
    except Exception:
        return [], [], []


qfluent_datas, qfluent_binaries, qfluent_hidden = _collect_package("qfluentwidgets")
docx_datas, docx_binaries, docx_hidden = _collect_package("docx")

# qfluentwidgets and python-docx both use package resources that are not always
# discovered from plain imports, so collect them explicitly.
datas = [
    (str(PROJECT_ROOT / "resources"), "resources"),
    (str(PROJECT_ROOT / "product"), "product"),
    # Initial command-library data is bundled read-only and copied into
    # LocalAppData on first run without overwriting existing user files.
    (str(PROJECT_ROOT / "data"), "defaults/data"),
]
datas += qfluent_datas
datas += docx_datas

binaries = []
binaries += qfluent_binaries
binaries += docx_binaries

hiddenimports = [
    "shiboken6",
    "serial.tools.list_ports",
    "serial.tools.list_ports_windows",
    "serial.serialwin32",
    "docx",
    "docx.opc.constants",
]
hiddenimports += qfluent_hidden
hiddenimports += docx_hidden
hiddenimports += collect_submodules("protocol_parser")

# Preserve installed distribution metadata used by some qfluentwidgets builds.
try:
    datas += copy_metadata("PySide6-Fluent-Widgets")
except Exception:
    pass


a = Analysis(
    [str(PROJECT_ROOT / "exe_entry.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5",
        "PyQt6",
        "PySide2",
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SST_串口工具",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "resources" / "lkl.ico"),
    version=str(PROJECT_ROOT / "resources" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SST_串口工具",
)
