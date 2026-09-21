# SPDX-License-Identifier: GPL-3.0-or-later
"""PyInstaller entry point: both programs run metadata_transfer.__main__.main().

"Metadata Transfer.exe" opens the window (files given as arguments are queued);
"MetadataTransfer-cli.exe --help" is the command line.
"""
from __future__ import annotations

import sys


def _run() -> int:
    from metadata_transfer.__main__ import main

    result = main()
    return int(result) if result is not None else 0


if __name__ == "__main__":
    sys.exit(_run())
