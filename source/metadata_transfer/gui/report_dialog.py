# SPDX-License-Identifier: GPL-3.0-or-later
"""End-of-run summary with the report of every file and the series that were not converted."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
)

from ..convert import JobResult
from . import theme


class ReportDialog(QDialog):
    def __init__(self, results: list[JobResult], skipped: list[tuple[str, str, str]] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Conversion report")
        self.resize(1000, 640)
        skipped = skipped or []
        c = theme.colours(theme.current_theme(QApplication.instance()))
        ok = sum(r.ok for r in results)
        failed = sum((not r.ok and not r.cancelled) for r in results)
        cancelled = sum(r.cancelled for r in results)
        files = sum(len(r.outputs) for r in results if r.ok)
        summary = QLabel(
            f"{ok} converted"
            + (f" ({files} files)" if files != ok else "")
            + (f", {failed} failed" if failed else "")
            + (f", {cancelled} cancelled" if cancelled else "")
            + (f", {len(skipped)} not convertible" if skipped else "")
        )
        summary.setProperty("role", "title")
        sub = QLabel("Every file written was read back and compared with the source. ✓ checked, "
                     "! kept elsewhere (description or .metadata.json), ✗ differs.")
        sub.setProperty("role", "muted")
        sub.setWordWrap(True)

        self.list = QListWidget()
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 9))
        for r in results:
            sym = "✓" if r.ok else ("—" if r.cancelled else "✗")
            target = f"  → {r.output_format.split()[-1]}" if r.output_format else ""
            it = QListWidgetItem(f"{sym}  {os.path.basename(r.source)} · {r.series_name}{target}")
            it.setData(Qt.ItemDataRole.UserRole, r)
            if not r.ok and not r.cancelled:
                it.setForeground(QColor(c["error"]))
            self.list.addItem(it)
        for file, name, reason in skipped:
            it = QListWidgetItem(f"○  {file} · {name}")
            it.setData(Qt.ItemDataRole.UserRole, f"NOT CONVERTED\n\nFile:   {file}\nSeries: {name}\n\nReason: {reason}\n")
            it.setToolTip(reason)
            it.setForeground(QColor(c["muted"]))
            self.list.addItem(it)
        self.list.currentItemChanged.connect(self._show)

        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(self.text)
        split.setSizes([320, 680])

        self.btn_open = QPushButton("Open output folder")
        self.btn_open.clicked.connect(self._open_folder)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.btn_open)
        bottom.addStretch()
        bottom.addWidget(buttons)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 12)
        lay.addWidget(summary)
        lay.addWidget(sub)
        lay.addWidget(split, 1)
        lay.addLayout(bottom)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _current(self):
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _show(self, *_):
        r = self._current()
        if isinstance(r, JobResult):
            self.text.setPlainText(r.report_text)
            self.btn_open.setEnabled(bool(r.output_path))
        else:
            self.text.setPlainText(r or "")
            self.btn_open.setEnabled(False)

    def _open_folder(self) -> None:
        r = self._current()
        if isinstance(r, JobResult) and r.output_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(r.output_path)))
