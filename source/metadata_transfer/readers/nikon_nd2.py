# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader for Nikon NIS-Elements `.nd2` files (input side, for ND2 -> VSI).

Pixels and structured metadata come from the independent `nd2` package (the same one that
validates the ND2 files this app writes). NIS-Elements keeps camera settings (exposure, gain,
binning), filter turret positions and the microscope name only in the image description; those
are parsed from its per-plane blocks ("Plane #1: ... Exposure: 50 ms ...").

One ND2 file = one series. Loops T (time), P (XY positions) and Z are supported in any order;
frames are delivered in T -> P -> Z order. RGB (brightfield colour camera) and spectral files are
listed with a reason.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from typing import Iterator

import numpy as np

from ..mapping.channel_naming import make_unique
from ..mapping.colors import wavelength_to_rgb
from ..model import (
    Acquisition,
    Calibration,
    Channel,
    Dimensions,
    Objective,
    Orientation,
    SeriesInfo,
    SeriesMetadata,
    StagePosition,
)
from .base import Reader

log = logging.getLogger(__name__)

_KNOWN_AXES = {"T", "P", "Z", "C", "Y", "X"}
_TRANSMITTED_WORDS = ("phase", "brightfield", "bright field", "bf", "dic", "trans", "dia")
_IMMERSION_BY_RI = [(1.0, "air"), (1.33, "water"), (1.406, "silicone"), (1.47, "glycerol"), (1.515, "oil")]


def _jdn_to_datetime(jdn: float | None) -> _dt.datetime | None:
    if not jdn:
        return None
    try:
        utc = _dt.datetime.fromtimestamp((jdn - 2440587.5) * 86400.0, _dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return utc.astimezone().replace(tzinfo=None)  # local wall-clock time, as NIS shows it


def _exposure_ms(text: str) -> float | None:
    m = re.match(r"\s*([\d.,]+)\s*(ms|s|us|µs|m)\b", text or "")
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2)
    return {"ms": value, "m": value, "s": value * 1000.0, "us": value / 1000.0, "µs": value / 1000.0}[unit]


def parse_plane_blocks(description: str) -> list[dict[str, str]]:
    """Per-plane key/value settings from an NIS-Elements description ("Plane #1: ...")."""
    text = (description or "").replace("\r\n", "\n")
    parts = re.split(r"\n\s*Plane #\d+:\s*\n", "\n" + text)
    blocks = []
    for part in parts[1:]:
        values: dict[str, str] = {}
        for line in part.split("\n"):
            line = line.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if key.startswith("Microscope Settings"):
                key, _, value = value.partition(":")
                key, value = key.strip(), value.strip()
            if key and key not in values:
                values[key] = value
        blocks.append(values)
    return blocks


def _is_transmitted(name: str, flags: list[str]) -> bool:
    flags_l = [f.lower() for f in flags or []]
    if any(f in flags_l for f in ("brightfield", "phasecontrast", "dicontrast", "dic")):
        return True
    words = name.lower()
    return any(re.search(rf"\b{w}\b", words) for w in _TRANSMITTED_WORDS)


def _immersion(ri: float | None) -> str:
    if ri is None:
        return ""
    return min(_IMMERSION_BY_RI, key=lambda p: abs(p[0] - ri))[1]


class NikonNd2Reader(Reader):
    extensions = (".nd2",)
    format_name = "Nikon ND2"

    def __init__(self, path: str) -> None:
        super().__init__(path)
        import nd2  # heavy import, only when an ND2 is opened

        self._f = nd2.ND2File(path)
        self._sizes = dict(self._f.sizes)
        self._seq: dict[tuple[int, int, int], int] = {}
        for i, idx in enumerate(self._f.loop_indices):
            self._seq[(idx.get("T", 0), idx.get("P", 0), idx.get("Z", 0))] = i
        log.info("%s: %s", path.rsplit("\\", 1)[-1], self._sizes)

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ listing
    def _dims(self) -> Dimensions:
        s = self._sizes
        return Dimensions(x=s.get("X", 1), y=s.get("Y", 1), z=s.get("Z", 1), c=s.get("C", 1), t=s.get("T", 1), p=s.get("P", 1))

    def _reason(self) -> str:
        f = self._f
        if f.is_rgb:
            return "RGB colour images are not supported in this version"
        extra = set(self._sizes) - _KNOWN_AXES
        if extra:
            return f"{', '.join(sorted(extra))} dimension is not supported in this version"
        if np.dtype(f.dtype) not in (np.dtype("uint8"), np.dtype("uint16")):
            return f"{f.dtype} pixels are not supported (8- or 16-bit only)"
        if len(self._seq) != self._dims().n_frames:
            return "the file's frame list does not match its loops"
        return ""

    def list_series(self) -> list[SeriesInfo]:
        reason = self._reason()
        name = self.path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return [
            SeriesInfo(index=0, name=name, path="", dims=self._dims(), dtype=str(self._f.dtype), supported=not reason, reason=reason)
        ]

    # ------------------------------------------------------------------ metadata
    def metadata(self, index: int, *, apply_orientation: bool = False, level: int = 0) -> SeriesMetadata:
        if index != 0:
            raise IndexError(f"series {index} does not exist")
        reason = self._reason()
        if reason:
            raise ValueError(f"cannot be converted: {reason}")
        f = self._f
        dims = self._dims()
        vs = f.voxel_size()
        md = f.metadata
        chans_md = md.channels or []
        ti = f.text_info or {}
        blocks = parse_plane_blocks(ti.get("description", ""))
        if len(blocks) != dims.c:
            cap = (ti.get("capturing", "") or "").replace("\r\n", "\n")
            blocks = parse_plane_blocks(re.sub(r"\n\s*Sample (\d+):", r"\nPlane #\1:", "\n" + cap)) or blocks
        warnings: list[str] = []
        cal = Calibration(pixel_size_x_um=vs.x or None, pixel_size_y_um=vs.y or None, z_step_um=(vs.z or None) if dims.z > 1 else None)

        mic0 = chans_md[0].microscope if chans_md else None
        obj = Objective()
        if mic0 is not None:
            obj = Objective(
                name=mic0.objectiveName or ti.get("optics", "") or "",
                magnification=mic0.objectiveMagnification,
                numerical_aperture=mic0.objectiveNumericalAperture,
                refractive_index=mic0.immersionRefractiveIndex,
                immersion=_immersion(mic0.immersionRefractiveIndex),
            )

        channels: list[Channel] = []
        camera = ""
        for i in range(dims.c):
            cm = chans_md[i] if i < len(chans_md) else None
            blk = blocks[i] if i < len(blocks) else {}
            name = (cm.channel.name if cm is not None else "") or f"Channel {i + 1}"
            flags = list(cm.microscope.modalityFlags or []) if cm is not None else []
            transmitted = _is_transmitted(name, flags)
            ex = cm.channel.excitationLambdaNm if cm is not None else None
            em = cm.channel.emissionLambdaNm if cm is not None else None
            col = cm.channel.color if cm is not None else None
            rgb = (col.r, col.g, col.b) if col is not None else wavelength_to_rgb(em)
            cam = blk.get("Camera Type", "") or re.sub(r"\s*\(.*\)$", "", blk.get("Detector", ""))
            camera = camera or cam
            exposure = _exposure_ms(blk.get("Exposure", ""))
            filt = ""
            for k, v in blk.items():
                if "FilterChanger" in k and v:
                    filt = re.sub(r"^\d+\s*\((.*)\)$", r"\1", v)
                    break
            ch = Channel(
                name=name,
                excitation_nm=None if transmitted else (ex or None),
                emission_range_nm=None if transmitted or not em else (em, em),
                color_rgb=(255, 255, 255) if transmitted else tuple(int(v) for v in rgb),
                modality="brightfield" if transmitted else "fluorescence",
                detector=cam,
                detector_type="camera",
                pinhole_um=cm.microscope.pinholeDiameterUm if cm is not None else None,
                filter_name=filt,
                vendor_name=name,
                exposure_ms=exposure,
            )
            if blk.get("Gain"):
                g = re.match(r"([\d.]+)", blk["Gain"])
                if g:
                    ch.detector_gain = float(g.group(1))
            ch.auto_name = " · ".join(x for x in (cam, f"{exposure:g} ms" if exposure else "", filt) if x)
            channels.append(ch)
        for ch, n in zip(channels, make_unique([c.name for c in channels])):
            ch.name = n

        # frames: times and stage positions
        acq = Acquisition()
        times: list[float] = []
        positions: list[StagePosition] = [StagePosition() for _ in range(dims.p)]
        zpos: dict[int, float] = {}
        t_first: dict[int, float] = {}
        jdn0 = None
        for (t, p, z) in dims.frame_keys():
            seq = self._seq[(t, p, z)]
            try:
                fm = f.frame_metadata(seq).channels[0]
            except Exception:
                times = []
                break
            ms = fm.time.relativeTimeMs
            times.append(ms)
            if jdn0 is None:
                jdn0 = fm.time.absoluteJulianDayNumber
            sp = fm.position.stagePositionUm
            if z == 0 and positions[p].x_um is None:
                positions[p] = StagePosition(x_um=sp.x, y_um=sp.y, z_um=sp.z, name=fm.position.name or "")
            if t == 0 and p == 0:
                zpos[z] = sp.z
            if p == 0 and z == 0:
                t_first[t] = ms
        if times:
            t0 = min(times)
            acq.frame_times_s = [(v - t0) / 1000.0 for v in times]
        if len(t_first) > 1:
            ts = [t_first[k] for k in sorted(t_first)]
            if ts[-1] > ts[0]:
                cal.time_step_s = (ts[-1] - ts[0]) / (len(ts) - 1) / 1000.0
        if times and max(times) <= min(times):
            times = []  # no real frame times recorded
            acq.frame_times_s = None
        for lp in f.experiment or []:
            if lp.type == "TimeLoop" and dims.t > 1 and lp.parameters.periodMs:
                cal.time_step_s = cal.time_step_s or lp.parameters.periodMs / 1000.0
            if lp.type == "XYPosLoop":
                for k, pt in enumerate(lp.parameters.points[: dims.p]):
                    if pt.name:
                        positions[k].name = pt.name
                    sp = pt.stagePositionUm
                    if positions[k].x_um is None and sp is not None:
                        positions[k] = StagePosition(x_um=sp.x, y_um=sp.y, z_um=sp.z or None, name=positions[k].name)
        z_positions = None
        if dims.z > 1 and len(zpos) == dims.z and len(set(zpos.values())) == dims.z:
            z_positions = [zpos[k] for k in range(dims.z)]
            steps = np.diff(z_positions)
            if cal.z_step_um is None and len(steps):
                cal.z_step_um = float(abs(np.mean(steps)))
        elif dims.z > 1:
            # NIS often records the home position for every plane: rebuild from the Z loop
            zl = next((lp for lp in f.experiment or [] if lp.type == "ZStackLoop"), None)
            if zl is not None and zpos:
                step = zl.parameters.stepUm or cal.z_step_um or 0.0
                sign = 1.0 if zl.parameters.bottomToTop else -1.0
                home = zl.parameters.homeIndex or 0
                z0 = zpos.get(0, 0.0)
                z_positions = [z0 + (k - home) * step * sign for k in range(dims.z)]
        if jdn0:
            # experiment start = time of the first frame minus its offset from the start
            acq.start = _jdn_to_datetime(jdn0 - (times[0] if times else 0.0) / 86400000.0)
        first = positions[0] if positions else StagePosition()
        acq.stage_x_um, acq.stage_y_um, acq.stage_z_um = first.x_um, first.y_um, first.z_um
        scope = next((b.get("Microscope", "") for b in blocks if b.get("Microscope")), "")
        if not scope:
            m = re.search(r"^Microscope: ([^,\r\n]+)", ti.get("description", "") or "", re.MULTILINE)
            scope = m.group(1).strip() if m and m.group(1).strip() != "n/a" else ""
        acq.microscope = scope.replace(" Microscope", "") if scope else ""
        if acq.microscope and not acq.microscope.lower().startswith("nikon"):
            acq.microscope = f"Nikon {acq.microscope}"
        acq.software = "NIS-Elements"

        scan: dict[str, str] = {}
        if camera:
            scan["Camera"] = camera
        for ch, blk in zip(channels, blocks):
            parts = [f"{k.lower()} {v}" for k, v in blk.items() if k in ("Exposure", "Gain", "Binning", "Readout Mode")]
            if ch.filter_name:
                parts.append(f"filter {ch.filter_name}")
            if parts:
                scan[f"Channel {ch.name}"] = ", ".join(parts)
        if dims.p > 1:
            scan["Stage positions"] = "; ".join(
                f"{k + 1}{' ' + pos.name if pos.name else ''}: X {pos.x_um:.1f}, Y {pos.y_um:.1f}, Z {pos.z_um:.2f} µm"
                for k, pos in enumerate(positions)
                if pos.x_um is not None
            )
        bits = f.attributes.bitsPerComponentSignificant or np.dtype(f.dtype).itemsize * 8
        return SeriesMetadata(
            source_path=self.path,
            source_format=self.format_name,
            series_index=0,
            series_name=self.list_series()[0].name,
            dims=dims,
            dtype=str(np.dtype(f.dtype)),
            bits=int(bits),
            calibration=cal,
            objective=obj,
            channels=channels,
            imaging_mode="widefield",
            zoom=mic0.zoomMagnification if mic0 is not None else None,
            orientation=Orientation(),
            acquisition=acq,
            z_positions_um=z_positions,
            scan_settings=scan,
            vendor_raw={"sizes": self._sizes},
            warnings=warnings,
            positions=positions if dims.p > 1 else [first],
        )

    # ------------------------------------------------------------------ pixels
    def _frame(self, t: int, p: int, z: int):
        arr = self._f.read_frame(self._seq[(t, p, z)])
        if arr.ndim == 2:
            return arr[..., None]
        if arr.shape[0] == self._sizes.get("C", 1) and arr.ndim == 3:
            return np.moveaxis(arr, 0, -1)  # (C, Y, X) -> (Y, X, C) view
        return arr

    def iter_bands(self, meta: SeriesMetadata, t: int, p: int, z: int, rows: int = 1024) -> Iterator[tuple[int, np.ndarray]]:
        arr = self._frame(t, p, z)
        for y0 in range(0, arr.shape[0], rows):
            yield y0, np.ascontiguousarray(arr[y0 : y0 + rows])

    def iter_frames(self, meta: SeriesMetadata) -> Iterator[tuple[int, int, int, np.ndarray]]:
        for t, p, z in meta.dims.frame_keys():
            yield t, p, z, np.ascontiguousarray(self._frame(t, p, z))

    def raw_metadata_xml(self, index: int) -> str:
        f = self._f
        doc = {"text_info": f.text_info, "sizes": self._sizes}
        for key, get in (("attributes", lambda: f.attributes), ("metadata", lambda: f.metadata),
                         ("experiment", lambda: f.experiment)):
            try:
                doc[key] = _jsonable(get())
            except Exception as exc:
                doc[key] = f"unavailable: {exc}"
        return json.dumps(doc, indent=1, default=str, ensure_ascii=False)


def _jsonable(obj):
    import dataclasses

    if dataclasses.is_dataclass(obj):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    return obj
