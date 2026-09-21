# SPDX-License-Identifier: GPL-3.0-or-later AND MIT
"""Welcome page shown while the queue is empty (layout of the BIOMIS team's Timelapse Video
Processing app): add files, drop files, what converts to what, recent files."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from .. import APP_NAME, APP_SUBTITLE, CREDITS, VERSION

ROUTES = [
    ("Leica LIF", "Nikon ND2 or Evident VSI", "series → one file each; choose the target in Output"),
    ("Evident VSI", "Nikon ND2", "numbered position files (_01, _02, …) → one ND2 with an XY loop"),
    ("Nikon ND2", "Evident VSI", "multipoint → one .vsi per position, as cellSens stores them"),
]


class WelcomePage(QWidget):
    add_files_requested = Signal()
    add_folder_requested = Signal()
    recent_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QHBoxLayout(self)
        column = QVBoxLayout()
        column.setSpacing(10)
        column.addStretch(2)

        title = QLabel(APP_NAME)
        title.setProperty("role", "title")
        subtitle = QLabel(APP_SUBTITLE + ". Pixels are copied unchanged and every file written is checked.")
        subtitle.setProperty("role", "subtitle")
        subtitle.setWordWrap(True)
        column.addWidget(title)
        column.addWidget(subtitle)
        column.addSpacing(14)

        self.add_button = QPushButton("Add microscope files…")
        self.add_button.setProperty("role", "primary")
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.add_button.clicked.connect(self.add_files_requested.emit)
        column.addWidget(self.add_button)
        hint = QLabel("or drag .lif, .vsi or .nd2 files, or whole folders, onto this window. For .vsi the "
                      "folder “_<name>_” next to the file holds the pixels; it is found automatically.")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        column.addWidget(hint)
        self.folder_button = QPushButton("Add every file in a folder…")
        self.folder_button.setProperty("role", "link")
        self.folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_button.clicked.connect(self.add_folder_requested.emit)
        column.addWidget(self.folder_button)
        column.addSpacing(14)

        routes_heading = QLabel("What converts to what")
        routes_heading.setProperty("role", "heading")
        column.addWidget(routes_heading)
        rows = "".join(
            f"<tr><td style='padding:4px 10px 0 0'><b>{src}</b></td><td style='padding:4px 10px 0 0'>→</td>"
            f"<td style='padding:4px 0 0 0'><b>{dst}</b></td></tr>"
            f"<tr><td colspan='3' style='padding:0 0 6px 0'><small>{note}</small></td></tr>"
            for src, dst, note in ROUTES
        )
        card = QLabel(f"<table cellspacing='0'>{rows}</table>")
        card.setProperty("role", "value")
        card.setWordWrap(True)
        card.setStyleSheet("QLabel { border: 1px solid palette(mid); border-radius: 8px; padding: 8px 12px; "
                           "background: palette(base); }")
        column.addWidget(card)
        column.addSpacing(18)

        self.recent_heading = QLabel("Recent")
        self.recent_heading.setProperty("role", "heading")
        column.addWidget(self.recent_heading)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(2)
        column.addLayout(self.recent_box)
        column.addStretch(3)

        footer = QLabel(f"{CREDITS}  ·  version {VERSION}")
        footer.setProperty("role", "muted")
        footer.setWordWrap(True)
        column.addWidget(footer)

        holder = QWidget()
        holder.setLayout(column)
        holder.setMaximumWidth(660)
        holder.setMinimumWidth(380)
        page = QWidget()
        row = QHBoxLayout(page)
        row.addStretch(1)
        row.addWidget(holder, 3)
        row.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(page)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.recent_buttons: list[QPushButton] = []
        self.set_recent([])

    def set_recent(self, files: list[str]) -> None:
        for button in self.recent_buttons:
            button.deleteLater()
        self.recent_buttons.clear()
        shown = [f for f in files if os.path.exists(f)][:5]
        for path in shown:
            button = QPushButton(f"{os.path.basename(path)}    {os.path.dirname(path)}")
            button.setProperty("role", "link")
            button.setToolTip(path)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, p=path: self.recent_requested.emit(p))
            self.recent_box.addWidget(button)
            self.recent_buttons.append(button)
        self.recent_heading.setVisible(bool(shown))
