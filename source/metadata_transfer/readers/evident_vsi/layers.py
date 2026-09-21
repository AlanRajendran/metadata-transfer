"""Extract per-layer acquisition metadata from a parsed `.vsi` tag tree.

Structure of a cellSens Dimension 4.4 file (tag ids in brackets, see tag_names.py):

    root
      [2000] collection
        [2001]#<stackId>  image volume (one per layer; <stackId> = number of the stack folder)
          [2003] dimension sizes, e.g. [1, 20, 3]        (order = ETS coordinate order)
          [2002]#k  image frame (one per plane) -> [2006] frame properties -> [2017] timestamp (ms)
                    the first one also holds [2018] external-file properties: [2053] image rect,
                    [2410] tile origin, [20005] external file present
          [2005] stack properties: [2030] name, [2019] pixel size (um), [2018] origin (um),
                    [2015] creation time, [2074] stack type, [2043] optical path (devices),
                    and for single-channel layers the channel fields ([2419], [2418], [20035])
          [2007]#d  dimension description, one per extra dimension, each with [2008]#e elements:
                    [2023] meaning (1 Z, 2 T, 3 lambda, 4 C, 9 phase), Z: [2012] start [2013] step,
                    C: [2021]/[2419] name, [2417] emission, [2474] excitation, [2418] type
                    (1 transmitted, 2 fluorescence), [2003] display limits, [2004] LUT,
                    [2043] optical path with the per-channel device settings
      display mapping entries -> [10005] displayed stack id, [10008] visible (0 = hidden)
      document properties ([2109] volume, or directly at the root in older files)
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any

from .tagtree import Volume

# dimension meanings
DIM_Z, DIM_T, DIM_LAMBDA, DIM_C, DIM_UNKNOWN, DIM_PHASE = 1, 2, 3, 4, 5, 9
DIM_NAMES = {DIM_Z: "Z", DIM_T: "T", DIM_LAMBDA: "lambda", DIM_C: "C", DIM_UNKNOWN: "unknown", DIM_PHASE: "phase"}

STACK_TYPES = {
    0: "image",
    1: "overview image",
    2: "sample mask",
    4: "focus image",
    8: "EFI sharpness map",
    16: "EFI height map",
    32: "EFI texture map",
    64: "EFI stack",
    256: "macro image",
}

# device subtypes (tag 120130)
DEV_CAMERA, DEV_OBJECTIVE, DEV_LAMP, DEV_DISK, DEV_LASER = 0, 20000, 20003, 20011, 20016

# tags used below
T_COLLECTION, T_IMAGE, T_FRAME, T_SIZES, T_STACK_PROPS, T_DIMDESC, T_ELEMENT = 2000, 2001, 2002, 2003, 2005, 2007, 2008
T_FRAME_PROPS, T_EXT_PROPS, T_TIMESTAMP, T_IMAGE_RECT, T_TILE_ORIGIN, T_EXT_PRESENT = 2006, 2018, 2017, 2053, 2410, 20005
T_STACK_NAME, T_PIXEL_SIZE, T_ORIGIN, T_CREATION, T_STACK_TYPE, T_OPTICAL_PATH, T_CHANNEL_DIM = (
    2030,
    2019,
    2018,
    2015,
    2074,
    2043,
    2031,
)
T_MEANING, T_Z_START, T_Z_INC, T_DIM_NAME, T_CH_NAME, T_EMISSION, T_EXCITATION, T_CH_TYPE = (
    2023,
    2012,
    2013,
    2021,
    2419,
    2417,
    2474,
    2418,
)
T_DISPLAY_LIMITS, T_LUT, T_IS_TRANSMISSION = 2003, 2004, 20035
T_DEV_NAME, T_DEV_SUBTYPE, T_DEV_MODEL, T_DEV_MANUFACTURER, T_DEV_PROPS = 120116, 120130, 120132, 120133, 120114
T_EXPOSURE_US, T_MIRROR_H, T_MIRROR_V, T_BINNING_X, T_BINNING_Y, T_BIT_DEPTH = (
    100002,
    100023,
    100024,
    100015,
    100016,
    100049,
)
T_OBJ_NAME, T_OBJ_DESC, T_OBJ_MAG, T_OBJ_NA, T_OBJ_RI, T_OBJ_WD = 120063, 120065, 120060, 120061, 120079, 120062
T_LAMP_PCT, T_LASER_NM, T_DISK_SPEED, T_PINHOLE = 121132, 122000, 125010, 125037
T_DISPLAYED_STACK, T_EXPERIMENT_NAME, T_EXPERIMENT_VOL = 10005, 175266, 21000
T_DOC_PROPS, T_PRODUCT, T_PRODUCT_VERSION, T_BUILD, T_AUTHOR, T_DOC_TIME = 2109, 34, 35, 21, 15, 14


@dataclass
class DeviceInfo:
    index: int
    name: str = ""
    subtype: int | None = None
    model: str = ""
    manufacturer: str = ""
    props: dict[int, Any] = field(default_factory=dict)

    def get(self, tag: int, default: Any = None) -> Any:
        v = self.props.get(tag)
        return default if v is None else v


@dataclass
class ObjectiveInfo:
    name: str = ""
    description: str = ""
    magnification: float | None = None
    numerical_aperture: float | None = None
    refractive_index: float | None = None
    working_distance_um: float | None = None

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.name, self.description) if p]
        return " ".join(parts)


@dataclass
class ChannelInfo:
    index: int
    name: str = ""
    channel_type: int | None = None  # 1 transmitted light, 2 fluorescence
    is_transmission: bool = False
    emission_nm: float | None = None
    excitation_nm: float | None = None
    display_limits: tuple[float, float] | None = None
    color_rgb: tuple[int, int, int] | None = None
    devices: list[DeviceInfo] = field(default_factory=list)
    # derived from the devices
    exposure_us: int | None = None
    mirror_h: bool = False
    mirror_v: bool = False
    filter_name: str = ""
    dichroic_name: str = ""
    lasers: list[tuple[float, float | None]] = field(default_factory=list)  # (wavelength nm, intensity %)
    lamp_pct: float | None = None
    disk_speed_rpm: float | None = None
    pinhole_um: float | None = None

    @property
    def is_brightfield(self) -> bool:
        return self.is_transmission or self.channel_type == 1

    @property
    def filter_band_nm(self) -> tuple[float, float] | None:
        return parse_filter_band(self.filter_name)

    @property
    def active_laser(self) -> tuple[float, float | None] | None:
        """Laser line closest to the excitation wavelength (cellSens does not flag the shutter state)."""
        if not self.lasers or self.excitation_nm is None:
            return None
        return min(self.lasers, key=lambda ln: abs(ln[0] - self.excitation_nm))


@dataclass
class LayerInfo:
    stack_id: int
    name: str = ""
    sizes: list[int] = field(default_factory=list)  # sizes of the extra dimensions, ETS order
    meanings: list[int | None] = field(default_factory=list)
    image_rect: tuple[int, int, int, int] | None = None  # x0, y0, width, height
    tile_origin: tuple[int, int] = (0, 0)
    has_external: bool = False
    pixel_size_um: tuple[float, float] | None = None
    origin_um: tuple[float, float] | None = None
    creation_time: int | None = None  # unix seconds (UTC)
    stack_type: int | None = None
    channel_dim: int | None = None
    z_start_um: float | None = None
    z_increment_um: float | None = None
    frame_timestamps_ms: list[float] = field(default_factory=list)
    channels: list[ChannelInfo] = field(default_factory=list)
    objective: ObjectiveInfo | None = None
    camera: DeviceInfo | None = None
    stack_devices: list[DeviceInfo] = field(default_factory=list)
    stage_center_um: tuple[float, float] | None = None
    experiment_name: str = ""
    n_frames: int = 0

    @property
    def is_image(self) -> bool:
        return self.image_rect is not None and self.n_frames > 0

    @property
    def stack_type_name(self) -> str:
        return STACK_TYPES.get(self.stack_type or 0, f"type {self.stack_type}")

    def size_of(self, meaning: int) -> int:
        n = 1
        for s, m in zip(self.sizes, self.meanings):
            if m == meaning:
                n *= s
        return n

    def unsupported_dimensions(self) -> list[str]:
        out = []
        for s, m in zip(self.sizes, self.meanings):
            if s > 1 and m not in (DIM_Z, DIM_T, DIM_C):
                out.append(DIM_NAMES.get(m, f"dimension {m}"))
        return out

    @property
    def width(self) -> int:
        return self.image_rect[2] if self.image_rect else 0

    @property
    def height(self) -> int:
        return self.image_rect[3] if self.image_rect else 0

    @property
    def creation_datetime(self) -> _dt.datetime | None:
        if self.creation_time is None:
            return None
        try:
            return _dt.datetime.fromtimestamp(self.creation_time)
        except (OverflowError, OSError, ValueError):
            return None


@dataclass
class DocumentInfo:
    product: str = ""
    version: str = ""
    build: int | None = None
    author: str = ""
    creation_time: int | None = None

    @property
    def software(self) -> str:
        parts = [p for p in (self.product, self.version) if p]
        s = " ".join(parts)
        if self.build:
            s += f" (build {self.build})"
        return s


@dataclass
class VsiInfo:
    document: DocumentInfo
    layers: list[LayerInfo]
    displayed_stack_ids: set[int] = field(default_factory=set)  # layers referenced by the display mapping

    def is_hidden(self, stack_id: int) -> bool:
        return bool(self.displayed_stack_ids) and stack_id not in self.displayed_stack_ids

    def layer(self, stack_id: int) -> LayerInfo | None:
        for lay in self.layers:
            if lay.stack_id == stack_id:
                return lay
        return None


# ---------------------------------------------------------------------- helpers
_BAND_RE = re.compile(r"(\d{3,4})\s*/\s*(\d{1,3})")


def parse_filter_band(name: str) -> tuple[float, float] | None:
    """'B525/50' -> (500, 550); 'D405/488/561/640' -> None."""
    if not name or name.count("/") != 1:
        return None
    m = _BAND_RE.search(name)
    if not m:
        return None
    centre, width = float(m.group(1)), float(m.group(2))
    return (centre - width / 2, centre + width / 2)


def _f(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _devices(optical_path: Volume | None) -> list[DeviceInfo]:
    out: list[DeviceInfo] = []
    if optical_path is None:
        return out
    for f in optical_path.fields:
        v = f.vol
        if v is None or f.tag > 1000:  # devices are numbered 1..N
            continue
        dev = DeviceInfo(index=f.tag)
        dev.name = _str(v.scalar(T_DEV_NAME))
        dev.subtype = v.scalar(T_DEV_SUBTYPE)
        dev.model = _str(v.scalar(T_DEV_MODEL))
        dev.manufacturer = _str(v.scalar(T_DEV_MANUFACTURER))
        props = v.sub(T_DEV_PROPS)
        if props is not None:
            for pf in props.fields:
                dev.props[pf.tag] = pf.scalar()
        out.append(dev)
    return out


def _objective(devices: list[DeviceInfo]) -> ObjectiveInfo | None:
    for d in devices:
        if d.subtype == DEV_OBJECTIVE:
            return ObjectiveInfo(
                name=_str(d.get(T_OBJ_NAME)),
                description=_str(d.get(T_OBJ_DESC)),
                magnification=_f(d.get(T_OBJ_MAG)),
                numerical_aperture=_f(d.get(T_OBJ_NA)),
                refractive_index=_f(d.get(T_OBJ_RI)),
                working_distance_um=_f(d.get(T_OBJ_WD)),
            )
    return None


def _camera(devices: list[DeviceInfo]) -> DeviceInfo | None:
    for d in devices:
        if d.subtype == DEV_CAMERA:
            return d
    return None


def _apply_devices(ch: ChannelInfo, devices: list[DeviceInfo]) -> None:
    ch.devices = devices
    for d in devices:
        if d.subtype == DEV_CAMERA:
            exp = d.get(T_EXPOSURE_US)
            if isinstance(exp, (int, float)):
                ch.exposure_us = int(exp)
            ch.mirror_h = bool(d.get(T_MIRROR_H, False))
            ch.mirror_v = bool(d.get(T_MIRROR_V, False))
        elif d.subtype == DEV_LASER:
            nm = _f(d.get(T_LASER_NM))
            if nm:
                ch.lasers.append((nm, _f(d.get(T_LAMP_PCT))))
        elif d.subtype == DEV_LAMP:
            ch.lamp_pct = _f(d.get(T_LAMP_PCT))
        elif d.subtype == DEV_DISK:
            ch.disk_speed_rpm = _f(d.get(T_DISK_SPEED))
            ch.pinhole_um = _f(d.get(T_PINHOLE))
        lname = d.name.lower()
        if "filter wheel" in lname and d.get(T_OBJ_NAME):
            ch.filter_name = _str(d.get(T_OBJ_NAME))
        elif "dichroic" in lname and d.get(T_OBJ_NAME):
            ch.dichroic_name = _str(d.get(T_OBJ_NAME))


def _lut_color(vol: Volume) -> tuple[int, int, int] | None:
    f = vol.first(T_LUT)
    if f is None:
        return None
    entries = f.scalar()
    if isinstance(entries, list) and entries and isinstance(entries[0], list) and len(entries[0]) == 3:
        r, g, b = entries[-1]
        return (int(r), int(g), int(b))
    return None


def _channel_from_volume(index: int, vol: Volume, fallback_name: str) -> ChannelInfo:
    ch = ChannelInfo(index=index)
    ch.name = _str(vol.scalar(T_DIM_NAME)) or _str(vol.scalar(T_CH_NAME)) or fallback_name
    ch.channel_type = vol.scalar(T_CH_TYPE)
    ch.is_transmission = bool(vol.scalar(T_IS_TRANSMISSION, False))
    ch.emission_nm = _f(vol.scalar(T_EMISSION))
    ch.excitation_nm = _f(vol.scalar(T_EXCITATION))
    dl = vol.scalar(T_DISPLAY_LIMITS)
    if isinstance(dl, list) and len(dl) == 2:
        ch.display_limits = (float(dl[0]), float(dl[1]))
    ch.color_rgb = _lut_color(vol)
    _apply_devices(ch, _devices(vol.sub(T_OPTICAL_PATH)))
    return ch


def _layer(img: Volume, stack_id: int) -> LayerInfo:
    lay = LayerInfo(stack_id=stack_id)
    sizes = img.scalar(T_SIZES)
    lay.sizes = [int(s) for s in sizes] if isinstance(sizes, list) else []

    frames = sorted(img.find(T_FRAME), key=lambda f: f.second_tag or 0)
    lay.n_frames = len(frames)
    for fr in frames:
        fv = fr.vol
        if fv is None:
            continue
        props = fv.sub(T_FRAME_PROPS)
        if props is not None:
            ts = props.scalar(T_TIMESTAMP)
            if isinstance(ts, (int, float)):
                lay.frame_timestamps_ms.append(float(ts))
        ext = fv.sub(T_EXT_PROPS)
        if ext is not None and lay.image_rect is None:
            rect = ext.scalar(T_IMAGE_RECT)
            if isinstance(rect, list) and len(rect) == 4:
                lay.image_rect = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
            to = ext.scalar(T_TILE_ORIGIN)
            if isinstance(to, list) and len(to) >= 2:
                lay.tile_origin = (int(to[0]), int(to[1]))
            lay.has_external = bool(ext.scalar(T_EXT_PRESENT, False))

    sp = img.sub(T_STACK_PROPS)
    if sp is not None:
        lay.name = _str(sp.scalar(T_STACK_NAME))
        ps = sp.scalar(T_PIXEL_SIZE)
        if isinstance(ps, list) and len(ps) >= 2:
            lay.pixel_size_um = (float(ps[0]), float(ps[1]))
        og = sp.scalar(T_ORIGIN)
        if isinstance(og, list) and len(og) >= 2:
            lay.origin_um = (float(og[0]), float(og[1]))
        ct = sp.scalar(T_CREATION)
        if isinstance(ct, int):
            lay.creation_time = ct
        lay.stack_type = sp.scalar(T_STACK_TYPE)
        lay.channel_dim = sp.scalar(T_CHANNEL_DIM)
        lay.stack_devices = _devices(sp.sub(T_OPTICAL_PATH))
        lay.objective = _objective(lay.stack_devices)
        lay.camera = _camera(lay.stack_devices)
        exp = sp.sub(T_EXPERIMENT_VOL)
        if exp is not None:
            lay.experiment_name = _str(exp.scalar(T_EXPERIMENT_NAME))
            cx, cy = exp.scalar(21007), exp.scalar(21008)
            if isinstance(cx, (int, float)) and isinstance(cy, (int, float)):
                lay.stage_center_um = (float(cx), float(cy))

    # dimension descriptions, in ETS coordinate order
    dimdescs = sorted(img.find(T_DIMDESC), key=lambda f: f.second_tag or 0)
    channel_vols: list[Volume] = []
    for k in range(len(lay.sizes)):
        meaning = None
        if k < len(dimdescs) and dimdescs[k].vol is not None:
            elements = sorted(dimdescs[k].vol.find(T_ELEMENT), key=lambda f: f.second_tag or 0)
            vols = [e.vol for e in elements if e.vol is not None]
            if vols:
                meaning = vols[0].scalar(T_MEANING)
                if meaning == DIM_Z:
                    lay.z_start_um = _f(vols[0].scalar(T_Z_START))
                    lay.z_increment_um = _f(vols[0].scalar(T_Z_INC))
                elif meaning == DIM_C or (meaning is None and any(v.first(T_CH_NAME) for v in vols)):
                    meaning = DIM_C
                    channel_vols = vols
        lay.meanings.append(meaning)

    if channel_vols:
        for i, v in enumerate(channel_vols):
            lay.channels.append(_channel_from_volume(i, v, f"Channel {i + 1}"))
        # Per-channel optical paths carry the exposure; the camera itself sits at stack level.
    elif sp is not None:
        ch = _channel_from_volume(0, sp, lay.name or "Channel 1")
        ch.name = _str(sp.scalar(T_CH_NAME)) or lay.name or "Channel 1"
        lay.channels.append(ch)
    return lay


def _document(root: Volume) -> DocumentInfo:
    doc = DocumentInfo()
    vol: Volume | None = None
    for f in root.walk():
        if f.tag == T_DOC_PROPS and f.vol is not None:
            vol = f.vol
            break
    if vol is None and root.first(T_PRODUCT) is not None:
        vol = root
    if vol is None:
        return doc
    doc.product = _str(vol.scalar(T_PRODUCT))
    doc.version = _str(vol.scalar(T_PRODUCT_VERSION))
    b = vol.scalar(T_BUILD)
    doc.build = int(b) if isinstance(b, int) else None
    doc.author = _str(vol.scalar(T_AUTHOR))
    t = vol.scalar(T_DOC_TIME)
    doc.creation_time = int(t) if isinstance(t, int) else None
    return doc


T_DISPLAY_VISIBLE = 10008


def _displayed_stack_ids(root: Volume) -> set[int]:
    """Stack ids that the display mapping shows.

    Each display entry holds `[10005] Displayed stack ID` and `[10008] Display visible`. An entry
    with visible = 0 is referenced but switched off, e.g. the DIC layer (stack10000) that cellSens
    stores next to a fluorescence time-lapse; OlyVIA does not show it.
    """
    shown: set[int] = set()
    vols = [root] + [c for f in root.walk() for c in f.vols]
    for v in vols:
        sid = v.scalar(T_DISPLAYED_STACK)
        if isinstance(sid, int) and v.scalar(T_DISPLAY_VISIBLE, 1) != 0:
            shown.add(sid)
    return shown


def parse_info(root: Volume) -> VsiInfo:
    layers: list[LayerInfo] = []
    coll = root.sub(T_COLLECTION)
    containers = [coll] if coll is not None else []
    containers.append(root)
    seen: set[int] = set()
    for cont in containers:
        for f in cont.find(T_IMAGE):
            if f.vol is None or f.second_tag is None or f.second_tag in seen:
                continue
            seen.add(f.second_tag)
            layers.append(_layer(f.vol, f.second_tag))
    displayed = _displayed_stack_ids(root)
    return VsiInfo(document=_document(root), layers=layers, displayed_stack_ids=displayed)
