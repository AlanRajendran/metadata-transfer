# Changelog

## 1.0.0 (2026-09-21)

Conversions in every direction between the three formats, a redesigned window and the first
release for GitHub.

### Conversions
- **Nikon ND2 input** (new reader on the `nd2` package): Z-stacks, time-lapses, multipoint and large
  images; channel names, wavelengths, colours, objective, pixel size, Z/time steps, per-frame times,
  stage positions; camera, exposure, gain and filter turret from the NIS-Elements description.
- **Evident VSI output** (new writer): the `.vsi` tag tree is built from a tree cellSens Dimension 4.4
  wrote (the parser/serialiser reproduces real files byte for byte); pixels go to
  `_name_/stack1/frame_t_0.ets` (raw tiles; 512-pixel tiles and a pyramid above 4096 pixels); a JPEG
  thumbnail with the TIFF/EXIF/Olympus SIS records cellSens writes.
- **Stage positions (P dimension)** across the model, readers and writers: ND2 multipoint in and out
  (XY loop with the point coordinates, per-frame times).
- **Numbered cellSens position files** (`name_01.vsi`, `name_02.vsi`, …) are recognised as one
  multi-position experiment and become one ND2 with an XY loop; multipoint ND2 becomes numbered
  `.vsi` files. This follows the cellSens user manual: a multi-position experiment produces one image
  document per position.
- Leica LIF can be converted to VSI as well as ND2 (choice in Output).
- Every VSI written is re-read with the cellSens reader and checked like the ND2 files (pixels,
  calibration, channels, objective, stage position, date).
- ND2 files now carry per-frame acquisition times (the time cache NIS-Elements reads).

### Window
- Design language of the BIOMIS team's Timelapse Video Processing: toolbar with line icons, queue
  (left), metadata (centre, welcome page while empty), output and Convert (right), log (bottom);
  side panels collapse to strips; layout remembered.
- Themes Terracotta (default), Dark, Light and Follow Windows.
- Stage-position table for multipoint data; the target format of every series in the queue.
- About box, crash window with a copyable report, Help → Check for updates (GitHub, on request only).

### Release
- GPL-3.0-or-later licence, third-party notices generated at build time (Qt LGPL text included),
  Qt Virtual Keyboard and other unused Qt modules left out of the build.
- NSIS installer with one stable identity (upgrades in place), standalone uninstaller, release files
  with fixed names and SHA-256 checksums, privacy scan, GitHub Actions tests on synthetic data.
- `--diagnostic` self-test (writes an ND2, converts it to VSI and back).

## 0.2.1 (2026-09-11)
- VSI: layers of one acquisition (same geometry and stage position) merged into one multichannel ND2.
- VSI: hidden layers detected from the display mapping's visibility flag; snapshots without pixel
  folder listed with a reason.

## 0.2 (2026-09-11)
- Evident/Olympus cellSens VSI input: multichannel Z-stacks, time-lapses, hidden layers, stitched
  overview pyramids with a resolution choice; banded writing of multi-GB planes; log pane and files;
  dark mode; standalone uninstaller.

## 0.1 (2026-09-10)
- Leica LIF → Nikon ND2 with queue, metadata panel, channel-name presets, validation report, sidecar.
