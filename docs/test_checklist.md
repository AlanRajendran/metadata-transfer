# Acceptance checklist on the microscope workstations

The automatic tests check every value the app reads back. These checks need the vendors' software.
Record the software versions first (Help → About).

NIS-Elements version: ____________    cellSens version: ____________    OlyVIA version: ____________

## Files written as ND2 — open in NIS-Elements

| # | Check | Pass? |
|---|---|---|
| 1 | Leica series: opens without warnings; channel tabs with the dye names and colours; Z slider; 0.28 or 0.11 µm/px; 12 bit | ☐ |
| 2 | cellSens multichannel Z-stack: C640 red, BF grey, C488 green; Z slider 20 planes; 0.325 µm/px; Z step 1.87 µm | ☐ |
| 3 | cellSens time-lapse with DIC: one file, C488 and DIC tabs, time slider 31 frames, 60 s interval | ☐ |
| 4 | cellSens stitched overview at full resolution (multi-GB) opens, pans and zooms | ☐ |
| 5 | **Numbered cellSens position files → one ND2**: XY slider with every position, stage coordinates in the point list, correct image per position | ☐ |
| 6 | Image → Image Info: description lists exposure times, laser lines, filters, objective | ☐ |

## Files written as VSI — open in OlyVIA and cellSens Dimension

| # | Check | OlyVIA | cellSens |
|---|---|---|---|
| 7 | ND2 Z-stack → VSI opens without a message; gallery shows the colour thumbnail | ☐ | ☐ |
| 8 | Dimension Selector: Z slider with every plane, channel list with the ND2 names and colours | ☐ | ☐ |
| 9 | Properties: pixel size (nm/pixel), channel emission/excitation, exposure time, objective name, magnification and NA, creation time | ☐ | ☐ |
| 10 | ND2 time-lapse → VSI: time slider, time stamps 1 interval apart | ☐ | ☐ |
| 11 | ND2 multipoint → `name_01.vsi` … : each opens at its stage position (Properties: Origin) | ☐ | ☐ |
| 12 | Large ND2 (over 4096 pixels) → VSI: opens as a zoomable image, zooming out is fast (pyramid) | ☐ | ☐ |
| 13 | Leica LIF → VSI (Output: Evident VSI): channels, Z step, pixel size as in LAS X | ☐ | ☐ |
| 14 | cellSens: measurement tools use the right calibration (measure a known structure) | — | ☐ |
| 15 | cellSens: File → Save As of a converted file works (the file is accepted as a normal document) | — | ☐ |

Notes / problems:
