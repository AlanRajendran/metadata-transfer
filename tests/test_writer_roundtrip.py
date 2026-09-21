"""Synthetic write -> independent read-back (no sample data needed)."""

import datetime as dt

import nd2
import numpy as np

from metadata_transfer.model import Acquisition, Calibration, Channel, Dimensions, Objective, SeriesMetadata
from metadata_transfer.validation import validate
from metadata_transfer.writers import write_nd2


def _synthetic(tmp_path):
    meta = SeriesMetadata(
        source_path="synthetic.lif", source_format="test", series_index=0, series_name="Synthetic",
        dims=Dimensions(x=64, y=48, z=4, c=2, t=2), dtype="uint16", bits=12,
        calibration=Calibration(0.25, 0.25, 0.75, 2.0),
        objective=Objective("HC PL APO 63x/1.40 OIL", 63, 1.4, "Oil", 1.518),
        channels=[
            Channel("GFP", excitation_nm=488, emission_range_nm=(500, 550), color_rgb=(0, 255, 0), color_name="Green", detector="HyD 1", pinhole_um=50.0),
            Channel("Trans", excitation_nm=488, modality="brightfield", color_rgb=(255, 255, 255), color_name="Gray", detector="PMT Trans", pinhole_um=50.0),
        ],
        zoom=3.0, pinhole_um=50.0,
        acquisition=Acquisition(start=dt.datetime(2026, 7, 29, 12, 37, 53), microscope="Leica TCS SP8"),
        z_positions_um=[10.0, 10.75, 11.5, 12.25],
    )
    rng = np.random.default_rng(0)
    frames = [(t, 0, z, rng.integers(0, 4096, (48, 64, 2), dtype=np.uint16)) for t in range(2) for z in range(4)]
    out = str(tmp_path / "synthetic.nd2")
    hashes = write_nd2(meta, iter(frames), out)
    return meta, frames, out, hashes


def test_synthetic_roundtrip_all_checks_pass(tmp_path):
    meta, frames, out, hashes = _synthetic(tmp_path)
    checks = validate(out, meta, hashes, text_info=True)
    failed = [c for c in checks if c.ok is False]
    assert not failed, failed
    with nd2.ND2File(out) as f:
        assert f.sizes == {"T": 2, "Z": 4, "C": 2, "Y": 48, "X": 64}
        arr = f.asarray()
        np.testing.assert_array_equal(arr[1, 2], np.moveaxis(frames[1 * 4 + 2][3], -1, 0))
        assert "GFP" in f.text_info["description"]
        assert f.metadata.channels[0].microscope.objectiveName == "HC PL APO 63x/1.40 OIL"


def test_banded_write_matches_whole_frames(tmp_path):
    """Frames above the band threshold are streamed as full-width bands; the file and the
    per-frame hashes must be identical to a whole-frame write."""
    meta, frames, out_whole, hashes_whole = _synthetic(tmp_path)
    by_key = {(t, z): fr for t, _p, z, fr in frames}

    def bands(m, t, p, z):
        fr = by_key[(t, z)]
        for y0 in range(0, fr.shape[0], 20):
            yield y0, fr[y0 : y0 + 20]

    out = str(tmp_path / "banded.nd2")
    hashes = write_nd2(meta, iter([]), out, bands=bands, band_threshold_bytes=1)
    assert hashes == hashes_whole
    checks = validate(out, meta, hashes, text_info=True)
    assert not [c for c in checks if c.ok is False]
    with nd2.ND2File(out) as f, nd2.ND2File(out_whole) as g:
        np.testing.assert_array_equal(f.asarray(), g.asarray())


def test_cancel_removes_partial_file(tmp_path):
    meta, frames, _, _ = _synthetic(tmp_path)
    from metadata_transfer.writers import Cancelled

    out = tmp_path / "cancel.nd2"
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3

    try:
        write_nd2(meta, iter(frames), str(out), cancel=cancel)
    except Cancelled:
        pass
    else:
        raise AssertionError("expected Cancelled")
    assert not out.exists() and not (tmp_path / "cancel.nd2.part").exists()
