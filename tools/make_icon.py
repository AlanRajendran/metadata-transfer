# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw the app icon (developer tool): source/metadata_transfer/assets/icon.png and icon.ico.

Style of the BIOMIS team's apps: a dark navy rounded square with bright fluorescent shapes. Here:
three stacked image planes (teal, periwinkle, coral) and a two-way arrow, for "convert between
formats with the metadata".

    python tools/make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(APP, "source", "metadata_transfer", "assets")


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 256.0
    bg = QLinearGradient(0, 0, 0, size)
    bg.setColorAt(0, QColor("#1c2a40"))
    bg.setColorAt(1, QColor("#121b2b"))
    p.setBrush(QBrush(bg))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 52 * s, 52 * s)
    # three stacked planes, back to front
    for i, colour in enumerate(("#8ea2ff", "#3fd0c9", "#ff7f73")):
        x0, y0 = (46 + 26 * i) * s, (40 + 26 * i) * s
        path = QPainterPath()
        path.addRoundedRect(QRectF(x0, y0, 108 * s, 108 * s), 18 * s, 18 * s)
        fill = QColor(colour)
        fill.setAlpha(255)
        p.setBrush(fill)
        p.setPen(QPen(QColor("#121b2b"), 6 * s))
        p.drawPath(path)
        p.setBrush(QColor("#ffffff"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(x0 + 54 * s, y0 + 54 * s), 9 * s, 9 * s)
    # two-way arrow along the bottom
    pen = QPen(QColor("#f4f1e8"), 12 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    y = 214 * s
    p.drawLine(QPointF(64 * s, y), QPointF(192 * s, y))
    for tip, d in ((64, 1), (192, -1)):
        p.drawLine(QPointF(tip * s, y), QPointF((tip + 18 * d) * s, y - 16 * s))
        p.drawLine(QPointF(tip * s, y), QPointF((tip + 18 * d) * s, y + 16 * s))
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main() -> int:
    QGuiApplication(sys.argv)
    os.makedirs(ASSETS, exist_ok=True)
    big = draw(256)
    big.save(os.path.join(ASSETS, "icon.png"))
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images = [png_bytes(draw(n)) for n in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for n, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(os.path.join(ASSETS, "icon.ico"), "wb") as fh:
        fh.write(header + entries + b"".join(images))
    print("icon written to", ASSETS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
