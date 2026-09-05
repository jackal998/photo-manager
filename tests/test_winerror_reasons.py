"""Tests for infrastructure.winerror_reasons.decode_winerror.

Mirrors the desktop's ``TestDecodeWinerror`` precedent
(tests/test_execute_action_dialog.py) but targets the new shared module the
web delete path calls. Real failure modes only: each test builds an OSError
shaped exactly like the one send2trash raises for a genuine Windows Shell
COPYENGINE_E_* HRESULT.
"""

from __future__ import annotations

from infrastructure.winerror_reasons import decode_winerror


def test_known_sharing_violation_src():
    """0x80270027 — file held open by another process. Historically this
    code was indistinguishable from a generic three-cause guess; the
    decoder must name the actual cause."""
    exc = OSError(None, "OLE error 0x80270027", "C:\\foo.MP4")
    exc.winerror = -2144927705
    assert decode_winerror(exc) == "file is in use by another process"


def test_known_access_denied_src():
    """0x80270021 — distinct from a sharing violation; must not collapse
    to the same reason text."""
    exc = OSError(None, "OLE error 0x80270021", "C:\\foo.jpg")
    exc.winerror = -2144927711
    assert decode_winerror(exc) == "access denied (source)"


def test_known_destination_disk_full():
    exc = OSError(None, "OLE error 0x8027003C", "C:\\foo.jpg")
    exc.winerror = -2144927684
    assert decode_winerror(exc) == "destination disk is full"


def test_unknown_winerror_returns_none():
    """An HRESULT we don't have a mapping for must return None — the
    caller picks its own fallback text rather than the decoder inventing
    one for an unrecognised code."""
    exc = OSError(None, "OLE error 0x80270000", "C:\\foo.jpg")
    exc.winerror = -2147024896  # arbitrary unmapped HRESULT
    assert decode_winerror(exc) is None


def test_exception_without_winerror_returns_none():
    """A plain OSError with no ``winerror`` attribute (e.g. raised by
    filesystem validation rather than send2trash) must return None
    instead of raising."""
    exc = OSError("disk full")
    assert decode_winerror(exc) is None


def test_non_int_winerror_returns_none():
    """A non-int ``winerror`` attribute (defensive — never observed in
    practice) must not crash the lookup."""
    exc = OSError("weird")
    exc.winerror = "not-an-int"
    assert decode_winerror(exc) is None
