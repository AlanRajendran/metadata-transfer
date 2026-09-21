# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-position cellSens experiments: numbered `.vsi` files that belong together.

cellSens saves one image document per stage position (Process Manager "XY-Positions" or the
Experiment Manager with stage moves), auto-numbered `<name>_01.vsi`, `<name>_02.vsi`, ... (see the
cellSens user manual, "Acquiring multi-channel Z-stack images at different positions"). This
module finds such a set so the reader can present it as one series with a position dimension,
which becomes one ND2 with an XY loop.

Files form a group when they sit in the same folder, share `<name>` and the numbering style
(`_01`, `_Pos01`, `_P01`), and each holds one convertible image with the same size, Z, T,
channels, pixel type and pixel size, at different stage positions. Anything else is converted
file by file as before.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from typing import Iterator

import numpy as np

from ...model import SeriesInfo, SeriesMetadata, StagePosition
from ..base import Reader

#: whether numbered position files are combined (GUI setting / CLI --no-group)
ENABLED = True

_NUMBERED = re.compile(r"^(?P<base>.+?)_(?P<tag>Pos|P|)(?P<num>\d{1,4})\.vsi$", re.IGNORECASE)


@dataclass
class PositionGroup:
    base: str  # file name without the number
    folder: str
    members: list[str]  # absolute paths, in position order

    @property
    def first(self) -> str:
        return self.members[0]


def _candidates(path: str) -> tuple[str, list[tuple[int, str]]] | None:
    folder, name = os.path.split(os.path.abspath(path))
    m = _NUMBERED.match(name)
    if not m:
        return None
    base, tag = m.group("base"), m.group("tag").lower()
    found = []
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    for entry in entries:
        mm = _NUMBERED.match(entry)
        if mm and mm.group("base") == base and mm.group("tag").lower() == tag:
            found.append((int(mm.group("num")), os.path.join(folder, entry)))
    found.sort()
    return base, found


def find_group(path: str, signature) -> PositionGroup | None:
    """The position group `path` belongs to, or None.

    `signature(path) -> (key, stage_xy) | None` describes one file: files are grouped when their
    keys are equal and their stage positions differ.
    """
    if not ENABLED:
        return None
    cand = _candidates(path)
    if cand is None:
        return None
    base, found = cand
    if len(found) < 2:
        return None
    sigs = []
    for _num, member in found:
        try:
            sig = signature(member)
        except Exception:
            sig = None
        if sig is None:
            return None
        sigs.append(sig)
    keys = {s[0] for s in sigs}
    stages = [s[1] for s in sigs]
    if len(keys) != 1 or None in stages or len({(round(x, 1), round(y, 1)) for x, y in stages}) != len(stages):
        return None
    return PositionGroup(base=base, folder=os.path.dirname(found[0][1]), members=[m for _n, m in found])


def file_signature(path: str):
    """(grouping key, stage X/Y) of a single-image `.vsi`, or None when it cannot be grouped."""
    from .reader import EvidentVsiReader

    with EvidentVsiReader(path) as r:
        ok = [i for i in r.list_series() if i.supported]
        if len(ok) != 1 or ok[0].pyramid_levels:
            return None
        m = r.metadata(ok[0].index)
        d = m.dims
        cal = m.calibration
        key = (
            d.x, d.y, d.z, d.c, d.t, m.dtype,
            round(cal.pixel_size_x_um or 0, 6), round(cal.pixel_size_y_um or 0, 6),
            tuple(ch.name for ch in m.channels),
            m.scan_settings.get("Experiment", ""),
        )
        a = m.acquisition
        return key, (None if a.stage_x_um is None or a.stage_y_um is None else (a.stage_x_um, a.stage_y_um))


_CACHE: dict[tuple, PositionGroup | None] = {}


def group_for(path: str) -> PositionGroup | None:
    """Cached per folder listing (file names and sizes), so the GUI can ask repeatedly."""
    if not ENABLED:
        return None
    folder = os.path.dirname(os.path.abspath(path))
    try:
        stamp = tuple(sorted((e.name, e.stat().st_mtime_ns) for e in os.scandir(folder) if e.name.lower().endswith(".vsi")))
    except OSError:
        stamp = ()
    key = (os.path.normcase(os.path.abspath(path)), stamp)
    if key not in _CACHE:
        _CACHE[key] = find_group(path, file_signature)
    return _CACHE[key]


class EvidentVsiGroupReader(Reader):
    """Numbered cellSens position files presented as one series with a position dimension."""

    extensions = (".vsi",)
    format_name = "Evident VSI"

    def __init__(self, group: PositionGroup) -> None:
        super().__init__(group.first)
        from .reader import EvidentVsiReader

        self.group = group
        self.output_stem = group.base
        self._readers = [EvidentVsiReader(m) for m in group.members]
        self._index = [next(i.index for i in r.list_series() if i.supported) for r in self._readers]
        self._metas: dict[int, list[SeriesMetadata]] = {}

    def close(self) -> None:
        for r in self._readers:
            r.close()

    def _member_metas(self, level: int = 0) -> list[SeriesMetadata]:
        if level not in self._metas:
            self._metas[level] = [r.metadata(i, level=level) for r, i in zip(self._readers, self._index)]
        return self._metas[level]

    def list_series(self) -> list[SeriesInfo]:
        info = self._readers[0].list_series()[self._index[0]]
        d = copy.deepcopy(info.dims)
        d.p = len(self._readers)
        names = ", ".join(os.path.basename(m) for m in self.group.members)
        return [SeriesInfo(index=0, name=f"{self.group.base} ({d.p} positions)", path=names, dims=d, dtype=info.dtype)]

    def metadata(self, index: int, *, apply_orientation: bool = False, level: int = 0) -> SeriesMetadata:
        if index != 0:
            raise IndexError(f"series {index} does not exist")
        metas = self._member_metas(level)
        m = copy.deepcopy(metas[0])
        d = m.dims
        d.p = len(metas)
        m.series_index = 0
        m.series_name = self.group.base
        starts = [x.acquisition.start for x in metas if x.acquisition.start is not None]
        t_ref = min(starts) if starts else None
        m.acquisition.start = t_ref
        m.positions = [
            StagePosition(
                x_um=x.acquisition.stage_x_um,
                y_um=x.acquisition.stage_y_um,
                z_um=x.acquisition.stage_z_um,
                name=os.path.splitext(os.path.basename(path))[0],
            )
            for x, path in zip(metas, self.group.members)
        ]
        # frame times: each file's own times, shifted by its start relative to the earliest file
        per_file = []
        for x in metas:
            off = (x.acquisition.start - t_ref).total_seconds() if (x.acquisition.start and t_ref) else 0.0
            per_file.append([off + v for v in (x.acquisition.frame_times_s or [])])
        times = []
        for t, p, z in d.frame_keys():
            ft = per_file[p]
            times.append(ft[t * d.z + z] if len(ft) == d.t * d.z else 0.0)
        m.acquisition.frame_times_s = times
        m.scan_settings = dict(m.scan_settings)
        m.scan_settings["Position files"] = ", ".join(os.path.basename(p) for p in self.group.members)
        m.warnings = sorted({w for x in metas for w in x.warnings})
        m.vendor_raw = {"position_files": [os.path.basename(p) for p in self.group.members], **m.vendor_raw}
        return m

    def iter_bands(self, meta: SeriesMetadata, t: int, p: int, z: int) -> Iterator[tuple[int, np.ndarray]]:
        member = self._member_metas(meta.level)[p]
        yield from self._readers[p].iter_bands(member, t, 0, z)

    def iter_frames(self, meta: SeriesMetadata) -> Iterator[tuple[int, int, int, np.ndarray]]:
        d = meta.dims
        for t, p, z in d.frame_keys():
            frame = np.empty((d.y, d.x, d.c), dtype=np.dtype(meta.dtype))
            for y0, band in self.iter_bands(meta, t, p, z):
                frame[y0 : y0 + band.shape[0]] = band
            yield t, p, z, frame

    def raw_metadata_xml(self, index: int) -> str:
        trees = [json.loads(r.raw_metadata_xml(i)) for r, i in zip(self._readers, self._index)]
        return json.dumps({"position_files": [os.path.basename(m) for m in self.group.members], "tag_trees": trees},
                          ensure_ascii=False, default=str)
