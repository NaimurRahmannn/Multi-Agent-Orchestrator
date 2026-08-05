from __future__ import annotations

import os
import tempfile
from pathlib import Path


def localize_crewai_paths() -> None:
    """Keep CrewAI runtime caches in a writable temp directory."""
    base = Path(tempfile.gettempdir()) / "agentorchestra-crewai"
    os.environ.setdefault("XDG_DATA_HOME", str(base / "data"))
    os.environ.setdefault("XDG_CONFIG_HOME", str(base / "config"))
    os.environ.setdefault("XDG_CACHE_HOME", str(base / "cache"))
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    os.environ.setdefault("CREWAI_DISABLE_TRACKING", "true")


localize_crewai_paths()
