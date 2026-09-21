# Metadata mapping between Leica LIF, Evident VSI and Nikon ND2

This table is the converter's specification. It was checked against a Leica SP8 acquisition
(LAS X 3.5.5), native NIS-Elements AR 5.41 files (Z-stack, time-lapse, multipoint, large image) and
cellSens Dimension 4.4 / 4.4.1 files (multichannel Z-stack, time-lapse with a hidden DIC layer,
stitched overview), with OlyVIA's Properties panel as the reference for cellSens values.

Key to the Status column:
- **direct**: stored in a structured field of the output and checked after every conversion
- **text**: written into the ND2 description (NIS image info)
- **sidecar**: kept in `.metadata.json` next to the output

Part 1: Leica LIF → ND2 (and → VSI, same fields as Part 3). Part 2: Evident VSI → ND2.
Part 3: Nikon ND2 → Evident VSI. Part 4: stage positions.

## Part 1 — Leica LIF → ND2

| Field | Leica source (XML) | ND2 target | Status |
|---|---|---|---|
| Size X / Y | `DimensionDescription` DimID 1 / 2 `NumberOfElements` | ImageAttributes width/height | direct ✓ |
| Z planes | DimID 3 | Z-stack experiment loop, count | direct ✓ |
| Channels | `ChannelDescription` count | components per frame (interleaved Y·X·C, like native NIS files) | direct ✓ |
| Time points | DimID 4 | Time loop (count, period) | direct (no T sample yet) |
| Pixel type / bit depth | `Resolution`=12, uint16 | `bitsPerComponentSignificant`=12 in 16-bit | direct ✓ (NIS shows "5x12bit") |
| Pixel size X/Y | `Length/(N−1)` of DimID 1/2 (m→µm) | `dCalibration` | direct ✓; same value as Bio-Formats (0.11358 / 0.28409 µm) |
| Z step | `Length/(N−1)` of DimID 3 | Z loop `dZStep` (+ low/high from Z coordinates) | direct ✓ (0.9994 µm, same as Bio-Formats) |
| Frame interval | `Length/(N−1)` of DimID 4 (s) | Time loop period (ms) | direct |
| Pixels | memory block via `liffile` | frames | direct ✓, SHA-1 identical for every frame |
| Channel name | MultiBand `DyeName` of the detector's band ("Leica/ALEXA 488" → "Alexa 488"); user-editable | plane `sDescription` | direct ✓ |
| Excitation | active `LaserLineSetting` (IntensityDev>0) of the sequential setting; longest line below the detection window | plane excitation (nm) | direct ✓ |
| Emission | `MultiBand` LeftWorld–RightWorld for the detector's channel number | centre wavelength; full window in filter name | direct ✓ (centre) / text |
| Channel colour | `ChannelDescription LUTName` | plane `uiColor` | direct ✓ |
| Modality | confocal setting → Laser Scanning Confocal; PMT Trans (ScanType TLD) → Brightfield + transmitted detector | plane modality flags | direct ✓ |
| Objective name | `ObjectiveName` | `wsObjectiveName` (limnd2 default patched) | direct ✓ |
| Magnification / NA / RI | `Magnification`, `NumericalAperture`, `RefractionIndex` | objective settings | direct ✓ |
| Immersion | `Immersion` (DRY) | no ND2 field | text + sidecar |
| Zoom | `Zoom` | `dZoom` / objective-to-pinhole zoom | direct ✓ |
| Pinhole | per sequential setting `Pinhole` (m→µm) | plane `dPinholeDiameter` | direct ✓ |
| Acquisition date/time | `TimeStampList` first stamp | `dTimeAbsolute` (limnd2's day rounding patched) + text "date" | direct |
| Per-frame times | `TimeStampList` (one per Z, or per Z·C) | frame timestamp (ms) | direct |
| Microscope | `SystemTypeName` + `MicroscopeModel` ("Leica TCS SP8 (DMI6000B-CS)") | microscope name | direct |
| Detector name | active `Detector Name` | plane camera name | direct |
| Laser power % | `IntensityDev` | — | text + sidecar |
| Detector gain / offset | `Detector Gain/Offset` | — | text + sidecar |
| Scan speed, dwell time, line/frame average/accumulation, scan direction | ATLConfocalSettingDefinition attrs | — | text + sidecar |
| Stage X/Y/Z | `StagePosX/Y`, `ZPosition` | — | text + sidecar |
| LAS X display range | `ViewerScaling/ChannelScalingInfo` | — | sidecar |
| FlipX / FlipY / SwapXY | ATLConfocalSettingDefinition | not applied (see below) | sidecar |
| Serial number, software version, full XML | HardwareSetting attachment | — | sidecar (complete XML) |

## Channel ↔ acquisition-setting rule

The SP8 records one *sequential setting* per scan pass. The sample has 4 settings but
5 channels, because setting 4 has two active detectors: PMT 1 and PMT Trans. The mapping rule:

1. Channels follow the sequential-setting order.
2. Within a setting, there is one channel per **active** detector, in detector-list order.
3. A detector's emission window is the `MultiBand` whose `Channel` equals the detector's `Channel`.
   The transmitted detector (`Channel=100`, `ScanType=TLD`) has none.
4. **Cross-check:** the setting's `LUT_List` entry for that detector channel must match the image's
   `ChannelDescription LUTName` at the same position. If it doesn't, the report and GUI show a warning.

Result for Series003–006:

| # | Name | Laser | Detection window | Detector | LUT |
|---|---|---|---|---|---|
| 1 | Alexa 488 | 488 nm (50 %) | 493–545 nm | HyD 3 | Green |
| 2 | Alexa 647 | 647 nm (20 %) | 652–703 nm | HyD 4 | Magenta |
| 3 | Alexa 568 | 561 nm (30 %) | 566–636 nm | HyD 4 | Yellow |
| 4 | DAPI | 405 nm (10 %) | 412–480 nm | PMT 1 | Cyan |
| 5 | Transmitted | 405 nm | — | PMT Trans | Gray |

**Bio-Formats 8.1.1 (Fiji) gets this file's channels wrong.** Channel 1 is reported with
excitation 670 nm and no name, and the names are shifted by one (e.g. "ALEXA 647" on the 561-nm
channel). It is a known class of LIF reader problem with sequential scans, and it's why the converter
reads the Leica XML itself. Pixel sizes and Z step do agree with Bio-Formats.

## Orientation

All series have FlipX=1, FlipY=1, SwapXY=1. The raw Series001 pixels were compared with LAS X Office's display:
- the magenta spot sits at the bottom, left of centre
- the fibre runs diagonally from the top right in the transmitted channel

LAS X shows the stored pixels **unchanged**, so the flags describe the scanner set-up and are already
applied to the stored data. The converter therefore copies pixels as stored. The flags go into the sidecar,
and `--apply-scan-flags` exists for debugging only.

## .lifext

It contains only LAS X's 8-bit display pyramids and histograms for the Z-stacks. It isn't needed and is ignored.

## Part 2 — Evident VSI (cellSens) → ND2

Tag ids refer to the `.vsi` tag tree, see [formats.md](formats.md). One layer (`stack<N>` folder) = one series.

| Field | cellSens source | ND2 target | Status |
|---|---|---|---|
| Size X / Y | image rect `[2053]` (cross-checked with the ETS header image size) | ImageAttributes width/height | direct ✓ |
| Z / C / T | dimension sizes `[2003]` + meanings `[2023]` per dimension description `[2007]` | Z loop, components, Time loop | direct ✓ |
| Pixel type / bit depth | ETS pixelType (uint8/uint16) + camera bit depth `[100049]` | `bitsPerComponentSignificant` | direct ✓ |
| Pixel size X/Y | stack `[2019]` (µm), × 2^level for pyramid levels | `dCalibration` | direct ✓ (0.325 µm = OlyVIA "325 nm/pixel") |
| Z step / Z positions | Z start `[2012]`, increment `[2013]` (negative = descending) | Z loop step, low/high | direct ✓ (1.87 µm = OlyVIA) |
| Frame interval | mean spacing of the T timestamps | Time loop period | direct ✓ (60 s) |
| Per-frame times | frame timestamps `[2017]` (first extra dimension fastest); earliest channel per (t, z) | frame timestamp | direct |
| Pixels | ETS tiles (raw only), cropped by tile origin `[2410]`, missing tiles = header background | frames (banded for > 512 MB planes) | direct ✓, SHA-1 identical; DIC time-lapse byte-identical to cellSens' per-frame TIFFs |
| Resolution level | ETS pyramid levels; user picks one | dims / calibration of that level; level in description | direct (level 8 = embedded thumbnail, r = 1.000) |
| Channel name | `[2021]`/`[2419]` ("C640", "BF", "C488"), user-editable | plane `sDescription` | direct ✓ |
| Excitation | channel `[2474]` (cellSens fluorochrome value, e.g. 494 nm) | plane excitation | direct ✓ |
| Emission | channel `[2417]` (e.g. 518 nm) | plane emission | direct ✓ |
| Emission filter | filter-wheel device name `[120063]` ("B525/50" → 500–550 nm) | plane filter name | direct + text |
| Laser line / power | laser devices `[122000]`/`[121132]`; the line closest to the excitation is labelled active | — | text + sidecar |
| Channel colour | last entry of the display LUT `[2004]` (BGR) | plane `uiColor` | direct ✓ (red / white / green) |
| Modality | spinning disk (CSU-W1 device present) → Spinning Disk Confocal + camera; type `[2418]`=1 or `[20035]` → Brightfield (+ DIC flag for "DIC") | plane modality flags | direct ✓ |
| Exposure | camera `[100002]` (µs) | — (no ND2 field) | text ("Exposure: 499.961 ms") + capturing + sidecar |
| Camera | camera device `[120116]` | plane camera name | direct |
| Objective | nosepiece `[120063]` + `[120065]` ("LUCPLFLN 20x"), `[120060]`, `[120061]`, `[120079]` | objective name, mag, NA, RI | direct ✓ |
| Immersion | from the refractive index (1.0 air, 1.406 silicone, 1.515 oil) | — | text |
| Pinhole | CSU-W1 disk `[125037]` (50 µm) | plane `dPinholeDiameter` | direct |
| Acquisition date/time | stack creation time `[2015]` (UTC → local) | `dTimeAbsolute` + text "date" | direct ✓ |
| Microscope / software | frame device model + disk ("IX83 P2ZF + Yokogawa CSU-W1 Disk"), document product/version/build | microscope name; text | direct / text |
| Stage position | experiment stage centre `[21007]`/`[21008]`; image origin `[2018]` | — | text + sidecar |
| Binning, sensor region, disk speed, dichroic, lamp intensity, experiment name, author | device properties | — | text + sidecar |
| Display range | channel display limits `[2003]` | — | sidecar |
| Mirror H/V | camera `[100023]`/`[100024]` (all 0 in the samples) | not applied | sidecar |
| Split layers | layers with the same size, Z, T, pixel type, pixel size, tile size and stage origin `[2018]` (e.g. C488 `stack1` + DIC `stack10000`) | merged into one series, channels in stack-id order; each frame gets the earliest time of its channels, the per-channel offset goes into the description | direct (since 0.2.1) |
| Hidden / derived layers | display mapping `[10005]` with `[10008]` visible = 0, stack type `[2074]` | merged when they match another layer, else listed with a note and converted if ticked | — |
| Embedded snapshots | layer without `[2053]` image rect or companion folder; pixels in the `.vsi` TIFF pages | listed as not convertible | — |
| Complete tag tree | everything | — | sidecar (`original_metadata`, JSON) |

## Part 3 — Nikon ND2 → Evident VSI

One ND2 = one series. The `.vsi` gets one layer with the dimensions T, Z, C; a multipoint ND2 gives
one `.vsi` per stage position. Tag ids: see [formats.md](formats.md).

| Field | ND2 source | cellSens target | Status |
|---|---|---|---|
| Size X / Y | attributes width/height | image rect `[2053]`, ETS image size, external file volume `[20025]` | direct ✓ |
| Z / C / T | loops `ZStackLoop`, channels, `TimeLoop` | sizes `[2003]` = [T, Z, C], dimension descriptions `[2007]` | direct ✓ |
| Positions | `XYPosLoop` points | one `.vsi` per position, stage centre `[21007]/[21008]`, origin `[2018]` | direct ✓ (per file) |
| Pixels | frames (bands for large planes) | ETS raw tiles; > 4096 px: 512-px tiles + pyramid | direct ✓, SHA-1 identical |
| Pixel type / bits | uint8/uint16, significant bits | ETS pixel type, camera bit depth `[100049]` | direct ✓ |
| Pixel size | voxel size X/Y | `[2019]` µm | direct ✓ |
| Z step / positions | Z loop (step, home index, direction) or frame Z | Z start `[2012]`, step `[2013]` | direct ✓ |
| Time step / frame times | loop period, frame times | frame timestamps `[2017]` (ms), frame rate `[10069]` | direct ✓ |
| Channel name | plane name | `[2021]`, `[2419]`, layer name `[2030]` = all names | direct ✓ |
| Excitation / emission | plane excitation/emission (nm) | `[2474]` / `[2417]` | direct ✓ |
| Transmitted light | modality flags, or phase/BF/DIC/trans in the name | channel type `[2418]` = 1 | direct ✓ |
| Colour | plane colour | display LUT `[2004]` (black → colour, 256 × BGR) | direct ✓ |
| Display range | — | `[2003]` from the 0.1/99.9 % percentiles of the middle plane | derived |
| Exposure | description "Exposure: 100 ms" | camera `[100002]` µs in each channel's optical path | direct ✓ |
| Camera | description "Camera Type" | camera device `[120116]/[120132]`, TIFF model tag | direct |
| Objective | objective name, magnification, NA, refractive index | nosepiece device `[120063]`, `[120060]`, `[120061]`, `[120079]` | direct ✓ |
| Microscope | description "Microscope: Ti2 Microscope" | frame device name | direct |
| Acquisition date | frame 0 absolute time minus its offset | creation time `[2015]`, document times | direct ✓ |
| Gain, binning, filter turret, readout mode | description | — | sidecar |
| Complete metadata | text info, attributes, experiment, metadata | — | sidecar |

## Part 4 — Stage positions (both directions)

| From | To | Rule |
|---|---|---|
| ND2 with an XY loop | ND2 | one file, XY loop with the point coordinates and names, times per frame |
| ND2 with an XY loop | VSI | `name_01.vsi`, `name_02.vsi`, … one per position (cellSens convention), same experiment name |
| numbered cellSens files `name_NN.vsi` | ND2 | one ND2 `name.nd2` with an XY loop when all files share size, Z, T, channels, pixel type/size and experiment name and lie at different stage positions; times shifted by each file's start |
| layers of one `.vsi` at the same position | ND2 | one multichannel series (see Part 2) |
