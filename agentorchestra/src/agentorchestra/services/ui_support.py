from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from pydantic import StrictBool

from agentorchestra.config import GroqAgentName, Settings
from agentorchestra.models import AgentOrchestraModel, EditRequest
from agentorchestra.services.workspace import validate_site_structure

CHROMIUM_PROBE_SUCCESS = "chromium-executable-ready"
CHROMIUM_PROBE_TIMEOUT_SECONDS = 15.0
SubprocessRunner = Callable[..., subprocess.CompletedProcess[str]]
ChromiumProbe = Callable[[], bool]


class RuntimeReadiness(AgentOrchestraModel):
    manager_configured: StrictBool
    html_configured: StrictBool
    css_configured: StrictBool
    seo_configured: StrictBool
    qa_configured: StrictBool
    lighthouse_available: StrictBool
    playwright_available: StrictBool
    chromium_available: StrictBool | None = None
    working_site_valid: StrictBool


def list_supported_target_pages(settings: Settings) -> tuple[str, ...]:
    """Return only validated, top-level HTML files from the working sample site."""
    validate_site_structure(settings.working_site_dir)
    pages: list[str] = []
    for candidate in settings.working_site_dir.iterdir():
        if candidate.is_symlink() or not candidate.is_file() or candidate.suffix.lower() != ".html":
            continue
        pages.append(
            EditRequest(
                target_page=candidate.name,
                instruction="Validate UI target page.",
            ).target_page
        )
    return tuple(sorted(pages))


def default_target_page(pages: tuple[str, ...]) -> str:
    """Prefer the primary landing page when the UI needs an initial selection."""
    if "index.html" in pages:
        return "index.html"
    if not pages:
        raise ValueError("pages must not be empty.")
    return pages[0]


def check_runtime_readiness(
    settings: Settings,
    *,
    check_chromium: bool = False,
    chromium_probe: ChromiumProbe | None = None,
) -> RuntimeReadiness:
    configured = {
        agent: _agent_configured(settings, agent)
        for agent in GroqAgentName
    }
    playwright_available = importlib.util.find_spec("playwright") is not None
    chromium_available: bool | None = None
    if check_chromium:
        chromium_available = False
        if playwright_available:
            try:
                probe = chromium_probe or (lambda: _probe_chromium_subprocess(settings))
                chromium_available = bool(probe())
            except Exception:
                chromium_available = False
    try:
        validate_site_structure(settings.working_site_dir)
        working_valid = True
    except Exception:
        working_valid = False
    lighthouse = shutil.which("lighthouse") is not None or (
        shutil.which("npx") is not None
        and any(
            path.is_file()
            for path in (
                settings.project_root / "node_modules" / ".bin" / "lighthouse",
                settings.project_root / "node_modules" / ".bin" / "lighthouse.cmd",
            )
        )
    )
    return RuntimeReadiness(
        manager_configured=configured[GroqAgentName.MANAGER],
        html_configured=configured[GroqAgentName.HTML],
        css_configured=configured[GroqAgentName.CSS],
        seo_configured=configured[GroqAgentName.SEO],
        qa_configured=configured[GroqAgentName.QA],
        lighthouse_available=lighthouse,
        playwright_available=playwright_available,
        chromium_available=chromium_available,
        working_site_valid=working_valid,
    )


def _agent_configured(settings: Settings, agent: GroqAgentName) -> bool:
    try:
        settings.require_groq_configuration(agent)
    except Exception:
        return False
    return True


def _probe_chromium_subprocess(
    settings: Settings,
    *,
    runner: SubprocessRunner = subprocess.run,
    timeout_seconds: float = CHROMIUM_PROBE_TIMEOUT_SECONDS,
) -> bool:
    """Probe Chromium in an isolated process and accept only a clean shutdown."""
    probe_script = Path(__file__).resolve().parents[1] / "scripts" / "probe_playwright.py"
    try:
        result = runner(
            [sys.executable, str(probe_script)],
            cwd=settings.project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return (
        result.returncode == 0
        and result.stdout.strip() == CHROMIUM_PROBE_SUCCESS
        and not result.stderr.strip()
    )
