# SPDX-License-Identifier: GPL-3.0-or-later
"""The TIFF part of a `.vsi`: a 512-pixel JPEG thumbnail with its IFD, EXIF and Olympus SIS
records, stored inside the tag tree (field `[2016]`) with absolute file offsets.

The layout copies what cellSens Dimension 4.4 writes: JPEG strip, value area, the three SIS
sub-records (a constant block, the thumbnail descriptor, the strip length), the SIS header,
the EXIF IFD and finally the main IFD. cellSens stores its JPEG tables in a separate tag; this
writer puts complete JPEG streams in the strip instead, which TIFF readers accept as well.
"""

from __future__ import annotations

import datetime as _dt
import io
import struct

import numpy as np

THUMB_MAX = 512

#: first SIS sub-record, identical in every cellSens file examined (222 bytes)
SIS_BLOCK_1 = bytes.fromhex(
    "00000000de00000000000000000000000000f03f000000000000f03f000000000000f03f000000000000f03f00000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000020000000100000000000000000000000000000002000000"
    "010000000000000000000000000000000000000001000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000"
)
assert len(SIS_BLOCK_1) == 222

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1}


def render_thumbnail(
    planes: list[np.ndarray],
    colours: list[tuple[int, int, int]],
    limits: list[tuple[float, float]],
) -> np.ndarray:
    """RGB uint8 composite (longest side <= 512) of downsampled channel planes."""
    h, w = planes[0].shape
    scale = min(1.0, THUMB_MAX / max(h, w))
    tw, th = max(1, round(w * scale)), max(1, round(h * scale))
    ys = np.minimum((np.arange(th) / scale).astype(int), h - 1)
    xs = np.minimum((np.arange(tw) / scale).astype(int), w - 1)
    rgb = np.zeros((th, tw, 3), dtype=np.float32)
    for plane, colour, (lo, hi) in zip(planes, colours, limits):
        p = plane[np.ix_(ys, xs)].astype(np.float32)
        p = np.clip((p - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        rgb += p[..., None] * (np.array(colour, dtype=np.float32) / 255.0)
    return (np.clip(rgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def encode_jpeg(rgb: np.ndarray) -> bytes:
    from PIL import Image  # bundled with the app

    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, "JPEG", quality=90, subsampling=0)
    return buf.getvalue()


class _Block:
    """Byte buffer that knows its absolute file offset."""

    def __init__(self, base: int) -> None:
        self.base = base
        self.data = bytearray()

    def here(self) -> int:
        return self.base + len(self.data)

    def add(self, raw: bytes, align: int = 2) -> int:
        while len(self.data) % align:
            self.data += b"\0"
        at = self.here()
        self.data += raw
        return at


def _ifd(block: _Block, entries: list[tuple[int, int, int, bytes | int]]) -> int:
    """Write the values that do not fit in 4 bytes, then the IFD; returns the IFD offset."""
    resolved = []
    for tag, typ, count, value in sorted(entries):
        if isinstance(value, int):
            resolved.append((tag, typ, count, value))
            continue
        if len(value) <= 4:
            resolved.append((tag, typ, count, struct.unpack("<I", value.ljust(4, b"\0"))[0]))
        else:
            resolved.append((tag, typ, count, block.add(value)))
    raw = struct.pack("<H", len(resolved))
    for tag, typ, count, val in resolved:
        raw += struct.pack("<HHII", tag, typ, count, val)
    raw += struct.pack("<I", 0)
    return block.add(raw)


def _ascii(text: str) -> tuple[int, bytes]:
    b = (text or "").encode("ascii", "replace") + b"\0"
    return len(b), b


def build_tiff_block(
    base: int,
    jpeg: bytes,
    thumb_w: int,
    thumb_h: int,
    full_w: int,
    full_h: int,
    when: _dt.datetime,
    make: str = "",
    model: str = "",
) -> tuple[bytes, int]:
    """(content of the `[2016]` field placed at absolute offset `base`, offset of the first IFD)."""
    blk = _Block(base)
    strip = blk.add(jpeg)
    stamp = when.strftime("%Y:%m:%d %H:%M:%S")

    # SIS sub-records, then the SIS header
    sis1 = blk.add(SIS_BLOCK_1)
    sis3 = blk.add(struct.pack("<HHHHHHII", 0, 24, thumb_w, thumb_h, 1, 6, strip, len(jpeg)))
    sis6 = blk.add(struct.pack("<I", len(jpeg)) + b"\0" * 122)
    tm = when.timetuple()
    sis_hdr = (
        b"SIS0" + struct.pack("<HH", 0x0101, 0)
        + struct.pack("<9h", tm.tm_sec, tm.tm_min, tm.tm_hour, tm.tm_mday, tm.tm_mon - 1, tm.tm_year - 1900,
                      (tm.tm_wday + 1) % 7, tm.tm_yday - 1, 0)
        + b"\0" * 32 + struct.pack("<H", 3)
        + struct.pack("<hhI", 1, 1, sis1) + struct.pack("<hhI", 3, 1, sis3) + struct.pack("<hhI", 6, 1, sis6)
    )
    sis = blk.add(sis_hdr)

    n, dt = _ascii(stamp)
    exif = _ifd(blk, [
        (36864, 7, 4, b"0210"),
        (36868, 2, n, dt),
        (40960, 7, 4, b"0100"),
        (40961, 3, 1, 1),
        (40962, 4, 1, full_w),
        (40963, 4, 1, full_h),
    ])
    desc_n, desc = _ascii("<OME-metadata not available>")
    entries = [
        (256, 4, 1, thumb_w),
        (257, 4, 1, thumb_h),
        (258, 3, 3, struct.pack("<3H", 8, 8, 8)),
        (259, 3, 1, 7),
        (262, 3, 1, 2),
        (270, 2, desc_n, desc),
        (273, 4, 1, strip),
        (274, 3, 1, 1),
        (277, 3, 1, 3),
        (278, 4, 1, thumb_h),
        (279, 4, 1, len(jpeg)),
        (282, 5, 1, struct.pack("<II", 96, 1)),
        (283, 5, 1, struct.pack("<II", 96, 1)),
        (284, 3, 1, 1),
        (296, 3, 1, 2),
        (306, 2, n, dt),
        (530, 3, 2, struct.pack("<HH", 1, 1)),
        (33560, 4, 1, sis),
        (34665, 4, 1, exif),
    ]
    if make:
        entries.append((271, 2, *_ascii(make)))
    if model:
        entries.append((272, 2, *_ascii(model)))
    first_ifd = _ifd(blk, entries)
    while len(blk.data) % 4:
        blk.data += b"\0"
    return bytes(blk.data), first_ifd
