"""Run long jobs off the UI thread."""

from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal


class Signals(QObject):
    progress = Signal(int, int, str)  # done, total, message
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    """Runs ``fn(job)``; the function reports through ``job.signals`` and checks ``job.cancelled``."""

    def __init__(self, fn: Callable[["Job"], object]):
        super().__init__()
        self.fn = fn
        self.signals = Signals()
        self.cancelled = False
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self.cancelled = True

    def run(self) -> None:
        try:
            result = self.fn(self)
        except Exception as e:  # report every failure to the UI
            self.signals.log.emit(traceback.format_exc())
            self.signals.failed.emit(str(e) or type(e).__name__)
            return
        self.signals.finished.emit(result)
