# SPDX-License-Identifier: GPL-3.0-or-later
"""Centre page: metadata of the selected series, channel-name editor, stage positions."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import presets
from ..mapping.channel_naming import default_name
from ..mapping.colors import rgb_to_hex
from ..model import SeriesMetadata
from .sections import CollapsibleSection


def _v(value, spec="", unit="") -> str:
    if value is None or value == "":
        return "—"
    if spec and isinstance(value, (int, float)):
        return f"{value:{spec}}{unit}"
    return f"{value}{unit}"


def _table(columns: tuple[str, ...]) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for i in range(1, len(columns)):
        t.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
    t.setAlternatingRowColors(True)
    return t


class MetadataPanel(QWidget):
    names_changed = Signal(list)
    apply_to_same = Signal()
    level_changed = Signal(int)

    COLS = ("Channel name (editable)", "Excitation", "Emission / filter", "Detector · exposure", "Colour")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._meta: SeriesMetadata | None = None
        self._updating = False

        self.header = QLabel("Select a series to see its metadata")
        self.header.setProperty("role", "title")
        self.header.setWordWrap(True)
        self.subheader = QLabel("")
        self.subheader.setProperty("role", "subtitle")
        self.subheader.setWordWrap(True)
        self.target = QLabel("")
        self.target.setProperty("role", "heading")

        # ---- image
        image = QWidget()
        self.form = QFormLayout(image)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.form.setContentsMargins(4, 8, 4, 8)
        self.fields: dict[str, QLabel] = {}
        for key in ("Dimensions", "Bit depth", "Pixel size", "Z step", "Time step", "Positions", "Objective",
                    "Zoom / pinhole", "Microscope", "Acquired"):
            lab = QLabel("—")
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lab.setWordWrap(True)
            name = QLabel(key)
            name.setProperty("role", "muted")
            self.fields[key] = lab
            self.form.addRow(name, lab)
        self.cmb_level = QComboBox()
        self.cmb_level.setToolTip("Stitched overview images are stored as a pyramid. Choose which resolution to convert.")
        self.cmb_level.currentIndexChanged.connect(self._on_level)
        self.lbl_level = QLabel("Resolution")
        self.lbl_level.setProperty("role", "muted")
        self.form.addRow(self.lbl_level, self.cmb_level)
        self.lbl_level.setVisible(False)
        self.cmb_level.setVisible(False)
        self.image_section = CollapsibleSection("Image", image)

        # ---- channels
        chan = QWidget()
        cl = QVBoxLayout(chan)
        cl.setContentsMargins(0, 8, 0, 8)
        self.table = _table(self.COLS)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setMinimumHeight(150)
        cl.addWidget(self.table)
        row1 = QHBoxLayout()
        self.btn_reset = QPushButton("Reset names")
        self.btn_reset.setToolTip("Restore the names from the source file")
        self.btn_reset.clicked.connect(self._reset_names)
        self.btn_same = QPushButton("Apply names to series with the same settings")
        self.btn_same.setToolTip("Copy these names to every queued series whose channels use the same settings")
        self.btn_same.clicked.connect(self.apply_to_same.emit)
        row1.addWidget(self.btn_reset)
        row1.addWidget(self.btn_same)
        row1.addStretch()
        cl.addLayout(row1)
        row2 = QHBoxLayout()
        preset_label = QLabel("Preset")
        preset_label.setProperty("role", "muted")
        row2.addWidget(preset_label)
        self.cmb_preset = QComboBox()
        self.cmb_preset.setMinimumWidth(160)
        self.btn_apply_preset = QPushButton("Apply")
        self.btn_apply_preset.clicked.connect(self._apply_preset)
        self.btn_save_preset = QPushButton("Save as preset…")
        self.btn_save_preset.clicked.connect(self._save_preset)
        self.btn_del_preset = QPushButton("Delete")
        self.btn_del_preset.clicked.connect(self._delete_preset)
        for w in (self.cmb_preset, self.btn_apply_preset, self.btn_save_preset, self.btn_del_preset):
            row2.addWidget(w)
        row2.addStretch()
        cl.addLayout(row2)
        self.channel_section = CollapsibleSection("Channels", chan)

        # ---- positions
        pos = QWidget()
        pl = QVBoxLayout(pos)
        pl.setContentsMargins(0, 8, 0, 8)
        self.positions = _table(("Position", "X (µm)", "Y (µm)", "Z (µm)"))
        self.positions.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.positions.setMinimumHeight(120)
        pl.addWidget(self.positions)
        self.position_section = CollapsibleSection("Stage positions", pos)

        # ---- notes
        self.warning = QLabel("")
        self.warning.setProperty("role", "warning")
        self.warning.setWordWrap(True)
        self.settings_text = QLabel("")
        self.settings_text.setProperty("role", "muted")
        self.settings_text.setWordWrap(True)
        self.settings_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        notes = QWidget()
        nl = QVBoxLayout(notes)
        nl.setContentsMargins(4, 8, 4, 8)
        nl.addWidget(self.warning)
        nl.addWidget(self.settings_text)
        self.notes_section = CollapsibleSection("Acquisition settings and notes", notes, expanded=False)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(18, 14, 18, 12)
        lay.setSpacing(6)
        lay.addWidget(self.header)
        lay.addWidget(self.subheader)
        lay.addWidget(self.target)
        lay.addSpacing(6)
        for sec in (self.image_section, self.channel_section, self.position_section, self.notes_section):
            lay.addWidget(sec)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._refresh_presets()
        self.set_metadata(None)

    # ------------------------------------------------------------------ display
    def set_metadata(self, meta: SeriesMetadata | None, message: str = "", target: str = "") -> None:
        self._meta = meta
        enabled = meta is not None
        for w in (self.table, self.btn_reset, self.btn_same, self.cmb_preset, self.btn_apply_preset,
                  self.btn_save_preset, self.btn_del_preset):
            w.setEnabled(enabled)
        self._updating = True
        self.table.setRowCount(0)
        self.positions.setRowCount(0)
        self._updating = False
        self.target.setText(f"Converts to {target}" if (meta is not None and target) else "")
        if meta is None:
            self.header.setText("Select a series to see its metadata" if not message else "Not convertible")
            self.subheader.setText(message)
            for lab in self.fields.values():
                lab.setText("—")
            self.warning.setText("")
            self.settings_text.setText("")
            self.position_section.setVisible(False)
            return

        d, cal, ob, acq = meta.output_dims, meta.output_calibration, meta.objective, meta.acquisition
        self.header.setText(meta.series_name)
        self.subheader.setText(f"{os.path.basename(meta.source_path)} · {meta.source_format}")
        dims = f"X {d.x}  ·  Y {d.y}  ·  Z {d.z}  ·  C {d.c}  ·  T {d.t}" + (f"  ·  P {d.p}" if d.p > 1 else "")
        if d.x * d.y >= 100e6:
            dims += f"   ({d.x * d.y / 1e6:,.0f} Mpx per plane)"
        self.fields["Dimensions"].setText(dims)
        self.fields["Bit depth"].setText(f"{meta.bits}-bit ({meta.dtype})")
        self.fields["Pixel size"].setText(f"{_v(cal.pixel_size_x_um, '.5g')} × {_v(cal.pixel_size_y_um, '.5g')} µm")
        self.fields["Z step"].setText(_v(cal.z_step_um, ".4g", " µm") if d.z > 1 else "— (single plane)")
        self.fields["Time step"].setText(_v(cal.time_step_s, ".4g", " s") if d.t > 1 else "— (single time point)")
        self.fields["Positions"].setText(f"{d.p} stage positions" if d.p > 1 else "one")
        self._updating = True
        self.cmb_level.clear()
        has_levels = len(meta.pyramid_levels) > 1
        if has_levels:
            for k, lv in enumerate(meta.pyramid_levels):
                label = "Full resolution" if k == 0 else f"1/{2 ** k}"
                self.cmb_level.addItem(f"{label}   ({lv.x} × {lv.y} px)", k)
            self.cmb_level.setCurrentIndex(meta.level)
        self.lbl_level.setVisible(has_levels)
        self.cmb_level.setVisible(has_levels)
        self._updating = False
        obj = ob.name or "—"
        if ob.numerical_aperture:
            obj += f"   (NA {ob.numerical_aperture:g}, RI {_v(ob.refractive_index, 'g')}{', ' + ob.immersion if ob.immersion else ''})"
        self.fields["Objective"].setText(obj)
        pinhole = meta.pinhole_um or next((c.pinhole_um for c in meta.channels if c.pinhole_um), None)
        self.fields["Zoom / pinhole"].setText(f"{_v(meta.zoom, '.2f')}  /  {_v(pinhole, '.1f', ' µm')}")
        self.fields["Microscope"].setText(" · ".join(x for x in (acq.microscope, acq.software) if x) or "—")
        self.fields["Acquired"].setText(f"{acq.start:%d/%m/%Y %H:%M:%S}" if acq.start else "—")

        self._updating = True
        self.table.setRowCount(len(meta.channels))
        for r, ch in enumerate(meta.channels):
            name = QTableWidgetItem(ch.name)
            tip = f"Settings: {ch.auto_name}" if ch.auto_name else ""
            if ch.dye_name:
                tip += f"\nDye label: {ch.dye_name}"
            if ch.vendor_name:
                tip += f"\nName in the acquisition software: {ch.vendor_name}"
            name.setToolTip(tip.strip())
            self.table.setItem(r, 0, name)
            ex = f"{ch.excitation_nm:.0f} nm" if ch.excitation_nm else "—"
            if ch.laser_intensity_pct is not None:
                ex += f"  ({ch.laser_intensity_pct:.0f} %)"
            if ch.emission_range_nm:
                lo, hi = ch.emission_range_nm
                em = f"{lo:.0f}–{hi:.0f} nm" if hi > lo else f"{lo:.0f} nm"
                if ch.filter_name:
                    em = f"{em}  ·  {ch.filter_name}"
            else:
                em = "transmitted light" if ch.modality == "brightfield" else "—"
            det = ch.detector or "—"
            if ch.detector_gain is not None:
                det += f"  (gain {ch.detector_gain:g})"
            if ch.exposure_ms is not None:
                det += f"  ·  {ch.exposure_ms:g} ms"
            for c, text in enumerate((ex, em, det), start=1):
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(r, c, it)
            col = QTableWidgetItem(ch.color_name or rgb_to_hex(ch.color_rgb))
            col.setFlags(col.flags() & ~Qt.ItemFlag.ItemIsEditable)
            col.setBackground(QColor(*ch.color_rgb))
            col.setForeground(QColor("black") if sum(ch.color_rgb) > 300 else QColor("white"))
            self.table.setItem(r, 4, col)
        self._updating = False

        self.position_section.setVisible(d.p > 1)
        if d.p > 1:
            self.positions.setRowCount(len(meta.positions))
            for r, pos in enumerate(meta.positions):
                vals = (pos.name or f"{r + 1}", _v(pos.x_um, ".1f"), _v(pos.y_um, ".1f"), _v(pos.z_um, ".2f"))
                for c, text in enumerate(vals):
                    self.positions.setItem(r, c, QTableWidgetItem(text))
        self.warning.setText("\n".join("⚠ " + w for w in meta.warnings))
        self.settings_text.setText("\n".join(f"{k}: {v}" for k, v in meta.scan_settings.items()))

    def current_names(self) -> list[str]:
        return [self.table.item(r, 0).text().strip() for r in range(self.table.rowCount())]

    # ----------------------------------------------------------------- editing
    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() != 0 or self._meta is None:
            return
        self.names_changed.emit(self.current_names())

    def _set_names(self, names: list[str]) -> None:
        self._updating = True
        for r, n in enumerate(names):
            self.table.item(r, 0).setText(n)
        self._updating = False
        self.names_changed.emit(self.current_names())

    def _reset_names(self) -> None:
        if self._meta is not None:
            self._set_names([default_name(ch) if (ch.detector or ch.dye_name or ch.vendor_name) else ch.name
                             for ch in self._meta.channels])

    def _on_level(self, index: int) -> None:
        if self._updating or self._meta is None or index < 0:
            return
        level = self.cmb_level.itemData(index)
        if isinstance(level, int) and level != self._meta.level:
            self.level_changed.emit(level)

    # ----------------------------------------------------------------- presets
    def _refresh_presets(self) -> None:
        cur = self.cmb_preset.currentText()
        self.cmb_preset.clear()
        self.cmb_preset.addItems(presets.preset_names())
        if cur:
            self.cmb_preset.setCurrentText(cur)

    def _apply_preset(self) -> None:
        name = self.cmb_preset.currentText()
        if self._meta is None or not name:
            return
        self._set_names(presets.names_from_preset(name, self._meta.channels))

    def _save_preset(self) -> None:
        if self._meta is None:
            return
        name, ok = QInputDialog.getText(self, "Save channel-name preset", "Preset name (for example 'Tight junction panel'):")
        if not ok or not name.strip():
            return
        for ch, n in zip(self._meta.channels, self.current_names()):
            ch.name = n
        presets.save_preset(name.strip(), self._meta.channels)
        self._refresh_presets()
        self.cmb_preset.setCurrentText(name.strip())

    def _delete_preset(self) -> None:
        name = self.cmb_preset.currentText()
        if name and QMessageBox.question(self, "Delete preset", f"Delete the preset '{name}'?") == QMessageBox.StandardButton.Yes:
            presets.delete_preset(name)
            self._refresh_presets()
