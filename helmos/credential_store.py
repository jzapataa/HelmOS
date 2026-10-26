"""User-bound Windows DPAPI storage. No plaintext files, dependencies or fallback."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from typing import Protocol

from .social_connectors import ConnectorError


class CredentialStore(Protocol):
    def load(self) -> dict | None: ...
    def save(self, credential: dict) -> None: ...
    def transaction(self): ...


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise ConnectorError("windows_credential_store_required")
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # UI_FORBIDDEN; no LOCAL_MACHINE flag: only the current Windows user.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ConnectorError("credential_protection_failed")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class WindowsCredentialStore:
    """One encrypted envelope includes both token and metadata, atomically replaced.

    transaction serializes HelmOS writers, including network refresh. A leftover
    lock after a killed process fails closed and must be removed with no writer.
    """

    def __init__(self, path: Path | None = None):
        if path is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local or os.name != "nt":
                raise ConnectorError("windows_credential_store_required")
            path = Path(local) / "HelmOS" / "credentials" / "instagram.dpapi"
        self.path = path

    def load(self) -> dict | None:
        try:
            if not self.path.exists():
                return None
            if self.path.stat().st_size > 65536:
                raise ConnectorError("invalid_credential_store")
            value = json.loads(_dpapi(self.path.read_bytes(), protect=False))
            if not isinstance(value, dict):
                raise ConnectorError("invalid_credential_store")
            return value
        except ConnectorError:
            raise
        except Exception:
            raise ConnectorError("credential_read_failed") from None

    def save(self, credential: dict) -> None:
        temporary = None
        try:
            encrypted = _dpapi(json.dumps(credential).encode("utf-8"), protect=True)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except ConnectorError:
            raise
        except Exception:
            raise ConnectorError("credential_write_failed") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    raise ConnectorError("credential_cleanup_failed") from None

    @contextmanager
    def transaction(self):
        lock = self.path.with_suffix(".lock")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stream = lock.open("xb")
        except FileExistsError:
            raise ConnectorError("credential_store_busy") from None
        except OSError:
            raise ConnectorError("credential_lock_failed") from None
        try:
            with stream:
                yield
        finally:
            try:
                lock.unlink()
            except OSError:
                raise ConnectorError("credential_unlock_failed") from None
