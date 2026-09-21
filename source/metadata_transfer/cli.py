# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line interface (batch conversion, dumps for debugging)."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import APP_NAME, __version__
from .convert import ConvertOptions, convert_series
from .log import setup_logging
from .readers import open_reader, resolve_input
from .readers.evident_vsi import groups
from .writers.registry import FORMATS, resolve_format


def _dump(path: str, level: int) -> int:
    with open_reader(path) as r:
        for info in r.list_series():
            flag = "" if info.supported else f"  [unsupported: {info.reason}]"
            extra = f"  ({info.kind})" if info.kind else ""
            extra += "  [hidden layer]" if info.hidden else ""
            print(f"[{info.index}] {info.name}  {info.summary}  {info.dtype}{extra}{flag}")
            if not info.supported:
                continue
            lv = level if level < max(1, len(info.pyramid_levels)) else 0
            m = r.metadata(info.index, level=lv)
            cal = m.calibration
            d = m.dims
            print(
                f"     {d.x}x{d.y} Z{d.z} C{d.c} T{d.t} P{d.p} bits={m.bits} px={cal.pixel_size_x_um} x {cal.pixel_size_y_um} µm  "
                f"z={cal.z_step_um} µm  t={cal.time_step_s} s  mode={m.imaging_mode}"
            )
            print(
                f"     obj={m.objective.name} mag={m.objective.magnification} NA={m.objective.numerical_aperture} "
                f"zoom={m.zoom} pinhole={m.pinhole_um}  acquired={m.acquisition.start}  scope={m.acquisition.microscope}"
            )
            if m.pyramid_levels:
                print("     resolutions: " + ", ".join(f"{k}: {lvl.x}x{lvl.y}" for k, lvl in enumerate(m.pyramid_levels)))
            for i, ch in enumerate(m.channels):
                exp = f" exp={ch.exposure_ms:g}ms" if ch.exposure_ms is not None else ""
                flt = f" filter={ch.filter_name}" if ch.filter_name else ""
                print(
                    f"     C{i}: {ch.name:<22} ex={ch.excitation_nm} em={ch.emission_range_nm} det={ch.detector}{exp}{flt} "
                    f"rgb={ch.color_rgb} mod={ch.modality}  [{ch.auto_name}]"
                )
            for k, pos in enumerate(m.positions if d.p > 1 else []):
                print(f"     P{k + 1}: X {pos.x_um} Y {pos.y_um} Z {pos.z_um} {pos.name}")
            for k, v in m.scan_settings.items():
                print(f"     · {k}: {v}")
            for w in m.warnings:
                print(f"     ! {w}")
    return 0


def _dump_tags(path: str) -> int:
    from .readers.evident_vsi.tagtree import dump_text, parse_vsi

    print(dump_text(parse_vsi(path)))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="MetadataTransfer", description=f"{APP_NAME} {__version__}")
    p.add_argument("--dump", metavar="FILE", help="list series and metadata")
    p.add_argument("--dump-tags", metavar="FILE.vsi", help="print the raw Evident/Olympus tag tree of a .vsi file")
    p.add_argument("--convert", metavar="FILE", help="convert a file (LIF/VSI -> ND2, ND2 -> VSI, LIF -> VSI with --to vsi)")
    p.add_argument("--to", choices=["auto", *FORMATS], default="auto", help="output format (default: ND2, or VSI for ND2 input)")
    p.add_argument("--no-group", action="store_true", help="do not combine numbered cellSens position files (_01.vsi, _02.vsi, ...)")
    p.add_argument("--series", default="all", help="series indices, e.g. 0,4,5 (default: all supported)")
    p.add_argument("--level", type=int, default=0, help="resolution level for pyramidal series (0 = full)")
    p.add_argument("--out", help="output folder (default: next to the source)")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--no-sidecar", action="store_true")
    p.add_argument("--no-textinfo", action="store_true")
    p.add_argument("--apply-scan-flags", action="store_true", help="re-orient using vendor flip/swap flags")
    p.add_argument("--json", action="store_true", help="print results as JSON")
    p.add_argument("--verbose", "-v", action="store_true", help="log progress to the console")
    a = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    setup_logging(console=a.verbose, level=logging.DEBUG if a.verbose else logging.INFO)
    if a.no_group:
        groups.ENABLED = False

    if a.dump_tags:
        return _dump_tags(a.dump_tags)
    if a.dump:
        return _dump(resolve_input(a.dump) or a.dump, a.level)
    if not a.convert:
        p.print_help()
        return 2

    source = resolve_input(a.convert) or a.convert
    with open_reader(source) as r:
        infos = r.list_series()
        fmt = resolve_format(r.format_name, a.to)
    if a.series == "all":
        indices = [i.index for i in infos if i.supported]
    else:
        indices = [int(s) for s in a.series.split(",")]
    opts = ConvertOptions(
        output_dir=a.out,
        overwrite=a.overwrite,
        write_sidecar=not a.no_sidecar,
        embed_text_info=not a.no_textinfo,
        apply_orientation=a.apply_scan_flags,
        output_format=fmt.key,
    )
    results = []
    ok = True
    for idx in indices:
        res = convert_series(source, idx, opts, level=a.level)
        ok &= res.ok
        results.append(res)
        if not a.json:
            print(res.report_text)
    if a.json:
        print(json.dumps([{"series": r.series_name, "ok": r.ok, "outputs": r.outputs, "error": r.error} for r in results], indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
