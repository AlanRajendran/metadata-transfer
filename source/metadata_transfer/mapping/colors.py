"""Vendor LUT names -> RGB display colours."""

from __future__ import annotations

LUT_RGB: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "yellow": (255, 255, 0),
    "gray": (255, 255, 255),
    "grey": (255, 255, 255),
    "white": (255, 255, 255),
    "orange": (255, 165, 0),
}


def lut_to_rgb(name: str) -> tuple[int, int, int]:
    return LUT_RGB.get((name or "").strip().lower(), (255, 255, 255))


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def wavelength_to_rgb(nm: float | None) -> tuple[int, int, int]:
    """Approximate display colour for an emission wavelength (fallback when no LUT)."""
    if nm is None:
        return (255, 255, 255)
    if nm < 470:
        return (0, 0, 255)
    if nm < 500:
        return (0, 255, 255)
    if nm < 570:
        return (0, 255, 0)
    if nm < 600:
        return (255, 255, 0)
    if nm < 650:
        return (255, 128, 0)
    if nm < 700:
        return (255, 0, 0)
    return (255, 0, 255)
