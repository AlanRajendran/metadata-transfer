# SPDX-License-Identifier: GPL-3.0-or-later
"""Main window, in the layout of the BIOMIS team's Timelapse Video Processing app.

Toolbar on top; the queue on the left, the selected series' metadata in the centre (a welcome
page while the queue is empty), output settings and Convert on the right, the log at the bottom.
Side panels collapse to slim strips; the layout, theme and options are remembered.
"""

from __future__ import annotations

import base64
import logging
import os
import time

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, VERSION, presets
from ..convert import JobResult
from ..log import log_dir, log_file
from ..model import SeriesInfo, SeriesMetadata
from ..readers import file_dialog_filter, is_companion_dir, open_reader, reader_for, resolve_input
from ..writers.registry import FORMATS, resolve_format
from . import theme
from .app import app_icon
from .dialogs import AboutDialog
from .icons import icon
from .log_dock import LogDock
from .metadata_panel import MetadataPanel
from .output_panel import OutputPanel
from .panels import PanelManager
from .report_dialog import ReportDialog
from .welcome import WelcomePage
from .worker import ConvertWorker, Job

ROLE_KEY = Qt.ItemDataRole.UserRole + 1  # (path, index) for series items, path for file items
ROLE_RESULT = Qt.ItemDataRole.UserRole + 2
LAYOUT_VERSION = 1

log = logging.getLogger(__name__)


class QueueTree(QTreeWidget):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self._window = window
        self.setHeaderLabels(["Series", "Size", "To", "Status"])
        h = self.header()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2, 3):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DropOnly)
        self.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)

    def dragEnterEvent(self, e):  # noqa: N802 - Qt API
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):  # noqa: N802 - Qt API
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802 - Qt API
        self._window.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
        e.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("main_window")
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self.setDockNestingEnabled(True)
        self.resize(1400, 880)
        self._series: dict[tuple[str, int], SeriesInfo] = {}
        self._formats: dict[str, str] = {}  # source path -> reader format name
        self._meta: dict[tuple[tuple[str, int], int], SeriesMetadata] = {}
        self._names: dict[tuple[str, int], list[str]] = {}
        self._levels: dict[tuple[str, int], int] = {}
        self._items: dict[tuple[str, int], QTreeWidgetItem] = {}
        self._worker: ConvertWorker | None = None
        self._total_jobs = 0
        self._done_jobs = 0
        self._started = 0.0
        self._last_results: list[JobResult] = []

        # ---- centre
        self.welcome = WelcomePage(self)
        self.panel = MetadataPanel(self)
        self.central = QStackedWidget(self)
        self.central.addWidget(self.welcome)
        self.central.addWidget(self.panel)
        self.setCentralWidget(self.central)
        self.welcome.add_files_requested.connect(self._browse_files)
        self.welcome.add_folder_requested.connect(self._browse_input_folder)
        self.welcome.recent_requested.connect(lambda p: self.add_paths([p]))
        self.welcome.set_recent(list(presets.get("recent_files", [])))
        self.panel.names_changed.connect(self._on_names_changed)
        self.panel.apply_to_same.connect(self._apply_names_to_same)
        self.panel.level_changed.connect(self._on_level_changed)

        # ---- queue (left)
        self.tree = QueueTree(self)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        self.tree.itemChanged.connect(lambda *_: self._update_enabled())
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        queue = QWidget()
        ql = QVBoxLayout(queue)
        ql.setContentsMargins(8, 6, 8, 8)
        ql.setSpacing(6)
        row = QHBoxLayout()
        self.btn_add = QPushButton("Add files…")
        self.btn_add.clicked.connect(self._browse_files)
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.setToolTip("Remove the selected files, or untick the selected series")
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(self._clear)
        for b in (self.btn_add, self.btn_remove, self.btn_clear):
            row.addWidget(b)
        row.addStretch(1)
        ql.addLayout(row)
        ql.addWidget(self.tree, 1)
        hint = QLabel("Drop .lif, .vsi or .nd2 files or folders here.")
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        ql.addWidget(hint)
        self.queue_dock = self._dock("Queue", "queue_dock", queue, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.queue_dock.setMinimumWidth(380)

        # ---- output (right)
        self.output = OutputPanel(self)
        self.output.convert_requested.connect(self._start)
        self.output.options_changed.connect(self._on_options_changed)
        self.output_dock = self._dock("Output", "output_dock", self.output, Qt.DockWidgetArea.RightDockWidgetArea)
        self.output_dock.setMinimumWidth(300)

        # ---- log (bottom)
        self.log_dock = LogDock(self, debug=False)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.Corner.BottomRightCorner, Qt.DockWidgetArea.RightDockWidgetArea)

        self.panels = PanelManager(self)
        self.panels.add("queue", self.queue_dock, "left", "Queue", "Ctrl+1")
        self.panels.add("output", self.output_dock, "right", "Output", "Ctrl+2")
        self.panels.add("log", self.log_dock, "bottom", "Log", "Ctrl+3", collapse_button=self.log_dock.collapse_button)

        self._build_toolbar()
        self._build_menus()

        # ---- status bar
        self.status_label = QLabel("Ready")
        self.statusBar().addWidget(self.status_label, 1)
        self.eta_label = QLabel("")
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.eta_label)
        self.statusBar().addPermanentWidget(self.progress_bar)

        if presets.get("layout_version") != LAYOUT_VERSION:
            QTimer.singleShot(0, lambda: self.resizeDocks([self.log_dock], [170], Qt.Orientation.Vertical))
        self._restore_layout()
        self._apply_theme_everywhere()
        app = QApplication.instance()
        if app is not None:
            app.styleHints().colorSchemeChanged.connect(self._on_windows_scheme)
        self._update_enabled()
        log.info("%s %s ready. Log file: %s", APP_NAME, VERSION, log_file())

    # ================================================================ building
    def _dock(self, title: str, name: str, widget: QWidget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.addDockWidget(area, dock)
        return dock

    def _action(self, text: str, tip: str, shortcut: str = "") -> QAction:
        a = QAction(text, self)
        a.setToolTip(tip)
        a.setStatusTip(tip)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        return a

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main toolbar", self)
        tb.setObjectName("main_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(22, 22))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.a_add = self._action("Add files", "Add microscope files (Ctrl+O)", "Ctrl+O")
        self.a_add.triggered.connect(self._browse_files)
        self.a_folder = self._action("Add folder", "Add every supported file in a folder (Ctrl+Shift+O)", "Ctrl+Shift+O")
        self.a_folder.triggered.connect(self._browse_input_folder)
        self.a_convert = self._action("Convert", "Convert the ticked series (Ctrl+R)", "Ctrl+R")
        self.a_convert.triggered.connect(self._start)
        self.a_cancel = self._action("Cancel", "Stop after the current series (Esc)", "Esc")
        self.a_cancel.triggered.connect(self._cancel)
        self.a_report = self._action("Report", "The report of the last run (Ctrl+Shift+R)", "Ctrl+Shift+R")
        self.a_report.triggered.connect(self._show_last_report)
        self.a_outdir = self._action("Output folder", "Open the output folder")
        self.a_outdir.triggered.connect(self._open_output_folder)
        for a in (self.a_add, self.a_folder):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.a_convert, self.a_cancel):
            tb.addAction(a)
        spacer = QWidget(tb)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.a_report)
        tb.addAction(self.a_outdir)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self.toolbar = tb

    def _build_menus(self) -> None:
        bar = self.menuBar()
        m_file = bar.addMenu("&File")
        for a in (self.a_add, self.a_folder):
            m_file.addAction(a)
        m_file.addSeparator()
        for a in (self.a_convert, self.a_cancel, self.a_report, self.a_outdir):
            m_file.addAction(a)
        m_file.addSeparator()
        a_clear = QAction("Clear the queue", self)
        a_clear.triggered.connect(self._clear)
        m_file.addAction(a_clear)
        a_quit = QAction("Exit", self)
        a_quit.setShortcut(QKeySequence.StandardKey.Quit)
        a_quit.triggered.connect(self.close)
        m_file.addAction(a_quit)

        m_view = bar.addMenu("&View")
        for key in ("queue", "output", "log"):
            m_view.addAction(self.panels.actions[key])
        m_view.addSeparator()
        m_theme = m_view.addMenu("Theme")
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        mode = presets.get("ui_theme", theme.DEFAULT_THEME)
        for name in theme.MODES:
            a = QAction(theme.MODE_LABELS[name], self)
            a.setCheckable(True)
            a.setChecked(mode == name)
            a.setData(name)
            self.theme_group.addAction(a)
            m_theme.addAction(a)
        self.theme_group.triggered.connect(lambda a: self.set_theme(str(a.data())))
        m_view.addSeparator()
        reset = QAction("Reset layout", self)
        reset.triggered.connect(self.reset_layout)
        m_view.addAction(reset)

        m_help = bar.addMenu("&Help")
        self.a_update = QAction("Check for updates…", self)
        self.a_update.setStatusTip("Ask GitHub whether a newer version exists. Only when you click; nothing is downloaded.")
        self.a_update.triggered.connect(self.check_for_updates)
        m_help.addAction(self.a_update)
        a_logs = QAction("Open log folder", self)
        a_logs.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(log_dir())))
        m_help.addAction(a_logs)
        m_help.addSeparator()
        a_about = QAction("About", self)
        a_about.triggered.connect(lambda: AboutDialog(self).open())
        m_help.addAction(a_about)

    # ================================================================ theme / layout
    def set_theme(self, mode: str) -> None:
        presets.put("ui_theme", mode)
        app = QApplication.instance()
        if app is not None:
            theme.apply_theme(app, mode)
        self._apply_theme_everywhere()
        log.info("Theme: %s", theme.MODE_LABELS.get(mode, mode))

    def _on_windows_scheme(self, *_):
        if presets.get("ui_theme", theme.DEFAULT_THEME) == "system":
            QTimer.singleShot(0, lambda: self.set_theme("system"))

    def _apply_theme_everywhere(self) -> None:
        name = theme.current_theme(QApplication.instance())
        c = theme.colours(name)
        for a, shape, key in ((self.a_add, "add", "text"), (self.a_folder, "folder", "text"),
                              (self.a_convert, "convert", "accent"), (self.a_cancel, "cancel", "text"),
                              (self.a_report, "report", "text"), (self.a_outdir, "output", "text")):
            ic = icon(shape, c[key])
            if not ic.isNull():
                a.setIcon(ic)
        self.log_dock.set_theme(name)
        for key, it in self._items.items():
            self._paint_status(it)
        self._on_selection()

    def _restore_layout(self) -> None:
        data = presets.load()
        if data.get("layout_version") != LAYOUT_VERSION:
            return
        try:
            if data.get("window_geometry"):
                self.restoreGeometry(QByteArray(base64.b64decode(data["window_geometry"])))
            if data.get("window_state"):
                self.restoreState(QByteArray(base64.b64decode(data["window_state"])), LAYOUT_VERSION)
            self.panels.sync_from_docks()
        except Exception:  # a broken saved layout must never stop the app
            log.debug("Saved layout ignored", exc_info=True)

    def _save_layout(self) -> None:
        data = presets.load()
        data.update(
            layout_version=LAYOUT_VERSION,
            window_geometry=base64.b64encode(bytes(self.saveGeometry())).decode("ascii"),
            window_state=base64.b64encode(bytes(self.saveState(LAYOUT_VERSION))).decode("ascii"),
        )
        presets.save(data)

    def reset_layout(self) -> None:
        for dock, area in ((self.queue_dock, Qt.DockWidgetArea.LeftDockWidgetArea),
                           (self.output_dock, Qt.DockWidgetArea.RightDockWidgetArea),
                           (self.log_dock, Qt.DockWidgetArea.BottomDockWidgetArea)):
            dock.setFloating(False)
            self.addDockWidget(area, dock)
        for key in ("queue", "output", "log"):
            self.panels.set_open(key, True)
        self.resize(1400, 880)

    # ================================================================ adding files
    def dragEnterEvent(self, e):  # noqa: N802 - Qt API
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802 - Qt API
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def _browse_files(self) -> None:
        start = presets.get("last_input_dir", "")
        paths, _ = QFileDialog.getOpenFileNames(self, "Add microscope files", start, file_dialog_filter())
        if paths:
            presets.put("last_input_dir", os.path.dirname(paths[0]))
            self.add_paths(paths)

    def _browse_input_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Add every supported file in a folder", presets.get("last_input_dir", ""))
        if d:
            presets.put("last_input_dir", d)
            self.add_paths([d])

    def add_paths(self, paths: list[str]) -> None:
        files: list[str] = []
        skipped: list[str] = []
        for p in paths:
            if os.path.isdir(p):
                owner = resolve_input(p)  # a dropped "_<name>_" companion folder -> its .vsi
                if owner:
                    files.append(owner)
                    continue
                for root, dirs, names in os.walk(p):
                    dirs[:] = [d for d in dirs if not is_companion_dir(os.path.join(root, d))]
                    for n in sorted(names):
                        if reader_for(n):
                            files.append(resolve_input(os.path.join(root, n)) or os.path.join(root, n))
            else:
                owner = resolve_input(p)
                if owner:
                    files.append(owner)
                else:
                    skipped.append(p)
        errors = []
        added = 0
        seen = set()
        for f in files:
            f = os.path.abspath(f)
            if f in seen or any(k[0] == f for k in self._series):
                continue
            seen.add(f)
            try:
                self._add_file(f)
                added += 1
            except Exception as exc:
                log.exception("Could not open %s", f)
                errors.append(f"{os.path.basename(f)}: {exc}")
        if added:
            recent = [f for f in seen] + [r for r in presets.get("recent_files", []) if r not in seen]
            presets.put("recent_files", recent[:8])
            self.welcome.set_recent(recent[:8])
        if skipped or errors:
            msg = ""
            if skipped:
                log.warning("Unsupported file type skipped: %s", "; ".join(os.path.basename(s) for s in skipped))
                msg += "These files are not .lif, .vsi or .nd2 files and were skipped:\n" + "\n".join(
                    os.path.basename(s) for s in skipped) + "\n\n"
            if errors:
                msg += "These files could not be opened (details in the log):\n" + "\n".join(errors)
            box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME, msg.strip(), QMessageBox.StandardButton.Ok, self)
            box.open()
        self._update_enabled()

    def _target_key(self, path: str) -> str:
        fmt = self._formats.get(path, "")
        wanted = self.output.lif_target if fmt == "Leica LIF" else "auto"
        try:
            return resolve_format(fmt, wanted).key
        except ValueError:
            return "nd2"

    def _add_file(self, path: str) -> None:
        log.info("Opening %s", path)
        with open_reader(path) as r:
            infos = r.list_series()
            self._formats[path] = r.format_name
            label = os.path.basename(path)
            if getattr(r, "output_stem", None):
                label = f"{r.output_stem}  (numbered position files)"
        n_ok = sum(i.supported for i in infos)
        log.info("%s: %d series, %d convertible", os.path.basename(path), len(infos), n_ok)
        for info in infos:
            if not info.supported:
                log.warning("%s · %s: not convertible: %s", os.path.basename(path), info.name, info.reason)
        target = FORMATS[self._target_key(path)].name.split()[-1]
        self.tree.blockSignals(True)
        fitem = QTreeWidgetItem([label, "", "", ""])
        fitem.setToolTip(0, path)
        fitem.setData(0, ROLE_KEY, path)
        fitem.setFlags(fitem.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
        fitem.setCheckState(0, Qt.CheckState.Checked)
        for info in infos:
            key = (path, info.index)
            self._series[key] = info
            name = info.name + (f"  ({info.kind})" if info.kind else "")
            status = "" if info.supported else "Not convertible"
            if info.supported and info.hidden:
                status = "hidden layer"
            it = QTreeWidgetItem([name, info.summary, target if info.supported else "", status])
            it.setData(0, ROLE_KEY, key)
            tips = [f"Series {info.index}: {info.path}" if info.path else f"Series {info.index}"]
            if info.hidden:
                tips.append("This layer is not shown by default in cellSens/OlyVIA.")
            if info.supported:
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked)
            else:
                it.setFlags((it.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEnabled)
                it.setCheckState(0, Qt.CheckState.Unchecked)
                tips.append(info.reason)
                it.setToolTip(3, info.reason)
            it.setToolTip(0, "\n".join(tips))
            fitem.addChild(it)
            self._items[key] = it
        self.tree.addTopLevelItem(fitem)
        fitem.setFirstColumnSpanned(True)
        fitem.setText(0, f"{label}  ·  {len(infos)} series")
        fitem.setExpanded(True)
        self.tree.blockSignals(False)
        if self.tree.currentItem() is None and fitem.childCount():
            self.tree.setCurrentItem(fitem.child(0))

    def _on_options_changed(self) -> None:
        for key, it in self._items.items():
            if self._series[key].supported:
                it.setText(2, FORMATS[self._target_key(key[0])].name.split()[-1])
        self._update_enabled()
        self._on_selection()

    def _remove_selected(self) -> None:
        for it in self.tree.selectedItems():
            key = it.data(0, ROLE_KEY)
            if isinstance(key, str):
                for k in [k for k in self._series if k[0] == key]:
                    self._forget(k)
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
            elif key is not None and it.parent() is not None:
                it.setCheckState(0, Qt.CheckState.Unchecked)
        self._update_enabled()

    def _forget(self, key) -> None:
        for d in (self._series, self._names, self._items, self._levels):
            d.pop(key, None)
        for mk in [mk for mk in self._meta if mk[0] == key]:
            self._meta.pop(mk, None)

    def _clear(self) -> None:
        if self._worker is not None:
            return
        self.tree.clear()
        for d in (self._series, self._meta, self._names, self._items, self._levels, self._formats):
            d.clear()
        self.panel.set_metadata(None)
        self._update_enabled()

    def _checked_keys(self) -> list[tuple[str, int]]:
        keys = []
        for i in range(self.tree.topLevelItemCount()):
            f = self.tree.topLevelItem(i)
            for j in range(f.childCount()):
                it = f.child(j)
                key = it.data(0, ROLE_KEY)
                if it.checkState(0) == Qt.CheckState.Checked and self._series[key].supported:
                    keys.append(key)
        return keys

    def _update_enabled(self) -> None:
        running = self._worker is not None
        keys = self._checked_keys()
        n = len(keys)
        counts: dict[str, int] = {}
        for k in keys:
            t = FORMATS[self._target_key(k[0])].name.split()[-1]
            counts[t] = counts.get(t, 0) + 1
        self.output.btn_convert.setText(f"Convert {n} series" if n else "Convert")
        self.output.summary.setText(
            ", ".join(f"{v} → {k}" for k, v in sorted(counts.items())) if n else "Tick the series to convert in the queue."
        )
        self.output.set_running(running)
        self.output.btn_convert.setEnabled(not running and n > 0)
        self.a_convert.setEnabled(not running and n > 0)
        self.a_cancel.setEnabled(running)
        self.a_add.setEnabled(not running)
        self.a_folder.setEnabled(not running)
        self.a_report.setEnabled(bool(self._last_results))
        for w in (self.btn_add, self.btn_remove, self.btn_clear):
            w.setEnabled(not running)
        self.central.setCurrentWidget(self.panel if self.tree.topLevelItemCount() else self.welcome)

    # ================================================================ metadata
    def _metadata(self, key) -> SeriesMetadata:
        level = self._levels.get(key, 0)
        mk = (key, level)
        if mk not in self._meta:
            with open_reader(key[0]) as r:
                self._meta[mk] = r.metadata(key[1], level=level)
        return self._meta[mk]

    def _current_key(self):
        it = self.tree.currentItem()
        key = it.data(0, ROLE_KEY) if it else None
        return key if isinstance(key, tuple) else None

    def _on_selection(self) -> None:
        key = self._current_key()
        if key is None:
            self.panel.set_metadata(None)
            return
        info = self._series.get(key)
        if info is None:
            return
        if not info.supported:
            self.panel.set_metadata(None, info.reason)
            return
        try:
            meta = self._metadata(key)
        except Exception as exc:
            log.exception("Could not read metadata of %s series %d", *key)
            self.panel.set_metadata(None, f"Could not read the metadata: {exc}")
            return
        if key in self._names:
            for ch, n in zip(meta.channels, self._names[key]):
                ch.name = n
        target = FORMATS[self._target_key(key[0])]
        note = target.name
        if meta.dims.p > 1 and not target.positions_in_one_file:
            note += f" (one .vsi per stage position, {meta.dims.p} files)"
        elif meta.dims.p > 1:
            note += f" (one file with {meta.dims.p} stage positions)"
        self.panel.set_metadata(meta, target=note)

    def _on_names_changed(self, names: list[str]) -> None:
        key = self._current_key()
        if key is None:
            return
        self._names[key] = names
        for ch, n in zip(self._metadata(key).channels, names):
            ch.name = n

    def _on_level_changed(self, level: int) -> None:
        key = self._current_key()
        if key is None or self._levels.get(key, 0) == level:
            return
        self._levels[key] = level
        meta = self._metadata(key)
        it = self._items.get(key)
        if it is not None:
            it.setText(1, f"{meta.dims.x}×{meta.dims.y} · C{meta.dims.c}" + (f" · 1/{2 ** level}" if level else " · full"))
        log.info("%s · %s: resolution level %d (%d×%d px)", os.path.basename(key[0]), meta.series_name, level,
                 meta.dims.x, meta.dims.y)
        self._on_selection()

    def _apply_names_to_same(self) -> None:
        key = self._current_key()
        if key is None:
            return
        src = self._metadata(key)
        lookup = {ch.settings_key: n for ch, n in zip(src.channels, self.panel.current_names())}
        changed = 0
        for k, info in self._series.items():
            if k == key or not info.supported:
                continue
            try:
                meta = self._metadata(k)
            except Exception:
                continue
            names = self._names.get(k, [ch.name for ch in meta.channels])
            new = [lookup.get(ch.settings_key, n) for ch, n in zip(meta.channels, names)]
            if new != names:
                self._names[k] = new
                for ch, n in zip(meta.channels, new):
                    ch.name = n
                changed += 1
        self.status_label.setText(f"Channel names copied to {changed} other series")

    def _on_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        res = item.data(0, ROLE_RESULT)
        if isinstance(res, JobResult):
            ReportDialog([res], parent=self).open()

    # ================================================================ run
    def _start(self) -> None:
        if self._worker is not None:
            return
        keys = self._checked_keys()
        if not keys:
            return
        if self.output.output_folder() == "":
            QMessageBox(QMessageBox.Icon.Information, APP_NAME, "Choose an output folder first, or save next to the "
                        "source files.", QMessageBox.StandardButton.Ok, self).open()
            return
        options = self.output.options()
        jobs = [Job(key=k, channel_names=self._names.get(k), level=self._levels.get(k, 0),
                    output_format=self._target_key(k[0])) for k in keys]
        for k in keys:
            self._set_status(k, "Queued")
        self._total_jobs, self._done_jobs = len(jobs), 0
        self._started = time.monotonic()
        log.info("Starting %d conversion(s); output: %s", len(jobs), options.output_dir or "next to the source files")
        self._worker = ConvertWorker(jobs, options, self)
        self._worker.job_started.connect(lambda k: self._set_status(k, "Converting…"))
        self._worker.job_progress.connect(self._on_progress)
        self._worker.job_finished.connect(self._on_finished)
        self._worker.all_done.connect(self._on_all_done)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self._worker.start()
        self._update_enabled()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("Cancelling after the current step…")

    def _set_status(self, key, text: str) -> None:
        it = self._items.get(key)
        if it is not None:
            it.setText(3, text)

    def _on_progress(self, key, frac: float, msg: str) -> None:
        self._set_status(key, f"{frac * 100:.0f} %")
        name = self._series[key].name
        self.status_label.setText(f"{os.path.basename(key[0])} · {name}: {msg}")
        done = (self._done_jobs + frac) / max(1, self._total_jobs)
        self.progress_bar.setValue(int(1000 * done))
        elapsed = time.monotonic() - self._started
        if done > 0.03 and elapsed > 5:
            left = elapsed * (1 - done) / done
            self.eta_label.setText(f"about {left / 60:.0f} min left" if left >= 90 else f"about {left:.0f} s left")

    def _paint_status(self, it: QTreeWidgetItem) -> None:
        res = it.data(0, ROLE_RESULT)
        if not isinstance(res, JobResult):
            return
        c = theme.colours(theme.current_theme(QApplication.instance()))
        colour = c["ok"] if res.ok else (c["muted"] if res.cancelled else c["error"])
        it.setForeground(3, QColor(colour))

    def _on_finished(self, key, res: JobResult) -> None:
        self._done_jobs += 1
        it = self._items.get(key)
        if it is not None:
            it.setData(0, ROLE_RESULT, res)
            it.setToolTip(3, res.error or "\n".join(res.outputs))
            if res.ok:
                it.setText(3, "✓ Done" + (f" ({len(res.outputs)} files)" if len(res.outputs) > 1 else ""))
                it.setCheckState(0, Qt.CheckState.Unchecked)
            elif res.cancelled:
                it.setText(3, "Cancelled")
            else:
                it.setText(3, "✗ Failed")
            self._paint_status(it)

    def _skipped(self) -> list[tuple[str, str, str]]:
        return [(os.path.basename(k[0]), info.name, info.reason) for k, info in self._series.items() if not info.supported]

    def _on_all_done(self, results: list[JobResult]) -> None:
        self._worker = None
        self._last_results = results
        self.progress_bar.setVisible(False)
        self.eta_label.setText("")
        ok = sum(r.ok for r in results)
        self.status_label.setText(f"Finished: {ok} of {len(results)} converted")
        log.info("Finished: %d of %d converted", ok, len(results))
        self._update_enabled()
        if results:
            ReportDialog(results, skipped=self._skipped(), parent=self).open()

    def _show_last_report(self) -> None:
        if self._last_results:
            ReportDialog(self._last_results, skipped=self._skipped(), parent=self).open()

    def _open_output_folder(self) -> None:
        folder = self.output.output_folder()
        if not folder and self._last_results:
            first = next((r.output_path for r in self._last_results if r.output_path), "")
            folder = os.path.dirname(first) if first else ""
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            self.status_label.setText("No output folder yet: files are saved next to their source files.")

    # ================================================================ updates
    def check_for_updates(self) -> None:
        from .update_check import check_latest

        self.status_label.setText("Asking GitHub for the latest version…")
        QApplication.processEvents()
        result = check_latest()
        box = QMessageBox(QMessageBox.Icon.Information, "Check for updates", result.message,
                          QMessageBox.StandardButton.Close, self)
        if result.status == "newer":
            open_btn = box.addButton("Open the release page", QMessageBox.ButtonRole.AcceptRole)
            open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(result.url)))
        box.open()
        self.status_label.setText(result.message)

    def closeEvent(self, e):  # noqa: N802 - Qt API
        if self._worker is not None:
            if QMessageBox.question(self, APP_NAME, "A conversion is running. Cancel it and quit?") != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self._worker.cancel()
            self._worker.wait(30000)
        self._save_layout()
        self.log_dock.detach()
        e.accept()
