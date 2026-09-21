# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""PyInstaller spec for Metadata Transfer (one-folder build, two programs).

Run by packaging\\build.ps1:
    python -m PyInstaller --noconfirm --clean --distpath <stage>\\dist --workpath <stage>\\pyinstaller packaging\\app.spec

Output: <stage>\\dist\\Metadata Transfer\\ with "Metadata Transfer.exe" (window) and
"MetadataTransfer-cli.exe" (command line) sharing one _internal\\ folder. installer.nsi packs
exactly this folder.

The package assets (icon) and the .vsi writer's template are package data, loaded with
importlib.resources in both source and frozen runs. Qt modules the app never uses are excluded
and their DLLs, plugins and translations removed after analysis (the PySide6 hook copies plugin
folders regardless of imports) — this also keeps Qt Virtual Keyboard (GPL-only) out of the build.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

PACKAGING_DIR = Path(SPECPATH)
ROOT = PACKAGING_DIR.parent
SOURCE_DIR = ROOT / "source"
PKG = SOURCE_DIR / "metadata_transfer"
ICON = str(PKG / "assets" / "icon.ico")
LAUNCHER = str(PACKAGING_DIR / "launcher.py")
VERSION_FILE = str(PACKAGING_DIR / "version_info.txt")

datas = [
    (str(PKG / "assets"), "metadata_transfer/assets"),
    (str(PKG / "formats" / "data"), "metadata_transfer/formats/data"),
]
binaries = []
hiddenimports = []
_notices = PACKAGING_DIR / "THIRD_PARTY_NOTICES.txt"
if _notices.exists():
    datas.append((str(_notices), "."))
_licence = ROOT / "LICENSE"
if _licence.exists():
    datas.append((str(_licence), "."))

# xsdata_pydantic_basemodel is loaded through an ome_types entry point, invisible to the analysis.
for _package in ("limnd2", "liffile", "nd2", "ome_types", "xsdata", "xsdata_pydantic_basemodel", "tifffile"):
    _d, _b, _h = collect_all(_package)
    datas += _d
    binaries += _b
    hiddenimports += _h

PYSIDE6_EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D", "PySide6.QtQuickControls2",
    "PySide6.QtQml", "PySide6.QtVirtualKeyboard",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtDesigner",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
    "PySide6.QtWebSockets", "PySide6.QtWebChannel", "PySide6.QtHttpServer", "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets", "PySide6.QtDataVisualization", "PySide6.QtLocation",
]
OTHER_EXCLUDES = ["PyQt5", "PyQt6", "PySide2", "tkinter", "pytest", "IPython", "matplotlib"]

_STRIP_DEST_PREFIXES = tuple(n.replace("PySide6.", "PySide6/") for n in PYSIDE6_EXCLUDES) + tuple(
    n.replace("PySide6.Qt", "PySide6/Qt6") for n in PYSIDE6_EXCLUDES
) + (
    "PySide6/translations", "PySide6/Qt/translations", "PySide6/Qt/qml", "PySide6/qml",
    "PySide6/opengl32sw.dll", "PySide6/plugins/platforminputcontexts", "PySide6/plugins/virtualkeyboard",
    "PySide6/plugins/qmltooling", "PySide6/plugins/multimedia", "PySide6/plugins/position",
    "PySide6/plugins/sqldrivers", "PySide6/plugins/webview", "PIL/_avif",
)


def _survives_trim(entry):
    return not entry[0].replace("\\", "/").startswith(_STRIP_DEST_PREFIXES)


a = Analysis(
    [LAUNCHER],
    pathex=[str(SOURCE_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    excludes=PYSIDE6_EXCLUDES + OTHER_EXCLUDES,
    noarchive=False,
)
a.binaries = [b for b in a.binaries if _survives_trim(b)]
a.datas = [d for d in a.datas if _survives_trim(d)]
pyz = PYZ(a.pure)

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Metadata Transfer", console=False, icon=ICON,
          version=VERSION_FILE, upx=False, strip=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MetadataTransfer-cli", console=True, icon=ICON,
          version=VERSION_FILE, upx=False, strip=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="Metadata Transfer")
