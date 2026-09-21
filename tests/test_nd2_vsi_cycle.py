# SPDX-License-Identifier: GPL-3.0-or-later
"""ND2 -> VSI -> ND2 with synthetic data: time, positions and Z survive the full cycle.

A synthetic multipoint ND2 (T2 x P3 x Z2 x C2) is written with the ND2 writer, read with the ND2
reader, converted to three numbered `.vsi` files (one per position, as cellSens stores them),
which the VSI reader recognises as one position group and converts back into one ND2.
"""

from __future__ import annotations

import os

import nd2
import numpy as np
import pytest
from test_vsi_writer import synthetic_frames, synthetic_meta

from metadata_transfer.convert import ConvertOptions, convert_series
from metadata_transfer.readers import open_reader, resolve_input
from metadata_transfer.readers.evident_vsi import groups
from metadata_transfer.writers import write_nd2


@pytest.fixture()
def multipoint_nd2(tmp_path):
    meta = synthetic_meta(x=80, y=60, z=2, t=2, c=2, p=3)
    frames = synthetic_frames(meta, seed=3)
    path = str(tmp_path / "src" / "plate.nd2")
    os.makedirs(os.path.dirname(path))
    write_nd2(meta, iter(frames), path)
    return path, meta, frames


def test_nd2_reader_on_synthetic_multipoint(multipoint_nd2):
    path, meta, frames = multipoint_nd2
    with open_reader(path) as r:
        info = r.list_series()[0]
        assert info.supported and (info.dims.t, info.dims.p, info.dims.z, info.dims.c) == (2, 3, 2, 2)
        m = r.metadata(0)
        assert [c.name for c in m.channels] == ["GFP", "Phase"]
        assert m.channels[1].modality == "brightfield"
        assert [round(p.x_um) for p in m.positions] == [1000, 1500, 2000]
        assert m.calibration.pixel_size_x_um == pytest.approx(0.325)
        for (t, p, z, fr), (t2, p2, z2, src) in zip(r.iter_frames(m), frames):
            assert (t, p, z) == (t2, p2, z2)
            np.testing.assert_array_equal(fr, src)


def test_full_cycle_nd2_vsi_nd2(tmp_path, multipoint_nd2):
    path, meta, frames = multipoint_nd2
    vsi_dir = tmp_path / "vsi"
    res = convert_series(path, 0, ConvertOptions(output_dir=str(vsi_dir)))
    assert res.ok, res.report_text
    assert res.output_format == "Evident VSI"
    assert [os.path.basename(o) for o in res.outputs] == ["plate_01.vsi", "plate_02.vsi", "plate_03.vsi"]
    # any member resolves to the group, which reads as one series with three positions
    first = resolve_input(str(vsi_dir / "plate_02.vsi"))
    assert os.path.basename(first) == "plate_01.vsi"
    with open_reader(first) as r:
        (info,) = r.list_series()
        assert info.dims.p == 3 and info.name == "plate (3 positions)"
    back = convert_series(first, 0, ConvertOptions(output_dir=str(tmp_path / "nd2")))
    assert back.ok, back.report_text
    assert os.path.basename(back.output_path) == "plate.nd2"
    with nd2.ND2File(back.output_path) as f:
        assert dict(f.sizes) == {"T": 2, "P": 3, "Z": 2, "C": 2, "Y": 60, "X": 80}
        pts = next(lp for lp in f.experiment if lp.type == "XYPosLoop").parameters.points
        assert [round(pt.stagePositionUm.x) for pt in pts] == [1000, 1500, 2000]
        for i, (_t, _p, _z, src) in enumerate(frames):
            np.testing.assert_array_equal(np.moveaxis(np.asarray(f.read_frame(i)), 0, -1), src)


def test_grouping_can_be_switched_off(tmp_path, multipoint_nd2, monkeypatch):
    path, _meta, _frames = multipoint_nd2
    res = convert_series(path, 0, ConvertOptions(output_dir=str(tmp_path / "vsi")))
    assert res.ok
    monkeypatch.setattr(groups, "ENABLED", False)
    member = str(tmp_path / "vsi" / "plate_02.vsi")
    assert resolve_input(member) == member
    with open_reader(member) as r:
        assert r.list_series()[0].dims.p == 1


def test_numbered_files_at_one_position_are_not_grouped(tmp_path):
    meta = synthetic_meta(x=40, y=30, z=1, t=1, c=1)
    from metadata_transfer.writers import write_vsi

    for k in (1, 2):
        write_vsi(meta, iter(synthetic_frames(meta, seed=k)), str(tmp_path / f"shot_{k:02d}.vsi"))
    assert resolve_input(str(tmp_path / "shot_02.vsi")).endswith("shot_02.vsi")
