# SPDX-License-Identifier: GPL-3.0-or-later
"""Canonical metadata + frames -> Nikon ND2 using Laboratory Imaging's `limnd2`.

Layout mirrors native NIS-Elements files: one frame per (t, p, z) with all channels
interleaved (Y, X, C); experiment loops = [Time] + [XY positions] + [Z-stack].
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import os
from typing import Callable, Iterable

import limnd2
import numpy as np
from limnd2.attributes import ImageAttributes
from limnd2.base import ND2_CHUNK_NAME_AcqTimesCache, ND2_CHUNK_NAME_ImageTextInfoLV
from limnd2.experiment_factory import ExperimentFactory
from limnd2.lite_variant import ELxLiteVariantType as LVType
from limnd2.lite_variant import encode_lv
from limnd2.metadata import PicturePlaneModalityFlags as MF
from limnd2.metadata_factory import MetadataFactory

from .. import APP_NAME, __version__
from ..mapping.colors import rgb_to_hex
from ..model import SeriesMetadata
from ..readers.evident_vsi.layers import parse_filter_band

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


class Cancelled(Exception):
    pass


_MODE_FLAGS = {
    "laser_scanning_confocal": MF.modLaserScanConfocal,
    "spinning_disk": MF.modSpinDiskConfocal,
    "widefield": MF.modCamera,
}
_MODE_TEXT = {
    "laser_scanning_confocal": "Laser Scanning Confocal",
    "spinning_disk": "Spinning Disk Confocal",
    "widefield": "Widefield",
}


def modality_flags(meta: SeriesMetadata, modality: str, channel_name: str = "") -> MF:
    mode = _MODE_FLAGS.get(meta.imaging_mode, MF.modUnknown)
    if meta.imaging_mode == "spinning_disk":
        mode |= MF.modCamera
    if modality == "brightfield":
        flags = MF.modBrightfield | mode
        if meta.imaging_mode == "laser_scanning_confocal":
            flags |= MF.modTransmitDetector
        if channel_name.strip().upper() == "DIC" or "DIC" in channel_name.upper().split():
            flags |= MF.modDIContrast
        return flags
    return MF.modFluorescence | mode


#: frames larger than this are written in horizontal bands (streamed) instead of whole
BAND_THRESHOLD_BYTES = 512 << 20


def _jdn(d: _dt.datetime) -> float:
    """Julian day number (with fraction) as used by NIS-Elements; naive times kept as recorded."""
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.timestamp() / 86400.0 + 2440587.5


def _fmt(v: float | None, spec: str = ".4g", unit: str = "") -> str:
    return "n/a" if v is None else f"{v:{spec}}{unit}"


def build_text_info(meta: SeriesMetadata) -> dict[str, str]:
    """Human-readable description in the style of NIS-Elements' own 'Metadata:' block."""
    d = meta.output_dims
    cal = meta.output_calibration
    dims = [f"T({d.t})"] if d.t > 1 else []
    dims += [f"XY({d.p})"] if d.p > 1 else []
    dims += [f"Z({d.z})"] if d.z > 1 else []
    dims += [f"λ({d.c})"]
    ob = meta.objective
    acq = meta.acquisition
    lines = [
        "Metadata:",
        f"Converted from: {os.path.basename(meta.source_path)} / {meta.series_name} ({meta.source_format})",
        f"Converted by: {APP_NAME} {__version__} on {_dt.datetime.now():%d/%m/%Y %H:%M}",
        f"Microscope: {acq.microscope or 'n/a'}" + (f", {acq.software}" if acq.software else ""),
        f"Dimensions: {' x '.join(dims)}",
        f"Objective: {ob.name or 'n/a'} (Mag {_fmt(ob.magnification)}x, NA {_fmt(ob.numerical_aperture)}, "
        f"RI {_fmt(ob.refractive_index)}{', ' + ob.immersion if ob.immersion else ''})",
        f"Pixel size: {_fmt(cal.pixel_size_x_um, '.5g')} x {_fmt(cal.pixel_size_y_um, '.5g')} µm",
    ]
    if d.z > 1:
        lines.append(f"Z step: {_fmt(cal.z_step_um, '.5g')} µm")
    if d.t > 1:
        lines.append(f"Time step: {_fmt(cal.time_step_s, '.5g')} s")
    if meta.pyramid_levels:
        lines.append(f"Resolution: level {meta.level} of {len(meta.pyramid_levels)} (1/{2 ** meta.level} of full resolution)")
    if meta.zoom is not None:
        lines.append(f"Zoom: {meta.zoom:.2f}")
    if meta.pinhole_um is not None:
        lines.append(f"Pinhole: {meta.pinhole_um:.1f} µm")
    lines.append(f"Number of Picture Planes: {len(meta.channels)}")
    for i, ch in enumerate(meta.channels, 1):
        lines.append(f"Plane #{i}:")
        lines.append(f" Name: {ch.name}")
        lines.append(f" Modality: {_MODE_TEXT.get(meta.imaging_mode, meta.imaging_mode)}, {ch.modality.title()}")
        if ch.excitation_nm:
            pct = f" ({ch.laser_intensity_pct:.1f} %)" if ch.laser_intensity_pct is not None else ""
            lines.append(f" Excitation: {ch.excitation_nm:.0f} nm{pct}")
        if ch.emission_range_nm:
            lo, hi = ch.emission_range_nm
            lines.append(f" Emission: {lo:.0f}-{hi:.0f} nm" if hi > lo else f" Emission: {lo:.0f} nm")
        if ch.filter_name:
            fb = parse_filter_band(ch.filter_name)
            lines.append(f" Emission filter: {ch.filter_name}" + (f" ({fb[0]:.0f}-{fb[1]:.0f} nm)" if fb else ""))
        if ch.exposure_ms is not None:
            lines.append(f" Exposure: {ch.exposure_ms:g} ms" + (f" ({ch.exposure_ms / 1000:g} s)" if ch.exposure_ms >= 1000 else ""))
        if ch.detector:
            extra = []
            if ch.detector_gain is not None:
                extra.append(f"gain {ch.detector_gain:.1f}")
            if ch.detector_offset is not None:
                extra.append(f"offset {ch.detector_offset:.2f}")
            lines.append(f" Detector: {ch.detector}" + (f" ({', '.join(extra)})" if extra else ""))
        if ch.pinhole_um is not None:
            lines.append(f" Pinhole: {ch.pinhole_um:.1f} µm")
        if ch.sequential_index is not None:
            lines.append(f" Sequential setting: {ch.sequential_index + 1}")
        if ch.auto_name and ch.auto_name != ch.name:
            lines.append(f" Settings: {ch.auto_name}")
    if meta.scan_settings:
        lines.append("Scan settings:")
        lines += [f" {k}: {v}" for k, v in meta.scan_settings.items()]
    if d.p > 1 and meta.positions:
        lines.append("Stage positions:")
        for k, pos in enumerate(meta.positions, 1):
            if pos.x_um is not None:
                lines.append(f" #{k}{' ' + pos.name if pos.name else ''}: X {pos.x_um:.1f} µm, Y {pos.y_um:.1f} µm, Z {_fmt(pos.z_um, '.1f')} µm")
    elif acq.stage_x_um is not None:
        lines.append(f"Stage position: X {acq.stage_x_um:.1f} µm, Y {acq.stage_y_um:.1f} µm, Z {_fmt(acq.stage_z_um, '.1f')} µm")

    capturing = "\r\n".join(
        f"Plane #{i}: {ch.detector or 'n/a'}"
        + (f", gain {ch.detector_gain:.1f}" if ch.detector_gain is not None else "")
        + (f", exposure {ch.exposure_ms:g} ms" if ch.exposure_ms is not None else "")
        for i, ch in enumerate(meta.channels, 1)
    )
    return {
        "description": "\r\n".join(lines),
        "capturing": capturing,
        "optics": ob.name,
        "date": f"{acq.start:%d/%m/%Y  %H:%M:%S}" if acq.start else "",
        "info1": f"Source: {meta.source_path} :: {meta.series_name}",
    }


_TEXT_KEYS = {  # ImageTextInfo item index for each field
    "imageId": 0, "type": 1, "group": 2, "sampleId": 3, "author": 4, "description": 5, "capturing": 6,
    "sampling": 7, "location": 8, "date": 9, "conclusion": 10, "info1": 11, "info2": 12, "optics": 13,
}


def encode_text_info(fields: dict[str, str]) -> bytes:
    items = {f"TextInfoItem_{i}": ("", LVType.STRING) for i in range(14)}
    for key, value in fields.items():
        items[f"TextInfoItem_{_TEXT_KEYS[key]}"] = (value or "", LVType.STRING)
    return encode_lv({"SLxImageTextInfo": items})


def _filter_name(ch) -> str | None:
    band = None
    if ch.emission_range_nm and ch.emission_range_nm[1] > ch.emission_range_nm[0]:
        band = f"{ch.emission_range_nm[0]:.0f}-{ch.emission_range_nm[1]:.0f} nm"
    if ch.filter_name:
        fb = parse_filter_band(ch.filter_name)
        if fb is not None:
            band = f"{fb[0]:.0f}-{fb[1]:.0f} nm"
        return f"{ch.filter_name} ({band})" if band else ch.filter_name
    return band


def build_picture_metadata(meta: SeriesMetadata):
    cal = meta.output_calibration
    ob = meta.objective
    common = {
        "objective_magnification": ob.magnification,
        "objective_numerical_aperture": ob.numerical_aperture,
        "immersion_refractive_index": ob.refractive_index,
        "zoom_magnification": meta.zoom,
        "pinhole_diameter": meta.pinhole_um,
        "microscope_name": meta.acquisition.microscope or None,
    }
    mf = MetadataFactory(
        pixel_calibration=cal.pixel_size_x_um if cal.pixel_size_x_um else -1.0,
        **{k: v for k, v in common.items() if v is not None},
    )
    for ch in meta.channels:
        plane = {
            "name": ch.name,
            "modality": modality_flags(meta, ch.modality, ch.name),
            "color": rgb_to_hex(ch.color_rgb),
            "excitation_wavelength": int(round(ch.excitation_nm)) if ch.excitation_nm else 0,
            "emission_wavelength": int(round(ch.emission_nm)) if ch.emission_nm else 0,
            "camera_name": ch.detector or None,
            "filter_name": _filter_name(ch),
            "pinhole_diameter": ch.pinhole_um,
            "acquisition_time": meta.acquisition.start,
        }
        mf.addPlane({k: v for k, v in plane.items() if v is not None})
    md = mf.createMetadata()

    # limnd2 names the objective after its magnification ("20x") and rounds the absolute
    # time to whole days; patch both so NIS shows the real objective and date/time.
    pp = md.sPicturePlanes
    changes = {}
    if ob.name:
        settings = [
            dataclasses.replace(s, pObjectiveSetting=dataclasses.replace(s.pObjectiveSetting, wsObjectiveName=ob.name))
            for s in pp.sSampleSetting
        ]
        changes["sPicturePlanes"] = dataclasses.replace(pp, sSampleSetting=settings)
        changes["wsObjectiveName"] = ob.name
    if meta.acquisition.start:
        changes["dTimeAbsolute"] = _jdn(meta.acquisition.start)
    if cal.pixel_size_x_um and cal.pixel_size_y_um and abs(cal.pixel_size_x_um - cal.pixel_size_y_um) > 1e-9:
        changes["dAspect"] = cal.pixel_size_y_um / cal.pixel_size_x_um
    return dataclasses.replace(md, **changes) if changes else md


def build_experiment(meta: SeriesMetadata):
    d = meta.output_dims
    cal = meta.output_calibration
    ef = ExperimentFactory()
    if d.t > 1:
        ef.t.count = d.t
        ef.t.step = (cal.time_step_s or 0.0) * 1000.0  # ms
    if d.p > 1:
        for k in range(d.p):
            pos = meta.positions[k] if k < len(meta.positions) else None
            ef.m.addPoint(float(pos.x_um or 0.0) if pos else 0.0, float(pos.y_um or 0.0) if pos else 0.0)
    if d.z > 1:
        ef.z.count = d.z
        ef.z.step = cal.z_step_um or 0.0
        if meta.z_positions_um and len(meta.z_positions_um) == d.z:
            ef.z.start = min(meta.z_positions_um)
            ef.z.end = max(meta.z_positions_um)
    return ef.createExperiment()


BandsFn = Callable[[SeriesMetadata, int, int, int], Iterable[tuple[int, np.ndarray]]]


def write_nd2(
    meta: SeriesMetadata,
    frames: Iterable[tuple[int, int, int, np.ndarray]],
    out_path: str,
    *,
    bands: BandsFn | None = None,
    band_threshold_bytes: int = BAND_THRESHOLD_BYTES,
    embed_text_info: bool = True,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> list[str]:
    """Write the ND2 (via a .part file, renamed on success). Returns SHA-1 of every source frame
    (row-major Y, X, C bytes).

    `frames` yields whole (t, p, z, frame[Y, X, C]) frames. When `bands` is given and one frame
    exceeds `band_threshold_bytes`, frames are instead streamed as full-width horizontal bands
    from `bands(meta, t, p, z)` -> (y0, band[Yb, X, C]) so multi-GB planes never sit in memory.
    """
    d = meta.output_dims
    n_frames = d.n_frames
    dtype = np.dtype(meta.dtype)
    bits = min(meta.bits, dtype.itemsize * 8)
    part = out_path + ".part"
    hashes: list[str] = []
    times = meta.acquisition.frame_times_s
    frame_bytes = d.x * d.y * d.c * dtype.itemsize
    use_bands = bands is not None and frame_bytes > band_threshold_bytes
    try:
        with limnd2.Nd2Writer(part) as w:
            w.imageAttributes = ImageAttributes.create(
                height=d.y, width=d.x, component_count=d.c, bits=bits, sequence_count=n_frames
            )
            exp = build_experiment(meta)
            if exp is not None:
                w.experiment = exp
            w.pictureMetadata = build_picture_metadata(meta)
            if embed_text_info:
                w.setChunk(ND2_CHUNK_NAME_ImageTextInfoLV, encode_text_info(build_text_info(meta)))
            if use_bands:
                assert bands is not None
                for i, (t, p, z) in enumerate(d.frame_keys()):
                        if cancel is not None and cancel():
                            raise Cancelled()
                        acq_ms = times[i] * 1000.0 if times and i < len(times) else -1.0
                        sha = hashlib.sha1()
                        rows = 0
                        for y0, band in bands(meta, t, p, z):
                            if cancel is not None and cancel():
                                raise Cancelled()
                            band = np.ascontiguousarray(band, dtype=dtype)
                            if band.ndim == 2:
                                band = band[..., None]
                            sha.update(band.tobytes())
                            try:
                                w.chunker.setImageTile(i, 0, y0, band, acqtime=acq_ms)
                            except TypeError:
                                w.setImageTile(i, 0, y0, band)
                            rows += band.shape[0]
                            if progress is not None:
                                progress(
                                    (i + min(1.0, rows / d.y)) / n_frames,
                                    f"Writing frame {i + 1}/{n_frames}: row {rows}/{d.y}",
                                )
                        if rows != d.y:
                            raise RuntimeError(f"Frame {i}: bands covered {rows} of {d.y} rows.")
                        hashes.append(sha.hexdigest())
            else:
                for i, (_t, _p, _z, frame) in enumerate(frames):
                    if cancel is not None and cancel():
                        raise Cancelled()
                    frame = np.ascontiguousarray(frame, dtype=dtype)
                    hashes.append(hashlib.sha1(frame.tobytes()).hexdigest())
                    acq_ms = times[i] * 1000.0 if times and i < len(times) else -1.0
                    try:
                        w.chunker.setImage(i, frame, acqtime=acq_ms)
                    except TypeError:
                        w.setImage(i, frame)
                    if progress is not None:
                        progress((i + 1) / n_frames, f"Writing frame {i + 1}/{n_frames}")
            if times and len(times) >= n_frames:
                # per-frame acquisition times (ms), read by NIS-Elements and the nd2 package
                w.setChunk(ND2_CHUNK_NAME_AcqTimesCache, np.asarray(times[:n_frames], dtype=np.float64).__mul__(1000.0).tobytes())
        if len(hashes) != n_frames:
            raise RuntimeError(f"Expected {n_frames} frames but the reader produced {len(hashes)}.")
        os.replace(part, out_path)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return hashes
