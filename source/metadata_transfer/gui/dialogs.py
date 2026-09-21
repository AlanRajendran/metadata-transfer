# SPDX-License-Identifier: GPL-3.0-or-later AND MIT
"""About box, crash window and the path helpers they use (layouts of the BIOMIS team's
Timelapse Video Processing app)."""
from __future__ import annotations

import os
import platform
import sys
import time
import traceback

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, CREDITS, DISCLAIMER, LICENSE_NAME, REPOSITORY_URL, RESEARCH_USE, VERSION
from ..log import log_dir
from ..presets import settings_dir


def friendly_path(path: str) -> str:
    """`path` as it reads on any PC: %LOCALAPPDATA%, %APPDATA% or %USERPROFILE% instead of this user's folder."""
    text = str(path)
    for variable in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        base = os.environ.get(variable)
        if base and text.lower().startswith(base.rstrip("\\/").lower()):
            return f"%{variable}%" + text[len(base.rstrip("\\/")):]
    return text


def friendly_text(text: str) -> str:
    """Every occurrence of this user's profile folders in `text` replaced by %LOCALAPPDATA% etc."""
    for variable in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        base = os.environ.get(variable)
        if base:
            base = base.rstrip("\\/")
            for spelling in {base, base.replace("\\", "/")}:
                start = text.lower().find(spelling.lower())
                while start >= 0:
                    text = text[:start] + f"%{variable}%" + text[start + len(spelling):]
                    start = text.lower().find(spelling.lower(), start + len(variable) + 2)
    return text


class AboutDialog(QDialog):
    """Non-modal About box (`open()`), so headless runs never block on it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)
        title = QLabel(APP_NAME)
        title.setProperty("role", "title")
        version = QLabel(f"Version {VERSION}")
        version.setProperty("role", "muted")
        layout.addWidget(title)
        layout.addWidget(version)
        what = QLabel("Converts microscopy files between Leica LIF, Evident/Olympus cellSens VSI and Nikon "
                      "NIS-Elements ND2, with their metadata, and checks every file it writes.")
        what.setWordWrap(True)
        layout.addWidget(what)
        credit = QLabel(CREDITS)
        credit.setWordWrap(True)
        credit.setProperty("role", "heading")
        layout.addWidget(credit)
        for text in (DISCLAIMER, RESEARCH_USE,
                     f"{LICENSE_NAME} licence. Provided as is, without warranty. Third-party components keep their "
                     "own licences (THIRD_PARTY_NOTICES.txt in the install folder)."):
            lab = QLabel(text)
            lab.setWordWrap(True)
            lab.setProperty("role", "muted")
            layout.addWidget(lab)
        paths = QLabel(f"Settings and presets: {friendly_path(settings_dir())}\nLogs: {friendly_path(log_dir())}")
        paths.setProperty("role", "muted")
        paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        paths.setWordWrap(True)
        layout.addWidget(paths)
        links = QHBoxLayout()
        self.repo_button = QPushButton("Project page and issues")
        self.repo_button.setToolTip(REPOSITORY_URL)
        self.repo_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPOSITORY_URL)))
        logs = QPushButton("Open log folder")
        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir())))
        links.addWidget(self.repo_button)
        links.addWidget(logs)
        links.addStretch(1)
        layout.addLayout(links)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        layout.addWidget(buttons)

    def text(self) -> str:
        return "\n".join(label.text() for label in self.findChildren(QLabel))


class CrashDialog(QDialog):
    def __init__(self, summary: str, report: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME}: something went wrong")
        self.setMinimumSize(560, 360)
        self._report = report
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        title = QLabel("Something went wrong")
        title.setProperty("role", "subtitle")
        layout.addWidget(title)
        text = QLabel(f"{summary}\n\nThe app kept running. If something looks wrong, restart it. To report the "
                      f"problem, copy the report below into an issue at {REPOSITORY_URL}/issues; it holds no "
                      "image data.")
        text.setWordWrap(True)
        layout.addWidget(text)
        self.details = QPlainTextEdit(report)
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        buttons = QHBoxLayout()
        self.copy_button = QPushButton("Copy report")
        self.copy_button.clicked.connect(self.copy_report)
        logs = QPushButton("Open log folder")
        logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir())))
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(logs)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def copy_report(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(self._report)
        self.copy_button.setText("Copied")


def build_report(exc_type, exc_value, exc_tb) -> str:
    try:
        from PySide6 import __version__ as pyside_version
    except Exception:  # pragma: no cover
        pyside_version = "?"
    head = (f"{APP_NAME} {VERSION}\nPython {sys.version.split()[0]} · PySide6 {pyside_version} · "
            f"{platform.system()} {platform.release()}\n\n")
    return friendly_text(head + "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))


class CrashReporter(QObject):
    raised = Signal(str, str)  # summary, report

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.window = window
        self.dialog: CrashDialog | None = None
        self._last = 0.0
        self.raised.connect(self._show)

    def report(self, exc_type, exc_value, exc_tb) -> None:
        self.raised.emit(f"{exc_type.__name__}: {exc_value}", build_report(exc_type, exc_value, exc_tb))

    def _show(self, summary: str, report: str) -> None:
        now = time.monotonic()
        if self.dialog is not None and self.dialog.isVisible() and now - self._last < 10.0:
            return
        self._last = now
        self.dialog = CrashDialog(summary, report, self.window)
        self.dialog.open()


_REPORTER: CrashReporter | None = None


def install_crash_window(window: QWidget) -> CrashReporter:
    global _REPORTER
    _REPORTER = CrashReporter(window)
    return _REPORTER


def report_exception(exc_type, exc_value, exc_tb) -> None:
    reporter = _REPORTER
    if reporter is None:
        return
    try:
        reporter.report(exc_type, exc_value, exc_tb)
    except RuntimeError:
        pass
