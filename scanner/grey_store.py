"""GROUPING 11 — the 128x128 grey signature, spilled out of memory.

Each image's grey signature is 16,384 bytes. During a scan they are appended
to one scan-local temporary file as fixed-size records, so the 655 MB of a
40,000-file library never sits in RAM. Stdlib only, so ``scanner.hasher`` and
``scanner.dedup`` can both import this module without a cycle.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

log = logging.getLogger(__name__)

GREY_SIDE = 128
GREY_SIG_BYTES = GREY_SIDE * GREY_SIDE


class GreySig(Protocol):
    """Anything that can produce a grey signature's 16,384 bytes."""

    def bytes(self) -> bytes: ...


@dataclass(frozen=True)
class GreyBytes:
    """In-memory signature: what the hasher emits and what tests use.

    Picklable, so it can cross the process pool."""

    data: bytes

    def bytes(self) -> bytes:
        return self.data


@dataclass(frozen=True)
class StoredGreySig:
    """A reference into a :class:`GreySigStore`. Created in the parent only;
    never pickled."""

    store: GreySigStore
    index: int

    def bytes(self) -> bytes:
        return self.store.get(self.index)


class GreySigStore:
    """Append-only spill file for one scan's grey signatures.

    The file is created lazily by the first :meth:`put`, in the system temp
    directory under a unique name, so writes never land on a manifest's
    volume (possibly a NAS). :meth:`close_and_delete` removes it. Every method
    takes the lock: compute threads call :meth:`put` concurrently.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fh: IO[bytes] | None = None
        self._path: Path | None = None
        self._count = 0
        self._closed = False
        # Count of failed reads and writes. The caller logs it; a failed
        # signature makes its file "pixel check unavailable", never EXACT.
        self.failures = 0

    @property
    def path(self) -> Path | None:
        """The temporary file, or ``None`` until the first :meth:`put`."""
        return self._path

    def put(self, data: bytes) -> int:
        """Append one signature and return its index.

        Raises ``ValueError`` for a wrong size, ``RuntimeError`` after close,
        and re-raises ``OSError`` (counted in ``failures``) on I/O failure."""
        if len(data) != GREY_SIG_BYTES:
            raise ValueError(
                f"grey signature must be {GREY_SIG_BYTES} bytes, got {len(data)}"
            )
        with self._lock:
            self._check_open()
            try:
                fh = self._handle()
                index = self._count
                fh.seek(index * GREY_SIG_BYTES)
                fh.write(data)
            except OSError:
                self.failures += 1
                raise
            self._count += 1
            return index

    def get(self, index: int) -> bytes:
        """Return the signature stored at ``index``.

        Raises ``IndexError`` outside the stored range and ``OSError``
        (counted in ``failures``) on a failed or short read."""
        with self._lock:
            self._check_open()
            if not 0 <= index < self._count:
                raise IndexError(
                    f"grey signature index {index} outside [0, {self._count})"
                )
            try:
                fh = self._handle()
                fh.seek(index * GREY_SIG_BYTES)
                data = fh.read(GREY_SIG_BYTES)
            except OSError:
                self.failures += 1
                raise
            if len(data) != GREY_SIG_BYTES:
                self.failures += 1
                raise OSError(f"short read of grey signature {index}")
            return data

    def close_and_delete(self) -> None:
        """Close and remove the temporary file. Idempotent.

        A file that cannot be removed (for example, held by antivirus) is
        logged and left: it sits in the temp directory until OS cleanup."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError as exc:
                    log.warning("could not close grey signature file: %s", exc)
            if self._path is not None:
                try:
                    self._path.unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("could not delete %s: %s", self._path, exc)

    def __enter__(self) -> GreySigStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close_and_delete()

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("grey signature store is closed")

    def _handle(self) -> IO[bytes]:
        """Return the open file, creating it on first use. Caller holds the lock."""
        if self._fh is None:
            fh = tempfile.NamedTemporaryFile(
                prefix="photo-manager-grey128-", suffix=".tmp", delete=False
            )
            self._fh = fh
            self._path = Path(fh.name)
        return self._fh
