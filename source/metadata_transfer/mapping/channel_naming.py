"""Automatic channel names derived from acquisition settings."""

from __future__ import annotations

import re

from ..model import Channel

# Tokens that should keep a specific spelling when prettifying Leica dye names.
_DYE_WORDS = {"ALEXA": "Alexa", "FLUOR": "Fluor", "HOECHST": "Hoechst", "CY": "Cy"}


def pretty_dye_name(raw: str) -> str:
    """'Leica/ALEXA 488' -> 'Alexa 488', 'Leica/DAPI' -> 'DAPI'."""
    name = (raw or "").strip()
    if "/" in name:
        name = name.split("/", 1)[1]
    words = []
    for w in name.split():
        words.append(_DYE_WORDS.get(w.upper(), w))
    return " ".join(words)


def settings_label(ch: Channel) -> str:
    """Description of the acquisition settings, e.g. '488 → 493–545 (HyD 3)'."""
    det = f" ({ch.detector})" if ch.detector else ""
    if ch.modality == "brightfield":
        return f"Trans{det}"
    ex = f"{ch.excitation_nm:.0f}" if ch.excitation_nm else "?"
    if ch.emission_range_nm:
        lo, hi = ch.emission_range_nm
        return f"{ex} → {lo:.0f}–{hi:.0f}{det}"
    return f"{ex}{det}"


def default_name(ch: Channel) -> str:
    if ch.vendor_name:
        return ch.vendor_name
    if ch.modality == "brightfield":
        return f"Transmitted ({ch.detector})" if ch.detector else "Transmitted"
    if ch.dye_name:
        return pretty_dye_name(ch.dye_name)
    return settings_label(ch)


def make_unique(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        n = n.strip() or "Channel"
        if n in seen:
            seen[n] += 1
            out.append(f"{n} ({seen[n]})")
        else:
            seen[n] = 1
            out.append(n)
    return out


_SAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str) -> str:
    return _SAFE.sub("_", name).strip(" .") or "unnamed"
