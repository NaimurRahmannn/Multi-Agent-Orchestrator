from __future__ import annotations

import importlib.util
import shutil
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from pydantic import StrictBool

from agentorchestra.config import GroqAgentName, Settings
from agentorchestra.models import AgentOrchestraModel, EditRequest
from agentorchestra.services.workspace import validate_site_structure


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


def check_runtime_readiness(
    settings: Settings,
    *,
    check_chromium: bool = False,
    playwright_factory: Callable[[], AbstractContextManager[Any]] | None = None,
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
            factory = playwright_factory or _sync_playwright_factory
            try:
                with factory() as playwright:
                    chromium_available = Path(playwright.chromium.executable_path).is_file()
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


def _sync_playwright_factory() -> AbstractContextManager[Any]:
    from playwright.sync_api import sync_playwright

    return sync_playwright()
