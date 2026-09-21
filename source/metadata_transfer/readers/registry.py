# SPDX-License-Identifier: GPL-3.0-or-later
"""Maps file extensions to readers. Add new formats here."""

from __future__ import annotations

import os

from .base import Reader
from .evident_vsi import EvidentVsiReader, vsi_for_path
from .evident_vsi.groups import EvidentVsiGroupReader, group_for
from .leica_lif import LeicaLifReader
from .nikon_nd2 import NikonNd2Reader

READERS: list[type[Reader]] = [
    LeicaLifReader,
    EvidentVsiReader,
    NikonNd2Reader,
]


def resolve_input(path: str) -> str | None:
    """Map a dropped path to the file the readers understand: a supported file itself, the
    `.vsi` that owns a dropped `_<name>_` companion folder / `.ets` file, or the first file of a
    numbered cellSens position group. None if neither."""
    if os.path.isfile(path) and reader_for(path) is not None:
        target = path
    else:
        target = vsi_for_path(path)
    if target and target.lower().endswith(".vsi"):
        group = group_for(target)
        if group is not None:
            return group.first
    return target


def is_companion_dir(path: str) -> bool:
    return os.path.isdir(path) and vsi_for_path(path) is not None


def supported_extensions() -> tuple[str, ...]:
    exts: list[str] = []
    for r in READERS:
        exts.extend(r.extensions)
    return tuple(exts)


def file_dialog_filter() -> str:
    parts = [f"{r.format_name} ({' '.join('*' + e for e in r.extensions)})" for r in READERS]
    all_exts = " ".join("*" + e for e in supported_extensions())
    return ";;".join([f"All supported ({all_exts})", *parts])


def reader_for(path: str) -> type[Reader] | None:
    for r in READERS:
        if r.can_read(path):
            return r
    return None


def open_reader(path: str) -> Reader:
    """Reader for `path`. The first file of a numbered cellSens position group opens as the
    whole group (one series with a position dimension)."""
    cls = reader_for(path)
    if cls is None:
        ext = os.path.splitext(path)[1]
        raise ValueError(f"Unsupported file type '{ext}'. Supported: {', '.join(supported_extensions())}")
    if cls is EvidentVsiReader:
        group = group_for(path)
        if group is not None and os.path.normcase(os.path.abspath(path)) == os.path.normcase(group.first):
            return EvidentVsiGroupReader(group)
    return cls(path)
