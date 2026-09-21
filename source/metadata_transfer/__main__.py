# SPDX-License-Identifier: GPL-3.0-or-later
"""Entry point: the GUI by default (files given as arguments are queued); `--version`,
`--diagnostic report.json` (environment report and self-test, used by the installer test) and
every other `--option` go to the command line interface."""

from __future__ import annotations

import json
import logging
import platform
import sys
import tempfile


def install_excepthook() -> None:
    """Unhandled exceptions go to the log and, once the window exists, to the crash window."""
    log = logging.getLogger("metadata_transfer")
    previous = sys.excepthook

    def hook(exc_type, exc_value, exc_tb):
        log.error("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
        try:
            from .gui.dialogs import report_exception

            report_exception(exc_type, exc_value, exc_tb)
        except Exception:
            pass
        if sys.stderr is not None:
            previous(exc_type, exc_value, exc_tb)

    sys.excepthook = hook
    import threading

    threading.excepthook = lambda args: hook(args.exc_type, args.exc_value, args.exc_traceback)


def selftest() -> dict[str, str]:
    """Write and read back a small ND2 and VSI (checks the frozen bundle has everything it needs)."""
    import os

    import numpy as np

    from .convert import ConvertOptions, convert_series
    from .model import Acquisition, Calibration, Channel, Dimensions, Objective, SeriesMetadata, StagePosition
    from .writers import write_nd2

    out: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="mt_selftest_") as tmp:
        meta = SeriesMetadata(
            source_path="selftest", source_format="test", series_index=0, series_name="selftest",
            dims=Dimensions(x=64, y=48, z=2, c=2, t=1, p=2), dtype="uint16", bits=12,
            calibration=Calibration(0.5, 0.5, 1.0, None), objective=Objective("Test 20x", 20, 0.75, "air", 1.0),
            channels=[Channel("A", excitation_nm=488, emission_range_nm=(520, 520), color_rgb=(0, 255, 0)),
                      Channel("B", modality="brightfield")],
            acquisition=Acquisition(frame_times_s=[0.0, 0.5, 1.0, 1.5]),
            positions=[StagePosition(0.0, 0.0, 0.0), StagePosition(100.0, 0.0, 0.0)],
        )
        rng = np.random.default_rng(0)
        frames = [(t, p, z, rng.integers(0, 4095, (48, 64, 2), dtype=np.uint16)) for t, p, z in meta.dims.frame_keys()]
        src = os.path.join(tmp, "selftest.nd2")
        try:
            write_nd2(meta, iter(frames), src)
            out["nd2_write"] = "ok"
        except Exception as exc:
            return {"nd2_write": f"failed: {type(exc).__name__}: {exc}"}
        try:
            res = convert_series(src, 0, ConvertOptions(output_dir=os.path.join(tmp, "vsi")))
            out["nd2_to_vsi"] = "ok" if res.ok and len(res.outputs) == 2 else f"failed: {res.error}"
            back = convert_series(res.outputs[0], 0, ConvertOptions(output_dir=os.path.join(tmp, "nd2")))
            out["vsi_to_nd2"] = "ok" if back.ok else f"failed: {back.error}"
        except Exception as exc:
            out["conversion"] = f"failed: {type(exc).__name__}: {exc}"
    return out


def diagnostic(path: str) -> int:
    from . import APP_NAME, VERSION
    from .log import log_dir, setup_logging

    def version_of(module: str) -> str:
        try:
            mod = __import__(module)
        except Exception as exc:
            return f"missing ({type(exc).__name__})"
        return str(getattr(mod, "__version__", "unknown"))

    report = {
        "app": APP_NAME,
        "app_version": VERSION,
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "modules": {m: version_of(m) for m in ("PySide6", "numpy", "nd2", "limnd2", "liffile", "tifffile", "PIL")},
        "log_dir": log_dir(),
        "log_file": setup_logging(console=False),
        "selftest": selftest(),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return 0 if all(v == "ok" for v in report["selftest"].values()) else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args:
        from . import APP_NAME, VERSION

        if sys.stdout is not None:
            print(f"{APP_NAME} {VERSION}")
        return 0
    if "--diagnostic" in args:
        i = args.index("--diagnostic")
        return diagnostic(args[i + 1] if i + 1 < len(args) else "diagnostic.json")
    if any(a.startswith("-") for a in args):
        from .cli import main as cli_main

        return cli_main(args)
    from .log import setup_logging

    setup_logging(console=False)
    install_excepthook()
    from .gui.app import run

    return run([sys.argv[0], *args])


if __name__ == "__main__":
    sys.exit(main())
