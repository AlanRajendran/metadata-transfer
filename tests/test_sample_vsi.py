"""Tests against the real cellSens samples (skipped when they are not present).

Expected values were read from the OlyVIA "Properties" panel of each file and from the
per-frame TIFF files cellSens wrote next to the time-lapse ETS (exact pixel ground truth).
"""

from __future__ import annotations

import glob
import os

import numpy as np
import pytest
import tifffile

from metadata_transfer.convert import ConvertOptions, convert_series
from metadata_transfer.readers import open_reader, resolve_input
from metadata_transfer.readers.evident_vsi.tagtree import dump_text, parse_vsi


def test_multichannel_zstack_metadata(vsi_multichannel):
    with open_reader(vsi_multichannel) as r:
        infos = r.list_series()
        assert [(i.name, i.path, i.supported) for i in infos] == [("C640, BF, C488", "stack1", True)]
        assert (infos[0].dims.x, infos[0].dims.y, infos[0].dims.z, infos[0].dims.c, infos[0].dims.t) == (1957, 1957, 20, 3, 1)
        m = r.metadata(0)
    assert m.imaging_mode == "spinning_disk" and m.bits == 16 and m.dtype == "uint16"
    assert m.calibration.pixel_size_x_um == pytest.approx(0.325, abs=1e-9)
    assert m.calibration.z_step_um == pytest.approx(1.87, abs=1e-6)
    assert m.objective.name == "LUCPLFLN 20x"
    assert (m.objective.magnification, m.objective.numerical_aperture, m.objective.immersion) == (20, 0.45, "air")
    assert m.acquisition.start.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-17 20:21:54"
    assert m.acquisition.software.startswith("cellSens Dimension 4.4.1")
    assert m.acquisition.stage_x_um == pytest.approx(-63797.13, abs=0.01)
    names = [c.name for c in m.channels]
    assert names == ["C640", "BF", "C488"]
    c640, bf, c488 = m.channels
    # ND2 wavelengths = cellSens values (as shown by OlyVIA); the filter band travels in filter_name
    assert (c640.excitation_nm, c640.emission_range_nm, c640.filter_name) == (625, (670, 670), "B685/40")
    assert (c488.excitation_nm, c488.emission_range_nm, c488.filter_name) == (494, (518, 518), "B525/50")
    assert c640.exposure_ms == pytest.approx(499.961) and bf.exposure_ms == pytest.approx(9.961) and c488.exposure_ms == pytest.approx(5000)
    assert (c640.color_rgb, bf.color_rgb, c488.color_rgb) == ((255, 0, 0), (255, 255, 255), (0, 255, 2))
    assert bf.modality == "brightfield" and c640.modality == "fluorescence"
    assert c640.laser_intensity_pct == 100 and c488.laser_intensity_pct == 50
    assert c640.detector == "Hamamatsu ORCA-Fusion"
    assert m.z_positions_um[0] == pytest.approx(6824.35) and m.z_positions_um[-1] == pytest.approx(6788.82, abs=1e-6)
    assert len(m.acquisition.frame_times_s) == 20 and m.acquisition.frame_times_s[0] == 0.0
    assert m.orientation.apply is False and not m.orientation.flip_x and not m.orientation.flip_y
    assert not m.pyramid_levels and not m.warnings


def test_multichannel_frames_read(vsi_multichannel):
    with open_reader(vsi_multichannel) as r:
        m = r.metadata(0)
        frames = list(r.iter_frames(m))
    assert len(frames) == 20 and frames[0][3].shape == (1957, 1957, 3) and frames[0][3].dtype == np.uint16
    # BF is bright, the fluorescence channels are dim: channel order must be C640, BF, C488
    means = frames[0][3].reshape(-1, 3).mean(axis=0)
    assert means[1] > 10 * means[0] and means[1] > 10 * means[2]


def test_timelapse_layers_merged_into_one_series(vsi_timelapse):
    with open_reader(vsi_timelapse) as r:
        # cellSens stores the DIC channel as its own layer (stack10000, switched off in the display
        # mapping). Same geometry and stage position as the C488 layer -> one two-channel series.
        assert r.info.is_hidden(10000) and not r.info.is_hidden(1)
        infos = r.list_series()
        assert [(i.name, i.path) for i in infos] == [("C488 + DIC", "stack1+stack10000")]
        info = infos[0]
        assert info.supported and not info.hidden
        assert (info.dims.t, info.dims.z, info.dims.c, info.dims.x) == (31, 1, 2, 2000)
        m = r.metadata(0)
        assert [c.name for c in m.channels] == ["C488", "DIC"]
        assert m.calibration.time_step_s == pytest.approx(60.0, abs=1e-6)
        assert m.calibration.pixel_size_x_um == pytest.approx(0.26)
        assert m.objective.name == "LUPLAPO S_GEL 25x" and m.objective.immersion == "silicone"
        c488, dic = m.channels
        assert c488.exposure_ms == pytest.approx(99.961)
        assert (c488.excitation_nm, c488.emission_range_nm) == (494, (518, 518))
        assert c488.modality == "fluorescence"
        assert dic.modality == "brightfield" and dic.excitation_nm is None
        assert m.acquisition.frame_times_s[0] == pytest.approx(0.0)
        assert m.acquisition.frame_times_s[-1] == pytest.approx(1800.0)
        assert m.scan_settings["Channel time offset"] == "C488 +0.00 s, DIC +2.27 s"
        assert any("DIC" in w and "not shown" in w for w in m.warnings)
        # cellSens wrote every DIC time point as a TIFF next to the ETS: exact ground truth
        tifs = sorted(glob.glob(os.path.join(r.companion, "stack1", "*.tif")))
        n = 0
        for t, _p, _z, frame in r.iter_frames(m):
            assert frame.shape == (2000, 2000, 2) and frame.dtype == np.uint16
            if t == 0:
                # C488 is dim fluorescence (OlyVIA mean 4.74, spikes excluded), DIC is bright transmitted light
                assert 4 < frame[..., 0].mean() < 6
                assert frame[..., 1].mean() > 10 * frame[..., 0].mean()
            if len(tifs) == 31:
                np.testing.assert_array_equal(frame[..., 1], tifffile.imread(tifs[t]))
            n += 1
        assert n == 31


def test_layers_stay_separate_when_positions_differ(monkeypatch, vsi_timelapse):
    from metadata_transfer.readers.evident_vsi import reader as vsi_reader

    original = vsi_reader.parse_info

    def moved(root):
        info = original(root)
        lay = info.layer(10000)
        lay.origin_um = (lay.origin_um[0] + 500.0, lay.origin_um[1])  # another stage position
        return info

    monkeypatch.setattr(vsi_reader, "parse_info", moved)
    with open_reader(vsi_timelapse) as r:
        assert [i.path for i in r.list_series()] == ["stack1", "stack10000"]
    monkeypatch.setattr(vsi_reader, "parse_info", original)
    monkeypatch.setattr(vsi_reader.EvidentVsiReader, "merge_layers", False)
    with open_reader(vsi_timelapse) as r:
        assert [i.path for i in r.list_series()] == ["stack1", "stack10000"]


def test_overview_pyramid(vsi_overview):
    with open_reader(vsi_overview) as r:
        infos = r.list_series()
        assert len(infos) == 1 and infos[0].supported and infos[0].kind == "overview image"
        assert (infos[0].dims.x, infos[0].dims.y) == (72407, 48923)
        assert [(lv.x, lv.y) for lv in infos[0].pyramid_levels[:3]] == [(72407, 48923), (36204, 24462), (18102, 12231)]
        assert len(infos[0].pyramid_levels) == 9
        m = r.metadata(0, level=8)
        assert (m.dims.x, m.dims.y) == (283, 192)
        assert m.calibration.pixel_size_x_um == pytest.approx(1.625 * 256)
        assert m.objective.name == "UPLFLN 4x"
        small = next(r.iter_frames(m))[3][..., 0]
        assert small.shape == (192, 283) and small.max() > 0
        # the level-8 plane must equal the 2x box-average of the level-7 plane (same tile
        # placement and background fill on both levels) ...
        m7 = r.metadata(0, level=7)
        mid = next(r.iter_frames(m7))[3][..., 0].astype(float)[:382, :566]
        box = (mid[0::2, 0::2] + mid[1::2, 0::2] + mid[0::2, 1::2] + mid[1::2, 1::2]) / 4
        corr = np.corrcoef(box.ravel(), small[:191, :283].astype(float).ravel())[0, 1]
        assert corr > 0.999
        # ... and match the thumbnail cellSens embedded in the .vsi itself
        with tifffile.TiffFile(vsi_overview) as tf:
            thumbs = [pg.asarray() for pg in tf.pages if pg.shape == (191, 282)]
        if thumbs:
            corr = np.corrcoef(thumbs[0].astype(float).ravel(), small[:191, :282].astype(float).ravel())[0, 1]
            assert corr > 0.999


def test_companion_folder_missing(tmp_path, vsi_multichannel):
    alone = tmp_path / os.path.basename(vsi_multichannel)
    alone.write_bytes(open(vsi_multichannel, "rb").read())
    with open_reader(str(alone)) as r:
        infos = r.list_series()
    assert len(infos) == 1 and not infos[0].supported
    assert "companion folder" in infos[0].reason and "not found" in infos[0].reason


def test_resolve_companion_inputs(vsi_timelapse):
    with open_reader(vsi_timelapse) as r:
        companion = r.companion
    assert resolve_input(companion) == vsi_timelapse
    assert resolve_input(os.path.join(companion, "stack1", "frame_t_0.ets")) == vsi_timelapse
    assert resolve_input(vsi_timelapse) == vsi_timelapse


def test_tag_dump_mentions_devices(vsi_multichannel):
    text = dump_text(parse_vsi(vsi_multichannel))
    assert "Hamamatsu ORCA-Fusion" in text and "B685/40" in text and "Stack name" in text


def test_convert_timelapse_merged_roundtrip(tmp_path, vsi_timelapse):
    import nd2

    res = convert_series(vsi_timelapse, 0, ConvertOptions(output_dir=str(tmp_path)))
    assert res.ok, res.report_text
    # the two layers are one series -> no series suffix
    stem = os.path.splitext(os.path.basename(vsi_timelapse))[0]
    assert os.path.basename(res.output_path) == stem + ".nd2"
    assert not res.failed_checks
    assert os.path.exists(os.path.splitext(res.output_path)[0] + ".metadata.json")
    with nd2.ND2File(res.output_path) as f:
        assert dict(f.sizes) == {"T": 31, "C": 2, "Y": 2000, "X": 2000}
        assert [c.channel.name for c in f.metadata.channels] == ["C488", "DIC"]


def test_convert_single_series_drops_suffix(tmp_path, vsi_multichannel):
    res = convert_series(vsi_multichannel, 0, ConvertOptions(output_dir=str(tmp_path), write_sidecar=False))
    assert res.ok, res.report_text
    stem = os.path.splitext(os.path.basename(vsi_multichannel))[0]
    assert os.path.basename(res.output_path) == stem + ".nd2"
    assert any(w.startswith("output path is") for w in res.warnings) or len(res.output_path) < 240
