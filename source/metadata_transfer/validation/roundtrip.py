# SPDX-License-Identifier: GPL-3.0-or-later
"""Re-open the written ND2 with the independent `nd2` reader and compare with the source."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import nd2
import numpy as np

from ..model import SeriesMetadata


@dataclass
class Check:
    group: str
    label: str
    source: str
    output: str
    ok: bool | None  # None = informational

    @property
    def symbol(self) -> str:
        return {True: "✓", False: "✗", None: "!"}[self.ok]


def _num(a: float | None, b: float | None, rel: float = 1e-4, abs_tol: float = 1e-9) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b)))


def _s(v, spec: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:{spec}}" if spec and isinstance(v, (int, float)) else str(v)


def _frame_sha1(arr: np.ndarray, band_bytes: int = 64 << 20) -> str:
    """SHA-1 of the row-major (Y, X, C) bytes, hashed in horizontal bands so that frames of
    several GB never need a contiguous copy (nd2 returns a memory-mapped view)."""
    h = hashlib.sha1()
    row_bytes = max(1, int(np.prod(arr.shape[1:])) * arr.dtype.itemsize)
    rows = max(1, band_bytes // row_bytes)
    for y in range(0, arr.shape[0], rows):
        h.update(np.ascontiguousarray(arr[y : y + rows]).tobytes())
    return h.hexdigest()


def validate(out_path: str, meta: SeriesMetadata, frame_hashes: list[str], *, text_info: bool) -> list[Check]:
    checks: list[Check] = []
    d = meta.output_dims
    cal = meta.output_calibration

    def add(group, label, src, out, ok):
        checks.append(Check(group, label, _s(src), _s(out), ok))

    with nd2.ND2File(out_path) as f:
        sizes = dict(f.sizes)
        g = "IMAGE DATA"
        for key, val in (("X", d.x), ("Y", d.y), ("Z", d.z), ("C", d.c), ("T", d.t), ("P", d.p)):
            got = sizes.get(key, 1)
            add(g, key, val, got, val == got)
        add(g, "dtype", meta.dtype, str(f.dtype), str(f.dtype) == meta.dtype)
        sig = f.attributes.bitsPerComponentSignificant
        add(g, "bits", meta.bits, sig, sig == meta.bits)

        n = d.n_frames
        bad = 0
        for i in range(n):
            arr = np.asarray(f.read_frame(i))
            if arr.ndim == 2:
                arr = arr[..., None]
            elif arr.shape[0] == d.c and arr.shape[-1] != d.c:
                arr = np.moveaxis(arr, 0, -1)
            if i >= len(frame_hashes) or _frame_sha1(arr) != frame_hashes[i]:
                bad += 1
        add(g, "pixel checksum", f"{n} frames", "identical" if bad == 0 else f"{bad} differ", bad == 0)
        if meta.pyramid_levels:
            add(g, "resolution level", f"{meta.level} of {len(meta.pyramid_levels)}", f"{d.x}×{d.y} px", None)

        g = "CALIBRATION"
        vs = f.voxel_size()
        add(g, "pixel size X (µm)", cal.pixel_size_x_um, vs.x, _num(cal.pixel_size_x_um, vs.x))
        add(g, "pixel size Y (µm)", cal.pixel_size_y_um, vs.y, _num(cal.pixel_size_y_um, vs.y))
        if d.z > 1:
            add(g, "Z step (µm)", cal.z_step_um, vs.z, _num(cal.z_step_um, vs.z))
        if d.t > 1:
            tl = next((lp for lp in f.experiment if lp.type == "TimeLoop"), None)
            got = tl.parameters.periodMs / 1000.0 if tl is not None else None
            add(g, "time step (s)", cal.time_step_s, got, _num(cal.time_step_s, got, rel=1e-3))

        if d.p > 1:
            g = "STAGE POSITIONS"
            loop = next((lp for lp in f.experiment if lp.type == "XYPosLoop"), None)
            pts = loop.parameters.points if loop is not None else []
            for k, pos in enumerate(meta.positions[: d.p]):
                got = pts[k].stagePositionUm if k < len(pts) else None
                ok = got is not None and _num(pos.x_um, got.x, abs_tol=0.05) and _num(pos.y_um, got.y, abs_tol=0.05)
                add(g, f"stage position {k + 1} (µm)",
                    f"{_s(pos.x_um, '.1f')}, {_s(pos.y_um, '.1f')}",
                    f"{_s(got.x, '.1f')}, {_s(got.y, '.1f')}" if got is not None else None, ok)

        g = "CHANNELS"
        out_channels = f.metadata.channels or []
        for i, ch in enumerate(meta.channels):
            if i >= len(out_channels):
                add(g, f"#{i + 1}", ch.name, None, False)
                continue
            oc = out_channels[i]
            add(g, f"#{i + 1} name", ch.name, oc.channel.name, ch.name == oc.channel.name)
            if ch.excitation_nm:
                got = oc.channel.excitationLambdaNm
                add(g, f"#{i + 1} excitation (nm)", round(ch.excitation_nm), got, _num(round(ch.excitation_nm), got, abs_tol=0.6))
            if ch.emission_nm:
                got = oc.channel.emissionLambdaNm
                add(g, f"#{i + 1} emission (nm)", round(ch.emission_nm), got, _num(round(ch.emission_nm), got, abs_tol=0.6))
            c = oc.channel.color
            got_rgb = (c.r, c.g, c.b)
            add(g, f"#{i + 1} colour", ch.color_name or ch.color_rgb, got_rgb, tuple(ch.color_rgb) == got_rgb)
            flags = oc.microscope.modalityFlags or []
            want = "brightfield" if ch.modality == "brightfield" else "fluorescence"
            add(g, f"#{i + 1} modality", ch.modality, ", ".join(flags), want in flags)

        g = "OBJECTIVE / OPTICS"
        mic = out_channels[0].microscope if out_channels else None
        ob = meta.objective
        if mic is not None:
            if ob.name:
                add(g, "objective", ob.name, mic.objectiveName, ob.name == mic.objectiveName)
            if ob.magnification is not None:
                add(g, "magnification", ob.magnification, mic.objectiveMagnification, _num(ob.magnification, mic.objectiveMagnification))
            if ob.numerical_aperture is not None:
                add(g, "NA", ob.numerical_aperture, mic.objectiveNumericalAperture, _num(ob.numerical_aperture, mic.objectiveNumericalAperture))
            if ob.refractive_index is not None:
                add(g, "refractive index", ob.refractive_index, mic.immersionRefractiveIndex, _num(ob.refractive_index, mic.immersionRefractiveIndex))
            if meta.zoom is not None:
                add(g, "zoom", round(meta.zoom, 3), mic.zoomMagnification, _num(meta.zoom, mic.zoomMagnification, rel=1e-3))
            src_ph = meta.channels[0].pinhole_um if meta.channels and meta.channels[0].pinhole_um else meta.pinhole_um
            if src_ph is not None:
                got = mic.pinholeDiameterUm
                add(g, "pinhole (µm)", round(src_ph, 2), None if got is None else round(got, 2), _num(src_ph, got, rel=1e-3))

        if text_info:
            ti = f.text_info or {}
            add("TEXT INFO", "description embedded", "yes", "yes" if ti.get("description") else "no", bool(ti.get("description")))
    return checks


def unmapped_items(meta: SeriesMetadata, text_info: bool) -> list[str]:
    """Source metadata with no structured ND2 field (kept in text info / sidecar)."""
    where = "ND2 description + sidecar" if text_info else "sidecar"
    items = []
    if any(ch.laser_intensity_pct is not None for ch in meta.channels):
        items.append(f"laser intensities → {where}")
    if any(ch.detector_gain is not None for ch in meta.channels):
        items.append(f"detector gain/offset → {where}")
    if meta.scan_settings:
        items.append(f"scan settings ({', '.join(meta.scan_settings)}) → {where}")
    if any(ch.emission_range_nm for ch in meta.channels):
        items.append("emission windows → ND2 stores the centre wavelength; full range in filter name + " + where)
    if meta.acquisition.stage_x_um is not None:
        items.append(f"stage X/Y position → {where}")
    if any(ch.exposure_ms is not None for ch in meta.channels):
        items.append(f"camera exposure times → {where}")
    if any(ch.filter_name for ch in meta.channels):
        items.append("emission filter names → ND2 filter name + " + where)
    if any(ch.display_range for ch in meta.channels):
        items.append("display ranges (vendor brightness/contrast) → sidecar")
    items.append("vendor hardware details and the complete original metadata → sidecar")
    return items
