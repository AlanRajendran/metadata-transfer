# SPDX-License-Identifier: GPL-3.0-or-later AND MIT
"""Themes: Terracotta (default), Dark, Light, and Follow Windows (Dark or Light like Windows).

The palette tokens, style sheet and role properties are the design language of the BIOMIS team's
Timelapse Video Processing app (MIT licence, same authors), so both apps look alike. Widgets opt
into styles with ``setProperty("role", ...)``: title, subtitle, heading, muted, primary, link,
section, warning, error, swatch. Colour previews of microscope data stay on a near-black canvas in
every theme (``VIEWER_BACKGROUND``).
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette

log = logging.getLogger(__name__)

THEMES: tuple[str, ...] = ("terracotta", "dark", "light")
MODES: tuple[str, ...] = ("terracotta", "dark", "light", "system")
MODE_LABELS = {"terracotta": "Terracotta", "dark": "Dark", "light": "Light", "system": "Follow Windows"}
DEFAULT_THEME = "terracotta"

VIEWER_BACKGROUND = "#0e0e10"

_TERRACOTTA = {
    "window": "#262624", "base": "#1f1e1d", "alt_base": "#30302e", "text": "#f0eee6", "muted": "#a6a39a",
    "button": "#30302e", "border": "#43423e", "accent": "#d97757", "accent_hover": "#e58b6d",
    "accent_text": "#ffffff", "tooltip_bg": "#30302e", "disabled": "#6e6b64",
    "ok": "#8fc27a", "warning": "#e3a14a", "error": "#ff7b6b",
}
_DARK = {
    "window": "#26272b", "base": "#1e1f22", "alt_base": "#2d2e33", "text": "#e6e6e6", "muted": "#9aa0a6",
    "button": "#2d2e33", "border": "#3d3f46", "accent": "#4c8dff", "accent_hover": "#6f9dff",
    "accent_text": "#101318", "tooltip_bg": "#2d2e33", "disabled": "#6b6f76",
    "ok": "#7cc47f", "warning": "#e0a83c", "error": "#ff6b6b",
}
_LIGHT = {
    "window": "#f3f3f4", "base": "#ffffff", "alt_base": "#ececee", "text": "#1c1d20", "muted": "#5f6368",
    "button": "#e7e7ea", "border": "#c4c5ca", "accent": "#1a6fe0", "accent_hover": "#1557b0",
    "accent_text": "#ffffff", "tooltip_bg": "#ffffff", "disabled": "#9aa0a6",
    "ok": "#2e7d32", "warning": "#a86a00", "error": "#c62828",
}
THEME_COLOURS = {"terracotta": _TERRACOTTA, "dark": _DARK, "light": _LIGHT}

LOG_COLOURS = {
    "terracotta": {"DEBUG": "#8f8b80", "INFO": "#e8e4da", "WARNING": "#e3a14a", "ERROR": "#ff7b6b", "CRITICAL": "#ff7b6b"},
    "dark": {"DEBUG": "#8c9199", "INFO": "#d7d7d7", "WARNING": "#e0a83c", "ERROR": "#ff6b6b", "CRITICAL": "#ff6b6b"},
    "light": {"DEBUG": "#6b7076", "INFO": "#1c1d20", "WARNING": "#a86a00", "ERROR": "#c62828", "CRITICAL": "#c62828"},
}


def resolve(mode: str) -> str:
    """The theme a mode shows: Follow Windows gives Dark or Light like Windows."""
    if mode in THEMES:
        return mode
    if mode == "system":
        try:
            from PySide6.QtGui import QGuiApplication

            hints = QGuiApplication.styleHints()
            if hints is not None and hints.colorScheme() == Qt.ColorScheme.Light:
                return "light"
            return "dark"
        except Exception:  # pragma: no cover
            return "dark"
    return DEFAULT_THEME


def colours(theme: str) -> dict[str, str]:
    return dict(THEME_COLOURS.get(theme, THEME_COLOURS[DEFAULT_THEME]))


def log_colour(theme: str, level: str) -> str:
    table = LOG_COLOURS.get(theme, LOG_COLOURS[DEFAULT_THEME])
    return table.get(level.upper(), table["INFO"])


def _palette(c: dict[str, str]) -> QPalette:
    p = QPalette()
    window, base, text = QColor(c["window"]), QColor(c["base"]), QColor(c["text"])
    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(c["alt_base"]))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["tooltip_bg"]))
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, QColor(c["button"]))
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor("#ff5252"))
    p.setColor(QPalette.ColorRole.Link, QColor(c["accent"]))
    p.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(c["accent_text"]))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["muted"]))
    p.setColor(QPalette.ColorRole.Mid, QColor(c["border"]))
    p.setColor(QPalette.ColorRole.Midlight, QColor(c["alt_base"]))
    p.setColor(QPalette.ColorRole.Dark, QColor(c["border"]).darker(130))
    p.setColor(QPalette.ColorRole.Light, QColor(c["base"]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(c["disabled"]))
    return p


def stylesheet(theme: str) -> str:
    c = colours(theme)
    return f"""
    QMainWindow::separator {{ background: {c['border']}; width: 1px; height: 1px; }}
    QToolBar {{ background: {c['window']}; border-bottom: 1px solid {c['border']}; padding: 4px 6px; spacing: 6px; }}
    QToolBar QToolButton {{ border: 1px solid transparent; border-radius: 7px; padding: 4px 9px; }}
    QToolBar QToolButton:hover {{ border-color: {c['border']}; background: {c['alt_base']}; }}
    QToolBar QToolButton:checked {{ border-color: {c['accent']}; background: {c['alt_base']}; color: {c['text']}; }}
    QToolBar QToolButton:disabled {{ color: {c['disabled']}; }}
    QDockWidget {{ titlebar-close-icon: none; }}
    QDockWidget::title {{ background: {c['alt_base']}; padding: 5px 8px; border-bottom: 1px solid {c['border']};
                          color: {c['muted']}; }}
    QTabBar::tab {{ background: {c['window']}; border: 1px solid {c['border']}; border-bottom: none; padding: 4px 10px; }}
    QTabBar::tab:selected {{ background: {c['alt_base']}; color: {c['text']}; }}
    QGroupBox {{ border: 1px solid {c['border']}; border-radius: 6px; margin-top: 10px; padding-top: 6px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {c['muted']}; }}
    QStatusBar {{ border-top: 1px solid {c['border']}; }}
    QStatusBar QLabel {{ color: {c['muted']}; }}
    QProgressBar {{ border: 1px solid {c['border']}; border-radius: 5px; text-align: center; max-height: 14px; }}
    QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
    QPlainTextEdit, QTextEdit, QListWidget, QTreeWidget, QTreeView, QListView, QTableWidget, QTableView {{
        background: {c['base']}; border: 1px solid {c['border']}; border-radius: 5px; }}
    QHeaderView::section {{ background: {c['alt_base']}; color: {c['muted']}; border: none;
        border-bottom: 1px solid {c['border']}; padding: 4px 6px; }}
    QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
        background: {c['accent']}; color: {c['accent_text']}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {c['base']}; border: 1px solid {c['border']};
        border-radius: 5px; padding: 2px 5px; min-height: 20px; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {c['accent']}; }}
    QPushButton {{ background: {c['button']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 4px 12px; }}
    QPushButton:hover {{ border-color: {c['accent']}; }}
    QPushButton:pressed {{ background: {c['alt_base']}; }}
    QPushButton:disabled {{ color: {c['disabled']}; }}
    QPushButton[role="primary"] {{ background: {c['accent']}; color: {c['accent_text']}; border: 1px solid {c['accent']};
        font-weight: 600; padding: 8px 20px; border-radius: 8px; }}
    QPushButton[role="primary"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
    QPushButton[role="primary"]:disabled {{ background: {c['alt_base']}; border-color: {c['border']}; color: {c['disabled']}; }}
    QPushButton[role="link"] {{ background: transparent; border: none; color: {c['text']}; text-align: left; padding: 4px 2px; }}
    QPushButton[role="link"]:hover {{ color: {c['accent']}; }}
    QToolButton[role="section"] {{ border: none; border-bottom: 1px solid {c['border']}; background: transparent;
        padding: 7px 4px; font-weight: 600; color: {c['text']}; text-align: left; }}
    QToolButton[role="section"]:hover {{ color: {c['accent']}; }}
    QToolButton[role="chip"] {{ padding: 3px 10px; border: 1px solid {c['border']}; border-radius: 10px; }}
    QToolButton[role="chip"]:checked {{ background: {c['accent']}; border-color: {c['accent']}; color: {c['accent_text']}; }}
    QToolButton[role="chip"]:hover:!checked {{ border-color: {c['accent']}; }}
    QLabel[role="title"] {{ font-size: 18pt; font-weight: 700; color: {c['text']}; }}
    QLabel[role="subtitle"] {{ font-size: 11pt; color: {c['muted']}; }}
    QLabel[role="heading"] {{ color: {c['muted']}; font-weight: 600; letter-spacing: 0.5px; }}
    QLabel[role="muted"] {{ color: {c['muted']}; }}
    QLabel[role="warning"] {{ color: {c['warning']}; }}
    QLabel[role="error"] {{ color: {c['error']}; }}
    QLabel[role="value"] {{ color: {c['text']}; }}
    QFrame[role="card"] {{ background: {c['base']}; border: 1px solid {c['border']}; border-radius: 8px; }}
    QLabel[role="viewer"] {{ background: {VIEWER_BACKGROUND}; border: 1px solid {c['border']}; border-radius: 6px;
        color: {c['muted']}; }}
    """


def apply_theme(app, mode: str = DEFAULT_THEME) -> str:
    """Apply the Fusion style with the palette of `mode`. Returns the theme actually shown."""
    hints = app.styleHints()
    try:
        if mode == "system":
            hints.unsetColorScheme()  # read what Windows uses
    except Exception:  # pragma: no cover - Qt < 6.8
        pass
    name = resolve(mode)
    try:
        app.setStyle("Fusion")
    except Exception:  # pragma: no cover - platform dependent
        log.debug("Fusion style is not available; the default style is kept.")
    try:
        if mode != "system":  # native dialogs and title bars follow the chosen theme
            hints.setColorScheme(Qt.ColorScheme.Light if name == "light" else Qt.ColorScheme.Dark)
    except Exception:  # pragma: no cover - Qt < 6.8
        pass
    app.setPalette(_palette(colours(name)))
    app.setStyleSheet(stylesheet(name))
    app.setProperty("themeMode", mode)
    app.setProperty("theme", name)
    return name


def current_theme(app) -> str:
    name = app.property("theme") if app is not None else None
    return name if name in THEMES else DEFAULT_THEME
