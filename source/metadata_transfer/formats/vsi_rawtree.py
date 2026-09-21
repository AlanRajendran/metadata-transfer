# SPDX-License-Identifier: GPL-3.0-or-later
"""Lossless model of the cellSens tag tree, for writing `.vsi` files.

`readers/evident_vsi/tagtree.py` decodes the tree into values for reading. This module keeps
every byte instead, so that a tree can be parsed, edited and written back. A file written back
without edits is byte-identical to the original (tested on every cellSens sample).

Layout (all integers little-endian):

* The file starts with a TIFF header (`II*\\0` + offset of the first IFD); the root volume
  starts at byte 8 and runs to the end of the file. The TIFF IFD and the thumbnail are stored
  inside the tree, as the content of a type-10 field (`[2016]`), with absolute file offsets.
* Volume header (24 bytes): int16 24, int16 21321, int32 volume version, int64 offset of the
  first field (24, or 0 in an empty volume), int32 flags (low 28 bits = field count), int32 pad.
* Field header (16 bytes, 20 with bit 27 of the field type): int32 field type, int32 tag,
  uint32 offset of the next field (relative to the volume start, 0 = last), int32 data size,
  [int32 second tag]. Field type bits: 27 second tag, 28 extended, 29 array, 30 inline,
  31 new volume; the low 24 bits are the data type.
* Plain field: `data size` bytes of value. Inline field: the value is the data size itself.
* Extended field, data type 1 or 2: data size 0, the content is one nested volume.
* Extended field, other data types: the content ends at volume start + data size + header
  length; data type 0 holds consecutive volumes, others (10 = TIFF block) are opaque bytes.
"""

from __future__ import annotations

import copy
import struct
from dataclasses import dataclass, field
from typing import Iterator

VOLUME_MAGIC = 21321
BIT_SECOND_TAG = 0x08000000
BIT_EXTENDED = 0x10000000
BIT_ARRAY = 0x20000000
BIT_INLINE = 0x40000000
BIT_NEW_VOLUME = 0x80000000

#: data types used when encoding values
T_INT, T_UINT, T_LONG, T_DOUBLE, T_BOOL, T_TCHAR, T_TIMESTAMP, T_UNICODE = 5, 6, 7, 10, 12, 13, 17, 8192
T_INT_ARRAY, T_INT_ARRAY2, T_DOUBLE_PAIR, T_INT_RECT, T_BGR = 8195, 8199, 260, 259, 270


class RawTreeError(ValueError):
    pass


@dataclass
class RawField:
    ftype: int  # full 32-bit field type (flags + data type)
    tag: int
    second_tag: int | None = None
    dsize: int = 0  # as stored; recomputed for extended fields when writing
    payload: bytes = b""  # plain fields
    volume: "RawVolume | None" = None  # extended data type 1/2
    volumes: "list[RawVolume] | None" = None  # extended data type 0
    blob: bytes | None = None  # other extended data types (TIFF block)
    gap: bytes = b""  # bytes between the end of this field and the next field

    @property
    def data_type(self) -> int:
        return self.ftype & 0x00FFFFFF

    @property
    def extended(self) -> bool:
        return bool(self.ftype & BIT_EXTENDED)

    @property
    def inline(self) -> bool:
        return bool(self.ftype & BIT_INLINE)

    @property
    def header_len(self) -> int:
        return 20 if self.ftype & BIT_SECOND_TAG else 16

    # ------------------------------------------------------------- values
    def get_int(self) -> int:
        if self.inline:
            return self.dsize
        if len(self.payload) >= 4:
            return struct.unpack_from("<i", self.payload)[0]
        raise RawTreeError(f"field [{self.tag}] has no integer value")

    def set_int(self, value: int) -> None:
        if self.inline:
            self.dsize = int(value)
        elif self.data_type in (T_LONG, T_TIMESTAMP, 8):
            self.payload = struct.pack("<q", int(value))
        else:
            self.payload = struct.pack("<i", int(value))

    def get_double(self) -> float:
        return struct.unpack_from("<d", self.payload)[0]

    def set_double(self, value: float) -> None:
        self.payload = struct.pack("<d", float(value))

    def get_doubles(self) -> list[float]:
        return list(struct.unpack_from(f"<{len(self.payload) // 8}d", self.payload))

    def set_doubles(self, values) -> None:
        self.payload = struct.pack(f"<{len(values)}d", *[float(v) for v in values])

    def get_ints(self) -> list[int]:
        return list(struct.unpack_from(f"<{len(self.payload) // 4}i", self.payload))

    def set_ints(self, values) -> None:
        self.payload = struct.pack(f"<{len(values)}i", *[int(v) for v in values])

    def get_str(self) -> str:
        if self.data_type == T_UNICODE:
            return self.payload.decode("utf-16-le", "replace").rstrip("\x00")
        return self.payload.decode("latin-1", "replace").rstrip("\x00")

    def set_str(self, text: str) -> None:
        if self.data_type == T_UNICODE:
            self.payload = (text or "").encode("utf-16-le")
        else:
            self.payload = (text or "").encode("latin-1", "replace")

    # ------------------------------------------------ measured quantities
    def quantity(self) -> "RawField | None":
        """The `Value` field (268435458) of a measured-quantity volume ({units, value})."""
        return self.volume.first(268435458) if self.volume is not None else None


@dataclass
class RawVolume:
    version: int = 1
    first_field_offset: int = 24
    flags_high: int = 0
    pad: int = 0
    fields: list[RawField] = field(default_factory=list)

    # ---------------------------------------------------------- navigation
    def find(self, tag: int, second_tag: int | None = None) -> list[RawField]:
        return [f for f in self.fields if f.tag == tag and (second_tag is None or f.second_tag == second_tag)]

    def first(self, tag: int, second_tag: int | None = None) -> RawField | None:
        for f in self.fields:
            if f.tag == tag and (second_tag is None or f.second_tag == second_tag):
                return f
        return None

    def need(self, tag: int, second_tag: int | None = None) -> RawField:
        f = self.first(tag, second_tag)
        if f is None:
            raise RawTreeError(f"tag [{tag}]{'#' + str(second_tag) if second_tag is not None else ''} not found")
        return f

    def sub(self, tag: int, second_tag: int | None = None) -> "RawVolume":
        """Nested volume of a field (type 1/2, or the first volume of a type-0 field)."""
        f = self.need(tag, second_tag)
        if f.volume is not None:
            return f.volume
        if f.volumes:
            return f.volumes[0]
        raise RawTreeError(f"tag [{tag}] has no nested volume")

    def path(self, *tags: int) -> "RawVolume":
        v = self
        for t in tags:
            v = v.sub(t)
        return v

    def walk(self) -> Iterator[tuple["RawVolume", RawField]]:
        for f in self.fields:
            yield self, f
            if f.volume is not None:
                yield from f.volume.walk()
            for c in f.volumes or []:
                yield from c.walk()

    def clone(self) -> "RawVolume":
        return copy.deepcopy(self)


# ------------------------------------------------------------------ parse
def _parse_volume(buf: bytes, fp: int) -> tuple[RawVolume, int]:
    if fp + 24 > len(buf):
        raise RawTreeError(f"volume at {fp} runs past the end of the data")
    hs, magic, version, dfo, flags, pad = struct.unpack_from("<hhiqii", buf, fp)
    if hs != 24 or magic != VOLUME_MAGIC:
        raise RawTreeError(f"no tag volume at {fp}")
    vol = RawVolume(version=version, first_field_offset=dfo, flags_high=flags & ~0x0FFFFFFF, pad=pad)
    n = flags & 0x0FFFFFFF
    end = fp + 24
    if dfo == 0 or n == 0:
        return vol, end
    pos = fp + dfo
    for k in range(n):
        ftype, tag, nxt, dsize = struct.unpack_from("<iiIi", buf, pos)
        hdr = 16
        second = None
        if ftype & BIT_SECOND_TAG:
            second = struct.unpack_from("<i", buf, pos + 16)[0]
            hdr = 20
        p = pos + hdr
        fld = RawField(ftype=ftype & 0xFFFFFFFF, tag=tag, second_tag=second, dsize=dsize)
        dtype = ftype & 0x00FFFFFF
        if ftype & BIT_EXTENDED and dtype in (1, 2):
            fld.volume, fend = _parse_volume(buf, p)
        elif ftype & BIT_EXTENDED:
            stop = fp + dsize + hdr
            if dtype == 0:
                fld.volumes = []
                q = p
                while q + 24 <= stop:
                    child, q2 = _parse_volume(buf, q)
                    fld.volumes.append(child)
                    q = q2
                if q != stop:
                    raise RawTreeError(f"volumes of field [{tag}] at {pos} do not fill its content")
            else:
                fld.blob = bytes(buf[p:stop]) if stop > p else b""  # data size 0: empty block
            fend = max(stop, p)
        elif ftype & BIT_INLINE:
            fend = p
        else:
            fld.payload = bytes(buf[p : p + dsize])
            fend = p + dsize
        vol.fields.append(fld)
        end = max(end, fend)
        if nxt == 0 or k == n - 1:
            if nxt != 0:
                raise RawTreeError(f"last field [{tag}] at {pos} points to another field")
            break
        nxt_abs = fp + nxt
        if nxt_abs < fend:
            raise RawTreeError(f"field [{tag}] at {pos}: next field at {nxt_abs} overlaps")
        fld.gap = bytes(buf[fend:nxt_abs])
        pos = nxt_abs
    return vol, end


def parse(buf: bytes) -> RawVolume:
    """Root volume of a `.vsi` file (bytes of the whole file)."""
    if buf[:4] != b"II*\x00":
        raise RawTreeError("not a little-endian TIFF / .vsi file")
    root, end = _parse_volume(buf, 8)
    if end != len(buf):
        raise RawTreeError(f"the tag tree ends at {end}, the file at {len(buf)}")
    return root


def parse_file(path: str) -> RawVolume:
    with open(path, "rb") as fh:
        return parse(fh.read())


# --------------------------------------------------------------- serialise
@dataclass
class _Blob:
    field: RawField
    offset: int  # absolute file offset of the blob content


def _serialize_volume(vol: RawVolume, base: int, blobs: list[_Blob]) -> bytes:
    """Bytes of `vol` placed at absolute file offset `base`."""
    body = bytearray()
    n = len(vol.fields)
    dfo = vol.first_field_offset if (n and vol.first_field_offset) else (0 if not n else 24)
    if n and dfo != 24:
        raise RawTreeError("only volumes whose first field follows the header can be written")
    flags = (n & 0x0FFFFFFF) | (vol.flags_high & ~0x0FFFFFFF)
    header = struct.pack("<hhiqii", 24, VOLUME_MAGIC, vol.version, dfo if n else vol.first_field_offset, flags, vol.pad)
    body += header
    for i, f in enumerate(vol.fields):
        start = len(body)
        hdr = f.header_len
        content = bytearray()
        dtype = f.data_type
        if f.extended and dtype in (1, 2):
            assert f.volume is not None
            content += _serialize_volume(f.volume, base + start + hdr, blobs)
            dsize = f.dsize
        elif f.extended:
            if dtype == 0:
                for c in f.volumes or []:
                    content += _serialize_volume(c, base + start + hdr + len(content), blobs)
            else:
                blobs.append(_Blob(f, base + start + hdr))
                content += f.blob or b""
            dsize = start + len(content) if content else 0  # empty content: data size 0 (as cellSens)
        elif f.inline:
            dsize = f.dsize
        else:
            content += f.payload
            dsize = len(f.payload)
        end = start + hdr + len(content)
        last = i == n - 1
        nxt = 0 if last else end + len(f.gap)
        head = struct.pack("<IiIi", f.ftype & 0xFFFFFFFF, f.tag, nxt, dsize)
        if f.ftype & BIT_SECOND_TAG:
            head += struct.pack("<i", f.second_tag or 0)
        body += head
        body += content
        if not last:
            body += f.gap
    return bytes(body)


def serialize(root: RawVolume, tiff_block=None) -> bytes:
    """Whole `.vsi` file. `tiff_block(offset) -> (bytes, first_ifd_offset)` builds the content of
    the type-10 field at its final absolute offset (its length must not depend on the offset);
    without it, stored blobs are written unchanged and the first-IFD offset is taken from
    `root.first_ifd_offset` (set by `parse_with_header`)."""
    blobs: list[_Blob] = []
    body = _serialize_volume(root, 8, blobs)
    first_ifd = getattr(root, "_first_ifd", 0)
    if tiff_block is not None:
        if len(blobs) != 1:
            raise RawTreeError(f"expected one TIFF block, found {len(blobs)}")
        b = blobs[0]
        data, first_ifd = tiff_block(b.offset)
        if len(data) != len(b.field.blob or b""):
            raise RawTreeError("the TIFF block changed length; set field.blob to a placeholder of the final size")
        b.field.blob = data
        body = _serialize_volume(root, 8, [])
    return b"II*\x00" + struct.pack("<I", first_ifd) + body


def parse_with_header(buf: bytes) -> RawVolume:
    root = parse(buf)
    root._first_ifd = struct.unpack_from("<I", buf, 4)[0]  # type: ignore[attr-defined]
    return root
