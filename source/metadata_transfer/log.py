"""Application logging: a rolling log file in %APPDATA%\\Metadata Transfer\\logs, optionally
mirrored to the console (CLI) and to the GUI log pane (via a Qt-signal handler installed by
the main window). All modules log through `logging.getLogger(__name__)`, which puts them
under the "metadata_transfer" logger configured here.
"""

from __future__ import annotations

import logging
import logging.handlers
import os

from .presets import settings_dir

ROOT_LOGGER = "metadata_transfer"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"


def log_dir() -> str:
    path = os.path.join(settings_dir(), "logs")
    os.makedirs(path, exist_ok=True)
    return path


def log_file() -> str:
    return os.path.join(log_dir(), "metadata-transfer.log")


def setup_logging(*, console: bool = False, level: int = logging.INFO) -> str:
    """Configure the file handler (2 MB x 5 files); returns the log file path."""
    root = logging.getLogger(ROOT_LOGGER)
    root.setLevel(level)
    path = log_file()
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        try:
            fh = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
            fh.setFormatter(logging.Formatter(_FORMAT, _DATE))
            root.addHandler(fh)
        except OSError:
            pass  # read-only profile: log to console/pane only
    if console and not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        root.addHandler(sh)
    return path


class CallbackHandler(logging.Handler):
    """Forwards formatted records to a callable (the GUI wraps this in a Qt signal)."""

    def __init__(self, callback, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._callback = callback
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._callback(self.format(record))
        except Exception:  # never let logging break the app
            pass
