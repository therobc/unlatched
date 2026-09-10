"""keystore.py - keep stored API keys out of a readable config.json.

(Named keystore, not secrets: `unlatched.secrets` would shadow the stdlib
`secrets` module for anything doing a plain `import secrets` inside this
package.)

The honest limit first, because it decides the design: a local app that
collects unattended cannot ask for a passphrase, so whatever unwraps the key
has to be reachable by the app itself. Encrypting with a key that also ships
on disk would be obfuscation. The only mechanism that is genuinely better
than plaintext under that constraint is the operating system's own user-bound
store, where the OS holds key material derived from the login and never hands
it to us.

On Windows that is DPAPI (`CryptProtectData` with `CRYPTPROTECT_LOCAL_MACHINE`
deliberately NOT set, so the blob is bound to the user, not the box). That
buys real properties: another account on the same machine cannot unwrap it,
and the file copied elsewhere - a backup, a synced folder, a lifted drive -
is inert. It does NOT defend against code already running as that same user,
which can call DPAPI exactly like we do. Nothing without a passphrase can.

Elsewhere (macOS, Linux) there is no stdlib-reachable equivalent, so the
value is stored as it always was and `is_protected` reports False, which is
what the config UI reads to avoid claiming protection it does not have.

Storage format is a tagged string in the same field the plaintext used, so
the config schema is unchanged and both front ends interoperate:

    dpapi:<base64 blob>     protected by the current user's DPAPI
    <anything else>         legacy plaintext, upgraded on the next save
"""
from __future__ import annotations

import base64
import sys

PREFIX = "dpapi:"


def _windows() -> bool:
    return sys.platform == "win32"


def _dpapi(data: bytes, *, encrypt: bool) -> bytes | None:
    """One call into crypt32. Returns None if the call fails for any reason,
    which callers treat as "this machine cannot protect secrets" rather than
    as an error - a user whose DPAPI is unavailable still gets a working app,
    just without this hardening.
    """
    import ctypes
    from ctypes import wintypes
    from typing import ClassVar

    class Blob(ctypes.Structure):
        _fields_: ClassVar = [("cbData", wintypes.DWORD),
                              ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob),
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                   ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL

    buf = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()

    # Description is stored with the blob and shows in some credential
    # tooling; naming the app there beats an unlabeled secret. It names the
    # APP and not the field: config._map_secrets walks every secret through
    # here, so a description naming one credential would be stamped on all
    # of them. No entropy argument: it would have to be stored beside the
    # blob to be usable, which adds nothing an attacker with file access
    # does not already have.
    ok = fn(ctypes.byref(source), "Unlatched stored credential", None,
            None, None, 0, ctypes.byref(out))
    if not ok:
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def available() -> bool:
    """Whether this machine can protect secrets at rest. The config UI shows
    the user which of the two states they are in instead of implying a
    guarantee the platform does not provide.
    """
    if not _windows():
        return False
    return _dpapi(b"probe", encrypt=True) is not None


def is_protected(stored: str) -> bool:
    return stored.startswith(PREFIX)


def protect(value: str) -> str:
    """Plain secret -> what belongs in config.json. Falls back to returning
    the value unchanged where DPAPI is unavailable; a stored secret that
    cannot be read back is worse than one stored plainly.
    """
    if not value or is_protected(value) or not _windows():
        return value
    blob = _dpapi(value.encode("utf-8"), encrypt=True)
    if blob is None:
        return value
    return PREFIX + base64.b64encode(blob).decode("ascii")


def unprotect(stored: str) -> str:
    """What is in config.json -> the plain secret. An untagged value is a
    legacy plaintext key and passes straight through, which is what lets an
    existing install keep working and get upgraded on its next save.
    """
    if not stored or not is_protected(stored):
        return stored
    if not _windows():
        return ""
    try:
        raw = base64.b64decode(stored[len(PREFIX):], validate=True)
    except (ValueError, TypeError):
        return ""
    plain = _dpapi(raw, encrypt=False)
    if plain is None:
        # Wrong user, or the blob moved from the machine it was written on.
        # Empty reads downstream as "no credential", so the source skips with
        # its normal hint rather than sending a corrupt key to the API.
        return ""
    return plain.decode("utf-8", errors="replace")
