"""Leica regression: 1.0 must produce the same ND2 content as 0.1 for the Test pack series.

The description text carries the converter version and conversion time, which moves every
following byte, so the comparison is semantic: attributes, experiment, metadata, text info
(minus the "Converted by" line) and the pixel data, all read back with the independent `nd2`.
"""

from __future__ import annotations

import os
import re

import nd2
import numpy as np
import pytest

from metadata_transfer.convert import ConvertOptions, convert_series

SERIES = {"Series001": 0, "Series003": 2, "Series005": 4, "Series006": 5}


def _strip_volatile(text: str) -> str:
    return re.sub(r"Converted by: .*", "Converted by: <version>", text)


@pytest.mark.parametrize("name", sorted(SERIES))
def test_matches_golden(tmp_path, sample_lif, golden_dir, name):
    stem = os.path.splitext(os.path.basename(sample_lif))[0]
    golden = os.path.join(golden_dir, f"{stem}_{name}.nd2")
    if not os.path.exists(golden):
        pytest.skip(f"no golden file for {name}")
    res = convert_series(sample_lif, SERIES[name], ConvertOptions(output_dir=str(tmp_path), write_sidecar=False, write_report=False))
    assert res.ok, res.report_text
    assert os.path.basename(res.output_path) == os.path.basename(golden)
    with nd2.ND2File(golden) as g, nd2.ND2File(res.output_path) as f:
        assert f.sizes == g.sizes and f.dtype == g.dtype
        assert f.attributes == g.attributes
        assert f.experiment == g.experiment
        assert f.metadata == g.metadata
        assert f.voxel_size() == g.voxel_size()
        ft, gt = dict(f.text_info), dict(g.text_info)
        assert _strip_volatile(ft.pop("description", "")) == _strip_volatile(gt.pop("description", ""))
        assert ft == gt
        for i in range(f.attributes.sequenceCount):
            np.testing.assert_array_equal(np.asarray(f.read_frame(i)), np.asarray(g.read_frame(i)))
