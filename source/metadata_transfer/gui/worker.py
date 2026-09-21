# SPDX-License-Identifier: GPL-3.0-or-later
"""Background conversion thread."""

from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from ..convert import ConvertOptions, JobResult, convert_series


@dataclass
class Job:
    key: tuple[str, int]  # (source path, series index)
    channel_names: list[str] | None
    level: int = 0  # resolution level for pyramidal series
    output_format: str = "auto"  # "nd2" | "vsi" | "auto"


class ConvertWorker(QThread):
    job_started = Signal(object)  # key
    job_progress = Signal(object, float, str)  # key, fraction, message
    job_finished = Signal(object, object)  # key, JobResult
    all_done = Signal(list)  # list[JobResult]

    def __init__(self, jobs: list[Job], options: ConvertOptions, parent=None) -> None:
        super().__init__(parent)
        self.jobs = jobs
        self.options = options
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        results: list[JobResult] = []
        for job in self.jobs:
            if self._cancel.is_set():
                break
            self.job_started.emit(job.key)
            src, idx = job.key
            res = convert_series(
                src,
                idx,
                dataclasses.replace(self.options, output_format=job.output_format),
                channel_names=job.channel_names,
                level=job.level,
                progress=lambda f, m, k=job.key: self.job_progress.emit(k, f, m),
                cancel=self._cancel.is_set,
            )
            results.append(res)
            self.job_finished.emit(job.key, res)
        self.all_done.emit(results)
