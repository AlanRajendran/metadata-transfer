# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader interface. Each input format implements one Reader subclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

import numpy as np

from ..model import SeriesInfo, SeriesMetadata


class Reader(ABC):
    #: File extensions (lower case, with dot) handled by this reader.
    extensions: tuple[str, ...] = ()
    #: Human readable format name, e.g. "Leica LIF".
    format_name: str = ""

    def __init__(self, path: str) -> None:
        self.path = path

    @classmethod
    def can_read(cls, path: str) -> bool:
        return path.lower().endswith(cls.extensions)

    @abstractmethod
    def list_series(self) -> list[SeriesInfo]:
        """All series in the file, including unsupported ones (with `supported=False`)."""

    @abstractmethod
    def metadata(self, index: int, *, apply_orientation: bool = True, level: int = 0) -> SeriesMetadata:
        """Full canonical metadata for one series. `level` selects a resolution level for
        pyramidal series (0 = full resolution); readers without pyramids ignore it."""

    @abstractmethod
    def iter_frames(self, meta: SeriesMetadata) -> Iterator[tuple[int, int, int, np.ndarray]]:
        """Yield (t, p, z, frame[Y, X, C]) in T -> P -> Z order, oriented per `meta.orientation`."""

    def iter_bands(self, meta: SeriesMetadata, t: int, p: int, z: int) -> Iterator[tuple[int, np.ndarray]]:
        """Yield (y0, band[Yb, X, C]) covering frame (t, p, z) top to bottom, for frames too large
        to hold in memory. Default: the whole frame as one band."""
        for ft, fp, fz, frame in self.iter_frames(meta):
            if (ft, fp, fz) == (t, p, z):
                yield 0, frame
                return

    def raw_metadata_xml(self, index: int) -> str:
        """Vendor metadata for the sidecar: XML text, or a JSON document for formats
        without XML (may be empty)."""
        return ""

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()
