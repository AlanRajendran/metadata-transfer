"""Parser for the Olympus/Evident tag tree stored inside a cellSens `.vsi` file.

A `.vsi` is a little-endian TIFF whose IFDs only hold thumbnails; the acquisition metadata
lives in a proprietary tree of "tag volumes" that starts at byte 8. The layout below was
established with Bio-Formats' CellSensReader.readTags as the reference and verified on
cellSens Dimension 4.4 files (see docs/vsi_format_notes.md):

* Volume header (24 bytes): int16 headerSize (24), int16 version (21321 = "IS"),
  int32 volumeVersion, int64 dataFieldOffset, int32 flags (low 28 bits = tag count), 4 pad.
* Field header (16 bytes, 20 with an extra tag): int32 fieldType, int32 tag,
  uint32 nextField (offset of the next field relative to the volume start; 0 = last),
  int32 dataSize, [int32 secondTag when bit 27 of fieldType is set].
  fieldType bits: 27 extra tag, 28 extended (nested) field, 29 array, 30 inline value,
  31 new volume; the low 24 bits are the data type.
* Plain fields: dataSize bytes of value (or the value itself when inline).
* Extended fields with type 1/2 (property set / multi-dim volume): dataSize is 0 and the
  content is exactly one nested volume.
* Extended fields with type 0 (new volume header) or other types (e.g. 10 = TIFF IFD blob):
  the content spans [dataStart, volumeStart + dataSize + fieldHeaderLen). Type 0 holds
  consecutive child volumes.
"""

from __future__ import annotations

import datetime as _dt
import json
import struct
from typing import Any, Iterator

from .tag_names import CONTEXT_NAMES, TAG_NAMES, VOLUME_NAMES

NEW_VOLUME_HEADER, PROPERTY_SET_VOLUME, NEW_MDIM_VOLUME_HEADER = 0, 1, 2

# data types (low 24 bits of fieldType)
CHAR, UCHAR, SHORT, USHORT, INT, UINT, LONG, ULONG, FLOAT, DOUBLE = 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
COMPLEX, BOOLEAN, TCHAR, DWORD, TIMESTAMP, DATE = 11, 12, 13, 14, 17, 18
RGB, BGR, FIELD_TYPE, MEM_MODEL, COLOR_SPACE = 269, 270, 271, 272, 273
UNICODE_TCHAR = 8192
_INT_ARRAY_TYPES = {256, 257, 258, 259, 267, 274, 275, 276, 277, 8195, 8199, 8200, 8470}
_DOUBLE_ARRAY_TYPES = {COMPLEX, 260, 261, 262, 263, 264, 265, 266, 268, 279, 280}
_INT_TYPES = {INT, UINT, DWORD, FIELD_TYPE, MEM_MODEL, COLOR_SPACE}

#: tag of the "Value" field inside a measured-quantity volume (value + units + ...)
VALUE_TAG = 268435458
UNITS_TAG = 268435456

_VOLUME_MAGIC = 21321


class VsiFormatError(ValueError):
    pass


class Field:
    """One tag inside a volume. Either `value` is set or `children` (nested volumes)."""

    __slots__ = ("tag", "second_tag", "data_type", "offset", "size", "value", "children", "parent_tag")

    def __init__(self, tag: int, second_tag: int | None, data_type: int, offset: int, size: int, parent_tag: int):
        self.tag = tag
        self.second_tag = second_tag
        self.data_type = data_type
        self.offset = offset
        self.size = size
        self.value: Any = None
        self.children: list[Volume] | None = None
        self.parent_tag = parent_tag

    @property
    def name(self) -> str:
        return CONTEXT_NAMES.get((self.parent_tag, self.tag)) or TAG_NAMES.get(self.tag) or VOLUME_NAMES.get(
            self.tag
        ) or f"tag{self.tag}"

    @property
    def vol(self) -> Volume | None:
        return self.children[0] if self.children else None

    @property
    def vols(self) -> list[Volume]:
        return self.children or []

    def scalar(self) -> Any:
        """Plain value, or the `Value` of a measured-quantity volume ({Value, Units, ...})."""
        if self.children is None:
            return self.value
        v = self.vol
        if v is not None:
            f = v.first(VALUE_TAG)
            if f is not None:
                return f.value
        return None

    def units(self) -> str | None:
        v = self.vol
        if v is None:
            return None
        f = v.first(UNITS_TAG)
        return f.value if f is not None else None

    def __repr__(self) -> str:
        return f"Field({self.tag}{'#' + str(self.second_tag) if self.second_tag is not None else ''}, {self.name!r})"


class Volume:
    __slots__ = ("offset", "end", "tag", "fields")

    def __init__(self, offset: int, tag: int):
        self.offset = offset
        self.end = offset
        self.tag = tag  # tag of the field that contains this volume (0 for the root)
        self.fields: list[Field] = []

    # ---------------------------------------------------------------- navigation
    def find(self, tag: int, second_tag: int | None = None) -> list[Field]:
        return [f for f in self.fields if f.tag == tag and (second_tag is None or f.second_tag == second_tag)]

    def first(self, tag: int, second_tag: int | None = None) -> Field | None:
        for f in self.fields:
            if f.tag == tag and (second_tag is None or f.second_tag == second_tag):
                return f
        return None

    def scalar(self, tag: int, default: Any = None) -> Any:
        f = self.first(tag)
        if f is None:
            return default
        v = f.scalar()
        return default if v is None else v

    def sub(self, tag: int, second_tag: int | None = None) -> Volume | None:
        """First nested volume of the first field with this tag."""
        f = self.first(tag, second_tag)
        return f.vol if f is not None else None

    def subs(self, tag: int) -> list[Volume]:
        """All nested volumes of all fields with this tag, in file order."""
        out: list[Volume] = []
        for f in self.find(tag):
            out.extend(f.vols)
        return out

    def walk(self) -> Iterator[Field]:
        for f in self.fields:
            yield f
            for c in f.vols:
                yield from c.walk()

    def __repr__(self) -> str:
        return f"Volume(@{self.offset}, {len(self.fields)} fields)"


def _read_value(buf: bytes, pos: int, data_type: int, size: int) -> Any:
    if size <= 0:
        return None
    if data_type in (CHAR, UCHAR):
        return buf[pos]
    if data_type in (SHORT, USHORT):
        return struct.unpack_from("<h", buf, pos)[0]
    if data_type in _INT_TYPES:
        return struct.unpack_from("<i", buf, pos)[0]
    if data_type in (LONG, ULONG, TIMESTAMP):
        return struct.unpack_from("<q", buf, pos)[0]
    if data_type == FLOAT:
        return struct.unpack_from("<f", buf, pos)[0]
    if data_type in (DOUBLE, DATE):
        return struct.unpack_from("<d", buf, pos)[0]
    if data_type == BOOLEAN:
        return bool(buf[pos])
    if data_type in (TCHAR, UNICODE_TCHAR):
        raw = buf[pos : pos + size]
        try:
            s = raw.decode("utf-16-le")
        except UnicodeDecodeError:
            s = raw.decode("latin-1")
        return s.rstrip("\x00")
    if data_type in _INT_ARRAY_TYPES:
        return list(struct.unpack_from(f"<{size // 4}i", buf, pos))
    if data_type in _DOUBLE_ARRAY_TYPES:
        return list(struct.unpack_from(f"<{size // 8}d", buf, pos))
    if data_type in (RGB, BGR):
        raw = bytes(buf[pos : pos + size])
        if size == 3:
            return list(raw) if data_type == RGB else list(raw[::-1])
        # a lookup table: n entries of 3 bytes
        entries = [list(raw[i : i + 3]) for i in range(0, size - size % 3, 3)]
        if data_type == BGR:
            entries = [e[::-1] for e in entries]
        return entries
    return ("raw", data_type, size)


def parse_volume(buf: bytes, fp: int, parent_tag: int = 0) -> Volume:
    vol = Volume(fp, parent_tag)
    if fp + 24 > len(buf):
        raise VsiFormatError(f"tag volume at {fp} runs past the end of the file")
    header_size, version, _volume_version, data_field_offset, flags = struct.unpack_from("<hhiqi", buf, fp)
    if header_size != 24 or version != _VOLUME_MAGIC:
        raise VsiFormatError(f"not a tag volume at {fp} (headerSize={header_size}, version={version})")
    tag_count = flags & 0x0FFFFFFF
    pos = fp + data_field_offset
    end = fp + 24
    for _ in range(tag_count):
        if pos + 16 > len(buf):
            break
        field_type, tag, next_field, data_size = struct.unpack_from("<iiIi", buf, pos)
        hdr = 16
        extra_tag = bool(field_type & 0x08000000)
        extended = bool(field_type & 0x10000000)
        inline = bool(field_type & 0x40000000)
        data_type = field_type & 0x00FFFFFF
        second_tag = None
        if extra_tag:
            second_tag = struct.unpack_from("<i", buf, pos + 16)[0]
            hdr = 20
        p = pos + hdr
        if tag < 0:
            break
        fld = Field(tag, second_tag, data_type, p, data_size, parent_tag)
        if extended and data_type in (PROPERTY_SET_VOLUME, NEW_MDIM_VOLUME_HEADER):
            child = parse_volume(buf, p, tag)
            fld.children = [child]
            fld.size = child.end - p
            end = max(end, child.end)
        elif extended:
            stop = min(fp + data_size + hdr, len(buf))
            fld.size = stop - p
            if data_type == NEW_VOLUME_HEADER:
                fld.children = []
                q = p
                while q + 24 <= stop:
                    child = parse_volume(buf, q, tag)
                    fld.children.append(child)
                    if child.end <= q:
                        break
                    q = child.end
            else:
                fld.value = ("blob", data_type, stop - p)
            end = max(end, stop)
        elif inline:
            fld.value = data_size
            fld.size = 0
            end = max(end, p)
        else:
            fld.value = _read_value(buf, p, data_type, data_size)
            end = max(end, p + data_size)
        vol.fields.append(fld)
        if next_field == 0:
            break
        pos = fp + next_field
    vol.end = end
    return vol


def parse_vsi(path: str) -> Volume:
    """Parse the whole tag tree of a `.vsi` file (the file is small; thumbnails aside)."""
    with open(path, "rb") as fh:
        buf = fh.read()
    if buf[:4] != b"II*\x00":
        raise VsiFormatError("not a little-endian TIFF / .vsi file")
    return parse_volume(buf, 8, 0)


# ---------------------------------------------------------------------- export
def _convert(v: Any) -> Any:
    if isinstance(v, tuple):
        return list(v)
    return v


def to_tree(vol: Volume) -> list[dict[str, Any]]:
    """JSON-friendly nested representation (for the sidecar and `--dump-tags`)."""
    out: list[dict[str, Any]] = []
    for f in vol.fields:
        entry: dict[str, Any] = {"tag": f.tag, "name": f.name}
        if f.second_tag is not None:
            entry["index"] = f.second_tag
        if f.children is not None:
            entry["children"] = [to_tree(c) for c in f.children]
        else:
            v = _convert(f.value)
            if f.tag in (14, 2015, 23) and isinstance(v, int) and v > 10**8:
                try:
                    v = f"{v} ({_dt.datetime.fromtimestamp(v, _dt.timezone.utc).isoformat()})"
                except (OverflowError, OSError, ValueError):
                    pass
            entry["value"] = v
        out.append(entry)
    return out


def to_json(vol: Volume) -> str:
    return json.dumps(to_tree(vol), indent=1, ensure_ascii=False, default=str)


def dump_text(vol: Volume) -> str:
    lines: list[str] = []

    def rec(tree: list[dict[str, Any]], indent: int) -> None:
        pre = "  " * indent
        for e in tree:
            head = f"{pre}[{e['tag']}] {e['name']}"
            if "index" in e:
                head += f" #{e['index']}"
            if "children" in e:
                lines.append(head + f"  ({len(e['children'])} volume{'s' if len(e['children']) != 1 else ''})")
                for i, c in enumerate(e["children"]):
                    if len(e["children"]) > 1:
                        lines.append(f"{pre}  -- {i}")
                    rec(c, indent + 1)
            else:
                v = e["value"]
                if isinstance(v, list) and len(v) > 12:
                    v = f"[{len(v)} values: {v[:4]} ...]"
                lines.append(head + f" = {v!r}")

    rec(to_tree(vol), 0)
    return "\n".join(lines)
