"""Tests against the real SP8 sample (skipped when the sample is not present)."""

import os

import pytest

from metadata_transfer.convert import ConvertOptions, convert_series
from metadata_transfer.readers import open_reader

# Ground truth for Series003/006, read from the LAS X acquisition settings (sequential
# settings 1-4) and confirmed against the channel colour tables shown in LAS X Office.
EXPECTED = [
    # name, excitation, emission window, detector, LUT, modality
    ("Alexa 488", 488, (493.0, 544.8), "HyD 3", "Green", "fluorescence"),
    ("Alexa 647", 647, (652.5, 702.8), "HyD 4", "Magenta", "fluorescence"),
    ("Alexa 568", 561, (566.2, 636.2), "HyD 4", "Yellow", "fluorescence"),
    ("DAPI", 405, (412.0, 480.3), "PMT 1", "Cyan", "fluorescence"),
    ("Transmitted (PMT Trans)", 405, None, "PMT Trans", "Gray", "brightfield"),
]


def test_series_listing(sample_lif):
    with open_reader(sample_lif) as r:
        infos = r.list_series()
    assert [i.name for i in infos] == [f"Series00{n}" for n in range(1, 7)]
    assert all(i.supported for i in infos)
    assert (infos[5].dims.z, infos[5].dims.c, infos[5].dims.x) == (77, 5, 1024)


@pytest.mark.parametrize("index", [2, 5])
def test_channel_mapping_matches_lasx(sample_lif, index):
    with open_reader(sample_lif) as r:
        m = r.metadata(index)
    assert not m.warnings
    assert m.vendor_raw["channel_mapping_verified"] is True
    got = [(c.name, c.excitation_nm, c.emission_range_nm, c.detector, c.color_name, c.modality) for c in m.channels]
    assert got == EXPECTED


def test_calibration_and_optics(sample_lif):
    with open_reader(sample_lif) as r:
        m = r.metadata(5)
    assert m.bits == 12
    assert m.calibration.pixel_size_x_um == pytest.approx(0.284090909, rel=1e-6)
    assert m.calibration.z_step_um == pytest.approx(0.9994, rel=1e-4)
    assert m.objective.name == "HC PL APO CS2 20x/0.75 DRY"
    assert (m.objective.magnification, m.objective.numerical_aperture) == (20, 0.75)
    assert m.orientation.apply is False  # LAS X displays stored pixels unchanged
    assert len(m.acquisition.frame_times_s) == 77


def test_convert_zstack_end_to_end(sample_lif, tmp_path):
    res = convert_series(sample_lif, 4, ConvertOptions(output_dir=str(tmp_path)))
    assert res.ok, res.report_text
    assert os.path.exists(res.output_path)
    base = os.path.splitext(res.output_path)[0]
    assert os.path.exists(base + ".metadata.json") and os.path.exists(base + ".report.txt")
    # second run must not overwrite
    res2 = convert_series(sample_lif, 4, ConvertOptions(output_dir=str(tmp_path), validate=False))
    assert res2.output_path.endswith("Series005 (2).nd2")


def test_custom_channel_names(sample_lif, tmp_path):
    names = ["ZO-1", "Ki67", "Actin", "Nuclei", "BF"]
    res = convert_series(sample_lif, 0, ConvertOptions(output_dir=str(tmp_path)), channel_names=names)
    assert res.ok, res.report_text
    import nd2

    with nd2.ND2File(res.output_path) as f:
        assert [c.channel.name for c in f.metadata.channels] == names[:4]
