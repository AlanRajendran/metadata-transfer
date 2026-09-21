# SPDX-License-Identifier: GPL-3.0-or-later
"""Writer for Olympus/Evident `.ets` tile files (the pixel data next to a `.vsi`).

Raw (uncompressed) tiles only, laid out like cellSens: chunk coordinates
(tile column, tile row, extra dimensions..., pyramid level). Small images are one tile the size
of the image (cellSens camera acquisitions do the same); large images use 512 x 512 tiles and a
pyramid of 2 x 2 averaged levels down to a single tile, so viewers can zoom out.

Planes are streamed: `PlaneWriter.add_rows()` takes full-width bands top to bottom and writes a
row of tiles whenever one is complete, for every pyramid level, so a multi-GB plane never has to
be held in memory. The reader (`readers/evident_vsi/ets.py`) is the reference for the layout.
"""

from __future__ import annotations

import math
import os
import struct

import numpy as np

PIXEL_TYPE = {np.dtype("uint8"): 2, np.dtype("uint16"): 4}
LARGE_TILE = 512
#: images with a side longer than this get 512 x 512 tiles and a pyramid
SINGLE_TILE_MAX_SIDE = 4096
HEADER_SIZE = 0x40
ADDITIONAL_HEADER_SIZE = 228


def tile_layout(width: int, height: int) -> tuple[int, int, int]:
    """(tile width, tile height, number of pyramid levels) for an image."""
    if max(width, height) <= SINGLE_TILE_MAX_SIDE:
        return width, height, 1
    levels = 1 + max(0, math.ceil(math.log2(max(width, height) / LARGE_TILE)))
    return LARGE_TILE, LARGE_TILE, levels


def level_shape(width: int, height: int, level: int) -> tuple[int, int]:
    w, h = width, height
    for _ in range(level):
        w, h = (w + 1) // 2, (h + 1) // 2
    return w, h


class EtsWriter:
    """One `.ets` file: header, tiles, chunk table."""

    def __init__(
        self,
        path: str,
        width: int,
        height: int,
        extra_sizes: tuple[int, ...],
        dtype,
        *,
        tile: tuple[int, int] | None = None,
        levels: int | None = None,
        background: int = 0,
    ) -> None:
        self.dtype = np.dtype(dtype)
        if self.dtype not in PIXEL_TYPE:
            raise ValueError(f"ETS files are written as uint8 or uint16, not {self.dtype}")
        tx, ty, lv = tile_layout(width, height)
        if tile is not None:
            tx, ty = tile
        if levels is not None:
            lv = levels
        self.width, self.height = width, height
        self.extra_sizes = tuple(int(s) for s in extra_sizes)
        self.tile_x, self.tile_y, self.levels = tx, ty, lv
        self.background = background
        self.path = path
        self._fh = open(path, "wb")
        self._fh.write(b"\0" * (HEADER_SIZE + ADDITIONAL_HEADER_SIZE))
        self._chunks: list[tuple[tuple[int, ...], int, int]] = []
        self._closed = False

    @property
    def n_dims(self) -> int:
        return 2 + len(self.extra_sizes) + 1  # column, row, extra dims, level

    def add_tile(self, col: int, row: int, extra: tuple[int, ...], level: int, tile: np.ndarray) -> None:
        tile = np.ascontiguousarray(tile, dtype=self.dtype)
        if tile.shape != (self.tile_y, self.tile_x):
            full = np.full((self.tile_y, self.tile_x), self.background, dtype=self.dtype)
            full[: tile.shape[0], : tile.shape[1]] = tile
            tile = full
        data = tile.tobytes()
        offset = self._fh.tell()
        self._fh.write(data)
        self._chunks.append(((col, row, *extra, level), offset, len(data)))

    def plane(self, extra: tuple[int, ...]) -> "PlaneWriter":
        return PlaneWriter(self, tuple(int(e) for e in extra))

    def close(self) -> None:
        if self._closed:
            return
        fh = self._fh
        table_offset = fh.tell()
        rec = struct.Struct(f"<i{self.n_dims}iqii")
        for i, (coords, off, size) in enumerate(self._chunks):
            fh.write(rec.pack(self.n_dims, *coords, off, size, 0))
        fh.seek(0)
        head = bytearray(HEADER_SIZE)
        head[0:4] = b"SIS\x00"
        struct.pack_into(
            "<iiiqiiqii", head, 4, HEADER_SIZE, 3, self.n_dims, HEADER_SIZE, ADDITIONAL_HEADER_SIZE, 0,
            table_offset, len(self._chunks), 0,
        )
        add = bytearray(ADDITIONAL_HEADER_SIZE)
        add[0:4] = b"ETS\x00"
        # version, pixel type, sizeC, colour space, compression (0 raw), quality, tile x/y/z
        struct.pack_into("<9i", add, 4, 196614, PIXEL_TYPE[self.dtype], 1, 1, 0, 100, self.tile_x, self.tile_y, 1)
        bg = np.array([self.background], dtype=self.dtype).tobytes()
        add[108 : 108 + len(bg)] = bg
        struct.pack_into("<ii", add, 148, -1, 1 if self.levels > 1 else 0)
        dims = [self.width, self.height, *self.extra_sizes]
        struct.pack_into("<i", add, 184, len(dims))
        struct.pack_into(f"<{len(dims)}i", add, 188, *dims)
        fh.write(head)
        fh.write(add)
        fh.close()
        self._closed = True

    def abort(self) -> None:
        if not self._closed:
            self._fh.close()
            self._closed = True
        try:
            os.remove(self.path)
        except OSError:
            pass


class _Level:
    """Row buffer of one pyramid level of one plane."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.rows: list[np.ndarray] = []  # pending rows for the next tile row
        self.n_pending = 0
        self.tile_row = 0
        self.rows_in = 0
        self.carry: np.ndarray | None = None  # odd row waiting for its partner (downsampling)


class PlaneWriter:
    """Streams one plane (all pyramid levels) into an EtsWriter, top to bottom."""

    def __init__(self, ets: EtsWriter, extra: tuple[int, ...]) -> None:
        self.ets = ets
        self.extra = extra
        self._levels = [_Level(*level_shape(ets.width, ets.height, lv)) for lv in range(ets.levels)]
        self._rows_seen = 0

    def add_rows(self, band: np.ndarray) -> None:
        band = np.asarray(band)
        if band.ndim != 2 or band.shape[1] != self.ets.width:
            raise ValueError(f"expected a band of shape (rows, {self.ets.width}), got {band.shape}")
        self._rows_seen += band.shape[0]
        self._feed(0, band.astype(self.ets.dtype, copy=False))

    def finish(self) -> None:
        if self._rows_seen != self.ets.height:
            raise ValueError(f"plane {self.extra}: {self._rows_seen} of {self.ets.height} rows written")
        for lv in range(len(self._levels)):
            level = self._levels[lv]
            if level.carry is not None and lv + 1 < len(self._levels):
                self._feed(lv + 1, self._downsample(level.carry[None, :]))
                level.carry = None
            self._flush(lv, final=True)

    # ------------------------------------------------------------------ internals
    def _feed(self, lv: int, rows: np.ndarray) -> None:
        level = self._levels[lv]
        level.rows.append(rows)
        level.n_pending += rows.shape[0]
        level.rows_in += rows.shape[0]
        self._flush(lv, final=False)
        if lv + 1 < len(self._levels):
            if level.carry is not None:
                rows = np.concatenate([level.carry[None, :], rows])
                level.carry = None
            n2 = rows.shape[0] // 2 * 2
            if rows.shape[0] > n2:
                level.carry = rows[-1].copy()
            if n2:
                self._feed(lv + 1, self._downsample(rows[:n2]))

    def _downsample(self, rows: np.ndarray) -> np.ndarray:
        """2 x 2 mean (rounded); a single row or an odd last column is averaged with itself."""
        a = rows.astype(np.uint32)
        if a.shape[0] >= 2:
            a = a[0::2] + a[1::2]
        else:
            a = a * 2
        if a.shape[1] % 2:
            a = np.concatenate([a, a[:, -1:]], axis=1)
        a = a[:, 0::2] + a[:, 1::2]
        return ((a + 2) // 4).astype(self.ets.dtype)

    def _flush(self, lv: int, final: bool) -> None:
        level = self._levels[lv]
        ty, tx = self.ets.tile_y, self.ets.tile_x
        while level.n_pending >= ty or (final and level.n_pending > 0):
            block = np.concatenate(level.rows) if len(level.rows) > 1 else level.rows[0]
            take = min(ty, block.shape[0])
            tile_rows, rest = block[:take], block[take:]
            level.rows = [rest] if rest.shape[0] else []
            level.n_pending = rest.shape[0]
            for col in range(math.ceil(level.width / tx)):
                self.ets.add_tile(col, level.tile_row, self.extra, lv, tile_rows[:, col * tx : (col + 1) * tx])
            level.tile_row += 1
