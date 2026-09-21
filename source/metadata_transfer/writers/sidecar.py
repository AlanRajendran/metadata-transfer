"""JSON sidecar keeping every piece of source metadata next to the ND2."""

from __future__ import annotations

import datetime as _dt
import json

from .. import APP_NAME, __version__
from ..model import SeriesMetadata


def write_sidecar(path: str, meta: SeriesMetadata, raw_xml: str, output_nd2: str) -> None:
    original: dict[str, object]
    stripped = (raw_xml or "").lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            original = {"original_metadata": json.loads(stripped)}
        except ValueError:
            original = {"original_metadata_text": raw_xml}
    else:
        original = {"original_metadata_xml": raw_xml}
    doc = {
        "tool": f"{APP_NAME} {__version__}",
        "converted_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "source": {"file": meta.source_path, "format": meta.source_format, "series": meta.series_name},
        "output": output_nd2,
        "metadata": meta.to_dict(),
        **original,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False, default=str)
