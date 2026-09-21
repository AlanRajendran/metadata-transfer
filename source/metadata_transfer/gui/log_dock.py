# SPDX-License-Identifier: GPL-3.0-or-later AND MIT
"""Log dock (layout of the BIOMIS team's Timelapse Video Processing app).

Every module logs through the ``metadata_transfer`` logger. ``QtLogHandler`` turns records into a
queued Qt signal (worker threads are safe) and the dock renders them: timestamped, coloured by
level, Normal hides DEBUG, capped at 5000 lines, autoscroll unless the user scrolled up, Copy,
Save as and Clear. The header row is the dock's title bar, with the collapse button the panel
manager connects. The same records go to the rolling log file (``log.py``).
"""
from __future__ import annotations

import datetime as _dt
import html
import logging
import os
from collections import deque

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..log import ROOT_LOGGER, log_dir
from . import theme as theme_module

MAX_LINES = 5000


class LogBridge(QObject):
    message = Signal(str, str)  # level name, text


class QtLogHandler(logging.Handler):
    def __init__(self, level: int = logging.DEBUG) -> None:
        super().__init__(level)
        self.bridge = LogBridge()
        self._closed = False

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102 - logging API
        if self._closed:
            return
        try:
            text = self.format(record)
        except Exception:  # pragma: no cover
            text = record.getMessage()
        try:
            self.bridge.message.emit(record.levelname, text)
        except RuntimeError:
            self._closed = True

    def close(self) -> None:  # noqa: D102 - logging API
        self._closed = True
        super().close()


class LogDock(QDockWidget):
    def __init__(self, parent=None, debug: bool = False) -> None:
        super().__init__("Log", parent)
        self.setObjectName("log_dock")
        self._theme = theme_module.DEFAULT_THEME
        self._records: deque[tuple[str, str, str]] = deque(maxlen=MAX_LINES)
        self._autoscroll = True

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 0, 8, 6)
        layout.setSpacing(6)

        header = QWidget(self)
        bar = QHBoxLayout(header)
        bar.setContentsMargins(8, 3, 4, 3)
        bar.setSpacing(6)
        heading = QLabel("Log")
        heading.setProperty("role", "heading")
        bar.addWidget(heading)
        self.detail = QComboBox()
        self.detail.addItems(["Normal", "Debug"])
        self.detail.setCurrentIndex(1 if debug else 0)
        self.detail.setToolTip("Normal shows what the app does; Debug adds details for troubleshooting.")
        self.detail.currentIndexChanged.connect(self._rerender)
        bar.addWidget(self.detail)
        bar.addStretch(1)
        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self.copy_to_clipboard)
        self.save_as_button = QPushButton("Save as…")
        self.save_as_button.clicked.connect(lambda _checked=False: self.save_as())
        self.folder_button = QPushButton("Log folder")
        self.folder_button.setToolTip("Open the folder with the rolling log files (attach them to a bug report)")
        self.folder_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir())))
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear)
        for b in (self.copy_button, self.save_as_button, self.folder_button, self.clear_button):
            bar.addWidget(b)
        self.collapse_button = QToolButton(header)
        self.collapse_button.setAutoRaise(True)
        bar.addWidget(self.collapse_button)
        self.setTitleBarWidget(header)

        self.view = QPlainTextEdit()
        self.view.setObjectName("log")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.view.setFont(font)
        self.view.setPlaceholderText("What the app does appears here: files opened, conversions, checks.")
        self.view.verticalScrollBar().valueChanged.connect(self._scrolled)
        layout.addWidget(self.view, 1)
        self.setWidget(body)

        self.handler = QtLogHandler()
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.handler.bridge.message.connect(self.append_record, Qt.ConnectionType.QueuedConnection)
        logger = logging.getLogger(ROOT_LOGGER)
        if logger.level == logging.NOTSET or logger.level > logging.DEBUG:
            logger.setLevel(logging.DEBUG)
        logger.addHandler(self.handler)

    def detach(self) -> None:
        logger = logging.getLogger(ROOT_LOGGER)
        if self.handler in logger.handlers:
            logger.removeHandler(self.handler)
        self.handler.close()

    @property
    def debug_visible(self) -> bool:
        return self.detail.currentIndex() == 1

    def set_theme(self, name: str) -> None:
        self._theme = name if name in theme_module.THEMES else theme_module.DEFAULT_THEME
        self._rerender()

    def append_record(self, level: str, text: str) -> None:
        stamp = _dt.datetime.now().strftime("%H:%M:%S")
        self._records.append((stamp, level, text))
        if self._visible(level):
            self._append_html(stamp, level, text)

    def clear(self) -> None:
        self._records.clear()
        self.view.clear()

    def text(self) -> str:
        return self.view.toPlainText()

    def copy_to_clipboard(self) -> None:
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(self.text())

    def save_as(self) -> str | None:
        default = os.path.join(log_dir(), f"log_{_dt.datetime.now():%Y%m%d_%H%M%S}.txt")
        chosen, _ = QFileDialog.getSaveFileName(self, "Save log as", default, "Text files (*.txt);;All files (*)")
        if not chosen:
            return None
        with open(chosen, "w", encoding="utf-8") as fh:
            fh.write(self.text())
        logging.getLogger(ROOT_LOGGER).info("Log saved to %s", chosen)
        return chosen

    def _visible(self, level: str) -> bool:
        return self.debug_visible or level.upper() != "DEBUG"

    def _append_html(self, stamp: str, level: str, text: str) -> None:
        colour = theme_module.log_colour(self._theme, level)
        muted = theme_module.colours(self._theme)["muted"]
        prefix = "" if level.upper() in ("INFO", "DEBUG") else f"{level.upper()}: "
        body = html.escape(f"{prefix}{text}").replace("\n", "<br>&nbsp;&nbsp;&nbsp;&nbsp;")
        self.view.appendHtml(f'<span style="color:{muted}">{stamp}</span>&nbsp;&nbsp;<span style="color:{colour}">{body}</span>')
        if self._autoscroll:
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _rerender(self) -> None:
        self.view.clear()
        for stamp, level, text in list(self._records):
            if self._visible(level):
                self._append_html(stamp, level, text)

    def _scrolled(self, value: int) -> None:
        bar = self.view.verticalScrollBar()
        self._autoscroll = value >= bar.maximum() - 2
