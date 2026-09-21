# SPDX-License-Identifier: GPL-3.0-or-later
"""Format-neutral metadata model.

Readers translate vendor metadata into these classes; writers translate them into the
output format. Nothing in here knows about Leica, Evident or Nikon.

Frames are always ordered time, then stage position, then Z (T -> P -> Z, like NIS-Elements'
loops); every frame holds all channels interleaved (Y, X, C).
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Dimensions:
    x: int
    y: int
    z: int = 1
    c: int = 1
    t: int = 1
    p: int = 1  # stage positions (multipoint)

    @property
    def n_frames(self) -> int:
        return self.t * self.p * self.z

    def frame_keys(self) -> list[tuple[int, int, int]]:
        """(t, p, z) of every frame in output order."""
        return [(t, p, z) for t in range(self.t) for p in range(self.p) for z in range(self.z)]


@dataclass
class StagePosition:
    x_um: float | None = None
    y_um: float | None = None
    z_um: float | None = None
    name: str = ""


@dataclass
class Calibration:
    pixel_size_x_um: float | None = None
    pixel_size_y_um: float | None = None
    z_step_um: float | None = None
    time_step_s: float | None = None


@dataclass
class Objective:
    name: str = ""
    magnification: float | None = None
    numerical_aperture: float | None = None
    immersion: str = ""
    refractive_index: float | None = None


@dataclass
class Orientation:
    """Scan-geometry flags recorded by the acquisition software.

    `apply` controls whether the reader re-orients pixels (and swaps X/Y sizes and
    calibration when `swap_xy`) so the output looks like the vendor viewer.
    """

    flip_x: bool = False
    flip_y: bool = False
    swap_xy: bool = False
    apply: bool = False


@dataclass
class Channel:
    name: str
    auto_name: str = ""
    excitation_nm: float | None = None
    emission_range_nm: tuple[float, float] | None = None
    color_rgb: tuple[int, int, int] = (255, 255, 255)
    color_name: str = ""
    modality: str = "fluorescence"  # "fluorescence" | "brightfield"
    detector: str = ""
    detector_type: str = ""
    detector_gain: float | None = None
    detector_offset: float | None = None
    laser_intensity_pct: float | None = None
    dye_name: str = ""
    pinhole_um: float | None = None
    sequential_index: int | None = None
    display_range: tuple[float, float] | None = None  # fraction of full scale (black, white)
    filter_name: str = ""  # emission filter as named by the vendor, e.g. "B525/50"
    vendor_name: str = ""  # channel name given in the acquisition software (cellSens "C488", ...)
    exposure_ms: float | None = None  # camera exposure (widefield / spinning disk)

    @property
    def emission_nm(self) -> float | None:
        if self.emission_range_nm is None:
            return None
        lo, hi = self.emission_range_nm
        return round((lo + hi) / 2.0, 1)

    @property
    def settings_key(self) -> tuple:
        """Identity of the acquisition settings, used for 'apply to all series with same settings'."""
        em = None if self.emission_range_nm is None else tuple(round(v) for v in self.emission_range_nm)
        ex = None if self.excitation_nm is None else round(self.excitation_nm)
        return (ex, em, self.detector, self.modality, self.vendor_name, self.filter_name)


@dataclass
class Acquisition:
    start: _dt.datetime | None = None
    frame_times_s: list[float] | None = None  # per output frame (t, p, z), seconds since start
    stage_x_um: float | None = None
    stage_y_um: float | None = None
    stage_z_um: float | None = None
    microscope: str = ""
    software: str = ""
    serial_number: str = ""


@dataclass
class SeriesInfo:
    """Lightweight description used to populate the queue before full metadata is read."""

    index: int
    name: str
    path: str = ""  # container path inside the file (e.g. "Folder/Series001")
    dims: Dimensions | None = None
    dtype: str = "uint16"
    supported: bool = True
    reason: str = ""
    pyramid_levels: list[Dimensions] = field(default_factory=list)  # resolution levels (full first)
    kind: str = ""  # vendor classification when not a plain image, e.g. "overview image"
    hidden: bool = False  # layer not displayed by default in the vendor software

    @property
    def summary(self) -> str:
        if self.dims is None:
            return ""
        d = self.dims
        parts = [f"{d.x}×{d.y}"]
        if d.z > 1:
            parts.append(f"Z{d.z}")
        parts.append(f"C{d.c}")
        if d.t > 1:
            parts.append(f"T{d.t}")
        if d.p > 1:
            parts.append(f"P{d.p}")
        if len(self.pyramid_levels) > 1:
            parts.append(f"{len(self.pyramid_levels)} resolutions")
        return " · ".join(parts)


@dataclass
class SeriesMetadata:
    source_path: str
    source_format: str
    series_index: int
    series_name: str
    dims: Dimensions
    dtype: str
    bits: int
    calibration: Calibration
    objective: Objective
    channels: list[Channel]
    imaging_mode: str = "laser_scanning_confocal"  # | "widefield" | "spinning_disk"
    zoom: float | None = None
    pinhole_um: float | None = None
    orientation: Orientation = field(default_factory=Orientation)
    acquisition: Acquisition = field(default_factory=Acquisition)
    z_positions_um: list[float] | None = None
    scan_settings: dict[str, Any] = field(default_factory=dict)  # human-readable extra settings
    vendor_raw: dict[str, Any] = field(default_factory=dict)  # everything else, for the sidecar
    warnings: list[str] = field(default_factory=list)
    pyramid_levels: list[Dimensions] = field(default_factory=list)  # all resolution levels, full first
    level: int = 0  # resolution level `dims` refers to (0 = full)
    positions: list[StagePosition] = field(default_factory=list)  # one per stage position (dims.p)

    @property
    def output_dims(self) -> Dimensions:
        """Dimensions after optional re-orientation (X/Y swapped when swap_xy is applied)."""
        d = self.dims
        if self.orientation.apply and self.orientation.swap_xy:
            return Dimensions(x=d.y, y=d.x, z=d.z, c=d.c, t=d.t, p=d.p)
        return Dimensions(x=d.x, y=d.y, z=d.z, c=d.c, t=d.t, p=d.p)

    @property
    def output_calibration(self) -> Calibration:
        cal = self.calibration
        if self.orientation.apply and self.orientation.swap_xy:
            return Calibration(cal.pixel_size_y_um, cal.pixel_size_x_um, cal.z_step_um, cal.time_step_s)
        return Calibration(cal.pixel_size_x_um, cal.pixel_size_y_um, cal.z_step_um, cal.time_step_s)

    def to_dict(self) -> dict[str, Any]:
        def conv(v: Any) -> Any:
            if isinstance(v, _dt.datetime):
                return v.isoformat()
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [conv(x) for x in v]
            return v

        return conv(asdict(self))
