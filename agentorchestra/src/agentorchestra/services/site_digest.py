from __future__ import annotations

import hashlib
from pathlib import Path

from agentorchestra.pipeline_models import SiteTreeDigest
from agentorchestra.services.workspace import validate_site_structure

_LENGTH_BYTES = 8


def compute_site_tree_digest(site_root: Path) -> SiteTreeDigest:
    """Hash validated site paths and exact bytes in a platform-independent order."""
    validate_site_structure(site_root)
    root = site_root.resolve(strict=True)
    files = sorted(
        (path.relative_to(root).as_posix(), path) for path in root.rglob("*") if path.is_file()
    )
    digest = hashlib.sha256()
    names: list[str] = []
    total_bytes = 0
    for relative_name, path in files:
        relative_bytes = relative_name.encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative_bytes).to_bytes(_LENGTH_BYTES, "big"))
        digest.update(relative_bytes)
        digest.update(len(content).to_bytes(_LENGTH_BYTES, "big"))
        digest.update(content)
        names.append(relative_name)
        total_bytes += len(content)
    return SiteTreeDigest(
        digest=digest.hexdigest(),
        files=names,
        total_bytes=total_bytes,
    )
