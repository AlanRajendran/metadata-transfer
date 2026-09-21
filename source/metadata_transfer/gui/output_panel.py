# SPDX-License-Identifier: GPL-3.0-or-later
"""Right-hand panel: where files go, the target format for Leica files, options, Convert."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import presets
from ..convert import ConvertOptions
from ..readers.evident_vsi import groups
from .sections import CollapsibleSection


class OutputPanel(QWidget):
    convert_requested = Signal()
    options_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        s = presets.load()

        # ---- target format
        fmt = QWidget()
        fl = QVBoxLayout(fmt)
        fl.setContentsMargins(4, 8, 4, 8)
        fl.setSpacing(6)
        lab = QLabel("Leica LIF files convert to")
        lab.setProperty("role", "muted")
        fl.addWidget(lab)
        chips = QHBoxLayout()
        self.chip_nd2 = QToolButton()
        self.chip_nd2.setText("Nikon ND2")
        self.chip_vsi = QToolButton()
        self.chip_vsi.setText("Evident VSI")
        self._chips = QButtonGroup(self)
        self._chips.setExclusive(True)
        for chip in (self.chip_nd2, self.chip_vsi):
            chip.setCheckable(True)
            chip.setProperty("role", "chip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            self._chips.addButton(chip)
            chips.addWidget(chip)
        chips.addStretch(1)
        (self.chip_vsi if s.get("lif_target") == "vsi" else self.chip_nd2).setChecked(True)
        self._chips.buttonToggled.connect(lambda *_: self._changed())
        fl.addLayout(chips)
        fixed = QLabel("Evident VSI → Nikon ND2\nNikon ND2 → Evident VSI")
        fixed.setProperty("role", "muted")
        fl.addWidget(fixed)
        self.format_section = CollapsibleSection("Target format", fmt)

        # ---- destination
        dest = QWidget()
        dl = QVBoxLayout(dest)
        dl.setContentsMargins(4, 8, 4, 8)
        dl.setSpacing(6)
        self.rb_next = QRadioButton("Next to each source file")
        self.rb_folder = QRadioButton("In this folder:")
        row = QHBoxLayout()
        self.ed_folder = QLineEdit(s.get("output_dir", ""))
        self.ed_folder.setPlaceholderText("Choose a folder…")
        self.btn_folder = QPushButton("Browse…")
        self.btn_folder.clicked.connect(self._browse)
        row.addWidget(self.ed_folder, 1)
        row.addWidget(self.btn_folder)
        (self.rb_folder if s.get("output_mode") == "folder" else self.rb_next).setChecked(True)
        for w in (self.rb_next, self.rb_folder):
            w.toggled.connect(lambda *_: self._changed())
        self.ed_folder.editingFinished.connect(self._changed)
        dl.addWidget(self.rb_next)
        dl.addWidget(self.rb_folder)
        dl.addLayout(row)
        self.dest_section = CollapsibleSection("Save to", dest)

        # ---- options
        opts = QWidget()
        ol = QVBoxLayout(opts)
        ol.setContentsMargins(4, 8, 4, 8)
        ol.setSpacing(4)
        self.cb_group = QCheckBox("Combine numbered cellSens position files")
        self.cb_group.setToolTip("name_01.vsi, name_02.vsi, … taken at different stage positions become one ND2 "
                                 "with an XY loop. Untick to convert each file on its own.")
        self.cb_overwrite = QCheckBox("Overwrite existing files")
        self.cb_overwrite.setToolTip("Otherwise a number is added: name (2).nd2")
        self.cb_sidecar = QCheckBox("Save all original metadata (.metadata.json)")
        self.cb_text = QCheckBox("Write acquisition settings into the ND2 description")
        self.cb_report = QCheckBox("Save a report per file (.report.txt)")
        self.cb_group.setChecked(s.get("group_positions", True))
        self.cb_overwrite.setChecked(s.get("overwrite", False))
        self.cb_sidecar.setChecked(s.get("sidecar", True))
        self.cb_text.setChecked(s.get("text_info", True))
        self.cb_report.setChecked(s.get("report", True))
        for cb in (self.cb_group, self.cb_overwrite, self.cb_sidecar, self.cb_text, self.cb_report):
            cb.toggled.connect(lambda *_: self._changed())
            ol.addWidget(cb)
        self.options_section = CollapsibleSection("Options", opts)

        # ---- convert
        self.summary = QLabel("Nothing selected")
        self.summary.setProperty("role", "muted")
        self.summary.setWordWrap(True)
        self.btn_convert = QPushButton("Convert")
        self.btn_convert.setProperty("role", "primary")
        self.btn_convert.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_convert.clicked.connect(self.convert_requested.emit)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(8)
        for sec in (self.format_section, self.dest_section, self.options_section):
            lay.addWidget(sec)
        lay.addStretch(1)
        lay.addWidget(self.summary)
        lay.addWidget(self.btn_convert)
        groups.ENABLED = self.cb_group.isChecked()

    # ------------------------------------------------------------------
    @property
    def lif_target(self) -> str:
        return "vsi" if self.chip_vsi.isChecked() else "nd2"

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.ed_folder.text())
        if d:
            self.ed_folder.setText(d)
            self.rb_folder.setChecked(True)
            self._changed()

    def _changed(self) -> None:
        groups.ENABLED = self.cb_group.isChecked()
        data = presets.load()
        data.update(
            lif_target=self.lif_target,
            output_dir=self.ed_folder.text().strip(),
            output_mode="folder" if self.rb_folder.isChecked() else "next",
            group_positions=self.cb_group.isChecked(),
            overwrite=self.cb_overwrite.isChecked(),
            sidecar=self.cb_sidecar.isChecked(),
            text_info=self.cb_text.isChecked(),
            report=self.cb_report.isChecked(),
        )
        presets.save(data)
        self.options_changed.emit()

    def output_folder(self) -> str | None:
        """The chosen folder, None for 'next to the source', or '' when a folder is required but empty."""
        if self.rb_folder.isChecked():
            return self.ed_folder.text().strip()
        return None

    def options(self) -> ConvertOptions:
        folder = self.output_folder()
        if folder:
            os.makedirs(folder, exist_ok=True)
        return ConvertOptions(
            output_dir=folder or None,
            overwrite=self.cb_overwrite.isChecked(),
            write_sidecar=self.cb_sidecar.isChecked(),
            embed_text_info=self.cb_text.isChecked(),
            write_report=self.cb_report.isChecked(),
        )

    def set_running(self, running: bool) -> None:
        for w in (self.chip_nd2, self.chip_vsi, self.rb_next, self.rb_folder, self.ed_folder, self.btn_folder,
                  self.cb_group, self.cb_overwrite, self.cb_sidecar, self.cb_text, self.cb_report, self.btn_convert):
            w.setEnabled(not running)
