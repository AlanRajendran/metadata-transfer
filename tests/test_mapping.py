import numpy as np

from metadata_transfer import presets
from metadata_transfer.mapping.channel_naming import make_unique, pretty_dye_name, safe_filename
from metadata_transfer.mapping.orientation import orient_plane
from metadata_transfer.model import Calibration, Channel, Dimensions, Objective, Orientation, SeriesMetadata


def _meta(**kw) -> SeriesMetadata:
    base = dict(
        source_path="x.lif", source_format="test", series_index=0, series_name="S",
        dims=Dimensions(x=40, y=30, z=3, c=2), dtype="uint16", bits=12,
        calibration=Calibration(0.1, 0.2, 1.5), objective=Objective(), channels=[Channel("a"), Channel("b")],
    )
    base.update(kw)
    return SeriesMetadata(**base)


def test_pretty_dye_names():
    assert pretty_dye_name("Leica/ALEXA 488") == "Alexa 488"
    assert pretty_dye_name("Leica/DAPI") == "DAPI"
    assert pretty_dye_name("Leica/ATTO 647N") == "ATTO 647N"


def test_unique_and_safe_names():
    assert make_unique(["DAPI", "DAPI", ""]) == ["DAPI", "DAPI (2)", "Channel"]
    assert safe_filename('a/b:c*?.') == "a_b_c__"


def test_swap_xy_swaps_dims_and_calibration():
    m = _meta(orientation=Orientation(swap_xy=True, apply=True))
    assert (m.output_dims.x, m.output_dims.y) == (30, 40)
    assert (m.output_calibration.pixel_size_x_um, m.output_calibration.pixel_size_y_um) == (0.2, 0.1)
    m2 = _meta(orientation=Orientation(swap_xy=True, apply=False))
    assert (m2.output_dims.x, m2.output_calibration.pixel_size_x_um) == (40, 0.1)


def test_orient_plane_is_distinguishable():
    a = np.arange(6).reshape(2, 3)
    assert orient_plane(a, Orientation(apply=False)) is a
    assert orient_plane(a, Orientation(swap_xy=True, apply=True)).shape == (3, 2)
    assert orient_plane(a, Orientation(flip_x=True, apply=True))[0, 0] == 2
    assert orient_plane(a, Orientation(flip_y=True, apply=True))[0, 0] == 3


def test_presets_roundtrip():
    chans = [Channel("GFP", excitation_nm=488, emission_range_nm=(500, 550), detector="HyD 3"),
             Channel("Trans", modality="brightfield", detector="PMT Trans")]
    presets.save_preset("panel", chans)
    other = [Channel("x", excitation_nm=488.2, emission_range_nm=(500.3, 549.8), detector="HyD 3"),
             Channel("y", excitation_nm=561, emission_range_nm=(570, 620), detector="HyD 4")]
    assert presets.names_from_preset("panel", other) == ["GFP", "y"]
    assert presets.preset_names() == ["panel"]
