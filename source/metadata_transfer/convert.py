# SPDX-License-Identifier: GPL-3.0-or-later
"""One conversion job: (source file, series) -> ND2 or VSI + sidecar + report.

Leica LIF -> ND2 or VSI, Evident VSI -> ND2, Nikon ND2 -> VSI. Multipoint sources become one ND2
with an XY loop, or one numbered `.vsi` per stage position (`<name>_01.vsi`, ...), which is how
cellSens itself stores multi-position experiments. Every output is re-read and checked.
"""

from __future__ import annotations

import copy
import datetime as _dt
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from . import APP_NAME, __version__
from .mapping.channel_naming import make_unique, safe_filename
from .model import SeriesMetadata, StagePosition
from .readers import open_reader
from .readers.base import Reader
from .validation import Check, unmapped_items, unmapped_items_vsi, validate, validate_vsi
from .writers import Cancelled, write_nd2, write_sidecar
from .writers.nd2_limnd2 import CancelFn, ProgressFn
from .writers.registry import OutputFormat, resolve_format
from .writers.vsi_writer import companion_dir, write_vsi

log = logging.getLogger(__name__)

#: Windows MAX_PATH is 260; warn above this and use the extended-length prefix when writing.
LONG_PATH_WARN = 240


@dataclass
class ConvertOptions:
    output_dir: str | None = None  # None -> next to the source file
    overwrite: bool = False
    write_sidecar: bool = True
    embed_text_info: bool = True
    validate: bool = True
    write_report: bool = True
    apply_orientation: bool = False
    output_format: str = "auto"  # "auto" | "nd2" | "vsi"


@dataclass
class JobResult:
    source: str
    series_name: str
    output_path: str = ""  # first output file
    outputs: list[str] = field(default_factory=list)  # every file written (several for VSI positions)
    output_format: str = ""
    ok: bool = False
    cancelled: bool = False
    error: str = ""
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    report_text: str = ""
    seconds: float = 0.0
    level: int = 0
    n_levels: int = 1

    @property
    def failed_checks(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]


def long_path(path: str) -> str:
    """Extended-length form (`\\\\?\\C:\\...`) for long Windows paths; unchanged otherwise."""
    if sys.platform == "win32" and len(path) >= LONG_PATH_WARN and not path.startswith("\\\\?\\"):
        return "\\\\?\\" + os.path.abspath(path)
    return path


def _taken(path: str, fmt: OutputFormat) -> bool:
    if os.path.exists(long_path(path)) or os.path.exists(long_path(path + ".part")):
        return True
    return fmt.key == "vsi" and os.path.exists(long_path(companion_dir(path)))


def output_path_for(
    source: str,
    series_name: str,
    options: ConvertOptions,
    *,
    single_series: bool = False,
    fmt: OutputFormat | None = None,
    suffix: str = "",
    stem_override: str | None = None,
) -> str:
    """`<file>_<series><suffix>.<ext>`, or `<file><suffix>.<ext>` when the source holds one series."""
    fmt = fmt or resolve_format("Leica LIF", "nd2")
    folder = options.output_dir or os.path.dirname(os.path.abspath(source))
    base = stem_override or os.path.splitext(os.path.basename(source))[0]
    stem = safe_filename((base if single_series else f"{base}_{series_name}") + suffix)
    path = os.path.join(folder, stem + fmt.extension)
    if options.overwrite:
        return path
    n = 2
    while _taken(path, fmt):
        path = os.path.join(folder, f"{stem} ({n}){fmt.extension}")
        n += 1
    return path


def apply_channel_names(meta: SeriesMetadata, names: list[str] | None) -> None:
    if not names:
        return
    names = make_unique([n if n.strip() else ch.name for n, ch in zip(names, meta.channels)])
    for ch, n in zip(meta.channels, names):
        ch.name = n


# ------------------------------------------------------------ positions
def position_view(meta: SeriesMetadata, p: int) -> SeriesMetadata:
    """Metadata of stage position `p` alone (for formats with one position per file)."""
    d = meta.dims
    m = copy.deepcopy(meta)
    m.dims.p = 1
    for lv in m.pyramid_levels:
        lv.p = 1
    pos = meta.positions[p] if p < len(meta.positions) else StagePosition()
    m.positions = [pos]
    m.acquisition.stage_x_um, m.acquisition.stage_y_um = pos.x_um, pos.y_um
    if pos.z_um is not None:
        m.acquisition.stage_z_um = pos.z_um
    times = meta.acquisition.frame_times_s
    if times and len(times) == d.n_frames:
        keys = d.frame_keys()
        m.acquisition.frame_times_s = [times[i] for i, (_t, pp, _z) in enumerate(keys) if pp == p]
    m.vendor_raw = {**m.vendor_raw, "experiment": meta.series_name}  # shared by all position files
    m.series_name = f"{meta.series_name} · position {p + 1}" + (f" ({pos.name})" if pos.name else "")
    return m


def _frames_of_position(reader: Reader, meta: SeriesMetadata, p: int) -> Iterator[tuple[int, int, int, np.ndarray]]:
    d = meta.dims
    for t in range(d.t):
        for z in range(d.z):
            frame = np.empty((d.y, d.x, d.c), dtype=np.dtype(meta.dtype))
            for y0, band in reader.iter_bands(meta, t, p, z):
                frame[y0 : y0 + band.shape[0]] = band.reshape(band.shape[0], d.x, d.c)
            yield t, 0, z, frame


# --------------------------------------------------------------- report
def format_report(res: JobResult, meta: SeriesMetadata | None) -> str:
    lines = [
        f"{APP_NAME} {__version__} — conversion report",
        f"Date:    {_dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Source:  {res.source}  [{res.series_name}]",
        f"Output:  {res.output_path or '—'}" + (f"  (+{len(res.outputs) - 1} more)" if len(res.outputs) > 1 else ""),
        f"Format:  {res.output_format or '—'}",
        f"Result:  {'OK' if res.ok else ('CANCELLED' if res.cancelled else 'FAILED')}"
        + (f"  ({res.seconds:.1f} s)" if res.seconds else ""),
    ]
    if len(res.outputs) > 1:
        lines += ["Files:"] + [f"  {o}" for o in res.outputs]
    if meta is not None:
        d = meta.output_dims
        size = f"Size:    {d.x} × {d.y} px, {d.c} channel{'s' if d.c != 1 else ''}"
        if d.z > 1:
            size += f", Z {d.z}"
        if d.t > 1:
            size += f", T {d.t}"
        if d.p > 1:
            size += f", {d.p} positions"
        nbytes = d.x * d.y * d.c * d.n_frames * np.dtype(meta.dtype).itemsize
        size += f"  ({d.x * d.y * d.c / 1e6:.1f} Mpx per plane, {nbytes / 1e9:.2f} GB)"
        lines.append(size)
        if res.n_levels > 1:
            lines.append(f"Resolution: level {res.level} of {res.n_levels} (1/{2 ** res.level} of full resolution)")
    if res.error:
        lines += ["", f"Error: {res.error}"]
    group = None
    for c in res.checks:
        if c.group != group:
            group = c.group
            lines += ["", group]
        arrow = f"{c.source} → {c.output}" if c.source != c.output else c.output
        lines.append(f"  {c.symbol} {c.label}: {arrow}")
    if res.unmapped:
        lines += ["", f"NOT IN STRUCTURED {(res.output_format or 'OUTPUT').upper()} FIELDS (preserved elsewhere)"]
        lines += [f"  ! {u}" for u in res.unmapped]
    if res.warnings:
        lines += ["", "WARNINGS"]
        lines += [f"  ! {w}" for w in res.warnings]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ job
def convert_series(
    source: str,
    series_index: int,
    options: ConvertOptions | None = None,
    *,
    channel_names: list[str] | None = None,
    level: int = 0,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> JobResult:
    options = options or ConvertOptions()
    t0 = time.perf_counter()
    res = JobResult(source=source, series_name=f"#{series_index}", level=level)
    meta: SeriesMetadata | None = None
    try:
        with open_reader(source) as reader:
            fmt = resolve_format(reader.format_name, options.output_format)
            res.output_format = fmt.name
            infos = reader.list_series()
            single = len(infos) == 1
            meta = reader.metadata(series_index, apply_orientation=options.apply_orientation, level=level)
            res.series_name = meta.series_name
            res.n_levels = max(1, len(meta.pyramid_levels))
            apply_channel_names(meta, channel_names)
            res.warnings = list(meta.warnings)
            d = meta.output_dims
            split = d.p > 1 and not fmt.positions_in_one_file
            parts = [(p, position_view(meta, p)) for p in range(d.p)] if split else [(None, meta)]
            width = max(2, len(str(d.p)))
            raw = reader.raw_metadata_xml(series_index) if options.write_sidecar else ""
            for n_part, (p, pmeta) in enumerate(parts):
                suffix = f"_{p + 1:0{width}d}" if p is not None else ""
                out = output_path_for(source, meta.series_name, options, single_series=single, fmt=fmt, suffix=suffix,
                                      stem_override=getattr(reader, "output_stem", None))
                if len(out) >= LONG_PATH_WARN:
                    res.warnings.append(
                        f"output path is {len(out)} characters long; some programs cannot open paths over 260 characters"
                    )
                fs_out = long_path(out)
                os.makedirs(os.path.dirname(fs_out), exist_ok=True)
                res.outputs.append(out)
                res.output_path = res.outputs[0]
                dd = pmeta.output_dims
                log.info(
                    "Converting %s [%s] -> %s (%dx%d, Z%d C%d T%d P%d, %s, level %d)",
                    os.path.basename(source), pmeta.series_name, out, dd.x, dd.y, dd.z, dd.c, dd.t, dd.p, meta.dtype, level,
                )
                share = 0.9 / len(parts)

                def prog(f, m, k=n_part):
                    if progress:
                        progress(share * (k + f), m + (f" (position {k + 1}/{len(parts)})" if len(parts) > 1 else ""))

                if fmt.key == "nd2":
                    hashes = write_nd2(
                        pmeta, reader.iter_frames(pmeta), fs_out, bands=reader.iter_bands,
                        embed_text_info=options.embed_text_info, progress=prog, cancel=cancel,
                    )
                else:
                    if p is None:
                        frames, bands = reader.iter_frames(pmeta), reader.iter_bands
                    else:
                        frames = _frames_of_position(reader, meta, p)

                        def bands(_m, t, _p, z, k=p):
                            return reader.iter_bands(meta, t, k, z)

                    hashes = write_vsi(pmeta, frames, fs_out, bands=bands, progress=prog, cancel=cancel)
                if options.write_sidecar:
                    write_sidecar(os.path.splitext(fs_out)[0] + ".metadata.json", pmeta, raw, out)
                if options.validate:
                    if progress:
                        progress(share * (n_part + 1) - 0.01, "Validating")
                    log.info("Validating %s", out)
                    checks = (
                        validate(fs_out, pmeta, hashes, text_info=options.embed_text_info)
                        if fmt.key == "nd2"
                        else validate_vsi(fs_out, pmeta, hashes)
                    )
                    if p is not None:
                        for c in checks:
                            c.group = f"POSITION {p + 1} · {c.group}"
                    res.checks += checks
        res.unmapped = unmapped_items(meta, options.embed_text_info) if fmt.key == "nd2" else unmapped_items_vsi(meta)
        res.ok = not res.failed_checks
        if res.failed_checks:
            res.error = f"{len(res.failed_checks)} validation check(s) failed"
            log.error("%s: %s: %s", res.output_path, res.error,
                      "; ".join(f"{c.label}: {c.source} -> {c.output}" for c in res.failed_checks))
        else:
            log.info("Done: %s (%.1f s)", res.output_path, time.perf_counter() - t0)
    except Cancelled:
        res.cancelled = True
        res.error = "Cancelled by user"
        res.output_path = ""
        log.warning("Cancelled: %s [%s]", os.path.basename(source), res.series_name)
    except Exception as exc:  # reported per job; the queue carries on
        res.error = f"{type(exc).__name__}: {exc}"
        log.exception("Failed: %s [%s]", os.path.basename(source), res.series_name)
    res.seconds = time.perf_counter() - t0
    res.report_text = format_report(res, meta)
    if options.write_report and res.output_path and os.path.exists(long_path(res.output_path)):
        report = os.path.splitext(res.output_path)[0]
        if len(res.outputs) > 1:
            report = report.rsplit("_", 1)[0]
        with open(long_path(report + ".report.txt"), "w", encoding="utf-8") as fh:
            fh.write(res.report_text)
    if progress:
        progress(1.0, "Done" if res.ok else (res.error or "Finished"))
    return res
