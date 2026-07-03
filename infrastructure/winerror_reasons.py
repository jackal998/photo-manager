"""Windows ``winerror`` → human-reason decoding, shared by the web delete path.

Canonical home for the ``OSError.winerror`` -> plain-language reason table.
The values below are copied VERBATIM from
``app/views/dialogs/execute_action_dialog.py`` (``_WINERROR_REASON_TABLE`` /
``_decode_winerror``, lines 92-116) so the web port's per-file failure
messages read identically to the Qt desktop's "Files Failed to Delete"
dialog. The Qt dialog keeps its own local copy of the table for now — its
bypass loop is out of scope for #742 (app/views/** untouched); LEAD will
file a follow-up to have it import from here instead of duplicating the
table.
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


def decode_winerror(exc: OSError) -> str | None:
    """Return a plain-language reason for ``exc.winerror``, or ``None``.

    Looks up the signed HRESULT (``OSError.winerror``) in
    ``WINERROR_REASON_TABLE``. Returns ``None`` when ``exc`` has no
    ``winerror`` attribute, when it isn't an ``int``, or when the code is
    unmapped — the caller decides its own fallback (e.g. ``str(exc)``).
    This differs slightly from the Qt dialog's ``_decode_winerror``, which
    falls back to ``str(exc)`` internally; returning ``None`` here lets a
    caller distinguish "no better information available" from "here is the
    raw message" and choose its own fallback text.
    """
    winerror = getattr(exc, "winerror", None)
    if isinstance(winerror, int):
        return WINERROR_REASON_TABLE.get(winerror)
    return None
