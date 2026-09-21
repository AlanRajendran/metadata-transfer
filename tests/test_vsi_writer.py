# SPDX-License-Identifier: GPL-3.0-or-later
"""The `.vsi` writer: lossless tag tree, ETS tiles and pyramids, synthetic round trips.

No sample data is needed except for the byte-identity test, which skips without samples.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os

import numpy as np
import pytest

from metadata_transfer.formats import ets_writer, vsi_rawtree
from metadata_transfer.formats.vsi_document import load_template
from metadata_transfer.model import Acquisition, Calibration, Channel, Dimensions, Objective, SeriesMetadata, StagePosition
from metadata_transfer.readers.evident_vsi.ets import EtsFile
from metadata_transfer.readers.evident_vsi.reader import EvidentVsiReader
from metadata_transfer.validation import validate_vsi
from metadata_transfer.writers import write_vsi


def synthetic_meta(x=96, y=64, z=3, c=2, t=2, p=1, dtype="uint16") -> SeriesMetadata:
    return SeriesMetadata(
        source_path="synthetic.nd2", source_format="Nikon ND2", series_index=0, series_name="Synthetic",
        dims=Dimensions(x=x, y=y, z=z, c=c, t=t, p=p), dtype=dtype, bits=16 if dtype == "uint16" else 8,
        calibration=Calibration(0.325, 0.325, 1.5 if z > 1 else None, 60.0 if t > 1 else None),
        objective=Objective("Plan Apo 20x", 20.0, 0.75, "air", 1.0),
        channels=[
            Channel("GFP", excitation_nm=488, emission_range_nm=(525, 525), color_rgb=(0, 255, 0), detector="Camera X",
                    exposure_ms=100.0),
            Channel("Phase", modality="brightfield", color_rgb=(255, 255, 255), detector="Camera X", exposure_ms=20.0),
        ][:c],
        imaging_mode="widefield",
        acquisition=Acquisition(start=dt.datetime(2026, 5, 21, 15, 6, 10), microscope="Nikon Ti2",
                                stage_x_um=1000.0, stage_y_um=-2000.0, stage_z_um=500.0,
                                frame_times_s=[60.0 * tt + 5.0 * pp + 0.5 * zz for tt in range(t) for pp in range(p) for zz in range(z)]),
        z_positions_um=[500.0 + 1.5 * k for k in range(z)] if z > 1 else None,
        positions=[StagePosition(1000.0 + 500 * k, -2000.0 + 250 * k, 500.0) for k in range(p)],
    )


def synthetic_frames(meta: SeriesMetadata, seed: int = 0):
    d = meta.dims
    rng = np.random.default_rng(seed)
    hi = 60000 if meta.dtype == "uint16" else 250
    return [(t, p, z, rng.integers(0, hi, (d.y, d.x, d.c), dtype=np.dtype(meta.dtype))) for t, p, z in d.frame_keys()]


# ----------------------------------------------------------------- tag tree
def test_template_parses_and_reserialises():
    root = load_template()
    data = vsi_rawtree.serialize(root)
    assert vsi_rawtree.serialize(vsi_rawtree.parse(data)) == data


def test_real_files_round_trip_byte_identical():
    from conftest import SAMPLES

    paths = [SAMPLES[k] for k in ("vsi_multichannel", "vsi_timelapse", "vsi_overview") if k in SAMPLES and os.path.exists(SAMPLES[k])]
    if not paths:
        pytest.skip("no cellSens samples")
    for path in paths:
        buf = open(path, "rb").read()
        assert vsi_rawtree.serialize(vsi_rawtree.parse_with_header(buf)) == buf, path


# ----------------------------------------------------------------- ETS
def test_ets_pyramid_levels_match_downsampling(tmp_path, monkeypatch):
    monkeypatch.setattr(ets_writer, "SINGLE_TILE_MAX_SIDE", 100)
    monkeypatch.setattr(ets_writer, "LARGE_TILE", 64)
    w, h = 301, 157
    rng = np.random.default_rng(1)
    plane = rng.integers(0, 60000, (h, w), dtype=np.uint16)
    path = str(tmp_path / "frame_t_0.ets")
    tx, ty, levels = ets_writer.tile_layout(w, h)
    assert (tx, ty, levels) == (64, 64, 4)
    ets = ets_writer.EtsWriter(path, w, h, (1, 1, 1), np.uint16)
    pw = ets.plane((0, 0, 0))
    for y0 in range(0, h, 23):  # bands that do not align with tiles
        pw.add_rows(plane[y0 : y0 + 23])
    pw.finish()
    ets.close()
    f = EtsFile(path)
    assert f.n_levels == 4
    expect = plane
    for lv in range(4):
        lw, lh = ets_writer.level_shape(w, h, lv)
        assert f.level_shape(lv, w, h) == (lw, lh)
        got = f.read_plane(lv, (0, 0, 0), lw, lh)
        np.testing.assert_array_equal(got, expect)
        # next level: 2x2 mean with the odd last row/column averaged with itself
        a = expect.astype(np.uint32)
        if a.shape[0] % 2:
            a = np.concatenate([a, a[-1:]])
        if a.shape[1] % 2:
            a = np.concatenate([a, a[:, -1:]], axis=1)
        expect = ((a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2] + 2) // 4).astype(np.uint16)
    f.close()


# ------------------------------------------------------------- round trip
@pytest.mark.parametrize("dims", [dict(z=3, t=2, c=2), dict(z=1, t=1, c=1), dict(z=5, t=1, c=2, dtype="uint8")])
def test_synthetic_vsi_round_trip(tmp_path, dims):
    meta = synthetic_meta(**dims)
    frames = synthetic_frames(meta)
    out = str(tmp_path / "synthetic.vsi")
    hashes = write_vsi(meta, iter(frames), out)
    assert os.path.exists(tmp_path / "_synthetic_" / "stack1" / "frame_t_0.ets")
    checks = validate_vsi(out, meta, hashes)
    failed = [c for c in checks if c.ok is False]
    assert not failed, failed
    with EvidentVsiReader(out) as r:
        m = r.metadata(0)
        assert [c.name for c in m.channels] == [c.name for c in meta.channels]
        assert m.acquisition.start == meta.acquisition.start
        for (t, p, z, fr), (_t, _p, _z, src) in zip(r.iter_frames(m), frames):
            np.testing.assert_array_equal(fr, src)


def test_banded_vsi_write_matches_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(ets_writer, "SINGLE_TILE_MAX_SIDE", 50)
    monkeypatch.setattr(ets_writer, "LARGE_TILE", 32)
    meta = synthetic_meta(x=130, y=90, z=2, t=1, c=2)
    frames = synthetic_frames(meta)
    by_key = {(t, z): fr for t, _p, z, fr in frames}

    def bands(m, t, p, z):
        fr = by_key[(t, z)]
        for y0 in range(0, fr.shape[0], 17):
            yield y0, fr[y0 : y0 + 17]

    out = str(tmp_path / "banded.vsi")
    hashes = write_vsi(meta, iter([]), out, bands=bands, band_threshold_bytes=1)
    assert hashes == [hashlib.sha1(np.ascontiguousarray(f).tobytes()).hexdigest() for _t, _p, _z, f in frames]
    checks = validate_vsi(out, meta, hashes)
    assert not [c for c in checks if c.ok is False]
    with EvidentVsiReader(out) as r:
        info = r.list_series()[0]
        assert len(info.pyramid_levels) > 1  # tiled + pyramid above the (patched) size limit


def test_cancel_removes_vsi_outputs(tmp_path):
    from metadata_transfer.writers import Cancelled

    meta = synthetic_meta()
    out = tmp_path / "cancel.vsi"
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(Cancelled):
        write_vsi(meta, iter(synthetic_frames(meta)), str(out), cancel=cancel)
    assert not out.exists() and not (tmp_path / "_cancel_").exists()
