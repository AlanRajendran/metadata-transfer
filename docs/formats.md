# File formats: what the readers and writers rely on

## Evident / Olympus cellSens `.vsi`

Established on cellSens Dimension 4.4 / 4.4.1 files (IX83 + Yokogawa CSU-W1 spinning disk,
Hamamatsu ORCA-Fusion; stitched overviews), with OlyVIA's Properties panel as the reference for
values. Bio-Formats' `CellSensReader.java` (GPL) served as the specification of the binary layouts.

### Files on disk

```
<name>.vsi                       TIFF container: the metadata "tag tree" with a thumbnail inside
_<name>_\                        companion folder, next to the .vsi
   stack1\frame_t_0.ets          pixels of layer 1 (all T, Z, C of that layer)
   stack10000\frame_t_0.ets      another layer (e.g. a transmitted-light channel stored separately)
   stack1\<name>_T0001.tif ...   per-frame TIFFs cellSens sometimes writes (ignored)
```

* Layer *N* of the tag tree (image volume `[2001]#N`) belongs to folder `stack<N>`; the numbers are
  not consecutive. Layers without a folder (e.g. a "Positions" vector layer) are not images.
* One acquisition can be split over layers (fluorescence in `stack1`, DIC in `stack10000`, same size,
  T count, pixel size and stage origin, DIC frames 2.27 s later on the same clock). Such layers are
  merged into one multichannel series; the stage origin is part of the match.
* A snapshot has no companion folder: its only layer has no image rectangle and the pixels are the
  second TIFF page of the `.vsi`. Listed as not convertible.
* **Multi-position experiments** (Process Manager "XY-Positions", Experiment Manager with stage
  moves) produce one image document per position — "The experiment will produce two multi-channel
  Z-stack images" (cellSens user manual, *Acquiring multi-channel Z-stack images at different
  positions on the sample*) — auto-saved with numbered names. `readers/evident_vsi/groups.py`
  treats `name_01.vsi`, `name_02.vsi`, … (also `_Pos01`, `_P01`) as one experiment when they share
  size, Z, T, channels, pixel type and size and experiment name and lie at different stage positions.
  cellSens stores no explicit link between the files. MIA scans instead produce one stitched image.

### `.ets` layout (little endian)

| Offset | Content |
|---|---|
| 0 | `SIS\0`, int32 headerSize (64), int32 version (3), int32 nDimensions, int64 additionalHeaderOffset, int32 additionalHeaderSize, 4 reserved, int64 usedChunkOffset, int32 nUsedChunks, 4 reserved |
| 64 | `ETS\0`, int32 version (196614), int32 pixelType (2 uint8, 4 uint16, …), int32 sizeC, int32 colour space, int32 compression (0 raw, 2 JPEG, 3 JPEG 2000, 5 lossless JPEG, 8 PNG, 9 BMP), int32 quality, int32 tileX, int32 tileY, int32 tileZ, hints, background colour at +108 (65535 for transmitted-light layers, 0 for fluorescence), int32 component order at +148, int32 usePyramid at +152, int32 nImageDims at +184 then width, height and the extra dimension sizes |
| usedChunkOffset | records: int32 nDims, int32 coords[nDims], int64 offset, int32 size, int32 unused |

Chunk coordinates are `(tile column, tile row, extra dimensions…, level)`; the level coordinate is
always present. The meaning of the extra dimensions is in the tag tree, not in the ETS. Camera
acquisitions use one tile the size of the frame; stitched images use 512 × 512 tiles, missing tiles
are background, and pyramid level *k* is 2^k downsampled (halve, round up when odd).

### Tag tree

The `.vsi` is a little-endian TIFF whose root tag volume starts at byte 8 and runs to the end of the
file. The TIFF directory, the JPEG thumbnail and the EXIF and Olympus SIS records are stored
*inside* the tree, as the content of the type-10 field `[2016]`, with absolute file offsets.

* Volume: 24-byte header (int16 24, int16 21321, int32 volume version, int64 offset of the first
  field — 24, or 0 when empty — int32 flags with the field count in the low 28 bits, int32 pad).
* Field: int32 field type, int32 tag, uint32 offset of the next field relative to the volume start
  (0 = last), int32 data size, [int32 second tag when bit 27 is set]. Bits: 27 second tag,
  28 extended, 29 array, 30 inline (the value is the data size), 31 new volume; low 24 bits = data
  type (5/6/14 int32, 7/17 int64, 10 double, 12 bool, 13/8192 UTF-16, 259/260/8195/8199/8200 arrays,
  270 BGR bytes).
* Extended field, data type 1 or 2: data size 0, content = one nested volume. Other extended data
  types: the content ends at `volume start + data size + field header length` (type 0 = consecutive
  volumes, type 10 = TIFF block; data size 0 = empty). Measured quantities are small volumes
  `{units (13), type code, dimensions, Value (268435458)}`; vectors carry a count (268435457).

`formats/vsi_rawtree.py` keeps every byte (field types, versions, gaps) and writes a parsed file back
byte-identical; that is tested on every sample.

Where the values are (several ids are reused in different contexts):

```
root
  [2]      document properties (older files)
  [2000]   collection
    [2035] version     [2016] TIFF block (thumbnail)     [2038] vector overlay store (XML)
    [2001]#<stack id>  image volume, one per layer
      [2003] sizes of the extra dimensions in ETS order, e.g. [T, Z, C] = [1, 20, 3]
      [2002]#k  one frame per plane, k = t + T·(z + Z·c) (first dimension fastest)
          [2006] -> [2017] timestamp (ms)
          frame 0 also [2018]: [2053] image rect, [2410] tile origin, [20025]/[20187] external file
          volume (origin + size of every dimension), [20005] external file present
      [2005] stack properties: [2030] layer name, [2019] pixel size µm, [2018] origin µm (top left),
          [2015] creation time (Unix s), [2003] display range, [2074] stack type (1 = overview),
          [2043] devices (camera 0, frame 40500, nosepiece/objective 20000, adapter), with
          [120116] name, [120132] model, [120133] maker and [120114] properties;
          [21000] experiment: [175266] name, [21007]/[21008] stage centre µm
      [2007]#d  one dimension description per extra dimension, with elements [2008]#e:
          [2023] meaning (1 Z, 2 T, 3 lambda, 4 C, 9 phase)
          T: [10069] original frame rate (Hz)   Z: [2012] start µm, [2013] step µm
          C, one element per channel: [2021]/[2419] name, [2417] emission nm, [2474] excitation nm,
             [2418] 1 transmitted / 2 fluorescence, [2003] display limits, [2004] LUT (256 × BGR),
             [2043] per-channel devices: camera [100002] exposure µs, lasers (20016) [122000] nm and
             [121132] %, filter wheel / dichroic [120063] name, lamp (20003), CSU-W1 disk (20011)
    [2004]  document: [2109] product, version, build, author, times; [20047] first-frame marker
    [2011]  display state: [2012]#0 view (origin, pixel size, rectangle, [10014] displayed plane,
            [10050] selected frames), [2012]#1.. displayed layers ([10005] stack id, [10008] visible)
```

Objective: [120063] name, [120065] description ("20x"), [120060] magnification, [120061] NA,
[120079] refractive index, [120062] working distance. Camera: [100049] bit depth, [100015]/[100016]
binning, [100017] sensor region.

### Writing a `.vsi` (`writers/vsi_writer.py`)

1. Pixels: `formats/ets_writer.py` writes `_<name>_/stack1/frame_t_0.ets`, raw tiles, dimensions
   (T, Z, C). Images up to 4096 pixels per side are one tile (as cellSens camera acquisitions);
   larger images get 512 × 512 tiles and a pyramid of 2 × 2 means down to one tile, streamed row by row.
2. Tag tree: `formats/vsi_document.py` loads `formats/data/vsi_template.vsi` (a cellSens 4.4
   multichannel Z-stack with its personal values removed, `tools/make_vsi_template.py`) and
   regenerates frames, sizes, channel elements (cloned from the template's fluorescence or
   transmitted-light channel), stack properties, devices (camera, frame, objective; the per-channel
   optical path keeps the camera with its exposure), document times and the display state.
3. Thumbnail: `formats/vsi_thumbnail.py` renders a 512-pixel colour composite of the middle plane
   and writes the TIFF block like cellSens (JPEG strip, EXIF, the three Olympus SIS records with
   their absolute offsets); the tree is serialised twice so the block knows where it lands.
4. Check: the file is re-read with the cellSens reader (pixels by SHA-1, calibration, channels,
   objective, stage position, date).

Multipoint sources become one `.vsi` per position (`name_01.vsi`, …), which the reader groups again.

## Nikon NIS-Elements `.nd2`

Read with the `nd2` package; written with Laboratory Imaging's `limnd2`.

* Loops: time (`TimeLoop`/`NETimeLoop`), XY positions (`XYPosLoop`, points with stage X/Y/Z and
  names), Z (`ZStackLoop`: step, home index, direction). Frames are ordered by the loops; the reader
  maps (t, p, z) to the frame index through `loop_indices`.
* Channels: name, excitation/emission, colour and modality flags per plane; NIS often marks
  phase-contrast channels as fluorescence, so names containing "phase", "BF", "DIC", "trans" or
  "brightfield" count as transmitted light.
* Camera, exposure, gain, binning, filter turret and the microscope name exist only in the image
  description ("Plane #n:" blocks, or "Sample n:" in the capturing text), which the reader parses.
* Per-frame times and absolute start: frame metadata (`CustomData|AcqTimesCache`, which the writer
  now also stores), stage positions from the frame metadata or the XY points.
* Written files: one frame per (t, p, z) with all channels interleaved; experiment T → XY → Z;
  planes over 512 MB are written in bands; the description holds everything without a field.

## Leica `.lif`

Pixels via `liffile`; metadata from the Leica XML (channel ↔ sequential-setting rule, emission
windows, lasers, detectors). See [mapping_table.md](mapping_table.md).
