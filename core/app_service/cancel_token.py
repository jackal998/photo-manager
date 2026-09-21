"""Cooperative cancellation token for the scan pipeline."""

from __future__ import annotations

import threading


class _CancelToken:
    """Cooperative cancellation flag wrapping threading.Event so it works
    across plain (non-Qt) threads.  Replaces the dual
    isInterruptionRequested()/cancel_flag scheme in ScanWorker.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def __call__(self) -> bool:
        return self._event.is_set()
