# SPDX-License-Identifier: GPL-3.0-or-later
"""Screenshots for the README from synthetic demo files (developer tool).

    python tools/make_screenshots.py [theme]

Writes docs/images/welcome.png, main_window.png and report.png. The demo files (a multipoint
ND2 made with the ND2 writer and a cellSens VSI made with the VSI writer, both with blob-shaped
"cells") are created in C:\\MetadataTransferDemo so that no user name appears in the pictures, and
deleted afterwards.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP / "source"))
DEMO = Path("C:/MetadataTransferDemo")
OUT = APP / "docs" / "images"


def demo_frames(meta, seed: int):
    import numpy as np

    d = meta.dims
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0 : d.y, 0 : d.x]
    frames = []
    cells = [(rng.uniform(40, d.x - 40), rng.uniform(40, d.y - 40), rng.uniform(8, 22)) for _ in range(60)]
    for t, p, z in d.frame_keys():
        frame = np.zeros((d.y, d.x, d.c), dtype=np.uint16)
        focus = 1.0 - abs(z - d.z / 2) / d.z
        for c in range(d.c):
            img = np.full((d.y, d.x), 300.0 if c < d.c - 1 else 9000.0)
            for k, (cx, cy, r) in enumerate(cells):
                if c < d.c - 1 and k % (c + 2):
                    continue
                cx2, cy2 = cx + 30 * p + 3 * t, cy + 20 * p
                blob = np.exp(-(((xx - cx2) ** 2 + (yy - cy2) ** 2) / (2 * (r * (1.5 - focus)) ** 2)))
                img += (3000 * focus if c < d.c - 1 else -2500) * blob
            img += rng.normal(0, 60, img.shape)
            frame[..., c] = np.clip(img, 0, 65535).astype(np.uint16)
        frames.append((t, p, z, frame))
    return frames


def make_demo_files() -> list[str]:
    from metadata_transfer.model import (Acquisition, Calibration, Channel, Dimensions, Objective, SeriesMetadata,
                                         StagePosition)
    from metadata_transfer.writers import write_nd2, write_vsi

    if DEMO.exists():
        shutil.rmtree(DEMO)
    DEMO.mkdir(parents=True)

    def meta(name, dims, fmt):
        return SeriesMetadata(
            source_path=name, source_format=fmt, series_index=0, series_name=Path(name).stem,
            dims=dims, dtype="uint16", bits=16, calibration=Calibration(0.65, 0.65, 2.0, 600.0 if dims.t > 1 else None),
            objective=Objective("Plan Fluor 10x Ph1", 10.0, 0.3, "air", 1.0),
            channels=[Channel("DAPI", excitation_nm=395, emission_range_nm=(460, 460), color_rgb=(0, 128, 255),
                              detector="sCMOS camera", exposure_ms=100.0),
                      Channel("GFP", excitation_nm=488, emission_range_nm=(525, 525), color_rgb=(0, 255, 0),
                              detector="sCMOS camera", exposure_ms=300.0),
                      Channel("Phase contrast", modality="brightfield", color_rgb=(255, 255, 255), detector="sCMOS camera",
                              exposure_ms=20.0)],
            imaging_mode="widefield",
            acquisition=Acquisition(start=dt.datetime(2026, 9, 1, 10, 30), microscope="Nikon Ti2",
                                    frame_times_s=[2.0 * i for i in range(dims.n_frames)]),
            z_positions_um=[1000.0 + 2.0 * k for k in range(dims.z)] if dims.z > 1 else None,
            positions=[StagePosition(5000.0 + 9000 * k, -3000.0 + 4000 * k, 1000.0, f"Well A{k + 1}") for k in range(dims.p)],
        )

    m1 = meta("Demo wells.nd2", Dimensions(x=640, y=480, z=5, c=3, t=1, p=4), "Nikon ND2")
    write_nd2(m1, iter(demo_frames(m1, 1)), str(DEMO / "Demo wells.nd2"))
    m2 = meta("Demo spheroid.vsi", Dimensions(x=640, y=480, z=9, c=3, t=1, p=1), "Evident VSI")
    write_vsi(m2, iter(demo_frames(m2, 2)), str(DEMO / "Demo spheroid.vsi"))
    return [str(DEMO / "Demo wells.nd2"), str(DEMO / "Demo spheroid.vsi")]


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "terracotta"
    os.environ["APPDATA"] = str(DEMO / "appdata")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from metadata_transfer.gui import theme
    from metadata_transfer.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    files = make_demo_files()
    theme.apply_theme(app, mode)
    OUT.mkdir(parents=True, exist_ok=True)
    w = MainWindow()
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.resize(1500, 930)
    w.show()
    app.processEvents()
    w.log_dock.clear()
    w.grab().save(str(OUT / "welcome.png"))
    w.add_paths(files)
    w.tree.setCurrentItem(w.tree.topLevelItem(0).child(0))
    w.output.rb_folder.setChecked(True)
    w.output.ed_folder.setText(str(DEMO / "converted"))
    for _ in range(5):
        app.processEvents()
    w.grab().save(str(OUT / "main_window.png"))
    w._start()
    while w._worker is not None:
        app.processEvents()
        time.sleep(0.02)
    for _ in range(10):
        app.processEvents()
    dialogs = [d for d in app.topLevelWidgets() if d.windowTitle() == "Conversion report" and d.isVisible()]
    if dialogs:
        dialogs[-1].resize(1100, 640)
        app.processEvents()
        dialogs[-1].grab().save(str(OUT / "report.png"))
    w.close()
    app.processEvents()
    shutil.rmtree(DEMO, ignore_errors=True)
    print("screenshots written to", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
