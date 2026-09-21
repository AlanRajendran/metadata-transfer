# SPDX-License-Identifier: GPL-3.0-or-later AND MIT
"""Line icons drawn from inline SVG in the theme's colours (same stroke style as the BIOMIS team's
Timelapse Video Processing app). Without Qt's SVG plugin the icons come back empty and the
buttons keep their text."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QImage, QPixmap

_STROKE = 'fill="none" stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'

SHAPES = {
    "add": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'
           '<path d="M12 11v6M9 14h6"/>',
    "folder": '<path d="M3 7.5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 10h18"/>',
    "convert": '<circle cx="12" cy="12" r="9"/><path d="M10 8.3v7.4l6-3.7z" fill="{c}"/>',
    "cancel": '<circle cx="12" cy="12" r="9"/><rect x="9" y="9" width="6" height="6" rx="1" fill="{c}"/>',
    "report": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "clear": '<path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13"/>',
    "settings": '<circle cx="12" cy="12" r="3.2"/><path d="M19.2 13.4a7.6 7.6 0 0 0 0-2.8l2-1.5-2-3.4-2.4 1a7.6 7.6 0 0 '
                '0-2.4-1.4L14 2.6h-4l-.4 2.7a7.6 7.6 0 0 0-2.4 1.4l-2.4-1-2 3.4 2 1.5a7.6 7.6 0 0 0 0 2.8l-2 1.5 2 3.4 '
                '2.4-1a7.6 7.6 0 0 0 2.4 1.4l.4 2.7h4l.4-2.7a7.6 7.6 0 0 0 2.4-1.4l2.4 1 2-3.4z"/>',
    "output": '<path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5"/>',
    "about": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5"/><circle cx="12" cy="7.8" r="0.9" fill="{c}"/>',
}


def svg(name: str, colour: str) -> bytes:
    body = SHAPES[name].replace("{c}", colour)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g {_STROKE.replace("{c}", colour)}>'
            f"{body}</g></svg>").encode("utf-8")


def icon(name: str, colour: str, size: int = 24) -> QIcon:
    """The named icon in ``colour``, rendered at twice ``size`` for sharp high-DPI display."""
    image = QImage()
    if not image.loadFromData(QByteArray(svg(name, colour)), "SVG"):
        return QIcon()
    pixel = size * 2
    pixmap = QPixmap.fromImage(image.scaled(pixel, pixel, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
    pixmap.setDevicePixelRatio(2.0)
    return QIcon(pixmap)
