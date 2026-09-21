# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata Transfer: convert microscopy files between Leica LIF, Evident/Olympus cellSens VSI and
Nikon NIS-Elements ND2, with their metadata."""

__version__ = "1.0.0"
VERSION = __version__
APP_NAME = "Metadata Transfer"
APP_SUBTITLE = "Leica LIF, Evident VSI and Nikon ND2, with their metadata"
# Short version used in file names of built programs ("Metadata Transfer Setup 1.0.exe").
SHORT_VERSION = ".".join(__version__.split(".")[:2])

#: Credit, disclaimer and use statement, shown in the About box, the README and the installer.
AFFILIATION = "BIOMIS Team, SATIE laboratory, ENS Paris-Saclay"
CREDITS = f"Developed by {AFFILIATION}."
COPYRIGHT = f"Copyright (c) 2026 the {APP_NAME} contributors"
DISCLAIMER = (
    "This project is independent: it is not affiliated with, endorsed by or supported by Nikon, Evident "
    "(Olympus) or Leica Microsystems. NIS-Elements, cellSens, OlyVIA and LAS X are trademarks of their owners."
)
RESEARCH_USE = "For research use only; not for diagnostic or clinical use."
LICENSE_NAME = "GPL-3.0-or-later"
REPOSITORY_URL = "https://github.com/AlanRajendran/metadata-transfer"
