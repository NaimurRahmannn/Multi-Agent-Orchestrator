from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath

_WINDOWS_DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/])[^\s\"']+")
_UNC_PATH = re.compile(r"(?:^|(?<=[\s\"']))\\\\[^\\\s]+\\[^\\\s]+")
_POSIX_PATH = re.compile(
    r"(?<![A-Za-z0-9:])/(?:home|tmp|Users|var|etc|opt|srv|mnt|private)/[^\s\"']+"
)
_GROQ_KEY = re.compile(r"(?i)gsk_[A-Za-z0-9_-]+")


def validate_relative_site_path(value: str) -> str:
    """Validate a portable project-relative site path without touching the filesystem."""
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise ValueError("site path must be a non-blank relative path.")
    if "\\" in value:
        raise ValueError("site path must use relative POSIX separators.")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root:
        raise ValueError("site path must not be absolute.")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise ValueError("site path must not contain traversal components.")
    if posix.as_posix() != value:
        raise ValueError("site path must use normalized relative POSIX syntax.")
    return value


def contains_absolute_path_text(value: str) -> bool:
    """Detect common absolute filesystem paths without treating ordinary slashes as paths."""
    return bool(
        _WINDOWS_DRIVE_PATH.search(value) or _UNC_PATH.search(value) or _POSIX_PATH.search(value)
    )


def reject_absolute_path_text(value: str, *, message: str) -> None:
    if contains_absolute_path_text(value):
        raise ValueError(message)


def redact_absolute_path_text(value: str) -> str:
    """Replace recognizable absolute filesystem paths in user-facing diagnostic text."""
    clean = _WINDOWS_DRIVE_PATH.sub("[path]", value)
    clean = _UNC_PATH.sub(" [path]", clean)
    return _POSIX_PATH.sub("[path]", clean)


def redact_secret_like_text(value: str) -> str:
    """Redact recognizable provider-key tokens from otherwise safe diagnostic text."""
    return _GROQ_KEY.sub("[redacted]", value)
