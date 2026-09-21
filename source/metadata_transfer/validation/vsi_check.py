# SPDX-License-Identifier: GPL-3.0-or-later
"""Re-open a written `.vsi` with this app's cellSens reader and compare it with the source.

Pixels are compared frame by frame (SHA-1 of the row-major Y, X, C bytes, as written); metadata
field by field. The reader was validated against cellSens/OlyVIA on real files, so a clean check
here means cellSens sees the same values.
"""

from __future__ import annotations

import hashlib

import numpy as np

from ..model import SeriesMetadata
from .roundtrip import Check, _num, _s


def validate_vsi(out_path: str, meta: SeriesMetadata, frame_hashes: list[str]) -> list[Check]:
    from ..readers.evident_vsi.reader import EvidentVsiReader

    checks: list[Check] = []

    def add(group, label, src, out, ok):
        checks.append(Check(group, label, _s(src), _s(out), ok))

    d = meta.output_dims
    cal = meta.output_calibration
    with EvidentVsiReader(out_path) as r:
        infos = r.list_series()
        if len(infos) != 1 or not infos[0].supported:
            add("IMAGE DATA", "readable", "1 layer", infos[0].reason if infos else "no layer", False)
            return checks
        m = r.metadata(0)
        o = m.dims
        g = "IMAGE DATA"
        for key, a, b in (("X", d.x, o.x), ("Y", d.y, o.y), ("Z", d.z, o.z), ("C", d.c, o.c), ("T", d.t, o.t)):
            add(g, key, a, b, a == b)
        add(g, "dtype", meta.dtype, m.dtype, m.dtype == meta.dtype)
        want_bits = min(meta.bits, np.dtype(meta.dtype).itemsize * 8)
        add(g, "bits", want_bits, m.bits, m.bits == want_bits)
        bad = 0
        n = 0
        for i, (_t, _p, _z, frame) in enumerate(r.iter_frames(m)):
            n += 1
            h = hashlib.sha1()
            for y in range(0, frame.shape[0], 1024):
                h.update(np.ascontiguousarray(frame[y : y + 1024]).tobytes())
            if i >= len(frame_hashes) or h.hexdigest() != frame_hashes[i]:
                bad += 1
        add(g, "pixel checksum", f"{len(frame_hashes)} frames", "identical" if bad == 0 and n == len(frame_hashes) else f"{bad} differ", bad == 0 and n == len(frame_hashes))

        g = "CALIBRATION"
        oc = m.calibration
        add(g, "pixel size X (µm)", cal.pixel_size_x_um, oc.pixel_size_x_um, _num(cal.pixel_size_x_um, oc.pixel_size_x_um))
        add(g, "pixel size Y (µm)", cal.pixel_size_y_um, oc.pixel_size_y_um, _num(cal.pixel_size_y_um, oc.pixel_size_y_um))
        if d.z > 1:
            add(g, "Z step (µm)", cal.z_step_um, oc.z_step_um, _num(cal.z_step_um, oc.z_step_um, rel=1e-3))
        if d.t > 1 and cal.time_step_s:
            add(g, "time step (s)", cal.time_step_s, oc.time_step_s, _num(cal.time_step_s, oc.time_step_s, rel=1e-3))
        pos = meta.positions[0] if meta.positions else None
        sx = pos.x_um if pos is not None and pos.x_um is not None else meta.acquisition.stage_x_um
        sy = pos.y_um if pos is not None and pos.y_um is not None else meta.acquisition.stage_y_um
        if sx is not None and sy is not None:
            ox, oy = m.acquisition.stage_x_um, m.acquisition.stage_y_um
            add(g, "stage position (µm)", f"{sx:.1f}, {sy:.1f}", f"{_s(ox, '.1f')}, {_s(oy, '.1f')}",
                _num(sx, ox, abs_tol=0.05) and _num(sy, oy, abs_tol=0.05))

        g = "CHANNELS"
        for i, ch in enumerate(meta.channels):
            if i >= len(m.channels):
                add(g, f"#{i + 1}", ch.name, None, False)
                continue
            oc_ = m.channels[i]
            add(g, f"#{i + 1} name", ch.name, oc_.name, ch.name == oc_.name)
            bf = ch.modality == "brightfield"
            add(g, f"#{i + 1} type", "transmitted" if bf else "fluorescence", oc_.modality.replace("brightfield", "transmitted"),
                oc_.modality == ch.modality)
            if ch.excitation_nm and not bf:
                add(g, f"#{i + 1} excitation (nm)", round(ch.excitation_nm), oc_.excitation_nm, _num(round(ch.excitation_nm), oc_.excitation_nm, abs_tol=0.6))
            if ch.emission_nm and not bf:
                got = oc_.emission_nm
                add(g, f"#{i + 1} emission (nm)", round(ch.emission_nm), got, _num(round(ch.emission_nm), got, abs_tol=0.6))
            add(g, f"#{i + 1} colour", tuple(ch.color_rgb), tuple(oc_.color_rgb), tuple(ch.color_rgb) == tuple(oc_.color_rgb))
            if ch.exposure_ms is not None:
                add(g, f"#{i + 1} exposure (ms)", round(ch.exposure_ms, 3), oc_.exposure_ms, _num(ch.exposure_ms, oc_.exposure_ms, abs_tol=0.002))

        g = "OBJECTIVE / OPTICS"
        ob, oo = meta.objective, m.objective
        if ob.name:
            add(g, "objective", ob.name, oo.name, ob.name == oo.name)
        if ob.magnification is not None:
            add(g, "magnification", ob.magnification, oo.magnification, _num(ob.magnification, oo.magnification))
        if ob.numerical_aperture is not None:
            add(g, "NA", ob.numerical_aperture, oo.numerical_aperture, _num(ob.numerical_aperture, oo.numerical_aperture))
        if ob.refractive_index is not None:
            add(g, "refractive index", ob.refractive_index, oo.refractive_index, _num(ob.refractive_index, oo.refractive_index))
        if meta.acquisition.start is not None and m.acquisition.start is not None:
            same = abs((meta.acquisition.start.replace(microsecond=0) - m.acquisition.start).total_seconds()) < 1.5
            add(g, "acquired", f"{meta.acquisition.start:%d/%m/%Y %H:%M:%S}", f"{m.acquisition.start:%d/%m/%Y %H:%M:%S}", same)
    return checks


def unmapped_items_vsi(meta: SeriesMetadata) -> list[str]:
    items = []
    if any(ch.detector_gain is not None for ch in meta.channels):
        items.append("camera gain → sidecar")
    if any(ch.laser_intensity_pct is not None for ch in meta.channels):
        items.append("laser intensities → sidecar")
    if any(ch.filter_name for ch in meta.channels):
        items.append("filter names → sidecar")
    if meta.scan_settings:
        items.append(f"acquisition settings ({', '.join(meta.scan_settings)}) → sidecar")
    if meta.output_dims.p > 1:
        items.append("stage positions → one .vsi per position (cellSens stores multi-position experiments as numbered files)")
    items.append("the complete original metadata → sidecar")
    return items
