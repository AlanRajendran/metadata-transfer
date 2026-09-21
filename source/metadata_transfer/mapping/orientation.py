"""Optional re-orientation of image planes from vendor scan flags.

Verified on the SP8 sample (LAS X 3.5.5): LAS X displays the stored pixels unchanged even
though FlipX/FlipY/SwapXY are all set, i.e. the flags describe the scanner set-up and are
already baked into the data. Re-orientation is therefore OFF by default; the flags are
kept in the sidecar. The code stays for formats where the flags must be applied.
"""

from __future__ import annotations

import numpy as np

from ..model import Orientation


def orient_plane(plane: np.ndarray, o: Orientation) -> np.ndarray:
    if not o.apply:
        return plane
    if o.swap_xy:
        plane = plane.T
    if o.flip_x:
        plane = plane[:, ::-1]
    if o.flip_y:
        plane = plane[::-1, :]
    return plane
