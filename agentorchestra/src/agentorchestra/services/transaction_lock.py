from __future__ import annotations

import errno
import math
import os
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agentorchestra.config import Settings
from agentorchestra.exceptions import PromotionError

LOCK_FILE_NAME = ".agentorchestra-working-site.lock"
DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0

# File locks coordinate processes; this lock also serializes threads in one process.
_PROCESS_TRANSACTION_LOCK = threading.Lock()


@contextmanager
def working_site_transaction(
    settings: Settings,
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> Iterator[None]:
    """Hold the project-wide working-site transaction lock."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise PromotionError("Working-site lock timeout must be a positive finite number.")

    deadline = time.monotonic() + timeout_seconds
    if not _PROCESS_TRANSACTION_LOCK.acquire(timeout=timeout_seconds):
        raise PromotionError("Timed out waiting for another working-site transaction.")

    descriptor = -1
    try:
        descriptor = _open_lock_file(_lock_file_path(settings))
        _acquire_file_lock(descriptor, deadline)
        yield
    finally:
        try:
            if descriptor >= 0:
                os.close(descriptor)
        finally:
            _PROCESS_TRANSACTION_LOCK.release()


def _lock_file_path(settings: Settings) -> Path:
    try:
        project_root = settings.project_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PromotionError("Project root is unavailable for working-site locking.") from exc
    if not project_root.is_dir():
        raise PromotionError("Project root is invalid for working-site locking.")
    path = project_root / LOCK_FILE_NAME
    if path.parent != project_root or path.is_symlink():
        raise PromotionError("Working-site lock path is unsafe.")
    return path


def _open_lock_file(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise PromotionError("Unable to open the working-site transaction lock.") from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise PromotionError("Working-site lock is not a regular file.")
        if file_stat.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _acquire_file_lock(descriptor: int, deadline: float) -> None:
    while True:
        try:
            _try_file_lock(descriptor)
            return
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise PromotionError("Unable to acquire the working-site transaction lock.") from exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PromotionError(
                    "Timed out waiting for another working-site transaction."
                ) from exc
            time.sleep(min(0.05, remaining))


def _try_file_lock(descriptor: int) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
