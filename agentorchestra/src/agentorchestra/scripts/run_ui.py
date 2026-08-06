from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from collections.abc import Callable, Sequence

from agentorchestra.config import Settings, get_settings

SubprocessRunner = Callable[..., subprocess.CompletedProcess[object]]


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Launch the AgentOrchestra Streamlit dashboard.")


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    subprocess_runner: SubprocessRunner = subprocess.run,
) -> int:
    build_parser().parse_args(argv)
    resolved = settings or get_settings()
    if importlib.util.find_spec("streamlit") is None:
        print("Streamlit is unavailable. Install project dependencies before launching the UI.")
        return 1
    app_path = resolved.source_dir / "agentorchestra" / "ui" / "app.py"
    if app_path.is_symlink() or not app_path.is_file():
        print("The fixed AgentOrchestra UI application is unavailable.")
        return 1
    completed = subprocess_runner(
        [sys.executable, "-m", "streamlit", "run", str(app_path)],
        cwd=resolved.project_root,
        shell=False,
        check=False,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
