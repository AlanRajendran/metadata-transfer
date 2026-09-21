"""Reader for Olympus/Evident `.ets` tile files (the pixel data of a `.vsi`).

Layout (little endian; reference: Bio-Formats CellSensReader.parseETSFile, verified on
cellSens Dimension 4.4 files):

* Volume header: "SIS\\0", int32 headerSize, int32 version, int32 nDimensions,
  int64 additionalHeaderOffset, int32 additionalHeaderSize, 4 reserved,
  int64 usedChunkOffset, int32 nUsedChunks, 4 reserved.
* Additional header: "ETS\\0", int32 version, int32 pixelType, int32 sizeC, int32 colorspace,
  int32 compression, int32 quality, int32 tileX, int32 tileY, int32 tileZ, 17 int32 hints,
  background colour (sizeC * bytesPerPixel bytes, padded to 40), int32 componentOrder,
  int32 usePyramid, then (at byte 184 of the additional header) int32 nImageDims followed by
  the image size per dimension: width, height, extra dimensions... (e.g. 5, 1957, 1957, 1, 20, 3).
* Chunk table: nUsedChunks records of int32 nDims, int32 coords[nDims], int64 offset,
  int32 size, int32 unused. coords = (tile column, tile row, extra dimensions..., [level]).

The meaning of the extra dimensions (Z/C/T order) is not in the ETS; it comes from the
`.vsi` tag tree. This module only deals with tiles and coordinates.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

PIXEL_TYPES = {
    1: np.dtype("int8"),
    2: np.dtype("uint8"),
    3: np.dtype("int16"),
    4: np.dtype("uint16"),
    5: np.dtype("int32"),
    6: np.dtype("uint32"),
    7: np.dtype("int64"),
    8: np.dtype("uint64"),
    9: np.dtype("float32"),
    10: np.dtype("float64"),
}
COMPRESSION_NAMES = {0: "raw", 2: "JPEG", 3: "JPEG 2000", 5: "lossless JPEG", 8: "PNG", 9: "BMP"}
RAW = 0


class EtsFormatError(ValueError):
    pass


@dataclass
class EtsHeader:
    n_dims: int
    pixel_type: int
    size_c: int
    colorspace: int
    compression: int
    quality: int
    tile_x: int
    tile_y: int
    tile_z: int
    component_order: int
    use_pyramid: bool
    n_chunks: int
    background: bytes = b""
    image_dims: tuple[int, ...] = ()  # width, height, extra dimension sizes (from the header)

    @property
    def dtype(self) -> np.dtype:
        try:
            return PIXEL_TYPES[self.pixel_type]
        except KeyError:
            raise EtsFormatError(f"unknown ETS pixel type {self.pixel_type}") from None

    @property
    def n_extra_dims(self) -> int:
        """Number of chunk coordinates besides tile column/row and the trailing level index.

        The header's image-dimension list is authoritative (its length minus X and Y); the
        chunk records always carry one more coordinate, the pyramid level, even when the
        file has a single level and `use_pyramid` is false.
        """
        if len(self.image_dims) >= 2:
            return len(self.image_dims) - 2
        return self.n_dims - 2 - (1 if self.use_pyramid else 0)

    @property
    def has_level_coordinate(self) -> bool:
        return self.n_dims > 2 + self.n_extra_dims

    @property
    def compression_name(self) -> str:
        return COMPRESSION_NAMES.get(self.compression, f"code {self.compression}")

    @property
    def background_value(self) -> int:
        """Fill value for tiles that are not stored (empty regions of a stitched mosaic)."""
        dt = PIXEL_TYPES.get(self.pixel_type)
        if dt is None or len(self.background) < dt.itemsize:
            return 0
        try:
            return int(np.frombuffer(self.background[: dt.itemsize], dt)[0])
        except (ValueError, TypeError):
            return 0


@dataclass
class Chunk:
    coords: tuple[int, ...]
    offset: int
    size: int


@dataclass
class Level:
    """Tiles of one pyramid level, indexed by (extra coords) -> {(col, row): Chunk}."""

    index: int
    planes: dict[tuple[int, ...], dict[tuple[int, int], Chunk]] = field(default_factory=dict)
    max_col: int = 0
    max_row: int = 0


class EtsFile:
    def __init__(self, path: str) -> None:
        self.path = path
        self.size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(0x30)
            if head[:4] != b"SIS\x00":
                raise EtsFormatError("not an ETS file (missing SIS magic)")
            (_hsz, _ver, n_dims, add_off, add_sz, _r1, chunk_off, n_chunks, _r2) = struct.unpack_from(
                "<iiiqiiqii", head, 4
            )
            fh.seek(add_off)
            add = fh.read(add_sz)
            if add[:4] != b"ETS\x00":
                raise EtsFormatError("not an ETS file (missing ETS header)")
            (_ver2, pixel_type, size_c, colorspace, compression, quality, tile_x, tile_y, tile_z) = struct.unpack_from(
                "<9i", add, 4
            )
            bg_start = 40 + 17 * 4
            dtype = PIXEL_TYPES.get(pixel_type)
            bg_len = size_c * (dtype.itemsize if dtype is not None else 1)
            background = add[bg_start : bg_start + bg_len]
            pos = bg_start + 40
            component_order, use_pyramid = (
                struct.unpack_from("<ii", add, pos) if pos + 8 <= len(add) else (0, 0)
            )
            image_dims: tuple[int, ...] = ()
            if len(add) >= 188:
                n_img = struct.unpack_from("<i", add, 184)[0]
                if 2 <= n_img <= 8 and 188 + 4 * n_img <= len(add):
                    image_dims = struct.unpack_from(f"<{n_img}i", add, 188)
            self.header = EtsHeader(
                n_dims=n_dims,
                pixel_type=pixel_type,
                size_c=size_c,
                colorspace=colorspace,
                compression=compression,
                quality=quality,
                tile_x=tile_x,
                tile_y=tile_y,
                tile_z=tile_z,
                component_order=component_order,
                use_pyramid=bool(use_pyramid),
                n_chunks=n_chunks,
                background=bytes(background),
                image_dims=image_dims,
            )
            fh.seek(chunk_off)
            rec = 4 + n_dims * 4 + 16
            table = fh.read(rec * n_chunks)
        self.levels: dict[int, Level] = {}
        fmt = f"<i{n_dims}iqii"
        for i in range(n_chunks):
            vals = struct.unpack_from(fmt, table, i * rec)
            coords = vals[1 : 1 + n_dims]
            offset, size = vals[1 + n_dims], vals[2 + n_dims]
            col, row = coords[0], coords[1]
            n_extra = self.header.n_extra_dims
            extra = tuple(coords[2 : 2 + n_extra])
            level_i = coords[2 + n_extra] if self.header.has_level_coordinate else 0
            lvl = self.levels.setdefault(level_i, Level(level_i))
            lvl.planes.setdefault(extra, {})[(col, row)] = Chunk(tuple(coords), offset, size)
            lvl.max_col = max(lvl.max_col, col)
            lvl.max_row = max(lvl.max_row, row)
        self._fh = None

    # ------------------------------------------------------------------ info
    @property
    def n_levels(self) -> int:
        return (max(self.levels) + 1) if self.levels else 0

    def extra_ranges(self, level: int = 0) -> list[int]:
        """Number of distinct values per extra coordinate at `level` (e.g. [1, 20, 3])."""
        lvl = self.levels.get(level)
        if lvl is None or not lvl.planes:
            return [1] * self.header.n_extra_dims
        n = self.header.n_extra_dims
        out = []
        for k in range(n):
            out.append(max(p[k] for p in lvl.planes) + 1)
        return out

    def tile_grid(self, level: int) -> tuple[int, int]:
        lvl = self.levels.get(level)
        if lvl is None:
            return (0, 0)
        return (lvl.max_col + 1, lvl.max_row + 1)

    def level_shape(self, level: int, width0: int, height0: int) -> tuple[int, int]:
        """(width, height) of a pyramid level following the cellSens/Bio-Formats halving rule."""
        w, h = width0, height0
        for lv in range(1, level + 1):
            cols, rows = self.tile_grid(lv)
            max_w = self.header.tile_x * max(cols, 1)
            max_h = self.header.tile_y * max(rows, 1)
            nw, nh = w // 2, h // 2
            if w % 2 == 1 and nw < max_w:
                nw += 1
            elif nw > max_w:
                nw = max_w
            if h % 2 == 1 and nh < max_h:
                nh += 1
            elif nh > max_h:
                nh = max_h
            w, h = nw, nh
        return w, h

    def unsupported_reason(self) -> str:
        h = self.header
        if h.compression != RAW:
            return f"compressed pixel data ({h.compression_name}) is not supported in this version"
        if h.pixel_type not in PIXEL_TYPES:
            return f"unknown pixel type {h.pixel_type}"
        if h.dtype not in (np.dtype("uint8"), np.dtype("uint16")):
            return f"pixel type {h.dtype} is not supported in this version"
        if h.size_c != 1:
            return f"interleaved {h.size_c}-component (RGB) tiles are not supported in this version"
        if h.tile_z != 1:
            return f"3D tiles (tileZ={h.tile_z}) are not supported in this version"
        return ""

    # ------------------------------------------------------------------ pixels
    def _open(self):
        if self._fh is None:
            self._fh = open(self.path, "rb", buffering=0)
        return self._fh

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def read_tile(self, chunk: Chunk) -> np.ndarray:
        h = self.header
        n = h.tile_x * h.tile_y * h.size_c
        dtype = h.dtype
        fh = self._open()
        fh.seek(chunk.offset)
        buf = fh.read(min(chunk.size, n * dtype.itemsize))
        if len(buf) < n * dtype.itemsize:
            # short chunk: pad with background
            arr = np.zeros(n, dtype=dtype)
            arr[: len(buf) // dtype.itemsize] = np.frombuffer(buf[: len(buf) - len(buf) % dtype.itemsize], dtype)
        else:
            arr = np.frombuffer(buf, dtype)
        if h.size_c > 1:
            return arr.reshape(h.tile_y, h.tile_x, h.size_c)
        return arr.reshape(h.tile_y, h.tile_x)

    def read_band(
        self,
        level: int,
        extra: tuple[int, ...],
        y0: int,
        y1: int,
        width: int,
        origin: tuple[int, int] = (0, 0),
        fill: int | None = None,
    ) -> np.ndarray:
        """Image rows [y0, y1) of one plane at `level` as an array (y1-y0, width).

        `origin` = pixel offset of tile (0, 0) at level 0 (cellSens "tile origin"); the stored
        tile grid may extend beyond the image, which is cropped away here.
        """
        h = self.header
        dtype = h.dtype
        if fill is None:
            fill = h.background_value
        out = np.full((y1 - y0, width), fill, dtype=dtype)
        lvl = self.levels.get(level)
        if lvl is None:
            return out
        tiles = lvl.planes.get(extra)
        if not tiles:
            return out
        scale = 2**level
        # cellSens/Bio-Formats scale the tile origin with truncating integer division
        ox, oy = int(origin[0] / scale), int(origin[1] / scale)
        tx, ty = h.tile_x, h.tile_y
        row0 = max(0, (y0 - oy) // ty)
        row1 = (y1 - 1 - oy) // ty
        for row in range(row0, row1 + 1):
            ty0 = row * ty + oy  # image y of the tile's first row
            iy0, iy1 = max(ty0, y0), min(ty0 + ty, y1)
            if iy1 <= iy0:
                continue
            for col in range(0, lvl.max_col + 1):
                tx0 = col * tx + ox
                ix0, ix1 = max(tx0, 0), min(tx0 + tx, width)
                if ix1 <= ix0:
                    continue
                chunk = tiles.get((col, row))
                if chunk is None:
                    continue
                tile = self.read_tile(chunk)
                out[iy0 - y0 : iy1 - y0, ix0:ix1] = tile[iy0 - ty0 : iy1 - ty0, ix0 - tx0 : ix1 - tx0]
        return out

    def read_plane(
        self, level: int, extra: tuple[int, ...], width: int, height: int, origin: tuple[int, int] = (0, 0)
    ) -> np.ndarray:
        return self.read_band(level, extra, 0, height, width, origin)

    def iter_bands(
        self, level: int, extra: tuple[int, ...], width: int, height: int, origin: tuple[int, int] = (0, 0)
    ) -> Iterator[tuple[int, np.ndarray]]:
        """Yield (y0, band) covering the plane in tile-row-aligned bands."""
        ty = self.header.tile_y
        oy = int(origin[1] / (2**level))
        y = 0
        while y < height:
            # end of the tile row containing y
            y_end = min(height, ((y - oy) // ty + 1) * ty + oy)
            if y_end <= y:
                y_end = min(height, y + ty)
            yield y, self.read_band(level, extra, y, y_end, width, origin)
            y = y_end

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()
