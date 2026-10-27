"""Kernel-owned locks, automatically released if a synchronizer is terminated."""

from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import os

from .social_connectors import ConnectorError


@contextmanager
def execution_lock(directory: Path, name: str = "execution.lock"):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ConnectorError("scheduled_sync_busy") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def serialize_instagram(function):
    @wraps(function)
    def wrapped(state_dir: Path, **options):
        target = state_dir.resolve()
        # Sidecar does not create Social state on a preview or failed read.
        with execution_lock(target.parent, f".{target.name}.instagram.lock"):
            return function(state_dir, **options)
    return wrapped
