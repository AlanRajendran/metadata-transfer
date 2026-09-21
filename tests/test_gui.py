# SPDX-License-Identifier: GPL-3.0-or-later
"""The window without a screen: themes, adding files, converting, the report (offscreen Qt)."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from metadata_transfer.gui import theme  # noqa: E402
from metadata_transfer.writers import write_nd2  # noqa: E402
from test_vsi_writer import synthetic_frames, synthetic_meta  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("mode", theme.MODES)
def test_every_theme_applies(app, mode):
    shown = theme.apply_theme(app, mode)
    assert shown in theme.THEMES
    assert theme.current_theme(app) == shown


def test_convert_from_the_window(app, tmp_path):
    from metadata_transfer.gui.main_window import MainWindow

    meta = synthetic_meta(x=64, y=48, z=2, t=1, c=2, p=2)
    src = tmp_path / "in" / "wells.nd2"
    src.parent.mkdir()
    write_nd2(meta, iter(synthetic_frames(meta)), str(src))
    w = MainWindow()
    w.add_paths([str(src)])
    assert w.tree.topLevelItemCount() == 1
    key = (str(src), 0)
    assert w._items[key].text(2) == "VSI"
    w.tree.setCurrentItem(w._items[key])
    assert w.panel.header.text() == "wells"
    assert "2 files" in w.panel.target.text()
    w.output.rb_folder.setChecked(True)
    w.output.ed_folder.setText(str(tmp_path / "out"))
    w._start()
    deadline = time.monotonic() + 120
    while w._worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert w._worker is None
    (res,) = w._last_results
    assert res.ok, res.report_text
    assert sorted(os.listdir(tmp_path / "out")) == sorted(
        ["wells_01.vsi", "_wells_01_", "wells_01.metadata.json", "wells_02.vsi", "_wells_02_", "wells_02.metadata.json",
         "wells.report.txt"])
    w.close()
