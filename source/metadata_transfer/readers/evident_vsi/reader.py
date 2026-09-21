"""Evident/Olympus cellSens `.vsi` reader.

A `.vsi` file is small: it holds the metadata (tag tree, see tagtree.py / layers.py) and
thumbnails. The pixels live next to it in a folder called `_<file stem>_` with one
`stack<N>` sub-folder per layer, each holding `frame_t_0.ets` (see ets.py). Layer N of the
tag tree (image volume `[2001]#N`) belongs to folder `stack<N>`.

One layer = one series, except that layers with the same size, Z, T, pixel type, pixel size and
stage position are one acquisition that cellSens split up (e.g. a DIC channel acquired with a
fluorescence time-lapse is stored as its own hidden layer); those are merged into one
multichannel series. Channels/Z/T come from the tag tree; the ETS chunk coordinates are
checked against it. Stitched overview images are pyramids: every level is offered as a
"resolution" and the caller picks one.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from ...mapping.channel_naming import make_unique
from ...model import (
    Acquisition,
    Calibration,
    Channel,
    Dimensions,
    Objective,
    Orientation,
    SeriesInfo,
    SeriesMetadata,
)
from ..base import Reader
from .ets import EtsFile, EtsFormatError
from .layers import DIM_C, DIM_T, DIM_Z, LayerInfo, VsiInfo, parse_info
from .tagtree import Volume, parse_vsi, to_json

log = logging.getLogger(__name__)

_STACK_RE = re.compile(r"^stack(\d+)$", re.IGNORECASE)
_IMMERSION_BY_RI = [(1.0, "air"), (1.33, "water"), (1.406, "silicone"), (1.47, "glycerol"), (1.515, "oil")]


def companion_dir(vsi_path: str) -> str:
    folder, name = os.path.split(os.path.abspath(vsi_path))
    stem = os.path.splitext(name)[0]
    return os.path.join(folder, f"_{stem}_")


def vsi_for_path(path: str) -> str | None:
    """`.vsi` that owns a dropped companion folder `_<stem>_` or an `.ets` inside it (else None)."""
    p = os.path.abspath(path)
    if os.path.isfile(p) and p.lower().endswith(".ets"):
        p = os.path.dirname(os.path.dirname(p))
    if os.path.isdir(p):
        name = os.path.basename(p)
        if len(name) > 2 and name.startswith("_") and name.endswith("_"):
            cand = os.path.join(os.path.dirname(p), name[1:-1] + ".vsi")
            if os.path.isfile(cand):
                return cand
    return None


def discover_stacks(companion: str) -> dict[int, str]:
    """stack id -> path of its frame_*.ets file."""
    out: dict[int, str] = {}
    if not os.path.isdir(companion):
        return out
    for entry in os.listdir(companion):
        m = _STACK_RE.match(entry)
        sub = os.path.join(companion, entry)
        if not m or not os.path.isdir(sub):
            continue
        ets = sorted(f for f in os.listdir(sub) if f.lower().startswith("frame_") and f.lower().endswith(".ets"))
        if ets:
            out[int(m.group(1))] = os.path.join(sub, ets[0])
    return out


def _immersion(ri: float | None) -> str:
    if ri is None:
        return ""
    return min(_IMMERSION_BY_RI, key=lambda p: abs(p[0] - ri))[1]


@dataclass
class _Entry:
    index: int
    stack_id: int
    layer: LayerInfo | None
    ets_path: str | None
    name: str
    dims: Dimensions | None = None
    dtype: str = "uint16"
    bits: int = 16
    reason: str = ""
    pyramid_levels: list[Dimensions] | None = None
    _ets: EtsFile | None = None
    members: list["_Entry"] = field(default_factory=list)  # layers merged into this series

    @property
    def supported(self) -> bool:
        return not self.reason

    @property
    def parts(self) -> list["_Entry"]:
        return self.members or [self]

    def ets(self) -> EtsFile:
        if self.members:
            return self.members[0].ets()
        if self._ets is None:
            assert self.ets_path is not None
            self._ets = EtsFile(self.ets_path)
        return self._ets


class EvidentVsiReader(Reader):
    extensions = (".vsi",)
    format_name = "Evident VSI"
    merge_layers = True  # merge layers that are one acquisition split up by cellSens

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._root: Volume = parse_vsi(path)
        self.info: VsiInfo = parse_info(self._root)
        self.companion = companion_dir(path)
        self._stacks = discover_stacks(self.companion)
        self._entries = self._build_entries()
        log.info(
            "%s: %d layer(s) in the tag tree, %d stack folder(s) in %s",
            os.path.basename(path),
            len(self.info.layers),
            len(self._stacks),
            self.companion,
        )

    def close(self) -> None:
        for e in self._entries:
            for p in e.parts:
                if p._ets is not None:
                    p._ets.close()

    # ------------------------------------------------------------------ listing
    def _build_entries(self) -> list[_Entry]:
        entries: list[_Entry] = []
        names = [lay.name or f"stack{lay.stack_id}" for lay in self.info.layers]
        counts = {n: names.count(n) for n in names}
        used: set[int] = set()
        internal: list[LayerInfo] = []
        for lay in self.info.layers:
            if not lay.is_image and not lay.has_external:
                if lay.n_frames > 0 and lay.channels and lay.stack_id not in self._stacks:
                    internal.append(lay)
                continue  # e.g. a "Positions" vector layer or a preview stored in the .vsi
            name = lay.name or f"stack{lay.stack_id}"
            if counts[name] > 1:
                name = f"{name} [stack{lay.stack_id}]"
            ets_path = self._stacks.get(lay.stack_id)
            used.add(lay.stack_id)
            e = _Entry(index=len(entries), stack_id=lay.stack_id, layer=lay, ets_path=ets_path, name=name)
            self._describe(e)
            entries.append(e)
        if not entries and not self._stacks:
            # a snapshot: the only image is stored inside the .vsi file itself (no companion folder)
            for lay in internal:
                entries.append(
                    _Entry(
                        index=len(entries),
                        stack_id=lay.stack_id,
                        layer=lay,
                        ets_path=None,
                        name=lay.name or f"stack{lay.stack_id}",
                        reason="the pixels are stored inside the .vsi file (snapshot without a companion "
                        "folder); this version converts only images with a companion folder",
                    )
                )
        for sid in sorted(set(self._stacks) - used):
            e = _Entry(
                index=len(entries),
                stack_id=sid,
                layer=None,
                ets_path=self._stacks[sid],
                name=f"stack{sid}",
                reason="no metadata for this layer in the .vsi file",
            )
            try:
                ets = e.ets()
                h = ets.header
                cols, rows = ets.tile_grid(0)
                e.dims = Dimensions(x=cols * h.tile_x, y=rows * h.tile_y)
                e.dtype = str(h.dtype)
            except (OSError, EtsFormatError, ValueError):
                pass
            entries.append(e)
        return self._merge_layers(entries)

    def _merge_key(self, e: _Entry) -> tuple | None:
        """Layers sharing this key are one acquisition that cellSens stored as several layers.

        The stage origin is part of the key so that multi-position acquisitions (same geometry,
        different position) stay separate series.
        """
        lay = e.layer
        if lay is None or not e.supported or e.dims is None or e.pyramid_levels or lay.stack_type not in (None, 0):
            return None

        def r(v, n):
            return None if v is None else round(float(v), n)

        d = e.dims
        h = e.ets().header
        ps = tuple(r(v, 6) for v in lay.pixel_size_um) if lay.pixel_size_um else None
        og = tuple(r(v, 2) for v in lay.origin_um) if lay.origin_um else None
        zs = (r(lay.z_start_um, 3), r(lay.z_increment_um, 4)) if d.z > 1 else None
        return (d.x, d.y, d.z, d.t, e.dtype, ps, og, zs, h.tile_x, h.tile_y)

    def _merge_layers(self, entries: list[_Entry]) -> list[_Entry]:
        if not self.merge_layers:
            return entries
        keys = {id(e): self._merge_key(e) for e in entries}
        groups: dict[tuple, list[_Entry]] = {}
        for e in entries:
            if keys[id(e)] is not None:
                groups.setdefault(keys[id(e)], []).append(e)
        out: list[_Entry] = []
        done: set[int] = set()
        for e in entries:
            if id(e) in done:
                continue
            k = keys[id(e)]
            grp = sorted(groups[k], key=lambda m: m.stack_id) if k is not None else [e]
            done.update(id(m) for m in grp)
            if len(grp) == 1:
                out.append(e)
                continue
            first = grp[0]
            assert first.dims is not None
            merged = _Entry(
                index=0,
                stack_id=first.stack_id,
                layer=first.layer,
                ets_path=first.ets_path,
                name=" + ".join((m.layer.name if m.layer else "") or f"stack{m.stack_id}" for m in grp),
                dims=Dimensions(
                    x=first.dims.x, y=first.dims.y, z=first.dims.z, t=first.dims.t, c=sum(m.dims.c for m in grp)
                ),
                dtype=first.dtype,
                bits=max(m.bits for m in grp),
                members=grp,
            )
            log.info(
                "%s: layers %s have the same geometry and stage position; merged into one %d-channel series",
                os.path.basename(self.path),
                ", ".join(f"stack{m.stack_id}" for m in grp),
                merged.dims.c,
            )
            out.append(merged)
        for i, e in enumerate(out):
            e.index = i
        return out

    def _describe(self, e: _Entry) -> None:
        lay = e.layer
        assert lay is not None
        bad = lay.unsupported_dimensions()
        z, c, t = lay.size_of(DIM_Z), lay.size_of(DIM_C), lay.size_of(DIM_T)
        if lay.channels and c == 1:
            c = len(lay.channels)
        e.dims = Dimensions(x=lay.width, y=lay.height, z=z, c=c, t=t)
        if lay.camera is not None and isinstance(lay.camera.get(100049), int):
            e.bits = int(lay.camera.get(100049))
        if e.ets_path is None:
            folder = os.path.basename(self.companion)
            if not os.path.isdir(self.companion):
                e.reason = f"companion folder '{folder}' not found next to the .vsi file"
            else:
                e.reason = f"pixel data folder '{folder}\\stack{lay.stack_id}' is missing"
            return
        if bad:
            e.reason = f"{', '.join(bad)} dimension is not supported in this version"
            return
        if None in lay.meanings[: len(lay.sizes)] and any(
            s > 1 for s, m in zip(lay.sizes, lay.meanings) if m is None
        ):
            e.reason = "the meaning of one image dimension could not be determined"
            return
        try:
            ets = e.ets()
        except (OSError, EtsFormatError, ValueError) as exc:
            e.reason = f"cannot read pixel data: {exc}"
            return
        h = ets.header
        reason = ets.unsupported_reason()
        if reason:
            e.reason = reason
            return
        e.dtype = str(h.dtype)
        e.bits = min(e.bits, h.dtype.itemsize * 8) if e.bits else h.dtype.itemsize * 8
        if h.n_extra_dims != len(lay.sizes):
            e.reason = (
                f"pixel file has {h.n_extra_dims} extra dimensions but the metadata describes {len(lay.sizes)}"
            )
            return
        if len(h.image_dims) >= 2 and (h.image_dims[0], h.image_dims[1]) != (lay.width, lay.height):
            e.reason = (
                f"pixel file is {h.image_dims[0]}×{h.image_dims[1]} but the metadata says {lay.width}×{lay.height}"
            )
            return
        ranges = ets.extra_ranges(0)
        if any(r > s for r, s in zip(ranges, lay.sizes)):
            e.reason = f"pixel file dimensions {ranges} exceed the metadata dimensions {lay.sizes}"
            return
        if ets.n_levels > 1:
            e.pyramid_levels = []
            for lv in range(ets.n_levels):
                w, hgt = ets.level_shape(lv, lay.width, lay.height)
                e.pyramid_levels.append(Dimensions(x=w, y=hgt, z=z, c=c, t=t))

    def list_series(self) -> list[SeriesInfo]:
        out = []
        for e in self._entries:
            lay = e.layer
            path = "+".join(f"stack{p.stack_id}" for p in e.parts)
            info = SeriesInfo(
                index=e.index,
                name=e.name,
                path=path,
                dims=e.dims,
                dtype=e.dtype,
                supported=e.supported,
                reason=e.reason,
                pyramid_levels=list(e.pyramid_levels or []),
            )
            if lay is not None and lay.stack_type not in (None, 0):
                info.kind = lay.stack_type_name
            if lay is not None and all(self.info.is_hidden(p.stack_id) for p in e.parts):
                info.hidden = True
            out.append(info)
        return out

    # ------------------------------------------------------------------ metadata
    def _entry(self, index: int) -> _Entry:
        try:
            e = self._entries[index]
        except IndexError:
            raise IndexError(f"series {index} does not exist") from None
        if not e.supported:
            raise ValueError(f"series '{e.name}' cannot be converted: {e.reason}")
        assert e.layer is not None
        return e

    def metadata(self, index: int, *, apply_orientation: bool = False, level: int = 0) -> SeriesMetadata:
        e = self._entry(index)
        lay = e.layer
        assert lay is not None and e.dims is not None
        ets = e.ets()
        n_levels = max(1, ets.n_levels)
        if not 0 <= level < n_levels:
            raise ValueError(f"resolution level {level} does not exist (file has {n_levels})")
        d0 = e.dims
        w, h = ets.level_shape(level, d0.x, d0.y) if level else (d0.x, d0.y)
        dims = Dimensions(x=w, y=h, z=d0.z, c=d0.c, t=d0.t)
        scale = 2**level
        psx, psy = lay.pixel_size_um or (None, None)
        cal = Calibration(
            pixel_size_x_um=psx * scale if psx else None,
            pixel_size_y_um=psy * scale if psy else None,
            z_step_um=abs(lay.z_increment_um) if lay.z_increment_um and dims.z > 1 else None,
        )
        warnings: list[str] = []
        obj = Objective()
        if lay.objective is not None:
            o = lay.objective
            obj = Objective(
                name=o.display_name,
                magnification=o.magnification,
                numerical_aperture=o.numerical_aperture,
                immersion=_immersion(o.refractive_index),
                refractive_index=o.refractive_index,
            )
        layers: list[LayerInfo] = [p.layer for p in e.parts if p.layer is not None]
        channels = [ch for p in e.parts if p.layer is not None for ch in self._channels(p.layer, p.bits, warnings)]
        for ch, n in zip(channels, make_unique([ch.name for ch in channels])):
            ch.name = n
        acq = self._acquisition(layers, dims, cal, warnings)
        z_positions = None
        if dims.z > 1 and lay.z_start_um is not None and lay.z_increment_um is not None:
            z_positions = [lay.z_start_um + k * lay.z_increment_um for k in range(dims.z)]
        spinning_disk = any(d.subtype == 20011 for x in layers for d in x.stack_devices) or any(
            d.subtype == 20011 for x in layers for ch in x.channels for d in ch.devices
        )
        meta = SeriesMetadata(
            source_path=self.path,
            source_format=self.format_name,
            series_index=index,
            series_name=e.name,
            dims=dims,
            dtype=e.dtype,
            bits=e.bits,
            calibration=cal,
            objective=obj,
            channels=channels,
            imaging_mode="spinning_disk" if spinning_disk else "widefield",
            orientation=Orientation(
                flip_x=any(ch.mirror_h for x in layers for ch in x.channels),
                flip_y=any(ch.mirror_v for x in layers for ch in x.channels),
                swap_xy=False,
                apply=False,
            ),
            acquisition=acq,
            z_positions_um=z_positions,
            scan_settings=self._scan_settings(layers, ets, level),
            vendor_raw={
                "stack_id": lay.stack_id,
                "stack_ids": [x.stack_id for x in layers],
                "stack_type": lay.stack_type_name,
                "layer_name": lay.name,
                "layer_names": [x.name for x in layers],
            },
            warnings=warnings,
            pyramid_levels=list(e.pyramid_levels or []),
            level=level,
        )
        if lay.stack_type not in (None, 0):
            warnings.append(f"cellSens marks this layer as '{lay.stack_type_name}'")
        for x in layers:
            if self.info.is_hidden(x.stack_id):
                what = "this layer" if len(layers) == 1 else f"layer '{x.name}' (stack{x.stack_id})"
                warnings.append(f"{what} is not shown by default in cellSens/OlyVIA")
        return meta

    def _channels(self, lay: LayerInfo, bits: int, warnings: list[str]) -> list[Channel]:
        out: list[Channel] = []
        full = float(2**bits - 1) if bits else 65535.0
        camera_name = lay.camera.name if lay.camera is not None else ""
        for ci in lay.channels:
            bf = ci.is_brightfield
            # ND2 gets the cellSens emission wavelength (what OlyVIA shows); the emission
            # filter band is kept in filter_name and used only when cellSens gives no value.
            em_range = None
            band = ci.filter_band_nm
            if ci.emission_nm:
                em_range = (ci.emission_nm, ci.emission_nm)
            elif band is not None and not bf:
                em_range = band
            laser = ci.active_laser
            display = None
            if ci.display_limits is not None:
                lo, hi = ci.display_limits
                display = (max(0.0, lo / full), min(1.0, hi / full)) if hi > lo else None
            color = ci.color_rgb or ((255, 255, 255) if bf else (255, 255, 255))
            ch = Channel(
                name=ci.name,
                excitation_nm=None if bf else ci.excitation_nm,
                emission_range_nm=None if bf else em_range,
                color_rgb=color,
                modality="brightfield" if bf else "fluorescence",
                detector=camera_name,
                detector_type="camera",
                laser_intensity_pct=(laser[1] if laser and not bf else None),
                pinhole_um=ci.pinhole_um,
                display_range=display,
                filter_name=ci.filter_name if not bf else "",
                vendor_name=ci.name,
                exposure_ms=(ci.exposure_us / 1000.0) if ci.exposure_us is not None else None,
                dye_name="",
            )
            ch.auto_name = _settings_label(ci, camera_name)
            out.append(ch)
        names = make_unique([c.name for c in out])
        for c, n in zip(out, names):
            c.name = n
        if not out:
            warnings.append("no channel description found; a single unnamed channel is assumed")
            out.append(Channel(name="Channel 1", detector=camera_name, detector_type="camera"))
        return out

    def _acquisition(
        self, layers: list[LayerInfo], dims: Dimensions, cal: Calibration, warnings: list[str]
    ) -> Acquisition:
        lay = layers[0]
        acq = Acquisition()
        starts = [x.creation_datetime for x in layers if x.creation_datetime is not None]
        acq.start = min(starts) if starts else None
        acq.software = self.info.document.software
        frame_dev = next((d for d in lay.stack_devices if d.subtype == 40500), None)
        scope = frame_dev.model if frame_dev is not None else ""
        if any(d.subtype == 20011 for d in lay.stack_devices) or any(
            d.subtype == 20011 for ch in lay.channels for d in ch.devices
        ):
            disk = next(
                (d for ch in lay.channels for d in ch.devices if d.subtype == 20011),
                next((d for d in lay.stack_devices if d.subtype == 20011), None),
            )
            if disk is not None:
                scope = f"{scope} + {disk.manufacturer + ' ' if disk.manufacturer else ''}{disk.model or disk.name}".strip(
                    " +"
                )
        acq.microscope = scope
        if lay.stage_center_um is not None:
            acq.stage_x_um, acq.stage_y_um = lay.stage_center_um
        elif lay.origin_um is not None:
            acq.stage_x_um, acq.stage_y_um = lay.origin_um
        if lay.z_start_um is not None:
            acq.stage_z_um = lay.z_start_um
        # frame timestamps: cellSens stores one per plane, first extra dimension fastest. Merged
        # layers share one clock; each ND2 frame gets the earliest time of its channels.
        per_frame: dict[tuple[int, int], float] = {}
        for x in layers:
            times = x.frame_timestamps_ms
            n_planes = int(np.prod(x.sizes)) if x.sizes else 0
            if not times or len(times) != n_planes:
                if times:
                    warnings.append(
                        f"{len(times)} frame timestamps for {n_planes} planes in layer '{x.name}'; "
                        "per-frame times not transferred"
                    )
                per_frame = {}
                break
            for k, ts in enumerate(times):
                coords = np.unravel_index(k, x.sizes, order="F")
                t = z = 0
                for m, v in zip(x.meanings, coords):
                    if m == DIM_T:
                        t = int(v)
                    elif m == DIM_Z:
                        z = int(v)
                key = (t, z)
                per_frame[key] = min(per_frame.get(key, ts), ts)
        if per_frame:
            t0 = min(per_frame.values())
            acq.frame_times_s = [(per_frame.get((t, z), t0) - t0) / 1000.0 for t in range(dims.t) for z in range(dims.z)]
            if dims.t > 1:
                tt = [per_frame[(t, 0)] for t in range(dims.t) if (t, 0) in per_frame]
                if len(tt) > 1:
                    cal.time_step_s = (tt[-1] - tt[0]) / (len(tt) - 1) / 1000.0
        return acq

    def _scan_settings(self, layers: list[LayerInfo], ets: EtsFile, level: int) -> dict[str, str]:
        lay = layers[0]
        s: dict[str, str] = {}
        doc = self.info.document
        if doc.software:
            s["Software"] = doc.software
        if doc.author:
            s["Author"] = doc.author
        if lay.experiment_name:
            s["Experiment"] = lay.experiment_name
        s["Layer" if len(layers) == 1 else "Layers"] = ", ".join(
            f"{x.name} (stack{x.stack_id}, {x.stack_type_name})" for x in layers
        )
        if len(layers) > 1:
            firsts = [(x.name, min(x.frame_timestamps_ms)) for x in layers if x.frame_timestamps_ms]
            if len(firsts) > 1:
                base = min(v for _, v in firsts)
                s["Channel time offset"] = ", ".join(f"{n} +{(v - base) / 1000:.2f} s" for n, v in firsts)
        cam = lay.camera
        if cam is not None:
            s["Camera"] = cam.name or cam.model
            bx, by = cam.get(100015), cam.get(100016)
            if bx and by:
                s["Binning"] = f"{bx}×{by}"
            if cam.get(100049):
                s["Camera bit depth"] = str(cam.get(100049))
            clip = cam.get(100017)
            if isinstance(clip, list) and len(clip) == 4:
                s["Sensor region"] = f"x {clip[0]}–{clip[2]}, y {clip[1]}–{clip[3]}"
        if lay.objective is not None and lay.objective.working_distance_um:
            s["Objective working distance"] = f"{lay.objective.working_distance_um:.0f} µm"
        if lay.origin_um is not None:
            s["Image origin (stage)"] = f"X {lay.origin_um[0]:.1f} µm, Y {lay.origin_um[1]:.1f} µm"
        if lay.z_start_um is not None and lay.z_increment_um is not None and lay.size_of(DIM_Z) > 1:
            s["Z range"] = f"{lay.z_start_um:.2f} → {lay.z_start_um + (lay.size_of(DIM_Z) - 1) * lay.z_increment_um:.2f} µm"
        for ci in [c for x in layers for c in x.channels]:
            parts = []
            if ci.exposure_us is not None:
                parts.append(f"exposure {fmt_ms(ci.exposure_us / 1000)}")
            if ci.is_brightfield:
                if ci.lamp_pct is not None:
                    parts.append(f"lamp {ci.lamp_pct:g}")
            else:
                if ci.excitation_nm:
                    parts.append(f"ex {ci.excitation_nm:.0f} nm")
                if ci.emission_nm:
                    parts.append(f"em {ci.emission_nm:.0f} nm")
                laser = ci.active_laser
                if laser:
                    parts.append(f"laser {laser[0]:.0f} nm" + (f" at {laser[1]:g}%" if laser[1] is not None else ""))
                if ci.filter_name:
                    parts.append(f"filter {ci.filter_name}")
            if ci.dichroic_name:
                parts.append(f"dichroic {ci.dichroic_name}")
            if ci.disk_speed_rpm:
                parts.append(f"disk {ci.disk_speed_rpm:.0f} rpm")
            if ci.pinhole_um:
                parts.append(f"pinhole {ci.pinhole_um:g} µm")
            if ci.mirror_h or ci.mirror_v:
                parts.append(f"mirror H={int(ci.mirror_h)} V={int(ci.mirror_v)}")
            if parts:
                s[f"Channel {ci.name}"] = ", ".join(parts)
        lasers = {ln for x in layers for ci in x.channels for ln in ci.lasers}
        if lasers:
            s["Laser lines"] = ", ".join(
                f"{nm:.0f} nm" + (f" ({pct:g}%)" if pct is not None else "") for nm, pct in sorted(lasers)
            )
        if ets.n_levels > 1:
            s["Resolution"] = f"level {level} of {ets.n_levels} (1/{2**level})"
        s["Pixel file"] = f"{ets.header.compression_name}, tiles {ets.header.tile_x}×{ets.header.tile_y}"
        return s

    # ------------------------------------------------------------------ pixels
    def _plane_coords(self, lay: LayerInfo, t: int, z: int, c: int) -> tuple[int, ...]:
        coords = []
        for m in lay.meanings:
            if m == DIM_T:
                coords.append(t)
            elif m == DIM_Z:
                coords.append(z)
            elif m == DIM_C:
                coords.append(c)
            else:
                coords.append(0)
        return tuple(coords)

    def iter_bands(self, meta: SeriesMetadata, t: int, p: int, z: int) -> Iterator[tuple[int, np.ndarray]]:
        e = self._entry(meta.series_index)
        d = meta.dims
        chans = []
        for part in e.parts:
            lay = part.layer
            assert lay is not None and part.dims is not None
            ets = part.ets()
            for c in range(part.dims.c):
                chans.append(ets.iter_bands(meta.level, self._plane_coords(lay, t, z, c), d.x, d.y, lay.tile_origin))
        for pieces in zip(*chans):
            y0 = pieces[0][0]
            band = np.stack([piece[1] for piece in pieces], axis=-1)
            yield y0, band

    def iter_frames(self, meta: SeriesMetadata) -> Iterator[tuple[int, int, int, np.ndarray]]:
        d = meta.dims
        for t, p, z in d.frame_keys():
            frame = np.empty((d.y, d.x, d.c), dtype=np.dtype(meta.dtype))
            for y0, band in self.iter_bands(meta, t, p, z):
                frame[y0 : y0 + band.shape[0]] = band
            yield t, p, z, frame

    def raw_metadata_xml(self, index: int) -> str:
        return to_json(self._root)


def fmt_ms(ms: float) -> str:
    """499.961 -> '500 ms', 9.961 -> '9.96 ms', 5000 -> '5 s'."""
    if ms >= 1000:
        return f"{ms / 1000:.3g} s"
    return f"{ms:.3g} ms"


def _settings_label(ci, camera: str) -> str:
    cam = f" ({camera})" if camera else ""
    exp = f", {fmt_ms(ci.exposure_us / 1000)}" if ci.exposure_us is not None else ""
    if ci.is_brightfield:
        return f"Trans{cam}{exp}"
    laser = ci.active_laser
    ex = f"{laser[0]:.0f}" if laser else (f"{ci.excitation_nm:.0f}" if ci.excitation_nm else "?")
    em = ci.filter_name or (f"{ci.emission_nm:.0f}" if ci.emission_nm else "?")
    return f"{ex} → {em}{cam}{exp}"
