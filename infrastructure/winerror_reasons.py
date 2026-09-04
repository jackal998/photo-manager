"""Windows ``winerror`` → human-reason decoding, shared by both delete paths.

Canonical — and since #757, the only — home for the ``OSError.winerror`` ->
plain-language reason table. The web delete path
(``infrastructure/delete_service.py``) and the Qt desktop dialog
(``app/views/dialogs/execute_action_dialog.py``, whose ``_decode_winerror``
is now a thin wrapper over ``decode_winerror``) both read the table here, so
the web port's per-file failure messages read identically to the desktop's
"Files Failed to Delete" dialog by construction rather than by copy. The
table originated in the Qt dialog and was lifted here verbatim by #742; #757
deleted the duplicate it had left behind.
"""

from __future__ import annotations

# Decode Windows Shell COPYENGINE_E_* HRESULTs raised by send2trash into
# plain-language reasons. send2trash wraps the COM error as
# ``OSError(None, "OLE error 0x80270027", path, winerror=-2144927705)`` —
# the raw HRESULT string is opaque to users (the documented bug was a
# misread of 0x80270027 as "permission denied or path too long" when the
# actual cause is a file-handle sharing violation). Constants from
# ``win32comext.shell.shellcon``; this maps the codes a user is most
# likely to hit on a Move-to-Recycle-Bin failure.
WINERROR_REASON_TABLE: dict[int, str] = {
    # Signed-int form of each HRESULT (Python's ``OSError.winerror``).
    -2144927705: "file is in use by another process",   # 0x80270027 SHARING_VIOLATION_SRC
    -2144927704: "destination is in use by another process",  # 0x80270028 SHARING_VIOLATION_DEST
    -2144927711: "access denied (source)",              # 0x80270021 ACCESS_DENIED_SRC
    -2144927710: "access denied (destination)",         # 0x80270022 ACCESS_DENIED_DEST
    -2144927688: "path too long for Recycle Bin",       # 0x80270038 RECYCLE_PATH_TOO_LONG
    -2144927683: "file not found",                      # 0x8027003D (best-known approximation)
    -2144927684: "destination disk is full",            # 0x8027003C
}


def decode_winerror(exc: BaseException) -> str | None:
    """Return a plain-language reason for ``exc.winerror``, or ``None``.

    Looks up the signed HRESULT (``OSError.winerror``) in
    ``WINERROR_REASON_TABLE``. Returns ``None`` when ``exc`` has no
    ``winerror`` attribute, when it isn't an ``int``, or when the code is
    unmapped — the caller decides its own fallback (e.g. ``str(exc)``).
    Accepts any ``BaseException``, not just ``OSError``: the lookup is
    ``getattr``-based, and the Qt dialog's ``_decode_winerror`` wrapper
    routes non-``OSError`` delete failures (the ``os.remove`` fallback path)
    through here too. Returning ``None`` rather than a raw message lets each
    caller distinguish "no better information available" from "here is the
    raw message" and choose its own fallback text — the Qt dialog appends
    ``or str(exc)``, the web path records the reason only when present.
    """
    winerror = getattr(exc, "winerror", None)
    if isinstance(winerror, int):
        return WINERROR_REASON_TABLE.get(winerror)
    return None
