"""The review thumbnails of one scan, spilled out of memory.

A thumbnail (``scanner.thumbnail``) is about 36 KB; a 40,000-file library
would hold 1.4 GB of them. During a scan they are appended to one scan-local
temporary file and addressed by offset and length. Same life cycle as
``scanner.grey_store``, whose records are fixed-size; these are not.
Stdlib only, so ``scanner.hasher`` and ``scanner.dedup`` can import it.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

log = logging.getLogger(__name__)


class Thumb(Protocol):
    """Anything that can produce a thumbnail's JPEG bytes."""

    def bytes(self) -> bytes: ...


@dataclass(frozen=True)
class ThumbBytes:
    """In-memory thumbnail: what the hasher emits and what tests use.

    Picklable, so it can cross the process pool."""

    data: bytes

    def bytes(self) -> bytes:
        return self.data


@dataclass(frozen=True)
class StoredThumb:
    """A reference into a :class:`ThumbStore`. Created in the parent only;
    never pickled."""

    store: ThumbStore
    offset: int
    length: int

    def bytes(self) -> bytes:
        return self.store.get(self.offset, self.length)


class ThumbStore:
    """Append-only spill file for one scan's thumbnails.

    The file is created lazily by the first :meth:`put`, in the system temp
    directory under a unique name, so writes never land on a manifest's
    volume (possibly a NAS). :meth:`close_and_delete` removes it. Every method
    takes the lock: compute threads call :meth:`put` concurrently.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fh: IO[bytes] | None = None
        self._path: Path | None = None
        self._size = 0
        self._closed = False
        # Count of failed reads and writes. The caller logs it; a picture
        # whose thumbnail is lost gets no similarity number.
        self.failures = 0

    @property
    def path(self) -> Path | None:
        """The temporary file, or ``None`` until the first :meth:`put`."""
        return self._path

    def put(self, data: bytes) -> StoredThumb:
        """Append one thumbnail and return its reference.

        Raises ``RuntimeError`` after close and re-raises ``OSError``
        (counted in ``failures``) on I/O failure."""
        with self._lock:
            self._check_open()
            try:
                fh = self._handle()
                offset = self._size
                fh.seek(offset)
                fh.write(data)
            except OSError:
                self.failures += 1
                raise
            self._size += len(data)
            return StoredThumb(self, offset, len(data))

    def get(self, offset: int, length: int) -> bytes:
        """Return the ``length`` bytes stored at ``offset``.

        Raises ``IndexError`` outside the stored range and ``OSError``
        (counted in ``failures``) on a failed or short read."""
        with self._lock:
            self._check_open()
            if offset < 0 or length < 0 or offset + length > self._size:
                raise IndexError(
                    f"thumbnail range [{offset}, {offset + length}) outside [0, {self._size})"
                )
            try:
                fh = self._handle()
                fh.seek(offset)
                data = fh.read(length)
            except OSError:
                self.failures += 1
                raise
            if len(data) != length:
                self.failures += 1
                raise OSError(f"short read of thumbnail at {offset}")
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
                    log.warning("could not close thumbnail file: %s", exc)
            if self._path is not None:
                try:
                    self._path.unlink(missing_ok=True)
                except OSError as exc:
                    log.warning("could not delete %s: %s", self._path, exc)

    def __enter__(self) -> ThumbStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close_and_delete()

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("thumbnail store is closed")

    def _handle(self) -> IO[bytes]:
        """Return the open file, creating it on first use. Caller holds the lock."""
        if self._fh is None:
            fh = tempfile.NamedTemporaryFile(
                prefix="photo-manager-thumbs-", suffix=".tmp", delete=False
            )
            self._fh = fh
            self._path = Path(fh.name)
        return self._fh
