# SPDX-License-Identifier: GPL-3.0-or-later
"""Conversions of real NIS-Elements and cellSens files (skipped without samples)."""

from __future__ import annotations

import os

import nd2
import pytest
from conftest import sample

from metadata_transfer.convert import ConvertOptions, convert_series
from metadata_transfer.readers import open_reader

ND2_ROLES = ["nd2_zstack", "nd2_timelapse", "nd2_multipoint", "nd2_large"]


@pytest.mark.parametrize("role", ND2_ROLES)
def test_nd2_to_vsi(tmp_path, role):
    path = sample(role)
    res = convert_series(path, 0, ConvertOptions(output_dir=str(tmp_path)))
    assert res.ok, res.report_text
    with open_reader(path) as r:
        d = r.list_series()[0].dims
    assert len(res.outputs) == d.p
    for out in res.outputs:
        assert os.path.exists(out)


def test_nd2_zstack_metadata():
    with open_reader(sample("nd2_zstack")) as r:
        m = r.metadata(0)
    assert (m.dims.z, m.dims.c) == (32, 4)
    assert m.calibration.z_step_um == pytest.approx(1.4)
    assert m.bits == 14
    assert m.channels[0].exposure_ms == pytest.approx(50.0)
    assert m.channels[3].modality == "brightfield"  # "Phase contrast" is transmitted light
    assert m.acquisition.microscope == "Nikon Ti2"
    assert len(m.z_positions_um) == 32 and m.z_positions_um[1] - m.z_positions_um[0] == pytest.approx(1.4)


def test_multipoint_round_trip_to_one_nd2(tmp_path):
    src = sample("nd2_multipoint")
    res = convert_series(src, 0, ConvertOptions(output_dir=str(tmp_path / "vsi")))
    assert res.ok and len(res.outputs) == 6
    back = convert_series(res.outputs[0], 0, ConvertOptions(output_dir=str(tmp_path / "nd2")))
    assert back.ok, back.report_text
    with nd2.ND2File(src) as a, nd2.ND2File(back.output_path) as b:
        assert dict(a.sizes) == dict(b.sizes)
        for i in range(a.attributes.sequenceCount):
            assert (a.read_frame(i) == b.read_frame(i)).all()


@pytest.mark.parametrize("role", ["vsi_multichannel", "vsi_timelapse"])
def test_vsi_to_nd2_to_vsi(tmp_path, role):
    src = sample(role)
    nd = convert_series(src, 0, ConvertOptions(output_dir=str(tmp_path / "nd2")))
    assert nd.ok, nd.report_text
    back = convert_series(nd.output_path, 0, ConvertOptions(output_dir=str(tmp_path / "vsi")))
    assert back.ok, back.report_text
    with open_reader(src) as a, open_reader(back.output_path) as b:
        ma, mb = a.metadata(0), b.metadata(0)
        assert [c.name for c in ma.channels] == [c.name for c in mb.channels]
        assert [c.excitation_nm for c in ma.channels] == [c.excitation_nm for c in mb.channels]
        assert ma.calibration.pixel_size_x_um == pytest.approx(mb.calibration.pixel_size_x_um)
        for (_, _, _, fa), (_, _, _, fb) in zip(a.iter_frames(ma), b.iter_frames(mb)):
            assert (fa == fb).all()
