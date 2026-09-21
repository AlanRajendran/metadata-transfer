# SPDX-License-Identifier: GPL-3.0-or-later
"""Output formats and which source formats may be converted to which."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OutputFormat:
    key: str  # "nd2" | "vsi"
    name: str
    extension: str
    #: False: multipoint sources are written as one file per stage position
    positions_in_one_file: bool


ND2 = OutputFormat("nd2", "Nikon ND2", ".nd2", True)
VSI = OutputFormat("vsi", "Evident VSI", ".vsi", False)
FORMATS: dict[str, OutputFormat] = {f.key: f for f in (ND2, VSI)}

#: source format name (Reader.format_name) -> allowed output formats, preferred first
TARGETS: dict[str, tuple[str, ...]] = {
    "Leica LIF": ("nd2", "vsi"),
    "Evident VSI": ("nd2",),
    "Nikon ND2": ("vsi",),
}


def targets_for(source_format: str) -> tuple[str, ...]:
    return TARGETS.get(source_format, ("nd2",))


def resolve_format(source_format: str, wanted: str = "auto") -> OutputFormat:
    """Output format for a source: `wanted` when allowed, else the source's default target."""
    allowed = targets_for(source_format)
    if wanted in allowed:
        return FORMATS[wanted]
    if wanted not in ("auto", "") and wanted in FORMATS:
        raise ValueError(f"{source_format} files cannot be converted to {FORMATS[wanted].name}")
    return FORMATS[allowed[0]]
