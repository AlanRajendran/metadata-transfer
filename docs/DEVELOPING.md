# Developing Metadata Transfer

## Setup

```
python -m venv .venv
.venv\Scripts\python -m pip install -r source\requirements.lock.txt
.venv\Scripts\python -m pytest tests
.venv\Scripts\python -m metadata_transfer            # the window (run from source\)
```

`limnd2` (the ND2 writer by Laboratory Imaging, MIT) comes from
`https://pypi.laboratory-imaging.com/simple`; the lock file names that index.

## Layout

```
source/metadata_transfer/
  model/canonical.py        format-neutral metadata: Dimensions (x, y, z, c, t, p), Channel, SeriesMetadata...
  readers/                  one Reader per input format (base.py is the contract)
    leica_lif.py            LIF via liffile; the Leica XML is parsed here
    evident_vsi/            cellSens: tagtree.py (tag tree for reading), layers.py (tree -> layer info),
                            ets.py (tile files), reader.py (Reader), groups.py (numbered position files)
    nikon_nd2.py            ND2 via the nd2 package
    registry.py             extension -> reader; companion folders and position groups resolved here
  formats/                  writing cellSens files
    vsi_rawtree.py          lossless tag-tree model (parse + serialise byte-identical)
    vsi_document.py         builds a new tree from data/vsi_template.vsi
    vsi_thumbnail.py        TIFF block: JPEG thumbnail, EXIF, Olympus SIS
    ets_writer.py           .ets tiles and pyramids, streamed
  writers/                  nd2_limnd2.py, vsi_writer.py, sidecar.py, registry.py (which source -> which target)
  validation/               re-read every output (ND2 with nd2, VSI with the cellSens reader) and compare
  convert.py                one job: read -> write (per position for VSI) -> sidecar -> validate -> report
  cli.py, __main__.py       command line, entry point, --diagnostic self-test
  gui/                      PySide6 window (theme, panels, log, dialogs from Timelapse Video Processing)
```

Frames always run time → position → Z (`Dimensions.frame_keys()`), each frame holding all channels
(Y, X, C). `Reader.iter_frames` yields `(t, p, z, frame)`; `Reader.iter_bands(meta, t, p, z)` yields
full-width bands for planes too large for memory (writers switch to bands above 512 MB).

## Adding a reader or writer

- Reader: subclass `readers.base.Reader`, implement `list_series`, `metadata`, `iter_frames`
  (and `iter_bands` for large planes), `raw_metadata_xml` for the sidecar; register it in
  `readers/registry.py`; put unsupported things into `SeriesInfo.reason` instead of raising.
- Writer: a `write_x(meta, frames, out_path, bands=..., progress=..., cancel=...) -> [sha1 per frame]`
  function, an entry in `writers/registry.py` (`TARGETS` says which source may go where), a
  validation function that re-reads the output, and a branch in `convert.py`.
- Add the fields to `docs/mapping_table.md` and tests with synthetic data (see `tests/test_vsi_writer.py`).

## The VSI writer in short

cellSens' tag tree is not documented. The writer therefore starts from a tree cellSens wrote
(`formats/data/vsi_template.vsi`, made by `tools/make_vsi_template.py` from a cellSens 4.4
multichannel Z-stack; personal values removed) and regenerates what depends on the image: frame
records, dimension sizes, channel elements, stack properties, devices, display state. The raw
model is verified byte-identical on real files (`tests/test_vsi_writer.py`), so everything the
writer does not touch is exactly what cellSens wrote. Details: [formats.md](formats.md).

## Tests

- `tests/test_vsi_writer.py`, `tests/test_nd2_vsi_cycle.py`, `tests/test_writer_roundtrip.py`,
  `tests/test_ets.py`, `tests/test_gui.py`: synthetic data, run everywhere (also on GitHub Actions).
- `tests/test_sample_*.py`, `tests/test_leica_golden.py`: real microscope files. They run when a
  `samples.json` (outside the repository, path in `METADATA_TRANSFER_SAMPLES` or next to the
  repository folder) maps roles to files: `lif`, `vsi_multichannel`, `vsi_timelapse`,
  `vsi_overview`, `nd2_zstack`, `nd2_timelapse`, `nd2_multipoint`, `nd2_large`, `golden_dir`.
  Sample files and their names never go into the repository.

## Building

`packaging\build.ps1` (tests, notices, PyInstaller, self-test of the frozen app, NSIS installer and
uninstaller); `packaging\test-installer.ps1 -Setup "<setup exe>"` installs and checks it.
Screenshots: `python tools/make_screenshots.py` (synthetic demo files only).
