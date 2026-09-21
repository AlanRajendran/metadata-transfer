# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the tag tree of a new `.vsi` from the template cellSens itself wrote.

The template (`data/vsi_template.vsi`, made by `tools/make_vsi_template.py`) is one layer with the
dimensions T, Z and C. Everything that depends on the image is regenerated here:

* one frame record `[2002]#k` per plane (k = t + T * (z + Z * c), first dimension fastest) with
  its timestamp; frame 0 carries the image rectangle and the external-file (ETS) description,
* the dimension sizes `[2003]`, the Z start/step and the time step,
* one channel element `[2008]#c` per channel (name, excitation/emission, transmitted or
  fluorescence, display range, colour LUT, camera exposure), cloned from the template's
  fluorescence or transmitted-light channel,
* stack properties (pixel size, origin, creation time, objective, camera, microscope, stage centre),
* document properties and the display state (which frames are selected, the displayed plane).

Device lists keep only what the source file can fill in (camera, frame, objective, camera
adapter); per-channel optical paths keep the camera with the exposure time.
"""

from __future__ import annotations

import copy
import datetime as _dt
import importlib.resources
from dataclasses import dataclass

from . import vsi_rawtree as rt
from .vsi_rawtree import RawField, RawVolume

VALUE = 268435458  # "Value" of a measured quantity
COUNT = 268435457  # element count of a vector volume

DEV_CAMERA, DEV_FRAME, DEV_OBJECTIVE = 0, 40500, 20000


@dataclass
class ChannelSpec:
    name: str
    transmitted: bool
    excitation_nm: float | None
    emission_nm: float | None
    colour: tuple[int, int, int]
    display: tuple[float, float]
    exposure_us: int | None = None


@dataclass
class DocumentSpec:
    layer_name: str
    width: int
    height: int
    t: int
    z: int
    channels: list[ChannelSpec]
    pixel_size_um: tuple[float, float]
    stage_centre_um: tuple[float, float] | None
    z_start_um: float | None
    z_step_um: float | None
    time_step_s: float | None
    frame_times_ms: list[float]  # one per (t, z), first frame 0 or later
    created: _dt.datetime | None
    objective_name: str = ""
    objective_magnification: float | None = None
    objective_na: float | None = None
    objective_ri: float | None = None
    camera: str = ""
    camera_manufacturer: str = ""
    microscope: str = ""
    bit_depth: int = 16
    experiment: str = ""
    note: str = ""


def load_template() -> RawVolume:
    data = importlib.resources.files("metadata_transfer.formats").joinpath("data/vsi_template.vsi").read_bytes()
    return rt.parse(data)


# ------------------------------------------------------------------ helpers
def _quantity(vol: RawVolume, tag: int) -> RawField:
    return vol.need(tag).volume.need(VALUE)  # type: ignore[union-attr]


def _set_quantity(vol: RawVolume, tag: int, value: float) -> None:
    _quantity(vol, tag).set_double(value)


def _remove(vol: RawVolume, tag: int) -> None:
    vol.fields = [f for f in vol.fields if f.tag != tag]


def _replace_group(vol: RawVolume, tag: int, new: list[RawField]) -> None:
    """Replace every field `tag` by `new`, at the position of the first one."""
    idx = next(i for i, f in enumerate(vol.fields) if f.tag == tag)
    vol.fields = [f for f in vol.fields if f.tag != tag]
    vol.fields[idx:idx] = new


def _lut(colour: tuple[int, int, int]) -> bytes:
    """256-entry black-to-colour ramp, BGR bytes (cellSens display LUT)."""
    r, g, b = colour
    out = bytearray()
    for i in range(256):
        out += bytes((round(b * i / 255), round(g * i / 255), round(r * i / 255)))
    return bytes(out)


def _epoch(dt: _dt.datetime | None) -> int:
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.astimezone()  # naive times are local acquisition times
    return int(dt.timestamp())


def _is_device(f: RawField) -> bool:
    return f.volume is not None and 0 < f.tag < 1000


def _devices(vol: RawVolume) -> list[RawField]:
    return [f for f in vol.fields if _is_device(f)]


def _set_devices(vol: RawVolume, keep: list[RawField]) -> None:
    """Keep only `keep` of a device list, numbered 1..n, and update the list's count."""
    others = [f for f in vol.fields if not _is_device(f)]
    for i, f in enumerate(keep, start=1):
        f.tag = i
    vol.fields = others + keep
    count = vol.first(COUNT)
    if count is not None:
        count.set_ints([len(keep)])


def _subtype(dev: RawField) -> int | None:
    f = dev.volume.first(120130) if dev.volume is not None else None
    return f.get_int() if f is not None else None


def _set_str(vol: RawVolume, tag: int, text: str) -> None:
    f = vol.first(tag)
    if f is not None:
        f.set_str(text)


# --------------------------------------------------------------------- build
def build(spec: DocumentSpec, tiff_placeholder: int) -> RawVolume:
    root = load_template()
    coll = root.sub(2000)
    coll.need(2016).blob = b"\0" * tiff_placeholder
    img = coll.sub(2001, 1)
    T, Z, C = spec.t, spec.z, len(spec.channels)
    W, H = spec.width, spec.height

    # ---- frames: one record per plane, first dimension fastest (t, then z, then c)
    frames = sorted(img.find(2002), key=lambda f: f.second_tag or 0)
    proto0, proto_n = frames[0], frames[1] if len(frames) > 1 else frames[0]
    new_frames = []
    for c in range(C):
        for z in range(Z):
            for t in range(T):
                k = t + T * (z + Z * c)
                f = copy.deepcopy(proto0 if k == 0 else proto_n)
                f.second_tag = k
                fv = f.volumes[0]
                ms = spec.frame_times_ms[t * Z + z] if len(spec.frame_times_ms) == T * Z else 0.0
                _set_quantity(fv.sub(2006), 2017, ms)
                if k == 0:
                    ext = fv.sub(2018)
                    ext.need(2053).set_ints([0, 0, W, H])
                    ext.need(2410).set_ints([0, 0, 0, 0, 0])
                    ext.need(20025).set_ints([0, 0, 0, 0, 0, W, H, T, Z, C])
                    ext.need(20187).set_ints([0, 0, 0, 0, 0, W, H, T, Z, C])
                    ext.need(20188).set_ints([0, 0, 0, 0, 0])
                new_frames.append((k, f))
    _replace_group(img, 2002, [f for _, f in sorted(new_frames, key=lambda kf: kf[0])])
    img.need(2003).set_ints([T, Z, C])

    # ---- stack properties
    sp = img.sub(2005)
    psx, psy = spec.pixel_size_um
    sp.need(2019).set_doubles([psx, psy])
    sp.need(2030).set_str(spec.layer_name)
    created = _epoch(spec.created)
    sp.need(2015).set_int(created)
    lo = min(ch.display[0] for ch in spec.channels)
    hi = max(ch.display[1] for ch in spec.channels)
    sp.need(2003).set_doubles([lo, hi])
    if spec.stage_centre_um is not None:
        cx, cy = spec.stage_centre_um
    else:
        cx, cy = W * psx / 2.0, H * psy / 2.0
    origin = (cx - W * psx / 2.0, cy - H * psy / 2.0)
    sp.need(2018).set_doubles(list(origin))
    devs = sp.sub(2043)
    keep = []
    for dev in _devices(devs):
        st, dv = _subtype(dev), dev.volume
        assert dv is not None
        if st == DEV_CAMERA:
            name = spec.camera or "Camera"
            _set_str(dv, 120116, name)
            _set_str(dv, 120132, name)
            _set_str(dv, 120133, spec.camera_manufacturer)
            props = dv.first(120114)
            if props is not None and props.volume is not None:
                bd = props.volume.first(100049)
                if bd is not None:
                    bd.set_int(spec.bit_depth)
        elif st == DEV_FRAME:
            _set_str(dv, 120116, spec.microscope or "Microscope")
            _set_str(dv, 120132, spec.microscope or "Microscope")
            _set_str(dv, 120133, "")
        elif st == DEV_OBJECTIVE:
            props = dv.sub(120114)
            _set_str(props, 120063, spec.objective_name)
            _set_str(props, 120065, "")
            for tag, val in ((120060, spec.objective_magnification), (120061, spec.objective_na),
                             (120079, spec.objective_ri)):
                f = props.first(tag)
                if f is not None and val is not None:
                    f.set_double(val)
        keep.append(dev)
    _set_devices(devs, keep)
    exp = sp.sub(21000)
    _set_quantity(exp, 21007, cx)
    _set_quantity(exp, 21008, cy)
    exp.need(175266).set_str(spec.experiment)

    # ---- dimensions: T (#0), Z (#1), C (#2)
    dims = {f.second_tag: f for f in img.find(2007)}
    t_el = dims[0].volumes[0].sub(2008, 0)
    if spec.time_step_s:
        _set_quantity(t_el, 10069, 1.0 / spec.time_step_s)
    z_el = dims[1].volumes[0].sub(2008, 0)
    _set_quantity(z_el, 2012, spec.z_start_um or 0.0)
    _set_quantity(z_el, 2013, spec.z_step_um or 0.0)
    cvol = dims[2].volumes[0]
    protos = sorted(cvol.find(2008), key=lambda f: f.second_tag or 0)
    fluo = next(f for f in protos if f.volume.first(2418) and f.volume.need(2418).get_int() == 2)
    trans = next((f for f in protos if f.volume.first(2418) and f.volume.need(2418).get_int() == 1), fluo)
    new_ch = []
    for i, ch in enumerate(spec.channels):
        f = copy.deepcopy(trans if ch.transmitted else fluo)
        f.second_tag = i
        v = f.volume
        assert v is not None
        v.need(2021).set_str(ch.name)
        v.need(2419).set_str(ch.name)
        v.need(2418).set_int(1 if ch.transmitted else 2)
        v.need(2003).set_doubles(list(ch.display))
        v.need(2004).volume.need(VALUE).payload = _lut(ch.colour)  # type: ignore[union-attr]
        for tag, val in ((2417, ch.emission_nm), (2474, ch.excitation_nm)):
            if v.first(tag) is None:
                continue
            if val and not ch.transmitted:
                _set_quantity(v, tag, float(val))
            else:
                _remove(v, tag)
        path = v.sub(2043)
        cams = [d for d in _devices(path) if _subtype(d) == DEV_CAMERA]
        for cam in cams:
            props = cam.volume.sub(120114)  # type: ignore[union-attr]
            e = props.first(100002)
            if e is not None:
                e.set_int(int(ch.exposure_us or 0))
            ens = props.first(100100)
            if ens is not None:
                ens.set_int(int(ch.exposure_us or 0) * 1000)
        _set_devices(path, cams)
        new_ch.append(f)
    _replace_group(cvol, 2008, new_ch)

    # ---- document properties
    doc_vol = coll.sub(2004)
    doc = doc_vol.sub(2109)
    for tag in (14, 23):
        f = doc.first(tag)
        if f is not None:
            f.set_int(created)
    _set_str(doc, 15, "")
    marker = doc_vol.first(20047)
    if marker is not None and marker.volume is not None:
        m = marker.volume.first(1)
        t0 = spec.frame_times_ms[0] if spec.frame_times_ms else 0.0
        if m is not None:
            m.set_str(f"1\n{t0!r}\n10^-3s^1\n{t0!r}\n10^-3s^1\nFirst Frame Marker\n8\n1\n")

    # ---- display state
    disp = coll.sub(2011, 0)
    entries = {f.second_tag: f for f in disp.find(2012)}
    view = entries[0].volumes[0].sub(2013, 0)
    view.need(20006).set_doubles(list(origin))
    view.need(20007).set_doubles([psx, psy])
    view.need(20008).set_ints([0, 0, W, H])
    mid = [0, Z // 2, 0]
    view.need(10014).set_ints(mid)
    sel = view.sub(10050).need(1073741825).volume
    assert sel is not None
    items = sorted([f for f in sel.fields if 0 < f.tag < 1073741824 and f.tag < 1000], key=lambda f: f.tag)
    if T > 1:
        wanted = [[t, 0, 0] for t in range(T)]
    elif Z > 1:
        wanted = [[0, z, 0] for z in range(Z)]
    else:
        wanted = [[0, 0, 0]]
    new_items = []
    for i, w in enumerate(wanted, start=1):
        f = copy.deepcopy(items[0])
        f.tag = i
        f.set_ints(w)
        new_items.append(f)
    sel.fields = [f for f in sel.fields if f not in items] + new_items
    sel.need(COUNT).set_ints([len(new_items)])
    if 1 in entries:
        shown = entries[1].volumes[0].sub(2013, 0)
        f = shown.first(10014)
        if f is not None:
            f.set_ints(mid)
    return root


def tree_bytes(root: RawVolume, tiff_block) -> bytes:
    """The whole `.vsi` file; `tiff_block(offset) -> (bytes, first IFD offset)`."""
    return rt.serialize(root, tiff_block=tiff_block)
