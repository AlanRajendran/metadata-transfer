# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI bootstrap: application object, theme, icon, main window."""

from __future__ import annotations

import importlib.resources
import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap

from .. import APP_NAME, VERSION, presets
from . import theme

log = logging.getLogger(__name__)


def app_icon() -> QIcon:
    """The app icon from the package assets (made by tools/make_icon.py)."""
    icon = QIcon()
    try:
        assets = importlib.resources.files("metadata_transfer").joinpath("assets")
        for name in ("icon.png",):
            data = assets.joinpath(name).read_bytes()
            pm = QPixmap()
            if pm.loadFromData(data):
                icon.addPixmap(pm)
    except (OSError, FileNotFoundError, ModuleNotFoundError):
        pass
    return icon


def run(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    from .dialogs import install_crash_window
    from .main_window import MainWindow

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(list(sys.argv if argv is None else argv))
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName(APP_NAME)
    app.setWindowIcon(app_icon())
    mode = presets.get("ui_theme", theme.DEFAULT_THEME)
    shown = theme.apply_theme(app, mode)
    log.info("%s %s starting (Python %s) · theme %s", APP_NAME, VERSION, sys.version.split()[0], shown)
    w = MainWindow()
    install_crash_window(w)
    w.show()
    files = [a for a in (argv or sys.argv)[1:] if not a.startswith("-")]
    if files:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, lambda: w.add_paths(files))
    return int(app.exec())
