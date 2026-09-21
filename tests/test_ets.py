"""ETS tile-file parsing and tile assembly on synthetic files (no sample data needed)."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from metadata_transfer.readers.evident_vsi.ets import EtsFile

TILE = 8


def write_ets(path, planes: dict[tuple[int, ...], np.ndarray], *, extra_sizes, levels=1, compression=0, pixel_type=4, tile=TILE):
    """Build an ETS file from full planes keyed by extra coordinates (level 0). Higher levels are
    2x downsampled copies. Tiles that would be completely empty are still written (like cellSens)."""
    dtype = {2: np.uint8, 4: np.uint16}[pixel_type]
    chunks = []  # (coords, bytes)
    for lv in range(levels):
        for extra, plane in planes.items():
            p = plane
            for _ in range(lv):
                p = p[::2, ::2]
            h, w = p.shape
            cols, rows = -(-w // tile), -(-h // tile)
            for r in range(rows):
                for c in range(cols):
                    t = np.zeros((tile, tile), dtype)
                    blk = p[r * tile : (r + 1) * tile, c * tile : (c + 1) * tile]
                    t[: blk.shape[0], : blk.shape[1]] = blk
                    chunks.append(((c, r, *extra, lv), t.tobytes()))
    n_dims = 2 + len(extra_sizes) + 1
    add = bytearray(228)
    add[0:4] = b"ETS\x00"
    struct.pack_into("<9i", add, 4, 196614, pixel_type, 1, 1, compression, 100, tile, tile, 1)
    struct.pack_into("<ii", add, 148, -1, 1 if levels > 1 else 0)
    img_dims = [planes[next(iter(planes))].shape[1], planes[next(iter(planes))].shape[0], *extra_sizes]
    struct.pack_into("<i", add, 184, len(img_dims))
    struct.pack_into(f"<{len(img_dims)}i", add, 188, *img_dims)
    data_start = 0x40 + len(add)
    body = bytearray()
    records = []
    for coords, raw in chunks:
        records.append((coords, data_start + len(body), len(raw)))
        body += raw
    chunk_off = data_start + len(body)
    table = bytearray()
    for i, (coords, off, size) in enumerate(records):
        table += struct.pack(f"<i{n_dims}iqii", n_dims, *coords, off, size, i)
    head = bytearray(0x40)
    head[0:4] = b"SIS\x00"
    struct.pack_into("<iiiqiiqii", head, 4, 0x40, 3, n_dims, 0x40, len(add), 0, chunk_off, len(records), 0)
    with open(path, "wb") as fh:
        fh.write(head)
        fh.write(add)
        fh.write(body)
        fh.write(table)


def _planes(shape=(11, 13), sizes=(1, 2, 3), seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    for t in range(sizes[0]):
        for z in range(sizes[1]):
            for c in range(sizes[2]):
                out[(t, z, c)] = rng.integers(0, 60000, shape, dtype=np.uint16)
    return out


def test_header_and_coordinates(tmp_path):
    planes = _planes()
    path = tmp_path / "frame_t_0.ets"
    write_ets(path, planes, extra_sizes=[1, 2, 3])
    ets = EtsFile(str(path))
    h = ets.header
    assert (h.n_dims, h.n_extra_dims, h.has_level_coordinate) == (6, 3, True)
    assert h.image_dims == (13, 11, 1, 2, 3)
    assert h.dtype == np.dtype("uint16") and h.compression == 0 and (h.tile_x, h.tile_y) == (TILE, TILE)
    assert ets.n_levels == 1
    assert ets.extra_ranges(0) == [1, 2, 3]
    assert ets.tile_grid(0) == (2, 2)
    assert ets.unsupported_reason() == ""


def test_plane_assembly_and_cropping(tmp_path):
    planes = _planes()
    path = tmp_path / "frame_t_0.ets"
    write_ets(path, planes, extra_sizes=[1, 2, 3])
    ets = EtsFile(str(path))
    for extra, plane in planes.items():
        got = ets.read_plane(0, extra, 13, 11)
        np.testing.assert_array_equal(got, plane)
    # bands cover the plane exactly, in order
    bands = list(ets.iter_bands(0, (0, 1, 2), 13, 11))
    assert [y0 for y0, _ in bands] == [0, 8]
    np.testing.assert_array_equal(np.concatenate([b for _, b in bands]), planes[(0, 1, 2)])


def test_tile_origin_shifts_pixels(tmp_path):
    planes = _planes(shape=(9, 9), sizes=(1, 1, 1))
    path = tmp_path / "frame_t_0.ets"
    write_ets(path, planes, extra_sizes=[1, 1, 1])
    ets = EtsFile(str(path))
    # tile origin (-1, -1): stored pixel (1, 1) is image pixel (0, 0)
    got = ets.read_plane(0, (0, 0, 0), 8, 8, origin=(-1, -1))
    np.testing.assert_array_equal(got, planes[(0, 0, 0)][1:9, 1:9])


def test_pyramid_levels(tmp_path):
    planes = _planes(shape=(20, 30), sizes=(1, 1, 1))
    path = tmp_path / "frame_t_0.ets"
    write_ets(path, planes, extra_sizes=[1, 1, 1], levels=3)
    ets = EtsFile(str(path))
    assert ets.n_levels == 3 and ets.header.use_pyramid
    assert ets.level_shape(0, 30, 20) == (30, 20)
    assert ets.level_shape(1, 30, 20) == (15, 10)
    assert ets.level_shape(2, 30, 20) == (8, 5)
    np.testing.assert_array_equal(ets.read_plane(1, (0, 0, 0), 15, 10), planes[(0, 0, 0)][::2, ::2])


@pytest.mark.parametrize("compression,expect", [(3, "JPEG 2000"), (2, "JPEG")])
def test_compressed_is_reported(tmp_path, compression, expect):
    planes = _planes(shape=(8, 8), sizes=(1, 1, 1))
    path = tmp_path / "frame_t_0.ets"
    write_ets(path, planes, extra_sizes=[1, 1, 1], compression=compression)
    assert expect in EtsFile(str(path)).unsupported_reason()


def test_uint8(tmp_path):
    rng = np.random.default_rng(1)
    planes = {(0,): rng.integers(0, 255, (8, 8), dtype=np.uint8)}
    path = tmp_path / "frame_t_0.ets"
    write_ets(path, planes, extra_sizes=[1], pixel_type=2)
    ets = EtsFile(str(path))
    assert ets.header.dtype == np.dtype("uint8")
    np.testing.assert_array_equal(ets.read_plane(0, (0,), 8, 8), planes[(0,)])
