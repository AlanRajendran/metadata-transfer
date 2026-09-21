# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical metadata + frames -> Evident/Olympus cellSens `.vsi` (+ `_<name>_` folder).

Output layout, as cellSens writes it:

    <name>.vsi                     tag tree + JPEG thumbnail (formats/vsi_document.py)
    _<name>_/stack1/frame_t_0.ets  pixels, raw tiles (formats/ets_writer.py)

One layer with the dimensions T, Z, C. Multipoint sources are split into one `.vsi` per stage
position by the caller (convert.py), which is how cellSens stores multi-position experiments.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import math
import os
import shutil
from typing import Callable, Iterable

import numpy as np

from ..formats import vsi_document as doc
from ..formats.ets_writer import EtsWriter
from ..formats.vsi_thumbnail import build_tiff_block, encode_jpeg, render_thumbnail
from ..model import SeriesMetadata
from .nd2_limnd2 import BAND_THRESHOLD_BYTES, Cancelled

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]
BandsFn = Callable[[SeriesMetadata, int, int, int], Iterable[tuple[int, np.ndarray]]]

THUMB_SAMPLE = 1024  # sampled plane for the thumbnail and the display ranges


def companion_dir(vsi_path: str) -> str:
    folder, name = os.path.split(os.path.abspath(vsi_path))
    return os.path.join(folder, f"_{os.path.splitext(name)[0]}_")


def vsi_outputs_exist(vsi_path: str) -> bool:
    return os.path.exists(vsi_path) or os.path.exists(companion_dir(vsi_path))


def _display_limits(meta: SeriesMetadata, samples: list[np.ndarray]) -> list[tuple[float, float]]:
    full = float(2 ** max(1, min(meta.bits, np.dtype(meta.dtype).itemsize * 8)) - 1)
    out = []
    for ch, s in zip(meta.channels, samples):
        if ch.display_range is not None:
            lo, hi = ch.display_range
            out.append((lo * full, hi * full))
            continue
        if s.size:
            lo, hi = np.percentile(s, [0.1, 99.9])
            if hi <= lo:
                hi = lo + 1
            out.append((float(lo), float(hi)))
        else:
            out.append((0.0, full))
    return out


def _spec(meta: SeriesMetadata, limits: list[tuple[float, float]]) -> doc.DocumentSpec:
    d = meta.output_dims
    cal = meta.output_calibration
    acq = meta.acquisition
    channels = []
    for ch, lim in zip(meta.channels, limits):
        channels.append(
            doc.ChannelSpec(
                name=ch.name,
                transmitted=ch.modality == "brightfield",
                excitation_nm=ch.excitation_nm,
                emission_nm=ch.emission_nm,
                colour=tuple(int(v) for v in ch.color_rgb),
                display=lim,
                exposure_us=int(round(ch.exposure_ms * 1000)) if ch.exposure_ms is not None else None,
            )
        )
    zpos = meta.z_positions_um
    z_start = zpos[0] if zpos else acq.stage_z_um
    z_step = (zpos[1] - zpos[0]) if zpos and len(zpos) > 1 else cal.z_step_um
    times = acq.frame_times_s or []
    n = d.t * d.z
    frame_ms = [t * 1000.0 for t in times[:n]] if len(times) >= n else []
    stage = None
    pos = meta.positions[0] if meta.positions else None
    sx = pos.x_um if pos is not None and pos.x_um is not None else acq.stage_x_um
    sy = pos.y_um if pos is not None and pos.y_um is not None else acq.stage_y_um
    if sx is not None and sy is not None:
        stage = (sx, sy)
    cam = next((ch.detector for ch in meta.channels if ch.detector), "")
    return doc.DocumentSpec(
        layer_name=", ".join(ch.name for ch in meta.channels),
        width=d.x,
        height=d.y,
        t=d.t,
        z=d.z,
        channels=channels,
        pixel_size_um=(cal.pixel_size_x_um or 1.0, cal.pixel_size_y_um or cal.pixel_size_x_um or 1.0),
        stage_centre_um=stage,
        z_start_um=z_start,
        z_step_um=z_step,
        time_step_s=cal.time_step_s,
        frame_times_ms=frame_ms,
        created=acq.start,
        objective_name=meta.objective.name,
        objective_magnification=meta.objective.magnification,
        objective_na=meta.objective.numerical_aperture,
        objective_ri=meta.objective.refractive_index,
        camera=cam,
        microscope=acq.microscope,
        bit_depth=min(meta.bits, np.dtype(meta.dtype).itemsize * 8),
        experiment=str(meta.vendor_raw.get("experiment") or meta.series_name),
    )


def write_vsi(
    meta: SeriesMetadata,
    frames: Iterable[tuple[int, int, int, np.ndarray]],
    out_path: str,
    *,
    bands: BandsFn | None = None,
    band_threshold_bytes: int = BAND_THRESHOLD_BYTES,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> list[str]:
    """Write `out_path` (.vsi) and its companion folder. Returns the SHA-1 of every source frame
    (row-major Y, X, C bytes) in T -> Z order, like `write_nd2`."""
    d = meta.output_dims
    if d.p != 1:
        raise ValueError("a .vsi holds one stage position; split multipoint data first")
    dtype = np.dtype(meta.dtype)
    if dtype not in (np.dtype("uint8"), np.dtype("uint16")):
        raise ValueError(f"cellSens files hold 8- or 16-bit images, not {dtype}")
    n_frames = d.t * d.z
    companion = companion_dir(out_path)
    stack = os.path.join(companion, "stack1")
    created_dir = not os.path.exists(companion)
    os.makedirs(stack, exist_ok=True)
    ets_path = os.path.join(stack, "frame_t_0.ets")
    ets = EtsWriter(ets_path + ".part", d.x, d.y, (d.t, d.z, d.c), dtype)
    stride = max(1, math.ceil(max(d.x, d.y) / THUMB_SAMPLE))
    thumb_key = (0, d.z // 2)
    samples: list[list[np.ndarray]] = [[] for _ in range(d.c)]
    hashes: list[str] = []
    frame_bytes = d.x * d.y * d.c * dtype.itemsize
    use_bands = bands is not None and frame_bytes > band_threshold_bytes
    part = out_path + ".part"
    try:

        def emit(t: int, z: int, parts: Iterable[tuple[int, np.ndarray]]) -> None:
            planes = [ets.plane((t, z, c)) for c in range(d.c)]
            sha = hashlib.sha1()
            rows = 0
            for y0, band in parts:
                if cancel is not None and cancel():
                    raise Cancelled()
                band = np.ascontiguousarray(band, dtype=dtype)
                if band.ndim == 2:
                    band = band[..., None]
                sha.update(band.tobytes())
                for c in range(d.c):
                    planes[c].add_rows(band[..., c])
                if (t, z) == thumb_key:
                    first = (-y0) % stride
                    for c in range(d.c):
                        samples[c].append(band[first::stride, ::stride, c])
                rows += band.shape[0]
                if progress is not None:
                    i = len(hashes)
                    progress(0.97 * (i + min(1.0, rows / d.y)) / n_frames, f"Writing plane {i + 1}/{n_frames}")
            if rows != d.y:
                raise RuntimeError(f"Frame (t={t}, z={z}): {rows} of {d.y} rows")
            for pw in planes:
                pw.finish()
            hashes.append(sha.hexdigest())

        if use_bands:
            assert bands is not None
            for t in range(d.t):
                for z in range(d.z):
                    emit(t, z, bands(meta, t, 0, z))
        else:
            for t, _p, z, frame in frames:
                emit(t, z, [(0, frame)])
        if len(hashes) != n_frames:
            raise RuntimeError(f"Expected {n_frames} frames but the reader produced {len(hashes)}.")
        ets.close()
        os.replace(ets_path + ".part", ets_path)

        sampled = [np.concatenate(s) if s else np.zeros((1, 1), dtype) for s in samples]
        limits = _display_limits(meta, sampled)
        rgb = render_thumbnail(sampled, [ch.color_rgb for ch in meta.channels], limits)
        jpeg = encode_jpeg(rgb)
        spec = _spec(meta, limits)
        when = meta.acquisition.start or _dt.datetime.now()
        cam = spec.camera

        def block(offset: int):
            return build_tiff_block(offset, jpeg, rgb.shape[1], rgb.shape[0], d.x, d.y, when, "", cam)

        size = len(block(0)[0])
        root = doc.build(spec, size)
        data = doc.tree_bytes(root, block)
        with open(part, "wb") as fh:
            fh.write(data)
        os.replace(part, out_path)
        if progress is not None:
            progress(1.0, "Written")
    except BaseException:
        ets.abort()
        for p in (part, ets_path + ".part"):
            try:
                os.remove(p)
            except OSError:
                pass
        if created_dir:
            shutil.rmtree(companion, ignore_errors=True)
        raise
    return hashes
