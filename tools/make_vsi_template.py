# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the `.vsi` writer's template from a cellSens multichannel Z-stack (developer tool).

    python tools/make_vsi_template.py <cellSens multichannel Z-stack .vsi>

The writer does not invent cellSens' tag tree: it starts from a tree cellSens itself wrote and
replaces the values (see `formats/vsi_document.py`). This tool takes such a file, removes the
thumbnail block and every personal value (author, experiment name, layer name, times), checks
that no text from the source file name is left, and writes
`source/metadata_transfer/formats/data/vsi_template.vsi` (tag tree only, no pixels).

Requirements for the source: cellSens Dimension 4.x, one layer with the dimensions T, Z and C
(in that order), at least one fluorescence and one transmitted-light channel. The template in
the repository was made from an IX83 + CSU-W1 acquisition (C640, BF, C488; 20 planes).
"""

from __future__ import annotations

import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP, "source"))

from metadata_transfer.formats import vsi_rawtree as rt  # noqa: E402

TARGET = os.path.join(APP, "source", "metadata_transfer", "formats", "data", "vsi_template.vsi")


def build(src: str) -> bytes:
    root = rt.parse_with_header(open(src, "rb").read())
    coll = root.sub(2000)
    coll.need(2016).blob = b""  # thumbnail block: rebuilt for every file
    img = coll.sub(2001, 1)
    if [f.second_tag for f in coll.find(2001)] != [1]:
        raise SystemExit("the source must have exactly one layer (stack1)")
    sizes = img.need(2003).get_ints()
    if len(sizes) != 3:
        raise SystemExit(f"the source must have the dimensions T, Z, C (found {sizes})")
    sp = img.sub(2005)
    sp.need(2030).set_str("")
    exp = sp.sub(21000)
    exp.need(175266).set_str("")
    doc = coll.sub(2004).sub(2109)
    for tag in (15, 16):
        f = doc.first(tag)
        if f is not None and f.payload:
            f.set_str("")
    for tag in (14, 23):
        f = doc.first(tag)
        if f is not None:
            f.set_int(0)
    sp.need(2015).set_int(0)
    root._first_ifd = 0  # type: ignore[attr-defined]
    data = rt.serialize(root)
    rt.parse(data)  # must parse back
    words = [w for w in os.path.splitext(os.path.basename(src))[0].replace("_", " ").split() if len(w) >= 5]
    for vol, f in root.walk():
        if f.payload and f.data_type in (rt.T_UNICODE, rt.T_TCHAR, 1):
            text = f.get_str().lower()
            for w in words:
                if w.lower() in text:
                    raise SystemExit(f"text from the source file name is still in tag [{f.tag}]: {f.get_str()!r}")
    return data


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    data = build(argv[0])
    os.makedirs(os.path.dirname(TARGET), exist_ok=True)
    with open(TARGET, "wb") as fh:
        fh.write(data)
    print(f"Template written: {TARGET} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
